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
        self.trade_actions: deque = self._load_trade_history()
        self._sync_wallet_positions()
        self.latest_meta: Dict[str, Any] = {}
        self.active_position_metas: Dict[str, Dict[str, Any]] = {}
        self.rejected_cooldowns: Dict[str, float] = {}
        self.scan_logs = deque(maxlen=30)
        self.ai_verdicts = deque(maxlen=30)
        self.max_concurrent_positions = 3

        # Trading active flag and daily trade counter for micro-capital protection
        self.trading_active = True
        self.daily_trades_count: int = 0
        self.last_trade_day: str = time.strftime("%Y-%m-%d")

        # Initialize Telegram
        self.telegram = TelegramInterface(
            get_status_fn=self.get_bot_status_str,
            get_balance_fn=self.get_balance_str
        )

    def _load_trade_history(self) -> deque:
        history_file = os.path.join(os.path.dirname(__file__), "data", "trade_history.json")
        items = []
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                    if not isinstance(items, list):
                        items = []
            except Exception as e:
                logger.error(f"Error loading trade_history.json: {e}")
        return deque(items, maxlen=500)

    def _save_trade_history(self) -> None:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        history_file = os.path.join(data_dir, "trade_history.json")
        try:
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.trade_actions), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving trade_history.json: {e}")
        
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

    def _sync_wallet_positions(self) -> None:
        """Picks up pre-existing non-USDT wallet holdings (e.g. leftover dust) not yet
        tracked in position.json and registers them as managed positions, so the normal
        SELL logic (RSI overbought / TP / SL) can evaluate and liquidate them."""
        if config.paper_trading:
            return
        try:
            balance = self.exchange.fetch_balance()
        except Exception as e:
            logger.warning(f"Could not sync wallet positions: {e}")
            return

        tracked_symbols = {p.get('symbol') for p in self.active_positions}
        ignore_keys = {'USDT', 'USDC', 'USD', 'INFO', 'FREE', 'USED', 'TOTAL', 'DATETIME', 'TIMESTAMP'}
        min_sellable_usdt = 5.0  # Bybit/Binance spot MIN_NOTIONAL floor

        for coin, info in balance.items():
            if not isinstance(info, dict) or coin.upper() in ignore_keys:
                continue
            total_amount = float(info.get('total', 0) or info.get('free', 0) or 0)
            if total_amount <= 0:
                continue
            symbol = f"{coin}/USDT"
            if symbol in tracked_symbols:
                continue
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price = float(ticker.get('last') or ticker.get('close') or 0)
            except Exception:
                continue
            if price <= 0:
                continue
            value_usdt = total_amount * price
            if value_usdt < min_sellable_usdt:
                continue

            # Auto-cancel any old open limit order on Bybit to release locked balance
            if hasattr(self.exchange, 'cancel_all_orders'):
                try:
                    self.exchange.cancel_all_orders(symbol)
                except Exception as c_err:
                    logger.debug(f"Auto-cancel on sync for {symbol}: {c_err}")

            # Re-fetch free amount after cancelling open orders
            free_amount = total_amount
            try:
                refetched = self.exchange.fetch_balance()
                free_amount = float(refetched.get(coin, {}).get('free', total_amount) or total_amount)
            except Exception:
                pass

            self.active_positions.append({
                'symbol': symbol,
                'amount': free_amount,
                'entry_price': price,
                'highest_price': price,
                'is_paper': False,
                'entry_time': time.time(),
            })
            logger.info(f"📦 Synced existing wallet holding as managed position: {symbol} ({free_amount:.4f} ≈ ${value_usdt:.2f})")

        self._save_positions()

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

    def check_position_health(self, symbol: str, position: Dict[str, Any], meta: Dict[str, Any]) -> Optional[str]:
        """Decides whether an open position is still worth holding.

        Entry conditions can go stale while a position is open (trend flips, price
        goes nowhere for hours, RSI overheats). Returns a human-readable exit reason
        when the position should be liquidated early, or None to keep holding.
        """
        if not getattr(config, 'use_position_health_check', True):
            return None

        entry_price = float(position.get('entry_price', 0) or 0)
        current_price = float(meta.get('price', 0) or 0)
        if entry_price <= 0 or current_price <= 0:
            return None

        age_minutes = (time.time() - float(position.get('entry_time', time.time()))) / 60.0
        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
        ema_fast = float(meta.get('ema_fast', 0) or 0)
        ema_slow = float(meta.get('ema_slow', 0) or 0)
        rsi = float(meta.get('rsi', 50) or 50)

        # ⚡ FAST MATH / BREAKOUT SNIPER SPECIAL GUARDIAN:
        # If position was opened on a 0ms Math Breakout pump, it MUST explode into profit immediately.
        # If after 1.5 minutes (90s) it hasn't gained +0.20% or is in negative PnL, exit IMMEDIATELY!
        is_breakout_entry = bool(position.get('is_breakout') or '0ms Math' in position.get('entry_reason', '') or 'BREAKOUT' in position.get('entry_reason', ''))
        if is_breakout_entry:
            if age_minutes >= 1.5 and pnl_pct < 0.20:
                return (f"⚡ FAST MATH BREAKOUT TIMEOUT: Імпульс не вистрілив за {age_minutes:.1f} хв "
                        f"(PnL: {pnl_pct:+.2f}% < +0.20%), миттєвий вихід для захисту капіталу.")

        # Grace period for standard dip-buy entries
        if age_minutes < config.health_min_hold_minutes:
            return None

        # 1. Trend flipped bearish — the reason we entered no longer holds.
        if ema_fast > 0 and ema_slow > 0 and ema_fast < ema_slow:
            return (f"🚨 EMERGENCY EXIT: Тренд перевернувся на BEARISH "
                    f"(EMA{config.ema_fast}={ema_fast:.6f} < EMA{config.ema_slow}={ema_slow:.6f}), PnL: {pnl_pct:+.2f}%")

        # Dynamic physics-based stagnation timeout by entry module
        entry_reason = str(position.get('entry_reason', '') or position.get('reason', '')).upper()
        is_breakout = bool('BREAKOUT' in entry_reason or position.get('is_breakout', False))
        is_dip = bool('DIP' in entry_reason or 'OVERSOLD' in entry_reason or 'ВІДСКОК' in entry_reason)

        if is_breakout:
            max_stagnation_time = 10.0  # 10 min for Breakouts (fakeout check)
            min_required_pnl = 0.30     # Must produce +0.30% momentum
            module_name = "BREAKOUT"
        elif is_dip:
            max_stagnation_time = 25.0  # 25 min for Dip Reversals (liquidity accumulation)
            min_required_pnl = 0.20     # Lower threshold for bottom bounces
            module_name = "DIP_REVERSAL"
        else:
            max_stagnation_time = 15.0  # Standard fallback
            min_required_pnl = 0.20
            module_name = "STANDARD"

        # Module-Specific Micro-Profit Exit: If after max_stagnation_time PnL >= min_required_pnl, lock in profit!
        if age_minutes >= max_stagnation_time and pnl_pct >= min_required_pnl:
            return f"💰 {module_name} PROFIT EXIT: Зафіксовано прибуток {pnl_pct:+.2f}% за {age_minutes:.1f} хв!"

        # Module-Specific Stagnation Exit: If after max_stagnation_time PnL < min_required_pnl, cut position!
        if age_minutes >= max_stagnation_time and pnl_pct < min_required_pnl:
            return (f"⏰ {module_name} STAGNATION EXIT ({max_stagnation_time:.0f} хв): Позиція не виросла вище +{min_required_pnl:.2f}% "
                    f"за {age_minutes:.1f} хв (PnL: {pnl_pct:+.2f}%), вивільняємо депозит для нових угод.")

        # RSI overheated while in profit — bank it before the pullback (non-breakout trades).
        if rsi > config.health_rsi_overheat and pnl_pct > 0 and not is_breakout:
            return f"💰 PROFIT PROTECTION: RSI перегрітий ({rsi:.1f}), фіксуємо прибуток {pnl_pct:+.2f}%"

        return None

    def convert_dust_to_usdt(self) -> Dict[str, Any]:
        """Converts unsellable leftover balances to USDT.

        Partial fills and lot-size rounding leave amounts too small to ever clear the
        exchange's minimum order value, so they accumulate as untradeable dust. Coins
        that are large enough to sell as a normal order are left alone — the strategy
        should decide those, not a cleanup routine.
        """
        if config.paper_trading:
            return {'converted': [], 'skipped': [], 'error': 'Not available in paper trading mode'}
        if not hasattr(self.exchange, 'fetch_convert_candidates'):
            return {'converted': [], 'skipped': [], 'error': 'Convert not supported on this exchange'}

        min_sellable = getattr(config, 'min_order_usdt', 5.5)
        converted, skipped = [], []

        for cand in self.exchange.fetch_convert_candidates():
            coin = cand['coin']
            symbol = f"{coin}/USDT"
            held_symbols = {p.get('symbol') for p in self.active_positions}
            if symbol in held_symbols:
                skipped.append({'coin': coin, 'why': 'open position'})
                continue

            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price = float(ticker.get('last') or ticker.get('close') or 0)
            except Exception:
                price = 0.0

            value = cand['balance'] * price
            if price > 0 and value >= min_sellable:
                skipped.append({'coin': coin, 'why': f'sellable as order (${value:.2f})'})
                continue

            amount = min(cand['balance'], cand['max_amount']) if cand['max_amount'] > 0 else cand['balance']
            try:
                res = self.exchange.convert_to_usdt(coin, amount)
                logger.info(f"♻️ Converted dust: {amount} {coin} → {res.get('to_amount')} USDT")
                converted.append({
                    'coin': coin,
                    'amount': amount,
                    'usdt_received': res.get('to_amount'),
                    'usd_value': round(value, 4),
                })
            except Exception as e:
                logger.error(f"Dust conversion failed for {coin}: {e}")
                skipped.append({'coin': coin, 'why': f'convert failed: {e}'})

        return {'converted': converted, 'skipped': skipped, 'error': None}

    async def close_position_market(self, symbol: str, reason: str, cooldown_minutes: int = 0) -> tuple:
        """Sells an open position on the exchange and drops it from tracking.

        Single path used by strategy SELL signals, emergency exits and the dashboard's
        manual close, so a closed position always means a real order was submitted.
        Returns (success: bool, message: str).
        """
        position = self._find_position(symbol)
        if not position:
            return False, f"No active position found for {symbol}"

        amount = float(position.get('amount', 0) or 0)
        entry_price = float(position.get('entry_price', 0) or 0)
        meta = self.active_position_metas.get(symbol, {})
        current_price = float(meta.get('price', 0) or 0)

        if current_price <= 0:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = float(ticker.get('last') or ticker.get('close') or entry_price)
            except Exception as e:
                logger.warning(f"Could not fetch exit price for {symbol}: {e}")
                current_price = entry_price

        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

        # Fetch exact free coin balance from wallet to prevent precision/dust overflow
        coin = symbol.split('/')[0]
        try:
            bal = self.exchange.fetch_balance()
            free_coin = float(bal.get(coin, {}).get('free', 0.0) or 0.0)
            if free_coin > 0:
                amount = free_coin
        except Exception as b_err:
            logger.debug(f"Balance check prior to SELL for {symbol}: {b_err}")

        logger.info(f"Executing SELL for {symbol} ({amount:.4f} coins @ ${current_price:.6f}). Reason: {reason}")
        try:
            self.exchange.execute_smart_order('sell', amount, current_price, symbol=symbol)
            status = 'FILLED'
            error_msg = None
        except Exception as order_err:
            status = 'EXCHANGE_REJECTED'
            error_msg = str(order_err)
            logger.error(f"SELL order rejected for {symbol}: {order_err}")

        if status == 'FILLED':
            self.trade_actions.appendleft({
                'timestamp': int(time.time() * 1000),
                'time': time.strftime("%H:%M:%S"),
                'symbol': symbol,
                'side': 'SELL',
                'amount': amount,
                'price': current_price,
                'entry_price': entry_price,
                'pnl_pct': round(pnl_pct, 2),
                'pnl_usdt': round((current_price - entry_price) * amount, 4),
                'reason': reason,
                'status': 'FILLED'
            })
            self._save_trade_history()

        if status == 'EXCHANGE_REJECTED':
            is_precision_or_balance_error = any(
                term in error_msg.lower() for term in ['insufficient balance', '170131', 'precision', 'minimum amount', 'less than minimum', 'min_notional']
            )
            if is_precision_or_balance_error:
                try:
                    bal = self.exchange.fetch_balance()
                    free_coin = float(bal.get(coin, {}).get('free', 0.0) or 0.0)
                    coin_val = free_coin * current_price
                    if coin_val < 5.00:
                        logger.info(f"🧹 Clearing dust position {symbol} (Free coin value ${coin_val:.4f} < $5 spot order minimum)")
                        self.active_positions = [p for p in self.active_positions if p.get('symbol') != symbol]
                        self.active_position_metas.pop(symbol, None)
                        self._save_positions()
                        return True, f"Dust position {symbol} cleared (${coin_val:.4f} < $5.00 CEX minimum)"
                except Exception as b_check_err:
                    logger.error(f"Error checking balance after sell rejection: {b_check_err}")

            return False, f"Exchange rejected SELL for {symbol}: {error_msg}"

        self.scan_logs.appendleft({
            'time': time.strftime("%H:%M:%S"),
            'symbol': symbol,
            'price': current_price,
            'signal': 'SELL',
            'reason': f"🔴 SOLD {amount:.4f} @ ${current_price:.6f} | PnL: {pnl_pct:+.2f}% | {reason}",
            'rsi': meta.get('rsi', 0.0),
            'trend': meta.get('trend', 'UNKNOWN')
        })

        # Feature 1: 45-Minute Symbol Lock for Stagnation, Emergency exits or non-profit exits (< +0.10%)
        if 'STAGNATION' in reason.upper() or 'EMERGENCY' in reason.upper() or 'TIMEOUT' in reason.upper() or pnl_pct < 0.0010:
            effective_cooldown = 45  # 45 minutes symbol lock
            logger.info(f"🔒 {symbol} заблоковано на 45 хв через невдалий/флетовий вихід ({reason}).")
        else:
            effective_cooldown = max(cooldown_minutes, 20)  # 20 minutes for normal exits
            logger.info(f"🔒 {symbol} заблоковано на {effective_cooldown} хв після виходу.")

        self.rejected_cooldowns[symbol] = time.time() + (effective_cooldown * 60)

        self.active_positions = [p for p in self.active_positions if p.get('symbol') != symbol]
        self.active_position_metas.pop(symbol, None)
        self._save_positions()

        await self.telegram.send_alert(
            f"🔴 *SELL ORDER EXECUTED*\n"
            f"• Pair: `{symbol}`\n"
            f"• Exit Price: `${current_price:.6f}` (Entry: `${entry_price:.6f}`)\n"
            f"• PnL: `{pnl_pct:+.2f}%`\n"
            f"• Reason: {reason}"
        )

        # Feature 2: Global 3-Minute Pause after ANY trade close to let orderbook settle
        logger.info("💤 Глобальна пауза 3 хвилини після закриття угоди. Аналіз ринку призупинено для стабілізації стакану.")
        await asyncio.sleep(180)

        return True, f"Position {symbol} sold at ${current_price:.6f} (PnL: {pnl_pct:+.2f}%)"

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

        # Force cancel all resting open orders on any coins to unlock locked balance on CEX
        if not config.paper_trading:
            try:
                bal = self.exchange.fetch_balance()
                for coin, info in bal.items():
                    if isinstance(info, dict) and coin.upper() not in {'USDT', 'USDC', 'USD', 'INFO', 'FREE', 'USED', 'TOTAL', 'DATETIME', 'TIMESTAMP'}:
                        used_qty = float(info.get('used', 0) or 0)
                        if used_qty > 0:
                            sym = f"{coin}/USDT"
                            logger.info(f"🔓 Cancelling resting open orders on Bybit for {sym} to unlock {used_qty} coins...")
                            try:
                                self.exchange.exchange.cancel_all_orders(sym)
                            except Exception as c_err:
                                logger.warning(f"Could not cancel open orders for {sym}: {c_err}")
            except Exception as b_err:
                logger.warning(f"Unlock balance check error: {b_err}")

        while True:
            try:
                # Sync any untracked tradable coins in wallet (>= $5.00) into active_positions
                if not config.paper_trading:
                    try:
                        self._sync_wallet_positions()
                    except Exception as sync_err:
                        logger.debug(f"Wallet position sync error: {sync_err}")

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

                # Filter out symbols currently in Cooldown after LLM rejection.
                now = time.time()
                held_symbols = {p.get('symbol') for p in self.active_positions}
                active_scan_symbols = [
                    s for s in symbols_to_scan
                    if s in held_symbols or now >= self.rejected_cooldowns.get(s, 0)
                ]

                # --- 🎯 DUAL-MODE MARKET REGIME DETECTION (HUNT ↔ STABLE) ---
                if config.use_dual_mode_scanner:
                    for sp in config.stable_pairs:
                        if sp not in active_scan_symbols:
                            active_scan_symbols.append(sp)

                recent_rsis = []
                for sym_check in active_scan_symbols[:10]:
                    meta_cached = self.active_position_metas.get(sym_check) or {}
                    if meta_cached.get('rsi'):
                        recent_rsis.append(meta_cached['rsi'])

                avg_market_rsi = (sum(recent_rsis) / len(recent_rsis)) if recent_rsis else 50.0
                is_overheated = (avg_market_rsi >= config.market_overheat_rsi_threshold)
                current_regime = "STABLE" if is_overheated else "HUNT"

                self.latest_market_regime = {
                    'mode': current_regime,
                    'avg_rsi': round(avg_market_rsi, 1),
                    'is_overheated': is_overheated
                }

                # Check BTC Gravity Shield: If Bitcoin is dumping on 5m, pause new altcoin BUY entries
                btc_is_dumping = self.is_btc_dumping()
                if btc_is_dumping:
                    logger.warning("📉 [BTC GRAVITY SHIELD]: BTC is dumping on 5m timeframe. Altcoin BUY signals paused to prevent fakeouts.")

                best_buy_opportunity = None
                suppressed_buys = []

                for sym in active_scan_symbols:
                    if not self.telegram.is_active or not getattr(self, 'trading_active', False):
                        break

                    try:
                        df = self.exchange.fetch_ohlcv(sym, config.timeframe, limit=100)
                        pos_for_sym = self._find_position(sym)
                        signal, reason, meta = self.strategy.analyze(df, pos_for_sym, symbol=sym)
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

                        # Adaptive position management: bail out early if the conditions we
                        # entered on no longer hold, before TP/SL has a chance to trigger.
                        if pos_for_sym and signal != 'SELL':
                            health_reason = self.check_position_health(sym, pos_for_sym, meta)
                            if not health_reason:
                                entry_t = float(pos_for_sym.get('entry_time', time.time()))
                                age_m = (time.time() - entry_t) / 60.0
                                entry_p = float(pos_for_sym.get('entry_price', 0) or meta.get('price', 0))
                                curr_p = float(meta.get('price', 0) or entry_p)
                                pnl = ((curr_p - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0
                                if age_m >= 5.0:
                                    entry_reason_str = str(pos_for_sym.get('reason') or pos_for_sym.get('entry_reason') or 'DIP_REVERSAL')
                                    should_llm_exit, llm_health_msg = await self.llm_analyst.evaluate_active_position_health(
                                        sym, config.timeframe, meta, age_m, pnl, entry_reason=entry_reason_str
                                    )
                                    if should_llm_exit:
                                        health_reason = llm_health_msg

                            if health_reason:
                                logger.warning(f"[{sym}] {health_reason}")
                                await self.close_position_market(
                                    sym, health_reason,
                                    cooldown_minutes=config.emergency_exit_cooldown_minutes
                                )
                                break

                        if signal == 'BUY' and not pos_for_sym and getattr(config, 'monitor_only', False):
                            suppressed_buys.append(sym)

                        if signal == 'BUY' and not pos_for_sym and not getattr(config, 'monitor_only', False):
                            if best_buy_opportunity is None or meta.get('rsi', 100) < best_buy_opportunity['meta'].get('rsi', 100):
                                best_buy_opportunity = {'symbol': sym, 'meta': meta, 'reason': reason}
                        elif signal == 'SELL' and pos_for_sym:
                            # Stop-loss exits get a shorter cooldown than emergency exits so the
                            # bot does not immediately re-enter a coin that is actively dumping.
                            is_stop_loss = "STOP LOSS" in reason.upper() or "STOP_LOSS" in reason.upper()
                            if is_stop_loss:
                                logger.warning(f"🛑 STOP-LOSS HIT on {sym}. Symbol cooldown activated for 15 minutes.")
                            await self.close_position_market(
                                sym, reason,
                                cooldown_minutes=15 if is_stop_loss else 0
                            )
                            break
                    except Exception as scan_err:
                        logger.error(f"Error scanning {sym}: {scan_err}")

                # Surface suppressed entries once per cycle — otherwise monitor-only mode
                # looks identical to "the strategy found nothing".
                if suppressed_buys:
                    note = f"👁 MONITOR ONLY: вхід заблоковано ({', '.join(suppressed_buys[:5])}"
                    note += f" +{len(suppressed_buys) - 5} ще)" if len(suppressed_buys) > 5 else ")"
                    logger.info(note)
                    self.scan_logs.appendleft({
                        'time': time.strftime("%H:%M:%S"),
                        'symbol': suppressed_buys[0],
                        'price': 0.0,
                        'signal': 'HOLD',
                        'reason': note,
                        'rsi': 0.0,
                        'trend': 'MONITOR'
                    })

                # Process Best Buy Opportunity / Auto-Swap Position Rotation
                should_auto_swap = False
                stagnant_info = None

                if best_buy_opportunity:
                    pos_age_min = 0.0
                    pnl_pct = 0.0
                    stagnant_sym = None

                    if self.active_positions:
                        # Find truly stagnant/losing position for auto-swap (minimum 10-minute hold time & significant RSI delta)
                        for pos in self.active_positions:
                            stagnant_sym = pos.get('symbol')
                            if stagnant_sym == best_buy_opportunity['symbol']:
                                continue

                            # 🛡️ BLUE CHIP PRIORITY SHIELD: FORBIDDEN to auto-swap out of BTC/ETH/SOL/BNB into altcoins!
                            is_blue_chip = any(bp in stagnant_sym for bp in ["BTC", "ETH", "SOL", "BNB"]) or (stagnant_sym in getattr(config, 'stable_pairs', []))
                            if is_blue_chip:
                                continue

                            entry_p = pos.get('entry_price', 1.0)
                            entry_t = pos.get('entry_time', now)
                            pos_age_min = (now - entry_t) / 60.0
                            pos_meta = self.active_position_metas.get(stagnant_sym, {})
                            curr_p = pos_meta.get('price', entry_p)
                            curr_rsi = float(pos_meta.get('rsi', 50) or 50)
                            target_rsi = float(best_buy_opportunity['meta'].get('rsi', 50) or 50)
                            pnl_pct = ((curr_p - entry_p) / entry_p)

                            # Strict Swap Rule: Position MUST be an altcoin in real loss (PnL <= -0.30%) and held >= 10 min to be swapped! Never swap out of green positions or Blue Chips!
                            is_stagnant_time = (pos_age_min >= 10.0 and pnl_pct < -0.0030)
                            is_deep_loss = (pnl_pct <= -0.015)
                            rsi_substantially_better = (target_rsi < (curr_rsi - 10.0))

                # Daily Trade Limit Check (Max 12 trades / day to prevent fee erosion)
                current_day = time.strftime("%Y-%m-%d")
                if current_day != self.last_trade_day:
                    self.last_trade_day = current_day
                    self.daily_trades_count = 0

                if self.daily_trades_count >= 12:
                    if not hasattr(self, '_limit_logged_day') or self._limit_logged_day != current_day:
                        logger.info("🛑 Денний ліміт угод (12/12) вичерпано. Бот переходить у режим очікування до завтра.")
                        self._limit_logged_day = current_day
                    best_buy_opportunity = None

                if best_buy_opportunity:
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
                        continue

                    # Check if balance is low or position limit reached
                    try:
                        curr_bal = self.exchange.fetch_balance()
                        curr_usdt = curr_bal.get('USDT', {}).get('free', 0.0)
                    except Exception:
                        curr_usdt = 0.0

                    need_swap_for_balance = (curr_usdt < getattr(config, 'min_order_usdt', 5.50))
                    at_max_positions = (len(self.active_positions) >= self.max_concurrent_positions)

                    # Execute Auto-Swap exit of stagnant position if needed (when at max capacity or low balance)
                    if should_auto_swap and stagnant_info and (at_max_positions or need_swap_for_balance):
                        logger.info(f"🔄 Executing AUTO-SWAP exit for stagnant position {stagnant_info['symbol']} → {target_sym}...")
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
                            self._save_trade_history()

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
                            await asyncio.sleep(1.2)  # Give CEX 1.2s to settle sold USDT into available balance
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

                    usdt_free = 0.0
                    for bal_attempt in range(3):
                        try:
                            bal = self.exchange.fetch_balance()
                            usdt_free = bal.get('USDT', {}).get('free', 0.0)
                            if usdt_free >= 5.0 or bal_attempt == 2:
                                break
                            await asyncio.sleep(0.8)
                        except Exception as bal_err:
                            logger.error(f"Error fetching balance for trade execution: {bal_err}")
                            if "apiKey" in str(bal_err):
                                await self.telegram.send_alert(f"⚠️ *LIVE Mode Error*: Потрібно вказати API ключі у файлі `.env`!")
                            await asyncio.sleep(0.5)

                    # Dynamically query CEX min_notional limit for target symbol (e.g. $1.00 - $5.00 on Bybit)
                    symbol_min_notional = 1.0
                    try:
                        if hasattr(self.exchange, 'get_min_notional'):
                            symbol_min_notional = self.exchange.get_min_notional(target_sym)
                    except Exception:
                        pass

                    is_allowed, amount, risk_reason = self.risk_manager.calculate_position_size(
                        usdt_free, target_meta['price'], min_notional=symbol_min_notional
                    )

                    if is_allowed:
                        try:
                            logger.info(f"Executing BUY for {target_sym} via Quant Engine: {risk_reason}")
                            orders = self.exchange.execute_smart_order('buy', amount, target_meta['price'], symbol=target_sym)
                            self.daily_trades_count += 1
                            logger.info(f"📊 Daily trades count: {self.daily_trades_count}/12")

                            verdict_record['amount'] = amount
                            new_pos = {
                                'symbol': target_sym,
                                'entry_price': target_meta['price'],
                                'amount': amount,
                                'entry_time': time.time(),
                                'order_id': orders[0].get('id') if orders else 'N/A',
                                'is_breakout': bool('0ms Math' in target_reason or 'BREAKOUT' in target_reason),
                                'entry_reason': target_reason
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
                            self._save_trade_history()

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
    print("🦝 CRYPTONASUA TRADING TERMINAL INITIALIZING...", flush=True)
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
