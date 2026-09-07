import asyncio
import logging
import sys
import io
import signal
import time
from typing import Dict, Any, Optional, List

# Force UTF-8 encoding for Windows console to support emojis in logs
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from config import config
from exchange_service import BaseExchangeService, create_exchange_service
from strategy import HybridStrategy, SignalResult
from risk_manager import RiskManager
from state_store import BotStateStore
from llm_analyst import LLMAnalyst
from telegram_bot import TelegramInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class TradingBot:
    """Main Orchestrator for the Bybit Crypto Trading Bot ($10 starting budget)."""

    def __init__(
        self,
        config_obj: Any = None,
        exchange: Optional[BaseExchangeService] = None,
        strategy: Optional[HybridStrategy] = None,
        risk_manager: Optional[RiskManager] = None,
        state_store: Optional[BotStateStore] = None,
        llm_analyst: Optional[LLMAnalyst] = None,
        telegram: Optional[TelegramInterface] = None,
    ) -> None:
        self.config = config_obj or config
        self.exchange = exchange or create_exchange_service()
        self.strategy = strategy or HybridStrategy()
        self.risk_manager = risk_manager or RiskManager()
        self.state_store = state_store or BotStateStore()
        self.llm_analyst = llm_analyst or LLMAnalyst()
        self.telegram = telegram or TelegramInterface(
            get_status_fn=self.get_bot_status_str,
            get_balance_fn=self.get_balance_str,
        )
        self._stop_requested = False

    # --- Backward-compatible state accessors (delegate to state store) ---
    @property
    def current_position(self) -> Optional[Dict[str, Any]]:
        return self.state_store.current_position

    @current_position.setter
    def current_position(self, value: Optional[Dict[str, Any]]) -> None:
        # Sync setter (used only at init/test); runtime updates go through state_store.
        self.state_store.current_position = value

    @property
    def latest_meta(self) -> Dict[str, Any]:
        return self.state_store.latest_meta

    @latest_meta.setter
    def latest_meta(self, value: Dict[str, Any]) -> None:
        self.state_store.latest_meta = value

    @property
    def scan_logs(self):
        return self.state_store.scan_logs

    @property
    def ai_verdicts(self):
        return self.state_store.ai_verdicts

    def get_bot_status_str(self) -> str:
        meta = self.latest_meta
        price = meta.get('price', 0.0)
        rsi = meta.get('rsi', 0.0)
        trend = meta.get('trend', 'UNKNOWN')
        cfg = self.config

        pos_str = "None"
        if self.current_position:
            entry = self.current_position['entry_price']
            amt = self.current_position['amount']
            pnl_pct = ((price - entry) / entry) * 100 if price > 0 else 0.0
            pos_str = f"{amt:.4f} @ ${entry:.2f} (PnL: {pnl_pct:+.2f}%)"

        return (
            f"📊 *BYBIT BOT STATUS*\n"
            f"• Execution: `{'PAPER TRADING' if cfg.paper_trading else 'LIVE'}`\n"
            f"• Style Mode: `{cfg.trading_mode_display}`\n"
            f"• Symbol: `{cfg.symbol}` ({cfg.timeframe})\n"
            f"• Last Price: `${price:.2f}`\n"
            f"• RSI (14): `{rsi:.1f}`\n"
            f"• Trend: `{trend}`\n"
            f"• LLM Filter: `{'ENABLED (' + cfg.llm_provider.upper() + ')' if cfg.use_llm_confirmation else 'DISABLED'}`\n"
            f"• Active Position: `{pos_str}`"
        )

    async def get_balance_str(self) -> str:
        try:
            bal = await self.exchange.fetch_balance()
            usdt_free = bal.get('USDT', {}).get('free', 0.0)
            base_currency = self.config.symbol.split('/')[0]
            coin_free = bal.get(base_currency, {}).get('free', 0.0)
            return (
                f"💰 *ACCOUNT BALANCE*\n"
                f"• Available USDT: `${usdt_free:.2f}`\n"
                f"• {base_currency} Balance: `{coin_free:.4f}`"
            )
        except Exception as e:
            return f"❌ Error fetching balance: {e}"

    async def _scan_candidate_pairs(self) -> List[str]:
        """Determine which symbols to scan this iteration."""
        pos = self.current_position
        if pos and 'symbol' in pos:
            return [pos['symbol']]

        cfg = self.config
        if cfg.symbol == "AUTO" or getattr(cfg, 'use_dynamic_market_screener', False):
            try:
                return await self.exchange.fetch_dynamic_hot_pairs(
                    min_volume=1_000_000.0, limit=15
                )
            except Exception as screener_err:
                logger.error(f"Dynamic Screener fallback error: {screener_err}")
                return ["SHIB/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "DOGE/USDT", "PEPE/USDT"]

        if getattr(cfg, 'multi_pair_scan', False) and hasattr(cfg, 'trading_pairs'):
            return list(cfg.trading_pairs)

        return [cfg.symbol]

    async def _evaluate_signals(self, symbols: List[str]) -> Optional[Dict[str, Any]]:
        """Scan symbols and return the best BUY opportunity, executing SELLs inline."""
        best_buy_opportunity: Optional[Dict[str, Any]] = None

        for sym in symbols:
            try:
                df = await self.exchange.fetch_ohlcv(sym, self.config.timeframe, limit=100)
                pos_for_sym = (
                    self.current_position
                    if (self.current_position and self.current_position.get('symbol') == sym)
                    else None
                )
                result: SignalResult = self.strategy.analyze(df, pos_for_sym)
                meta = dict(result.metadata)
                meta['signal'] = result.signal
                meta['reason'] = result.reason
                meta['symbol'] = sym
                await self.state_store.update_meta(meta)

                scan_entry = {
                    'time': time.strftime("%H:%M:%S"),
                    'symbol': sym,
                    'price': meta.get('price', 0.0),
                    'signal': result.signal,
                    'reason': result.reason,
                    'rsi': meta.get('rsi', 0.0),
                    'trend': meta.get('trend', 'UNKNOWN'),
                }
                await self.state_store.record_scan(scan_entry)
                logger.info(f"[{sym} ${meta.get('price', 0):.4f}] Signal: {result.signal} | Reason: {result.reason}")

                if result.signal == 'BUY' and self.current_position is None:
                    if (
                        best_buy_opportunity is None
                        or meta.get('rsi', 100) < best_buy_opportunity['meta'].get('rsi', 100)
                    ):
                        best_buy_opportunity = {'symbol': sym, 'meta': meta, 'reason': result.reason}
                elif result.signal == 'SELL' and self.current_position is not None:
                    await self._handle_sell_execution(sym, meta)
                    break
            except Exception as scan_err:
                logger.error(f"Error scanning {sym}: {scan_err}")

        return best_buy_opportunity

    async def _handle_sell_execution(self, sym: str, meta: Dict[str, Any]) -> None:
        amount = self.current_position['amount']
        entry_p = self.current_position['entry_price']
        curr_p = meta['price']
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100

        logger.info(f"Executing SELL for {sym} ({amount:.4f} coins @ ${curr_p:.2f}). Reason: {meta.get('reason')}")
        await self.exchange.execute_smart_order('sell', amount, curr_p, symbol=sym)

        await self.telegram.send_alert(
            f"🔴 *SELL ORDER EXECUTED (Quant Engine)*\n"
            f"• Pair: `{sym}`\n"
            f"• Exit Price: `${curr_p:.2f}` (Entry: `${entry_p:.2f}`)\n"
            f"• PnL: `{pnl_pct:+.2f}%`\n"
            f"• Execution: `Limit Offset + Iceberg`\n"
            f"• Reason: {meta.get('reason')}"
        )
        await self.state_store.clear_position()
        self.risk_manager.reset_stop_state()

    async def _handle_buy_execution(self, opportunity: Dict[str, Any]) -> None:
        target_sym = opportunity['symbol']
        target_meta = opportunity['meta']
        target_reason = opportunity['reason']

        is_confirmed, llm_reason = await self.llm_analyst.evaluate_trade_signal(
            target_sym, self.config.timeframe, target_meta, target_reason
        )

        verdict_record = {
            'timestamp': int(time.time() * 1000),
            'time': time.strftime("%H:%M:%S"),
            'symbol': target_sym,
            'side': 'buy' if is_confirmed else 'reject',
            'price': target_meta.get('price', 0.0),
            'amount': 0.0,
            'status': 'CONFIRMED' if is_confirmed else 'REJECTED',
            'reason': llm_reason,
            'provider': self.config.llm_provider.upper(),
        }
        await self.state_store.record_verdict(verdict_record)

        ai_icon = "🟢 CONFIRMED" if is_confirmed else "🛑 REJECTED"
        await self.state_store.record_scan({
            'time': time.strftime("%H:%M:%S"),
            'symbol': target_sym,
            'price': target_meta.get('price', 0.0),
            'signal': 'BUY' if is_confirmed else 'REJECTED',
            'reason': f"🤖 {ai_icon}: {llm_reason}",
            'rsi': target_meta.get('rsi', 0.0),
            'trend': target_meta.get('trend', 'UNKNOWN'),
        })

        if not is_confirmed:
            logger.warning(f"🛑 BUY signal for {target_sym} rejected by LLM Analyst: {llm_reason}")
            await self.telegram.send_alert(f"⚠️ *BUY Signal REJECTED by LLM ({target_sym})*: {llm_reason}")
            return

        bal = await self.exchange.fetch_balance()
        usdt_free = bal.get('USDT', {}).get('free', 0.0)

        size_result = self.risk_manager.calculate_position_size(usdt_free, target_meta['price'])

        if not size_result.is_allowed:
            logger.warning(f"BUY rejected by RiskManager for {target_sym}: {size_result.reason}")
            return

        logger.info(f"Executing BUY for {target_sym} via Quant Engine: {size_result.reason}")
        orders = await self.exchange.execute_smart_order(
            'buy', size_result.amount, target_meta['price'], symbol=target_sym
        )

        self.risk_manager.reset_stop_state()
        await self.state_store.update_position({
            'symbol': target_sym,
            'entry_price': target_meta['price'],
            'amount': size_result.amount,
            'order_id': orders[0].get('id') if orders else 'N/A',
        })

        await self.telegram.send_alert(
            f"🟢 *BUY ORDER EXECUTED (Quant Engine)*\n"
            f"• Pair: `{target_sym}`\n"
            f"• Price: `${target_meta['price']:.2f}`\n"
            f"• Amount: `{size_result.amount:.4f}`\n"
            f"• Execution: `Limit Offset + Iceberg ({self.config.iceberg_slices} slices)`\n"
            f"• Strategy Reason: {target_reason}\n"
            f"• {llm_reason}"
        )

    async def run_loop(self) -> None:
        logger.info("🚀 Starting Bybit Trading Bot loop...")
        await self.state_store.set_running(True)

        await self.telegram.send_alert(
            f"🚀 *Trading Bot Started!*\n"
            f"Execution: `{'PAPER TRADING' if self.config.paper_trading else 'LIVE'}`\n"
            f"Style Mode: `{self.config.trading_mode_display}`\n"
            f"Capital: `${self.config.initial_capital:.2f}`\n"
            f"Pair: `{self.config.symbol}` ({self.config.timeframe})\n"
            f"LLM Filter: `{'ENABLED' if self.config.use_llm_confirmation else 'DISABLED'}`"
        )

        while not self._stop_requested:
            try:
                if not self.telegram.is_active:
                    await asyncio.sleep(5)
                    continue

                symbols_to_scan = await self._scan_candidate_pairs()
                best_buy_opportunity = await self._evaluate_signals(symbols_to_scan)

                if best_buy_opportunity and self.current_position is None:
                    await self._handle_buy_execution(best_buy_opportunity)

            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                await asyncio.sleep(10)

            await asyncio.sleep(10)

        await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully close exchange sessions and persist state."""
        logger.info("🛑 Shutting down: closing exchange sessions and saving state...")
        await self.state_store.set_running(False)
        try:
            await self.exchange.close()
        except Exception as e:
            logger.error(f"Error during exchange close: {e}")

    def request_shutdown(self) -> None:
        self._stop_requested = True


async def main() -> None:
    bot = TradingBot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.request_shutdown)
        except NotImplementedError:
            # Fallback for platforms without add_signal_handler (e.g., Windows).
            pass

    from dashboard_server import DashboardServer
    dashboard_server = DashboardServer(bot)
    asyncio.create_task(dashboard_server.start())

    await bot.run_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped by keyboard interrupt.")