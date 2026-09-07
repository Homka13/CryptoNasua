import os
import json
import time
import logging
import asyncio
import abc
from typing import Dict, Any, List, Optional, Tuple

import ccxt.async_support as ccxt_async
import pandas as pd

from config import config

logger = logging.getLogger(__name__)


class BaseExchangeService(abc.ABC):
    """Abstract interface for a spot exchange used by the trading bot.

    Implementations must provide non-blocking (async) exchange operations and a
    clean ``close()`` to release resources (CCXT/aiohttp sessions) on shutdown.
    """

    @property
    @abc.abstractmethod
    def is_paper(self) -> bool:
        """True when running in simulated (paper) mode."""

    @abc.abstractmethod
    async def fetch_ticker(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        ...

    @abc.abstractmethod
    async def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def fetch_real_balance(self) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def fetch_dynamic_hot_pairs(
        self, min_volume: float = 1_000_000.0, limit: int = 15
    ) -> List[str]:
        ...

    @abc.abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def check_vwap_slippage(
        self, symbol: str, amount: float, side: str
    ) -> Tuple[float, float, float]:
        ...

    @abc.abstractmethod
    async def execute_smart_order(
        self,
        side: str,
        total_amount: float,
        current_price: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...


def _resolve_symbol(symbol: Optional[str]) -> str:
    """Return a concrete symbol, falling back to config.symbol and a sane default."""
    target = symbol or config.symbol
    if not target or target == "AUTO":
        target = "SHIB/USDT"
    return target


def calculate_vwap_slippage(
    levels: List[Tuple[float, float]], amount: float
) -> Tuple[float, float, float]:
    """Pure VWAP computation over an order-book side.

    ``levels`` is a list of ``(price, volume)`` sorted best-to-worst (asks for buys,
    bids for sells). Returns ``(vwap_price, best_price, slippage_pct)`` where slippage
    is ``abs(vwap - best) / best``.
    """
    if not levels:
        raise ValueError("No order-book levels provided")

    best_price = float(levels[0][0])
    remaining_volume = amount
    total_cost = 0.0
    filled_volume = 0.0

    for price_level, level_volume in levels:
        price_level = float(price_level)
        level_volume = float(level_volume)

        take_volume = min(remaining_volume, level_volume)
        total_cost += take_volume * price_level
        filled_volume += take_volume
        remaining_volume -= take_volume

        if remaining_volume <= 0:
            break

    if remaining_volume > 0 and filled_volume > 0:
        last_price = float(levels[-1][0])
        total_cost += remaining_volume * last_price
        filled_volume += remaining_volume

    vwap_price = total_cost / filled_volume if filled_volume > 0 else best_price
    slippage_pct = abs(vwap_price - best_price) / best_price

    return vwap_price, best_price, slippage_pct


class PaperBroker:
    """Simulated order book + balance ledger for paper trading.

    Kept synchronous on purpose: it is a pure in-memory model with no I/O, so it
    does not block the asyncio event loop. Exchange-visible methods expose it
    through async wrappers.
    """

    def __init__(self, initial_usdt: float):
        self.usdt_balance = initial_usdt
        self.asset_balance = 0.0
        self.open_orders: List[Dict[str, Any]] = []
        self.closed_orders: List[Dict[str, Any]] = []
        self.order_id_counter = 1000

    def get_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        target = _resolve_symbol(symbol)
        base_currency = target.split('/')[0] if '/' in target else 'SHIB'
        return {
            'USDT': {'free': self.usdt_balance, 'used': 0.0, 'total': self.usdt_balance},
            base_currency: {'free': self.asset_balance, 'used': 0.0, 'total': self.asset_balance},
        }

    def create_order(
        self, symbol: str, order_type: str, side: str, amount: float, price: float
    ) -> Dict[str, Any]:
        self.order_id_counter += 1
        order = {
            'id': str(self.order_id_counter),
            'symbol': symbol,
            'type': order_type,
            'side': side,
            'amount': amount,
            'price': price,
            'status': 'open',
            'timestamp': int(time.time() * 1000),
        }

        cost = amount * price
        if side == 'buy':
            if self.usdt_balance < cost:
                raise Exception(
                    f"Paper trading insufficient USDT balance. Available: "
                    f"{self.usdt_balance:.2f}, Required: {cost:.2f}"
                )
            self.usdt_balance -= cost
            self.asset_balance += amount
            order['status'] = 'closed'
            order['filled'] = amount
            logger.info(
                f"🟢 [PAPER BUY EXECUTED] {amount:.4f} {symbol.split('/')[0]} "
                f"@ ${price:.2f} (Cost: ${cost:.2f})"
            )
        elif side == 'sell':
            if self.asset_balance < amount:
                amount = self.asset_balance
                cost = amount * price
            if amount <= 0:
                raise Exception("Paper trading no asset balance available to sell.")
            self.asset_balance -= amount
            self.usdt_balance += cost
            order['status'] = 'closed'
            order['filled'] = amount
            logger.info(
                f"🔴 [PAPER SELL EXECUTED] {amount:.4f} {symbol.split('/')[0]} "
                f"@ ${price:.2f} (Received: ${cost:.2f})"
            )

        self.closed_orders.append(order)
        return order


class BybitLiveExchange(BaseExchangeService):
    """Live Bybit Spot exchange via CCXT async_support."""

    def __init__(self) -> None:
        self._api_key, self._secret = self._load_credentials()

        public_params: Dict[str, Any] = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        }
        private_params: Dict[str, Any] = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        }

        if config.testnet:
            test_urls = {'api': ccxt_async.bybit().urls['test']}
            public_params['urls'] = test_urls
            private_params['urls'] = test_urls

        if self._api_key:
            private_params['apiKey'] = self._api_key
        if self._secret:
            private_params['secret'] = self._secret

        self.public_exchange = ccxt_async.bybit(public_params)
        self.exchange = ccxt_async.bybit(private_params)

    def _load_credentials(self) -> Tuple[str, str]:
        api_key = config.bybit_api_key.strip()
        secret_val = config.bybit_api_secret.strip()

        if "your_bybit_api_key" in api_key.lower():
            api_key = ""

        if not api_key:
            appdata = os.getenv("APPDATA", "")
            oauth_path = (
                os.path.join(appdata, "bybit", "oauth_token.json")
                if appdata
                else os.path.expanduser("~/.bybit/oauth_token.json")
            )
            if os.path.exists(oauth_path):
                try:
                    with open(oauth_path, 'r', encoding='utf-8') as f:
                        oauth_data = json.load(f)
                    ai_acc = oauth_data.get('ai-account', {})
                    if ai_acc.get('api_key') and ai_acc.get('api_secret'):
                        api_key = ai_acc['api_key']
                        secret_val = ai_acc['api_secret']
                        logger.info("🔑 Bybit OAuth AI Account credentials loaded from oauth_token.json!")
                except Exception as e:
                    logger.error(f"Error reading OAuth token file: {e}")

        if config.bybit_private_key_path and os.path.exists(config.bybit_private_key_path):
            try:
                with open(config.bybit_private_key_path, 'r', encoding='utf-8') as f:
                    secret_val = f.read()
                logger.info("🔑 Bybit RSA Private Key loaded for API authentication.")
            except Exception as e:
                logger.error(f"Error loading RSA Private Key file: {e}")
        elif os.path.exists("bybit_rsa_private.pem") and not secret_val:
            try:
                with open("bybit_rsa_private.pem", 'r', encoding='utf-8') as f:
                    secret_val = f.read()
                logger.info("🔑 Bybit RSA Private Key loaded from bybit_rsa_private.pem")
            except Exception as e:
                logger.error(f"Error loading bybit_rsa_private.pem: {e}")

        return api_key, secret_val

    @property
    def is_paper(self) -> bool:
        return False

    async def fetch_ticker(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        target = _resolve_symbol(symbol)
        try:
            return await self.public_exchange.fetch_ticker(target)
        except Exception as e:
            logger.error(f"Error fetching ticker for {target}: {e}")
            raise

    async def fetch_ohlcv(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        target_symbol = _resolve_symbol(symbol)
        tf = timeframe or config.timeframe
        try:
            raw_candles = await self.public_exchange.fetch_ohlcv(
                target_symbol, timeframe=tf, limit=limit
            )
            df = pd.DataFrame(
                raw_candles,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            )
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {target_symbol}: {e}")
            raise

    async def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        try:
            return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise

    async def fetch_real_balance(self) -> Dict[str, Any]:
        try:
            return await self.exchange.fetch_balance()
        except Exception as e:
            logger.debug(f"Could not fetch real Bybit balance: {e}")
            return {}

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        return await self.public_exchange.fetch_order_book(symbol, limit=limit)

    async def fetch_dynamic_hot_pairs(
        self, min_volume: float = 1_000_000.0, limit: int = 15
    ) -> List[str]:
        try:
            tickers = await self.exchange.fetch_tickers()
            candidates = []

            for symbol, t in tickers.items():
                if not symbol.endswith('/USDT'):
                    continue
                if any(x in symbol for x in ['3L', '3S', 'BEAR', 'BULL', 'USDC', 'DAI', 'FDUSD', 'EUR']):
                    continue

                quote_vol = float(t.get('quoteVolume') or t.get('baseVolume', 0) * t.get('last', 0))
                high = float(t.get('high') or 0)
                low = float(t.get('low') or 0)

                if quote_vol < min_volume or low == 0:
                    continue

                volatility_pct = (high - low) / low
                candidates.append({
                    'symbol': symbol,
                    'volume_usdt': quote_vol,
                    'volatility': volatility_pct,
                })

            candidates.sort(key=lambda x: x['volatility'], reverse=True)
            hot_symbols = [c['symbol'] for c in candidates[:limit]]

            if hot_symbols:
                logger.info(
                    f"🔥 [DYNAMIC SCREENER]: Auto-discovered {len(hot_symbols)} "
                    f"hot volatile pairs: {hot_symbols[:5]}..."
                )
                return hot_symbols
            return config.trading_pairs
        except Exception as e:
            logger.error(f"Error running Dynamic Market Screener: {e}. Falling back to default list.")
            return config.trading_pairs

    async def check_vwap_slippage(
        self, symbol: str, amount: float, side: str
    ) -> Tuple[float, float, float]:
        try:
            orderbook = await self.fetch_order_book(symbol, limit=20)
            levels = orderbook['asks'] if side.lower() == 'buy' else orderbook['bids']

            if not levels:
                ticker = await self.fetch_ticker(symbol)
                best_price = ticker['last']
                return best_price, best_price, 0.0

            vwap_price, best_price, slippage_pct = calculate_vwap_slippage(levels, amount)

            logger.info(
                f"📊 [ORDERBOOK VWAP]: Side: {side.upper()} | Best: ${best_price:.4f} | "
                f"VWAP: ${vwap_price:.4f} | Slippage: {slippage_pct*100:.3f}%"
            )
            return vwap_price, best_price, slippage_pct
        except Exception as e:
            logger.error(f"Error calculating Orderbook VWAP: {e}")
            ticker = await self.fetch_ticker(symbol)
            p = ticker['last']
            return p, p, 0.0

    async def execute_smart_order(
        self,
        side: str,
        total_amount: float,
        current_price: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target = _resolve_symbol(symbol)

        _vwap_price, _best_price, slippage_pct = await self.check_vwap_slippage(
            target, total_amount, side
        )

        if slippage_pct > config.max_slippage_pct:
            raise Exception(
                f"🛑 QUANT REJECTION: Orderbook slippage ({slippage_pct*100:.2f}%) "
                f"exceeds max allowed limit ({config.max_slippage_pct*100:.2f}%)"
            )

        slices_count = (
            config.iceberg_slices if config.use_iceberg and total_amount > 0.001 else 1
        )
        slice_amount = total_amount / slices_count
        executed_orders = []

        logger.info(
            f"🧊 [QUANT ENGINE]: Executing {side.upper()} order for {total_amount:.4f} "
            f"{target} ({slices_count} Iceberg slice(s))"
        )

        for slice_idx in range(slices_count):
            ticker = await self.fetch_ticker(target)
            ask_price = ticker.get('ask', ticker['last'])
            bid_price = ticker.get('bid', ticker['last'])

            if side.lower() == 'buy':
                limit_price = ask_price * (1.0 + config.limit_offset_pct) if config.use_limit_offset else ask_price
            else:
                limit_price = bid_price * (1.0 - config.limit_offset_pct) if config.use_limit_offset else bid_price

            order = await self._create_spot_order(
                side=side,
                amount=slice_amount,
                price=limit_price,
                order_type='limit' if config.use_limit_offset else 'market',
                symbol=target,
            )
            executed_orders.append(order)

            logger.info(
                f"  └─ Slice #{slice_idx+1}/{slices_count}: {slice_amount:.4f} "
                f"{target} @ Limit Offset ${limit_price:.4f}"
            )

            if slice_idx < slices_count - 1 and config.iceberg_delay_sec > 0:
                await asyncio.sleep(config.iceberg_delay_sec)

        return executed_orders

    async def _create_spot_order(
        self,
        side: str,
        amount: float,
        price: Optional[float] = None,
        order_type: str = 'market',
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = _resolve_symbol(symbol)
        if price is None:
            ticker = await self.fetch_ticker(target)
            price = ticker['last']

        try:
            if order_type == 'market':
                return await self.exchange.create_market_order(target, side, amount)
            return await self.exchange.create_limit_order(target, side, amount, price)
        except Exception as e:
            logger.error(f"Error executing {side} order on {target}: {e}")
            raise

    async def close(self) -> None:
        for client in (self.public_exchange, self.exchange):
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"Error closing exchange client: {e}")


class BybitPaperExchange(BaseExchangeService):
    """Paper-trading implementation wrapping a live market-data feed + simulated ledger."""

    def __init__(self, live_exchange: Optional[BybitLiveExchange] = None) -> None:
        # Reuse a live exchange purely for read-only public market data.
        self._live = live_exchange or BybitLiveExchange()
        self.paper = PaperBroker(config.initial_capital)

    @property
    def is_paper(self) -> bool:
        return True

    async def fetch_ticker(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return await self._live.fetch_ticker(symbol)

    async def fetch_ohlcv(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        return await self._live.fetch_ohlcv(symbol, timeframe, limit)

    async def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.paper.get_balance(symbol)

    async def fetch_real_balance(self) -> Dict[str, Any]:
        return await self._live.fetch_real_balance()

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        return await self._live.fetch_order_book(symbol, limit)

    async def fetch_dynamic_hot_pairs(
        self, min_volume: float = 1_000_000.0, limit: int = 15
    ) -> List[str]:
        return await self._live.fetch_dynamic_hot_pairs(min_volume, limit)

    async def check_vwap_slippage(
        self, symbol: str, amount: float, side: str
    ) -> Tuple[float, float, float]:
        return await self._live.check_vwap_slippage(symbol, amount, side)

    async def execute_smart_order(
        self,
        side: str,
        total_amount: float,
        current_price: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target = _resolve_symbol(symbol)

        _vwap_price, _best_price, slippage_pct = await self.check_vwap_slippage(
            target, total_amount, side
        )

        if slippage_pct > config.max_slippage_pct:
            raise Exception(
                f"🛑 QUANT REJECTION: Orderbook slippage ({slippage_pct*100:.2f}%) "
                f"exceeds max allowed limit ({config.max_slippage_pct*100:.2f}%)"
            )

        slices_count = (
            config.iceberg_slices if config.use_iceberg and total_amount > 0.001 else 1
        )
        slice_amount = total_amount / slices_count
        executed_orders = []

        for slice_idx in range(slices_count):
            ticker = await self.fetch_ticker(target)
            ask_price = ticker.get('ask', ticker['last'])
            bid_price = ticker.get('bid', ticker['last'])

            if side.lower() == 'buy':
                price = ask_price * (1.0 + config.limit_offset_pct) if config.use_limit_offset else ask_price
            else:
                price = bid_price * (1.0 - config.limit_offset_pct) if config.use_limit_offset else bid_price

            order = self.paper.create_order(target, 'limit', side, slice_amount, price)
            executed_orders.append(order)

            if slice_idx < slices_count - 1 and config.iceberg_delay_sec > 0:
                await asyncio.sleep(config.iceberg_delay_sec)

        return executed_orders

    async def close(self) -> None:
        await self._live.close()


def create_exchange_service() -> BaseExchangeService:
    """Factory that returns the appropriate exchange service for the configured mode."""
    if config.paper_trading:
        logger.info("Exchange initialized. Mode: PAPER TRADING")
        return BybitPaperExchange()
    logger.info("Exchange initialized. Mode: LIVE BYBIT SPOT")
    return BybitLiveExchange()