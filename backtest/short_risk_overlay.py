"""輕量向量化強平近似（Short Forced Liquidation Overlay）。

對空頭倉位套用 risk/manager.py 中 check_forced_liquidation() 同樣的雙層防線，
但以向量化方式近似，而非逐根 event-driven 模擬。

近似邏輯：
  - 對每個連續 -1 倉位區段，找到第一根觸發強平條件的 K 棒
  - 從該 K 棒到訊號自然翻轉前，position 強制設為 0（空手等待）
  - 強平後不模擬再進場（下一個策略訊號出現才重新建倉）

使用限制（文件化，不要悄悄改掉）：
  1. 進場價用「position 第一根的前一根收盤」近似，非真實滑點成交價
  2. 強平後再進場時機用簡化規則（等訊號），非 event-driven 精確處理
  3. 本模組結果只適合評估「有無風控的量級差距」，不適合最佳化風控參數
  4. 完整精確驗證需要 backtest/event_engine.py（Phase 2 工作）

ATR 常數與 risk/manager.py 保持同步：N=3, window=14（2026-07-13 歷史校正
完成，建議維持現值；風控層的正式參數已入 RiskConfig.forced_liq_atr_n /
forced_liq_safety_pct，本模組常數僅供近似 overlay 的預設值）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.report import HOURLY_PERIODS_PER_YEAR, build_report
from backtest.vector_engine import run_backtest
from backtest.walk_forward import FoldResult, WalkForwardSummary
from indicators.technical import atr as compute_atr
from strategy.base import BaseStrategy

_FORCED_LIQ_ATR_N: float = 3.0
_FORCED_LIQ_FIXED_PCT: float = 0.15


def apply_short_forced_liquidation(
    signal: pd.Series,
    close: pd.Series,
    atr_series: pd.Series,
    atr_n: float = _FORCED_LIQ_ATR_N,
    fixed_pct: float = _FORCED_LIQ_FIXED_PCT,
) -> pd.Series:
    """對空頭倉位套用強平近似，回傳已 shift(1) 的倉位序列。

    回傳值應傳給 run_backtest(..., shift_signal=False)，
    因為 shift(1) 已在本函式內完成。

    Args:
        signal: 原始策略訊號（{-1, 0, 1}），尚未 shift。
        close:  與 signal 對齊的收盤價序列。
        atr_series: 與 signal 對齊的 ATR 序列（因果計算，window=14 時前 13 根為 NaN）。
        atr_n:   ATR 動態防線倍率（預設 3.0，與 risk/manager.py 同步）。
        fixed_pct: 固定安全網百分比（預設 15%，與 risk/manager.py 同步）。

    Returns:
        已 shift(1) 且套用強平邏輯的倉位序列。
    """
    if not signal.index.equals(close.index):
        raise ValueError("signal 與 close 的 index 必須對齊")
    if not signal.index.equals(atr_series.index):
        raise ValueError("signal 與 atr_series 的 index 必須對齊")

    # shift(1)：訊號在下一根才生效（與 vector_engine 一致）
    position = signal.shift(1).fillna(0.0)
    pos_arr = position.to_numpy(dtype=float).copy()
    close_arr = close.to_numpy(dtype=float)
    atr_arr = atr_series.to_numpy(dtype=float)
    n = len(pos_arr)

    i = 0
    while i < n:
        if pos_arr[i] != -1.0:
            i += 1
            continue

        # 找出一段連續 -1 的倉位區段
        run_start = i
        while i < n and pos_arr[i] == -1.0:
            i += 1
        run_end = i  # exclusive

        # 進場價近似：run_start 前一根收盤（shift(1) 後第一根 -1 的前一根）
        entry_price = close_arr[run_start - 1] if run_start > 0 else close_arr[run_start]

        # 找第一根觸發強平的 K 棒
        liq_idx: int | None = None
        for t in range(run_start, run_end):
            atr_val = atr_arr[t]
            if np.isnan(atr_val):
                continue
            price = close_arr[t]
            # 空頭強平：價格反彈超過進場價的 ATR 防線或 15% 固定安全網
            if price >= entry_price + atr_n * atr_val:
                liq_idx = t
                break
            if entry_price > 0 and (price - entry_price) / entry_price >= fixed_pct:
                liq_idx = t
                break

        if liq_idx is not None:
            # 強平後空手，等下一個策略訊號
            pos_arr[liq_idx:run_end] = 0.0

    return pd.Series(pos_arr, index=signal.index, name="position_risk_adjusted")


def walk_forward_with_risk_overlay(
    data: pd.DataFrame,
    strategy: BaseStrategy,
    n_splits: int = 5,
    fee_bps: float = 5.0,
    periods_per_year: int = HOURLY_PERIODS_PER_YEAR,
    atr_window: int = 14,
) -> WalkForwardSummary:
    """Walk-forward 回測，對空頭倉位加入強平近似。

    與 walk_forward() 邏輯相同，但在每個 fold 的 run_backtest() 前
    先呼叫 apply_short_forced_liquidation()。

    Args:
        data: canonical schema（需含 ts, open, high, low, close）。
        strategy: 任何 BaseStrategy 實作（訊號需包含 -1）。
        n_splits: walk-forward fold 數。
        fee_bps: 單邊換手成本（基點）。
        periods_per_year: 年化因子（1h=8760）。
        atr_window: ATR 計算窗口（預設 14，與 risk/manager.py 同步）。
    """
    from sklearn.model_selection import TimeSeriesSplit

    if n_splits < 2:
        raise ValueError("n_splits 至少為 2")

    indexed = data.reset_index(drop=True)

    # 用 ts 當 index 對齊 vector_engine 的慣例
    ts_index = indexed["ts"].to_numpy()
    close_df = pd.DataFrame({"close": indexed["close"].to_numpy()}, index=ts_index)

    # 在全段資料上預先計算訊號與 ATR（讓暖機期能用測試窗之前的歷史）
    full_signal = strategy.generate_signals(indexed)
    full_signal.index = ts_index

    full_close = pd.Series(indexed["close"].to_numpy(), index=ts_index, dtype=float)

    # ATR 需要 high/low/close；canonical schema 保證這三欄存在
    atr_input = pd.DataFrame(
        {
            "high": indexed["high"].to_numpy(),
            "low": indexed["low"].to_numpy(),
            "close": indexed["close"].to_numpy(),
        }
    )
    full_atr = compute_atr(atr_input, window=atr_window)
    full_atr.index = ts_index

    tscv = TimeSeriesSplit(n_splits=n_splits)
    positions_idx = np.arange(len(indexed))
    folds: list[FoldResult] = []

    for i, (train_idx, test_idx) in enumerate(tscv.split(positions_idx), start=1):
        test_data = close_df.iloc[test_idx]
        test_signal = full_signal.iloc[test_idx]
        test_close = full_close.iloc[test_idx]
        test_atr = full_atr.iloc[test_idx]

        adj_position = apply_short_forced_liquidation(
            test_signal, test_close, test_atr,
            atr_n=_FORCED_LIQ_ATR_N,
            fixed_pct=_FORCED_LIQ_FIXED_PCT,
        )

        # shift_signal=False：adj_position 已完成 shift(1)
        result = run_backtest(test_data, adj_position, fee_bps=fee_bps, shift_signal=False)
        report = build_report(result, periods_per_year=periods_per_year)

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

    keys = WalkForwardSummary.METRIC_KEYS
    stacked = {k: np.array([getattr(f.report, k) for f in folds]) for k in keys}
    metrics_mean = {k: float(v.mean()) for k, v in stacked.items()}
    metrics_std = {
        k: float(v.std(ddof=1)) if len(v) > 1 else 0.0
        for k, v in stacked.items()
    }

    return WalkForwardSummary(folds=folds, metrics_mean=metrics_mean, metrics_std=metrics_std)
