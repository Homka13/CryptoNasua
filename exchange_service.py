"""Backward-compatible Exchange Service wrapper forwarding to Exchanges Package."""

import logging
from exchanges.exchange_factory import ExchangeFactory
from exchanges.base_adapter import BaseExchangeAdapter
from config import config

logger = logging.getLogger(__name__)

def ExchangeService() -> BaseExchangeAdapter:
    """Instantiates the active exchange adapter (Bybit, Binance, or Paper)."""
    return ExchangeFactory.create_adapter(
        exchange_name=getattr(config, 'active_exchange', getattr(config, 'exchange_name', 'bybit')),
        is_paper=getattr(config, 'paper_trading', True)
    )
