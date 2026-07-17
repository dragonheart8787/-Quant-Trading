"""參數網格搜尋與 fold regime 標註測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.grid_search import (
    default_param_grid,
    grid_search,
    label_fold_regimes,
)
from tests.test_walk_forward import _canonical


def test_default_grid_is_at_least_3x3():
    grid = default_param_grid()
    # fast<slow 過濾後仍需 >= 9 組（3x3）
    assert len(grid) >= 9
    for p in grid:
        assert p["fast_window"] < p["slow_window"]


def test_grid_search_table_shape_and_columns():
    data = _canonical(1200, seed=1)
    grid = default_param_grid()
    table = grid_search(data, grid, n_splits=5, fee_bps=5.0)
    assert len(table) == len(grid)
    for col in ["fast", "slow", "rsi_ob", "sharpe_mean", "sharpe_std"]:
        assert col in table.columns
    # 5 fold → fold1..fold5 欄位齊全
    for i in range(1, 6):
        assert f"fold{i}" in table.columns
    # 每列的 mean 應等於該列 fold sharpe 的平均
    fold_cols = [f"fold{i}" for i in range(1, 6)]
    recomputed = table[fold_cols].mean(axis=1)
    assert np.allclose(recomputed.to_numpy(), table["sharpe_mean"].to_numpy())


def test_grid_search_std_non_negative_and_finite():
    data = _canonical(1200, seed=2)
    table = grid_search(data, default_param_grid(), n_splits=5)
    assert (table["sharpe_std"] >= 0).all()
    assert np.isfinite(table["sharpe_mean"]).all()
    assert np.isfinite(table["sharpe_std"]).all()


def test_label_fold_regimes_count_and_labels():
    data = _canonical(1200, seed=3)
    regimes = label_fold_regimes(data, n_splits=5)
    assert len(regimes) == 5
    assert [r.fold for r in regimes] == [1, 2, 3, 4, 5]
    for r in regimes:
        assert r.regime.label in {"trend_up", "trend_down", "range"}
        assert 0.0 <= r.regime.r2 <= 1.0
        assert 0.0 <= r.regime.efficiency <= 1.0


def test_fold_regimes_align_with_walk_forward_windows():
    """regime 標註的 fold 視窗，必須與 walk_forward 的 fold 視窗一致。"""
    from backtest.walk_forward import walk_forward
    from strategy.ma_rsi import MaRsiStrategy

    data = _canonical(1200, seed=4)
    regimes = label_fold_regimes(data, n_splits=5)
    summary = walk_forward(data, MaRsiStrategy(20, 60), n_splits=5)
    for fr, wf in zip(regimes, summary.folds):
        assert fr.test_start == wf.test_start
        assert fr.test_end == wf.test_end
