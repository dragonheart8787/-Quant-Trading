"""空頭風控規則單元測試（規則一、二、三）。

規則一：空頭單筆風險比例（short_risk_multiplier）
規則二：多空槓桿上限分開設定（max_long_leverage / max_short_leverage）+ 多空互斥
規則三：空頭強制平倉 check_forced_liquidation（ATR動態防線 + 15%固定安全網）
       獨立於 is_circuit_broken / 日內熔斷機制之外。
"""

from __future__ import annotations

from datetime import date

import pytest

from risk.manager import OrderDecision, PositionSizing, RiskConfig, RiskManager


# ===== 規則一：空頭單筆風險比例 =====

def test_risk_per_trade_short_property_default():
    """預設 short_risk_multiplier=0.5：空頭風險 = 多頭風險 * 0.5。"""
    cfg = RiskConfig(risk_per_trade=0.01)
    assert cfg.risk_per_trade_short == pytest.approx(0.005)


def test_risk_per_trade_short_custom_multiplier():
    cfg = RiskConfig(risk_per_trade=0.02, short_risk_multiplier=0.25)
    assert cfg.risk_per_trade_short == pytest.approx(0.005)


def test_position_size_short_uses_lower_risk():
    """空頭 position_size 使用 risk_per_trade_short，數量應比多頭小。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(risk_per_trade=0.01, max_leverage=10))
    long_sizing = rm.position_size(entry_price=50_000, stop_price=49_000, side="long")
    short_sizing = rm.position_size(entry_price=50_000, stop_price=51_000, side="short")
    # 空頭風險 = 多頭 * 0.5 → 數量也是 0.5 倍（在槓桿未觸頂時）
    assert short_sizing.quantity == pytest.approx(long_sizing.quantity * 0.5)


def test_position_size_short_capped_by_short_leverage():
    """空頭倉位名目金額不超過 equity * max_short_leverage。"""
    cfg = RiskConfig(risk_per_trade=0.01, max_leverage=2.0)
    rm = RiskManager(equity=10_000, config=cfg)
    # max_short_leverage = 2.0 * 0.5 = 1.0 → 名目上限 10000
    sizing = rm.position_size(entry_price=100.0, stop_price=101.0, side="short")
    assert sizing.notional <= 10_000.0 + 1e-9


def test_invalid_short_risk_multiplier_raises():
    with pytest.raises(ValueError):
        RiskConfig(short_risk_multiplier=0.0)
    with pytest.raises(ValueError):
        RiskConfig(short_risk_multiplier=1.5)


# ===== 規則二：多空槓桿上限分開 =====

def test_max_short_leverage_is_half_of_long():
    """max_short_leverage 預設 = max_long_leverage * 0.5。"""
    cfg = RiskConfig(max_leverage=2.0)
    assert cfg.max_short_leverage == pytest.approx(1.0)


def test_max_long_leverage_explicit_overrides_max_leverage():
    """明確設定 max_long_leverage 時，不受 max_leverage 影響。"""
    cfg = RiskConfig(max_leverage=1.0, max_long_leverage=3.0)
    assert cfg.max_long_leverage == pytest.approx(3.0)
    assert cfg.max_short_leverage == pytest.approx(1.5)


def test_approve_order_long_uses_long_leverage():
    """多頭下單使用 max_long_leverage 檢查。"""
    cfg = RiskConfig(max_long_leverage=2.0)
    rm = RiskManager(equity=10_000, config=cfg)
    d = date(2026, 6, 25)
    # notional = 1 * 15000 = 15000 < 10000 * 2.0 = 20000 → 通過
    assert rm.approve_order(1.0, 15_000, d, side="long") is OrderDecision.APPROVED


def test_approve_order_short_uses_short_leverage():
    """空頭下單使用 max_short_leverage（= max_long_leverage * 0.5）檢查。"""
    cfg = RiskConfig(max_long_leverage=2.0)  # max_short_leverage = 1.0
    rm = RiskManager(equity=10_000, config=cfg)
    d = date(2026, 6, 25)
    # notional = 1 * 15000 = 15000 > 10000 * 1.0 = 10000 → 拒絕
    assert rm.approve_order(1.0, 15_000, d, side="short") is OrderDecision.REJECTED_LEVERAGE
    # notional = 0.5 * 15000 = 7500 < 10000 → 通過
    assert rm.approve_order(0.5, 15_000, d, side="short") is OrderDecision.APPROVED


def test_approve_order_rejects_long_when_short_exists():
    """已有空頭倉位時，不得開多頭（多空互斥）。"""
    rm = RiskManager(equity=100_000)
    d = date(2026, 6, 25)
    # current_position_qty=-1 表示目前有空單
    decision = rm.approve_order(
        quantity=1.0, price=50_000, today=d,
        side="long", current_position_qty=-1.0,
    )
    assert decision is OrderDecision.REJECTED_CONFLICTING_POSITION


def test_approve_order_rejects_short_when_long_exists():
    """已有多頭倉位時，不得開空頭（多空互斥）。"""
    rm = RiskManager(equity=100_000)
    d = date(2026, 6, 25)
    decision = rm.approve_order(
        quantity=1.0, price=50_000, today=d,
        side="short", current_position_qty=1.0,
    )
    assert decision is OrderDecision.REJECTED_CONFLICTING_POSITION


def test_approve_order_no_conflict_when_flat():
    """倉位為 0 時，多空都可開。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(max_leverage=2.0))
    d = date(2026, 6, 25)
    assert rm.approve_order(0.1, 50_000, d, side="long", current_position_qty=0.0) is OrderDecision.APPROVED
    assert rm.approve_order(0.1, 50_000, d, side="short", current_position_qty=0.0) is OrderDecision.APPROVED


