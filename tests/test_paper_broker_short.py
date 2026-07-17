"""Paper broker 空頭倉位邊界案例測試。

驗證 broker/paper.py 的 Position.quantity 負值（空頭）結構性支援：
均價計算（開空、加碼、部分回補、翻倉）及 equity() 盈虧方向。

結論：paper.py 邏輯已正確支援空頭，不需修改。
"""

from __future__ import annotations

import pytest

from broker.paper import PaperBroker, Side


def test_short_open_avg_price():
    """開空單：0 → 負，均價等於進場價。"""
    broker = PaperBroker(cash=100_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=50_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(-1.0)
    assert pos.avg_price == pytest.approx(50_000.0)


def test_short_add_avg_price():
    """空單加碼：加權平均價。"""
    broker = PaperBroker(cash=500_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=50_000)
    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=40_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(-2.0)
    assert pos.avg_price == pytest.approx(45_000.0)  # (50000*1 + 40000*1) / 2


def test_short_partial_cover_avg_unchanged():
    """空單部分回補（減倉）：均價維持不變。"""
    broker = PaperBroker(cash=500_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.SELL, quantity=2.0, price=50_000)
    broker.market_order("BTCUSDT", Side.BUY, quantity=0.5, price=45_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(-1.5)
    assert pos.avg_price == pytest.approx(50_000.0)  # 部分回補均價不變


def test_short_to_long_flip_avg_becomes_new_price():
    """空翻多：均價改為新成交價。"""
    broker = PaperBroker(cash=500_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=50_000)
    broker.market_order("BTCUSDT", Side.BUY, quantity=3.0, price=45_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(2.0)        # -1 + 3 = +2
    assert pos.avg_price == pytest.approx(45_000.0)  # 翻倉後均價 = 新成交價


def test_long_to_short_flip_avg_becomes_new_price():
    """多翻空：均價改為新成交價。"""
    broker = PaperBroker(cash=500_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.BUY, quantity=2.0, price=50_000)
    broker.market_order("BTCUSDT", Side.SELL, quantity=3.0, price=55_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(-1.0)       # 2 - 3 = -1
    assert pos.avg_price == pytest.approx(55_000.0)  # 翻倉後均價 = 新成交價


def test_short_equity_direction():
    """持有空單時 equity() 盈虧方向正確：價漲 = 虧損，價跌 = 獲利。"""
    broker = PaperBroker(cash=100_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=50_000)
    # cash = 100000 + 50000 = 150000；pos.quantity = -1
    eq_entry = broker.equity({"BTCUSDT": 50_000})
    eq_up = broker.equity({"BTCUSDT": 55_000})    # 價漲 5000 → 虧損 5000
    eq_down = broker.equity({"BTCUSDT": 45_000})  # 價跌 5000 → 獲利 5000
    assert eq_entry == pytest.approx(100_000.0)
    assert eq_up == pytest.approx(95_000.0)
    assert eq_down == pytest.approx(105_000.0)
