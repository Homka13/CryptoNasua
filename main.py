import os
import json
import time
import asyncio
import logging
import sys
import io
from typing import Dict, Any, Optional
from config import config

# Prevent Windows System Sleep function
def set_prevent_sleep(enabled: bool):
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            if enabled:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                logging.info("💤 Windows 24/7 Anti-Sleep Prevention: ENABLED.")
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                logging.info("💤 Windows 24/7 Anti-Sleep Prevention: DISABLED.")
        except Exception as e:
            logging.warning(f"Could not update Windows execution state: {e}")

if getattr(config, 'prevent_sleep', True):
    set_prevent_sleep(True)

# Force UTF-8 encoding for Windows console to support emojis in logs
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from exchange_service import ExchangeService
from strategy import HybridStrategy
from risk_manager import RiskManager
from telegram_bot import TelegramInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from collections import deque
import time

class TradingBot:
    """Main Orchestrator for the Bybit Crypto Trading Bot ($10 starting budget)."""

    def __init__(self):
        self.exchange = ExchangeService()
        self.strategy = HybridStrategy()
        self.risk_manager = RiskManager()
        self.current_position: Optional[Dict[str, Any]] = self._load_position()
        self.latest_meta: Dict[str, Any] = {}
        self.rejected_cooldowns: Dict[str, float] = {}  # Symbol -> expiry timestamp
        self.scan_logs = deque(maxlen=30)
        self.ai_verdicts = deque(maxlen=30)
        
        # Initialize trading_active flag (requires Web UI authentication)
        self.trading_active = False

        # Initialize LLM Analyst filter
        from llm_analyst import LLMAnalyst
        self.llm_analyst = LLMAnalyst()
        
        # Initialize Telegram
        self.telegram = TelegramInterface(
            get_status_fn=self.get_bot_status_str,
            get_balance_fn=self.get_balance_str
        )

    def _load_position(self) -> Optional[Dict[str, Any]]:
        pos_file = os.path.join(os.path.dirname(__file__), "data", "position.json")
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r', encoding='utf-8') as f:
                    pos = json.load(f)
                    if pos and isinstance(pos, dict) and pos.get('amount', 0) > 0:
                        logger.info(f"📌 Loaded active open position from position.json: {pos.get('symbol')}")
                        return pos
            except Exception as e:
                logger.error(f"Error loading position.json: {e}")
        return None

    def _save_position(self, pos: Optional[Dict[str, Any]]) -> None:
        pos_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(pos_dir, exist_ok=True)
        pos_file = os.path.join(pos_dir, "position.json")
        try:
            with open(pos_file, 'w', encoding='utf-8') as f:
                json.dump(pos or {}, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving position.json: {e}")

    def get_bot_status_str(self) -> str:
        meta = self.latest_meta
        price = meta.get('price', 0.0)
        rsi = meta.get('rsi', 0.0)
        trend = meta.get('trend', 'UNKNOWN')

        pos_str = "None"
        if self.current_position:
            entry = self.current_position['entry_price']
            amt = self.current_position['amount']
            pnl_pct = ((price - entry) / entry) * 100 if price > 0 else 0.0
            pos_str = f"{amt:.4f} @ ${entry:.2f} (PnL: {pnl_pct:+.2f}%)"

        return (
            f"📊 *BYBIT BOT STATUS*\n"
            f"• Mode: `{'PAPER TRADING' if config.paper_trading else 'LIVE'}`\n"
            f"• Symbol: `{config.symbol}` ({config.timeframe})\n"
            f"• Last Price: `${price:.2f}`\n"
            f"• RSI (14): `{rsi:.1f}`\n"
            f"• Trend: `{trend}`\n"
            f"• LLM Filter: `{'ENABLED (' + config.llm_provider.upper() + ')' if config.use_llm_confirmation else 'DISABLED'}`\n"
            f"• Active Position: `{pos_str}`"
        )

    def get_balance_str(self) -> str:
        try:
            bal = self.exchange.fetch_balance()
            usdt_free = bal.get('USDT', {}).get('free', 0.0)
            base_currency = config.symbol.split('/')[0]
            coin_free = bal.get(base_currency, {}).get('free', 0.0)
            return (
                f"💰 *ACCOUNT BALANCE*\n"
                f"• Available USDT: `${usdt_free:.2f}`\n"
                f"• {base_currency} Balance: `{coin_free:.4f}`"
            )
        except Exception as e:
            return f"❌ Error fetching balance: {e}"

    async def run_loop(self):
        logger.info("🚀 Starting Bybit Trading Bot loop...")
        await self.telegram.send_alert(
            f"🚀 *Trading Bot Started!*\n"
            f"Mode: `{'PAPER TRADING' if config.paper_trading else 'LIVE'}`\n"
            f"Capital: `${config.initial_capital:.2f}`\n"
            f"Pair: `{config.symbol}` ({config.timeframe})\n"
            f"LLM Filter: `{'ENABLED' if config.use_llm_confirmation else 'DISABLED'}`"
        )

        import time
        while True:
            try:
                if not getattr(self, 'trading_active', False):
                    await asyncio.sleep(2)
                    continue

                if not self.telegram.is_active:
                    await asyncio.sleep(5)
                    continue

                # Determine pairs to scan
                if self.current_position and 'symbol' in self.current_position:
                    symbols_to_scan = [self.current_position['symbol']]
                elif config.symbol == "AUTO" or getattr(config, 'use_dynamic_market_screener', False):
                    try:
                        symbols_to_scan = self.exchange.fetch_dynamic_hot_pairs(min_volume=1000000.0, limit=15)
                    except Exception as screener_err:
                        logger.error(f"Dynamic Screener fallback error: {screener_err}")
                        symbols_to_scan = ["SHIB/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "DOGE/USDT", "PEPE/USDT"]
                elif getattr(config, 'multi_pair_scan', False) and hasattr(config, 'trading_pairs'):
                    symbols_to_scan = config.trading_pairs
                else:
                    symbols_to_scan = [config.symbol]

                # Filter out symbols currently in Cooldown after LLM rejection
                now = time.time()
                active_scan_symbols = [
                    s for s in symbols_to_scan 
                    if now >= self.rejected_cooldowns.get(s, 0)
                ]

                best_buy_opportunity = None

                for sym in active_scan_symbols:
                    if not self.telegram.is_active:
                        break

                    try:
                        df = self.exchange.fetch_ohlcv(sym, config.timeframe, limit=100)
                        pos_for_sym = self.current_position if (self.current_position and self.current_position.get('symbol') == sym) else None
                        signal, reason, meta = self.strategy.analyze(df, pos_for_sym)
                        meta['signal'] = signal
                        meta['reason'] = reason
                        meta['symbol'] = sym
                        self.latest_meta = meta

                        scan_entry = {
                            'time': time.strftime("%H:%M:%S"),
                            'symbol': sym,
                            'price': meta.get('price', 0.0),
                            'signal': signal,
                            'reason': reason,
                            'rsi': meta.get('rsi', 0.0),
                            'trend': meta.get('trend', 'UNKNOWN')
                        }
                        self.scan_logs.appendleft(scan_entry)
                        logger.info(f"[{sym} ${meta.get('price', 0):.4f}] Signal: {signal} | Reason: {reason}")

                        if signal == 'BUY' and self.current_position is None:
                            if best_buy_opportunity is None or meta.get('rsi', 100) < best_buy_opportunity['meta'].get('rsi', 100):
                                best_buy_opportunity = {'symbol': sym, 'meta': meta, 'reason': reason}
                        elif signal == 'SELL' and self.current_position is not None:
                            amount = self.current_position['amount']
                            entry_p = self.current_position['entry_price']
                            curr_p = meta['price']
                            pnl_pct = ((curr_p - entry_p) / entry_p) * 100

                            logger.info(f"Executing SELL for {sym} ({amount:.4f} coins @ ${curr_p:.2f}). Reason: {reason}")
                            await self.exchange.execute_smart_order('sell', amount, curr_p)
                            
                            await self.telegram.send_alert(
                                f"🔴 *SELL ORDER EXECUTED (Quant Engine)*\n"
                                f"• Pair: `{sym}`\n"
                                f"• Exit Price: `${curr_p:.2f}` (Entry: `${entry_p:.2f}`)\n"
                                f"• PnL: `{pnl_pct:+.2f}%`\n"
                                f"• Execution: `Limit Offset + Iceberg`\n"
                                f"• Reason: {reason}"
                            )
                            self.current_position = None
                            self._save_position(None)
                            break
                    except Exception as scan_err:
                        logger.error(f"Error scanning {sym}: {scan_err}")

                # Process Best Buy Opportunity found across scanned symbols
                if best_buy_opportunity and self.current_position is None:
                    target_sym = best_buy_opportunity['symbol']
                    target_meta = best_buy_opportunity['meta']
                    target_reason = best_buy_opportunity['reason']

                    is_confirmed, llm_reason = await self.llm_analyst.evaluate_trade_signal(
                        target_sym, config.timeframe, target_meta, target_reason
                    )

                    # Record AI Verdict into History Deque for Dashboard UI
                    verdict_record = {
                        'timestamp': int(time.time() * 1000),
                        'time': time.strftime("%H:%M:%S"),
                        'symbol': target_sym,
                        'side': 'buy' if is_confirmed else 'reject',
                        'price': target_meta.get('price', 0.0),
                        'amount': 0.0,
                        'status': 'CONFIRMED' if is_confirmed else 'REJECTED',
                        'reason': llm_reason,
                        'provider': config.llm_provider.upper()
                    }
                    self.ai_verdicts.appendleft(verdict_record)

                    # Add prominent entry into live scan logs feed
                    ai_icon = "🟢 DEEPSEEK CONFIRMED" if is_confirmed else "🛑 DEEPSEEK REJECTED"
                    self.scan_logs.appendleft({
                        'time': time.strftime("%H:%M:%S"),
                        'symbol': target_sym,
                        'price': target_meta.get('price', 0.0),
                        'signal': 'BUY' if is_confirmed else 'REJECTED',
                        'reason': f"🤖 {ai_icon}: {llm_reason}",
                        'rsi': target_meta.get('rsi', 0.0),
                        'trend': target_meta.get('trend', 'UNKNOWN')
                    })

                    if not is_confirmed:
                        # Activate 15-minute cooldown for rejected pair to prevent spam and focus on other pairs
                        self.rejected_cooldowns[target_sym] = time.time() + (15 * 60)
                        logger.warning(f"🛑 BUY signal for {target_sym} rejected by LLM Analyst (15m Cooldown activated): {llm_reason}")
                        await self.telegram.send_alert(f"⚠️ *BUY Signal REJECTED by LLM ({target_sym})* [15m Cooldown Activated]: {llm_reason}")
                    else:
                        bal = self.exchange.fetch_balance()
                        usdt_free = bal.get('USDT', {}).get('free', 0.0)
                        
                        is_allowed, amount, risk_reason = self.risk_manager.calculate_position_size(
                            usdt_free, target_meta['price']
                        )

                        if is_allowed:
                            try:
                                logger.info(f"Executing BUY for {target_sym} via Quant Engine: {risk_reason}")
                                orders = await self.exchange.execute_smart_order('buy', amount, target_meta['price'])
                                
                                verdict_record['amount'] = amount
                                self.current_position = {
                                    'symbol': target_sym,
                                    'entry_price': target_meta['price'],
                                    'amount': amount,
                                    'order_id': orders[0].get('id') if orders else 'N/A'
                                }
                                self._save_position(self.current_position)
                                
                                await self.telegram.send_alert(
                                    f"🟢 *BUY ORDER EXECUTED (Quant Engine)*\n"
                                    f"• Pair: `{target_sym}`\n"
                                    f"• Price: `${target_meta['price']:.2f}`\n"
                                    f"• Amount: `{amount:.4f}`\n"
                                    f"• Execution: `Limit Offset + Iceberg ({config.iceberg_slices} slices)`\n"
                                    f"• Strategy Reason: {target_reason}\n"
                                    f"• {llm_reason}"
                                )
                            except Exception as order_err:
                                verdict_record['status'] = 'EXCHANGE_REJECTED'
                                verdict_record['reason'] += f" | ⚠️ Bybit Error: {order_err}"
                                logger.error(f"Bybit Order Execution Error for {target_sym}: {order_err}")
                                await self.telegram.send_alert(f"⚠️ *Bybit Order Rejected*: {order_err}")
                        else:
                            verdict_record['status'] = 'RISK_REJECTED'
                            verdict_record['reason'] += f" | ⚠️ RiskManager: {risk_reason}"
                            logger.warning(f"BUY rejected by RiskManager for {target_sym}: {risk_reason}")

            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                await asyncio.sleep(10)

            # Pause between candles / scans
            await asyncio.sleep(10)

async def main():
    bot = TradingBot()
    
    # Initialize & Start Private Web Dashboard Server
    from dashboard_server import DashboardServer
    dashboard_server = DashboardServer(bot)
    asyncio.create_task(dashboard_server.start())

    await bot.run_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped by keyboard interrupt.")
