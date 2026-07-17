"""Phase 3：台股日K（FinMind TaiwanStockPrice）ingest + 端到端籌碼對齊 demo。

抓 POC 子集 4 檔的日K → canonical → 落地 data/klines_tw.sqlite（獨立於凍結的
BTC klines.sqlite）。接著把「真實台股日K bar 軸」與已落地的「真實籌碼
（chips_tw.sqlite）」用 indicators/features.align_chips_to_bars 做端到端排他式
as-of 對齊——這是價格(TWSE/TPEx)＋籌碼(FinMind)兩條真實資料流第一次真正對齊。

重點展示 6446 藥華藥「價格持續、籌碼(institutional)在 2024-01-25 斷尾」的分歧點：
asof_date 凍結於 2024-01-25、age_days 持續遞增、age>max 後轉 NaN + stale
（先前只在合成資料驗證過此效果，這次看真實資料實際輸出）。

用法：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 FINMIND_MIN_INTERVAL=2 \
        .venv/Scripts/python.exe run_ingest_tw_price.py
"""

from __future__ import annotations

import pandas as pd

from data import storage_chips, storage_sqlite
from data.cleaning import finmind_price_to_canonical
from data.providers import finmind
from indicators.features import align_chips_to_bars

# POC 子集：涵蓋 TWSE/TPEx、正常 vs 籌碼斷尾對照（exchange 依 FinMind type）。
# 每檔起始日 = 價格∩Foreign_Investor 籌碼交集起點（2026-07-10 查證）：
# TWSE 法人資料實際 2012-05-02 起、8069 TPEx 2005-01-06 起、6446 上櫃即 2014-03-11。
# 6446 籌碼 2024-01-25 斷尾但價格照抓到現在——僅作 stale 機制長歷史測試樣本。
POC_PRICE = {
    "2330": ("TWSE", "2012-05-02", "台積電（超大型/外資主導）"),
    "2603": ("TWSE", "2012-05-02", "長榮（融資券活躍）"),
    "8069": ("TPEx", "2005-01-06", "元太（上櫃覆蓋驗證）"),
    "6446": ("TPEx", "2014-03-11", "藥華藥（籌碼斷尾樣本，不入統計）"),
}
PRICE_END = None   # None = 抓到 FinMind 最新
# demo_alignment 的展示視窗維持 2024Q1（斷尾分歧點在此），與 ingest 範圍脫鉤
DEMO_START, DEMO_END = "2024-01-01", "2024-03-31"
CHIP_START = "2023-12-15"
MAX_AGE_DAYS = 7  # 新鮮度硬上限；6446 斷尾後超過即轉 NaN + stale
TW_DB = "data/klines_tw.sqlite"

# 籌碼來源 → features 輸出前綴（chip_frames 的 key）
CHIP_SOURCES = {
    "instit": "chip_institutional",
    "margin": "chip_margin",
    "shares": "chip_foreign_shareholding",
}


def ingest_prices() -> None:
    print(f"=== [1] 抓取日K 並落地 {TW_DB}（每檔起始日=價格∩籌碼交集起點 → 最新）===")
    conn = storage_sqlite.connect(TW_DB)
    try:
        for stock_id, (exchange, start, desc) in POC_PRICE.items():
            raw = finmind.fetch_price(stock_id, start, PRICE_END, exchange=exchange)
            canonical = finmind_price_to_canonical(raw)
            n = storage_sqlite.save_klines(conn, canonical)
            if canonical.empty:
                print(f"  {stock_id} {exchange:<4} rows=     0  ★無資料 {desc}")
                continue
            lo = canonical["ts"].min().tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
            hi = canonical["ts"].max().tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
            print(f"  {stock_id} {exchange:<4} rows={n:>4}  {lo} → {hi}  {desc}")
    finally:
        conn.close()


def _load_price_bars(kconn, stock_id: str, exchange: str) -> pd.DataFrame:
    """讀回日K canonical，裁到 demo 視窗（2024Q1），轉成 features 需要的
    price_bars（tz-aware DatetimeIndex）。ingest 範圍擴大後 demo 展示不跟著掃全歷史。"""
    df = storage_sqlite.load_klines(kconn, stock_id, "1d", exchange=exchange)
    bars = df.set_index("ts")  # ts 已是 tz-aware UTC
    return bars.loc[DEMO_START:DEMO_END]


def _load_chip_frames(cconn, stock_id: str) -> dict[str, pd.DataFrame]:
    frames = {}
    for prefix, table in CHIP_SOURCES.items():
        frames[prefix] = storage_chips.load_chips(
            cconn, table, stock_id=stock_id, start=CHIP_START, end=DEMO_END
        )
    return frames


