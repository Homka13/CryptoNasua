"""Exchange Adapter Factory module."""

import logging
import ccxt
from typing import Dict, Any, Optional
from exchanges.base_adapter import BaseExchangeAdapter
from exchanges.bybit_adapter import BybitExchangeAdapter
from exchanges.binance_adapter import BinanceExchangeAdapter
from exchanges.paper_adapter import PaperExchangeAdapter
from config import config

logger = logging.getLogger(__name__)

class ExchangeFactory:
    """Factory for instantiating and managing exchange adapters."""

    @staticmethod
    def create_adapter(exchange_name: Optional[str] = None, is_paper: Optional[bool] = None) -> BaseExchangeAdapter:
        name = (exchange_name or getattr(config, 'active_exchange', getattr(config, 'exchange_name', 'bybit'))).lower()
        paper = is_paper if is_paper is not None else getattr(config, 'paper_trading', True)

        public_exchange_client = ccxt.binance({'enableRateLimit': True}) if name == 'binance' else ccxt.bybit({'enableRateLimit': True})

        if paper:
            logger.info(f"🧪 Initialized Paper Trading Simulator (Public Market Feed: {name.upper()})")
            return PaperExchangeAdapter(public_exchange=public_exchange_client, initial_usdt=config.initial_capital)

        if name == 'binance':
            logger.info("🟡 Initialized LIVE Binance Spot Exchange Adapter")
            return BinanceExchangeAdapter()
        else:
            logger.info("🟢 Initialized LIVE Bybit Spot Exchange Adapter")
            return BybitExchangeAdapter()
