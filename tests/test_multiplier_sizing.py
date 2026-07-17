"""short_risk_multiplier 校正輪（2026-07-14）單元測試。

把事前預期寫成可證偽的測試：
  - position_size 線性：未封頂時空頭 quantity ∝ m（網格 4 點手算）。
  - 規則二封頂：停損距離夠小時 quantity 跨 m 相等（0.5x 全倉安全繩）。
  - E2 property：觸發 bar 集合跨 m 不變（check_forced_liquidation 只看
    quantity 正負號，與倉位大小無關）。
  - E2 property：未封頂強平的權益損失 ∝ m ⟺ 損失/預算比率跨 m 不變。
  - runner 層：RISK_BUDGET 參數化後預設行為不變（_liq_loss_ratios wrapper
    ≡ _liq_loss_details 投影；_entry_stats 預設 ≡ 顯式 budget=0.005）。

（short_risk_multiplier 的 0<m≤1 驗證已在 test_risk_manager_short.py，不重複。）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
from risk.manager import RiskConfig, RiskManager

GRID_M = (0.25, 0.5, 0.75, 1.0)


# ===== position_size 層 =====

class TestPositionSizeMultiplierGrid:
    def test_uncapped_quantity_linear_in_m(self):
        """entry=100、stop=106（D/price=6%）：全網格未封頂，qty = 0.01m/6。"""
        qtys = {}
        for m in GRID_M:
            rm = RiskManager(
                equity=1.0, config=RiskConfig(short_risk_multiplier=m)
            )
            qtys[m] = rm.position_size(100.0, 106.0, side="short").quantity
        assert qtys[0.5] == pytest.approx(0.01 * 0.5 / 6.0)
        for m in GRID_M:
            assert qtys[m] / qtys[0.25] == pytest.approx(m / 0.25)

    def test_capped_quantity_equal_across_m(self):
        """entry=100、stop=100.4（D/price=0.4%）：全網格觸及規則二 0.5x 封頂，
        qty 一律 = 0.5×equity/price，跨 m 相等。"""
        qtys = []
        for m in GRID_M:
            rm = RiskManager(
                equity=1.0, config=RiskConfig(short_risk_multiplier=m)
            )
            qtys.append(rm.position_size(100.0, 100.4, side="short").quantity)
        for q in qtys:
            assert q == pytest.approx(0.5 / 100.0)


# ===== E2 層 property =====

def _make_canonical(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": open_,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


def _run_e2_r1(m: float):
    """合成情境：entry@100（未封頂）、close 升破 100+3×ATR=106 觸發強平。"""
    close = np.array([100.0, 100.0, 100.0, 107.0, 107.0, 107.0, 107.0])
    data = _make_canonical(close)
    signal = pd.Series([0, -1, -1, -1, -1, 0, 0])
    atr_series = pd.Series(np.full(len(close), 2.0))
    return run_event_backtest(
        data, signal, fee_bps=0.0, fill_mode="next_open", slippage_bps=0.0,
        risk=RiskManager(equity=1.0, config=RiskConfig(short_risk_multiplier=m)),
        atr_series=atr_series, sizing_mode="risk_per_trade",
    )


class TestE2MultiplierProperties:
    def test_trigger_bars_invariant_across_m(self):
        bars = {m: _run_e2_r1(m).liquidation_bars for m in GRID_M}
        assert bars[0.5] == [3]  # 107 ≥ 100 + 3×2，測試要有料
        for m in GRID_M:
            assert bars[m] == bars[0.5]

    def test_uncapped_liq_loss_linear_in_m(self):
        """未封頂 ⇒ 損失 ∝ m ⇒ 損失/預算比率跨 m 不變。
        手算：qty=0.01m/6、損失=qty×(107−100)，final=1−損失。"""
        finals = {m: float(_run_e2_r1(m).equity_curve.iloc[-1]) for m in GRID_M}
        loss_half = 1.0 - finals[0.5]
        assert loss_half == pytest.approx(0.01 * 0.5 / 6.0 * 7.0)
        for m in GRID_M:
            assert (1.0 - finals[m]) / loss_half == pytest.approx(m / 0.5)


# ===== runner 層：RISK_BUDGET 參數化回歸 =====

class TestRunnerBudgetParam:
    def test_ratio_wrapper_and_details_agree(self):
        from run_rule1_sizing import RISK_BUDGET, _liq_loss_details, _liq_loss_ratios

        ev = _run_e2_r1(0.5)
        ts_arr = list(
            pd.date_range("2026-01-01", periods=7, freq="h", tz="UTC")
        )
        details = _liq_loss_details(ev, ts_arr, budget=RISK_BUDGET)
        ratios_default = _liq_loss_ratios(ev, ts_arr)
        assert ratios_default == [r for r, _ in details]
        # 手算：loss/(0.5%×e_base=1.0) = (0.005/6×7)/0.005 = 7/6
        assert len(details) == 1
        ratio, capped = details[0]
        assert ratio == pytest.approx(7.0 / 6.0)
        assert capped is False  # 0.5%×100/6 = 0.083 ≤ 0.5，未封頂

    def test_details_budget_scales_denominator_only(self):
        from run_rule1_sizing import _liq_loss_details

        ev = _run_e2_r1(0.5)
        ts_arr = list(
            pd.date_range("2026-01-01", periods=7, freq="h", tz="UTC")
        )
        r_half = _liq_loss_details(ev, ts_arr, budget=0.005)[0][0]
        r_full = _liq_loss_details(ev, ts_arr, budget=0.01)[0][0]
        assert r_full == pytest.approx(r_half / 2.0)

    def test_entry_stats_default_unchanged(self):
        from run_rule1_sizing import _entry_stats

        ev = _run_e2_r1(0.5)
        open_arr = np.array([100.0, 100, 100, 100, 107, 107, 107])
        assert _entry_stats(ev, open_arr) == _entry_stats(ev, open_arr, budget=0.005)
