"""Abstract Base Class for Exchange Adapters (Bybit, Binance, Paper Trading)."""

from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

class BaseExchangeAdapter(ABC):
    """Unified interface for crypto CEX exchange adapters."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100) -> pd.DataFrame:
        """Fetches historical OHLCV candlestick data into a pandas DataFrame."""
        pass

    @abstractmethod
    def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Fetches account wallet balance."""
        pass

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetches the latest ticker (bid/ask/last price) for a symbol."""
        pass

    @abstractmethod
    def create_spot_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
        """Creates a spot buy/sell order."""
        pass

    @abstractmethod
    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Executes smart order with limit offset and Iceberg slicing."""
        pass

    @abstractmethod
    def fetch_dynamic_hot_pairs(self, min_volume: float = 1000000.0, limit: int = 25) -> List[str]:
        """Fetches high-volume volatile USDT spot trading pairs from exchange ticker data."""
        pass
