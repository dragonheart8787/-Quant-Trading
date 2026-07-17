"""Phase 4 ML 訊號層測試（TDD 先行，2026-07-15）。

涵蓋 ML 訓練管線特有的洩漏通道與行為不變量：
  1. 特徵矩陣欄位註冊（9 欄）＋因果性（竄改未來不改變過去特徵）
  2. label 方向與尾端 NaN（沿用 backtest/ic.py forward_return）
  3. purge 邊界：竄改 test 首根 close（改變 test_start-1 的 label）不影響訓練
  4. 測試期零洩漏：竄改 test 期資料，模型參數與更早的 test 訊號逐位不變
  5. 訊號域 {-1,0,+1} 與 NaN 特徵列安全空手
  6. τ 中性帶單調性：τ 越大非零訊號數不增
  7. LR＋交互項可表達 AND 規則（v2 表達力 sanity）
  8. 確定性：同輸入兩次訓練逐位相同
  9. 訓練集單一類別 → 退化為全空手，不 crash
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai.ml_train import (
    FEATURE_COLUMNS,
    LABEL_K,
    build_feature_matrix,
    fit_fold,
    make_label,
)
from backtest.ic import forward_return
from strategy.ml_signal import MlLogisticSignal


def _synthetic_ohlc(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """合成隨機漫步 OHLC（含 warmup 所需長度；ts 為 UTC 1h bar）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"ts": ts, "open": open_, "high": high, "low": low, "close": close}
    )


def _synthetic_features(n: int = 300, seed: int = 1) -> tuple[pd.DataFrame, np.ndarray]:
    """直接構造 9 欄特徵矩陣＋AND 模式（death × trend_down）。"""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        rng.normal(size=(n, len(FEATURE_COLUMNS))), columns=list(FEATURE_COLUMNS)
    )
    death = rng.integers(0, 2, n).astype(float)
    td = rng.integers(0, 2, n).astype(float)
    pattern = death * td
    df["ix_death_trend_down"] = pattern
    return df, pattern


# ---------- 1. 特徵矩陣：欄位註冊 + 因果性 ----------

def test_feature_matrix_columns_and_causality():
    data = _synthetic_ohlc()
    fm = build_feature_matrix(data)

    # 預先註冊的 9 個特徵欄，順序固定
    assert list(fm.columns) == list(FEATURE_COLUMNS)
    assert len(FEATURE_COLUMNS) == 9

    # regime 暖機期（前 regime_window-1 根）trend_down 應為 NaN（非靜默 False）
    assert fm["trend_down"].iloc[:119].isna().all()
    assert fm["trend_down"].iloc[130:].notna().all()

    # 因果性：竄改 350 之後的所有價格，350 之前的特徵逐位不變
    tampered = data.copy()
    for col in ("open", "high", "low", "close"):
        tampered.loc[350:, col] = tampered.loc[350:, col] * 1.5
    fm2 = build_feature_matrix(tampered)
    pd.testing.assert_frame_equal(fm.iloc[:350], fm2.iloc[:350])


# ---------- 2. label：方向 + 尾端 NaN ----------

def test_label_forward_direction_and_tail_nan():
    close = pd.Series([1.0, 2.0, 1.0, 1.0])
    label = make_label(close, k=1)
    # fwd = [+1.0, -0.5, 0.0, NaN] → label = [1, 0, 0, NaN]（0 報酬不算 up）
    assert label.iloc[0] == 1.0
    assert label.iloc[1] == 0.0
    assert label.iloc[2] == 0.0
    assert pd.isna(label.iloc[3])
    assert LABEL_K == 1


# ---------- 3. purge 邊界 ----------

def test_fit_fold_purges_boundary_label():
    data = _synthetic_ohlc()
    g0 = 300  # test fold 首根的全域位置

    def _fit(d: pd.DataFrame):
        fm = build_feature_matrix(d)
        fwd = forward_return(d["close"], LABEL_K)
        return fit_fold(fm, make_label(d["close"]), fwd, test_start_pos=g0)

    base = _fit(data)

    # 竄改 test 首根 close：唯一受影響的「訓練期」量是 label[g0-1]，
    # 該列必須已被 purge——訓練結果需逐位不變
    tampered = data.copy()
    tampered.loc[g0, "close"] = tampered.loc[g0, "close"] * 1.05
    after = _fit(tampered)

    assert after.tau == base.tau
    assert np.array_equal(after.strategy.model.coef_, base.strategy.model.coef_)
    assert after.n_train == base.n_train


