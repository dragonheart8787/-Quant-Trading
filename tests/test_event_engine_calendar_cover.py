"""強制回補日曆機制測試（2026-07-16 第五次範圍性解鎖，item 3）。

兩個新選配參數（預設 None=關閉=逐位不變）：
  - `short_entry_ban`：停券窗布林序列——窗內 entry_short 成交作廢
    （blocked_fills reason="short_sale_suspension"）。BUY 回補不受限
    （停券恰恰是要你回補）。
  - `forced_cover_deadline`：最後回補日布林序列——成交 bar 為死線日且
    仍持有空頭 → 引擎產生 reason="calendar_forced_cover" 的 BUY 平倉單，
    記入 calendar_cover_bars（與價格驅動的 liquidation_bars 完全平行、
    歸因分離）。

★非未來函數的文件性斷言★：引擎在 bar t 決策時使用 t+1 的日曆旗標
（next_open 模式）**不是未來函數 bug**——停券日程是數週前公告的公開資訊
（51/52 筆與除權息日交叉驗證確認事前公告制），布林序列代表「當時已公告的
日程」；與價格類資訊的因果紀律（shift(1)）是兩回事。這是本專案第二個
「合法非因果例外」（第一個=還原股價），分類標準見 AGENTS.md
「使用未來已公告資訊的合法性分類」節。
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


def _flags(n: int, true_at: list[int]) -> pd.Series:
    s = pd.Series(np.zeros(n, dtype=bool))
    for i in true_at:
        s.iloc[i] = True
    return s


class TestDefaultOffIsNeutral:
    def test_omitted_equals_all_false(self):
        rng = np.random.default_rng(13)
        close = 100.0 * np.cumprod(1.0 + rng.uniform(-0.02, 0.02, size=100))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=100))
        ev_omit = run_event_backtest(data, signal, fee_bps=5.0, slippage_bps=0.0)
        ev_false = run_event_backtest(
            data, signal, fee_bps=5.0, slippage_bps=0.0,
            short_entry_ban=_flags(100, []), forced_cover_deadline=_flags(100, []),
        )
        assert np.array_equal(
            ev_omit.equity_curve.to_numpy(), ev_false.equity_curve.to_numpy()
        )
        assert ev_false.calendar_cover_bars == []


class TestShortEntryBan:
    def test_entry_short_dropped_inside_ban_window(self):
        """停券窗內的 entry_short 成交作廢；窗結束後訊號持續 → 自然重新進場。"""
        close = np.full(8, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, 0])
        # entry 決策 bar1 → fill bar2；bar2~3 為停券窗
        ban = _flags(8, [2, 3])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0, short_entry_ban=ban,
        )
        blocked = [(b, r, why) for b, _, r, why in ev.blocked_fills]
        assert (2, "entry_short", "short_sale_suspension") in blocked
        assert (3, "entry_short", "short_sale_suspension") in blocked
        # bar3 決策（仍空手、仍 -1）→ fill bar4 已出窗 → 成交
        sells = [f for f in ev.fills if f.side is Side.SELL]
        assert len(sells) == 1
        assert ev.position_qty.iloc[4] < 0

    def test_covering_buy_not_banned_inside_window(self):
        """停券窗內 BUY 回補完全正常（停券恰恰是要你回補）。"""
        close = np.full(7, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, 0, 0, 0, 0])  # bar3 決策出場 → fill bar4
        ban = _flags(7, [4, 5])  # 出場成交日在停券窗內
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0, short_entry_ban=ban,
        )
        buys = [f for f in ev.fills if f.side is Side.BUY]
        assert len(buys) == 1  # 回補正常成交
        assert all(why != "short_sale_suspension" for _, _, _, why in ev.blocked_fills)


class TestForcedCoverDeadline:
    def test_short_covered_on_deadline_bar(self):
        """空頭部位在死線日（成交 bar）被 calendar_forced_cover 平掉；
        訊號仍 -1 也一樣（日曆優先於訊號）。"""
        close = np.full(8, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, 0])
        deadline = _flags(8, [5])  # bar5 = 最後回補日
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline,
        )
        assert ev.calendar_cover_bars == [5]
        cover_orders = [o for o in ev.orders if o.reason == "calendar_forced_cover"]
        assert len(cover_orders) == 1
        # 部位在 bar5 收盤時已平（fill 於 bar5 開盤）
        assert ev.position_qty.iloc[5] == pytest.approx(0.0)
        # 死線日之前部位正常存在
        assert ev.position_qty.iloc[4] < 0

    def test_attribution_separated_from_price_liquidation(self):
        """歸因分離：日曆回補記 calendar_cover_bars、不進 liquidation_bars；
        reason 字串不同。"""
        close = np.full(8, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, 0])
        deadline = _flags(8, [5])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=pd.Series(np.full(8, 2.0)),
            forced_cover_deadline=deadline,
        )
        assert ev.calendar_cover_bars == [5]
        assert ev.liquidation_bars == []  # 價格從未觸發，價格防線清單必須為空
        reasons = {o.reason for o in ev.orders}
        assert "calendar_forced_cover" in reasons
        assert "forced_liquidation" not in reasons

    def test_price_liq_in_flight_skips_calendar_cover(self):
        """價格強平已在途時跳過日曆單（部位已在回補程序中，不重複下單）。"""
        close = np.array([100.0, 100.0, 100.0, 109.0, 109.0, 100.0, 100.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        atr = pd.Series(np.full(7, 2.0))  # N=3×2=6 → bar3 close=109 觸發價格強平
        deadline = _flags(7, [4])  # 價格強平 fill bar4 恰為死線日
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
            forced_cover_deadline=deadline,
        )
        assert ev.liquidation_bars == [3]           # 價格防線先觸發
        assert ev.calendar_cover_bars == []         # 日曆單不重複產生
        n_buys = sum(1 for f in ev.fills if f.side is Side.BUY)
        assert n_buys == 1                          # 只有一張回補單

    def test_deadline_cover_deferred_by_limit_lock(self):
        """死線日撞漲停鎖死（軋空高峰的真實情境）：日曆回補單延後重試、
        期間持續失血——與鎖死延後語意自動組合。"""
        close = np.array([100.0, 100.0, 100.0, 100.0, 109.0, 110.0, 100.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        deadline = _flags(7, [4])
        lock_up = _flags(7, [4])   # 死線日整天漲停鎖死
        lock_down = _flags(7, [])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline, lock_up=lock_up, lock_down=lock_down,
        )
        assert ev.calendar_cover_bars == [4]
        assert (4, 5, "calendar_forced_cover") in ev.deferred_fills
        cover_fill = [f for f in ev.fills if f.side is Side.BUY][-1]
        assert cover_fill.price == pytest.approx(109.0)  # bar5 open = close[4]
        # 死線日（bar4）收盤仍持有空頭、繼續失血
        assert ev.position_qty.iloc[4] < 0
        assert ev.equity_curve.iloc[4] < ev.equity_curve.iloc[3]

    def test_no_reentry_during_ban_after_deadline_cover(self):
        """死線回補後、停券窗未結束前，訊號持續 -1 也不得再進空
        （窗內 entry ban 自動擋住）；窗結束後可重新進場。"""
        close = np.full(9, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, -1, -1, 0])
        deadline = _flags(9, [4])
        ban = _flags(9, [4, 5, 6])  # 停券窗 = bar4~6（死線=窗起始）
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline, short_entry_ban=ban,
        )
        assert ev.calendar_cover_bars == [4]
        # 窗內重進場嘗試被擋
        assert any(why == "short_sale_suspension" for _, _, _, why in ev.blocked_fills)
        # 窗結束後（bar7）重新進空
        assert ev.position_qty.iloc[7] < 0

    def test_signal_close_mode_deadline(self):
        """signal_close 模式：死線 = 決策 bar 本身（同 bar 收盤成交）。"""
        close = np.full(7, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        deadline = _flags(7, [4])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0, fill_mode="signal_close",
            forced_cover_deadline=deadline,
        )
        assert ev.calendar_cover_bars == [4]
        assert ev.position_qty.iloc[4] == pytest.approx(0.0)

    def test_flat_position_on_deadline_is_noop(self):
        """死線日空手 → 不產生任何單（機制只處理既有空頭）。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0, 0, 0])
        deadline = _flags(6, [3])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline,
        )
        assert ev.calendar_cover_bars == []
        assert ev.fills == []

    def test_index_mismatch_raises(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])
        bad = pd.Series([False] * 4, index=[7, 8, 9, 10])
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, short_entry_ban=bad)
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, forced_cover_deadline=bad)


