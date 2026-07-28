import os
import time
import logging
import asyncio
import ccxt
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
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
        
        secret_val = config.bybit_api_secret
        if config.bybit_private_key_path and os.path.exists(config.bybit_private_key_path):
            with open(config.bybit_private_key_path, 'r', encoding='utf-8') as f:
                secret_val = f.read()
            logger.info("🔑 Bybit RSA Private Key loaded for API authentication.")
        elif os.path.exists("bybit_rsa_private.pem") and not secret_val:
            with open("bybit_rsa_private.pem", 'r', encoding='utf-8') as f:
                secret_val = f.read()
            logger.info("🔑 Bybit RSA Private Key loaded from bybit_rsa_private.pem")

        exchange_params = {
            'apiKey': config.bybit_api_key,
            'secret': secret_val,
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

    def calculate_orderbook_vwap(self, symbol: str, amount: float, side: str) -> Tuple[float, float, float]:
        """
        Calculates the Volume-Weighted Average Price (VWAP) against live orderbook depth.
        Returns: (vwap_price, best_price, slippage_pct)
        """
        try:
            orderbook = self.exchange.fetch_order_book(symbol, limit=20)
            levels = orderbook['asks'] if side.lower() == 'buy' else orderbook['bids']
            
            if not levels:
                ticker = self.fetch_ticker(symbol)
                best_price = ticker['last']
                return best_price, best_price, 0.0

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

            # If orderbook depth is smaller than requested volume, fill remaining at last level price
            if remaining_volume > 0 and filled_volume > 0:
                last_price = float(levels[-1][0])
                total_cost += remaining_volume * last_price
                filled_volume += remaining_volume

            vwap_price = total_cost / filled_volume if filled_volume > 0 else best_price
            slippage_pct = abs(vwap_price - best_price) / best_price
            
            logger.info(f"📊 [ORDERBOOK VWAP]: Side: {side.upper()} | Best Price: ${best_price:.4f} | VWAP: ${vwap_price:.4f} | Slippage: {slippage_pct*100:.3f}%")
            return vwap_price, best_price, slippage_pct
        except Exception as e:
            logger.error(f"Error calculating Orderbook VWAP: {e}")
            ticker = self.fetch_ticker(symbol)
            p = ticker['last']
            return p, p, 0.0

    async def execute_smart_order(self, side: str, total_amount: float, current_price: Optional[float] = None, symbol: str = config.symbol) -> List[Dict[str, Any]]:
        """
        Quant Execution Engine combining:
        1. Order Book VWAP Slippage Check
        2. Iceberg Slicing (Split into N slices with delay)
        3. Limit Order with Offset tolerance
        """
        # Step 1: Calculate Order Book VWAP & Slippage Check
        vwap_price, best_price, slippage_pct = self.calculate_orderbook_vwap(symbol, total_amount, side)
        
        if slippage_pct > config.max_slippage_pct:
            raise Exception(
                f"🛑 QUANT REJECTION: Orderbook slippage ({slippage_pct*100:.2f}%) exceeds max allowed limit ({config.max_slippage_pct*100:.2f}%)"
            )

        # Step 2: Determine Slices (Iceberg vs Single)
        slices_count = config.iceberg_slices if config.use_iceberg and total_amount > 0.001 else 1
        slice_amount = total_amount / slices_count
        executed_orders = []

        logger.info(f"🧊 [QUANT ENGINE]: Executing {side.upper()} order for {total_amount:.4f} {symbol} ({slices_count} Iceberg slice(s))")

        for slice_idx in range(slices_count):
            # Recalculate Limit with Offset price per slice
            ticker = self.fetch_ticker(symbol)
            ask_price = ticker.get('ask', ticker['last'])
            bid_price = ticker.get('bid', ticker['last'])

            if side.lower() == 'buy':
                # Set limit price +0.15% above ask to guarantee fill without market slippage
                limit_price = ask_price * (1.0 + config.limit_offset_pct) if config.use_limit_offset else ask_price
            else:
                # Set limit price -0.15% below bid to guarantee fill without market slippage
                limit_price = bid_price * (1.0 - config.limit_offset_pct) if config.use_limit_offset else bid_price

            order = self.create_spot_order(
                side=side,
                amount=slice_amount,
                price=limit_price,
                order_type='limit' if config.use_limit_offset else 'market',
                symbol=symbol
            )
            executed_orders.append(order)

            logger.info(f"  └─ Slice #{slice_idx+1}/{slices_count}: {slice_amount:.4f} {symbol} @ Limit Offset ${limit_price:.4f}")

            # Inter-slice delay for market makers to replenish orderbook depth
            if slice_idx < slices_count - 1 and config.iceberg_delay_sec > 0:
                await asyncio.sleep(config.iceberg_delay_sec)

        return executed_orders

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
