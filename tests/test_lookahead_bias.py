"""自動化 lookahead bias 檢查。

可重複執行（每次 pytest 都跑），而非手動驗證一次。

原理：對「策略剛好能預測下一根方向」的資料，
  - shift_signal=True（正式回測）：訊號延後一根才生效 → 績效正常、受成本拖累。
  - shift_signal=False（移除 shift，引入未來函數）：訊號當根就吃到報酬 →
    績效會「不合理地變好」。
測試斷言「拿掉 shift 後績效顯著優於有 shift」，藉此證明 shift(1) 確實在防未來函數；
若哪天有人不小心拿掉了引擎裡的 shift，這個測試會失敗。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.report import build_report
from backtest.vector_engine import run_backtest
from indicators.technical import atr


def _make_predictive_case(n: int = 500, seed: int = 7):
    """造一段資料 + 一個帶『同根未來函數』的訊號。

    signal[t] = sign(close[t] - close[t-1])，即用「當根的漲跌」當訊號。
    現實中第 t 根的漲跌要到該根收盤才知道，根本來不及用它交易第 t 根；
    這正是典型的 lookahead bug。

    - shift_signal=False（移除 shift）：position[t]=signal[t]，等於用當根漲跌方向
      去吃當根報酬 → 永遠賺，績效不合理變好。
    - shift_signal=True（正式回測）：position[t]=signal[t-1]，訊號被延後一根、
      與當根報酬錯開 → 回到正常（接近隨機）水準。
    """
    rng = np.random.default_rng(seed)
    rets = rng.standard_normal(n) * 0.01
    close = 100.0 * np.exp(np.cumsum(rets))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    data = pd.DataFrame({"close": close}, index=idx)

    same_bar_ret = data["close"].diff()
    signal = np.sign(same_bar_ret).fillna(0.0).astype(int)
    signal = signal.clip(lower=0)  # Phase 1 baseline 只做多/空手
    signal = pd.Series(signal.to_numpy(), index=idx, name="signal")
    return data, signal


def test_removing_shift_inflates_performance():
    """移除 shift 後，Sharpe / 總報酬應顯著變好（證明原本的 shift 在防未來函數）。"""
    data, signal = _make_predictive_case()

    with_shift = build_report(run_backtest(data, signal, fee_bps=0.0, shift_signal=True))
    without_shift = build_report(run_backtest(data, signal, fee_bps=0.0, shift_signal=False))

    # 拿掉 shift = 偷看未來 → 報酬與 Sharpe 不合理地暴增
    assert without_shift.total_return > with_shift.total_return
    assert without_shift.sharpe > with_shift.sharpe + 1.0
    # 完美預測下一根方向、又當根生效 → 幾乎不會虧
    assert without_shift.win_rate > 0.95
    assert without_shift.total_return > 0.0


def test_default_engine_uses_shift():
    """預設（不傳 shift_signal）必須等同 shift_signal=True，不可退化成未來函數版本。"""
    data, signal = _make_predictive_case()
    default = build_report(run_backtest(data, signal, fee_bps=0.0))
    explicit = build_report(run_backtest(data, signal, fee_bps=0.0, shift_signal=True))
    assert default.total_return == pytest.approx(explicit.total_return)
    assert default.sharpe == pytest.approx(explicit.sharpe)


def test_shift_reduces_winrate_vs_lookahead():
    """同一訊號，有 shift 的勝率不應達到偷看未來那種接近全勝的水準。"""
    data, signal = _make_predictive_case()
    with_shift = build_report(run_backtest(data, signal, fee_bps=0.0, shift_signal=True))
    without_shift = build_report(run_backtest(data, signal, fee_bps=0.0, shift_signal=False))
    assert with_shift.win_rate < without_shift.win_rate


# ---------- ATR 無未來函數驗證 ----------

def _make_ohlc_for_lookahead(n: int = 100) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"high": 101.0, "low": 99.0, "close": 100.0},
        index=idx,
    )


def test_atr_no_lookahead_future_spike_does_not_affect_past():
    """ATR 為因果指標：在末根插入極端值，不應改變之前任何 bar 的 ATR。

    原理：若 ATR 使用了未來資料（例如 centered rolling window），
    末尾極端值會向前「洩漏」，改變中間 bar 的值。
    正確的因果計算只改最後一根。
    """
    base = _make_ohlc_for_lookahead(n=100)
    modified = base.copy()
    # 末根插入 10 倍振幅的極端值
    modified.iloc[-1] = {"high": 200.0, "low": 50.0, "close": 150.0}

    atr_base = atr(base, window=14)
    atr_mod = atr(modified, window=14)

    # 前 99 根不應受最後一根影響
    pd.testing.assert_series_equal(atr_base.iloc[:-1], atr_mod.iloc[:-1])
    # 最後一根 ATR 應該因極端值而改變
    assert atr_mod.iloc[-1] != pytest.approx(atr_base.iloc[-1])
