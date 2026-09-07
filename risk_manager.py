import logging
from dataclasses import dataclass
from typing import Tuple, Optional

from config import config

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """Outcome of a position-sizing evaluation."""

    is_allowed: bool
    amount: float
    reason: str


class RiskManager:
    """Protects small capital by enforcing order size, min value, and daily loss limits."""

    def __init__(self) -> None:
        self.max_daily_loss: float = config.max_daily_loss_pct
        self.trade_size_usdt: float = config.trade_size_usdt
        self.min_order_usdt: float = config.min_order_usdt
        self.daily_start_capital: float = config.initial_capital
        self.current_drawdown: float = 0.0
        self._peak_price: Optional[float] = None
        self._hard_stop_triggered: bool = False

    def calculate_position_size(
        self, usdt_free: float, current_price: float
    ) -> PositionSizeResult:
        """Compute the buy quantity in coins given free USDT and current price."""
        if current_price <= 0:
            return PositionSizeResult(False, 0.0, f"Invalid price (${current_price:.2f})")

        if usdt_free < self.min_order_usdt:
            return PositionSizeResult(
                False,
                0.0,
                f"Insufficient USDT balance (${usdt_free:.2f} < ${self.min_order_usdt:.2f} min)",
            )

        alloc_usdt = min(self.trade_size_usdt, usdt_free)

        if alloc_usdt < self.min_order_usdt:
            return PositionSizeResult(
                False,
                0.0,
                f"Order size (${alloc_usdt:.2f}) below Bybit minimum (${self.min_order_usdt:.2f})",
            )

        amount = alloc_usdt / current_price
        return PositionSizeResult(
            True,
            amount,
            f"Order size: ${alloc_usdt:.2f} ({amount:.4f} coins @ ${current_price:.2f})",
        )

    def check_daily_drawdown(self, current_capital: float) -> bool:
        """Return True when cumulative loss has hit the daily circuit breaker (10%)."""
        if self.daily_start_capital <= 0:
            return False

        loss_pct = (self.daily_start_capital - current_capital) / self.daily_start_capital
        self.current_drawdown = loss_pct

        if loss_pct >= self.max_daily_loss:
            logger.warning(
                f"🚨 MAX DAILY DRAWDOWN REACHED: {loss_pct*100:.2f}% loss. Trading paused."
            )
            return True
        return False

    def check_stop_loss(
        self, entry_price: float, current_price: float
    ) -> Tuple[bool, str]:
        """Hard stop-loss strictly at -2.0% (never movable towards increased loss)."""
        if entry_price <= 0:
            return True, "Invalid entry price"

        loss_pct = (current_price - entry_price) / entry_price

        if loss_pct <= -config.stop_loss_pct:
            self._hard_stop_triggered = True
            return True, f"🛑 HARD STOP-LOSS hit ({loss_pct*100:.2f}% <= -{config.stop_loss_pct*100:.1f}%)"
        return False, ""

    def evaluate_trailing_stop(
        self, entry_price: float, current_price: float
    ) -> Tuple[bool, str]:
        """Trailing stop: activates at +2.5% gain, trails 1.5% below the running peak.

        Returns ``(should_sell, reason)``. The stop only ever moves up (tightens),
        never down, so losses are never increased by the trailing mechanism.

        Once a peak that satisfies the +2.5% activation threshold is observed, the
        trailing stop remains armed even if the current gain later falls below that
        threshold (the peak and its floor persist until the stop fires or the
        position state is reset).
        """
        if entry_price <= 0:
            return False, ""

        gain_pct = (current_price - entry_price) / entry_price

        # Update peak if we've crossed the activation threshold or already armed.
        if self._peak_price is None or current_price > self._peak_price:
            if gain_pct >= config.trailing_stop_activation_pct or self._peak_price is not None:
                self._peak_price = current_price

        # Not yet armed (no peak above activation threshold has been captured).
        if self._peak_price is None:
            return False, ""

        trailing_floor = self._peak_price * (1.0 - config.trailing_stop_distance_pct)

        if current_price <= trailing_floor:
            reason = (
                f"🎯 TRAILING STOP triggered ({gain_pct*100:.2f}% gain, "
                f"stopped {config.trailing_stop_distance_pct*100:.1f}% below peak ${self._peak_price:.6f})"
            )
            return True, reason

        return False, ""

    def reset_stop_state(self) -> None:
        """Reset per-position stop state before each new position is opened."""
        self._peak_price = None
        self._hard_stop_triggered = False