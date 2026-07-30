"""Binance Spot Exchange Adapter via CCXT."""

import os
import json
import time
import logging
import asyncio
import ccxt
import pandas as pd
from typing import Dict, Any, List, Optional
from exchanges.base_adapter import BaseExchangeAdapter
from config import config

logger = logging.getLogger(__name__)

class BinanceExchangeAdapter(BaseExchangeAdapter):
    """Adapter for Binance Spot Exchange."""

    def __init__(self, api_key: str = "", api_secret: str = ""):
        key = api_key or getattr(config, 'binance_api_key', '').strip()
        secret = api_secret or getattr(config, 'binance_api_secret', '').strip()

        if "your_binance_api_key" in key.lower():
            key = ""

        exchange_params = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        }
        if key:
            exchange_params['apiKey'] = key
        if secret:
            exchange_params['secret'] = secret

        if getattr(config, 'binance_testnet', False) or config.testnet:
            exchange_params['urls'] = {'api': ccxt.binance().urls['test']}

        self.exchange = ccxt.binance(exchange_params)
        try:
            self.exchange.load_markets()
            logger.info("🟡 Binance Spot Exchange Adapter initialized successfully!")
        except Exception as e:
            logger.warning(f"Failed to pre-load Binance markets: {e}")

    def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100) -> pd.DataFrame:
        target = symbol if symbol and symbol != "AUTO" else "SOL/USDT"
        ohlcv = self.exchange.fetch_ohlcv(target, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.exchange.fetch_balance()

    def create_spot_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
        try:
            if not self.exchange.markets:
                self.exchange.load_markets()
            market = self.exchange.market(symbol)
            min_cost = float(market.get('limits', {}).get('cost', {}).get('min') or 5.0)
            order_cost = amount * price

            if order_cost < min_cost:
                required_amount = (min_cost * 1.05) / price
                logger.warning(f"⚠️ Binance Order cost (${order_cost:.2f}) < min_cost (${min_cost:.2f}). Adjusting amount to {required_amount:.4f}")
                amount = required_amount

            # Apply exchange price and amount precision formatting
            formatted_amount = float(self.exchange.amount_to_precision(symbol, amount))
            formatted_price = float(self.exchange.price_to_precision(symbol, price)) if price > 0 else None

            if order_type == 'market' or formatted_price is None:
                return self.exchange.create_order(symbol, 'market', side, formatted_amount)

            try:
                return self.exchange.create_order(symbol, order_type, side, formatted_amount, formatted_price)
            except Exception as limit_err:
                err_msg = str(limit_err)
                if "PERCENT_PRICE" in err_msg or "PRICE_FILTER" in err_msg or "higher than" in err_msg.lower():
                    logger.warning(f"⚠️ Binance Price Collar limit hit ({err_msg}). Falling back to MARKET order for {symbol}...")
                    return self.exchange.create_order(symbol, 'market', side, formatted_amount)
                raise limit_err
        except Exception as e:
            logger.error(f"Binance Order creation error for {symbol}: {e}")
            raise e

    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        target_sym = symbol if symbol and symbol != "AUTO" else getattr(config, 'symbol', 'SOL/USDT')
        if target_sym == "AUTO":
            target_sym = "SOL/USDT"

        limit_price = current_price if side == 'buy' else current_price * 0.9995

        total_value = amount * current_price
        if total_value < 10.0 or config.iceberg_slices <= 1:
            order = self.create_spot_order(target_sym, 'limit', side, amount, limit_price)
            return [order]

        slice_amount = amount / config.iceberg_slices
        executed_orders = []

        for i in range(config.iceberg_slices):
            logger.info(f"🧊 Executing Binance Iceberg Slice {i+1}/{config.iceberg_slices}: {slice_amount:.4f} @ ${limit_price:.4f}")
            order = self.create_spot_order(target_sym, 'limit', side, slice_amount, limit_price)
            executed_orders.append(order)
            time.sleep(0.5)

        return executed_orders

    def fetch_dynamic_hot_pairs(self, min_volume: float = 1000000.0, limit: int = 25) -> List[str]:
        try:
            tickers = self.exchange.fetch_tickers()
            usdt_tickers = []
            for sym, t in tickers.items():
                if sym.endswith('/USDT') and 'BEAR/' not in sym and 'BULL/' not in sym and 'UP/' not in sym and 'DOWN/' not in sym:
                    quote_vol = float(t.get('quoteVolume') or (t.get('baseVolume', 0) * t.get('last', 0)))
                    percentage = abs(float(t.get('percentage') or 0.0))
                    if quote_vol >= min_volume:
                        usdt_tickers.append((sym, quote_vol, percentage))

            usdt_tickers.sort(key=lambda x: (x[2], x[1]), reverse=True)
            top_pairs = [x[0] for x in usdt_tickers[:limit]]
            return top_pairs if top_pairs else ["SOL/USDT", "BTC/USDT", "ETH/USDT", "WLD/USDT", "PEPE/USDT"]
        except Exception as e:
            logger.error(f"Error fetching Binance hot pairs: {e}")
            return ["SOL/USDT", "BTC/USDT", "ETH/USDT", "WLD/USDT", "PEPE/USDT"]
