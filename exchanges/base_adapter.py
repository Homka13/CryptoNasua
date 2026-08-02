"""Abstract Base Class for Exchange Adapters (Bybit, Binance, Paper Trading)."""

import time
import logging
from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

class BaseExchangeAdapter(ABC):
    """Unified interface for crypto CEX exchange adapters."""

    # Optional capability: exchanges that can convert sub-minimum balances override these.
    supports_convert: bool = False

    def _verify_filled(self, order: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Confirms an order was not rejected by the matching engine.

        An exchange can ack an order over REST and only then reject it asynchronously,
        so a successful create_order() call is not proof of execution. Without this check
        a rejected order looks identical to a filled one and the bot drops the position
        from tracking while the coins are still sitting in the wallet.
        """
        client = getattr(self, 'exchange', None)
        order_id = order.get('id') or (order.get('info', {}) or {}).get('orderId')
        if client is None or not order_id:
            return order

        for attempt in range(3):
            time.sleep(0.4)
            try:
                fetched = client.fetch_order(order_id, symbol)
            except Exception as e:
                logger.debug(f"Could not verify order {order_id} (attempt {attempt + 1}): {e}")
                continue

            raw = fetched.get('info', {}) or {}
            status = (raw.get('orderStatus') or raw.get('status') or fetched.get('status') or '').lower()
            filled = float(
                fetched.get('filled')
                or raw.get('cumExecQty')
                or raw.get('executedQty')
                or 0
            )

            if status in ('rejected', 'expired', 'cancelled', 'canceled') and filled <= 0:
                raise Exception(f"Order {order_id} rejected by exchange: {raw.get('rejectReason') or status}")
            return fetched

        return order

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