def _with_local_date(feat: pd.DataFrame) -> pd.DataFrame:
    """在特徵矩陣前面插入 bar 的台北當地日曆日與星期，方便閱讀。"""
    local = feat.index.tz_convert("Asia/Taipei")
    out = feat.copy()
    out.insert(0, "bar_date", local.strftime("%Y-%m-%d"))
    out.insert(1, "wd", local.strftime("%a"))
    return out


def demo_alignment() -> None:
    kconn = storage_sqlite.connect(TW_DB)
    cconn = storage_chips.connect()
    try:
        # --- 2330：正常對齊（asof 嚴格早於當日、週間 age=1、跨週末 age=3）---
        print("\n=== [2] 2330 正常對齊（真實日K bar 軸 × 真實籌碼）===")
        bars = _load_price_bars(kconn, "2330", "TWSE")
        frames = _load_chip_frames(cconn, "2330")
        feat = align_chips_to_bars(bars, frames, session_tz="Asia/Taipei", max_age_days=MAX_AGE_DAYS)
        show = _with_local_date(feat)[
            ["bar_date", "wd", "instit_asof_date", "instit_age_days",
             "foreign_net", "invest_trust_net", "margin_balance_today", "foreign_shares_ratio"]
        ].head(8)
        print(show.to_string(index=False))
        # 不變量健檢：每根 bar 的 asof 嚴格早於當日
        _assert_exclusive(feat, "2330")

        # --- 6446：價格持續、籌碼斷尾的分歧點（本次端到端驗證最有價值的一格）---
        print("\n=== [3] ★6446 價格持續、institutional 籌碼 2024-01-25 斷尾 → asof 凍結/age 遞增/stale ★ ===")
        bars = _load_price_bars(kconn, "6446", "TPEx")
        frames = _load_chip_frames(cconn, "6446")
        instit_last = frames["instit"]["date"].max() if not frames["instit"].empty else "（無）"
        print(f"  6446 institutional 已落地最後交易日 = {instit_last}（HANDOFF 記錄的真實斷尾）")
        feat = align_chips_to_bars(bars, frames, session_tz="Asia/Taipei", max_age_days=MAX_AGE_DAYS)
        show = _with_local_date(feat)[
            ["bar_date", "wd", "instit_asof_date", "instit_age_days", "instit_stale", "foreign_net"]
        ]
        # 聚焦斷尾前後窗（2024-01-24 → 2024-02-08）
        focus = show[(show["bar_date"] >= "2024-01-24") & (show["bar_date"] <= "2024-02-08")]
        print(focus.to_string(index=False))
        _report_truncation(feat, instit_last)
        _assert_exclusive(feat, "6446")
    finally:
        kconn.close()
        cconn.close()


def _assert_exclusive(feat: pd.DataFrame, tag: str) -> None:
    """健檢：任何 bar 採用的籌碼 asof_date 必嚴格早於該 bar 的台北當地日（防未來函數）。"""
    local_date = pd.to_datetime(feat.index.tz_convert("Asia/Taipei").strftime("%Y-%m-%d"))
    asof = pd.to_datetime(feat["instit_asof_date"])
    valid = asof.notna().to_numpy()
    ok = (asof[valid].to_numpy() < local_date[valid].to_numpy()).all()
    print(f"  [健檢] {tag} instit_asof 全部嚴格早於當日（排他式，無未來函數）: {'PASS' if ok else '✗FAIL'}")


def _report_truncation(feat: pd.DataFrame, instit_last) -> None:
    """量化 6446 斷尾效果：asof 凍結值、age 最大值、轉 stale 的列數。"""
    frozen = feat["instit_asof_date"].dropna().iloc[-1] if feat["instit_asof_date"].notna().any() else None
    max_age = feat["instit_age_days"].max()
    stale_n = int(feat["instit_stale"].sum()) if "instit_stale" in feat else 0
    nan_after_stale = int((feat["instit_stale"] & feat["foreign_net"].isna()).sum()) if "instit_stale" in feat else 0
    print(f"  [結果] asof 最終凍結於 = {frozen}（= 斷尾日 {instit_last}）；"
          f"instit_age_days 最大 = {max_age}；")
    print(f"         age>{MAX_AGE_DAYS} 轉 stale 的 bar 數 = {stale_n}，其中 foreign_net 已轉 NaN = {nan_after_stale}")


if __name__ == "__main__":
    ingest_prices()
    demo_alignment()
