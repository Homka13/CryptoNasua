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

    supports_convert = True

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

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def get_min_notional(self, symbol: str) -> float:
        """Dynamically fetches the exact minimum order notional (USDT) for a symbol."""
        try:
            if not self.exchange.markets:
                self.exchange.load_markets()
            market = self.exchange.market(symbol)
            min_cost = market.get('limits', {}).get('cost', {}).get('min')
            if min_cost is not None and float(min_cost) > 0:
                return float(min_cost)
        except Exception as e:
            logger.debug(f"Could not fetch dynamic min_notional for {symbol}: {e}")
        return 5.0  # Binance Spot MIN_NOTIONAL fallback

    def create_spot_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
        try:
            if not self.exchange.markets:
                self.exchange.load_markets()
            market = self.exchange.market(symbol)

            formatted_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if side == 'sell':
                # Release any coins locked by resting orders, then never try to sell more
                # than is actually free — a padded sell amount is rejected outright.
                try:
                    self.exchange.cancel_all_orders(symbol)
                except Exception as c_err:
                    logger.debug(f"Auto-cancel open orders prior to SELL for {symbol}: {c_err}")

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

            try:
                order = self.exchange.create_order(symbol, order_type, side, formatted_amount, formatted_price)
                return self._verify_filled(order, symbol)
            except Exception as limit_err:
                err_msg = str(limit_err)
                if "PERCENT_PRICE" in err_msg or "PRICE_FILTER" in err_msg or "higher than" in err_msg.lower():
                    logger.warning(f"⚠️ Binance Price Collar limit hit ({err_msg}). Falling back to MARKET order for {symbol}...")
                    return self._verify_filled(self.exchange.create_order(symbol, 'market', side, formatted_amount), symbol)
                raise limit_err
        except Exception as e:
            logger.error(f"Binance Order creation error for {symbol}: {e}")
            raise e

    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        target_sym = symbol if symbol and symbol != "AUTO" else getattr(config, 'symbol', 'SOL/USDT')
        if target_sym == "AUTO":
            target_sym = "SOL/USDT"

        # Spread Trap Filter: only block *entries* on a wide spread. An exit must never be
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

    def fetch_convert_candidates(self) -> List[Dict[str, Any]]:
        """Lists wallet balances Binance will accept for dust conversion.

        Binance exposes this as "dust transfer": it tells you which sub-minimum balances
        are convertible and pays out in BNB rather than USDT (unlike Bybit's Convert).
        Only assets Binance itself lists here can be converted, so the eligibility check
        is the API's answer rather than our own threshold.
        """
        try:
            res = self.exchange.sapiPostAssetDustBtc()
        except Exception as e:
            logger.error(f"Could not fetch Binance dust list: {e}")
            return []

        candidates = []
        for d in res.get('details', []) or []:
            coin = d.get('asset')
            if not coin or coin.upper() in ('USDT', 'USDC', 'USD', 'BNB'):
                continue
            balance = float(d.get('amount') or 0)
            if balance <= 0:
                continue
            candidates.append({
                'coin': coin,
                'balance': balance,
                'min_amount': 0.0,          # Binance decides eligibility, not a per-coin floor
                'max_amount': balance,
                'payout': 'BNB',
            })
        return candidates

    def convert_to_usdt(self, coin: str, amount: float) -> Dict[str, Any]:
        """Converts a dust balance via Binance dust transfer.

        Note the payout is BNB, not USDT — Binance's dust endpoint has no USDT option.
        The amount is ignored: Binance always sweeps the full balance of the asset.
        """
        res = self.exchange.sapiPostAssetDust({'asset': coin})
        results = (res.get('transferResult') or [{}])[0]
        return {
            'coin': coin,
            'from_amount': float(results.get('amount') or amount),
            'to_amount': f"{results.get('transferedAmount', '?')} BNB",
            'quote_tx_id': results.get('tranId'),
            'info': res,
        }

    def fetch_dynamic_hot_pairs(self, min_volume: float = 1000000.0, limit: int = 25) -> List[str]:
        try:
            tickers = self.exchange.fetch_tickers()
            usdt_tickers = []
            for sym, t in tickers.items():
                if sym.endswith('/USDT') and 'BEAR/' not in sym and 'BULL/' not in sym and 'UP/' not in sym and 'DOWN/' not in sym:
                    # Binance returns these keys present-but-None on illiquid pairs, so
                    # dict.get(key, 0) still yields None and the fallback multiply blew up —
                    # taking the whole screener down to its 5-coin hardcoded fallback.
                    quote_vol = t.get('quoteVolume')
                    if quote_vol is None:
                        quote_vol = float(t.get('baseVolume') or 0) * float(t.get('last') or 0)
                    quote_vol = float(quote_vol or 0)
                    percentage = abs(float(t.get('percentage') or 0.0))
                    if quote_vol >= min_volume:
                        usdt_tickers.append((sym, quote_vol, percentage))

            usdt_tickers.sort(key=lambda x: (x[2], x[1]), reverse=True)
            top_pairs = [x[0] for x in usdt_tickers[:limit]]
            return top_pairs if top_pairs else ["SOL/USDT", "BTC/USDT", "ETH/USDT", "WLD/USDT", "PEPE/USDT"]
        except Exception as e:
            logger.error(f"Error fetching Binance hot pairs: {e}")
            return ["SOL/USDT", "BTC/USDT", "ETH/USDT", "WLD/USDT", "PEPE/USDT"]
