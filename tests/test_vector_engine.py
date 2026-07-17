"""向量化回測引擎測試，重點驗證防未來函數（shift(1)）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.report import build_report, max_drawdown
from backtest.vector_engine import run_backtest


def _make_data(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1h", tz="UTC")
    return pd.DataFrame({"close": prices}, index=idx)


def test_no_lookahead_signal_is_shifted():
    """第 t 根的訊號只能影響第 t+1 根的報酬。

    價格在第 1→2 根大漲。若訊號在第 1 根變 1，
    持倉要到第 2 根才生效，因此第 2 根的漲幅才該被賺到。
    """
    data = _make_data([100.0, 100.0, 110.0, 110.0])
    # 第 1 根（index 1）才出現多單訊號
    signal = pd.Series([0, 1, 1, 1], index=data.index)

    result = run_backtest(data, signal, fee_bps=0.0)

    # position 應是 signal.shift(1) → [NaN→0, 0, 1, 1]
    assert result.position.tolist() == [0.0, 0.0, 1.0, 1.0]
    # 第 2 根（index 2）報酬 = 持倉(1) * 10% = 0.10
    assert result.strategy_returns.iloc[2] == pytest.approx(0.10)
    # 第 1 根還沒持倉，賺不到任何東西
    assert result.strategy_returns.iloc[1] == pytest.approx(0.0)


def test_perfect_foresight_would_differ_without_shift():
    """健全性檢查：策略報酬不可等於用『當根訊號 * 當根報酬』的未來函數版本。

    訊號在第 1 根（index 1）才轉多。作弊版（無 shift）會在第 1 根就賺到當根
    10% 漲幅；正確版因 shift(1)，持倉到第 2 根才生效。
    """
    data = _make_data([100.0, 110.0, 121.0])
    signal = pd.Series([0, 1, 1], index=data.index)
    result = run_backtest(data, signal, fee_bps=0.0)
    market = data["close"].pct_change().fillna(0.0)
    lookahead = signal * market  # 作弊版（用到當根才知道的訊號）
    # 第 1 根：作弊版賺到 10%，正確版（持倉仍為 0）賺 0
    assert lookahead.iloc[1] == pytest.approx(0.10)
    assert result.strategy_returns.iloc[1] == pytest.approx(0.0)
    # 第 2 根：正確版才吃到 +10% 漲幅
    assert result.strategy_returns.iloc[2] == pytest.approx(121.0 / 110.0 - 1.0)


def test_fees_reduce_returns():
    data = _make_data([100.0, 100.0, 100.0])
    signal = pd.Series([1, 1, 0], index=data.index)
    with_fee = run_backtest(data, signal, fee_bps=10.0)
    no_fee = run_backtest(data, signal, fee_bps=0.0)
    assert with_fee.equity_curve.iloc[-1] < no_fee.equity_curve.iloc[-1]
    assert with_fee.trades >= 1


def test_max_drawdown():
    eq = pd.Series([1.0, 1.2, 0.9, 1.1])
    # 從 1.2 跌到 0.9 → -25%
    assert max_drawdown(eq) == pytest.approx(-0.25)


def test_report_buy_and_hold_matches_market():
    """一路持倉(訊號全1，shift後僅首根空手)，扣除首根後報酬應貼近買進持有。"""
    prices = [100.0 * (1.01 ** i) for i in range(50)]  # 每根漲 1%
    data = _make_data(prices)
    signal = pd.Series(1, index=data.index)
    result = run_backtest(data, signal, fee_bps=0.0)
    report = build_report(result)
    assert report.num_bars == 50
    assert report.total_return > 0
    assert report.win_rate > 0.9  # 幾乎每根持倉都在漲
    assert -1.0 <= report.max_drawdown <= 0.0
