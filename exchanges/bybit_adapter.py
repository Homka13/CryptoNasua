"""Bybit Spot Exchange Adapter via CCXT."""

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

class BybitExchangeAdapter(BaseExchangeAdapter):
    """Adapter for Bybit Spot Exchange."""

    def __init__(self, api_key: str = "", api_secret: str = ""):
        key = api_key or config.bybit_api_key.strip()
        secret = api_secret or config.bybit_api_secret.strip()

        if "your_bybit_api_key" in key.lower():
            key = ""

        # Check OAuth token file fallback if no key provided
        if not key:
            appdata = os.getenv("APPDATA", "")
            oauth_path = os.path.join(appdata, "bybit", "oauth_token.json") if appdata else os.path.expanduser("~/.bybit/oauth_token.json")
            if os.path.exists(oauth_path):
                try:
                    with open(oauth_path, 'r', encoding='utf-8') as f:
                        oauth_data = json.load(f)
                    ai_acc = oauth_data.get('ai-account', {})
                    if ai_acc.get('api_key') and ai_acc.get('api_secret'):
                        key = ai_acc['api_key']
                        secret = ai_acc['api_secret']
                        logger.info("🔑 Bybit OAuth AI Account credentials loaded from oauth_token.json!")
                except Exception as e:
                    logger.error(f"Error reading OAuth token file: {e}")

        if config.bybit_private_key_path and os.path.exists(config.bybit_private_key_path):
            try:
                with open(config.bybit_private_key_path, 'r', encoding='utf-8') as f:
                    secret = f.read()
            except Exception as e:
                logger.error(f"Error loading RSA Private Key file: {e}")
        elif os.path.exists("bybit_rsa_private.pem") and not secret:
            try:
                with open("bybit_rsa_private.pem", 'r', encoding='utf-8') as f:
                    secret = f.read()
            except Exception as e:
                logger.error(f"Error loading bybit_rsa_private.pem: {e}")

        exchange_params = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        }
        if key:
            exchange_params['apiKey'] = key
        if secret:
            exchange_params['secret'] = secret

        if config.testnet:
            exchange_params['urls'] = {'api': ccxt.bybit().urls['test']}

        self.exchange = ccxt.bybit(exchange_params)
        try:
            self.exchange.load_markets()
        except Exception as e:
            logger.warning(f"Failed to pre-load Bybit markets: {e}")

    def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100) -> pd.DataFrame:
        target = symbol if symbol and symbol != "AUTO" else "SHIB/USDT"
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
            min_cost = float(market.get('limits', {}).get('cost', {}).get('min') or 1.0)
            order_cost = amount * price

            if order_cost < min_cost:
                required_amount = (min_cost * 1.05) / price
                logger.warning(f"⚠️ Bybit Order cost (${order_cost:.2f}) < min_cost (${min_cost:.2f}). Adjusting amount to {required_amount:.4f}")
                amount = required_amount

            return self.exchange.create_order(symbol, order_type, side, amount, price)
        except Exception as e:
            logger.error(f"Bybit Order creation error for {symbol}: {e}")
            raise e

    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        target_sym = symbol if symbol and symbol != "AUTO" else getattr(config, 'symbol', 'SHIB/USDT')
        if target_sym == "AUTO":
            target_sym = "SHIB/USDT"

        offset_pct = 0.0005 if side == 'buy' else -0.0005
        limit_price = current_price * (1 + offset_pct)

        total_value = amount * current_price
        if total_value < 6.0 or config.iceberg_slices <= 1:
            order = self.create_spot_order(target_sym, 'limit', side, amount, limit_price)
            return [order]

        slice_amount = amount / config.iceberg_slices
        executed_orders = []

        for i in range(config.iceberg_slices):
            logger.info(f"🧊 Executing Bybit Iceberg Slice {i+1}/{config.iceberg_slices}: {slice_amount:.4f} @ ${limit_price:.4f}")
            order = self.create_spot_order(target_sym, 'limit', side, slice_amount, limit_price)
            executed_orders.append(order)
            time.sleep(0.5)

        return executed_orders

    def fetch_dynamic_hot_pairs(self, min_volume: float = 1000000.0, limit: int = 25) -> List[str]:
        try:
            tickers = self.exchange.fetch_tickers()
            usdt_tickers = []
            for sym, t in tickers.items():
                if sym.endswith('/USDT') and 'QUOTE' not in sym and 'UP/' not in sym and 'DOWN/' not in sym:
                    quote_vol = float(t.get('quoteVolume') or (t.get('baseVolume', 0) * t.get('last', 0)))
                    percentage = abs(float(t.get('percentage') or 0.0))
                    if quote_vol >= min_volume:
                        usdt_tickers.append((sym, quote_vol, percentage))

            usdt_tickers.sort(key=lambda x: (x[2], x[1]), reverse=True)
            top_pairs = [x[0] for x in usdt_tickers[:limit]]
            return top_pairs if top_pairs else ["SOL/USDT", "WLD/USDT", "PUMP/USDT", "PEPE/USDT", "SHIB/USDT"]
        except Exception as e:
            logger.error(f"Error fetching Bybit hot pairs: {e}")
            return ["SOL/USDT", "WLD/USDT", "PUMP/USDT", "PEPE/USDT", "SHIB/USDT"]
