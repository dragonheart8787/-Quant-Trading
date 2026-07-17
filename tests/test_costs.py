"""買賣分離交易成本測試（backtest/costs.py + vector_engine/walk_forward 整合）。

重點：
- **保護性回歸（本檔最重要的測試）**：對稱費率 TradeCosts(x, x) 必須與舊制
  fee_bps=x **逐位（bitwise）相同**——這是「為台股加真實成本，不改壞加密貨幣
  既有驗證結果」的關鍵防線。含 {0,1} long-only 與 {-1,0,+1} 雙向兩種訊號。
- 非對稱費率落在正確方向：Δposition>0 收 buy_bps、Δposition<0 收 sell_bps。
- 台股費率常數：牌告 14.25bps 買賣皆收；賣出另加證交稅 30bps；折扣僅作用於
  手續費、不作用於證交稅。
- walk_forward 透傳 costs 參數（實際到達引擎，不是被默默忽略）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.costs import CRYPTO_DEFAULT, TradeCosts, tw_stock_costs
from backtest.vector_engine import run_backtest
from backtest.walk_forward import walk_forward
from data.cleaning import CANONICAL_COLUMNS
from strategy.ma_rsi import MaRsiStrategy


def _price_df(prices: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame({"close": prices}, index=idx)


def _daily_canonical(close: np.ndarray) -> pd.DataFrame:
    """close 陣列 → 合法日K canonical（walk_forward 用）。"""
    n = len(close)
    ts = pd.date_range("2024-01-02 01:00:00", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({
        "ts": ts,
        "symbol": "TEST",
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
        "volume": 1000.0, "turnover": close * 1000.0, "vwap": close,
        "exchange": "TWSE", "interval": "1d", "source": "test",
        "ingested_at": ts,
    })
    return df[CANONICAL_COLUMNS]


def test_tw_stock_costs_values():
    """牌告無折扣：買 14.25bps、賣 14.25+30=44.25bps；折扣只砍手續費不砍證交稅。"""
    c = tw_stock_costs()
    assert c.buy_bps == pytest.approx(14.25)
    assert c.sell_bps == pytest.approx(44.25)

    d = tw_stock_costs(fee_discount=0.6)
    assert d.buy_bps == pytest.approx(14.25 * 0.6)
    assert d.sell_bps == pytest.approx(14.25 * 0.6 + 30.0)

    assert CRYPTO_DEFAULT.buy_bps == 5.0 and CRYPTO_DEFAULT.sell_bps == 5.0


def test_symmetric_costs_bitwise_equals_legacy_long_only():
    """★保護性回歸★ TradeCosts(5,5) 與 fee_bps=5 逐位相同（{0,1} 訊號）。"""
    rng = np.random.default_rng(42)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 300)))
    df = _price_df(prices)
    sig = pd.Series(rng.integers(0, 2, 300), index=df.index)

    legacy = run_backtest(df, sig, fee_bps=5.0)
    new = run_backtest(df, sig, costs=TradeCosts(5.0, 5.0))

    assert np.array_equal(legacy.strategy_returns.to_numpy(), new.strategy_returns.to_numpy())
    assert np.array_equal(legacy.equity_curve.to_numpy(), new.equity_curve.to_numpy())
    assert legacy.trades == new.trades


def test_symmetric_costs_bitwise_equals_legacy_bidirectional():
    """★保護性回歸★ 同上，但 {-1,0,+1} 雙向訊號（保護加密貨幣 v2 空頭路徑）。"""
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 300)))
    df = _price_df(prices)
    sig = pd.Series(rng.integers(-1, 2, 300), index=df.index)

    legacy = run_backtest(df, sig, fee_bps=5.0)
    new = run_backtest(df, sig, costs=TradeCosts(5.0, 5.0))

    assert np.array_equal(legacy.strategy_returns.to_numpy(), new.strategy_returns.to_numpy())
    assert np.array_equal(legacy.equity_curve.to_numpy(), new.equity_curve.to_numpy())
    assert legacy.trades == new.trades


def test_asymmetric_costs_charge_correct_side():
    """常數價格（market_returns=0）→ strategy_returns = -成本，逐 bar 驗證方向：
    Δposition>0 的 bar 收 buy_bps、Δposition<0 的 bar 收 sell_bps。"""
    df = _price_df(np.full(10, 100.0))
    sig = pd.Series([0, 1, 1, 0, 0, 0, 0, 0, 0, 0], index=df.index)
    # shift(1) 後 position = [0,0,1,1,0,0,...]：bar2 買進（Δ=+1）、bar4 賣出（Δ=-1）
    res = run_backtest(df, sig, costs=TradeCosts(buy_bps=10.0, sell_bps=40.0))
    r = res.strategy_returns.to_numpy()

    assert r[2] == pytest.approx(-10.0 / 10_000.0)   # 買進端
    assert r[4] == pytest.approx(-40.0 / 10_000.0)   # 賣出端
    assert np.all(r[[0, 1, 3, 5, 6, 7, 8, 9]] == 0.0)

    # 一買一賣完整回合：終點淨值 = (1-買費)(1-賣費)
    assert res.equity_curve.iloc[-1] == pytest.approx((1 - 0.0010) * (1 - 0.0040))


def test_walk_forward_passes_costs_through():
    """costs 參數要實際到達引擎：對稱 costs 與同值 fee_bps 彙總逐位相同；
    高費率 costs 與 5bps 顯著不同（證明不是被忽略）。"""
    i = np.arange(120)
    close = 100.0 + 0.5 * i + 7.0 * np.sin(i / 4.0)
    data = _daily_canonical(close)
    strat = MaRsiStrategy(5, 20, 14)

    wf_legacy = walk_forward(data, strat, n_splits=3, fee_bps=5.0, periods_per_year=252)
    wf_sym = walk_forward(data, strat, n_splits=3, costs=TradeCosts(5.0, 5.0),
                          periods_per_year=252)
    assert wf_legacy.metrics_mean == wf_sym.metrics_mean
    assert wf_legacy.metrics_std == wf_sym.metrics_std

    wf_heavy = walk_forward(data, strat, n_splits=3, costs=TradeCosts(500.0, 500.0),
                            periods_per_year=252)
    assert wf_heavy.metrics_mean["annual_return"] < wf_legacy.metrics_mean["annual_return"]
