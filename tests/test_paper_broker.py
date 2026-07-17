"""Paper broker 模擬成交測試。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from broker.paper import PaperBroker, Side


def test_market_order_default_ts_is_timezone_aware_utc():
    """未傳 ts 時的 fallback 必須是 tz-aware UTC（canonical schema 全 tz-aware，
    naive datetime.utcnow() 是唯一例外且已棄用，此測試鎖住修正後行為）。"""
    broker = PaperBroker(cash=100_000, fee_bps=0.0)
    before = datetime.now(timezone.utc)
    fill = broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
    after = datetime.now(timezone.utc)

    assert fill.ts.tzinfo is not None
    assert fill.ts.utcoffset() == timezone.utc.utcoffset(None)
    assert before <= fill.ts <= after


def test_buy_then_sell_pnl_and_cash():
    broker = PaperBroker(cash=100_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(1.0)
    assert pos.avg_price == pytest.approx(50_000)
    assert broker.cash == pytest.approx(50_000)

    broker.market_order("BTCUSDT", Side.SELL, quantity=1.0, price=55_000)
    assert broker.get_position("BTCUSDT").quantity == pytest.approx(0.0)
    assert broker.cash == pytest.approx(105_000)  # 賺 5000


def test_fees_are_charged():
    broker = PaperBroker(cash=100_000, fee_bps=10.0)  # 0.1%
    fill = broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
    assert fill.fee == pytest.approx(50.0)
    assert broker.cash == pytest.approx(100_000 - 50_000 - 50.0)


def test_average_price_on_add():
    broker = PaperBroker(cash=1_000_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=100.0)
    broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=200.0)
    pos = broker.get_position("BTCUSDT")
    assert pos.quantity == pytest.approx(2.0)
    assert pos.avg_price == pytest.approx(150.0)


def test_equity_marks_to_market():
    broker = PaperBroker(cash=100_000, fee_bps=0.0)
    broker.market_order("BTCUSDT", Side.BUY, quantity=1.0, price=50_000)
    eq = broker.equity({"BTCUSDT": 60_000})
    assert eq == pytest.approx(50_000 + 60_000)  # 現金 50k + 持倉市值 60k


def test_invalid_orders_raise():
    broker = PaperBroker(cash=100)
    with pytest.raises(ValueError):
        broker.market_order("BTCUSDT", Side.BUY, quantity=0, price=100)
    with pytest.raises(ValueError):
        broker.market_order("BTCUSDT", Side.BUY, quantity=1, price=0)
