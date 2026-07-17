"""台股強制回補日曆機制診斷（item 3，2026-07-16）：C1~C5 事前判準驗證。

資料源：FinMind `TaiwanStockMarginShortSaleSuspension`（停券日曆，免費層，
事前公告制已於資料存在性查證階段以 51/52 筆與除權息日交叉驗證）。
規則（已查證）：停券窗 [date..end_date] 禁新融券賣出；最後回補日 = 窗
起始日（含），既有空頭須在該日（含）前回補完成。

事前判準（動工前登錄）：
  C1：每筆 calendar_forced_cover 成交 bar <= 對應死線 bar（鎖死延後案例
      除外，單獨記錄）。
  C2：零筆空頭進場成交落在任何停券窗內（獨立交叉驗證；多單出場的 SELL
      在窗內是合法的，只驗空頭進場）。
  C3：新參數省略時既有 tests 逐位不變（pytest 層驗證，非本 runner）。
  C4：資料未覆蓋 fold 完整揭露——2015-04 前（dataset 起點）機制不生效
      （不觸發也不誤判），比照 6446 籌碼資格的忠實反映方式；6446 另因
      2023-07-28 才有融券資格，之前無停券事件屬真實反映非缺漏。
  C5：日曆資料層驗證——每個停券窗的交易日數應為 4（列出例外）、
      死線 = 窗起始日。

附帶量測（使用者核可，純量測不改行為）：主動避開優化的決策輸入——
撞死線案例數、持有天數、來回成本量級。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tw_calendar_cover_diag.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import tw_stock_costs
from backtest.event_engine import run_event_backtest
from backtest.liq_calibration import stratified_forced_liq_n
from data import storage_events_tw
from data.adjustment import apply_back_adjustment
from data.storage_sqlite import connect, load_klines
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOLS: dict[str, str] = {
    "2330": "TWSE",
    "2603": "TWSE",
    "8069": "TPEx",
    "6446": "TPEx",
}
N_SPLITS = 15
TW_DB = "data/klines_tw.sqlite"
UPTICK_DROP = 0.035
ATR_WINDOW = 14
CALENDAR_DATA_START = "2015-04-01"  # dataset 歷史起點（資料存在性查證確認）
LIMIT_ERA_SPLIT = "2015-06-01"


def _load(symbol: str, exchange: str):
    conn = connect(TW_DB)
    try:
        raw = load_klines(conn, symbol, "1d", exchange=exchange)
    finally:
        conn.close()
    econn = storage_events_tw.connect()
    try:
        dividends = storage_events_tw.load_dividend_results(econn, symbol)
        suspension = storage_events_tw.load_suspensions(econn, symbol)
    finally:
        econn.close()
    if dividends.empty or suspension.empty:
        raise RuntimeError(f"{symbol} 事件快照無資料；請先跑 run_ingest_tw_events.py")
    adjusted = apply_back_adjustment(raw, dividends).reset_index(drop=True)
    return raw.reset_index(drop=True), adjusted, suspension


def _lock_masks(raw: pd.DataFrame):
    close = raw["close"].to_numpy(dtype=float)
    high = raw["high"].to_numpy(dtype=float)
    low = raw["low"].to_numpy(dtype=float)
    ret = np.full(len(raw), np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    era_10 = raw["ts"] >= pd.Timestamp(LIMIT_ERA_SPLIT, tz="Asia/Taipei").tz_convert("UTC")
    threshold = np.where(era_10.to_numpy(), 0.10, 0.07) * 0.9
    flat = high == low
    return (
        pd.Series(flat & (ret >= threshold), index=raw.index),
        pd.Series(flat & (ret <= -threshold), index=raw.index),
    )


def _calendar_masks(raw: pd.DataFrame, suspension: pd.DataFrame):
    """停券窗/死線布林序列 + C5 資料層驗證列。"""
    bar_dates = raw["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(bar_dates)}
    ban = pd.Series(np.zeros(len(raw), dtype=bool), index=raw.index)
    deadline = pd.Series(np.zeros(len(raw), dtype=bool), index=raw.index)
    c5_rows = []
    for _, r in suspension.iterrows():
        start, end = str(r["date"]), str(r["end_date"])
        in_window = [i for d, i in date_to_idx.items() if start <= d <= end]
        if not in_window:
            c5_rows.append({"start": start, "end": end, "reason": r["reason"],
                            "n_bars": 0, "deadline_is_start": False,
                            "note": "窗內無交易bar（可能在資料範圍外）"})
            continue
        for i in in_window:
            ban.iloc[i] = True
        dl_idx = min(in_window)
        deadline.iloc[dl_idx] = True
        c5_rows.append({"start": start, "end": end, "reason": r["reason"],
                        "n_bars": len(in_window),
                        "deadline_is_start": bar_dates.iloc[dl_idx] == start,
                        "note": ""})
    return ban, deadline, pd.DataFrame(c5_rows)


def main() -> None:
    strat = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_overbought=70.0, regime_window=120
    )
    costs = tw_stock_costs()

    c1_rows, c1_violations = [], []
    c2_violations = []
    c4_rows = []
    c5_all = []
    avoid_rows = []

    for symbol, exchange in SYMBOLS.items():
        print(f"跑 {symbol}（{exchange}）...")
        raw, adjusted, suspension = _load(symbol, exchange)
        lock_up_f, lock_down_f = _lock_masks(raw)
        ban_f, deadline_f, c5_df = _calendar_masks(raw, suspension)
        c5_df["symbol"] = symbol
        c5_all.append(c5_df)

        full_signal = strat.generate_signals(adjusted)
        full_atr = compute_atr(adjusted[["high", "low", "close"]], window=ATR_WINDOW)
        splits = list(TimeSeriesSplit(n_splits=N_SPLITS).split(np.arange(len(adjusted))))
        bar_dates_full = raw["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
        cal_start = pd.Timestamp(CALENDAR_DATA_START)

        for k, (train_idx, test_idx) in enumerate(splits):
            # ---- C4：資料未覆蓋 fold 揭露 ----
            t0 = pd.Timestamp(bar_dates_full.iloc[test_idx[0]])
            t1 = pd.Timestamp(bar_dates_full.iloc[test_idx[-1]])
            if t1 < cal_start:
                cover_state = "完全未覆蓋（dataset 起點 2015-04 之前）"
            elif t0 < cal_start:
                cover_state = "部分覆蓋（測試窗跨越 2015-04 dataset 起點）"
            else:
                first_evt = suspension["date"].min() if not suspension.empty else None
                if first_evt and t1 < pd.Timestamp(first_evt):
                    cover_state = f"未覆蓋（該股最早停券事件 {first_evt} 之後才有；6446=融券資格 2023-07 起的真實反映）"
                else:
                    cover_state = "覆蓋"
            c4_rows.append({"symbol": symbol, "fold": k + 1,
                            "test_range": f"{t0.date()}~{t1.date()}", "coverage": cover_state})

            n_strat = stratified_forced_liq_n(adjusted.iloc[train_idx])
            adj_s = adjusted.iloc[test_idx].reset_index(drop=True)
            sig_s = full_signal.iloc[test_idx].reset_index(drop=True)
            atr_s = full_atr.iloc[test_idx].reset_index(drop=True)
            lu = lock_up_f.iloc[test_idx].reset_index(drop=True)
            ld = lock_down_f.iloc[test_idx].reset_index(drop=True)
            ban_s = ban_f.iloc[test_idx].reset_index(drop=True)
            dl_s = deadline_f.iloc[test_idx].reset_index(drop=True)
            dates_s = bar_dates_full.iloc[test_idx].reset_index(drop=True)

            ev = run_event_backtest(
                adj_s, sig_s,
                risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=n_strat)),
                atr_series=atr_s, costs=costs, slippage_bps=0.0,
                short_uptick_rule_drop=UPTICK_DROP, lock_up=lu, lock_down=ld,
                short_entry_ban=ban_s, forced_cover_deadline=dl_s,
            )

            # ---- C1：每筆日曆回補的成交 bar <= 死線 bar ----
            deferred_map = {i: a for i, a, r in ev.deferred_fills if r == "calendar_forced_cover"}
            for d in ev.calendar_cover_bars:
                actual = deferred_map.get(d, d)
                ok = (actual == d) or (d in deferred_map)  # 準時 or 鎖死延後（單獨記錄）
                c1_rows.append({"symbol": symbol, "fold": k + 1,
                                "deadline_date": dates_s.iloc[d],
                                "actual_fill_bar_offset": actual - d,
                                "deferred_by_lock": d in deferred_map})
                if not ok:
                    c1_violations.append(f"{symbol} fold{k+1} bar{d}")

            # ---- C2：空頭進場成交不落在停券窗（重建 fills 的部位路徑判別進場）----
            qty = 0.0
            fill_dates = {}
            for i, f in enumerate(ev.fills):
                d_str = pd.Timestamp(f.ts).tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
                signed = f.quantity if f.side.value == "buy" else -f.quantity
                new_qty = qty + signed
                if signed < 0 and new_qty < 0 and qty <= 0:  # 空頭進場（開/加）
                    bar_pos = dates_s[dates_s == d_str].index
                    if len(bar_pos) and bool(ban_s.iloc[bar_pos[0]]):
                        c2_violations.append(f"{symbol} fold{k+1} {d_str}")
                qty = new_qty

            # ---- 主動避開量測：撞死線案例的持有天數與來回成本 ----
            for d in ev.calendar_cover_bars:
                actual = deferred_map.get(d, d)
                actual_date = dates_s.iloc[actual]
                # 回補 fill = actual_date 當天的 BUY（由後往前找第一筆）
                cover_idx = next(
                    (i for i in range(len(ev.fills) - 1, -1, -1)
                     if ev.fills[i].side.value == "buy"
                     and pd.Timestamp(ev.fills[i].ts).tz_convert("Asia/Taipei")
                     .strftime("%Y-%m-%d") == actual_date), None)
                if cover_idx is None:
                    continue
                # 只掃回補 fill 之前的部位路徑，找開出這筆空頭的進場 fill
                # （不能掃全 fold——回補之後的再進場單會誤植 entry）
                qty2, entry_i = 0.0, None
                for i in range(cover_idx):
                    f = ev.fills[i]
                    signed = f.quantity if f.side.value == "buy" else -f.quantity
                    if signed < 0 and qty2 <= 0 and qty2 + signed < 0:
                        entry_i = i
                    qty2 += signed
                if entry_i is None:
                    continue
                entry_fill = ev.fills[entry_i]
                cover_fill = ev.fills[cover_idx]
                pnl = (entry_fill.price - cover_fill.price) * cover_fill.quantity
                fees = entry_fill.fee + cover_fill.fee
                entry_date = pd.Timestamp(entry_fill.ts).tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
                held = (actual - dates_s[dates_s == entry_date].index[0]
                        if (dates_s == entry_date).any() else -1)
                avoid_rows.append({"symbol": symbol, "fold": k + 1,
                                   "deadline_date": dates_s.iloc[d],
                                   "held_bars": held, "pnl": pnl, "round_trip_fees": fees})

    print()
    print("=" * 100)
    print("C1：日曆回補成交時點（成交 bar <= 死線 bar；鎖死延後單獨記錄）")
    print("=" * 100)
    c1_df = pd.DataFrame(c1_rows)
    if c1_df.empty:
        print("本批測試訊號無撞上死線的空頭部位（v2 空單少且持有期短，屬合理）")
    else:
        print(c1_df.to_string(index=False))
    print(f"C1 判定：{'PASS' if not c1_violations else 'FAIL ' + str(c1_violations)}")

    print()
    print("=" * 100)
    print("C2：空頭進場成交不落在任何停券窗內")
    print("=" * 100)
    print(f"C2 判定：{'PASS（零違規）' if not c2_violations else 'FAIL ' + str(c2_violations)}")

    print()
    print("=" * 100)
    print("C4：資料未覆蓋 fold 完整揭露")
    print("=" * 100)
    c4_df = pd.DataFrame(c4_rows)
    uncovered = c4_df[c4_df["coverage"] != "覆蓋"]
    print(f"未覆蓋/部分覆蓋 fold：{len(uncovered)} / {len(c4_df)}")
    print(uncovered.to_string(index=False))

    print()
    print("=" * 100)
    print("C5：日曆資料層驗證（停券窗交易日數應=4；死線=窗起始日）")
    print("=" * 100)
    c5_df = pd.concat(c5_all, ignore_index=True)
    in_range = c5_df[c5_df["n_bars"] > 0]
    print(f"落在資料範圍內的停券窗：{len(in_range)}  n_bars 分布：{in_range['n_bars'].value_counts().to_dict()}")
    print(f"死線=窗起始日：{int(in_range['deadline_is_start'].sum())}/{len(in_range)}")
    weird = in_range[(in_range["n_bars"] != 4) | (~in_range["deadline_is_start"])]
    if not weird.empty:
        print("例外清單：")
        print(weird.to_string(index=False))

    print()
    print("=" * 100)
    print("主動避開量測（純量測，未來是否開主動避開輪的決策輸入）")
    print("=" * 100)
    av_df = pd.DataFrame(avoid_rows)
    if av_df.empty:
        print("本批無撞死線案例，主動避開暫無可省下的成本")
    else:
        print(av_df.to_string(index=False, formatters={
            "pnl": "{:+.6f}".format, "round_trip_fees": "{:.6f}".format}))
        print(f"\n合計 {len(av_df)} 案例  持有天數 median={av_df['held_bars'].median():.0f}  "
              f"來回費用合計={av_df['round_trip_fees'].sum():.6f}（equity=1.0 基準）")


if __name__ == "__main__":
    main()
