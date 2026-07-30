"""Risk Management Module for Crypto Trading Bot ($10 Starting Capital Engine)."""

import logging
from typing import Dict, Any, Tuple
from config import config

logger = logging.getLogger(__name__)

class RiskManager:
    """Manages trade sizing, max drawdown limits, dynamic notional min limits, and risk allocation."""
    
    def __init__(self):
        self.max_trade_pct = config.max_trade_pct
        self.max_daily_drawdown = config.max_daily_drawdown
        self.iceberg_slices = config.iceberg_slices

    def calculate_position_size(self, usdt_balance: float, current_price: float) -> Tuple[float, float, str]:
        """
        Calculates trade amount in target coin and total cost in USDT.
        Uses 45% of available balance per trade with a hard minimum allocation of $2.60 USDT.
        Returns: (amount_in_coin, cost_in_usdt, risk_status_reason)
        """
        if usdt_balance <= 0 or current_price <= 0:
            return 0.0, 0.0, "Invalid balance or price"

        # Calculate trade allocation (45% of balance, floor at min $2.60 USDT)
        trade_allocation_usdt = usdt_balance * self.max_trade_pct
        if trade_allocation_usdt < 2.60 and usdt_balance >= 2.60:
            trade_allocation_usdt = 2.60
        elif usdt_balance < 2.60:
            trade_allocation_usdt = usdt_balance

        amount_in_coin = trade_allocation_usdt / current_price
        
        logger.info(f"💰 RiskManager Allocation: Available ${usdt_balance:.2f} | Trade Cost ${trade_allocation_usdt:.2f} ({amount_in_coin:.4f} coins @ ${current_price:.4f})")
        return amount_in_coin, trade_allocation_usdt, "OK"

    def validate_trade_risk(self, usdt_balance: float, cost_usdt: float) -> Tuple[bool, str]:
        """Validates whether executing trade violates risk parameters."""
        if cost_usdt > usdt_balance:
            return False, f"Cost (${cost_usdt:.2f}) exceeds free balance (${usdt_balance:.2f})"
        if cost_usdt < 1.0:
            return False, f"Trade cost (${cost_usdt:.2f}) below exchange minimum ($1.00)"
        return True, "Trade risk validation passed"
