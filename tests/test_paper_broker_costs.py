"""PaperBroker 買賣分離成本（costs）測試（台股 E2 化第一輪，範圍性解鎖）。

重點：
- **保護性回歸（本檔最重要）**：`costs=None` 與舊 `fee_bps` 對稱路徑逐位相同
  ——比照 `backtest/costs.py` 對 vector_engine 的既有保護性回歸紀律，套用到
  broker 這一層。
- 非對稱費率落在正確方向：BUY 收 `buy_bps`、SELL 收 `sell_bps`。
- `costs` 給定時完全接管費率計算，`fee_bps` 值被忽略（即使給誇張值也不影響）。
"""

from __future__ import annotations

import pytest

from backtest.costs import TradeCosts
from broker.paper import PaperBroker, Side


def test_costs_none_bitwise_identical_to_fee_bps():
    """★保護性回歸★ costs=None 時，行為與舊 fee_bps 對稱路徑逐位相同。"""
    legacy = PaperBroker(cash=100_000, fee_bps=10.0)
    via_costs = PaperBroker(cash=100_000, fee_bps=10.0, costs=None)

    for broker in (legacy, via_costs):
        broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
        broker.market_order("BTCUSDT", Side.SELL, quantity=0.5, price=55_000)

    assert legacy.cash == pytest.approx(via_costs.cash)
    assert [f.fee for f in legacy.fills] == [pytest.approx(f.fee) for f in via_costs.fills]


def test_symmetric_costs_bitwise_identical_to_equal_fee_bps():
    """TradeCosts(x, x)（對稱）與 fee_bps=x 逐位相同，證明 costs 路徑本身正確。"""
    legacy = PaperBroker(cash=100_000, fee_bps=10.0)
    via_costs = PaperBroker(cash=100_000, costs=TradeCosts(buy_bps=10.0, sell_bps=10.0))

    for broker in (legacy, via_costs):
        broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
        broker.market_order("BTCUSDT", Side.SELL, quantity=0.5, price=55_000)

    assert legacy.cash == pytest.approx(via_costs.cash)
    assert [f.fee for f in legacy.fills] == [pytest.approx(f.fee) for f in via_costs.fills]


def test_asymmetric_costs_charge_correct_side():
    """台股費率：買 14.25bps、賣 44.25bps（含證交稅）——BUY/SELL 各收對應費率。"""
    broker = PaperBroker(cash=1_000_000, costs=TradeCosts(buy_bps=14.25, sell_bps=44.25))

    buy_fill = broker.market_order("2330", Side.BUY, quantity=10.0, price=1000.0)
    assert buy_fill.fee == pytest.approx(10.0 * 1000.0 * 14.25 / 10_000.0)

    sell_fill = broker.market_order("2330", Side.SELL, quantity=10.0, price=1000.0)
    assert sell_fill.fee == pytest.approx(10.0 * 1000.0 * 44.25 / 10_000.0)


def test_costs_overrides_fee_bps_when_both_given():
    """costs 給定時完全接管費率，fee_bps 的值（即使刻意給誇張值）不影響結果。"""
    broker = PaperBroker(cash=1_000_000, fee_bps=9_999.0, costs=TradeCosts(buy_bps=5.0, sell_bps=5.0))
    fill = broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
    assert fill.fee == pytest.approx(50_000 * 5.0 / 10_000.0)
