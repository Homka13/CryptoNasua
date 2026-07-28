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
        """Calculates RSI and EMA indicators using pandas/numpy."""
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
            'trend': 'BULLISH' if ema_fast_val > ema_slow_val else 'BEARISH'
        }

        # 1. Check active position for Take-Profit / Stop-Loss
        if current_position and current_position.get('amount', 0) > 0:
            entry_price = current_position['entry_price']
            price_change = (current_price - entry_price) / entry_price

            if price_change >= config.take_profit_pct:
                return 'SELL', f'🎯 TAKE PROFIT TRIGGERED (+{price_change*100:.2f}%)', metadata

            if price_change <= -config.stop_loss_pct:
                return 'SELL', f'🛑 STOP LOSS TRIGGERED ({price_change*100:.2f}%)', metadata

            if rsi_val >= self.rsi_overbought:
                return 'SELL', f'⚠️ RSI OVERBOUGHT ({rsi_val:.1f} >= {self.rsi_overbought})', metadata

            return 'HOLD', f'Position active (PnL: {price_change*100:+.2f}%)', metadata

        # 2. Check for Buy Entry Opportunities (No active position)
        is_bullish_trend = ema_fast_val >= ema_slow_val
        is_oversold = rsi_val <= self.rsi_oversold

        if is_oversold and is_bullish_trend:
            return 'BUY', f'🟢 RSI OVERSOLD ({rsi_val:.1f} <= {self.rsi_oversold}) in Bullish Trend', metadata

        # Micro-Dip Buy condition: RSI < 45 and price slightly below fast EMA
        if rsi_val <= 45 and current_price < ema_fast_val and is_bullish_trend:
            return 'BUY', f'🟢 Micro-Dip Buy (RSI: {rsi_val:.1f}, Price below EMA20)', metadata

        return 'HOLD', f'Scanning market (RSI: {rsi_val:.1f}, Trend: {metadata["trend"]})', metadata
