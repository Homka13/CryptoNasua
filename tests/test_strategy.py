import numpy as np
import pandas as pd
import pytest

from strategy import HybridStrategy, SignalResult
from tests.conftest import make_ohlcv


@pytest.fixture
def strategy() -> HybridStrategy:
    return HybridStrategy()


def test_rsi_is_bounded(strategy, bullish_df):
    df = strategy.calculate_indicators(bullish_df)
    rsi = df["rsi"].dropna()
    assert len(rsi) > 0
    assert rsi.between(0.0, 100.0).all()


def test_rsi_oversold_on_consistent_decline(strategy):
    closes = [100.0 - i * 3.0 for i in range(60)]
    df = strategy.calculate_indicators(make_ohlcv(closes))
    latest_rsi = float(df["rsi"].iloc[-1])
    # A long pure decline drives RSI essentially to 0 (oversold).
    assert latest_rsi < strategy.rsi_oversold


def test_rsi_overbought_on_consistent_rise(strategy):
    closes = [100.0 + i * 3.0 for i in range(60)]
    df = strategy.calculate_indicators(make_ohlcv(closes))
    latest_rsi = float(df["rsi"].iloc[-1])
    assert latest_rsi > strategy.rsi_overbought


def test_ema_cross_alignment(strategy, bullish_df):
    df = strategy.calculate_indicators(bullish_df)
    assert float(df["ema_fast"].iloc[-1]) > float(df["ema_slow"].iloc[-1])


def test_ema_alignment_bearish(strategy, bearish_df):
    df = strategy.calculate_indicators(bearish_df)
    assert float(df["ema_fast"].iloc[-1]) < float(df["ema_slow"].iloc[-1])


def test_insufficient_data_returns_hold(strategy):
    df = make_ohlcv(10)
    result = strategy.analyze(df, None)
    assert isinstance(result, SignalResult)
    assert result.signal == "HOLD"


def test_sell_take_profit_signal(strategy):
    closes = [100.0 + i * 0.2 for i in range(60)]
    df = strategy.calculate_indicators(make_ohlcv(closes))
    position = {"entry_price": 100.0, "amount": 1.0}
    result = strategy.analyze(df, position)
    # Entry at 100, latest ~111.8 => > +3.5% take-profit.
    assert result.signal == "SELL"
    assert "TAKE PROFIT" in result.reason


def test_sell_stop_loss_signal(strategy):
    closes = [100.0 - i * 0.2 for i in range(60)]
    df = strategy.calculate_indicators(make_ohlcv(closes))
    position = {"entry_price": 100.0, "amount": 1.0}
    result = strategy.analyze(df, position)
    assert result.signal == "SELL"
    assert "STOP LOSS" in result.reason


def test_buy_oversold_bullish_trend(strategy):
    # A long strong uptrend, then a multi-candle pullback strong enough to drive
    # RSI(14) into oversold territory while EMA(20) still sits above EMA(50).
    closes = [100.0 + i * 1.0 for i in range(60)]  # +59 strong uptrend
    # 16-candle pullback (~ -16 total) to force RSI oversold on the latest candles.
    closes += [closes[-1] - i * 1.0 for i in range(1, 17)]
    df = strategy.calculate_indicators(make_ohlcv(closes))
    assert float(df["ema_fast"].iloc[-1]) > float(df["ema_slow"].iloc[-1]), "expected bullish EMA alignment"
    assert float(df["rsi"].iloc[-1]) <= strategy.rsi_oversold, (
        f"expected oversold RSI, got {float(df['rsi'].iloc[-1]):.2f}"
    )
    result = strategy.analyze(df, None)
    assert result.signal == "BUY"
    assert "OVERSOLD" in result.reason or "Micro-Dip" in result.reason


def test_hold_when_no_clear_signal(strategy, bullish_df):
    # In a clean uptrend with no position, RSI is overbought -> no BUY.
    result = strategy.analyze(bullish_df, None)
    assert result.signal == "HOLD"
    assert result.metadata["trend"] == "BULLISH"