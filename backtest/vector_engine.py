"""向量化回測引擎。

核心防未來函數規範：position = signal.shift(1)。
策略在第 t 根 K 棒「收盤後」才看得到第 t 根訊號，因此最早只能在第 t+1 根
的報酬上生效。我們用「持倉 * 當根報酬」計算策略報酬，持倉來自 shift(1) 後的訊號。

手續費：每次持倉變動（換手）收取 fee_bps 的成本；或傳 costs=TradeCosts(...)
啟用買賣分離費率（台股手續費+證交稅等不對稱成本；見 backtest/costs.py）。
costs=None 時走原 fee_bps 邏輯，行為逐位不變（test_costs.py 保護性回歸驗證）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.costs import TradeCosts


@dataclass
class BacktestResult:
    """回測輸出的時間序列。"""

    equity_curve: pd.Series        # 累積淨值（起始 1.0）
    strategy_returns: pd.Series    # 每根 K 棒的策略淨報酬（已扣成本）
    market_returns: pd.Series      # 每根 K 棒的標的報酬（buy & hold 基準）
    position: pd.Series            # 實際持倉（已 shift(1)）
    trades: int                    # 換手次數


def run_backtest(
    data: pd.DataFrame,
    signal: pd.Series,
    fee_bps: float = 5.0,
    initial_equity: float = 1.0,
    shift_signal: bool = True,
    costs: TradeCosts | None = None,
) -> BacktestResult:
    """執行向量化回測。

    Args:
        data: canonical schema，需含 close。
        signal: 與 data index 對齊的目標訊號（{-1,0,+1}）。
        fee_bps: 單邊每次換手的成本（基點，1 bps = 0.01%）。costs 給定時忽略。
        costs: 買賣分離費率（Δposition>0 收 buy_bps、<0 收 sell_bps）。
            None = 沿用 fee_bps 對稱費率（既有加密貨幣路徑，行為逐位不變）。
        initial_equity: 起始淨值。
        shift_signal: 是否對訊號做 shift(1) 防未來函數。**正式回測一律保持 True**；
            設為 False 會引入未來函數，僅供 lookahead bias 自動化檢查使用
            （證明拿掉 shift 後績效會不合理變好），不可用於任何決策。
    """
    if len(data) != len(signal):
        raise ValueError("data 與 signal 長度不一致")
    if not data.index.equals(signal.index):
        raise ValueError("data 與 signal 的 index 必須對齊")

    close = data["close"].astype(float)
    market_returns = close.pct_change().fillna(0.0)

    # === 防未來函數核心：訊號延後一根才轉成持倉 ===
    if shift_signal:
        position = signal.shift(1).fillna(0.0).astype(float)
    else:
        # 故意不延後：第 t 根的訊號吃到第 t 根的報酬 → 未來函數（僅供檢查）
        position = signal.fillna(0.0).astype(float)

    # 換手成本：持倉變動的絕對值 * 單邊費率（買賣可分離；對稱時與舊制逐位相同）
    if costs is None:
        costs = TradeCosts(buy_bps=fee_bps, sell_bps=fee_bps)
    delta = position.diff().fillna(position)
    cost = (
        delta.clip(lower=0.0) * (costs.buy_bps / 10_000.0)
        + (-delta).clip(lower=0.0) * (costs.sell_bps / 10_000.0)
    )

    strategy_returns = position * market_returns - cost
    equity_curve = (1.0 + strategy_returns).cumprod() * initial_equity

    trades = int((position.diff().fillna(position).abs() > 0).sum())

    return BacktestResult(
        equity_curve=equity_curve,
        strategy_returns=strategy_returns,
        market_returns=market_returns,
        position=position,
        trades=trades,
    )
