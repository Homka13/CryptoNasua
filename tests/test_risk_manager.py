import pytest

from risk_manager import RiskManager


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def _rm_with_capital(capital: float) -> RiskManager:
    r = RiskManager()
    r.daily_start_capital = capital
    return r


def test_position_size_for_10_usdt(rm):
    result = rm.calculate_position_size(10.0, 2.0)
    assert result.is_allowed is True
    # trade_size_usdt default 2.5 -> 2.5 / 2.0 = 1.25 coins
    assert result.amount == pytest.approx(1.25)


def test_position_size_for_5_usdt(rm):
    result = rm.calculate_position_size(5.0, 2.0)
    assert result.is_allowed is True
    assert result.amount == pytest.approx(1.25)


def test_position_size_insufficient_balance(rm):
    result = rm.calculate_position_size(0.50, 2.0)
    assert result.is_allowed is False
    assert result.amount == 0.0
    assert "Insufficient" in result.reason


def test_bybit_minimum_order_enforced(rm):
    # balance slightly above min but trade_size below min scenario handled by alloc.
    result = rm.calculate_position_size(1.2, 100.0)
    # alloc = min(trade_size 2.5, 1.2) = 1.2 >= 1.0 min -> allowed, tiny amount
    assert result.is_allowed is True
    assert result.amount == pytest.approx(1.2 / 100.0)


def test_invalid_price_rejected(rm):
    result = rm.calculate_position_size(10.0, 0.0)
    assert result.is_allowed is False


def test_daily_drawdown_trigger(rm):
    r = _rm_with_capital(10.0)
    # 10% loss boundary: capital 9.0 -> exactly 10% -> triggers
    assert r.check_daily_drawdown(9.0) is True
    # below boundary does not trigger
    r2 = _rm_with_capital(10.0)
    assert r2.check_daily_drawdown(9.5) is False


def test_hard_stop_loss_triggered(rm):
    hit, reason = rm.check_stop_loss(100.0, 97.5)
    assert hit is True
    assert "STOP-LOSS" in reason


def test_hard_stop_loss_not_triggered(rm):
    hit, _ = rm.check_stop_loss(100.0, 99.0)
    assert hit is False


def test_trailing_stop_activates_and_trails(rm):
    rm.reset_stop_state()
    # Not activated below +2.5%.
    hit, _ = rm.evaluate_trailing_stop(100.0, 102.0)
    assert hit is False
    # Activate at +3%.
    hit, _ = rm.evaluate_trailing_stop(100.0, 103.0)
    assert hit is False  # peak updated to 103, not yet 1.5% below
    # Drop below peak - 1.5% (103 * 0.985 = 101.455).
    hit, reason = rm.evaluate_trailing_stop(100.0, 101.0)
    assert hit is True
    assert "TRAILING STOP" in reason


def test_trailing_stop_never_moves_down(rm):
    """Trailing floor only ever rises with the peak (loss not increased)."""
    rm.reset_stop_state()
    rm.evaluate_trailing_stop(100.0, 103.0)  # peak = 103
    floor_after_peak = rm._peak_price * (1.0 - 0.015)
    # A lower later peak should not widen the stop (peak stays at 103).
    rm.evaluate_trailing_stop(100.0, 102.0)
    assert rm._peak_price == 103.0
    assert floor_after_peak == pytest.approx(101.455)