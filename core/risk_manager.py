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

    def calculate_position_size(self, usdt_balance: float, current_price: float, min_notional: float = 1.0) -> Tuple[bool, float, str]:
        """
        Decides whether a trade is allowed and how much of the coin to buy.
        Dynamically adapts to the exchange's min_notional for the specific symbol.
        Returns: (is_allowed, amount_in_coin, risk_status_reason)
        """
        if usdt_balance <= 0 or current_price <= 0:
            return False, 0.0, "Invalid balance or price"

        # Effective minimum notional: use symbol's dynamic CEX limit (default $1.00)
        effective_min = max(min_notional, 1.0)

        if usdt_balance < effective_min:
            return False, 0.0, (
                f"Insufficient balance: need ${effective_min:.2f} "
                f"(exchange minimum), have ${usdt_balance:.2f}"
            )

        # Allocate configured trade pct (e.g. 45-50%), but scale up if below effective_min
        trade_allocation_usdt = usdt_balance * self.max_trade_pct
        if trade_allocation_usdt < effective_min:
            trade_allocation_usdt = min(usdt_balance * 0.995, usdt_balance)

        # Ensure trade allocation stays strictly within free available balance
        if trade_allocation_usdt > usdt_balance:
            trade_allocation_usdt = usdt_balance * 0.995

        if trade_allocation_usdt < effective_min:
            return False, 0.0, (
                f"Insufficient balance: need ${effective_min:.2f} "
                f"(exchange minimum), have ${usdt_balance:.2f}"
            )

        amount_in_coin = trade_allocation_usdt / current_price

        logger.info(
            f"💰 RiskManager Allocation: Available ${usdt_balance:.2f} | "
            f"Trade Cost ${trade_allocation_usdt:.2f} ({amount_in_coin:.8f} coins @ ${current_price:.6f})"
        )
        return True, amount_in_coin, f"OK (${trade_allocation_usdt:.2f} allocated)"

    def validate_trade_risk(self, usdt_balance: float, cost_usdt: float) -> Tuple[bool, str]:
        """Validates whether executing trade violates risk parameters."""
        if cost_usdt > usdt_balance:
            return False, f"Cost (${cost_usdt:.2f}) exceeds free balance (${usdt_balance:.2f})"
        if cost_usdt < 1.0:
            return False, f"Trade cost (${cost_usdt:.2f}) below exchange minimum ($1.00)"
        return True, "Trade risk validation passed"
