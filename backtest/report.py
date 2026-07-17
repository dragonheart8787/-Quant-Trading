"""績效報表：年化報酬、Sharpe、最大回撤(MDD)、勝率。

所有指標需要「每根 K 棒的週期長度」才能正確年化，因此用 periods_per_year
（1h K 線 = 24*365 = 8760）作為年化因子。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from backtest.vector_engine import BacktestResult

# 1 小時 K 線一年的根數（含週末，加密市場 24/7）
HOURLY_PERIODS_PER_YEAR = 24 * 365


@dataclass
class PerformanceReport:
    total_return: float        # 總報酬（倍數-1）
    annual_return: float       # 年化報酬
    annual_volatility: float   # 年化波動
    sharpe: float              # 年化 Sharpe（rf=0）
    max_drawdown: float        # 最大回撤（負值）
    win_rate: float            # 勝率（持倉期間正報酬根數占比）
    num_trades: int            # 換手次數
    num_bars: int              # 回測根數

    def as_dict(self) -> dict:
        return asdict(self)

    def pretty(self) -> str:
        return (
            f"總報酬:      {self.total_return:8.2%}\n"
            f"年化報酬:    {self.annual_return:8.2%}\n"
            f"年化波動:    {self.annual_volatility:8.2%}\n"
            f"Sharpe:      {self.sharpe:8.2f}\n"
            f"最大回撤:    {self.max_drawdown:8.2%}\n"
            f"勝率:        {self.win_rate:8.2%}\n"
            f"換手次數:    {self.num_trades:8d}\n"
            f"K棒數:       {self.num_bars:8d}"
        )


def max_drawdown(equity_curve: pd.Series) -> float:
    """計算最大回撤（回傳負值或 0）。"""
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    return float(drawdown.min())


def _annualize_return(strategy_returns: pd.Series, periods_per_year: int) -> float:
    n = len(strategy_returns)
    if n == 0:
        return 0.0
    total_growth = float((1.0 + strategy_returns).prod())
    if total_growth <= 0:
        return -1.0
    return total_growth ** (periods_per_year / n) - 1.0


def build_report(
    result: BacktestResult,
    periods_per_year: int = HOURLY_PERIODS_PER_YEAR,
) -> PerformanceReport:
    """從回測結果建立績效報表。"""
    rets = result.strategy_returns
    n = len(rets)

    total_return = float(result.equity_curve.iloc[-1] - 1.0) if n else 0.0
    annual_return = _annualize_return(rets, periods_per_year)

    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    annual_vol = std * math.sqrt(periods_per_year)
    mean = float(rets.mean()) if n else 0.0
    sharpe = (mean / std * math.sqrt(periods_per_year)) if std > 0 else 0.0

    mdd = max_drawdown(result.equity_curve)

    # 勝率：只看實際有持倉的 K 棒，正報酬占比
    in_position = result.position != 0
    active = rets[in_position]
    win_rate = float((active > 0).mean()) if len(active) else 0.0

    return PerformanceReport(
        total_return=total_return,
        annual_return=annual_return,
        annual_volatility=annual_vol,
        sharpe=sharpe,
        max_drawdown=mdd,
        win_rate=win_rate,
        num_trades=result.trades,
        num_bars=n,
    )
