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
        self.ai_verdicts: deque = self._load_ai_verdicts()
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

    def _load_ai_verdicts(self) -> deque:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        file_name = "paper_ai_verdicts.json" if config.paper_trading else "live_ai_verdicts.json"
        verdicts_file = os.path.join(data_dir, file_name)
        legacy_file = os.path.join(data_dir, "ai_verdicts.json")

        target_file = verdicts_file
        if not os.path.exists(verdicts_file) and os.path.exists(legacy_file):
            if config.paper_trading:
                target_file = legacy_file

        items = []
        if os.path.exists(target_file):
            try:
                with open(target_file, 'r', encoding='utf-8') as f:
                    raw_items = json.load(f)
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            if not isinstance(item, dict):
                                continue
                            item_is_paper = item.get('is_paper', True if target_file == legacy_file else config.paper_trading)
                            if item_is_paper == config.paper_trading:
                                item['is_paper'] = item_is_paper
                                items.append(item)
            except Exception as e:
                logger.error(f"Error loading {target_file}: {e}")

        if target_file == legacy_file and config.paper_trading and items:
            try:
                with open(verdicts_file, 'w', encoding='utf-8') as f:
                    json.dump(items, f, indent=2)
            except Exception as e:
                logger.error(f"Error migrating legacy ai verdicts: {e}")

        return deque(items, maxlen=300)

    def _save_ai_verdicts(self) -> None:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        file_name = "paper_ai_verdicts.json" if config.paper_trading else "live_ai_verdicts.json"
        verdicts_file = os.path.join(data_dir, file_name)
        try:
            for item in self.ai_verdicts:
                if isinstance(item, dict):
                    item['is_paper'] = config.paper_trading
            with open(verdicts_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.ai_verdicts), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {file_name}: {e}")

    def _load_trade_history(self) -> deque:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        file_name = "paper_trade_history.json" if config.paper_trading else "live_trade_history.json"
        history_file = os.path.join(data_dir, file_name)
        legacy_file = os.path.join(data_dir, "trade_history.json")

        target_file = history_file
        if not os.path.exists(history_file) and os.path.exists(legacy_file):
            target_file = legacy_file

        items = []
        if os.path.exists(target_file):
            try:
                with open(target_file, 'r', encoding='utf-8') as f:
                    raw_items = json.load(f)
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            if not isinstance(item, dict):
                                continue
                            item_is_paper = item.get('is_paper', config.paper_trading)
                            item['is_paper'] = item_is_paper
                            items.append(item)
            except Exception as e:
                logger.error(f"Error loading {target_file}: {e}")

        # If targeted mode history is empty, import filled trades from legacy trade_history.json
        if not items and os.path.exists(legacy_file) and target_file != legacy_file:
            try:
                with open(legacy_file, 'r', encoding='utf-8') as f:
                    raw_items = json.load(f)
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            if isinstance(item, dict):
                                item['is_paper'] = config.paper_trading
                                items.append(item)
            except Exception as e:
                logger.error(f"Error importing legacy trade history: {e}")

        if target_file == legacy_file and config.paper_trading and items:
            try:
                with open(history_file, 'w', encoding='utf-8') as f:
                    json.dump(items, f, indent=2)
            except Exception as e:
                logger.error(f"Error migrating legacy trade history: {e}")

        return deque(items, maxlen=500)

    def _save_trade_history(self) -> None:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        file_name = "paper_trade_history.json" if config.paper_trading else "live_trade_history.json"
        history_file = os.path.join(data_dir, file_name)
        try:
            for item in self.trade_actions:
                if isinstance(item, dict):
                    item['is_paper'] = config.paper_trading
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.trade_actions), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {file_name}: {e}")

    def _load_positions(self) -> list:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        file_name = "paper_position.json" if config.paper_trading else "live_position.json"
        pos_file = os.path.join(data_dir, file_name)
        legacy_file = os.path.join(data_dir, "position.json")

        target_file = pos_file if os.path.exists(pos_file) else (legacy_file if os.path.exists(legacy_file) else None)
        if target_file and os.path.exists(target_file):
            try:
                with open(target_file, 'r', encoding='utf-8') as f:
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
                        if is_pos_paper != config.paper_trading and os.path.exists(pos_file):
                            continue
                        if 'entry_time' not in pos or not pos['entry_time']:
                            pos['entry_time'] = os.path.getmtime(target_file)
                        logger.info(f"📌 Loaded position: {pos.get('symbol')} (Paper: {is_pos_paper})")
                        valid.append(pos)
                    return valid
            except Exception as e:
                logger.error(f"Error loading {target_file}: {e}")
        return []

    def _save_positions(self) -> None:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        file_name = "paper_position.json" if config.paper_trading else "live_position.json"
        pos_file = os.path.join(data_dir, file_name)
        try:
            for p in self.active_positions:
                p['is_paper'] = config.paper_trading
                if 'entry_time' not in p or not p['entry_time']:
                    p['entry_time'] = time.time()
            with open(pos_file, 'w', encoding='utf-8') as f:
                json.dump(self.active_positions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {file_name}: {e}")

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

    def get_trade_statistics(self, limit: int = 15) -> Dict[str, Any]:
        """Calculates statistics for recent filled trades (Win Rate, Total PnL, consecutive losses, worst coin)."""
        filled_sells = [
            t for t in self.trade_actions 
            if isinstance(t, dict) and t.get('side') == 'SELL' and t.get('status') == 'FILLED'
        ]
        recent = filled_sells[:limit]
        total_trades = len(recent)
        
        if total_trades == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'total_pnl_usdt': 0.0,
                'total_pnl_pct': 0.0,
                'consecutive_losses': 0,
                'worst_symbol': 'N/A',
                'worst_pnl_usdt': 0.0,
                'recent_trades_summary': 'Немає закритої історії угод'
            }
        
        wins = sum(1 for t in recent if float(t.get('pnl_usdt', 0.0) or 0.0) > 0)
        win_rate = (wins / total_trades) * 100.0
        total_pnl_usdt = sum(float(t.get('pnl_usdt', 0.0) or 0.0) for t in recent)
        total_pnl_pct = sum(float(t.get('pnl_pct', 0.0) or 0.0) for t in recent)
        
        consecutive_losses = 0
        for t in recent:
            pnl_u = float(t.get('pnl_usdt', 0.0) or 0.0)
            if pnl_u < 0:
                consecutive_losses += 1
            else:
                break
                
        coin_pnl = {}
        for t in recent:
            sym = t.get('symbol', 'UNKNOWN')
            coin_pnl[sym] = coin_pnl.get(sym, 0.0) + float(t.get('pnl_usdt', 0.0) or 0.0)
        
        worst_sym = min(coin_pnl, key=coin_pnl.get) if coin_pnl else 'N/A'
        worst_pnl = coin_pnl.get(worst_sym, 0.0) if coin_pnl else 0.0
        
        summary_lines = []
        for t in recent[:8]:
            sym = t.get('symbol', '?')
            pnl_pct = float(t.get('pnl_pct', 0.0) or 0.0)
            pnl_u = float(t.get('pnl_usdt', 0.0) or 0.0)
            r = t.get('reason', '')
            summary_lines.append(f"- {sym}: PnL {pnl_pct:+.2f}% (${pnl_u:+.4f}) [{r}]")
            
        recent_summary = "\n".join(summary_lines)
        
        return {
            'total_trades': total_trades,
            'win_rate': round(win_rate, 1),
            'total_pnl_usdt': round(total_pnl_usdt, 4),
            'total_pnl_pct': round(total_pnl_pct, 2),
            'consecutive_losses': consecutive_losses,
            'worst_symbol': worst_sym,
            'worst_pnl_usdt': round(worst_pnl, 4),
            'recent_trades_summary': recent_summary
        }

    async def run_ai_risk_manager_briefing(self, force: bool = False) -> Dict[str, Any]:
        """
        Executes AI Chief Risk Manager review ("Ранковий Брифінг").
        Triggered periodically (every 24h), after every 10 trades, or on manual request.
        Sets Market Regime: ATTACK, CAUTION, or DEFENSE.
        """
        now = time.time()
        time_since_last = now - getattr(config, 'last_briefing_time', 0.0)
        trades_count = getattr(config, 'trades_since_last_briefing', 0)
        
        if not force and time_since_last < 7200 and trades_count < 5:
            return {
                'status': 'SKIPPED',
                'reason': f'Брифінг не потрібен (минуло {time_since_last/3600:.1f} год, угод з останнього брифінгу: {trades_count}/5)',
                'regime': getattr(config, 'current_ai_regime', 'ATTACK')
            }

        logger.info("🧠 Launching AI Pilot Statistical Briefing ('Ранковий Брифінг')...")
        stats = self.get_trade_statistics(limit=15)
        
        regime, reason, adj = await self.llm_analyst.evaluate_market_regime(stats)
        
        old_regime = getattr(config, 'current_ai_regime', 'ATTACK')
        config.current_ai_regime = regime
        config.ai_regime_reason = reason
        config.last_briefing_time = now
        config.trades_since_last_briefing = 0
        config.save_persisted_config()
        
        try:
            os.makedirs("data", exist_ok=True)
            with open("data/ai_regime_state.json", "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": now,
                    "regime": regime,
                    "reason": reason,
                    "stats": stats,
                    "adjustments": adj
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        if regime == "DEFENSE":
            config.defense_paused_until = now + (2 * 3600)  # 2-hour trading pause
            config.save_persisted_config()
            logger.warning(f"🔴 AI PILOT ACTIVATED DEFENSE MODE: {reason}")
            
            positions_to_close = list(self.active_positions)
            for pos in positions_to_close:
                sym = pos.get('symbol')
                if sym:
                    await self.close_position_market(sym, f"🛡️ DEFENSE MODE ACTIVATED ({reason})")
                    
            await self.telegram.send_alert(
                f"🛡️ *AI PILOT: DEFENSE MODE ACTIVATED*\n"
                f"• Режим: `🔴 DEFENSE` (Захист капіталу)\n"
                f"• Причина: `{reason}`\n"
                f"• Дія: Повна зупинка торгівлі, вихід у USDT на 2 години."
            )
            
        elif regime == "CAUTION":
            logger.warning(f"🟡 AI RISK MANAGER ACTIVATED CAUTION MODE: {reason}")
            await self.telegram.send_alert(
                f"🟡 *AI RISK MANAGER: CAUTION MODE ACTIVATED*\n"
                f"• Режим: `🟡 CAUTION` (Обережно)\n"
                f"• Причина: `{reason}`\n"
                f"• Дія: Торгівлю обмежено ТІЛЬКИ Top-5 Blue Chips (BTC, ETH, BNB, SOL, XRP). Ціль TP: +0.8%."
            )
        else:
            logger.info(f"🟢 AI RISK MANAGER ACTIVATED ATTACK MODE: {reason}")
            if old_regime != "ATTACK":
                await self.telegram.send_alert(
                    f"🟢 *AI RISK MANAGER: ATTACK MODE ACTIVATED*\n"
                    f"• Режим: `🟢 ATTACK` (Нормальна торгівля)\n"
                    f"• Причина: `{reason}`\n"
                    f"• Дія: Торгівлю волатильними альткоїнами (Top-25) відновлено."
                )

        return {
            'status': 'SUCCESS',
            'regime': regime,
            'reason': reason,
            'stats': stats,
            'adjustments': adj
        }

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

        entry_reason_upper = str(position.get('entry_reason', '') or position.get('reason', '')).upper()
        is_breakout = bool(
            position.get('is_breakout') or
            '0MS MATH' in entry_reason_upper or
            'BREAKOUT' in entry_reason_upper or
            'SNIPER' in entry_reason_upper or
            'PUMP' in entry_reason_upper
        )
        is_dip = bool('DIP' in entry_reason_upper or 'OVERSOLD' in entry_reason_upper or 'ВІДСКОК' in entry_reason_upper)

        # ⚡ FAST MATH / BREAKOUT SNIPER GUARDIAN:
        # Give breakout trades 15 minutes to unfold. Only exit if after 15m PnL hasn't reached +0.45%.
        if is_breakout:
            if age_minutes >= 15.0 and pnl_pct < 0.45:
                return (f"⚡ FAST MATH BREAKOUT TIMEOUT: Імпульс не вистрілив за {age_minutes:.1f} хв "
                        f"(PnL: {pnl_pct:+.2f}% < +0.45%), вихід для захисту капіталу.")

        # Grace period for standard dip-buy entries
        if age_minutes < config.health_min_hold_minutes:
            return None

        # 1. Trend flipped bearish — the reason we entered no longer holds.
        if ema_fast > 0 and ema_slow > 0 and ema_fast < ema_slow:
            return (f"🚨 EMERGENCY EXIT: Тренд перевернувся на BEARISH "
                    f"(EMA{config.ema_fast}={ema_fast:.6f} < EMA{config.ema_slow}={ema_slow:.6f}), PnL: {pnl_pct:+.2f}%")

        # Dynamic physics-based stagnation timeout by entry module
        if is_breakout:
            max_stagnation_time = 20.0  # 20 min for Breakouts (gives price room to breathe)
            min_required_pnl = 0.50     # Must produce +0.50% momentum (net gain after fees)
            module_name = "BREAKOUT"
        elif is_dip:
            max_stagnation_time = 30.0  # 30 min for Dip Reversals (liquidity accumulation)
            min_required_pnl = 0.45     # Net profit after exchange fees (+0.45%)
            module_name = "DIP_REVERSAL"
        else:
            max_stagnation_time = 25.0  # Standard fallback
            min_required_pnl = 0.45
            module_name = "STANDARD"

        # Module-Specific Micro-Profit Exit: If after max_stagnation_time PnL >= min_required_pnl, lock in profit!
        if age_minutes >= max_stagnation_time and pnl_pct >= min_required_pnl:
            return f"💰 {module_name} PROFIT EXIT: Зафіксовано прибуток {pnl_pct:+.2f}% за {age_minutes:.1f} хв!"

        # Module-Specific Stagnation Exit: If after max_stagnation_time PnL < min_required_pnl, cut position!
        if age_minutes >= max_stagnation_time and pnl_pct < min_required_pnl:
            return (f"⏰ {module_name} STAGNATION EXIT ({max_stagnation_time:.0f} хв): Позиція не виросла вище +{min_required_pnl:.2f}% "
                    f"за {age_minutes:.1f} хв (PnL: {pnl_pct:+.2f}%), вивільняємо депозит для нових угод.")

        # RSI overheated while in net profit (>= +0.45%) — bank it before the pullback (non-breakout trades).
        if rsi > config.health_rsi_overheat and pnl_pct >= 0.45 and not is_breakout:
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
        if not getattr(self.exchange, 'supports_convert', False):
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
            entry_time_val = target_pos.get('entry_time') if 'target_pos' in locals() and isinstance(target_pos, dict) else None
            entry_time_str = time.strftime("%H:%M:%S", time.localtime(entry_time_val)) if entry_time_val and entry_time_val < 1e11 else (time.strftime("%H:%M:%S", time.localtime(entry_time_val / 1000)) if entry_time_val else time.strftime("%H:%M:%S"))

            closed_record = {
                'timestamp': int(time.time() * 1000),
                'time': time.strftime("%H:%M:%S"),
                'entry_time': entry_time_str,
                'symbol': symbol,
                'side': 'SELL',
                'amount': amount,
                'price': current_price,
                'entry_price': entry_price,
                'pnl_pct': round(pnl_pct, 2),
                'pnl_usdt': round((current_price - entry_price) * amount, 4),
                'reason': reason,
                'status': 'FILLED'
            }
            self.trade_actions.appendleft(closed_record)
            self._save_trade_history()
            asyncio.create_task(self.llm_analyst.analyze_closed_trade(closed_record))

            config.trades_since_last_briefing = getattr(config, 'trades_since_last_briefing', 0) + 1
            config.save_persisted_config()
            if config.trades_since_last_briefing >= 5:
                asyncio.create_task(self.run_ai_risk_manager_briefing())

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

        # Mandatory Symbol Lock: 45 minutes for stagnation / emergency / timeout / PnL < +0.45% exits, 30 minutes for normal exits
        if 'STAGNATION' in reason.upper() or 'EMERGENCY' in reason.upper() or 'TIMEOUT' in reason.upper() or pnl_pct < 0.45:
            effective_cooldown = 45  # 45 minutes symbol lock
            logger.info(f"🔒 {symbol} заблоковано на 45 хв через невдалий/флетовий вихід ({reason}).")
        else:
            effective_cooldown = max(cooldown_minutes, 30)  # 30 minutes minimum for normal exits
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

    async def ingest_existing_trade_history_and_train(self) -> None:
        """
        Ingests completed trades from trade_history.json into AI learning memory,
        extracting AI post-mortem lessons for all historical completed trades.
        """
        filled_sells = [
            t for t in self.trade_actions 
            if isinstance(t, dict) and t.get('side') == 'SELL' and t.get('status') == 'FILLED'
        ]
        if not filled_sells:
            return

        # Check existing lessons to avoid duplicate calls
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        mem_file = os.path.join(data_dir, "ai_learning_memory.json")
        existing_symbols = set()
        if os.path.exists(mem_file):
            try:
                with open(mem_file, "r", encoding="utf-8") as f:
                    lessons = json.load(f)
                    existing_symbols = {l.get('symbol') for l in lessons if isinstance(l, dict)}
            except Exception:
                pass

        # Ingest top 10 recent completed trades into AI learning memory
        unprocessed = [t for t in filled_sells[:10] if t.get('symbol') not in existing_symbols]
        for t in unprocessed:
            try:
                await self.llm_analyst.analyze_closed_trade(t)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"Historical trade ingestion error: {e}")

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

        # Ingest historical trade history and trigger immediate AI Risk Manager briefing on startup
        try:
            logger.info("🧠 Ingesting historical trade records & executing initial AI Risk Manager briefing...")
            await self.ingest_existing_trade_history_and_train()
            await self.run_ai_risk_manager_briefing(force=True)
        except Exception as init_briefing_err:
            logger.warning(f"Initial AI Risk Manager briefing error: {init_briefing_err}")

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

                # 🛡️ DEFENSE Mode check: If DEFENSE mode is active and there are no active positions, pause buy scanning
                if time.time() < getattr(config, 'defense_paused_until', 0.0):
                    pause_left_min = int((config.defense_paused_until - time.time()) / 60)
                    if not hasattr(self, '_logged_defense_pause') or self._logged_defense_pause != pause_left_min:
                        logger.info(f"🛡️ DEFENSE Mode active: Trading paused for capital protection ({pause_left_min} min remaining).")
                        self._logged_defense_pause = pause_left_min
                    if not self.active_positions:
                        await asyncio.sleep(10)
                        continue

                # 🟡 CAUTION Mode check: Restrict trading strictly to Top-5 Blue Chips (BTC, ETH, BNB, SOL, XRP)
                if getattr(config, 'current_ai_regime', 'ATTACK') == "CAUTION":
                    symbols_to_scan = list(getattr(config, 'top5_blue_chips', ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]))
                # Determine pairs to scan (Always scan top hot pairs to enable live screener feed and Auto-Swap rotation)
                elif config.symbol == "AUTO" or getattr(config, 'use_dynamic_market_screener', False):
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

                        # Active positions exit ONLY on quantitative strategy signals (Take Profit, Stop Loss, Trailing Stop)

                        if signal == 'BUY' and not pos_for_sym and getattr(config, 'monitor_only', False):
                            suppressed_buys.append(sym)

                        if signal == 'BUY' and not pos_for_sym and not getattr(config, 'monitor_only', False):
                            if now < self.rejected_cooldowns.get(sym, 0):
                                cd_left = int((self.rejected_cooldowns[sym] - now) / 60)
                                scan_entry['signal'] = 'REJECTED'
                                scan_entry['reason'] = f"🔒 COOLDOWN LOCK ({cd_left}m remaining) | {reason}"
                                logger.info(f"🔒 Skipped BUY candidate {sym}: Symbol locked in Cooldown for {cd_left} min")
                                continue
                            if best_buy_opportunity is None or meta.get('rsi', 100) < best_buy_opportunity['meta'].get('rsi', 100):
                                best_buy_opportunity = {'symbol': sym, 'meta': meta, 'reason': reason}
                        elif signal == 'SELL' and pos_for_sym:
                            is_stop_loss = "STOP LOSS" in reason.upper() or "STOP_LOSS" in reason.upper()
                            if is_stop_loss:
                                logger.warning(f"🛑 STOP-LOSS HIT on {sym}. Symbol cooldown activated for 45 minutes.")
                            await self.close_position_market(
                                sym, reason,
                                cooldown_minutes=45 if is_stop_loss else 30
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
                    if now < self.rejected_cooldowns.get(target_sym, 0):
                        cd_left = int((self.rejected_cooldowns[target_sym] - now) / 60)
                        cd_msg = f"🔒 COOLDOWN LOCK: {target_sym} заблоковано ще на {cd_left} хв після закриття угоди."
                        logger.info(cd_msg)
                        self.scan_logs.appendleft({
                            'time': time.strftime("%H:%M:%S"),
                            'symbol': target_sym,
                            'price': best_buy_opportunity['meta'].get('price', 0.0),
                            'signal': 'REJECTED',
                            'reason': cd_msg,
                            'rsi': best_buy_opportunity['meta'].get('rsi', 0.0),
                            'trend': best_buy_opportunity['meta'].get('trend', 'UNKNOWN')
                        })
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
                    self._save_ai_verdicts()

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
                        pos_limit_msg = f"⚠️ RiskManager: Досягнуто ліміту активних позицій ({len(self.active_positions)}/{self.max_concurrent_positions})"
                        logger.warning(f"🛑 BUY candidate {target_sym} dropped: {pos_limit_msg}")
                        verdict_record['status'] = 'RISK_REJECTED'
                        verdict_record['reason'] += f" | {pos_limit_msg}"
                        self._save_ai_verdicts()
                        self.scan_logs.appendleft({
                            'time': time.strftime("%H:%M:%S"),
                            'symbol': target_sym,
                            'price': target_meta.get('price', 0.0),
                            'signal': 'REJECTED',
                            'reason': pos_limit_msg,
                            'rsi': target_meta.get('rsi', 0.0),
                            'trend': target_meta.get('trend', 'UNKNOWN')
                        })
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
                            self._save_ai_verdicts()
                            logger.error(f"Exchange Order Execution Error for {target_sym}: {order_err}")
                            await self.telegram.send_alert(f"⚠️ *Exchange Order Rejected*: {order_err}")
                    else:
                        risk_rej_msg = f"⚠️ RiskManager: {risk_reason}"
                        verdict_record['status'] = 'RISK_REJECTED'
                        verdict_record['reason'] += f" | {risk_rej_msg}"
                        self._save_ai_verdicts()
                        logger.warning(f"🛑 BUY candidate {target_sym} dropped by RiskManager: {risk_rej_msg}")
                        self.scan_logs.appendleft({
                            'time': time.strftime("%H:%M:%S"),
                            'symbol': target_sym,
                            'price': target_meta.get('price', 0.0),
                            'signal': 'REJECTED',
                            'reason': risk_rej_msg,
                            'rsi': target_meta.get('rsi', 0.0),
                            'trend': target_meta.get('trend', 'UNKNOWN')
                        })
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
