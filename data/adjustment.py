"""台股除權息還原股價：向後調整（back-adjustment），錨點 = 最新價格。

背景：`klines_tw.sqlite` 存的是 FinMind TaiwanStockPrice 的原始成交價，未做
除權息還原（台股 E2 化前置查證任務逐位驗證：65/65 個除權息事件的前一交易日
收盤price == FinMind before_price）。MA/RSI/ATR 等 rolling 指標若直接吃原始
價格，會在除權息日附近算到人工跳空（極端案例：2603 2023-06-30 現金股利
70 元，原始報酬 -39.68%）。本模組提供一個動態計算的還原函式，不落地覆蓋
`klines_tw.sqlite` 的原始資料——原始資料保持不動、轉換在下游做，比照
`indicators/features.py` 對齊籌碼資料的既有架構原則。

演算法：
  ratio_i = after_price_i / before_price_i（除權息事件 i 的稀釋/減資比例）
  cum_factor(t) = Π{ ratio_i : 事件 i 的除權息日 > bar t 的交易日 }
  adjusted_price(t) = raw_price(t) × cum_factor(t)

用 after_price 而非 reference_price：FinMind 對純股票股利事件（"權"）的
reference_price 會停滯於 before_price（未反映稀釋），只有 after_price 在
所有事件類型（權/息/權息）都正確反映稀釋幅度，見
tests/test_price_adjustment.py::test_adjustment_uses_after_price_not_reference_price
的具體反例。

**執行語意邊界（重要）**：還原價只用於訊號/指標計算（MA/RSI/ATR），漲跌停
鎖死判定等「這個價位實際能不能成交」的執行語意判斷一律用原始價格——漲跌
幅限制是交易所相對「原始前一日收盤」計算的，用還原價判斷會在除權息日附近
產生假的鎖死/不鎖死誤判。**但書（2026-07-16 使用者確認）**：若某執行規則
在真實世界的定義本身就建立在除權息調整後價格上，則該規則用還原價才是
忠實建模——具體案例：3.5% 跌幅次日禁空規則（`event_engine.py` 的
`short_uptick_rule_drop`），交易所在除權息日計算跌幅的基準本來就是除權息
調整後參考價，用原始價會在除息日出現假觸發（如 2603 -45.2% 機械跳空誤判
為暴跌禁空）。判斷準則：先問「交易所自己用哪個價格基底定義這條規則」，
不是機械套用「執行=原始價」。

**這是刻意的非因果轉換，不是防未來函數 bug**：cum_factor(t) 依賴「晚於 t」
的除權息事件，是產業標準的還原價做法（用未來已發生的公司行動回填過去價格
水位），與本專案 `shift(1)` 訊號防未來函數紀律是兩件不同的事——訊號的未來
函數是「用還不存在的資訊做交易決策」，還原價是「事後對歷史價格水位的重新
表述」，兩者不可混淆。見
tests/test_price_adjustment.py::test_adjustment_intentionally_uses_future_dividend_events。

OHLC 調整範圍：open/high/low/close（與 vwap，若存在）用同一個 cum_factor
縮放，保持 bar 內部 low<=open,close<=high 的不等式；volume/turnover 不調整
（代表真實發生的原始股數/金額，不是價格類欄位）。
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

REQUIRED_DIVIDEND_COLUMNS: Final = {"date", "before_price", "after_price"}
_ADJUSTABLE_PRICE_COLUMNS: Final = ["open", "high", "low", "close", "vwap"]
_TW_TZ: Final = "Asia/Taipei"
_PRICE_MATCH_TOL: Final = 1e-6


def compute_cum_factor(bar_dates: pd.Series, dividends: pd.DataFrame) -> pd.Series:
    """算出與 bar_dates 對齊的累積調整因子（不做 before_price 對帳，見
    apply_back_adjustment 的完整版）。

    Args:
        bar_dates: 每根 bar 對應的交易日（datetime.date），須遞增排序。
        dividends: 至少含 date / before_price / after_price 欄位的除權息事件表
            （原始 FinMind TaiwanStockDividendResult 欄位可直接傳入，多餘欄位
            會被忽略；不可用 reference_price 取代 after_price，見模組 docstring）。

    Returns:
        與 bar_dates 對齊的 pd.Series，最新一根 bar（無晚於它的事件）恆為 1.0。
    """
    missing = REQUIRED_DIVIDEND_COLUMNS - set(dividends.columns)
    if missing:
        raise ValueError(f"dividends 缺少必要欄位: {sorted(missing)}")

    if dividends.empty:
        return pd.Series(1.0, index=bar_dates.index)

    events = dividends.copy()
    events["date"] = pd.to_datetime(events["date"]).dt.date
    events["before_price"] = pd.to_numeric(events["before_price"], errors="coerce")
    events["after_price"] = pd.to_numeric(events["after_price"], errors="coerce")

    if events[["before_price", "after_price"]].isna().any().any():
        raise ValueError("dividends 的 before_price/after_price 含無法轉換為數值的值")
    if (events["before_price"] <= 0).any() or (events["after_price"] <= 0).any():
        raise ValueError("dividends 的 before_price/after_price 必須為正")
    if events["date"].duplicated().any():
        dup = sorted(events.loc[events["date"].duplicated(), "date"].astype(str).unique())
        raise ValueError(f"dividends 同一日期出現重複除權息事件: {dup}")

    events = events.sort_values("date").reset_index(drop=True)
    event_dates = events["date"].to_numpy()
    ratios = (events["after_price"] / events["before_price"]).to_numpy()

    log_ratios = np.log(ratios)
    # suffix_sum[k] = sum(log_ratios[k:])，suffix_sum[len(events)] = 0（尾端補值）
    suffix_sum = np.concatenate([np.cumsum(log_ratios[::-1])[::-1], [0.0]])

    dates = bar_dates.to_numpy()
    # 「嚴格晚於 t」的事件數 = 事件總數 - 「<= t」的事件數（events 已遞增排序）
    idx_le = np.searchsorted(event_dates, dates, side="right")
    cum_log = suffix_sum[idx_le]

    return pd.Series(np.exp(cum_log), index=bar_dates.index)


def _validate_events_against_bars(
    klines: pd.DataFrame, bar_dates: pd.Series, dividends: pd.DataFrame
) -> None:
    """事件當日若在資料範圍內，前一交易日收盤應與 before_price 相符（容忍浮點
    誤差）——不符代表日期定義或資料源不一致，拋錯而非默默算出錯誤的調整因子
    （比照融資融券哨兵值『已知情況轉換、未知情況拋錯』的既有紀律）。"""
    if dividends.empty:
        return

    events = dividends.copy()
    events["date"] = pd.to_datetime(events["date"]).dt.date
    date_list = list(bar_dates)
    closes = klines["close"].to_numpy(dtype=float)
    date_to_idx = {d: i for i, d in enumerate(date_list)}

    for _, row in events.iterrows():
        idx = date_to_idx.get(row["date"])
        if idx is None or idx == 0:
            continue  # 事件日不在資料範圍內，或是第一根 bar（無前一日可比對）
        prev_close = closes[idx - 1]
        before_price = float(row["before_price"])
        if not np.isclose(prev_close, before_price, rtol=_PRICE_MATCH_TOL, atol=_PRICE_MATCH_TOL):
            raise ValueError(
                f"除權息事件 {row['date']} 的 before_price={before_price} 與資料中"
                f"前一交易日收盤 {prev_close} 不符（容忍值外），可能日期定義或資料源"
                "不一致，需人工確認後再決定處理方式"
            )


def apply_back_adjustment(klines: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
    """回傳還原後的 K 線 DataFrame（新物件，不修改輸入；原始資料保持不動）。

    Args:
        klines: 單一 symbol 的 canonical K 線（至少含 ts/symbol/open/high/low/close；
            通常由 `data.storage_sqlite.load_klines` 讀出，ts 為 tz-aware UTC）。
        dividends: 該 symbol 的除權息事件表（同 symbol 的 TaiwanStockDividendResult
            子集；若含 stock_id 欄位會與 klines 的 symbol 互相驗證一致）。

    Returns:
        新 DataFrame：open/high/low/close（與 vwap，若存在）乘上 cum_factor；
        volume/turnover 等其餘欄位原封不動複製；額外新增 `adj_factor` 欄位
        （非 canonical 標準欄位，方便除錯/測試檢查用的累積調整因子本身）。
    """
    required = {"ts", "symbol", "open", "high", "low", "close"}
    missing = required - set(klines.columns)
    if missing:
        raise ValueError(f"klines 缺少必要欄位: {sorted(missing)}")

    if klines.empty:
        out = klines.copy()
        out["adj_factor"] = pd.Series(dtype=float)
        return out

    symbols = klines["symbol"].unique()
    if len(symbols) > 1:
        raise ValueError(f"apply_back_adjustment 一次只接受單一 symbol 的 klines，收到: {sorted(symbols)}")

    if not dividends.empty and "stock_id" in dividends.columns:
        div_symbols = dividends["stock_id"].astype(str).unique()
        if len(div_symbols) > 1 or str(div_symbols[0]) != str(symbols[0]):
            raise ValueError(
                f"dividends 的 stock_id 與 klines 的 symbol 不一致: {sorted(div_symbols)} vs {symbols[0]}"
            )

    bar_dates = klines["ts"].dt.tz_convert(_TW_TZ).dt.date
    _validate_events_against_bars(klines, bar_dates, dividends)

    cum_factor = compute_cum_factor(bar_dates, dividends)
    factor_arr = cum_factor.to_numpy()

    out = klines.copy()
    for col in _ADJUSTABLE_PRICE_COLUMNS:
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype=float) * factor_arr
    out["adj_factor"] = factor_arr
    return out