def test_approve_order_backward_compat_no_side():
    """不傳 side 時（向後相容）預設為 long，不影響現有行為。"""
    rm = RiskManager(equity=10_000, config=RiskConfig(max_leverage=1.0))
    d = date(2026, 6, 25)
    # notional = 2 * 10000 = 20000 > 10000 * 1.0 → 拒絕
    assert rm.approve_order(quantity=2.0, price=10_000, today=d) is OrderDecision.REJECTED_LEVERAGE
    # notional = 0.5 * 10000 = 5000 < 10000 → 通過
    assert rm.approve_order(quantity=0.5, price=10_000, today=d) is OrderDecision.APPROVED


# ===== 規則三：空頭強制平倉 =====

def test_forced_liq_not_triggered_within_bounds():
    """在 ATR 動態防線與 15% 安全網以內，不觸發強制平倉。"""
    rm = RiskManager(equity=100_000)
    # avg_price=50000, ATR=1000, N=3 → atr_trigger=53000
    # fixed_trigger = 50000 * 1.15 = 57500
    # current_price=52000 < 53000 → 不觸發
    assert not rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=52_000,
        atr_value=1_000,
    )


def test_forced_liq_atr_trigger():
    """ATR 動態防線：current_price >= avg_price + 3 * ATR → 觸發。"""
    rm = RiskManager(equity=100_000)
    # atr_trigger = 50000 + 3*1000 = 53000；current=53000 剛好觸發
    assert rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=53_000,
        atr_value=1_000,
    )


def test_forced_liq_atr_trigger_just_below():
    """剛好在 ATR 防線下方（53000 - epsilon）→ 不觸發。"""
    rm = RiskManager(equity=100_000)
    assert not rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=52_999.99,
        atr_value=1_000,
    )


def test_forced_liq_fixed_15pct_trigger():
    """固定安全網：虧損達 15% 觸發，即使 ATR 防線更寬鬆。"""
    rm = RiskManager(equity=100_000)
    # 巨大 ATR=10000 → atr_trigger=80000；fixed_trigger = 50000 * 1.15 = 57500
    # current_price=57500 剛好觸發固定安全網
    assert rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=57_500,
        atr_value=10_000,
    )


def test_forced_liq_fixed_15pct_just_below():
    """剛好在 15% 安全網下方 → 不觸發。"""
    rm = RiskManager(equity=100_000)
    assert not rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=57_499.99,
        atr_value=10_000,
    )


def test_forced_liq_takes_stricter_of_two():
    """ATR 防線比固定 15% 更嚴格時（ATR 很小），ATR 先觸發。"""
    rm = RiskManager(equity=100_000)
    # avg=50000, ATR=500, N=3 → atr_trigger=51500（比 57500 嚴格）
    # current=52000 超過 atr_trigger=51500 但未超過 fixed 57500 → 仍觸發
    assert rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=52_000,
        atr_value=500,
    )


def test_forced_liq_only_for_short_not_long():
    """強制平倉只對空頭（position_quantity < 0）生效，多頭倉位不觸發。"""
    rm = RiskManager(equity=100_000)
    # 即使 current_price 遠超 avg_price，多頭倉位也不觸發
    assert not rm.check_forced_liquidation(
        position_quantity=1.0,   # 多頭
        avg_price=50_000,
        current_price=100_000,  # 翻倍虧損（對空頭而言）
        atr_value=1_000,
    )


