"""事件引擎風控整合（E2）語意驗證。

涵蓋（2026-07-06 設計決策③④⑤⑥）：
  - 強平：15% 固定網 / ATR 動態防線分別觸發；ATR NaN 整根跳過（對齊 overlay）；
    強平後鎖倉到訊號重置、重置後可再進場；next_open 模式強平以次根開盤成交。
  - 熔斷：擋開倉、不擋平倉；跨日自動解除。
  - sizing：多頭 1.0x / 空頭 0.5x（規則二）槓桿上限全倉。
  - 機制對齊：強平觸發 bar 集合與 short_risk_overlay 完全一致（兩層驗收的第一層）。
  - 多空互斥永不被自身流程觸發（先平後開）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
from backtest.short_risk_overlay import apply_short_forced_liquidation
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
            "high": close * 1.01,  # 合成 high/low 供引擎就地計算 ATR
            "low": close * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


def _const_atr(n: int, value: float) -> pd.Series:
    return pd.Series(np.full(n, value))


class TestForcedLiquidation:
    def test_fixed_net_triggers_and_locks_until_signal_reset(self):
        # bar1 進空 @100；bar5 反彈到 120（+20% ≥ 15% 固定網）→ 強平；
        # bar6-8 訊號仍 -1 → 鎖倉不重進；bar9 訊號 0 解鎖；bar10 訊號 -1 重進。
        close = np.array([100.0] * 5 + [120.0] * 8)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, -1, -1, 0, -1, -1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            forced_liquidation=True, atr_series=_const_atr(len(close), 50.0),
        )

        assert ev.liquidation_bars == [5]
        sides = [(f.side, f.price) for f in ev.fills]
        assert sides == [
            (Side.SELL, 100.0),   # bar1 進空
            (Side.BUY, 120.0),    # bar5 強平
            (Side.SELL, 120.0),   # bar10 訊號重置後重進
        ]
        assert (ev.position_qty.iloc[5:10] == 0).all()  # 鎖倉期間空手
        assert ev.position_qty.iloc[10] < 0

    def test_atr_line_triggers_below_fixed_net(self):
        # 進空 @100，ATR=2 → 防線 106；反彈到 107（+7% < 15%）→ 由 ATR 觸發。
        close = np.array([100.0, 100.0, 100.0, 107.0, 107.0, 107.0])
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            forced_liquidation=True, atr_series=_const_atr(len(close), 2.0),
        )

        assert ev.liquidation_bars == [3]

    def test_nan_atr_skips_whole_check(self):
        # ATR 全 NaN → 連 15% 固定網也不檢查（對齊 overlay 行為）。
        close = np.array([100.0, 100.0, 100.0, 125.0, 125.0, 125.0])
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            forced_liquidation=True,
            atr_series=pd.Series(np.full(len(close), np.nan)),
        )

        assert ev.liquidation_bars == []
        assert ev.position_qty.iloc[4] < 0  # 空單仍在

    def test_next_open_liquidation_fills_at_next_bar_open(self):
        close = np.array([100.0] * 3 + [120.0, 121.0, 122.0, 123.0])
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="next_open", slippage_bps=0.0,
            forced_liquidation=True, atr_series=_const_atr(len(close), 50.0),
        )

        # 進空成交於 open[2]（bar1 決策）；bar3 close=120 觸發 → 成交於 open[4]
        assert ev.liquidation_bars == [3]
        liq_fill = [f for f in ev.fills if f.side is Side.BUY][0]
        assert liq_fill.ts == data["ts"].iloc[4]
        assert liq_fill.price == pytest.approx(float(data["open"].iloc[4]))


class TestTriggerBarsMatchOverlay:
    """兩層驗收第一層：同觸發定義（close[t]）下，觸發 bar 集合必須與
    short_risk_overlay 逐一相同。"""

    @staticmethod
    def _overlay_trigger_bars(signal: pd.Series, adj: pd.Series) -> list[int]:
        """從 overlay 輸出推導觸發 bar：-1 倉位區段內第一個被強制歸零的 bar。"""
        shifted = signal.shift(1).fillna(0.0).to_numpy()
        adj_arr = adj.to_numpy()
        bars: list[int] = []
        in_zeroed = False
        for t in range(len(adj_arr)):
            if shifted[t] == -1.0 and adj_arr[t] == 0.0:
                if not in_zeroed:
                    bars.append(t)
                    in_zeroed = True
            else:
                in_zeroed = False
        return bars

    def test_trigger_sets_identical_on_volatile_path(self):
        rng = np.random.default_rng(17)
        n = 400
        rets = rng.normal(0.0, 0.02, size=n)
        # 在幾個空頭區段內插入強力反彈，確保有觸發可比
        for k in (60, 110, 250, 320):
            rets[k] = 0.09
        close = 100.0 * np.cumprod(1.0 + rets)
        data = _make_data(close)

        values = np.zeros(n, dtype=int)
        values[40:90] = -1
        values[100:160] = -1
        values[230:280] = -1
        values[300:360] = -1
        signal = pd.Series(values)

        # ATR 因果計算（high/low 以 close 上下 1% 合成）
        from indicators.technical import atr as compute_atr

        ohlc = pd.DataFrame(
            {"high": close * 1.01, "low": close * 0.99, "close": close}
        )
        atr_series = compute_atr(ohlc, window=14)

        adj = apply_short_forced_liquidation(signal, pd.Series(close), atr_series)
        overlay_bars = self._overlay_trigger_bars(signal, adj)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            forced_liquidation=True, atr_series=atr_series,
        )

        assert len(overlay_bars) > 0  # 測試要有料
        assert ev.liquidation_bars == overlay_bars


class TestCircuitBreaker:
    def test_blocks_entry_not_exit_and_resets_next_day(self):
        # day1 20:00 起：bar1 進多 @100 → bar2 收 94（日內 -6% 熔斷）
        # bar2 訊號 0 → 平倉單須照常成交；bar3 訊號 1 → 拒單；
        # bar4 跨日 → 解除熔斷，進場成功。
        close = np.array([100.0, 100.0, 94.0, 94.0, 94.0, 94.0])
        signal = pd.Series([0, 1, 0, 1, 1, 0])
        data = _make_data(close, start_ts="2026-01-01 20:00")

        risk = RiskManager(equity=1.0)
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", risk=risk,
        )

        assert [(r[0], r[2], r[3]) for r in ev.rejections] == [
            (3, "entry_long", "rejected_circuit_breaker")
        ]
        fill_kinds = [(f.side, f.ts) for f in ev.fills]
        assert fill_kinds == [
            (Side.BUY, data["ts"].iloc[1]),   # 進多
            (Side.SELL, data["ts"].iloc[2]),  # 熔斷日平倉照常成交
            (Side.BUY, data["ts"].iloc[4]),   # 跨日解除後進場
        ]
        assert ev.tripped_days == [data["ts"].iloc[2].date()]


class TestSizing:
    def test_short_entry_uses_half_leverage(self):
        close = np.full(6, 100.0)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0),
        )

        entry = ev.fills[0]
        assert entry.quantity * entry.price == pytest.approx(0.5)  # 規則二

    def test_long_entry_uses_full_leverage(self):
        close = np.full(6, 100.0)
        signal = pd.Series([0, 1, 1, 1, 1, 0])
        data = _make_data(close)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0),
        )

        entry = ev.fills[0]
        assert entry.quantity * entry.price == pytest.approx(1.0)

    def test_conflicting_position_never_rejected(self):
        """先平後開的引擎流程下，多空互斥拒單不應出現。"""
        rng = np.random.default_rng(23)
        n = 300
        close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=n))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=n))

        for mode in ("signal_close", "next_open"):
            ev = run_event_backtest(
                data, signal, fee_bps=5.0, fill_mode=mode,
                risk=RiskManager(equity=1.0),
            )
            reasons = {r[3] for r in ev.rejections}
            assert "rejected_conflicting_position" not in reasons
