"""E2 買賣分離成本（costs）測試（2026-07-16 範圍性解鎖，台股 E2 化第一輪）。

任務定位：`event_engine.py` 原本只有對稱 `fee_bps`（PaperBroker 建構時的
單一費率），台股手續費+證交稅是買賣不對稱結構（見 backtest/costs.py）。
本檔驗證：
  1. `costs=None`（省略）與既有 `fee_bps` 對稱路徑逐位相同（保護性回歸，
     保護 BTC/ETH 既有呼叫端不受影響）。
  2. 非對稱費率確實透傳到每一筆 Fill：BUY 收 buy_bps、SELL 收 sell_bps。
  3. costs 給定時完全接管費率，fee_bps 的值不影響結果。
  4. costs 只影響手續費計算，不影響訊號生成/mark-to-market/熔斷/強平判定
     （比照 slippage_bps 那輪「只調整成交環節」的既有精神）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.costs import TradeCosts
from backtest.event_engine import run_event_backtest
from broker.paper import Side
from risk.manager import RiskManager


def _make_data(close: np.ndarray, start_ts: str = "2026-01-01") -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    ts = pd.date_range(start_ts, periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": open_,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


class TestCostsNoneIsProtectiveRegression:
    def test_costs_none_matches_fee_bps_only_run(self):
        """★保護性回歸★ costs 省略（=None）時，equity/position/fills 與既有
        fee_bps-only 呼叫方式逐位相同——不威脅 BTC/ETH 既有呼叫端。"""
        rng = np.random.default_rng(3)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=150))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=150))

        legacy = run_event_backtest(data, signal, fee_bps=7.0, slippage_bps=0.0)
        explicit_none = run_event_backtest(
            data, signal, fee_bps=7.0, slippage_bps=0.0, costs=None
        )

        assert np.array_equal(
            legacy.equity_curve.to_numpy(), explicit_none.equity_curve.to_numpy()
        )
        assert np.array_equal(
            legacy.position_qty.to_numpy(), explicit_none.position_qty.to_numpy()
        )
        assert [(f.side, f.price, f.fee) for f in legacy.fills] == [
            (f.side, f.price, f.fee) for f in explicit_none.fills
        ]

    def test_symmetric_costs_matches_equal_fee_bps(self):
        """TradeCosts(x, x) 與 fee_bps=x 逐位相同，證明 costs 傳遞路徑本身正確。"""
        rng = np.random.default_rng(4)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=150))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=150))

        legacy = run_event_backtest(data, signal, fee_bps=7.0, slippage_bps=0.0)
        via_costs = run_event_backtest(
            data, signal, slippage_bps=0.0, costs=TradeCosts(buy_bps=7.0, sell_bps=7.0)
        )

        assert np.array_equal(
            legacy.equity_curve.to_numpy(), via_costs.equity_curve.to_numpy()
        )


class TestAsymmetricCostsChargeCorrectSide:
    def test_buy_and_sell_fills_use_respective_bps(self):
        """台股費率：買 14.25bps、賣 44.25bps——BUY/SELL 各筆 Fill 費率正確。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 0, 0, 0])  # bar1 進場(BUY)、bar3 出場(SELL)
        ev = run_event_backtest(
            data, signal, fill_mode="signal_close", slippage_bps=0.0,
            costs=TradeCosts(buy_bps=14.25, sell_bps=44.25),
        )
        buy_fills = [f for f in ev.fills if f.side is Side.BUY]
        sell_fills = [f for f in ev.fills if f.side is Side.SELL]
        assert len(buy_fills) == 1 and len(sell_fills) == 1
        assert buy_fills[0].fee == pytest.approx(
            buy_fills[0].quantity * buy_fills[0].price * 14.25 / 10_000.0
        )
        assert sell_fills[0].fee == pytest.approx(
            sell_fills[0].quantity * sell_fills[0].price * 44.25 / 10_000.0
        )

    def test_costs_overrides_fee_bps_when_both_given(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 0])
        ev = run_event_backtest(
            data, signal, fill_mode="signal_close", slippage_bps=0.0,
            fee_bps=9_999.0, costs=TradeCosts(buy_bps=5.0, sell_bps=5.0),
        )
        buy_fill = ev.fills[0]
        assert buy_fill.fee == pytest.approx(
            buy_fill.quantity * buy_fill.price * 5.0 / 10_000.0
        )


class TestCostsOnlyAffectsFees:
    def test_costs_does_not_change_signal_or_liquidation_trigger(self):
        """costs 只影響手續費計算，不影響強平觸發 bar 集合（觸發依據=close，
        與 costs 無關）——比照 slippage_bps 那輪「機制不變量」驗證精神。"""
        close = np.array([100.0, 100.0, 100.0, 100.0, 109.0, 109.0, 109.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        atr = pd.Series(np.full(7, 2.0))  # N=3 × ATR=2 → 觸發距離 6；close=109 超越

        ev_no_costs = run_event_backtest(
            data, signal, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_with_costs = run_event_backtest(
            data, signal, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
            costs=TradeCosts(buy_bps=14.25, sell_bps=44.25),
        )
        assert ev_no_costs.liquidation_bars == ev_with_costs.liquidation_bars == [4]
