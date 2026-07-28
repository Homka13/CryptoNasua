import logging
import pandas as pd
from typing import Dict, Any, List
from config import config
from strategy import HybridStrategy
from exchange_service import ExchangeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class Backtester:
    """Backtests the Hybrid Micro-Grid + RSI/EMA strategy on historical OHLCV candles."""

    def __init__(self, initial_capital: float = 10.0, trade_size: float = 2.5):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trade_size = trade_size
        self.strategy = HybridStrategy()
        self.trades: List[Dict[str, Any]] = []

    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Executes historical simulation row by row."""
        df_calc = self.strategy.calculate_indicators(df)
        
        position = None
        asset_balance = 0.0

        for i in range(self.strategy.ema_slow + 5, len(df_calc)):
            window = df_calc.iloc[:i+1]
            current_row = window.iloc[-1]
            current_price = float(current_row['close'])
            timestamp = current_row['datetime']

            signal, reason, meta = self.strategy.analyze(window, position)

            if signal == 'BUY' and position is None:
                if self.capital >= config.min_order_usdt:
                    cost = min(self.trade_size, self.capital)
                    asset_balance = cost / current_price
                    self.capital -= cost
                    position = {
                        'entry_price': current_price,
                        'amount': asset_balance,
                        'cost': cost,
                        'entry_time': timestamp
                    }

            elif signal == 'SELL' and position is not None:
                sale_value = asset_balance * current_price
                pnl = sale_value - position['cost']
                pnl_pct = (pnl / position['cost']) * 100
                self.capital += sale_value
                
                self.trades.append({
                    'entry_time': position['entry_time'],
                    'exit_time': timestamp,
                    'entry_price': position['entry_price'],
                    'exit_price': current_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'reason': reason
                })
                
                position = None
                asset_balance = 0.0

        # Close active position at last price if any
        if position is not None:
            last_price = float(df_calc.iloc[-1]['close'])
            sale_value = asset_balance * last_price
            pnl = sale_value - position['cost']
            self.capital += sale_value
            self.trades.append({
                'entry_time': position['entry_time'],
                'exit_time': df_calc.iloc[-1]['datetime'],
                'entry_price': position['entry_price'],
                'exit_price': last_price,
                'pnl': pnl,
                'pnl_pct': (pnl / position['cost']) * 100,
                'reason': 'END OF BACKTEST'
            })

        return self._generate_report()

    def _generate_report(self) -> Dict[str, Any]:
        total_trades = len(self.trades)
        if total_trades == 0:
            return {'total_trades': 0, 'profit_usdt': 0.0, 'win_rate': 0.0, 'final_capital': self.capital}

        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100
        total_pnl = self.capital - self.initial_capital
        total_pnl_pct = (total_pnl / self.initial_capital) * 100

        logger.info("=" * 50)
        logger.info("📊 BACKTESTING RESULTS")
        logger.info("=" * 50)
        logger.info(f"Initial Capital: ${self.initial_capital:.2f}")
        logger.info(f"Final Capital:   ${self.capital:.2f}")
        logger.info(f"Total PnL:       ${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)")
        logger.info(f"Total Trades:    {total_trades}")
        logger.info(f"Win Rate:        {win_rate:.1f}% ({len(winning_trades)} W / {len(losing_trades)} L)")
        logger.info("=" * 50)

        return {
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'total_trades': total_trades,
            'win_rate': win_rate
        }

if __name__ == "__main__":
    try:
        ex = ExchangeService()
        logger.info(f"Fetching historical 5m candles for {config.symbol}...")
        df_candles = ex.fetch_ohlcv(symbol=config.symbol, timeframe=config.timeframe, limit=500)
        tester = Backtester()
        tester.run(df_candles)
    except Exception as e:
        logger.error(f"Backtest error: {e}")