# ---------- 4. 測試期零洩漏 ----------

def test_fit_fold_ignores_test_period():
    data = _synthetic_ohlc()
    g0 = 300

    fm = build_feature_matrix(data)
    base = fit_fold(fm, make_label(data["close"]),
                    forward_return(data["close"], LABEL_K), test_start_pos=g0)
    sig_base = base.strategy.predict_signal(fm.iloc[g0 : g0 + 5])

    # 竄改 g0+5 之後的全部價格：scaler/模型/τ 與 g0..g0+4 的訊號必須逐位不變
    tampered = data.copy()
    for col in ("open", "high", "low", "close"):
        tampered.loc[g0 + 5 :, col] = tampered.loc[g0 + 5 :, col] * 1.5
    fm2 = build_feature_matrix(tampered)
    after = fit_fold(fm2, make_label(tampered["close"]),
                     forward_return(tampered["close"], LABEL_K), test_start_pos=g0)

    assert after.tau == base.tau
    assert np.array_equal(after.strategy.model.coef_, base.strategy.model.coef_)
    sig_after = after.strategy.predict_signal(fm2.iloc[g0 : g0 + 5])
    pd.testing.assert_series_equal(sig_base, sig_after)


# ---------- 5. 訊號域 + NaN 安全 ----------

def test_signal_domain_and_nan_safe():
    df, pattern = _synthetic_features()
    label = pd.Series(pattern)
    fwd = pd.Series(np.where(pattern == 1, 0.01, -0.01))
    res = fit_fold(df, label, fwd, test_start_pos=250)

    holed = df.copy()
    holed.iloc[260] = np.nan
    sig = res.strategy.predict_signal(holed.iloc[250:])

    assert set(sig.unique()) <= {-1, 0, 1}
    assert sig.loc[260] == 0  # NaN 特徵列 → 安全空手（比照 chip NaN 語意）
    assert sig.dtype == np.int64


# ---------- 6. τ 單調性 ----------

def test_tau_monotone_nonzero_count():
    df, pattern = _synthetic_features()
    label = pd.Series(pattern)
    fwd = pd.Series(np.where(pattern == 1, 0.01, -0.01))
    res = fit_fold(df, label, fwd, test_start_pos=250)

    counts = []
    for tau in (0.0, 0.05, 0.1, 0.2, 0.4):
        strat = MlLogisticSignal(
            feature_columns=FEATURE_COLUMNS,
            scaler=res.strategy.scaler, model=res.strategy.model, tau=tau,
        )
        counts.append(int((strat.predict_signal(df) != 0).sum()))
    assert all(a >= b for a, b in zip(counts, counts[1:]))


# ---------- 7. LR + 交互項可表達 AND 規則 ----------

def test_lr_with_interactions_learns_and_rule():
    df, pattern = _synthetic_features()
    label = pd.Series(pattern)  # y 完全由 death × trend_down 交互項決定
    fwd = pd.Series(np.where(pattern == 1, 0.01, -0.01))
    res = fit_fold(df, label, fwd, test_start_pos=250)

    sig = res.strategy.predict_signal(df.iloc[250:]).to_numpy()
    want = np.where(pattern[250:] == 1, 1, -1)
    accuracy = float((sig == want).mean())
    assert accuracy >= 0.9  # AND 規則落在假設空間內，LR+交互項應能近乎還原


# ---------- 8. 確定性 ----------

def test_determinism_two_runs_identical():
    data = _synthetic_ohlc()
    fm = build_feature_matrix(data)
    label = make_label(data["close"])
    fwd = forward_return(data["close"], LABEL_K)

    r1 = fit_fold(fm, label, fwd, test_start_pos=300)
    r2 = fit_fold(fm, label, fwd, test_start_pos=300)

    assert r1.tau == r2.tau
    assert np.array_equal(r1.strategy.model.coef_, r2.strategy.model.coef_)
    pd.testing.assert_series_equal(
        r1.strategy.predict_signal(fm.iloc[300:]),
        r2.strategy.predict_signal(fm.iloc[300:]),
    )


# ---------- 9. 單一類別退化 ----------

def test_single_class_train_degenerates_to_flat():
    df, _ = _synthetic_features()
    label = pd.Series(np.ones(len(df)))  # 訓練期只有一個類別
    fwd = pd.Series(np.full(len(df), 0.01))
    res = fit_fold(df, label, fwd, test_start_pos=250)

    assert res.degenerate
    sig = res.strategy.predict_signal(df.iloc[250:])
    assert (sig == 0).all()
