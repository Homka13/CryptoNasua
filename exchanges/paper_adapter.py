"""Paper Trading CEX Simulator Exchange Adapter."""

import time
import logging
import pandas as pd
from typing import Dict, Any, List, Optional
from exchanges.base_adapter import BaseExchangeAdapter
from config import config

logger = logging.getLogger(__name__)

class PaperExchangeAdapter(BaseExchangeAdapter):
    """Simulates a CEX exchange for paper trading / risk-free execution."""

    def __init__(self, public_exchange, initial_usdt: float = 10.0):
        self.public_exchange = public_exchange
        self.usdt_balance = initial_usdt
        self.asset_balance = 0.0
        self.open_orders: List[Dict[str, Any]] = []
        self.closed_orders: List[Dict[str, Any]] = []
        self.order_id_counter = 1000

    def get_min_notional(self, symbol: str) -> float:
        """Dynamically fetches exact minimum order notional value (in USDT) for symbol."""
        try:
            if hasattr(self.public_exchange, 'market'):
                market = self.public_exchange.market(symbol)
                min_cost = market.get('limits', {}).get('cost', {}).get('min')
                if min_cost is not None and float(min_cost) > 0:
                    return float(min_cost)
        except Exception:
            pass
        return 1.0  # Default minimum order for paper trading

    def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100) -> pd.DataFrame:
        target = symbol if symbol and symbol != "AUTO" else "SHIB/USDT"
        ohlcv = self.public_exchange.fetch_ohlcv(target, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_balance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        target = symbol if symbol and symbol != "AUTO" else "SHIB/USDT"
        base_currency = target.split('/')[0] if '/' in target else 'SHIB'
        return {
            'USDT': {'free': self.usdt_balance, 'used': 0.0, 'total': self.usdt_balance},
            base_currency: {'free': self.asset_balance, 'used': 0.0, 'total': self.asset_balance}
        }

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        target = symbol if symbol and symbol != "AUTO" else "SHIB/USDT"
        return self.public_exchange.fetch_ticker(target)

    def create_spot_order(self, symbol: str, order_type: str, side: str, amount: float, price: float) -> Dict[str, Any]:
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
                raise Exception(f"Paper trading insufficient USDT balance. Available: ${self.usdt_balance:.2f}, Required: ${cost:.2f}")
            self.usdt_balance -= cost
            self.asset_balance += amount
            order['status'] = 'closed'
            order['filled'] = amount
            logger.info(f"🟢 [PAPER BUY EXECUTED] {amount:.4f} {symbol.split('/')[0]} @ ${price:.4f} (Cost: ${cost:.2f})")
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
            logger.info(f"🔴 [PAPER SELL EXECUTED] {amount:.4f} {symbol.split('/')[0]} @ ${price:.4f} (Received: ${cost:.2f})")

        self.closed_orders.append(order)
        return order

    def execute_smart_order(self, side: str, amount: float, current_price: float, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        target_sym = symbol if symbol and symbol != "AUTO" else getattr(config, 'symbol', 'SHIB/USDT')
        if target_sym == "AUTO":
            target_sym = "SHIB/USDT"
        order = self.create_spot_order(target_sym, 'limit', side, amount, current_price)
        return [order]

    def fetch_dynamic_hot_pairs(self, min_volume: float = 1000000.0, limit: int = 25) -> List[str]:
        try:
            tickers = self.public_exchange.fetch_tickers()
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
            logger.error(f"Error fetching paper hot pairs: {e}")
            return ["SOL/USDT", "WLD/USDT", "PUMP/USDT", "PEPE/USDT", "SHIB/USDT"]
