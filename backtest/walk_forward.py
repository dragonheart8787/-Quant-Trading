"""Walk-forward 驗證。

用 sklearn 的 TimeSeriesSplit 把時間序列切成多個 (train, test) fold，
每個 fold 在 *測試窗* 上用既有 backtest/vector_engine.py 跑回測，
收集 sharpe / max_drawdown / win_rate / annual_return，最後彙總平均與標準差。

設計重點：
- TimeSeriesSplit 保證測試窗永遠在訓練窗之後（不洩漏未來），符合硬性規範
  「禁止隨機 K-fold」。
- 訊號在「全段資料」上產生後，才依 fold 的 index 切片。這是為了讓技術指標的
  rolling 暖機期能用到測試窗之前的真實歷史，避免每個 fold 開頭都是一段 NaN；
  指標只用到「當根與更早」的資料，配合回測 shift(1)，不會造成未來函數。
- Phase 1 的 MA/RSI 是規則策略、無需擬合參數，因此 train 窗在此僅用來界定
  時間邊界；介面仍保留 train/test 切分，未來換成需要擬合的策略可直接沿用。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import TradeCosts
from backtest.report import PerformanceReport, build_report
from backtest.vector_engine import run_backtest
from strategy.base import BaseStrategy


@dataclass
class FoldResult:
    fold: int
    train_size: int
    test_size: int
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    report: PerformanceReport


@dataclass
class WalkForwardSummary:
    folds: list[FoldResult]
    metrics_mean: dict[str, float]
    metrics_std: dict[str, float]

    # 彙總要呈現的指標
    METRIC_KEYS = ("sharpe", "max_drawdown", "win_rate", "annual_return")

    def pretty(self) -> str:
        lines = []
        header = (
            f"{'fold':>4} {'test_start':>19} {'test_end':>19} "
            f"{'bars':>6} {'sharpe':>8} {'MDD':>9} {'win':>8} {'annual':>9}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for fr in self.folds:
            r = fr.report
            lines.append(
                f"{fr.fold:>4} "
                f"{str(fr.test_start)[:19]:>19} {str(fr.test_end)[:19]:>19} "
                f"{r.num_bars:>6} {r.sharpe:>8.2f} {r.max_drawdown:>8.2%} "
                f"{r.win_rate:>8.2%} {r.annual_return:>8.2%}"
            )
        lines.append("-" * len(header))
        mean, std = self.metrics_mean, self.metrics_std
        lines.append(
            f"{'mean':>4} {'':>19} {'':>19} {'':>6} "
            f"{mean['sharpe']:>8.2f} {mean['max_drawdown']:>8.2%} "
            f"{mean['win_rate']:>8.2%} {mean['annual_return']:>8.2%}"
        )
        lines.append(
            f"{'std':>4} {'':>19} {'':>19} {'':>6} "
            f"{std['sharpe']:>8.2f} {std['max_drawdown']:>8.2%} "
            f"{std['win_rate']:>8.2%} {std['annual_return']:>8.2%}"
        )
        return "\n".join(lines)


def _extract_metrics(report: PerformanceReport) -> dict[str, float]:
    return {
        "sharpe": report.sharpe,
        "max_drawdown": report.max_drawdown,
        "win_rate": report.win_rate,
        "annual_return": report.annual_return,
    }


def walk_forward(
    data: pd.DataFrame,
    strategy: BaseStrategy,
    n_splits: int = 5,
    fee_bps: float = 5.0,
    periods_per_year: int | None = None,
    costs: TradeCosts | None = None,
) -> WalkForwardSummary:
    """對 canonical 資料做 walk-forward 回測。

    Args:
        data: canonical schema（需含 ts, close），以時間遞增排序。
        strategy: 任何 BaseStrategy 實作。
        n_splits: TimeSeriesSplit 的 fold 數（至少 2）。
        fee_bps: 單邊換手成本。costs 給定時忽略。
        periods_per_year: 年化因子；None 則用 report 預設（1h=8760）。
        costs: 買賣分離費率，透傳給 run_backtest（None = 沿用 fee_bps）。
    """
    if n_splits < 2:
        raise ValueError("n_splits 至少為 2")
    if len(data) <= n_splits + 1:
        raise ValueError("資料量不足以切出指定 fold 數")

    # 用 ts 當 index，方便回測與切片
    indexed = data.reset_index(drop=True)
    close_df = pd.DataFrame({"close": indexed["close"].to_numpy()}, index=indexed["ts"].to_numpy())

    # 在全段資料上產生訊號（指標可用測試窗之前的歷史暖機），再依 fold 切片
    full_signal = strategy.generate_signals(indexed)
    full_signal.index = close_df.index

    from backtest.report import HOURLY_PERIODS_PER_YEAR

    ppy = periods_per_year if periods_per_year is not None else HOURLY_PERIODS_PER_YEAR

    tscv = TimeSeriesSplit(n_splits=n_splits)
    positions = np.arange(len(indexed))

    folds: list[FoldResult] = []
    for i, (train_idx, test_idx) in enumerate(tscv.split(positions), start=1):
        test_data = close_df.iloc[test_idx]
        test_signal = full_signal.iloc[test_idx]

        result = run_backtest(test_data, test_signal, fee_bps=fee_bps, shift_signal=True, costs=costs)
        report = build_report(result, periods_per_year=ppy)

        folds.append(
            FoldResult(
                fold=i,
                train_size=len(train_idx),
                test_size=len(test_idx),
                test_start=pd.Timestamp(test_data.index[0]),
                test_end=pd.Timestamp(test_data.index[-1]),
                report=report,
            )
        )

    # 彙總平均與標準差
    keys = WalkForwardSummary.METRIC_KEYS
    stacked = {k: np.array([_extract_metrics(f.report)[k] for f in folds]) for k in keys}
    metrics_mean = {k: float(v.mean()) for k, v in stacked.items()}
    metrics_std = {k: float(v.std(ddof=1)) if len(v) > 1 else 0.0 for k, v in stacked.items()}

    return WalkForwardSummary(folds=folds, metrics_mean=metrics_mean, metrics_std=metrics_std)
