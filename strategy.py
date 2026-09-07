import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal

import pandas as pd
import numpy as np

from config import config

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Typed result of a strategy analysis over the latest candle."""

    signal: Literal["BUY", "SELL", "HOLD"]
    reason: str
    metadata: Dict[str, Any]


class HybridStrategy:
    """Combines RSI and EMA indicators for safe micro-capital trading."""

    def __init__(self) -> None:
        self.rsi_period: int = config.rsi_period
        self.rsi_oversold: float = config.rsi_oversold
        self.rsi_overbought: float = config.rsi_overbought
        self.ema_fast: int = config.ema_fast
        self.ema_slow: int = config.ema_slow

    def calculate_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        """Vectorized Wilder-style RSI using simple moving averages."""
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    def calculate_ema(self, close: pd.Series, span: int) -> pd.Series:
        """Vectorized exponential moving average."""
        return close.ewm(span=span, adjust=False).mean()

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute EMA fast/slow and RSI into a copy of the input frame."""
        df = df.copy()
        df['ema_fast'] = self.calculate_ema(df['close'], self.ema_fast)
        df['ema_slow'] = self.calculate_ema(df['close'], self.ema_slow)
        df['rsi'] = self.calculate_rsi(df['close'], self.rsi_period)
        return df

    def analyze(
        self, df: pd.DataFrame, current_position: Optional[Dict[str, Any]] = None
    ) -> SignalResult:
        """Analyze the latest candle and return a typed :class:`SignalResult`."""
        if len(df) < self.ema_slow + 5:
            return SignalResult(signal="HOLD", reason="Insufficient historical data", metadata={})

        df_calc = self.calculate_indicators(df)
        latest = df_calc.iloc[-1]
        current_price = float(latest['close'])
        rsi_val = float(latest['rsi'])
        ema_fast_val = float(latest['ema_fast'])
        ema_slow_val = float(latest['ema_slow'])

        metadata: Dict[str, Any] = {
            'price': current_price,
            'rsi': rsi_val,
            'ema_fast': ema_fast_val,
            'ema_slow': ema_slow_val,
            'trend': 'BULLISH' if ema_fast_val > ema_slow_val else 'BEARISH',
        }

        if current_position and current_position.get('amount', 0) > 0:
            entry_price = float(current_position['entry_price'])
            price_change = (current_price - entry_price) / entry_price

            if price_change >= config.take_profit_pct:
                return SignalResult(
                    signal="SELL",
                    reason=f'🎯 TAKE PROFIT TRIGGERED (+{price_change*100:.2f}%)',
                    metadata=metadata,
                )

            if price_change <= -config.stop_loss_pct:
                return SignalResult(
                    signal="SELL",
                    reason=f'🛑 STOP LOSS TRIGGERED ({price_change*100:.2f}%)',
                    metadata=metadata,
                )

            if rsi_val >= self.rsi_overbought:
                return SignalResult(
                    signal="SELL",
                    reason=f'⚠️ RSI OVERBOUGHT ({rsi_val:.1f} >= {self.rsi_overbought})',
                    metadata=metadata,
                )

            return SignalResult(
                signal="HOLD",
                reason=f'Position active (PnL: {price_change*100:+.2f}%)',
                metadata=metadata,
            )

        is_bullish_trend = ema_fast_val >= ema_slow_val
        is_oversold = rsi_val <= self.rsi_oversold

        if is_oversold and is_bullish_trend:
            return SignalResult(
                signal="BUY",
                reason=f'🟢 RSI OVERSOLD ({rsi_val:.1f} <= {self.rsi_oversold}) in Bullish Trend',
                metadata=metadata,
            )

        if rsi_val <= 45 and current_price < ema_fast_val and is_bullish_trend:
            return SignalResult(
                signal="BUY",
                reason=f'🟢 Micro-Dip Buy (RSI: {rsi_val:.1f}, Price below EMA20)',
                metadata=metadata,
            )

        return SignalResult(
            signal="HOLD",
            reason=f'Scanning market (RSI: {rsi_val:.1f}, Trend: {metadata["trend"]})',
            metadata=metadata,
        )