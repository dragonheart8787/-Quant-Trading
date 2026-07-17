"""市場狀態分類測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.regime import (
    classify_regime,
    efficiency_ratio,
    linreg_r2,
    multi_scale_trend_down_mask,
    rolling_trend_down_mask,
)


def _series(values) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def test_strong_uptrend_is_trend_up():
    close = _series(100.0 * 1.005 ** np.arange(200))  # 穩定複利上漲
    label = classify_regime(close)
    assert label.label == "trend_up"
    assert label.is_trend
    assert label.r2 > 0.9          # 近乎完美直線（log 空間）
    assert label.efficiency > 0.9
    assert label.slope > 0


def test_strong_downtrend_is_trend_down():
    close = _series(100.0 * 0.995 ** np.arange(200))
    label = classify_regime(close)
    assert label.label == "trend_down"
    assert label.slope < 0


def test_choppy_market_is_range():
    # 在固定價位附近上下鋸齒，無淨位移
    base = 100.0 + np.tile([0.0, 2.0, 0.0, -2.0], 50)
    close = _series(base)
    label = classify_regime(close)
    assert label.label == "range"
    assert not label.is_trend
    assert label.r2 < 0.5
    assert label.efficiency < 0.3


def test_efficiency_ratio_bounds():
    up = _series(np.arange(1, 50, dtype=float))
    assert efficiency_ratio(up) == pytest.approx(1.0)  # 單調 → 路徑=位移
    flat = _series(np.full(20, 5.0))
    assert efficiency_ratio(flat) == 0.0               # 無位移


def test_linreg_r2_perfect_line():
    close = _series(np.exp(np.arange(50) * 0.01))  # log 後是完美直線
    r2, slope = linreg_r2(close)
    assert r2 == pytest.approx(1.0, abs=1e-6)
    assert slope == pytest.approx(0.01, abs=1e-6)


# ===== rolling_trend_down_mask（逐根因果 trend_down 判斷）=====

def test_rolling_mask_warmup_is_false():
    """暖機期（前 window-1 根）一律 False，即使整段是下跌。"""
    close = _series(100.0 * 0.995 ** np.arange(200))
    mask = rolling_trend_down_mask(close, window=120)
    assert not mask.iloc[:119].any()


def test_rolling_mask_detects_downtrend_after_warmup():
    close = _series(100.0 * 0.995 ** np.arange(200))
    mask = rolling_trend_down_mask(close, window=120)
    assert mask.iloc[119:].all(), "穩定下跌段暖機後應全為 True"


def test_rolling_mask_all_false_in_uptrend():
    close = _series(100.0 * 1.005 ** np.arange(200))
    mask = rolling_trend_down_mask(close, window=120)
    assert not mask.any()


def test_rolling_mask_causal():
    """截掉未來資料不改變過去判斷（因果性）。"""
    rng = np.random.default_rng(3)
    close = _series(100.0 * np.exp(np.cumsum(rng.standard_normal(400) * 0.01)))
    full = rolling_trend_down_mask(close, window=120)
    trunc = rolling_trend_down_mask(close.iloc[:250], window=120)
    np.testing.assert_array_equal(full.to_numpy()[:250], trunc.to_numpy())


def test_rolling_mask_invalid_window_raises():
    close = _series(np.full(50, 100.0))
    with pytest.raises(ValueError, match="window"):
        rolling_trend_down_mask(close, window=2)


# ===== multi_scale_trend_down_mask（A2 多尺度一致性）=====

def test_multi_scale_blocks_local_downtrend_in_uptrend():
    """長期上漲中的局部下跌子段：短視窗判 trend_down，但長視窗不同意 → 過濾。

    構造：400 根穩定上漲 + 130 根下跌尾段。
    短視窗（120）在尾段末端完全落在下跌區 → True；
    長視窗（300）仍涵蓋大量上漲區 → 非 trend_down → AND 後為 False。
    """
    up = 100.0 * 1.004 ** np.arange(400)
    down = up[-1] * 0.996 ** np.arange(1, 131)
    close = _series(np.concatenate([up, down]))

    short_mask = rolling_trend_down_mask(close, window=120)
    combined = multi_scale_trend_down_mask(close, short_window=120, long_window=300)

    assert short_mask.iloc[-5:].all(), "前提：短視窗在下跌尾段末端應判 trend_down"
    assert not combined.iloc[-5:].any(), "長視窗不同意時，多尺度遮罩應為 False"


def test_multi_scale_agrees_in_sustained_downtrend():
    """夠長的持續下跌：兩個視窗都同意 → True。"""
    close = _series(100.0 * 0.997 ** np.arange(400))
    combined = multi_scale_trend_down_mask(close, short_window=120, long_window=300)
    assert combined.iloc[299:].all(), "持續下跌段長視窗暖機後應全為 True"


def test_multi_scale_invalid_windows_raise():
    close = _series(np.full(400, 100.0))
    with pytest.raises(ValueError, match="long_window"):
        multi_scale_trend_down_mask(close, short_window=120, long_window=120)
