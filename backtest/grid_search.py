"""參數網格搜尋 + fold 市場狀態標註。

兩者都建立在既有 walk_forward / vector_engine 之上，不重寫回測邏輯：
- `label_fold_regimes`：用與 walk_forward 完全相同的 TimeSeriesSplit 切分，
  對每個 fold 的測試窗呼叫 regime.classify_regime，確保 fold 邊界對齊。
- `grid_search`：對每組 (fast, slow, rsi_overbought) 參數呼叫 walk_forward，
  收集每個 fold 的 sharpe，並算跨 fold 的平均與標準差。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.regime import RegimeLabel, classify_regime
from backtest.walk_forward import walk_forward
from strategy.ma_rsi import MaRsiStrategy


@dataclass
class FoldRegime:
    fold: int
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    regime: RegimeLabel


def label_fold_regimes(data: pd.DataFrame, n_splits: int = 5) -> list[FoldRegime]:
    """用與 walk_forward 相同的切分，標註每個 fold 測試窗的市場狀態。"""
    if n_splits < 2:
        raise ValueError("n_splits 至少為 2")
    if len(data) <= n_splits + 1:
        raise ValueError("資料量不足以切出指定 fold 數")

    indexed = data.reset_index(drop=True)
    close = pd.Series(indexed["close"].to_numpy(), index=indexed["ts"].to_numpy())

    tscv = TimeSeriesSplit(n_splits=n_splits)
    positions = np.arange(len(indexed))

    out: list[FoldRegime] = []
    for i, (_, test_idx) in enumerate(tscv.split(positions), start=1):
        seg = close.iloc[test_idx]
        out.append(
            FoldRegime(
                fold=i,
                test_start=pd.Timestamp(seg.index[0]),
                test_end=pd.Timestamp(seg.index[-1]),
                regime=classify_regime(seg),
            )
        )
    return out


def grid_search(
    data: pd.DataFrame,
    param_grid: list[dict],
    n_splits: int = 5,
    fee_bps: float = 5.0,
    strategy_cls: type = MaRsiStrategy,
) -> pd.DataFrame:
    """對每組參數在同一組 walk-forward fold 上跑回測，輸出每 fold sharpe + mean/std。

    Args:
        data: canonical schema。
        param_grid: 每個元素是策略建構參數 dict，
            例如 {"fast_window":10,"slow_window":40,"rsi_overbought":70}。
        n_splits: fold 數。
        fee_bps: 單邊換手成本。
        strategy_cls: 要評估的策略類別（預設 MaRsiStrategy）。需能吃 param_grid
            裡的關鍵字參數，例如 MaRsiRegimeStrategy 也接受同一組參數。

    Returns:
        DataFrame，每列一組參數，欄位含 fast/slow/rsi、fold1..foldN 的 sharpe、
        sharpe_mean、sharpe_std。
    """
    rows: list[dict] = []
    for params in param_grid:
        strat = strategy_cls(**params)
        summary = walk_forward(data, strat, n_splits=n_splits, fee_bps=fee_bps)
        sharpes = [f.report.sharpe for f in summary.folds]

        row: dict = {
            "fast": params.get("fast_window"),
            "slow": params.get("slow_window"),
            "rsi_ob": params.get("rsi_overbought", 70.0),
        }
        for i, s in enumerate(sharpes, start=1):
            row[f"fold{i}"] = s
        row["sharpe_mean"] = float(np.mean(sharpes))
        row["sharpe_std"] = float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
        rows.append(row)

    return pd.DataFrame(rows)


def default_param_grid() -> list[dict]:
    """3x3(x options) 的參數網格：fast × slow（× rsi 門檻）。"""
    grid: list[dict] = []
    fasts = [10, 20, 30]
    slows = [40, 60, 90]
    rsi_obs = [70.0]
    for f in fasts:
        for s in slows:
            for ob in rsi_obs:
                if f < s:  # MaRsiStrategy 要求 fast < slow
                    grid.append({"fast_window": f, "slow_window": s, "rsi_overbought": ob})
    return grid
