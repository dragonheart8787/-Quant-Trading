"""Walk-forward 驗證測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.walk_forward import WalkForwardSummary, walk_forward
from data.cleaning import to_canonical_from_binance
from strategy.ma_rsi import MaRsiStrategy
from tests.test_indicators_and_cleaning import _fake_binance_raw


def _canonical(n: int = 1000, seed: int = 0) -> pd.DataFrame:
    """用隨機漫步價格造一段 canonical 資料（避免依賴外部 API）。"""
    rng = np.random.default_rng(seed)
    raw = _fake_binance_raw(n)
    # 用隨機漫步覆蓋價格欄位，讓策略訊號有變化
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.01))
    raw["open"] = prices
    raw["high"] = prices * 1.005
    raw["low"] = prices * 0.995
    raw["close"] = prices
    raw["quote_asset_volume"] = raw["volume"].astype(float) * prices
    return to_canonical_from_binance(raw)


def test_walk_forward_produces_n_folds():
    data = _canonical(1000)
    strat = MaRsiStrategy(fast_window=20, slow_window=60)
    summary = walk_forward(data, strat, n_splits=5, fee_bps=5.0)
    assert isinstance(summary, WalkForwardSummary)
    assert len(summary.folds) == 5
    # fold 編號遞增
    assert [f.fold for f in summary.folds] == [1, 2, 3, 4, 5]


def test_walk_forward_test_windows_are_chronological_and_non_overlapping():
    data = _canonical(1000)
    strat = MaRsiStrategy(fast_window=20, slow_window=60)
    summary = walk_forward(data, strat, n_splits=5)
    starts = [f.test_start for f in summary.folds]
    ends = [f.test_end for f in summary.folds]
    # 每個測試窗的結尾都早於下一個測試窗的開頭（時間遞增、不重疊）
    for i in range(len(summary.folds) - 1):
        assert ends[i] < starts[i + 1]


def test_walk_forward_summary_has_mean_and_std():
    data = _canonical(1000)
    strat = MaRsiStrategy(fast_window=20, slow_window=60)
    summary = walk_forward(data, strat, n_splits=5)
    for key in WalkForwardSummary.METRIC_KEYS:
        assert key in summary.metrics_mean
        assert key in summary.metrics_std
        assert np.isfinite(summary.metrics_mean[key])
        assert summary.metrics_std[key] >= 0.0
    # 平均 sharpe 應介於合理區間（不是 NaN/inf）
    assert -10 < summary.metrics_mean["sharpe"] < 10


def test_walk_forward_rejects_too_few_splits():
    data = _canonical(100)
    strat = MaRsiStrategy(fast_window=5, slow_window=10)
    with pytest.raises(ValueError):
        walk_forward(data, strat, n_splits=1)


def test_walk_forward_rejects_insufficient_data():
    data = _canonical(60)[:4]  # 太少
    strat = MaRsiStrategy(fast_window=2, slow_window=3)
    with pytest.raises(ValueError):
        walk_forward(data, strat, n_splits=5)
