"""事件驅動回測引擎（Phase 2）：MarketEvent → SignalEvent → OrderEvent → FillEvent。

與 vector_engine.py 的關係（該檔★不要動★，兩引擎並存互為對照）：
  - vector_engine 是「訊號 shift(1) × 收盤對收盤報酬」的再平衡近似；
  - 本引擎逐 bar 驅動 broker/paper.py 的真實倉位（固定數量、持有到平倉），
    成交價明確、手續費在成交當下由 broker 扣除。

已知理論差異（設計決策②，2026-07-06 確認，E0 實證見 HANDOFF.md）：
  - 多頭兩引擎數學上等價（fee=0 時逐位一致，tests 有驗證）。
  - 空頭必然分歧：vector 的 -1 是「每根再平衡的反向曝險」（∏(1-r)），本引擎是
    固定數量空單（權益 = 2 - P_t/P_entry，線性 payoff）。單向趨勢中 vector 佔優
    （凸性）、震盪路徑中固定倉位佔優（vector 有波動拖累）——差異路徑相依、
    方向不定，引用 vector 版空頭報酬數字時必須記得。

風控整合（E2，2026-07-06 設計確認決策③④⑤⑥）——傳入 risk（RiskManager）啟用：
  - 每根收盤標記市值 → risk.update_equity()（日內熔斷偵測與跨日重置）。
  - 強平檢查先於新訊號：空頭倉位逐根呼叫 risk.check_forced_liquidation()，
    觸發依據 = 當根 close（決策③，與 short_risk_overlay 同定義；盤中 high
    觸價留待 ATR 校正任務）。ATR 為 NaN 的根整個跳過檢查（含 15% 固定網，
    對齊 overlay 行為）。強平後鎖倉直到訊號離開 -1（決策④，= overlay 的
    「強平後空手等下一個訊號」語意）。
  - 開倉單過 risk.approve_order()（含熔斷/槓桿/多空互斥檢查），核可用價格
    與數量以決策當根收盤估計；**平倉單與強平單不過閘門**（決策⑤：熔斷語意
    是禁開新倉、不是禁減風險）。
  - 倉位大小（sizing_mode 參數，2026-07-13 使用者核可的範圍性解鎖）：
    * "leverage_cap"（預設 = 原行為逐位不變）：該方向槓桿上限內全倉，
      多 max_long_leverage(1.0x)、空 max_short_leverage(0.5x=規則二)。
    * "risk_per_trade"（規則一，只接空頭=選項A）：空頭進場數量 =
      risk.position_size(entry, entry + forced_liq_atr_n×ATR[決策bar], "short")
      ——停損定義 = 強平 ATR 線（2026-07-13 定案 N=3/w=14），倉位隨波動呼吸，
      規則二上限仍為封頂安全繩。多頭無停損機制、維持槓桿上限全倉。
      決策 bar ATR=NaN → fallback 槓桿上限 sizing。停損距離一律取決策 bar
      的 ATR（next_open 模式下 fill bar 的 ATR 含未來資訊，不可用）。
      已知近似：position_size 用 risk 內部權益（決策 bar 收盤更新），
      next_open 成交時有 ≤1 根的權益陳舊性（與核可估計同精神）。
    舊決策⑥（規則一不生效）自此僅適用預設模式。
  - 診斷用法：risk=None 且 forced_liquidation=True 可只掛強平（sizing 1.0x、
    無 approve/熔斷），供與 overlay 做機制對齊比較；此時內部建立一個僅呼叫
    check_forced_liquidation 純函數的 RiskManager，帳戶狀態不參與。

成交假設：
  - fill_mode="signal_close"：第 t 根收盤出訊號、以 close[t] 成交
    （= vector shift(1) 的隱含假設，用於對照 = E0）。
  - fill_mode="next_open"：第 t 根收盤出訊號、以 open[t+1] 成交（預設，較真實）。
  - slippage_bps（2026-07-15 範圍性解鎖，預設 0.0 = 逐位不變）：市價單不利滑價，
    只調整 _execute() 實際成交價——BUY ×(1+bps/1e4)、SELL ×(1−bps/1e4)。強平單
    （forced_liquidation，本質是 BUY 回補）同樣套用、不豁免（強平集中在高波動
    bar，正是滑價壓力測試最相關的情境）。**不調整** close_arr/open_arr 本身：
    訊號生成、mark-to-market equity、強平觸發判定（close-only）、approve_order
    核可估價全部維持用原始價格，只有 broker.market_order 收到的成交價受影響
    ——避免整體價格序列被污染而混淆不相干機制（見 run_slippage_calibration.py
    的壓力測試設計；任務定位是「零滑價是否系統性低估成本」，非參數校正）。
  - 最後一根不產生新訂單（無後續報酬可實現，與 vector 語意對齊）；
    強平單不受此限（減風險單永遠可出）。
  - costs（2026-07-16 範圍性解鎖，台股 E2 化第一輪）：買賣分離費率
    （TradeCosts；台股手續費+證交稅不對稱結構，見 backtest/costs.py）。
    None（預設）＝沿用 fee_bps 對稱費率，逐位不變（保護 BTC/ETH 既有呼叫
    端）。只影響 broker.market_order 收取的手續費，不影響訊號/mark-to-
    market/熔斷/強平判定，與 slippage_bps 同精神（只動成交環節）。
  - short_uptick_rule_drop（2026-07-16 第四次範圍性解鎖，台股放空第一輪）：
    3.5% 跌幅次日禁空規則（台股現行監理規則）。None（預設）＝關閉、逐位
    不變。啟用時對在 bar f 成交的 entry_short 單檢查：若
    close[f-1]/close[f-2]-1 <= -threshold 且成交價（含滑價）< close[f-1]，
    該單**作廢**（保守處理：市價單引擎無掛限價等回升的機制）、記入
    blocked_fills。這是**執行約束不是風控約束**——規則是成交價格條件式
    （高於前收仍可放空），決策時點（approve_order）不知道明天成交價，
    無法在那裡判定。價格基底：台股 runner 餵還原價——交易所在除權息日
    計算跌幅的基準本來就是除權息調整後參考價，還原價才是貼近真實規則
    的基底（「執行語意用原始價」邊界的但書，見 CLAUDE_2.md）。fold 前
    兩根因無前前收盤可比，規則不適用（文件化近似）。
  - lock_up / lock_down（同輪解鎖）：漲跌停全日鎖死布林序列（與 data
    對齊；runner 用**原始價**計算後傳入——分時代門檻 2015-06-01 前 7%／
    後 10%，引擎不含任何市場知識）。None（預設）＝關閉、逐位不變，兩者
    需成對提供。方向感知（真實市場微結構）：漲停鎖死＝買單堆積無賣方→
    BUY 無法成交；跌停鎖死反之 SELL 無法成交。兩類訂單區別處理（延伸
    決策⑤「熔斷禁開新倉不禁減風險」的分類邏輯）：
    * 風險縮減單（exit / forced_liquidation）：被鎖 → **延後重試**到
      下一根未鎖死 bar（以該 bar 的 fill_mode 價格成交、滑價照常），
      記入 deferred_fills=(原定bar, 實際bar, reason)。延後期間部位延續、
      mark-to-market 照常失血——誠實建模軋空情境（連續漲停時強平單
      打不出去，現實中無法立刻止血）。延後期間不重複產生強平單，也
      暫停新的強平觸發處理（部位已在回補程序中）。
    * 新倉單（entry_long / entry_short）：被鎖 → **作廢**（記入
      blocked_fills，reason="limit_lock"）；訊號若持續，下一根決策自然
      重新產生進場單，不損失真實機會。另外：風險縮減單延後未成交期間，
      任何新倉單（即使方向不被鎖）一律作廢（reason=
      "pending_risk_reduction"）——防止舊倉未平、新倉疊加。

防未來函數：第 t 根的決策只使用 ≤ t 的資訊；next_open 用 open[t+1] 是
「執行價」不是決策輸入。tests/test_event_engine.py 以截斷未來資料驗證。

限制（Phase 1 broker 沿用）：無保證金/維持率模型；強平規則（規則三）即為
此近似下的防線。熔斷以「每根收盤標記」判定，非逐 tick——**日頻資料下這個
判定的實際保護範圍會塌縮**（同一根 bar 內 update_equity 與 approve_order
共用同一個 today，能擋到「觸發熔斷當根自己的新倉決策」，但日頻沒有「當天
剩餘的 bar」可擋、跨日又固定重置，量化數字與模擬見 HANDOFF.md「台股 E2 化
前置查證」節，不在此複述）。broker.market_order 永遠假設市價單全額成交，
未建模漲跌停鎖死時可能無法成交的情況（歷史頻率與具體受影響交易日見同節）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from backtest.costs import TradeCosts
from backtest.vector_engine import BacktestResult
from broker.paper import Fill, PaperBroker, Side
from risk.manager import OrderDecision, RiskManager

_REQUIRED_COLUMNS = ("ts", "open", "close")
_VALID_FILL_MODES = ("signal_close", "next_open")
_VALID_SIZING_MODES = ("leverage_cap", "risk_per_trade")
_ENTRY_REASONS = ("entry_long", "entry_short")
_RISK_REDUCE_REASONS = ("exit", "forced_liquidation", "calendar_forced_cover")


@dataclass(frozen=True)
class MarketEvent:
    """一根新 K 棒收盤。"""

    bar_index: int
    ts: pd.Timestamp
    open: float
    close: float


@dataclass(frozen=True)
class SignalEvent:
    """策略在該根收盤後的目標狀態（{-1, 0, +1}）。"""

    bar_index: int
    ts: pd.Timestamp
    target: int


@dataclass(frozen=True)
class OrderEvent:
    """由目標狀態/風控事件產生的訂單。

    quantity 為 None 表示成交當下計算：entry 單以「當時權益 × 該方向槓桿上限」
    全倉；exit / forced_liquidation 單平掉全部現有倉位。
    reason ∈ {"exit", "entry_long", "entry_short", "forced_liquidation"}。
    stop_distance：規則一 sizing 用的停損距離（forced_liq_atr_n × 決策bar ATR），
    僅 sizing_mode="risk_per_trade" 的 entry_short 單有值，其餘 None。
    """

    created_index: int
    ts: pd.Timestamp
    side: Side
    reason: str
    quantity: Optional[float] = None
    stop_distance: Optional[float] = None


@dataclass
class EventBacktestResult:
    """事件驅動回測輸出。FillEvent 直接沿用 broker/paper.py 的 Fill。"""

    equity_curve: pd.Series      # 每根 close 標記市值（起始資金 initial_equity）
    position_qty: pd.Series      # 每根收盤後的持倉數量（正=多、負=空）
    orders: list[OrderEvent]
    fills: list[Fill]
    initial_equity: float
    # ---- 風控診斷（無風控模式下皆為空）----
    rejections: list[tuple[int, pd.Timestamp, str, str]] = field(default_factory=list)
    #   (bar_index, ts, order_reason, OrderDecision.value)
    liquidation_bars: list[int] = field(default_factory=list)
    #   強平條件成立的 bar index（觸發依據 = 該根 close）
    tripped_days: list[date] = field(default_factory=list)
    #   日內熔斷觸發過的日期（UTC）
    blocked_fills: list[tuple[int, pd.Timestamp, str, str]] = field(default_factory=list)
    #   執行時被市場規則攔截而作廢的單（區別於決策時的 rejections）：
    #   (fill_bar_index, ts, order_reason, block_reason)，block_reason ∈
    #   {"uptick_rule", "limit_lock", "pending_risk_reduction", "short_sale_suspension"}
    deferred_fills: list[tuple[int, int, str]] = field(default_factory=list)
    #   鎖死延後成交的單：(原定成交 bar, 實際成交 bar, order_reason)
    calendar_cover_bars: list[int] = field(default_factory=list)
    #   日曆強制回補的死線 bar index（與價格驅動的 liquidation_bars 完全
    #   平行、互不混用——歸因分離，見「成交假設」節 item 3 段）

    def to_backtest_result(self, close: pd.Series) -> BacktestResult:
        """組裝成 vector_engine.BacktestResult 相容結構，讓 build_report 共用。

        position 取 shift(1)：第 t 根報酬（close[t-1]→close[t]）由前一根收盤後
        的持倉承擔，與 vector 的 position 語意對齊（勝率計算用）。
        """
        rets = self.equity_curve.pct_change()
        if len(rets):
            rets.iloc[0] = self.equity_curve.iloc[0] / self.initial_equity - 1.0
        return BacktestResult(
            equity_curve=self.equity_curve / self.initial_equity,
            strategy_returns=rets,
            market_returns=close.astype(float).pct_change().fillna(0.0),
            position=self.position_qty.shift(1).fillna(0.0),
            trades=len(self.fills),
        )


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _orders_for_target(
    target: int, current_qty: float, bar_index: int, ts: pd.Timestamp
) -> list[OrderEvent]:
    """比較目標狀態與當前持倉，產生 0~2 張訂單（翻向時先平後開）。"""
    current_sign = _sign(current_qty)
    if target == current_sign:
        return []

    orders: list[OrderEvent] = []
    if current_sign != 0:
        exit_side = Side.SELL if current_sign > 0 else Side.BUY
        orders.append(OrderEvent(bar_index, ts, exit_side, "exit"))
    if target == 1:
        orders.append(OrderEvent(bar_index, ts, Side.BUY, "entry_long"))
    elif target == -1:
        orders.append(OrderEvent(bar_index, ts, Side.SELL, "entry_short"))
    return orders


def _resolve_atr(
    data: pd.DataFrame, atr_series: Optional[pd.Series], atr_window: int
) -> np.ndarray:
    """取得與 data 對齊的 ATR 陣列（優先用外部傳入，否則就地計算）。

    fold 對照時應傳入「全段資料計算後切片」的 ATR（與 overlay 同慣例），
    讓 fold 開頭有暖機值；就地計算則前 atr_window-1 根為 NaN（強平檢查跳過）。
    """
    if atr_series is not None:
        if not atr_series.index.equals(data.index):
            raise ValueError("atr_series 與 data 的 index 必須對齊")
        return atr_series.to_numpy(dtype=float)
    missing = [c for c in ("high", "low", "close") if c not in data.columns]
    if missing:
        raise ValueError(f"就地計算 ATR 需要欄位 {missing}（或改傳 atr_series）")
    from indicators.technical import atr as compute_atr

    return compute_atr(
        data[["high", "low", "close"]].reset_index(drop=True), window=atr_window
    ).to_numpy(dtype=float)


def run_event_backtest(
    data: pd.DataFrame,
    signal: pd.Series,
    fee_bps: float = 5.0,
    initial_equity: float = 1.0,
    fill_mode: str = "next_open",
    risk: Optional[RiskManager] = None,
    forced_liquidation: Optional[bool] = None,
    atr_series: Optional[pd.Series] = None,
    atr_window: int = 14,
    sizing_mode: str = "leverage_cap",
    slippage_bps: float = 2.0,
    costs: Optional[TradeCosts] = None,
    short_uptick_rule_drop: Optional[float] = None,
    lock_up: Optional[pd.Series] = None,
    lock_down: Optional[pd.Series] = None,
    short_entry_ban: Optional[pd.Series] = None,
    forced_cover_deadline: Optional[pd.Series] = None,
) -> EventBacktestResult:
    """執行事件驅動回測。

    Args:
        data: 需含 ts / open / close 欄位，時間遞增（canonical 子集即可）；
            啟用強平且未傳 atr_series 時另需 high / low。
        signal: 與 data index 對齊的目標訊號（{-1, 0, +1}），**未 shift**；
            引擎自身的事件時序即防未來函數機制，不需要也不可先 shift。
        fee_bps: 單邊手續費（基點），由 PaperBroker 於成交當下收取。
        initial_equity: 起始資金。
        fill_mode: "signal_close"（對照用）或 "next_open"（預設）。
        risk: RiskManager（帳戶級風控：approve_order + 熔斷 + 槓桿 sizing）。
            None = 無風控（E0/E1，全倉 1.0x）。
        forced_liquidation: 空頭強平檢查開關；None（預設）= 跟隨 risk 是否給定。
            risk=None 且此值為 True = 「僅強平」診斷模式（sizing 1.0x）。
        atr_series: 與 data 對齊的 ATR（強平/規則一 sizing 用）；None 則就地計算。
        atr_window: 就地計算 ATR 的窗口（預設 14，與 risk/manager.py 同步）。
        sizing_mode: "leverage_cap"（預設 = 原行為逐位不變）或 "risk_per_trade"
            （規則一，只接空頭，需傳 risk；見模組 docstring）。
        slippage_bps: 市價單不利滑價（基點，預設 0.0 = 逐位不變）；只影響
            broker.market_order 收到的實際成交價，不影響 sizing 基準價／
            mark-to-market／強平觸發判定（見模組 docstring「成交假設」節）。
        costs: 買賣分離費率（TradeCosts；2026-07-16 範圍性解鎖，台股 E2 化
            第一輪）。None（預設）= 沿用 fee_bps 對稱費率，逐位不變（保護
            BTC/ETH 既有呼叫端）；給定時完全接管費率計算，fee_bps 的值
            被忽略。只影響 broker.market_order 收取的手續費，不影響訊號/
            mark-to-market/熔斷/強平判定（與 slippage_bps 同精神：只動
            成交環節）。台股呼叫端應一律明確傳入 `tw_stock_costs()`，不可
            省略（見 run_e2_tw_baseline.py 的必選介面設計）。
        short_uptick_rule_drop: 3.5% 跌幅次日禁空規則門檻（如 0.035）。
            None（預設）= 關閉、逐位不變。見模組 docstring「成交假設」節。
        lock_up / lock_down: 漲跌停全日鎖死布林序列（與 data index 對齊，
            需成對提供；runner 用原始價計算）。None（預設）= 關閉、逐位
            不變。見模組 docstring「成交假設」節的方向感知與延後/作廢語意。
        short_entry_ban: 停券窗布林序列（台股強制回補日曆，item 3，
            2026-07-16 第五次範圍性解鎖）。窗內 entry_short 成交作廢
            （blocked_fills reason="short_sale_suspension"）；BUY 回補
            不受限。None（預設）= 關閉、逐位不變。
        forced_cover_deadline: 最後回補日布林序列。成交 bar 為死線日且仍
            持有空頭 → 產生 reason="calendar_forced_cover" 的 BUY 平倉單
            （記入 calendar_cover_bars，與價格驅動 liquidation_bars 完全
            平行、歸因分離；價格強平已在途時跳過不重複下單）。
            ★非未來函數：bar t 決策讀 t+1 的日曆旗標是刻意設計——停券
            日程數週前公告（事前公告制已交叉驗證），布林序列代表「當時
            已公告的日程」，與價格因果紀律是兩回事。本專案第二個合法
            非因果例外（第一個=還原股價），分類標準見 AGENTS.md「使用
            未來已公告資訊的合法性分類」節。★
    """
    if fill_mode not in _VALID_FILL_MODES:
        raise ValueError(f"fill_mode 必須是 {_VALID_FILL_MODES}")
    if sizing_mode not in _VALID_SIZING_MODES:
        raise ValueError(f"sizing_mode 必須是 {_VALID_SIZING_MODES}")
    if slippage_bps < 0.0:
        raise ValueError("slippage_bps 不可為負（市價單滑價恆不利）")
    if short_uptick_rule_drop is not None and short_uptick_rule_drop <= 0.0:
        raise ValueError("short_uptick_rule_drop 必須為正（如 0.035）")
    if (lock_up is None) != (lock_down is None):
        raise ValueError("lock_up 與 lock_down 需成對提供（方向感知的鎖死語意）")
    if lock_up is not None:
        if not lock_up.index.equals(data.index) or not lock_down.index.equals(data.index):
            raise ValueError("lock_up/lock_down 與 data 的 index 必須對齊")
    for name, series in (("short_entry_ban", short_entry_ban),
                         ("forced_cover_deadline", forced_cover_deadline)):
        if series is not None and not series.index.equals(data.index):
            raise ValueError(f"{name} 與 data 的 index 必須對齊")
    if sizing_mode == "risk_per_trade" and risk is None:
        raise ValueError('sizing_mode="risk_per_trade" 需要傳入 risk（規則一需風險比例與權益）')
    missing = [c for c in _REQUIRED_COLUMNS if c not in data.columns]
    if missing:
        raise ValueError(f"data 缺少必要欄位: {missing}")
    if len(data) != len(signal):
        raise ValueError("data 與 signal 長度不一致")
    if not data.index.equals(signal.index):
        raise ValueError("data 與 signal 的 index 必須對齊")
    invalid = set(pd.unique(signal.dropna())) - {-1, 0, 1}
    if invalid:
        raise ValueError(f"signal 只允許 -1/0/+1，發現: {sorted(invalid)}")

    liq_enabled = forced_liquidation if forced_liquidation is not None else (risk is not None)
    # 診斷模式（risk=None）時建立僅供呼叫純函數 check_forced_liquidation 的
    # RiskManager；其帳戶狀態不參與本回測。
    liq_checker = risk if risk is not None else (
        RiskManager(equity=max(float(initial_equity), 1e-9)) if liq_enabled else None
    )
    need_atr = liq_enabled or sizing_mode == "risk_per_trade"
    atr_arr = _resolve_atr(data, atr_series, atr_window) if need_atr else None

    symbol = str(data["symbol"].iloc[0]) if "symbol" in data.columns else "SYM"
    broker = PaperBroker(cash=initial_equity, fee_bps=fee_bps, costs=costs)

    n = len(data)
    ts_arr = list(data["ts"])
    date_arr = [ts.date() for ts in ts_arr]
    open_arr = data["open"].to_numpy(dtype=float)
    close_arr = data["close"].to_numpy(dtype=float)
    signal_arr = signal.fillna(0).to_numpy(dtype=int)

    lock_up_arr = lock_up.to_numpy(dtype=bool) if lock_up is not None else None
    lock_down_arr = lock_down.to_numpy(dtype=bool) if lock_down is not None else None
    entry_ban_arr = (
        short_entry_ban.to_numpy(dtype=bool) if short_entry_ban is not None else None
    )
    cover_deadline_arr = (
        forced_cover_deadline.to_numpy(dtype=bool)
        if forced_cover_deadline is not None else None
    )

    equity_vals = np.empty(n, dtype=float)
    pos_vals = np.empty(n, dtype=float)
    all_orders: list[OrderEvent] = []
    rejections: list[tuple[int, pd.Timestamp, str, str]] = []
    liquidation_bars: list[int] = []
    tripped_days: list[date] = []
    blocked_fills: list[tuple[int, pd.Timestamp, str, str]] = []
    deferred_fills: list[tuple[int, int, str]] = []
    calendar_cover_bars: list[int] = []
    pending: list[OrderEvent] = []
    short_locked = False  # 強平後鎖倉，直到訊號離開 -1（決策④）

    def _entry_leverage(reason: str) -> float:
        if risk is None:
            return 1.0  # 無風控：全倉 1.0x，對齊 vector ±1
        if reason == "entry_short":
            return float(risk.config.max_short_leverage)
        return float(risk.config.max_long_leverage)  # type: ignore[arg-type]

    def _slipped(side: Side, price: float) -> float:
        """市價單不利滑價：BUY 買貴、SELL 賣賤（sizing 基準價不受影響）。"""
        if slippage_bps <= 0.0:
            return price
        adj = slippage_bps / 10_000.0
        return price * (1.0 + adj) if side is Side.BUY else price * (1.0 - adj)

    def _execute(order: OrderEvent, price: float, ts: pd.Timestamp) -> None:
        if order.reason in _RISK_REDUCE_REASONS:
            pos = broker.get_position(symbol)
            if pos.quantity == 0:
                return  # 防禦：不會由正常流程觸發
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            broker.market_order(symbol, side, abs(pos.quantity), _slipped(side, price), ts)
        elif order.stop_distance is not None and risk is not None:
            # 規則一（空頭）：風險比例倉位，停損距離 = N×ATR[決策bar]。
            # position_size 內建規則二槓桿上限封頂；權益為決策 bar 收盤值
            # （next_open 成交時 ≤1 根陳舊性，文件化近似）。sizing 用未滑價
            # 的參考價，滑價只影響最終送進 broker 的實際成交價。
            quantity = risk.position_size(
                entry_price=price, stop_price=price + order.stop_distance, side="short"
            ).quantity
            if quantity <= 0:
                return
            broker.market_order(symbol, order.side, quantity, _slipped(order.side, price), ts)
        else:  # entry：以成交當下權益 × 該方向槓桿上限全倉（sizing 用未滑價參考價）
            equity_now = broker.equity({symbol: price})
            if equity_now <= 0:
                return  # 權益耗盡，無法開新倉
            quantity = equity_now * _entry_leverage(order.reason) / price
            broker.market_order(symbol, order.side, quantity, _slipped(order.side, price), ts)

    def _lock_blocked(side: Side, t: int) -> bool:
        """方向感知的鎖死判定：BUY 被漲停鎖死擋、SELL 被跌停鎖死擋。"""
        if lock_up_arr is None:
            return False
        return bool(lock_up_arr[t]) if side is Side.BUY else bool(lock_down_arr[t])

    def _uptick_blocked(t: int, fill_price: float) -> bool:
        """3.5% 禁空規則：前一 bar 跌幅達標且成交價（含滑價）低於前收。
        fold 前兩根無前前收盤可比，規則不適用（文件化近似）。"""
        if short_uptick_rule_drop is None or t < 2:
            return False
        drop = close_arr[t - 1] / close_arr[t - 2] - 1.0
        if drop > -short_uptick_rule_drop:
            return False
        return _slipped(Side.SELL, fill_price) < close_arr[t - 1]

    def _process_fills(
        orders: list[OrderEvent], t: int, fill_price: float,
        initial_risk_reduce: bool = False,
    ) -> list[OrderEvent]:
        """在 bar t 以 fill_price 逐一處理訂單：成交／延後（回傳續掛清單）／作廢。

        清單順序 = 建立順序（風險縮減單恆在同批新倉單之前），確保
        pending_risk_reduction 判定看得到本 bar 稍早被延後的風險縮減單；
        initial_risk_reduce 供 signal_close 模式傳入「pending 中已有延後
        風險縮減單」的既存狀態（該模式的新單不經過同一清單）。
        """
        still: list[OrderEvent] = []
        risk_reduce_deferred = initial_risk_reduce
        for order in orders:
            if _lock_blocked(order.side, t):
                if order.reason in _RISK_REDUCE_REASONS:
                    still.append(order)
                    risk_reduce_deferred = True
                else:
                    blocked_fills.append((t, ts_arr[t], order.reason, "limit_lock"))
                continue
            if order.reason in _ENTRY_REASONS and risk_reduce_deferred:
                blocked_fills.append((t, ts_arr[t], order.reason, "pending_risk_reduction"))
                continue
            if order.reason == "entry_short" and (
                (entry_ban_arr is not None and entry_ban_arr[t])
                # 死線 bar 隱含禁新空：真實規則中最後回補日=停券窗首日，
                # 即使呼叫端只提供 deadline 未提供 ban 窗，也不允許
                # 「回補後同根立刻重新進空」這種經濟上無意義的行為
                or (cover_deadline_arr is not None and cover_deadline_arr[t])
            ):
                blocked_fills.append((t, ts_arr[t], order.reason, "short_sale_suspension"))
                continue
            if order.reason == "entry_short" and _uptick_blocked(t, fill_price):
                blocked_fills.append((t, ts_arr[t], order.reason, "uptick_rule"))
                continue
            intended = order.created_index + (1 if fill_mode == "next_open" else 0)
            if t > intended:
                deferred_fills.append((intended, t, order.reason))
            _execute(order, price=fill_price, ts=ts_arr[t])
        return still

    for t in range(n):
        # (a) 成交上一根決策的掛單與鎖死延後單（以本根 fill_mode 價格成交；
        #     next_open=本根開盤，signal_close 延後單=本根收盤）
        if pending:
            fill_price = open_arr[t] if fill_mode == "next_open" else close_arr[t]
            pending[:] = _process_fills(pending, t, fill_price)

        # (b) 收盤標記市值 → 熔斷偵測（跨日自動重置）
        equity_mark = broker.equity({symbol: close_arr[t]})
        if risk is not None:
            risk.update_equity(equity_mark, date_arr[t])
            if risk.is_circuit_broken and (
                not tripped_days or tripped_days[-1] != date_arr[t]
            ):
                tripped_days.append(date_arr[t])

        # (c) 強平檢查（先於新訊號；觸發依據 = 當根 close；ATR NaN 整根跳過；
        #     已有風險縮減單延後在途時暫停新觸發處理——部位已在回補程序中）
        if liq_enabled:
            risk_reduce_in_flight = any(o.reason in _RISK_REDUCE_REASONS for o in pending)
            pos = broker.get_position(symbol)
            if pos.quantity < 0 and not risk_reduce_in_flight and not np.isnan(atr_arr[t]):
                assert liq_checker is not None
                if liq_checker.check_forced_liquidation(
                    pos.quantity, pos.avg_price, close_arr[t], atr_arr[t]
                ):
                    liq_order = OrderEvent(t, ts_arr[t], Side.BUY, "forced_liquidation")
                    all_orders.append(liq_order)
                    liquidation_bars.append(t)
                    short_locked = True
                    if fill_mode == "signal_close":
                        # 立即成交；鎖死時 _process_fills 會把它延後回 pending
                        pending.extend(_process_fills([liq_order], t, close_arr[t]))
                    else:
                        pending.append(liq_order)

        # (c2) 日曆強制回補（item 3；與價格驅動強平平行的獨立機制）。
        #     判定 = 「成交 bar 是否為最後回補日」：next_open 模式決策在 t、
        #     成交在 t+1 → 讀 t+1 旗標（非未來函數：日程數週前公告，見
        #     docstring）；signal_close 模式同 bar 收盤成交 → 讀 t。
        #     風險縮減單已在途（價格強平/訊號出場/先前延後）→ 跳過不重複下單。
        if cover_deadline_arr is not None:
            cover_fill_bar = t + 1 if fill_mode == "next_open" else t
            if cover_fill_bar < n and cover_deadline_arr[cover_fill_bar]:
                in_flight = any(o.reason in _RISK_REDUCE_REASONS for o in pending)
                pos = broker.get_position(symbol)
                if pos.quantity < 0 and not in_flight:
                    cover_order = OrderEvent(
                        t, ts_arr[t], Side.BUY, "calendar_forced_cover"
                    )
                    all_orders.append(cover_order)
                    calendar_cover_bars.append(cover_fill_bar)
                    if fill_mode == "signal_close":
                        pending.extend(_process_fills([cover_order], t, close_arr[t]))
                    else:
                        pending.append(cover_order)

        # 風險縮減單在途（本根新掛或先前延後未成交）→ 決策視同已空手
        liq_exit_pending = any(o.reason in _RISK_REDUCE_REASONS for o in pending)

        # (d) SignalEvent → OrderEvent（最後一根不產生新訂單）
        if t < n - 1:
            target = int(signal_arr[t])
            if target != -1:
                short_locked = False  # 訊號重置，解除強平鎖倉
            # 強平出場已排入時，決策視同已空手（避免重複平倉單）
            effective_qty = 0.0 if liq_exit_pending else broker.get_position(symbol).quantity
            orders = _orders_for_target(target, effective_qty, t, ts_arr[t])
            if short_locked:
                orders = [o for o in orders if o.reason != "entry_short"]
            if sizing_mode == "risk_per_trade":
                # 規則一：空頭進場單標上停損距離（決策 bar 的 ATR；NaN → 不標，
                # 成交時 fallback 槓桿上限 sizing）
                orders = [
                    replace(o, stop_distance=risk.config.forced_liq_atr_n * atr_arr[t])
                    if o.reason == "entry_short" and not np.isnan(atr_arr[t])
                    else o
                    for o in orders
                ]

            approved: list[OrderEvent] = []
            for order in orders:
                if risk is not None and order.reason in _ENTRY_REASONS:
                    # 開倉單過閘門：核可價/量以決策當根收盤估計。
                    # current_position_qty=0：引擎保證先平後開，開倉時必為空手，
                    # 多空互斥由此結構性滿足。
                    side_str = "short" if order.reason == "entry_short" else "long"
                    if order.stop_distance is not None:
                        qty_est = risk.position_size(
                            entry_price=close_arr[t],
                            stop_price=close_arr[t] + order.stop_distance,
                            side="short",
                        ).quantity
                    else:
                        qty_est = equity_mark * _entry_leverage(order.reason) / close_arr[t]
                    decision = risk.approve_order(
                        qty_est, close_arr[t], date_arr[t],
                        side=side_str, current_position_qty=0.0,
                    )
                    if decision is not OrderDecision.APPROVED:
                        rejections.append(
                            (t, ts_arr[t], order.reason, decision.value)
                        )
                        continue
                approved.append(order)

            if approved:
                all_orders.extend(approved)
                if fill_mode == "signal_close":
                    pending.extend(_process_fills(
                        approved, t, close_arr[t],
                        initial_risk_reduce=liq_exit_pending,
                    ))
                else:
                    pending.extend(approved)

        # (e) 收盤紀錄（signal_close 的成交已反映；手續費即時扣除）
        equity_vals[t] = broker.equity({symbol: close_arr[t]})
        pos_vals[t] = broker.get_position(symbol).quantity

    return EventBacktestResult(
        equity_curve=pd.Series(equity_vals, index=data.index, name="equity"),
        position_qty=pd.Series(pos_vals, index=data.index, name="position_qty"),
        orders=all_orders,
        fills=list(broker.fills),
        initial_equity=float(initial_equity),
        rejections=rejections,
        liquidation_bars=liquidation_bars,
        tripped_days=tripped_days,
        blocked_fills=blocked_fills,
        deferred_fills=deferred_fills,
        calendar_cover_bars=calendar_cover_bars,
    )
