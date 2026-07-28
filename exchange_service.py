import time
import logging
import ccxt
import pandas as pd
from typing import Dict, Any, List, Optional
from config import config

logger = logging.getLogger(__name__)

class PaperExchange:
    """Simulates a CEX exchange for paper trading / backtesting risk-free."""
    def __init__(self, initial_usdt: float):
        self.usdt_balance = initial_usdt
        self.asset_balance = 0.0
        self.open_orders: List[Dict[str, Any]] = []
        self.closed_orders: List[Dict[str, Any]] = []
        self.order_id_counter = 1000

    def get_balance(self, symbol: str) -> Dict[str, float]:
        base_currency = symbol.split('/')[0]
        return {
            'USDT': {'free': self.usdt_balance, 'used': 0.0, 'total': self.usdt_balance},
            base_currency: {'free': self.asset_balance, 'used': 0.0, 'total': self.asset_balance}
        }

    def create_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
        self.order_id_counter += 1
        order = {
            'id': str(self.order_id_counter),
            'symbol': symbol,
            'type': order_type,
            'side': side,
            'amount': amount,
            'price': price,
            'status': 'open',
            'timestamp': int(time.time() * 1000)
        }
        
        cost = amount * price
        if side == 'buy':
            if self.usdt_balance < cost:
                raise Exception(f"Paper trading insufficient USDT balance. Available: {self.usdt_balance:.2f}, Required: {cost:.2f}")
            self.usdt_balance -= cost
            self.asset_balance += amount
            order['status'] = 'closed'
            order['filled'] = amount
            logger.info(f"🟢 [PAPER BUY EXECUTED] {amount:.4f} {symbol.split('/')[0]} @ ${price:.2f} (Cost: ${cost:.2f})")
        elif side == 'sell':
            if self.asset_balance < amount:
                # Sell all available if slightly off due to precision
                amount = self.asset_balance
                cost = amount * price
            if amount <= 0:
                raise Exception("Paper trading no asset balance available to sell.")
            self.asset_balance -= amount
            self.usdt_balance += cost
            order['status'] = 'closed'
            order['filled'] = amount
            logger.info(f"🔴 [PAPER SELL EXECUTED] {amount:.4f} {symbol.split('/')[0]} @ ${price:.2f} (Received: ${cost:.2f})")

        self.closed_orders.append(order)
        return order


class ExchangeService:
    """Interface for Bybit CEX via CCXT with seamless Paper Trading support."""
    def __init__(self):
        self.is_paper = config.paper_trading
        
        exchange_params = {
            'apiKey': config.bybit_api_key,
            'secret': config.bybit_api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot'
            }
        }
        
        if config.testnet:
            exchange_params['urls'] = {'api': ccxt.bybit().urls['test']}
            
        self.exchange = ccxt.bybit(exchange_params)
        self.paper = PaperExchange(config.initial_capital) if self.is_paper else None
        
        logger.info(f"Exchange initialized. Mode: {'PAPER TRADING' if self.is_paper else 'LIVE BYBIT SPOT'}")

    def fetch_ticker(self, symbol: str = config.symbol) -> Dict[str, Any]:
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise

    def fetch_ohlcv(self, symbol: str = config.symbol, timeframe: str = config.timeframe, limit: int = 100) -> pd.DataFrame:
        """Fetches OHLCV candlestick data and returns a pandas DataFrame."""
        try:
            raw_candles = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(raw_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            raise

    def fetch_balance(self, symbol: str = config.symbol) -> Dict[str, Any]:
        if self.is_paper and self.paper:
            return self.paper.get_balance(symbol)
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise

    def create_spot_order(self, side: str, amount: float, price: Optional[float] = None, order_type: str = 'market', symbol: str = config.symbol) -> Dict[str, Any]:
        """Creates a buy or sell order (live or paper)."""
        if price is None:
            ticker = self.fetch_ticker(symbol)
            price = ticker['last']
            
        if self.is_paper and self.paper:
            return self.paper.create_order(symbol, order_type, side, amount, price)
        
        try:
            if order_type == 'market':
                return self.exchange.create_market_order(symbol, side, amount)
            else:
                return self.exchange.create_limit_order(symbol, side, amount, price)
        except Exception as e:
            logger.error(f"Error executing {side} order on {symbol}: {e}")
            raise
