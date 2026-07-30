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

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def _verify_filled(self, order: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Confirms an order was not rejected by the matching engine.

        Bybit acks an order over REST (retCode 0) and only then rejects it asynchronously,
        so a successful create_order() call is not proof of execution. Without this check a
        rejected order looks identical to a filled one and the bot drops the position from
        tracking while the coins are still sitting in the wallet.
        """
        order_id = order.get('id') or order.get('info', {}).get('orderId')
        if not order_id:
            return order

        for attempt in range(3):
            time.sleep(0.4)
            try:
                fetched = self.exchange.fetch_order(order_id, symbol)
            except Exception as e:
                logger.debug(f"Could not verify order {order_id} (attempt {attempt + 1}): {e}")
                continue

            raw = fetched.get('info', {}) or {}
            status = (raw.get('orderStatus') or fetched.get('status') or '').lower()
            filled = float(fetched.get('filled') or raw.get('cumExecQty') or 0)

            if status in ('rejected', 'cancelled', 'canceled') and filled <= 0:
                reject_reason = raw.get('rejectReason') or status
                raise Exception(f"Order {order_id} rejected by exchange: {reject_reason}")
            if filled > 0 or status in ('filled', 'closed'):
                return fetched
            # Still 'new'/'partiallyfilled' — resting in the book, treat as live.
            return fetched

        return order

    def create_spot_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
        try:
            if not self.exchange.markets:
                self.exchange.load_markets()
            market = self.exchange.market(symbol)

            # Apply exchange price and amount precision formatting
            formatted_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if side == 'sell':
                coin = symbol.split('/')[0]
                try:
                    bal = self.fetch_balance()
                    free_coin = float(bal.get(coin, {}).get('free', 0.0) or 0.0)
                    if free_coin > 0:
                        amount = min(amount, free_coin)
                        formatted_amount = float(self.exchange.amount_to_precision(symbol, amount))
                        amount_precision = market.get('precision', {}).get('amount')
                        step = float(amount_precision) if amount_precision else 0.0001
                        if formatted_amount > free_coin and formatted_amount >= step:
                            formatted_amount = float(self.exchange.amount_to_precision(symbol, formatted_amount - step))
                except Exception as b_err:
                    logger.debug(f"Could not check free balance for {coin}: {b_err}")

            formatted_price = float(self.exchange.price_to_precision(symbol, price)) if price > 0 else None

            if order_type == 'market' or formatted_price is None:
                return self._verify_filled(self.exchange.create_order(symbol, 'market', side, formatted_amount), symbol)

            # NOTE: postOnly is deliberately NOT used here. These limit orders are priced to
            # cross the spread on purpose (see execute_smart_order), so Bybit's matching engine
            # rejects them with EC_PostOnlyWillTakeLiquidity — and it does so *asynchronously*,
            # returning HTTP 200 with retCode 0, so no exception is ever raised. "Maker-only"
            # and "fill immediately" are mutually exclusive; execution certainty wins here.
            try:
                order = self.exchange.create_order(symbol, order_type, side, formatted_amount, formatted_price)
                return self._verify_filled(order, symbol)
            except Exception as limit_err:
                err_msg = str(limit_err)
                if "170193" in err_msg or "higher than" in err_msg.lower() or "lower than" in err_msg.lower():
                    logger.warning(f"⚠️ Bybit Price Collar limit hit ({err_msg}). Falling back to MARKET order for {symbol}...")
                    return self._verify_filled(self.exchange.create_order(symbol, 'market', side, formatted_amount), symbol)
                raise limit_err
        except Exception as e:
            logger.error(f"Bybit Order creation error for {symbol}: {e}")
            raise e

    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        target_sym = symbol if symbol and symbol != "AUTO" else getattr(config, 'symbol', 'SHIB/USDT')
        if target_sym == "AUTO":
            target_sym = "SHIB/USDT"

        # 1. Spread Trap Filter: only block *entries* on a wide spread. An exit must never be
        # blocked by it — refusing to sell is what leaves a position stuck in a falling market.
        if side == 'buy':
            spread_pct = None
            try:
                ticker = self.exchange.fetch_ticker(target_sym)
                bid_price = float(ticker.get('bid') or current_price)
                ask_price = float(ticker.get('ask') or current_price)
                spread_pct = ((ask_price - bid_price) / (bid_price + 1e-10)) * 100.0
            except Exception as spread_err:
                logger.debug(f"Spread check skipped for {target_sym}: {spread_err}")

            if spread_pct is not None and spread_pct > 0.30:
                logger.warning(f"🛑 SPREAD TRAP REJECTION: {target_sym} orderbook spread ({spread_pct:.3f}%) exceeds max limit (0.30%). Entry skipped.")
                raise Exception(f"Spread too wide ({spread_pct:.2f}% > 0.30%)")

        # Use exact current market price for buy orders to strictly respect Bybit price collar
        limit_price = current_price if side == 'buy' else current_price * 0.9995

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

    def fetch_convert_candidates(self) -> List[Dict[str, Any]]:
        """Lists wallet coins eligible for Bybit's Convert flow.

        Convert has far lower minimums than the spot order book (e.g. 841 SHIB vs the
        ~1.08M SHIB needed to clear a $5 spot order), so leftovers that can never be
        sold as an order can still be recovered here.
        """
        try:
            res = self.exchange.privateGetV5AssetExchangeQueryCoinList({'accountType': 'eb_convert_uta'})
        except Exception as e:
            logger.error(f"Could not fetch Bybit convert coin list: {e}")
            return []

        candidates = []
        for c in res.get('result', {}).get('coins', []) or []:
            coin = c.get('coin')
            if not coin or coin.upper() in ('USDT', 'USDC', 'USD'):
                continue
            balance = float(c.get('balance') or 0)
            min_amount = float(c.get('singleFromMinLimit') or 0)
            max_amount = float(c.get('singleFromMaxLimit') or 0)
            if balance <= 0 or min_amount <= 0 or balance < min_amount:
                continue
            candidates.append({
                'coin': coin,
                'balance': balance,
                'min_amount': min_amount,
                'max_amount': max_amount,
            })
        return candidates

    def convert_to_usdt(self, coin: str, amount: float) -> Dict[str, Any]:
        """Converts a coin balance to USDT via Bybit Convert (quote -> confirm)."""
        quote = self.exchange.privatePostV5AssetExchangeQuoteApply({
            'fromCoin': coin,
            'toCoin': 'USDT',
            'requestCoin': coin,
            'requestAmount': str(amount),
            'accountType': 'eb_convert_uta',
        })
        result = quote.get('result', {}) or {}
        quote_tx_id = result.get('quoteTxId')
        if not quote_tx_id:
            raise Exception(f"No quote returned for {coin}: {quote}")

        to_amount = result.get('toAmount')
        confirm = self.exchange.privatePostV5AssetExchangeConvertExecute({'quoteTxId': quote_tx_id})
        return {
            'coin': coin,
            'from_amount': amount,
            'to_amount': to_amount,
            'quote_tx_id': quote_tx_id,
            'info': confirm,
        }

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
