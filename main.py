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

# Force UTF-8 encoding for Windows console to support emojis in logs with instant unbuffered flushing
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', write_through=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', write_through=True)

from exchanges.exchange_factory import ExchangeFactory
from core.strategy import HybridStrategy
from core.risk_manager import RiskManager
from core.llm_analyst import LLMAnalyst
from telegram_bot import TelegramInterface

log_file_handler = logging.FileHandler("bot_activity.log", encoding="utf-8")
log_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        log_file_handler
    ],
    force=True
)
logger = logging.getLogger(__name__)

from collections import deque
import time

class TradingBot:
    """Main Orchestrator for the Multi-Exchange Crypto Trading Bot ($10 starting budget)."""

    def __init__(self):
        self.exchange = ExchangeFactory.create_adapter()
        self.strategy = HybridStrategy()
        self.risk_manager = RiskManager()
        self.llm_analyst = LLMAnalyst()
        self.active_positions: list = self._load_positions()
        self.latest_meta: Dict[str, Any] = {}
        self.active_position_metas: Dict[str, Dict[str, Any]] = {}
        self.rejected_cooldowns: Dict[str, float] = {}
        self.scan_logs = deque(maxlen=30)
        self.ai_verdicts = deque(maxlen=30)
        self.trade_actions = deque(maxlen=50)
        self.max_concurrent_positions = 3
        
        # Trading active flag
        self.trading_active = True
        
        # Initialize Telegram
        self.telegram = TelegramInterface(
            get_status_fn=self.get_bot_status_str,
            get_balance_fn=self.get_balance_str
        )

    def _load_positions(self) -> list:
        pos_file = os.path.join(os.path.dirname(__file__), "data", "position.json")
        if os.path.exists(pos_file):
            try:
                with open(pos_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        positions = data
                    elif isinstance(data, dict) and data.get('amount', 0) > 0:
                        positions = [data]
                    else:
                        positions = []

                    valid = []
                    for pos in positions:
                        if not isinstance(pos, dict) or pos.get('amount', 0) <= 0:
                            continue
                        is_pos_paper = pos.get('is_paper', True)
                        if is_pos_paper and not config.paper_trading:
                            logger.warning(f"⚠️ Paper position ({pos.get('symbol')}) ignored — bot is in LIVE mode.")
                            continue
                        if 'entry_time' not in pos or not pos['entry_time']:
                            pos['entry_time'] = os.path.getmtime(pos_file)
                        logger.info(f"📌 Loaded position: {pos.get('symbol')} (Paper: {is_pos_paper})")
                        valid.append(pos)
                    return valid
            except Exception as e:
                logger.error(f"Error loading position.json: {e}")
        return []

    def _save_positions(self) -> None:
        pos_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(pos_dir, exist_ok=True)
        pos_file = os.path.join(pos_dir, "position.json")
        try:
            for p in self.active_positions:
                p['is_paper'] = config.paper_trading
                if 'entry_time' not in p or not p['entry_time']:
                    p['entry_time'] = time.time()
            with open(pos_file, 'w', encoding='utf-8') as f:
                json.dump(self.active_positions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving position.json: {e}")

    def _find_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        for p in self.active_positions:
            if p.get('symbol') == symbol:
                return p
        return None

    def get_bot_status_str(self) -> str:
        meta = self.latest_meta
        price = meta.get('price', 0.0)
        rsi = meta.get('rsi', 0.0)
        trend = meta.get('trend', 'UNKNOWN')

        pos_str = "None"
        if self.active_positions:
            parts = []
            for p in self.active_positions:
                entry = p['entry_price']
                amt = p['amount']
                pnl_pct = ((price - entry) / entry) * 100 if price > 0 else 0.0
                parts.append(f"{p['symbol']}: {amt:.4f} @ ${entry:.2f} (PnL: {pnl_pct:+.2f}%)")
            pos_str = '\n  '.join(parts)

        return (
            f"📊 *BOT STATUS ({config.active_exchange.upper()})*\n"
            f"• Mode: `{'PAPER TRADING' if config.paper_trading else 'LIVE'}`\n"
            f"• Symbol: `{config.symbol}` ({config.timeframe})\n"
            f"• Last Price: `${price:.2f}`\n"
            f"• RSI (14): `{rsi:.1f}`\n"
            f"• Trend: `{trend}`\n"
            f"• LLM Filter: `{'ENABLED (' + config.llm_provider.upper() + ')' if config.use_llm_confirmation else 'DISABLED'}`\n"
            f"• Active Positions ({len(self.active_positions)}): `{pos_str}`"
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

    def is_btc_dumping(self) -> bool:
        """Checks if Bitcoin is currently dumping on 5m timeframe."""
        try:
            df_btc = self.exchange.fetch_ohlcv("BTC/USDT", "5m", limit=30)
            if df_btc is not None and len(df_btc) >= 20:
                df_calc = self.strategy.calculate_indicators(df_btc)
                latest_btc = df_calc.iloc[-1]
                close_p = float(latest_btc['close'])
                ema20_p = float(latest_btc['ema_fast'])
                rsi_p = float(latest_btc['rsi'])
                if close_p < ema20_p and rsi_p < 42.0:
                    return True
        except Exception as e:
            logger.debug(f"BTC shield check skipped: {e}")
        return False

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
        self.last_heartbeat = time.time()
        while True:
            try:
                # 60-second Exchange Connection Heartbeat Check
                if time.time() - getattr(self, 'last_heartbeat', 0) > 60:
                    try:
                        self.exchange.fetch_ticker('BTC/USDT')
                        self.last_heartbeat = time.time()
                    except Exception as heartbeat_err:
                        logger.error(f"⚠️ HEARTBEAT DISCONNECTION DETECTED: {heartbeat_err}")
                        await self.telegram.send_alert("⚠️ *WARNING: Lost exchange API connection! Re-establishing...*")
                        self.last_heartbeat = time.time()

                if not getattr(self, 'trading_active', False):
                    await asyncio.sleep(2)
                    continue

                if not self.telegram.is_active:
                    await asyncio.sleep(5)
                    continue

                # Determine pairs to scan (Always scan top hot pairs to enable live screener feed and Auto-Swap rotation)
                if config.symbol == "AUTO" or getattr(config, 'use_dynamic_market_screener', False):
                    try:
                        symbols_to_scan = self.exchange.fetch_dynamic_hot_pairs(min_volume=1000000.0, limit=15)
                    except Exception as screener_err:
                        logger.error(f"Dynamic Screener fallback error: {screener_err}")
                        symbols_to_scan = ["SHIB/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "DOGE/USDT", "PEPE/USDT"]
                elif getattr(config, 'multi_pair_scan', False) and hasattr(config, 'trading_pairs'):
                    symbols_to_scan = list(config.trading_pairs)
                else:
                    symbols_to_scan = [config.symbol]

                # Ensure all active position symbols are in scan list for SL/TP monitoring
                for pos in self.active_positions:
                    if pos.get('symbol') and pos['symbol'] not in symbols_to_scan:
                        symbols_to_scan.insert(0, pos['symbol'])

                # Filter out symbols currently in Cooldown after LLM rejection
                now = time.time()
                active_scan_symbols = [
                    s for s in symbols_to_scan 
                    if now >= self.rejected_cooldowns.get(s, 0)
                ]

                # Check BTC Gravity Shield: If Bitcoin is dumping on 5m, pause new altcoin BUY entries
                btc_is_dumping = self.is_btc_dumping()
                if btc_is_dumping:
                    logger.warning("📉 [BTC GRAVITY SHIELD]: BTC is dumping on 5m timeframe. Altcoin BUY signals paused to prevent fakeouts.")

                best_buy_opportunity = None

                for sym in active_scan_symbols:
                    if not self.telegram.is_active or not getattr(self, 'trading_active', False):
                        break

                    try:
                        df = self.exchange.fetch_ohlcv(sym, config.timeframe, limit=100)
                        pos_for_sym = self._find_position(sym)
                        signal, reason, meta = self.strategy.analyze(df, pos_for_sym)
                        meta['signal'] = signal
                        meta['reason'] = reason
                        meta['symbol'] = sym
                        self.latest_meta = meta
                        if pos_for_sym:
                            self.active_position_metas[sym] = meta

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

                        can_open_new = len(self.active_positions) < self.max_concurrent_positions
                        if signal == 'BUY' and can_open_new and not pos_for_sym:
                            if best_buy_opportunity is None or meta.get('rsi', 100) < best_buy_opportunity['meta'].get('rsi', 100):
                                best_buy_opportunity = {'symbol': sym, 'meta': meta, 'reason': reason}
                        elif signal == 'SELL' and pos_for_sym:
                            amount = pos_for_sym['amount']
                            entry_p = pos_for_sym['entry_price']
                            curr_p = meta['price']
                            pnl_pct = ((curr_p - entry_p) / entry_p) * 100

                            logger.info(f"Executing SELL for {sym} ({amount:.4f} coins @ ${curr_p:.2f}). Reason: {reason}")
                            orders = self.exchange.execute_smart_order('sell', amount, curr_p, symbol=sym)

                            # If Stop-Loss triggered, activate 15-minute symbol cooldown to prevent re-entering a dumping coin
                            if "STOP LOSS" in reason.upper() or "STOP_LOSS" in reason.upper():
                                cooldown_until = time.time() + (15 * 60)
                                self.rejected_cooldowns[sym] = cooldown_until
                                logger.warning(f"🛑 STOP-LOSS HIT on {sym}. Symbol cooldown activated for 15 minutes.")

                            self.trade_actions.appendleft({
                                'timestamp': int(time.time() * 1000),
                                'time': time.strftime("%H:%M:%S"),
                                'symbol': sym,
                                'side': 'SELL',
                                'amount': amount,
                                'price': curr_p,
                                'entry_price': entry_p,
                                'pnl_pct': round(pnl_pct, 2),
                                'pnl_usdt': round((curr_p - entry_p) * amount, 4),
                                'reason': reason,
                                'status': 'FILLED'
                            })

                            self.scan_logs.appendleft({
                                'time': time.strftime("%H:%M:%S"),
                                'symbol': sym,
                                'price': curr_p,
                                'signal': 'SELL',
                                'reason': f"🔴 SOLD {amount:.4f} @ ${curr_p:.4f} | PnL: {pnl_pct:+.2f}% | {reason}",
                                'rsi': meta.get('rsi', 0.0),
                                'trend': meta.get('trend', 'UNKNOWN')
                            })

                            await self.telegram.send_alert(
                                f"🔴 *SELL ORDER EXECUTED (Quant Engine)*\n"
                                f"• Pair: `{sym}`\n"
                                f"• Exit Price: `${curr_p:.2f}` (Entry: `${entry_p:.2f}`)\n"
                                f"• PnL: `{pnl_pct:+.2f}%`\n"
                                f"• Execution: `Limit Offset + Iceberg`\n"
                                f"• Reason: {reason}"
                            )
                            self.active_positions = [p for p in self.active_positions if p.get('symbol') != sym]
                            self.active_position_metas.pop(sym, None)
                            self._save_positions()
                            break
                    except Exception as scan_err:
                        logger.error(f"Error scanning {sym}: {scan_err}")

                # Process Best Buy Opportunity / Auto-Swap Position Rotation
                should_auto_swap = False
                stagnant_info = None

                if best_buy_opportunity:
                    pos_age_min = 0.0
                    pnl_pct = 0.0
                    stagnant_sym = None

                    if self.active_positions:
                        # Find the most stagnant position for auto-swap (only if at max capacity)
                        for pos in self.active_positions:
                            stagnant_sym = pos.get('symbol')
                            if stagnant_sym == best_buy_opportunity['symbol']:
                                continue
                            entry_p = pos.get('entry_price', 1.0)
                            entry_t = pos.get('entry_time', now)
                            pos_age_min = (now - entry_t) / 60.0
                            pos_meta = self.active_position_metas.get(stagnant_sym, {})
                            curr_p = pos_meta.get('price', entry_p)
                            pnl_pct = ((curr_p - entry_p) / entry_p)

                            if pos_age_min >= 2.0 or pnl_pct < 0 or (-0.020 <= pnl_pct <= 0.008):
                                should_auto_swap = True
                                stagnant_info = {'symbol': stagnant_sym, 'amount': pos['amount'], 'price': curr_p, 'age': pos_age_min, 'pnl': pnl_pct}
                                logger.info(f"🔄 Candidate swap: {stagnant_sym} (Age: {pos_age_min:.1f}m, PnL: {pnl_pct*100:+.2f}%) → {best_buy_opportunity['symbol']}")
                                break

                    target_sym = best_buy_opportunity['symbol']
                    target_meta = best_buy_opportunity['meta']
                    target_reason = best_buy_opportunity['reason']

                    # Always evaluate AI Sentinel when a quantitative buy setup appears
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
                        # Execute Auto-Swap exit of stagnant position if needed (only when at max capacity)
                        if should_auto_swap and stagnant_info and len(self.active_positions) >= self.max_concurrent_positions:
                            logger.info(f"🔄 Executing AUTO-SWAP exit for stagnant position {stagnant_info['symbol']}...")
                            swap_successful = False
                            try:
                                self.exchange.execute_smart_order('sell', stagnant_info['amount'], stagnant_info['price'], symbol=stagnant_info['symbol'])

                                self.trade_actions.appendleft({
                                    'timestamp': int(time.time() * 1000),
                                    'time': time.strftime("%H:%M:%S"),
                                    'symbol': stagnant_info['symbol'],
                                    'side': 'SELL',
                                    'amount': stagnant_info['amount'],
                                    'price': stagnant_info['price'],
                                    'pnl_pct': round(stagnant_info['pnl'] * 100, 2),
                                    'pnl_usdt': round(stagnant_info['pnl'] * stagnant_info['amount'] * stagnant_info['price'], 4),
                                    'reason': f"🔄 AUTO-SWAP → {target_sym}",
                                    'status': 'FILLED'
                                })

                                self.scan_logs.appendleft({
                                    'time': time.strftime("%H:%M:%S"),
                                    'symbol': stagnant_info['symbol'],
                                    'price': stagnant_info['price'],
                                    'signal': 'SELL',
                                    'reason': f"🔄 AUTO-SWAP SOLD {stagnant_info['amount']:.4f} @ ${stagnant_info['price']:.4f} → {target_sym}",
                                    'rsi': 0.0,
                                    'trend': 'UNKNOWN'
                                })

                                await self.telegram.send_alert(
                                    f"🔄 *AUTO-SWAP POSITION ROTATION EXECUTED*\n"
                                    f"• Closed Stagnant Pair: `{stagnant_info['symbol']}`\n"
                                    f"• Stagnation Hold Time: `{stagnant_info['age']:.0f} min` (PnL: `{stagnant_info['pnl']*100:+.2f}%`)\n"
                                    f"• Reason: Swapping capital into hot momentum coin `{target_sym}`!"
                                )
                                self.active_positions = [p for p in self.active_positions if p.get('symbol') != stagnant_info['symbol']]
                                self.active_position_metas.pop(stagnant_info['symbol'], None)
                                self._save_positions()
                                swap_successful = True
                            except Exception as swap_sell_err:
                                logger.error(f"Auto-Swap sell error for {stagnant_info['symbol']}: {swap_sell_err}")
                                self.scan_logs.appendleft({
                                    'time': time.strftime("%H:%M:%S"),
                                    'symbol': stagnant_info['symbol'],
                                    'price': stagnant_info['price'],
                                    'signal': 'ERROR',
                                    'reason': f"⚠️ Помилка Auto-Swap на {config.active_exchange.upper()}: {swap_sell_err}"
                                })
                                if "apiKey" in str(swap_sell_err):
                                    await self.telegram.send_alert(
                                        f"⚠️ *Помилка авторизації {config.active_exchange.upper()}*:\n"
                                        f"Для виконання торгівлі у LIVE режимі перевірте API-ключі у файлі `.env`!"
                                    )

                        # Only proceed to buy if we have room (either from swap or under max)
                        if len(self.active_positions) >= self.max_concurrent_positions:
                            continue

                        try:
                            bal = self.exchange.fetch_balance()
                            usdt_free = bal.get('USDT', {}).get('free', 0.0)
                        except Exception as bal_err:
                            logger.error(f"Error fetching balance for trade execution: {bal_err}")
                            if "apiKey" in str(bal_err):
                                await self.telegram.send_alert(f"⚠️ *LIVE Mode Error*: Потрібно вказати API ключі у файлі `.env`!")
                            continue

                        # Split available capital across remaining position slots
                        slots_remaining = self.max_concurrent_positions - len(self.active_positions)
                        alloc_usdt = usdt_free / max(slots_remaining, 1)

                        is_allowed, amount, risk_reason = self.risk_manager.calculate_position_size(
                            alloc_usdt, target_meta['price']
                        )

                        if is_allowed:
                            try:
                                logger.info(f"Executing BUY for {target_sym} via Quant Engine: {risk_reason}")
                                orders = self.exchange.execute_smart_order('buy', amount, target_meta['price'], symbol=target_sym)

                                verdict_record['amount'] = amount
                                new_pos = {
                                    'symbol': target_sym,
                                    'entry_price': target_meta['price'],
                                    'amount': amount,
                                    'entry_time': time.time(),
                                    'order_id': orders[0].get('id') if orders else 'N/A'
                                }
                                self.active_positions.append(new_pos)
                                self._save_positions()

                                self.trade_actions.appendleft({
                                    'timestamp': int(time.time() * 1000),
                                    'time': time.strftime("%H:%M:%S"),
                                    'symbol': target_sym,
                                    'side': 'BUY',
                                    'amount': amount,
                                    'price': target_meta['price'],
                                    'pnl_pct': None,
                                    'pnl_usdt': None,
                                    'reason': target_reason,
                                    'status': 'FILLED'
                                })

                                self.scan_logs.appendleft({
                                    'time': time.strftime("%H:%M:%S"),
                                    'symbol': target_sym,
                                    'price': target_meta['price'],
                                    'signal': 'BUY',
                                    'reason': f"🟢 BOUGHT {amount:.4f} @ ${target_meta['price']:.4f} | {target_reason}",
                                    'rsi': target_meta.get('rsi', 0.0),
                                    'trend': target_meta.get('trend', 'UNKNOWN')
                                })

                                await self.telegram.send_alert(
                                    f"🟢 *BUY ORDER EXECUTED (Quant Engine)*\n"
                                    f"• Pair: `{target_sym}`\n"
                                    f"• Price: `${target_meta['price']:.2f}`\n"
                                    f"• Amount: `{amount:.4f}`\n"
                                    f"• Active Positions: `{len(self.active_positions)}/{self.max_concurrent_positions}`\n"
                                    f"• Execution: `Limit Offset + Iceberg ({config.iceberg_slices} slices)`\n"
                                    f"• Strategy Reason: {target_reason}\n"
                                    f"• {llm_reason}"
                                )
                            except Exception as order_err:
                                verdict_record['status'] = 'EXCHANGE_REJECTED'
                                verdict_record['reason'] += f" | ⚠️ Exchange Error: {order_err}"
                                logger.error(f"Exchange Order Execution Error for {target_sym}: {order_err}")
                                await self.telegram.send_alert(f"⚠️ *Exchange Order Rejected*: {order_err}")
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
    print("=" * 65, flush=True)
    print("🚀 CRYPTO TRADING BOT SERVER INITIALIZING...", flush=True)
    print("🌐 WEB DASHBOARD: http://127.0.0.1:5001", flush=True)
    print("🔑 LOGIN: yuhim1308@gmail.com | PASSWORD: admin", flush=True)
    print("=" * 65, flush=True)

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
