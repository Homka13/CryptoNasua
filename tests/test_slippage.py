import pytest

from exchange_service import calculate_vwap_slippage
from config import config


def test_vwap_within_slippage_limit():
    # Tight order book: buying 100 units at prices 100.0, 100.1, 100.15.
    levels = [(100.0, 50.0), (100.1, 50.0), (100.15, 50.0)]
    vwap, best, slippage = calculate_vwap_slippage(levels, 100.0)
    # 50@100 + 50@100.1 = 100 units -> vwap = 100.05
    assert vwap == pytest.approx(100.05)
    assert best == pytest.approx(100.0)
    assert slippage == pytest.approx(0.0005)
    assert slippage <= config.max_slippage_pct  # 0.05% <= 0.20%


def test_vwap_rejects_over_limit_slippage():
    # Wide order book: buy volume spans far beyond best price.
    levels = [(100.0, 10.0), (101.5, 100.0)]
    vwap, best, slippage = calculate_vwap_slippage(levels, 100.0)
    # 10@100 + 90@101.5 = 9135 + 9135? -> total = 10*100 + 90*101.5 = 1000 + 9135 = 10135
    assert vwap == pytest.approx(101.35)
    assert slippage == pytest.approx(0.0135)
    assert slippage > config.max_slippage_pct  # 1.35% > 0.20%


def test_vwap_flat_book_zero_slippage():
    levels = [(100.0, 1000.0)]
    vwap, best, slippage = calculate_vwap_slippage(levels, 10.0)
    assert vwap == pytest.approx(100.0)
    assert slippage == pytest.approx(0.0)


def test_vwap_depth_smaller_than_amount_fills_at_last_price():
    levels = [(100.0, 10.0)]
    vwap, best, slippage = calculate_vwap_slippage(levels, 20.0)
    # 10 @ 100 + remaining 10 @ last price 100 -> vwap 100
    assert vwap == pytest.approx(100.0)
    assert slippage == pytest.approx(0.0)


def test_vwap_empty_levels_raises():
    with pytest.raises(ValueError):
        calculate_vwap_slippage([], 10.0)


def test_sell_side_bids_reference_frame():
    # For a sell, levels are bids (best = highest bid). Symmetric to asks logic.
    levels = [(99.9, 50.0), (99.8, 50.0)]
    vwap, best, slippage = calculate_vwap_slippage(levels, 100.0)
    # 50@99.9 + 50@99.8 = 9985 / 100 = 99.85
    assert vwap == pytest.approx(99.85)
    assert best == pytest.approx(99.9)
    assert slippage == pytest.approx(abs(99.85 - 99.9) / 99.9)