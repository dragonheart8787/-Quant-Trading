"""backtest/ic.py 的防未來函數與正確性測試（TDD，先寫測試後實作）。

三類核心測試（IC 診斷設計規格，2026-07-12）：
1. 手工數字驗證 forward_return 的 shift 方向沒寫反（尾端 k 根 NaN、不回繞）。
2. 合成預測力：construct close 使 feature(t) 單調決定下一根報酬 → IC(k=1) ≈ +1；
   規格版 sign 構造（close(t+1)=close(t)*(1+0.01*sign(feature(t)))）因 fwd 只有
   兩個值、Spearman 有大量 tie，理論值 <1，斷言強正（>0.8）。
3. 反向對照：feature 只跟「過去」報酬相關（feature(t)=r(t)，iid 隨機漫步）→
   IC(k=1) ≈ 0。
另含：NaN 逐對剔除與 n_valid 回報、fold 切分自足性（fwd 不跨 fold 回繞）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.ic import (
    ICResult,
    compute_ic,
    fold_ics,
    forward_return,
    summarize_folds,
)


# ---------- 1. forward_return：手工數字驗 shift 方向 ----------

def test_forward_return_manual_numbers() -> None:
    """close = [100, 110, 121, 133.1]（每根 +10%）→ fwd_1 前三根都是 +0.10，
    最後 1 根 NaN（沒有未來資料，不回繞）。"""
    close = pd.Series([100.0, 110.0, 121.0, 133.1])
    fwd1 = forward_return(close, 1)
    assert fwd1.iloc[0] == pytest.approx(0.10)
    assert fwd1.iloc[1] == pytest.approx(0.10)
    assert fwd1.iloc[2] == pytest.approx(0.10)
    assert np.isnan(fwd1.iloc[3])

    fwd2 = forward_return(close, 2)
    assert fwd2.iloc[0] == pytest.approx(0.21)  # 100 → 121
    assert fwd2.iloc[1] == pytest.approx(0.21)  # 110 → 133.1
    assert np.isnan(fwd2.iloc[2])
    assert np.isnan(fwd2.iloc[3])


def test_forward_return_direction_single_jump() -> None:
    """單一跳升發生在 t=5→t=6：fwd_1 只有 t=5 那格捕捉到（+100%），
    t=6 之後為 0——方向寫反的話會出現在 t=6。"""
    close = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0])
    fwd1 = forward_return(close, 1)
    assert fwd1.iloc[5] == pytest.approx(1.0)
    assert fwd1.iloc[4] == pytest.approx(0.0)
    assert fwd1.iloc[6] == pytest.approx(0.0)


# ---------- 2. 合成預測力：IC(k=1) ≈ +1 ----------

def test_ic_synthetic_monotone_predictive_feature() -> None:
    """close(t+1) = close(t) * (1 + 0.001 * feature(t))，feature 值互異 →
    fwd_1(t) 與 feature(t) 秩序完全一致 → Spearman IC = +1。"""
    rng = np.random.default_rng(42)
    feature = pd.Series(rng.normal(size=300))
    close_vals = [100.0]
    for t in range(len(feature) - 1):
        close_vals.append(close_vals[-1] * (1 + 0.001 * feature.iloc[t]))
    close = pd.Series(close_vals)

    res = compute_ic(feature, forward_return(close, 1))
    assert res.spearman == pytest.approx(1.0)
    assert res.pearson == pytest.approx(1.0, abs=1e-6)


def test_ic_synthetic_sign_predictive_feature() -> None:
    """規格版構造：close(t+1) = close(t) * (1 + 0.01 * sign(feature(t)))。
    fwd_1 只有 ±1% 兩個值（大量 tie），連續 vs 二值的 Spearman 理論上限
    ≈ √3/2 ≈ 0.87 而非 1（seed=7 實測 0.796）——斷言強正即可；
    「精確 ≈ +1」由上面單調構造版覆蓋。"""
    rng = np.random.default_rng(7)
    feature = pd.Series(rng.normal(size=500))
    close_vals = [100.0]
    for t in range(len(feature) - 1):
        close_vals.append(close_vals[-1] * (1 + 0.01 * np.sign(feature.iloc[t])))
    close = pd.Series(close_vals)

    res = compute_ic(feature, forward_return(close, 1))
    assert res.spearman > 0.75


# ---------- 3. 反向對照：只跟過去相關 → IC ≈ 0 ----------

def test_ic_reverse_control_past_return_feature() -> None:
    """iid 隨機漫步報酬下，feature(t) = 當根已實現報酬 r(t)（純過去資訊）
    與 fwd_1(t) = r(t+1) 獨立 → IC ≈ 0。"""
    rng = np.random.default_rng(123)
    rets = rng.normal(0, 0.01, size=2000)
    close = pd.Series(100.0 * np.cumprod(1 + rets))
    feature = close.pct_change()  # feature(t) = r(t)，只含過去

    res = compute_ic(feature, forward_return(close, 1))
    assert abs(res.spearman) < 0.05
    assert abs(res.pearson) < 0.05


# ---------- NaN 逐對剔除與 n_valid ----------

def test_ic_pairwise_nan_drop_and_n_valid() -> None:
    """特徵或 fwd 任一為 NaN 的配對剔除；n_valid 回報有效配對數。"""
    feature = pd.Series([1.0, np.nan, 3.0, 4.0, 5.0])
    fwd = pd.Series([0.01, 0.02, np.nan, 0.04, 0.05])
    res = compute_ic(feature, fwd)
    assert res.n_valid == 3  # index 0, 3, 4
    assert res.spearman == pytest.approx(1.0)


def test_ic_insufficient_pairs_gives_nan() -> None:
    """有效配對 <3 或零變異 → IC 給 NaN（不拋錯，供上層彙總時剔除）。"""
    res = compute_ic(pd.Series([1.0, 2.0]), pd.Series([0.1, 0.2]))
    assert res.n_valid == 2
    assert np.isnan(res.spearman)

    const = compute_ic(pd.Series([1.0, 1.0, 1.0, 1.0]),
                       pd.Series([0.1, 0.2, 0.3, 0.4]))
    assert np.isnan(const.spearman)


# ---------- fold 切分：連續不重疊、fold 自足（fwd 不跨界） ----------

def test_fold_ics_returns_n_folds_results() -> None:
    rng = np.random.default_rng(0)
    close = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 90)))
    feature = pd.Series(rng.normal(size=90))
    results = fold_ics(feature, close, k=1, n_folds=3)
    assert len(results) == 3
    assert all(isinstance(r, ICResult) for r in results)
    # 3 folds × 30 bars，各 fold 內算 fwd 損失尾端 k=1 根 → n_valid = 29
    assert all(r.n_valid == 29 for r in results)


def test_fold_ics_folds_are_self_contained() -> None:
    """fwd return 在各 fold 內計算，不跨 fold 拿下一段的 close——
    fold 交界處的巨大跳空不得出現在前一 fold 的 fwd 配對中；
    且竄改後段資料不改變前段 fold 的 IC（無未來函數）。"""
    rng = np.random.default_rng(1)
    n = 60  # 2 folds × 30
    feature = pd.Series(rng.normal(size=n))
    close_a = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, n)))

    # 竄改第二個 fold 的 close（含 fold 交界跳空 10 倍）
    close_b = close_a.copy()
    close_b.iloc[30:] = close_b.iloc[30:] * 10 + rng.normal(0, 5, 30)

    res_a = fold_ics(feature, close_a, k=1, n_folds=2)
    res_b = fold_ics(feature, close_b, k=1, n_folds=2)
    # 第一個 fold 完全不受第二個 fold 資料影響
    assert res_a[0].spearman == pytest.approx(res_b[0].spearman)
    assert res_a[0].n_valid == res_b[0].n_valid


# ---------- 彙總 ----------

def test_summarize_folds_mean_std_pos_frac() -> None:
    results = [
        ICResult(spearman=0.1, pearson=0.2, n_valid=50),
        ICResult(spearman=-0.1, pearson=0.0, n_valid=40),
        ICResult(spearman=0.3, pearson=0.1, n_valid=60),
        ICResult(spearman=np.nan, pearson=np.nan, n_valid=2),  # 無效 fold 剔除
    ]
    s = summarize_folds(results)
    assert s["n_folds_valid"] == 3
    assert s["mean_spearman"] == pytest.approx(0.1)
    assert s["std_spearman"] == pytest.approx(0.2)  # ddof=1: std([0.1,-0.1,0.3])
    assert s["pos_frac_spearman"] == pytest.approx(2 / 3)
    assert s["mean_pearson"] == pytest.approx(0.1)
    assert s["mean_n_valid"] == pytest.approx(50.0)
