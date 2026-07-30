"""Hybrid Technical Strategy module consuming core.math_engine."""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
from config import config
from core.math_engine import calculate_ema, calculate_rsi, calculate_atr, analyze_trend

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
        """Calculates RSI and EMA indicators via math_engine."""
        df = df.copy()
        
        # Calculate EMA 20 & EMA 50
        df['ema_fast'] = calculate_ema(df['close'], self.ema_fast)
        df['ema_slow'] = calculate_ema(df['close'], self.ema_slow)
        
        # Calculate RSI 14
        df['rsi'] = calculate_rsi(df['close'], self.rsi_period)
        
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
        current_price = float(latest['close'])
        rsi_val = float(latest['rsi'])
        ema_fast_val = float(latest['ema_fast'])
        ema_slow_val = float(latest['ema_slow'])

        metadata = {
            'price': current_price,
            'rsi': rsi_val,
            'ema_fast': ema_fast_val,
            'ema_slow': ema_slow_val,
            'trend': analyze_trend(ema_fast_val, ema_slow_val)
        }

        # 1. Check active position for Trailing Stop / Take-Profit / Stop-Loss
        if current_position and current_position.get('amount', 0) > 0:
            entry_price = current_position['entry_price']
            price_change = (current_price - entry_price) / entry_price

            # --- ⚡ MODULE 1: QUICK SCALPING TRAILING STOP (+1.0% ACTIVATION) ---
            highest_price = max(current_position.get('highest_price', entry_price), current_price)
            current_position['highest_price'] = highest_price
            peak_gain_pct = (highest_price - entry_price) / entry_price

            # Activate Scalping Trailing Stop when peak gain reaches +1.0% (trailing 0.5% below peak)
            if peak_gain_pct >= 0.010:
                trailing_stop_price = highest_price * 0.995  # Trailing 0.5% below peak
                if current_price <= trailing_stop_price:
                    return 'SELL', f'⚡ QUICK SCALPING PROFIT TAKEN (Peak: +{peak_gain_pct*100:.2f}%, Closed: {price_change*100:+.2f}%)', metadata

            # Hard Take-Profit backup (+10% max blowoff top)
            if price_change >= 0.10:
                return 'SELL', f'🚀 MAX BLOWOFF TAKE PROFIT (+{price_change*100:.2f}%)', metadata

            # Hard Stop-Loss protection (-2.0%)
            if price_change <= -config.stop_loss_pct:
                return 'SELL', f'🛑 STOP LOSS TRIGGERED ({price_change*100:.2f}%)', metadata

            if rsi_val >= self.rsi_overbought:
                return 'SELL', f'⚠️ RSI OVERBOUGHT ({rsi_val:.1f} >= {self.rsi_overbought})', metadata

            return 'HOLD', f'Position active (PnL: {price_change*100:+.2f}%, Peak: +{peak_gain_pct*100:.2f}%)', metadata

        # 2. Check for Buy Entry Opportunities (No active position)
        is_bullish_trend = (metadata['trend'] == 'BULLISH')
        is_oversold = rsi_val <= self.rsi_oversold

        # --- MODULE 2: BREAKOUT MOMENTUM PUMP SNIPER (24h High + Volume Surge) ---
        high_24h = float(df_calc['high'].tail(96).max()) if len(df_calc) >= 96 else float(df_calc['high'].max())
        avg_vol_20 = float(df_calc['volume'].tail(20).mean())
        curr_vol = float(latest['volume'])
        vol_ratio = curr_vol / (avg_vol_20 + 1e-10)

        if current_price >= (0.975 * high_24h) and vol_ratio >= 2.0 and rsi_val >= 55.0:
            return 'BUY', f'⚡ BREAKOUT MOMENTUM PUMP SNIPER (Пробой 24h макс ${high_24h:.4f}, Об\'єм x{vol_ratio:.1f})', metadata

        # --- ЛОВЕЦЬ ВІДСКОКІВ (ULTRA-DIP REVERSAL: RSI < 25) ---
        prev_candle = df_calc.iloc[-2]
        prev_rsi = float(prev_candle['rsi'])

        # Extreme oversold condition (current RSI < 25 OR previous candle RSI < 25)
        if rsi_val <= 25.0 or prev_rsi <= 25.0:
            current_candle = latest
            is_green_candle = float(current_candle['close']) > float(current_candle['open'])
            is_price_rebounding = float(current_candle['close']) > float(prev_candle['close'])

            if is_green_candle or is_price_rebounding:
                return 'BUY', f'⚡ ЛОВЕЦЬ ВІДСКОКІВ [Ultra-Dip Reversal] (RSI: {rsi_val:.1f} < 25, Перша зелена свічка розвороту)', metadata

        # Standard Bullish Trend Oversold Entry
        if is_oversold and is_bullish_trend:
            return 'BUY', f'🟢 RSI OVERSOLD ({rsi_val:.1f} <= {self.rsi_oversold}) in Bullish Trend', metadata

        # Micro-Dip Buy condition: RSI < 45 and price slightly below fast EMA
        if rsi_val <= 45 and current_price < ema_fast_val and is_bullish_trend:
            return 'BUY', f'🟢 Micro-Dip Buy (RSI: {rsi_val:.1f}, Price below EMA20)', metadata

        return 'HOLD', f'Scanning market (RSI: {rsi_val:.1f}, Trend: {metadata["trend"]})', metadata
