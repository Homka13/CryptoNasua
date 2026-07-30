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
        """Calculates RSI, EMA, and Bollinger Bands via math_engine."""
        df = df.copy()

        # Calculate EMA 20 & EMA 50
        df['ema_fast'] = calculate_ema(df['close'], self.ema_fast)
        df['ema_slow'] = calculate_ema(df['close'], self.ema_slow)

        # Calculate RSI 14
        df['rsi'] = calculate_rsi(df['close'], self.rsi_period)

        # Calculate Bollinger Bands (20, 2.0)
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['std20'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['sma20'] + (df['std20'] * 2.0)
        df['bb_lower'] = df['sma20'] - (df['std20'] * 2.0)

        return df

    def analyze(self, df: pd.DataFrame, current_position: Any = None, symbol: str = "") -> Tuple[str, str, Dict[str, Any]]:
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
        bb_lower_val = float(latest['bb_lower'])
        bb_upper_val = float(latest['bb_upper'])

        is_stable = bool(symbol and symbol in config.stable_pairs)
        rsi_oversold_target = config.stable_mode_rsi_threshold if is_stable else self.rsi_oversold
        sl_pct_target = config.stable_mode_sl_pct if is_stable else config.stop_loss_pct

        last_5 = df_calc.tail(5)
        last_5_closes = [round(float(c), 4) for c in last_5['close']]
        price_5_ago = float(last_5['close'].iloc[0])
        slope_5_candles_pct = ((current_price - price_5_ago) / price_5_ago) * 100.0 if price_5_ago > 0 else 0.0
        last_3_red = all(float(r['close']) < float(r['open']) for _, r in last_5.tail(3).iterrows())

        metadata = {
            'price': current_price,
            'rsi': rsi_val,
            'ema_fast': ema_fast_val,
            'ema_slow': ema_slow_val,
            'bb_lower': bb_lower_val,
            'bb_upper': bb_upper_val,
            'trend': analyze_trend(ema_fast_val, ema_slow_val),
            'is_stable': is_stable,
            'last_5_closes': last_5_closes,
            'slope_5_candles_pct': round(slope_5_candles_pct, 2),
            'last_3_red': last_3_red
        }

        # 1. Check active position for Trailing Stop / Take-Profit / Stop-Loss
        if current_position and current_position.get('amount', 0) > 0:
            entry_price = current_position['entry_price']
            price_change = (current_price - entry_price) / entry_price

            # --- ⚡ MODULE 1: QUICK SCALPING TRAILING STOP ---
            highest_price = max(current_position.get('highest_price', entry_price), current_price)
            current_position['highest_price'] = highest_price
            peak_gain_pct = (highest_price - entry_price) / entry_price

            tp_threshold = config.stable_mode_tp_pct if is_stable else 0.0050
            if peak_gain_pct >= tp_threshold:
                # Lock in net profit immediately if price pulls back micro-step (0.1%) or hits TP
                trailing_stop_price = highest_price * (0.999 if is_stable else 0.996)
                if current_price <= trailing_stop_price or price_change >= (tp_threshold * 1.2):
                    return 'SELL', f'⚡ MICRO-SCALP TAKE-PROFIT (Peak: +{peak_gain_pct*100:.2f}%, Net PnL: {price_change*100:+.2f}%)', metadata

            # Hard Take-Profit backup (+10% max blowoff top)
            if price_change >= 0.10:
                return 'SELL', f'🚀 MAX BLOWOFF TAKE PROFIT (+{price_change*100:.2f}%)', metadata

            # Hard Stop-Loss protection
            if price_change <= -sl_pct_target:
                return 'SELL', f'🛑 STOP LOSS TRIGGERED ({price_change*100:.2f}%)', metadata

            if rsi_val >= self.rsi_overbought:
                return 'SELL', f'⚠️ RSI OVERBOUGHT ({rsi_val:.1f} >= {self.rsi_overbought})', metadata

            return 'HOLD', f'Position active (PnL: {price_change*100:+.2f}%, Peak: +{peak_gain_pct*100:.2f}%)', metadata

        # 2. Check for Buy Entry Opportunities (No active position)
        is_bullish_trend = (metadata['trend'] == 'BULLISH')
        is_oversold = rsi_val <= rsi_oversold_target

        # --- MODULE 2: BREAKOUT MOMENTUM PUMP SNIPER (24h High + Volume Surge - 0ms Fast Math Execution) ---
        high_24h = float(df_calc['high'].tail(96).max()) if len(df_calc) >= 96 else float(df_calc['high'].max())
        avg_vol_20 = float(df_calc['volume'].tail(20).mean())
        curr_vol = float(latest['volume'])
        vol_ratio = curr_vol / (avg_vol_20 + 1e-10)

        if current_price >= (0.98 * high_24h) and vol_ratio >= 2.0 and (55.0 <= rsi_val < 68.0):
            metadata['skip_llm'] = False  # Mandatory DeepSeek LLM Audit
            return 'BUY', f'⚡ BREAKOUT MOMENTUM PUMP SNIPER (Пробой 24h макс ${high_24h:.4f}, Об\'єм x{vol_ratio:.1f})', metadata

        # --- ЛОВЕЦЬ ВІДСКОКІВ (ULTRA-DIP REVERSAL: RSI < 25 OR Lower BB Piercing) ---
        prev_candle = df_calc.iloc[-2]
        prev_rsi = float(prev_candle['rsi'])

        # Extreme oversold condition (RSI <= 25 OR lower Bollinger Band piercing)
        bb_lower_pierce = current_price <= bb_lower_val or float(prev_candle['close']) <= float(prev_candle['bb_lower'])
        if (rsi_val <= 25.0 or prev_rsi <= 25.0) and bb_lower_pierce:
            current_candle = latest
            is_green_candle = float(current_candle['close']) > float(current_candle['open'])
            is_price_rebounding = float(current_candle['close']) > float(prev_candle['close'])

            if is_green_candle or is_price_rebounding:
                metadata['skip_llm'] = False  # Mandatory DeepSeek LLM Audit
                return 'BUY', f'⚡ ЛОВЕЦЬ ВІДСКОКІВ [Ultra-Dip Reversal] (RSI: {rsi_val:.1f} < 25, Пробій нижньої Боллінджера + Зелена свічка розвороту)', metadata

        # Do not allow standard dip buys if the micro-trend is slumping (3 consecutive red candles or strong downward slope)
        if last_3_red and slope_5_candles_pct < -0.20:
            regime_tag = " [STABLE]" if is_stable else ""
            return 'HOLD', f'Scanning market (RSI: {rsi_val:.1f}, Micro-Downtrend 3 Red Candles Slope: {slope_5_candles_pct:+.2f}%){regime_tag}', metadata

        # Standard Bullish Trend Oversold Entry
        if is_oversold and is_bullish_trend:
            coin_type_tag = " [Blue Chip STABLE]" if is_stable else ""
            return 'BUY', f'🟢 RSI OVERSOLD ({rsi_val:.1f} <= {rsi_oversold_target}){coin_type_tag} in Bullish Trend', metadata

        # Micro-Dip Buy condition: RSI < 45 and price slightly below fast EMA
        micro_dip_rsi = 45.0 if not is_stable else 48.0
        if rsi_val <= micro_dip_rsi and current_price < ema_fast_val and is_bullish_trend:
            coin_type_tag = " [Blue Chip STABLE]" if is_stable else ""
            return 'BUY', f'🟢 Micro-Dip Buy ({rsi_val:.1f} <= {micro_dip_rsi}, Price below EMA20){coin_type_tag}', metadata

        regime_tag = " [STABLE]" if is_stable else ""
        return 'HOLD', f'Scanning market (RSI: {rsi_val:.1f}, Trend: {metadata["trend"]}){regime_tag}', metadata
