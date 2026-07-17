"""Phase 4 ML 訊號層第一輪：特徵矩陣＋逐 fold 訓練（L2 邏輯回歸）。

任務定位（2026-07-15 使用者核可的設計）：驗證「ML 學現有特徵的非線性組合
能否超越 v2 規則策略」，判定基準 = E2（event-driven、leverage_cap、真實成本）
下的配對 Δ(ML−v2)。本模組只負責特徵與訓練，E2 對照在 run_ml_signal.py。

★ 預先註冊的 9 個特徵（鎖定，不中途增刪）★
  基礎 5 欄（全部來自既有已驗證指標的因果衍生量）：
    ma_ratio   = sma20/sma60 - 1        （交叉強度連續版）
    close_dev  = close/sma60 - 1        （價格乖離）
    rsi14      = Wilder RSI(14)
    atr_norm   = ATR(14)/close          （正規化波動）
    trend_down = rolling_trend_down_mask(close, 120)（0/1；暖機期 NaN）
  交互 4 欄——完全由 v2/baseline 既有規則的 AND 結構逐條映射（窮舉自
  規則配對：多頭 1 組＋空頭三元 AND 的 C(3,2)=3 組），零裁量、零試跑：
    ix_golden_rsi_lt70     = (fast>slow) × (RSI<70)   ← baseline/v2 多頭
    ix_death_trend_down    = (fast<slow) × trend_down ← v2 空頭配對①
    ix_rsi_gt30_trend_down = (RSI>30)   × trend_down  ← v2 空頭配對②
    ix_death_rsi_gt30      = (fast<slow) × (RSI>30)   ← v2 空頭配對③

★ ML 特有洩漏通道的防治（tests/test_ml_signal.py 逐條背書）★
  - purge：label 用 k 根 forward return → 訓練列僅取 pos ≤ test_start-1-k，
    杜絕 train 尾與 test 頭共享同一段未來報酬。
  - scaler / 模型 / τ 全部只在訓練列上決定；τ 用訓練集尾端 20% 內部驗證
    （淨損益代理指標，含 fee_bps 換手成本），永不接觸 test。
  - 無超參數搜尋、無早停：LR 固定 C=1.0 / lbfgs / max_iter=1000，凸優化
    確定性收斂，AGENTS 多 seed 規範自動滿足。
  - 訓練樣本 < MIN_TRAIN_ROWS 或單一類別 → 退化為全空手（誠實不硬 fit）。

已知近似（誠實標注）：
  - τ 內部驗證的損益代理 = signal×fwd − 單邊費率×換手邊數，是向量近似
    （非 E2）；僅用於在訓練資料內挑 τ，不用於任何跨策略比較。
  - n_train < THIN_TRAIN_FLOOR 標 thin（窗C 前段 fold），最終報告需
    附「排除 thin fold 後」的判準複驗（使用者 D2 決策）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from backtest.ic import forward_return
from backtest.regime import rolling_trend_down_mask
from indicators.technical import atr, rsi, sma
from strategy.ml_signal import MlLogisticSignal

FEATURE_COLUMNS: tuple[str, ...] = (
    "ma_ratio",
    "close_dev",
    "rsi14",
    "atr_norm",
    "trend_down",
    "ix_golden_rsi_lt70",
    "ix_death_trend_down",
    "ix_rsi_gt30_trend_down",
    "ix_death_rsi_gt30",
)

LABEL_K: int = 1                       # D1 決策：k=1 forward return
TAU_GRID: tuple[float, ...] = (0.0, 0.02, 0.05, 0.10, 0.15)  # D3：內部驗證選取
TAU_FALLBACK: float = 0.05             # 內部驗證退化時的預設 τ（預先註冊）
INNER_VAL_FRACTION: float = 0.2        # 訓練集尾端 20% 作 τ 內部驗證
MIN_TRAIN_ROWS: int = 50               # 低於此數退化為全空手
THIN_TRAIN_FLOOR: int = 2000           # 過薄 fold 標注門檻（D2 決策）

# v2/baseline 規則帶的門檻（沿用既有策略參數，非新自由度）
_RSI_OVERBOUGHT = 70.0
_RSI_NOT_OVERSOLD = 30.0


def build_feature_matrix(
    data: pd.DataFrame,
    fast_window: int = 20,
    slow_window: int = 60,
    rsi_window: int = 14,
    atr_window: int = 14,
    regime_window: int = 120,
) -> pd.DataFrame:
    """由 OHLC 產出預先註冊的 9 欄特徵矩陣（index 沿用 data；暖機期 NaN）。

    全部因果計算：sma/rsi/atr 為既有向量化指標（rolling/ewm）、trend_down
    為 trailing 視窗逐根判斷。暖機期一律 NaN（不靜默補 0），訓練/預測端
    以「任一欄 NaN → 剔除/空手」處理。
    """
    required = {"high", "low", "close"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"data 缺少欄位: {missing}")

    close = data["close"]
    fast = sma(close, fast_window)
    slow = sma(close, slow_window)
    r = rsi(close, rsi_window)
    a = atr(data[["high", "low", "close"]], atr_window)

    ma_valid = fast.notna() & slow.notna()
    golden = (fast > slow).astype(float).where(ma_valid)
    death = (fast < slow).astype(float).where(ma_valid)
    rsi_lt70 = (r < _RSI_OVERBOUGHT).astype(float).where(r.notna())
    rsi_gt30 = (r > _RSI_NOT_OVERSOLD).astype(float).where(r.notna())

    td_mask = rolling_trend_down_mask(close, regime_window)
    trend_down = td_mask.astype(float)
    trend_down.iloc[: regime_window - 1] = np.nan  # 暖機期不可判定，標 NaN

    return pd.DataFrame(
        {
            "ma_ratio": fast / slow - 1.0,
            "close_dev": close / slow - 1.0,
            "rsi14": r,
            "atr_norm": a / close,
            "trend_down": trend_down,
            "ix_golden_rsi_lt70": golden * rsi_lt70,
            "ix_death_trend_down": death * trend_down,
            "ix_rsi_gt30_trend_down": rsi_gt30 * trend_down,
            "ix_death_rsi_gt30": death * rsi_gt30,
        },
        index=data.index,
    )


def make_label(close: pd.Series, k: int = LABEL_K) -> pd.Series:
    """二元 label：forward k 根報酬 > 0 → 1.0，≤ 0 → 0.0；尾端 k 根 NaN。"""
    fwd = forward_return(close, k)
    return (fwd > 0).astype(float).where(fwd.notna())


@dataclass
class FoldFitResult:
    """單一 fold 的訓練產物與樣本量診斷。"""

    strategy: MlLogisticSignal
    tau: float
    n_train: int
    thin: bool          # n_train < THIN_TRAIN_FLOOR（過薄，最終報告標注＋複驗）
    degenerate: bool    # 樣本不足/單一類別 → 全空手


def _fit_lr(x: np.ndarray, y: np.ndarray) -> tuple[StandardScaler, LogisticRegression]:
    """固定超參數的 scaler+LR（無搜尋、無早停；確定性）。"""
    scaler = StandardScaler().fit(x)
    # sklearn 1.8 起 penalty 參數棄用；預設即 L2（等價 l1_ratio=0），故不顯式傳
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    model.fit(scaler.transform(x), y)
    return scaler, model


def _tau_metric(signal: np.ndarray, fwd: np.ndarray, fee_bps: float) -> float:
    """τ 內部驗證的淨損益代理：Σ(signal×fwd) − 單邊費率 × 換手邊數。"""
    cost = fee_bps * 1e-4
    turnover_sides = np.abs(np.diff(signal, prepend=0)).sum()
    return float(np.nansum(signal * fwd) - cost * turnover_sides)


def _signal_from_proba(proba: np.ndarray, tau: float) -> np.ndarray:
    return np.where(proba > 0.5 + tau, 1, np.where(proba < 0.5 - tau, -1, 0))


def fit_fold(
    features: pd.DataFrame,
    label: pd.Series,
    fwd_ret: pd.Series,
    test_start_pos: int,
    k: int = LABEL_K,
    tau_grid: Sequence[float] = TAU_GRID,
    fee_bps: float = 5.0,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
) -> FoldFitResult:
    """訓練單一 fold 的 ML 訊號模型（expanding：test_start 之前全部可用歷史）。

    Args:
        features: 全歷史特徵矩陣（列位置 = 全域 bar 位置）。
        label: make_label 產出的二元 label（與 features 等長）。
        fwd_ret: k 根 forward return（τ 內部驗證用，與 label 同源）。
        test_start_pos: test fold 首根的全域位置；訓練列僅取
            pos ≤ test_start_pos - 1 - k（purge k 根，杜絕 label 洩漏）。
    """
    if not (len(features) == len(label) == len(fwd_ret)):
        raise ValueError("features / label / fwd_ret 長度必須一致")
    if not 0 <= test_start_pos <= len(features):
        raise ValueError("test_start_pos 超出資料範圍")

    cols = list(feature_columns)
    positions = np.arange(len(features))
    usable = (
        features[cols].notna().all(axis=1).to_numpy()
        & label.notna().to_numpy()
        & (positions <= test_start_pos - 1 - k)
    )
    train_pos = positions[usable]
    n_train = int(len(train_pos))
    thin = n_train < THIN_TRAIN_FLOOR

    def _degenerate() -> FoldFitResult:
        strategy = MlLogisticSignal(
            feature_columns=tuple(cols), scaler=None, model=None, tau=TAU_FALLBACK
        )
        return FoldFitResult(
            strategy=strategy, tau=TAU_FALLBACK, n_train=n_train,
            thin=thin, degenerate=True,
        )

    x_all = features[cols].to_numpy(dtype=float)[train_pos]
    y_all = label.to_numpy(dtype=float)[train_pos]
    if n_train < MIN_TRAIN_ROWS or len(np.unique(y_all)) < 2:
        return _degenerate()

    # --- τ 內部驗證：訓練集尾端 20%（永不接觸 test）---
    n_inner = max(1, int(round(INNER_VAL_FRACTION * n_train)))
    n_core = n_train - n_inner
    tau = TAU_FALLBACK
    if n_core >= MIN_TRAIN_ROWS and len(np.unique(y_all[:n_core])) >= 2:
        scaler_c, model_c = _fit_lr(x_all[:n_core], y_all[:n_core])
        proba_inner = model_c.predict_proba(scaler_c.transform(x_all[n_core:]))[:, 1]
        fwd_inner = fwd_ret.to_numpy(dtype=float)[train_pos[n_core:]]
        best = -np.inf
        for cand in tau_grid:  # 平手取較大 τ（較保守），grid 升冪 + >= 即為此語意
            metric = _tau_metric(_signal_from_proba(proba_inner, cand), fwd_inner, fee_bps)
            if metric >= best:
                best, tau = metric, float(cand)

    # --- 以選定 τ 用全部訓練列重訓 ---
    scaler, model = _fit_lr(x_all, y_all)
    strategy = MlLogisticSignal(
        feature_columns=tuple(cols), scaler=scaler, model=model, tau=tau
    )
    return FoldFitResult(
        strategy=strategy, tau=tau, n_train=n_train, thin=thin, degenerate=False
    )
