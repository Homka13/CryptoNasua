import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
from config import config

logger = logging.getLogger(__name__)

class HybridStrategy:
    """Combines Micro-Grid levels with RSI and EMA indicators for safe micro-capital trading."""
    
    def __init__(self):
        self.rsi_period = config.rsi_period
        self.rsi_oversold = config.rsi_oversold
        self.rsi_overbought = config.rsi_overbought
        self.ema_fast = config.ema_fast
        self.ema_slow = config.ema_slow

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates RSI, EMA, and Bollinger Bands using pandas/numpy."""
        df = df.copy()
        
        # Calculate EMA 20 & EMA 50
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        
        # Calculate RSI 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))

        # Calculate Bollinger Bands (20, 2.0)
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['std20'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['sma20'] + (df['std20'] * 2.0)
        df['bb_lower'] = df['sma20'] - (df['std20'] * 2.0)
        
        return df

    def analyze(self, df: pd.DataFrame, current_position: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
        """
        Analyzes the latest candle and current position state.
        Returns: (Signal ['BUY', 'SELL', 'HOLD'], Reason, Metadata)
        """
        if len(df) < self.ema_slow + 5:
            return 'HOLD', 'Insufficient historical data', {}

        df_calc = self.calculate_indicators(df)
        latest = df_calc.iloc[-1]
        prev = df_calc.iloc[-2]
        current_price = float(latest['close'])
        rsi_val = float(latest['rsi'])
        ema_fast_val = float(latest['ema_fast'])
        ema_slow_val = float(latest['ema_slow'])
        bb_lower_val = float(latest['bb_lower'])
        bb_upper_val = float(latest['bb_upper'])

        metadata = {
            'price': current_price,
            'rsi': rsi_val,
            'ema_fast': ema_fast_val,
            'ema_slow': ema_slow_val,
            'bb_lower': bb_lower_val,
            'bb_upper': bb_upper_val,
            'trend': 'BULLISH' if ema_fast_val > ema_slow_val else 'BEARISH'
        }

        # 1. Check active position for Trailing Stop / Take-Profit / Stop-Loss
        if current_position and current_position.get('amount', 0) > 0:
            entry_price = current_position['entry_price']
            price_change = (current_price - entry_price) / entry_price

            # --- 📈 MODULE 1: DYNAMIC TRAILING STOP-LOSS & PROFIT RUNNER ---
            highest_price = max(current_position.get('highest_price', entry_price), current_price)
            current_position['highest_price'] = highest_price
            peak_gain_pct = (highest_price - entry_price) / entry_price

            # --- ⚡ QUICK SCALPER TAKE-PROFIT (+1.0% to +1.85% STALL EXIT) ---
            # If price reaches +1.0%+ and stalls or pulls back slightly (1-2 updates), lock in the profit!
            if peak_gain_pct >= 0.010 and current_price <= (highest_price * 0.996):
                return 'SELL', f'⚡ QUICK SCALPER TAKE-PROFIT (Peak: +{peak_gain_pct*100:.2f}%, Closed: {price_change*100:+.2f}%)', metadata

            # Trailing Stop for bigger pumps (+2.5%+)
            if peak_gain_pct >= 0.025:
                trailing_stop_price = highest_price * 0.985  # Trailing 1.5% below peak
                if current_price <= trailing_stop_price:
                    return 'SELL', f'🎯 DYNAMIC TRAILING STOP EXITED (Peak: +{peak_gain_pct*100:.2f}%, Closed: {price_change*100:+.2f}%)', metadata

            # Hard Take-Profit backup (+15% max blowoff top)
            if price_change >= 0.15:
                return 'SELL', f'🚀 MAX BLOWOFF TAKE PROFIT (+{price_change*100:.2f}%)', metadata

            # Hard Stop-Loss protection (-2.5%)
            if price_change <= -config.stop_loss_pct:
                return 'SELL', f'🛑 STOP LOSS TRIGGERED ({price_change*100:.2f}%)', metadata

            if rsi_val >= self.rsi_overbought:
                return 'SELL', f'⚠️ RSI OVERBOUGHT ({rsi_val:.1f} >= {self.rsi_overbought})', metadata

            return 'HOLD', f'Position active (PnL: {price_change*100:+.2f}%, Peak: +{peak_gain_pct*100:.2f}%)', metadata

        # 2. Check for Buy Entry Opportunities (No active position)
        is_bullish_trend = ema_fast_val >= ema_slow_val
        is_oversold = rsi_val <= self.rsi_oversold

        # --- ⚡ MODULE 2: BREAKOUT MOMENTUM PUMP SNIPER (24h High + Volume Surge - 0ms Fast Math Execution) ---
        high_24h = float(df_calc['high'].tail(96).max()) if len(df_calc) >= 96 else float(df_calc['high'].max())
        avg_vol_20 = float(df_calc['volume'].tail(20).mean())
        curr_vol = float(latest['volume'])
        vol_ratio = curr_vol / (avg_vol_20 + 1e-10)

        if current_price >= (0.975 * high_24h) and vol_ratio >= 2.0 and rsi_val >= 55.0:
            metadata['skip_llm'] = True  # Bypass 2s LLM delay for 0ms breakout execution!
            return 'BUY', f'⚡ BREAKOUT MOMENTUM PUMP SNIPER (Пробой 24h макс ${high_24h:.4f}, Об\'єм x{vol_ratio:.1f}) [0ms Math]', metadata

        # --- 🔥 ЛОВЕЦЬ ВІДСКОКІВ (ULTRA-DIP REVERSAL: RSI < 25 + Lower Bollinger Band Piercing) ---
        prev_candle = df_calc.iloc[-2]
        prev_rsi = float(prev_candle['rsi'])

        # Extreme oversold condition (RSI <= 25 OR lower Bollinger Band piercing)
        bb_lower_pierce = current_price <= bb_lower_val or float(prev_candle['close']) <= float(prev_candle['bb_lower'])
        if (rsi_val <= 25.0 or prev_rsi <= 25.0) and bb_lower_pierce:
            current_candle = latest
            is_green_candle = float(current_candle['close']) > float(current_candle['open'])
            is_price_rebounding = float(current_candle['close']) > float(prev_candle['close'])

            if is_green_candle or is_price_rebounding:
                return 'BUY', f'⚡ ЛОВЕЦЬ ВІДСКОКІВ [Ultra-Dip Reversal] (RSI: {rsi_val:.1f} < 25, Пробій нижньої Боллінджера + Зелена свічка розвороту)', metadata

        # Standard Bullish Trend Oversold Entry
        if is_oversold and is_bullish_trend:
            return 'BUY', f'🟢 RSI OVERSOLD ({rsi_val:.1f} <= {self.rsi_oversold}) in Bullish Trend', metadata

        # Micro-Dip Buy condition: RSI < 45 and price slightly below fast EMA
        if rsi_val <= 45 and current_price < ema_fast_val and is_bullish_trend:
            return 'BUY', f'🟢 Micro-Dip Buy (RSI: {rsi_val:.1f}, Price below EMA20)', metadata

        return 'HOLD', f'Scanning market (RSI: {rsi_val:.1f}, Trend: {metadata["trend"]})', metadata
