"""IC（Information Coefficient）診斷核心函式（Phase 3 特徵資訊價值診斷）。

目的：檢驗特徵（如 foreign_net）對未來報酬有無統計資訊價值，決定籌碼方向
換特徵／改連續加權／棄用之前的分岔依據。純診斷分析，不碰回測引擎、不下單。

設計要點（2026-07-12 規格）：
- 主指標 Spearman rank IC（foreign_net 是股數級距、fwd return 厚尾，Pearson
  會被離群值主導），Pearson 一併回報僅供參照。
- forward return 標準定義：fwd_k(T) = close(T+k)/close(T) - 1，用 close.shift(-k)
  實作，尾端 k 根 NaN 不回繞（純診斷特徵資訊價值，非執行延遲版）。
- NaN 逐對剔除：特徵或 fwd 任一為 NaN 的配對剔除，每 fold 回報 n_valid。
- fold 自足性：fold_ics 把序列切成連續不重疊等長子段，**fwd return 在各 fold
  內計算**（每 fold 損失尾端 k 根），fold 之間不共享任何 close 資料——竄改
  後段資料不會改變前段 fold 的 IC（測試 test_fold_ics_folds_are_self_contained）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ICResult:
    """單一 fold 的 IC 計算結果。IC 無法計算（有效配對 <3 或零變異）時為 NaN。"""

    spearman: float
    pearson: float
    n_valid: int


def forward_return(close: pd.Series, k: int) -> pd.Series:
    """fwd_k(T) = close(T+k)/close(T) - 1；尾端 k 根 NaN（無未來資料，不回繞）。"""
    if k < 1:
        raise ValueError("k 至少為 1")
    c = pd.to_numeric(close, errors="coerce")
    return c.shift(-k) / c - 1.0


def compute_ic(feature: pd.Series, fwd_ret: pd.Series) -> ICResult:
    """特徵 vs forward return 的 Spearman／Pearson IC；NaN 配對逐對剔除。

    有效配對 <3 或任一邊零變異時，相關係數無意義 → 回 NaN（不拋錯，
    供上層彙總時剔除該 fold）。
    """
    f = pd.to_numeric(pd.Series(feature).reset_index(drop=True), errors="coerce")
    r = pd.to_numeric(pd.Series(fwd_ret).reset_index(drop=True), errors="coerce")
    if len(f) != len(r):
        raise ValueError("feature 與 fwd_ret 長度不一致")

    mask = f.notna() & r.notna()
    n_valid = int(mask.sum())
    fv, rv = f[mask], r[mask]

    if n_valid < 3 or fv.nunique() < 2 or rv.nunique() < 2:
        return ICResult(spearman=float("nan"), pearson=float("nan"), n_valid=n_valid)
    return ICResult(
        spearman=float(fv.corr(rv, method="spearman")),
        pearson=float(fv.corr(rv, method="pearson")),
        n_valid=n_valid,
    )


def fold_ics(
    feature: pd.Series, close: pd.Series, k: int, n_folds: int
) -> list[ICResult]:
    """切成 n_folds 個連續不重疊等長子段，逐 fold 在段內算 IC。

    fwd return 在各 fold 段內計算（forward_return 尾端 k 根 NaN 被逐對剔除），
    fold 之間零資料共享——後段任何竄改不影響前段結果。
    """
    f = pd.Series(feature).reset_index(drop=True)
    c = pd.Series(close).reset_index(drop=True)
    if len(f) != len(c):
        raise ValueError("feature 與 close 長度不一致")
    if n_folds < 1:
        raise ValueError("n_folds 至少為 1")
    if len(f) < n_folds:
        raise ValueError("資料長度不足以切出 n_folds 段")

    bounds = np.linspace(0, len(f), n_folds + 1, dtype=int)
    results: list[ICResult] = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        f_seg = f.iloc[lo:hi].reset_index(drop=True)
        c_seg = c.iloc[lo:hi].reset_index(drop=True)
        results.append(compute_ic(f_seg, forward_return(c_seg, k)))
    return results


def summarize_folds(results: list[ICResult]) -> dict[str, float]:
    """彙總多 fold IC：mean±std（ddof=1）、正 IC fold 佔比、平均有效樣本數。

    IC 為 NaN 的 fold（樣本不足/零變異）剔除後彙總；n_folds_valid 回報實際
    進入統計的 fold 數。
    """
    valid = [r for r in results if not math.isnan(r.spearman)]
    if not valid:
        return {
            "n_folds_valid": 0,
            "mean_spearman": float("nan"), "std_spearman": float("nan"),
            "pos_frac_spearman": float("nan"),
            "mean_pearson": float("nan"),
            "mean_n_valid": float("nan"),
        }
    sp = np.array([r.spearman for r in valid])
    pe = np.array([r.pearson for r in valid])
    nv = np.array([r.n_valid for r in valid])
    return {
        "n_folds_valid": len(valid),
        "mean_spearman": float(sp.mean()),
        "std_spearman": float(sp.std(ddof=1)) if len(sp) > 1 else float("nan"),
        "pos_frac_spearman": float((sp > 0).mean()),
        "mean_pearson": float(pe.mean()),
        "mean_n_valid": float(nv.mean()),
    }
