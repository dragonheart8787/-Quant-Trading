"""台股放空執行規則測試（2026-07-16 範圍性解鎖，第四次）。

兩個新選配參數（預設關閉 = 逐位不變，保護所有既有呼叫端）：
  1. `short_uptick_rule_drop`：3.5% 跌幅次日禁空規則（成交時點檢查，
     非 approve_order——執行約束不是風控約束）。對在 bar f 成交的
     entry_short 單：若 close[f-1]/close[f-2]-1 <= -threshold 且
     成交價 < close[f-1]，該單作廢、記入 blocked_fills。
  2. `lock_up`/`lock_down`：漲跌停全日鎖死布林序列（runner 用原始價
     計算後傳入，引擎不含市場知識）。方向感知：BUY 在漲停鎖死 bar
     無法成交、SELL 在跌停鎖死 bar 無法成交。風險縮減單（exit/
     forced_liquidation）延後重試到下一根未鎖死 bar；新倉單作廢。

診斷欄位：blocked_fills（執行時攔截，區別於決策時的 rejections）、
deferred_fills（延後成交的 (原定bar, 實際bar, reason)）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
from broker.paper import Side
from risk.manager import RiskManager


def _make_data(close: np.ndarray, start_ts: str = "2026-01-01") -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    ts = pd.date_range(start_ts, periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.01,
            "low": np.minimum(open_, close) * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


def _const_atr(n: int, value: float) -> pd.Series:
    return pd.Series(np.full(n, value))


def _locks(n: int, up: list[int] | None = None, down: list[int] | None = None):
    lock_up = pd.Series(np.zeros(n, dtype=bool))
    lock_down = pd.Series(np.zeros(n, dtype=bool))
    for i in up or []:
        lock_up.iloc[i] = True
    for i in down or []:
        lock_down.iloc[i] = True
    return lock_up, lock_down


# ---------------------------------------------------------------------------
# 保護性回歸：新參數省略/中性值時行為不變
# ---------------------------------------------------------------------------

class TestDefaultOffIsNeutral:
    def test_omitted_equals_neutral_values(self):
        """省略新參數 與 傳入中性值（全 False 鎖死表 + uptick 啟用但資料
        無限制日）結果逐位相同——證明 wiring 本身零污染。"""
        rng = np.random.default_rng(9)
        # 控制單日波動 < 3.5%，確保 uptick 規則永不觸發
        close = 100.0 * np.cumprod(1.0 + rng.uniform(-0.02, 0.02, size=120))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=120))
        atr = _const_atr(120, 1.0)
        lock_up, lock_down = _locks(120)

        ev_omitted = run_event_backtest(
            data, signal, fee_bps=5.0, slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_neutral = run_event_backtest(
            data, signal, fee_bps=5.0, slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
            short_uptick_rule_drop=0.035, lock_up=lock_up, lock_down=lock_down,
        )
        assert np.array_equal(
            ev_omitted.equity_curve.to_numpy(), ev_neutral.equity_curve.to_numpy()
        )
        assert np.array_equal(
            ev_omitted.position_qty.to_numpy(), ev_neutral.position_qty.to_numpy()
        )
        assert ev_neutral.blocked_fills == []
        assert ev_neutral.deferred_fills == []

    def test_result_has_empty_diagnostics_by_default(self):
        close = np.full(5, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 0, 0])
        ev = run_event_backtest(data, signal, fee_bps=0.0, slippage_bps=0.0)
        assert ev.blocked_fills == []
        assert ev.deferred_fills == []


# ---------------------------------------------------------------------------
# 3.5% 禁空規則（uptick rule）
# ---------------------------------------------------------------------------

class TestUptickRule:
    def test_short_entry_blocked_below_prev_close_after_drop(self):
        """前一 bar 跌 4%（>3.5%），成交價（open）低於前收 → entry_short 作廢。"""
        close = np.array([100.0, 96.0, 94.0, 93.0, 93.0, 93.0])
        data = _make_data(close)
        data.loc[2, "open"] = 95.0  # fill bar2 open=95 < close[1]=96 → 違規
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            short_uptick_rule_drop=0.035,
        )
        assert [(b, r, why) for b, _, r, why in ev.blocked_fills] == [
            (2, "entry_short", "uptick_rule")
        ]
        # 訊號持續 -1：bar2 決策時仍空手 → 再產生進場單，bar3 open=94 < close[2]=94?
        # close[2]/close[1]-1 = 94/96-1 = -2.08% < 3.5% 門檻 → bar3 不受限 → 成交
        assert len(ev.fills) >= 1
        assert ev.fills[0].side is Side.SELL

    def test_short_entry_allowed_at_or_above_prev_close(self):
        """跌幅達標但成交價 >= 前收 → 不擋（規則是價格條件式，非全面禁令）。"""
        close = np.array([100.0, 96.0, 97.0, 95.0, 95.0, 95.0])
        data = _make_data(close)
        data.loc[2, "open"] = 96.5  # >= close[1]=96 → 合規
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            short_uptick_rule_drop=0.035,
        )
        assert ev.blocked_fills == []
        assert ev.fills[0].side is Side.SELL
        assert ev.fills[0].price == pytest.approx(96.5)

    def test_no_block_when_drop_below_threshold(self):
        """前一 bar 只跌 3%（<3.5%）→ 即使成交價低於前收也不擋。"""
        close = np.array([100.0, 97.0, 95.0, 95.0, 95.0, 95.0])
        data = _make_data(close)
        data.loc[2, "open"] = 96.0  # < close[1]=97，但跌幅未達標
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            short_uptick_rule_drop=0.035,
        )
        assert ev.blocked_fills == []
        assert len(ev.fills) >= 1

    def test_covering_buy_not_restricted(self):
        """規則只限制放空（entry_short），回補（exit BUY）不受限。"""
        close = np.array([100.0, 100.0, 100.0, 96.0, 92.0, 92.0, 92.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, 0, 0, 0])  # bar3 決策出場，bar4 成交
        # bar3 跌 4%（96→92 於 bar4？close[3]/close[2]=96/100=-4%），bar4 為受限日
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            short_uptick_rule_drop=0.035,
        )
        buys = [f for f in ev.fills if f.side is Side.BUY]
        assert len(buys) == 1  # 回補正常成交，未被 uptick 規則攔截
        assert all(why != "uptick_rule" or r != "exit" for _, _, r, why in ev.blocked_fills)

    def test_negative_threshold_raises(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, short_uptick_rule_drop=-0.01)


# ---------------------------------------------------------------------------
# 漲跌停鎖死耦合（lock_up / lock_down）
# ---------------------------------------------------------------------------

class TestLimitLockCoupling:
    def test_forced_liq_deferred_on_lock_up_and_fills_next_bar(self):
        """強平單（BUY 回補）在漲停鎖死 bar 延後，下一根未鎖死 bar 成交；
        延後期間權益持續失血（mark-to-market 照常）。"""
        close = np.array([100.0, 100.0, 100.0, 107.0, 109.0, 109.0, 100.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        atr = _const_atr(7, 2.0)  # N=3 × 2 = 6 → close[3]=107 ≥ 106 觸發
        lock_up, lock_down = _locks(7, up=[4])  # 原定成交 bar4 漲停鎖死

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
            lock_up=lock_up, lock_down=lock_down,
        )
        assert ev.liquidation_bars == [3]           # 觸發判定不受鎖死影響
        assert ev.deferred_fills == [(4, 5, "forced_liquidation")]
        # 回補實際成交在 bar5 open = close[4] = 109（比未鎖死情境的 107 更差）
        liq_fill = [f for f in ev.fills if f.side is Side.BUY][-1]
        assert liq_fill.price == pytest.approx(109.0)
        # 延後那根（bar4 close=109 vs bar3 close=107）空單繼續失血
        assert ev.equity_curve.iloc[4] < ev.equity_curve.iloc[3]
        # 不重複產生強平單
        n_liq_orders = sum(1 for o in ev.orders if o.reason == "forced_liquidation")
        assert n_liq_orders == 1

    def test_entry_dropped_on_locked_bar_then_refilled_by_persisting_signal(self):
        """新倉單在鎖死 bar 作廢（不延後）；訊號若持續，下一根自然重新進場。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 1, 0, 0])
        lock_up, lock_down = _locks(6, up=[2])  # entry_long 原定 bar2 成交，BUY 被鎖

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            lock_up=lock_up, lock_down=lock_down,
        )
        assert (2, "entry_long", "limit_lock") in [
            (b, r, why) for b, _, r, why in ev.blocked_fills
        ]
        # bar2 決策時仍空手、訊號仍 1 → 重新產生進場單 → bar3 成交
        assert len(ev.fills) >= 1
        assert ev.position_qty.iloc[3] > 0

    def test_long_exit_deferred_on_lock_down(self):
        """多單出場（SELL）在跌停鎖死 bar 延後——方向感知的另一側。"""
        close = np.array([100.0, 100.0, 100.0, 100.0, 90.0, 90.0, 90.0])
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 0, 0, 0, 0])  # bar3 決策出場 → bar4 成交
        lock_up, lock_down = _locks(7, down=[4])

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            lock_up=lock_up, lock_down=lock_down,
        )
        assert ev.deferred_fills == [(4, 5, "exit")]
        sells = [f for f in ev.fills if f.side is Side.SELL]
        assert len(sells) == 1

    def test_entry_blocked_while_risk_reduce_deferred(self):
        """出場單延後未成交期間，新倉單（即使方向不被鎖死）也不得成交，
        防止舊倉未平、新倉疊加（記為 pending_risk_reduction）。"""
        close = np.full(8, 100.0)
        data = _make_data(close)
        # bar1 進空(bar2成交)、bar3 出場(bar4成交但鎖死)、bar4 訊號又 -1
        signal = pd.Series([0, -1, -1, 0, -1, -1, 0, 0])
        lock_up, lock_down = _locks(8, up=[4, 5])  # bar4/5 漲停鎖死，BUY 出場連兩根延後

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            lock_up=lock_up, lock_down=lock_down,
        )
        # bar4 決策的 entry_short 原定 bar5 成交（SELL 不被 lock_up 鎖），
        # 但出場單仍在延後 → 記 pending_risk_reduction
        assert ("entry_short", "pending_risk_reduction") in [
            (r, why) for _, _, r, why in ev.blocked_fills
        ]
        # 出場最終在 bar6 成交
        assert (4, 6, "exit") in ev.deferred_fills
        # 空單從未被疊加（最小持倉 = 單筆空單量，不是兩倍）
        first_short_qty = ev.fills[0].quantity
        assert ev.position_qty.min() == pytest.approx(-first_short_qty)

    def test_signal_close_mode_liq_deferral(self):
        """signal_close 模式：強平在觸發 bar 收盤被鎖死 → 延後到下一根收盤成交。"""
        close = np.array([100.0, 100.0, 100.0, 107.0, 108.0, 100.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        atr = _const_atr(6, 2.0)
        lock_up, lock_down = _locks(6, up=[3])  # 觸發 bar3 當根收盤鎖死

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=atr,
            lock_up=lock_up, lock_down=lock_down,
        )
        assert ev.liquidation_bars == [3]
        assert ev.deferred_fills == [(3, 4, "forced_liquidation")]
        liq_fill = [f for f in ev.fills if f.side is Side.BUY][-1]
        assert liq_fill.price == pytest.approx(108.0)  # bar4 close

    def test_lock_series_index_mismatch_raises(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])
        bad_lock = pd.Series([False] * 4, index=[10, 11, 12, 13])
        good_lock = pd.Series([False] * 4)
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, lock_up=bad_lock, lock_down=good_lock)

    def test_lock_series_must_come_in_pairs(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, lock_up=pd.Series([False] * 4))
