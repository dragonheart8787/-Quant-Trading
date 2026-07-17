"""風控模組單元測試（硬性規範：熔斷、槓桿、倉位邏輯必須有測試）。"""

from __future__ import annotations

from datetime import date

import pytest

from risk.manager import OrderDecision, PositionSizing, RiskConfig, RiskManager


# ---------- 倉位計算 ----------

def test_fixed_fractional_position_size():
    """1% 風險、停損距離 1000 → quantity = (100000*0.01)/1000 = 1.0。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(risk_per_trade=0.01, max_leverage=10))
    sizing = rm.position_size(entry_price=50_000, stop_price=49_000)
    assert isinstance(sizing, PositionSizing)
    assert sizing.quantity == pytest.approx(1.0)
    assert sizing.risk_amount == pytest.approx(1000.0)
    assert sizing.notional == pytest.approx(50_000.0)


def test_position_size_capped_by_leverage():
    """名目金額不得超過 equity * max_leverage；超過要縮量。"""
    # 停損很近 → 未受限數量會很大，名目遠超 1x 槓桿
    rm = RiskManager(equity=10_000, config=RiskConfig(risk_per_trade=0.02, max_leverage=1.0))
    sizing = rm.position_size(entry_price=100.0, stop_price=99.9)
    assert sizing.notional == pytest.approx(10_000.0)  # 被 1x 槓桿頂住
    assert sizing.quantity == pytest.approx(100.0)


def test_position_size_zero_risk_raises():
    rm = RiskManager(equity=10_000)
    with pytest.raises(ValueError):
        rm.position_size(entry_price=100.0, stop_price=100.0)


# ---------- 日內熔斷 ----------

def test_circuit_breaker_trips_on_daily_loss():
    """當日虧損達門檻 → 熔斷觸發，後續下單一律拒絕。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(max_daily_loss_pct=0.03))
    d = date(2026, 6, 22)
    assert not rm.is_circuit_broken
    # 虧 3000 = 3%，剛好觸發
    rm.update_equity(97_000, d)
    assert rm.is_circuit_broken
    decision = rm.approve_order(quantity=1.0, price=50_000, today=d)
    assert decision is OrderDecision.REJECTED_CIRCUIT_BREAKER


def test_circuit_breaker_not_tripped_above_threshold():
    rm = RiskManager(equity=100_000, config=RiskConfig(max_daily_loss_pct=0.03))
    d = date(2026, 6, 22)
    rm.update_equity(98_000, d)  # 只虧 2%
    assert not rm.is_circuit_broken
    assert rm.approve_order(quantity=0.01, price=50_000, today=d) is OrderDecision.APPROVED


def test_circuit_breaker_resets_next_day():
    """跨日後熔斷解除，當日基準淨值重設。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(max_daily_loss_pct=0.03))
    d1 = date(2026, 6, 22)
    rm.update_equity(96_000, d1)
    assert rm.is_circuit_broken
    # 隔日
    d2 = date(2026, 6, 23)
    rm.update_equity(96_000, d2)  # 與前一日相同淨值，但今天還沒虧
    assert not rm.is_circuit_broken
    assert rm.approve_order(quantity=0.001, price=50_000, today=d2) is OrderDecision.APPROVED


def test_circuit_breaker_intraday_drop_after_reset():
    """跨日重設後，當日再次大跌應重新觸發熔斷。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(max_daily_loss_pct=0.03))
    d2 = date(2026, 6, 23)
    rm.update_equity(100_000, d2)   # 開盤基準
    rm.update_equity(96_500, d2)    # 當日虧 3.5%
    assert rm.is_circuit_broken


# ---------- 下單核可 ----------

def test_approve_order_rejects_leverage_breach():
    rm = RiskManager(equity=10_000, config=RiskConfig(max_leverage=1.0))
    d = date(2026, 6, 22)
    # 名目 = 1 * 20000 = 20000 > 10000*1 → 拒絕
    assert rm.approve_order(quantity=1.0, price=20_000, today=d) is OrderDecision.REJECTED_LEVERAGE


def test_approve_order_rejects_invalid():
    rm = RiskManager(equity=10_000)
    d = date(2026, 6, 22)
    assert rm.approve_order(quantity=0, price=100, today=d) is OrderDecision.REJECTED_INVALID
    assert rm.approve_order(quantity=1, price=0, today=d) is OrderDecision.REJECTED_INVALID


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade=0)
    with pytest.raises(ValueError):
        RiskConfig(max_daily_loss_pct=1.5)
    with pytest.raises(ValueError):
        RiskConfig(max_leverage=0)


def test_invalid_equity_raises():
    with pytest.raises(ValueError):
        RiskManager(equity=0)
