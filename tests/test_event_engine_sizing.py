"""E2 規則一 sizing（risk_per_trade 比例倉位）整合測試。

2026-07-13 使用者核可的設計（五決策點）：
  ① 只接空頭（多頭維持槓桿上限全倉）——空頭的 N×ATR 停損由強平真實執行，
    多頭無停損機制，sizing 用假停損違背規則一語意。
  ② 決策 bar 的 ATR 為 NaN → fallback 槓桿上限 sizing（機制未定義時不動作）。
  ③ short_risk_multiplier 維持 0.5（風險預算 = 0.5%）。
  ④ event_engine.py 範圍性解鎖：sizing_mode 參數 + 空頭進場數量分支 +
    OrderEvent.stop_distance（預設 None）。預設路徑逐位不變。
  ⑤ 停損距離 = forced_liq_atr_n × ATR[決策bar]（next_open 模式不可用
    fill bar 的 ATR——那含未來資訊）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
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


def _const_atr(n: int, value: float) -> pd.Series:
    return pd.Series(np.full(n, value))


class TestDefaultModeUnchanged:
    def test_explicit_leverage_cap_equals_default(self):
        """sizing_mode 預設 = "leverage_cap"，顯式傳入與省略結果逐位相同。"""
        rng = np.random.default_rng(5)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=200))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=200))
        atr = _const_atr(200, 1.0)

        ev_default = run_event_backtest(
            data, signal, fee_bps=5.0, risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_explicit = run_event_backtest(
            data, signal, fee_bps=5.0, risk=RiskManager(equity=1.0), atr_series=atr,
            sizing_mode="leverage_cap",
        )
        assert np.array_equal(
            ev_default.equity_curve.to_numpy(), ev_explicit.equity_curve.to_numpy()
        )
        assert np.array_equal(
            ev_default.position_qty.to_numpy(), ev_explicit.position_qty.to_numpy()
        )


class TestRule1ShortSizing:
    def test_short_quantity_hand_computed(self):
        """空頭數量 = equity × risk_per_trade_short / (N × ATR)。

        equity=1、risk 0.01×0.5=0.005、N=3、ATR=2 → D=6 → qty=0.005/6。
        """
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=_const_atr(6, 2.0),
            sizing_mode="risk_per_trade",
        )
        entry = ev.fills[0]
        assert entry.quantity == pytest.approx(0.005 / 6.0)
        # 停損距離記錄在 OrderEvent 上（供跑批診斷）
        entry_orders = [o for o in ev.orders if o.reason == "entry_short"]
        assert entry_orders[0].stop_distance == pytest.approx(6.0)

    def test_short_capped_by_rule2_leverage_when_atr_small(self):
        """低波動時規則一數量超過規則二 0.5x 上限 → 被封頂（分工：規則二成為安全繩）。

        ATR=0.02 → D=0.06 → 未封頂名目 0.005/0.06×100=8.33 > 0.5 → 封頂 0.5。
        """
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=_const_atr(6, 0.02),
            sizing_mode="risk_per_trade",
        )
        entry = ev.fills[0]
        assert entry.quantity * entry.price == pytest.approx(0.5)

    def test_long_entry_unchanged_option_a(self):
        """選項A：多頭不走規則一，維持槓桿上限全倉（1.0x）。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 1, 1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=_const_atr(6, 2.0),
            sizing_mode="risk_per_trade",
        )
        entry = ev.fills[0]
        assert entry.quantity * entry.price == pytest.approx(1.0)

    def test_nan_atr_falls_back_to_leverage_cap(self):
        """決策 bar ATR=NaN → 無停損定義 → 退回槓桿上限 sizing（0.5x）。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0),
            atr_series=pd.Series(np.full(6, np.nan)),
            sizing_mode="risk_per_trade",
        )
        entry = ev.fills[0]
        assert entry.quantity * entry.price == pytest.approx(0.5)

    def test_next_open_uses_decision_bar_atr(self):
        """next_open 模式：停損距離用決策 bar 的 ATR，不可用 fill bar 的
        （ATR[t+1] 含 t+1 整根 high/low/close，在 t+1 開盤時是未來資訊）。"""
        close = np.full(8, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, 0])
        atr = pd.Series([2.0, 2.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="next_open",
            risk=RiskManager(equity=1.0), atr_series=atr,
            sizing_mode="risk_per_trade",
        )
        # 決策在 bar1（ATR=2 → D=6）、成交在 open[2]；若誤用 ATR[2]=50 → D=150
        entry = ev.fills[0]
        assert entry.quantity == pytest.approx(0.005 / 6.0)

    def test_realized_loss_vs_budget_on_forced_liq(self):
        """強平時實際權益損失 = 預算 × (實際觸發距離 / D)。

        entry@100、D=6、觸發於 close=109（超越 3）→ 損失 = qty×9 =
        0.005×9/6 = 0.75%（= 預算 0.5% 的 1.5 倍，close-only 超越損失）。
        """
        close = np.array([100.0, 100.0, 100.0, 100.0, 109.0, 109.0, 109.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=_const_atr(7, 2.0),
            sizing_mode="risk_per_trade",
        )
        assert ev.liquidation_bars == [4]
        assert ev.equity_curve.iloc[-1] == pytest.approx(1.0 - 0.005 * 9.0 / 6.0)


class TestValidation:
    def test_risk_per_trade_requires_risk(self):
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        with pytest.raises(ValueError):
            run_event_backtest(
                data, signal, atr_series=_const_atr(6, 2.0),
                sizing_mode="risk_per_trade",
            )

    def test_invalid_sizing_mode_raises(self):
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0, 0, 0])
        with pytest.raises(ValueError):
            run_event_backtest(
                data, signal, risk=RiskManager(equity=1.0),
                atr_series=_const_atr(6, 2.0), sizing_mode="full_yolo",
            )
