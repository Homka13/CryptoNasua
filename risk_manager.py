import logging
from typing import Dict, Any, Tuple
from config import config

logger = logging.getLogger(__name__)

class RiskManager:
    """Protects small capital ($10) by enforcing position size, min order value, and max loss limits."""

    def __init__(self):
        self.max_daily_loss = config.max_daily_loss_pct
        self.trade_size_usdt = config.trade_size_usdt
        self.min_order_usdt = config.min_order_usdt
        self.daily_start_capital = config.initial_capital
        self.current_drawdown = 0.0

    def calculate_position_size(self, usdt_free: float, current_price: float) -> Tuple[bool, float, str]:
        """
        Calculates the buy quantity based on $10 micro-capital strategy.
        Returns: (is_allowed, amount_in_coins, reason)
        """
        if usdt_free < self.min_order_usdt:
            return False, 0.0, f"Insufficient USDT balance (${usdt_free:.2f} < ${self.min_order_usdt:.2f} min)"

        # Use allocated trade size or free balance if sufficient
        alloc_usdt = max(self.trade_size_usdt, self.min_order_usdt)
        if usdt_free < alloc_usdt:
            alloc_usdt = usdt_free

        if alloc_usdt < self.min_order_usdt:
            return False, 0.0, f"Order size (${alloc_usdt:.2f}) below Bybit minimum limit (${self.min_order_usdt:.2f})"

        amount = alloc_usdt / current_price
        return True, amount, f"Order size: ${alloc_usdt:.2f} | Amount: {amount:.4f} coins @ ${current_price:.2f}"

    def check_daily_drawdown(self, current_capital: float) -> bool:
        """Returns True if daily drawdown exceeds safe limits."""
        loss_pct = (self.daily_start_capital - current_capital) / self.daily_start_capital
        self.current_drawdown = loss_pct
        
        if loss_pct >= self.max_daily_loss:
            logger.warning(f"🚨 MAX DAILY DRAWDOWN REACHED: {loss_pct*100:.2f}% loss. Trading paused.")
            return True
        return False
