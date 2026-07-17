"""台股放空七環節整合邊界測試（2026-07-16，正式基準輪機制層前置）。

使用者指定的三項「個別驗證都對、疊加後才會出現」的交叉檢查：
  1. ATR 強平的 BUY 回補單不得被 3.5% 禁空規則誤傷（uptick 設計上只擋
     entry_short，這裡在「強平回補恰好落在受限日且成交價低於前收」的
     最壞組合下明確驗證）。
  2. 同一根 bar 同時符合「ATR/固定網觸發」與「停券死線」兩種強制平倉
     理由時的處理與標注——特徵化現有行為：價格強平先建單、日曆層因
     風險縮減單已在途而跳過（見 test 內註解與 HANDOFF 對應說明）。
  3. ATR 強平單被漲跌停鎖死延後、同一天又是停券死線：兩種延後/觸發
     邏輯必須正確疊加為「單一回補單持續重試」，不得重複下單或互相覆蓋。

紀律：這些測試**特徵化**現有引擎行為；若未來行為改變導致測試失敗，
先回報根因再決定修測試還是修引擎，不可靜默改斷言。
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
    return pd.DataFrame({
        "ts": ts, "open": open_,
        "high": np.maximum(open_, close) * 1.01,
        "low": np.minimum(open_, close) * 0.99,
        "close": close, "symbol": ["TEST"] * n,
    })


def _flags(n: int, true_at: list[int]) -> pd.Series:
    s = pd.Series(np.zeros(n, dtype=bool))
    for i in true_at:
        s.iloc[i] = True
    return s


def test_1_forced_liq_cover_not_blocked_by_uptick_rule():
    """交叉檢查 1：強平回補（BUY）落在 3.5% 受限日、成交價低於前收——
    最壞組合下回補必須照常成交，uptick 只擋 entry_short。

    構造：entry@100；bar2 close=110（ATR=5 → 線 115 未觸發）；bar3
    close=106（ATR=2 → 線 106 觸發）且 106/110-1=-3.64% 使 bar4 成為
    受限日；bar4 open=104 < 前收 106（若 uptick 誤傷 BUY 就會攔）。"""
    close = np.array([100.0, 100.0, 110.0, 106.0, 104.0, 104.0])
    data = _make_data(close)
    data.loc[4, "open"] = 104.0
    signal = pd.Series([0, -1, -1, -1, -1, 0])
    atr = pd.Series([2.0, 2.0, 5.0, 2.0, 2.0, 2.0])

    ev = run_event_backtest(
        data, signal, fee_bps=0.0, slippage_bps=0.0,
        risk=RiskManager(equity=1.0), atr_series=atr,
        short_uptick_rule_drop=0.035,
    )
    assert ev.liquidation_bars == [3]
    # 回補正常成交於受限日（104 < 前收 106，若被誤傷這裡就沒有 BUY fill）
    buys = [f for f in ev.fills if f.side is Side.BUY]
    assert len(buys) == 1
    assert buys[0].price == pytest.approx(104.0)
    # uptick 沒有攔截任何風險縮減單
    assert all(
        not (reason == "forced_liquidation" and why == "uptick_rule")
        for _, _, reason, why in ev.blocked_fills
    )
    assert ev.position_qty.iloc[4] == pytest.approx(0.0)


def test_2_same_bar_price_liq_and_calendar_deadline_attribution():
    """交叉檢查 2（特徵化現有行為）：價格強平的成交 bar 恰為停券死線日。

    現有語意：步驟 (c) 價格強平先建單 → 步驟 (c2) 日曆層偵測到風險縮減
    單已在途、跳過不重複下單。結果：**歸因記在 liquidation_bars、
    calendar_cover_bars 為空**——同一 bar 雙重平倉理由成立時，系統以
    「價格強平優先、日曆靜默跳過」處理。此測試釘住這個行為；
    「雙重理由同時成立的 bar 目前不留日曆側記錄」已作為屬性回報使用者
    （見 HANDOFF「台股放空正式基準」節機制層第 2 項）。"""
    close = np.array([100.0, 100.0, 100.0, 107.0, 107.0, 107.0, 100.0])
    data = _make_data(close)
    signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
    atr = pd.Series(np.full(7, 2.0))         # 線 = 106 → bar3 close=107 觸發
    deadline = _flags(7, [4])                # 成交 bar4 同時是停券死線

    ev = run_event_backtest(
        data, signal, fee_bps=0.0, slippage_bps=0.0,
        risk=RiskManager(equity=1.0), atr_series=atr,
        forced_cover_deadline=deadline,
    )
    assert ev.liquidation_bars == [3]        # 價格強平記錄
    assert ev.calendar_cover_bars == []      # 日曆側不重複記錄（現有語意）
    buys = [f for f in ev.fills if f.side is Side.BUY]
    assert len(buys) == 1                    # 只有一張回補單，未重複下單
    reasons = [o.reason for o in ev.orders]
    assert reasons.count("forced_liquidation") == 1
    assert reasons.count("calendar_forced_cover") == 0


def test_3_liq_deferred_by_lock_on_deadline_day_single_retry():
    """交叉檢查 3：強平單被漲停鎖死延後、同一天也是停券死線——兩種邏輯
    必須疊加為「單一回補單持續重試」：不重複下單、不互相覆蓋、
    延後記錄完整、解除鎖死後一次回補完成。"""
    close = np.array([100.0, 100.0, 100.0, 107.0, 109.0, 108.0, 100.0])
    data = _make_data(close)
    signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
    atr = pd.Series(np.full(7, 2.0))
    deadline = _flags(7, [4])                # bar4 = 死線
    lock_up = _flags(7, [4])                 # bar4 同時漲停鎖死
    lock_down = _flags(7, [])

    ev = run_event_backtest(
        data, signal, fee_bps=0.0, slippage_bps=0.0,
        risk=RiskManager(equity=1.0), atr_series=atr,
        forced_cover_deadline=deadline, lock_up=lock_up, lock_down=lock_down,
    )
    assert ev.liquidation_bars == [3]
    assert ev.calendar_cover_bars == []      # 日曆層看到在途單、不疊加
    assert ev.deferred_fills == [(4, 5, "forced_liquidation")]  # 鎖死延後記錄
    # 全程只有一張 BUY 回補單（兩種機制沒有各下一張）
    n_cover_orders = sum(
        1 for o in ev.orders if o.reason in ("forced_liquidation", "calendar_forced_cover")
    )
    assert n_cover_orders == 1
    buys = [f for f in ev.fills if f.side is Side.BUY]
    assert len(buys) == 1
    assert buys[0].price == pytest.approx(109.0)   # bar5 open = close[4]
    assert ev.position_qty.iloc[5] == pytest.approx(0.0)
    # 死線日（bar4）因鎖死無法回補、部位誠實延續失血
    assert ev.position_qty.iloc[4] < 0