class TestPreannouncedCalendarIsNotLookahead:
    def test_calendar_flags_are_preannounced_information_not_lookahead(self):
        """★文件性斷言★：next_open 模式下，bar t 的決策讀取 t+1 的日曆旗標
        是**刻意設計**——停券日程數週前公告，t 時點「已知」t+1 是死線日，
        這與用 t+1 的價格做決策（真正的未來函數）本質不同。本測試釘住
        「決策 bar 早於死線 bar 一根」這個行為：若未來有人誤判為未來函數
        bug 而改成「死線當根才決策」，會使回補成交落到死線次日、違反
        「（含）當日前完成」的真實規則，此測試會失敗。"""
        close = np.full(7, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        deadline = _flags(7, [4])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline,
        )
        cover_order = [o for o in ev.orders if o.reason == "calendar_forced_cover"][0]
        assert cover_order.created_index == 3      # 決策在死線前一根（t）
        cover_fill_bar = ev.calendar_cover_bars[0]
        assert cover_fill_bar == 4                 # 成交在死線當根（t+1）
        # 價格資訊仍然嚴格因果：死線後的價格變動不影響死線前的任何決策
        data2 = data.copy()
        data2.loc[5:, "close"] = 250.0
        data2.loc[5:, "open"] = 250.0
        ev2 = run_event_backtest(
            data2, signal, fee_bps=0.0, slippage_bps=0.0,
            forced_cover_deadline=deadline,
        )
        assert ev2.calendar_cover_bars == ev.calendar_cover_bars
        assert [f.price for f in ev2.fills[:2]] == [f.price for f in ev.fills[:2]]