def test_forced_liq_flat_position_not_triggered():
    """平倉狀態（quantity=0）不觸發強制平倉。"""
    rm = RiskManager(equity=100_000)
    assert not rm.check_forced_liquidation(
        position_quantity=0.0,
        avg_price=50_000,
        current_price=100_000,
        atr_value=1_000,
    )


# ===== 規則三參數化：forced_liq_atr_n / forced_liq_safety_pct（ATR 校正輪，2026-07-13）=====

def test_forced_liq_config_defaults():
    """新欄位預設值 = 現行常數（N=3.0、安全網 15%）。"""
    cfg = RiskConfig()
    assert cfg.forced_liq_atr_n == pytest.approx(3.0)
    assert cfg.forced_liq_safety_pct == pytest.approx(0.15)


def test_forced_liq_custom_atr_n_tightens_line():
    """N=1.5 時 ATR 防線收緊：avg+1.5*ATR 即觸發（預設 N=3 下不觸發的價位）。"""
    rm = RiskManager(
        equity=100_000, config=RiskConfig(forced_liq_atr_n=1.5)
    )
    # avg=50000, ATR=1000 → N=1.5 防線 51500；current=51500 觸發
    assert rm.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=51_500, atr_value=1_000,
    )
    # 同價位在預設 N=3（防線 53000）下不觸發（對照）
    rm_default = RiskManager(equity=100_000)
    assert not rm_default.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=51_500, atr_value=1_000,
    )


def test_forced_liq_custom_atr_n_loosens_line():
    """N=4 時 ATR 防線放寬：avg+3*ATR 不再觸發、avg+4*ATR 才觸發。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(forced_liq_atr_n=4.0))
    assert not rm.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=53_000, atr_value=1_000,  # N=3 防線價位
    )
    assert rm.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=54_000, atr_value=1_000,  # N=4 防線價位
    )


def test_forced_liq_custom_safety_pct():
    """safety_pct=0.10 時固定安全網收緊到 10%（ATR 巨大使 ATR 線不生效）。"""
    rm = RiskManager(
        equity=100_000, config=RiskConfig(forced_liq_safety_pct=0.10)
    )
    assert rm.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=55_000, atr_value=10_000,  # +10%，預設 15% 下不觸發
    )
    rm_default = RiskManager(equity=100_000)
    assert not rm_default.check_forced_liquidation(
        position_quantity=-1.0, avg_price=50_000,
        current_price=55_000, atr_value=10_000,
    )


def test_forced_liq_default_config_regression_sweep():
    """保護性回歸：預設 config 下與舊常數公式（avg+3*ATR / +15%）逐點一致。"""
    rm = RiskManager(equity=100_000)
    avg, atr_val = 50_000.0, 800.0
    for price in [50_000, 51_000, 52_399.99, 52_400, 53_000,
                  57_499.99, 57_500, 60_000]:
        expected = (price >= avg + 3.0 * atr_val) or ((price - avg) / avg >= 0.15)
        assert rm.check_forced_liquidation(-1.0, avg, price, atr_val) == expected, price


def test_forced_liq_invalid_params_raise():
    """forced_liq_atr_n 必須為正；forced_liq_safety_pct 必須介於 0 與 1。"""
    with pytest.raises(ValueError):
        RiskConfig(forced_liq_atr_n=0.0)
    with pytest.raises(ValueError):
        RiskConfig(forced_liq_atr_n=-1.0)
    with pytest.raises(ValueError):
        RiskConfig(forced_liq_safety_pct=0.0)
    with pytest.raises(ValueError):
        RiskConfig(forced_liq_safety_pct=1.0)


def test_forced_liq_independent_of_circuit_breaker():
    """強制平倉與日內熔斷獨立：熔斷後 check_forced_liquidation 仍可獨立判斷。"""
    rm = RiskManager(equity=100_000, config=RiskConfig(max_daily_loss_pct=0.03))
    d = date(2026, 6, 25)
    rm.update_equity(96_000, d)  # 觸發日內熔斷
    assert rm.is_circuit_broken

    # 熔斷狀態下，空頭強制平倉仍可觸發（兩者獨立）
    assert rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=58_000,  # 超過 15% 固定安全網
        atr_value=1_000,
    )

    # 熔斷狀態下，不滿足平倉條件的空單也不應被誤觸發
    assert not rm.check_forced_liquidation(
        position_quantity=-1.0,
        avg_price=50_000,
        current_price=51_000,  # 未超過任何防線
        atr_value=1_000,
    )
