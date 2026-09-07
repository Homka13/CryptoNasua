import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes, start_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
    """Build a synthetic OHLCV frame.

    ``closes`` may be an int (number of candles) or an explicit list of close prices.
    """
    if isinstance(closes, int):
        n = closes
        closes = [start_price * (1.0 + trend * i) for i in range(n)]

    closes = list(closes)
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.roll(closes_arr, 1)
    opens[0] = closes_arr[0]
    highs = np.maximum(opens, closes_arr) * 1.001
    lows = np.minimum(opens, closes_arr) * 0.999
    volumes = np.full(n, 1000.0)

    df = pd.DataFrame(
        {
            "timestamp": (idx.astype("int64") // 10**6).astype("int64"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes_arr,
            "volume": volumes,
        },
        index=idx,
    )
    df["datetime"] = idx
    return df


@pytest.fixture
def bullish_df() -> pd.DataFrame:
    """A steady uptrend (bullish EMA alignment)."""
    closes = [100.0 + i * 0.3 for i in range(60)]
    return make_ohlcv(closes)


@pytest.fixture
def bearish_df() -> pd.DataFrame:
    """A steady downtrend (bearish EMA alignment)."""
    closes = [100.0 - i * 0.3 for i in range(60)]
    return make_ohlcv(closes)