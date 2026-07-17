"""Phase 3 POC：FinMind 台股籌碼抓取 → canonical → data/chips_tw.sqlite 落地。

用法（POC 預設 = 12 檔代表股 × 4 資料集全歷史）：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ingest_chips.py
    選項：--stocks 2330,2317  --datasets institutional,margin
          --start 2020-01-01  --end 2024-12-31  --db path.sqlite  --no-checks
    環境變數：FINMIND_TOKEN（可選）、FINMIND_MIN_INTERVAL（節流間隔秒）

POC 首日驗證（使用者指定，2026-07-07；結果印在 ingest 之後）：
  1. 融資恆等式 balance_today = yesterday + buy - sell - cash_repayment：
     用 2330 全歷史實測，記錄失敗率與失敗日樣態（除權息日/融資限額變動日
     交叉比對），再由使用者決定是否納入 validate()。
  2. 週頻股權分散表公布時點：記錄回傳 date 的星期分布、間隔、最新一筆距今
     天數——先記事實，對齊規則設計留給 features.py 階段（前視偏誤風險評估用）。
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data import storage_chips
from data.cleaning import (
    check_margin_balance_identity,
    finmind_holding_dispersion_to_canonical,
    finmind_institutional_to_canonical,
    finmind_margin_to_canonical,
    finmind_shareholding_to_canonical,
)
from data.providers import finmind

# 12 檔代表股（2026-07-07 使用者核可；跨產業/市值/市場，含 2 檔上櫃、1 檔小型股）
POC_STOCKS = {
    "2330": "台積電（半導體/超大型/外資主導）",
    "2317": "鴻海（電子代工/大型）",
    "2454": "聯發科（IC設計/大型）",
    "2881": "富邦金（金融/大型）",
    "2412": "中華電（電信/大型/防禦對照組）",
    "2002": "中鋼（鋼鐵傳產/大型/散戶存股）",
    "2603": "長榮（航運/中大型/融資券活躍）",
    "3008": "大立光（光學/高價股）",
    "6446": "藥華藥（生技/中型/題材股）",
    "8069": "元太（電子紙/中型/上櫃-TPEx覆蓋驗證）",
    "5483": "中美晶（半導體/中型/上櫃-年資長）",
    "1723": "中碳（碳材/小型/資料品質下限樣本）",
}

# dataset 短名 → (抓取函式, canonical 轉換, 目的表)
DATASET_PIPES = {
    "institutional": (
        finmind.fetch_institutional, finmind_institutional_to_canonical, "chip_institutional",
    ),
    "margin": (
        finmind.fetch_margin_short, finmind_margin_to_canonical, "chip_margin",
    ),
    "shareholding": (
        finmind.fetch_shareholding, finmind_shareholding_to_canonical, "chip_foreign_shareholding",
    ),
    "dispersion": (
        finmind.fetch_holding_dispersion, finmind_holding_dispersion_to_canonical,
        "chip_holding_dispersion",
    ),
}


def check_stock_universe(stocks: dict) -> None:
    """用 TaiwanStockInfo 核對 12 檔的產業別/上市上櫃，找不到的股票要警示。"""
    print("=" * 78)
    print("[0] TaiwanStockInfo 核對股票清單（產業別 / 上市 twse・上櫃 tpex）")
    info = finmind.fetch_stock_info()
    if info.empty:
        print("  ! TaiwanStockInfo 回傳空表，無法核對（不擋 ingest）")
        return
    hit = info[info["stock_id"].astype(str).isin(stocks)]
    seen = set()
    for _, row in hit.iterrows():
        if row["stock_id"] in seen:  # 同一股票可能多個產業分類列
            continue
        seen.add(row["stock_id"])
        print(f"  {row['stock_id']} {row.get('stock_name', '?'):　<8} "
              f"type={row.get('type', '?'):<5} industry={row.get('industry_category', '?')}")
    missing = set(stocks) - set(hit["stock_id"].astype(str))
    if missing:
        print(f"  ★ 警示：TaiwanStockInfo 找不到 {sorted(missing)}（確認代碼/是否下市）")


def ingest(stocks: dict, datasets: list, start: str | None, end: str | None,
           conn) -> list:
    """逐（股票 × 資料集）抓取→轉 canonical→upsert。單筆失敗記錄後繼續。"""
    failures = []
    total = len(stocks) * len(datasets)
    i = 0
    for stock_id in stocks:
        for ds in datasets:
            i += 1
            fetch, to_canonical, table = DATASET_PIPES[ds]
            try:
                raw = fetch(stock_id, start_date=start, end_date=end)
                canonical = to_canonical(raw)
                n = storage_chips.save_chips(conn, canonical, table)
                dropped = canonical.attrs.get("dropped_rows", 0)
                dmin = canonical["date"].min() if n else "-"
                dmax = canonical["date"].max() if n else "-"
                drop_note = f" dropped={dropped}" if dropped else ""
                print(f"  [{i:2d}/{total}] {stock_id} {ds:<13} rows={n:>6}  "
                      f"{dmin} → {dmax}{drop_note}")
            except Exception as exc:  # POC 要完整覆蓋圖，單筆失敗不中斷
                failures.append((stock_id, ds, repr(exc)))
                print(f"  [{i:2d}/{total}] {stock_id} {ds:<13} ★失敗: {exc}")
    return failures


def poc_margin_identity(conn, stock_id: str = "2330") -> None:
    """POC 首日驗證 1：融資恆等式（2330 全歷史），失敗率＋失敗日樣態。"""
    print("=" * 78)
    print(f"[1] 融資恆等式驗證（{stock_id} 全歷史）："
          "balance_today = yesterday + buy - sell - cash_repayment")
    margin = storage_chips.load_chips(conn, "chip_margin", stock_id=stock_id)
    if margin.empty:
        print("  ! chip_margin 無資料，跳過")
        return
    violations = check_margin_balance_identity(margin)
    rate = len(violations) / len(margin)
    print(f"  總列數 {len(margin)}（{margin['date'].min()} → {margin['date'].max()}）")
    print(f"  違反列數 {len(violations)}，失敗率 {rate:.4%}")
    if violations.empty:
        print("  → 恆等式全歷史成立，可考慮納入 validate()（等使用者決定）")
        return

    # 樣態一：違反日是否為融資限額變動日（現增/減資/股本變動的代理訊號）
    limit_changed = margin["margin_limit"].diff() != 0
    limit_change_dates = set(margin.loc[limit_changed, "date"]) - {margin["date"].iloc[0]}
    hit_limit = violations["date"].isin(limit_change_dates).sum()

    # 樣態二：違反日是否為除權息日（TaiwanStockDividendResult 對照，best-effort）
    div_note = ""
    try:
        div = finmind.fetch_dataset(finmind.DATASET_DIVIDEND_RESULT, stock_id)
        if not div.empty:
            div_dates = set(pd.to_datetime(div["date"]).dt.strftime("%Y-%m-%d"))
            hit_div = violations["date"].isin(div_dates).sum()
            div_note = f"除權息日命中 {hit_div}/{len(violations)}；"
        else:
            div_note = "除權息資料集回傳空表；"
    except Exception as exc:
        div_note = f"除權息對照失敗（{exc}）；"

    print(f"  樣態：{div_note}融資限額變動日命中 {hit_limit}/{len(violations)}")
    show = violations.head(10)[["date", "margin_balance_yesterday", "margin_buy",
                                "margin_sell", "margin_cash_repayment",
                                "margin_balance_today", "residual"]]
    print("  前 10 筆違反樣本：")
    print(show.to_string(index=False))
    print("  → 是否納入 validate() 等使用者依上述失敗率/樣態決定")


def poc_dispersion_timing(conn, stocks: dict) -> None:
    """POC 首日驗證 2：股權分散表 date 的星期/間隔/新鮮度——只記事實。"""
    print("=" * 78)
    print("[2] 股權分散表（週頻）公布時點事實記錄（對齊規則設計留給 features.py 階段）")
    today = pd.Timestamp.today().normalize()
    for stock_id in stocks:
        df = storage_chips.load_chips(conn, "chip_holding_dispersion", stock_id=stock_id)
        if df.empty:
            print(f"  {stock_id}: 無資料")
            continue
        dates = pd.to_datetime(sorted(df["date"].unique()))
        weekdays = pd.Series(dates.dayofweek).value_counts().sort_index()
        wd_str = ", ".join(f"{'一二三四五六日'[k]}:{v}" for k, v in weekdays.items())
        gaps = pd.Series(dates).diff().dt.days.dropna()
        gap_mode = int(gaps.mode().iloc[0]) if not gaps.empty else 0
        lag = (today - dates[-1]).days
        print(f"  {stock_id}: {len(dates)} 個週次（{dates[0].date()} → {dates[-1].date()}）"
              f" 星期分布[{wd_str}] 間隔眾數={gap_mode}天 最新一筆距今={lag}天")
    print("  → date 是「資料基準日」；實際公布延遲需下週再抓一次對照（記錄在 HANDOFF）")


def poc_misc_checks(conn) -> None:
    """其餘 POC 事實記錄：法人類別值、TPEx 覆蓋、單位量級。"""
    print("=" * 78)
    print("[3] 法人買賣超 investor_type distinct 值（設計時假設 5 種，實測核對）")
    rows = conn.execute(
        "SELECT investor_type, COUNT(*) FROM chip_institutional GROUP BY investor_type"
    ).fetchall()
    for name, cnt in rows:
        print(f"  {name}: {cnt} 列")

    print("[4] TPEx 覆蓋（8069 元太、5483 中美晶）各表列數與日期範圍")
    for stock_id in ("8069", "5483"):
        for table in storage_chips.CHIP_TABLES:
            n = storage_chips.count_chips(conn, table, stock_id=stock_id)
            if n:
                r = conn.execute(
                    f"SELECT MIN(date), MAX(date) FROM {table} WHERE stock_id=?", (stock_id,)
                ).fetchone()
                print(f"  {stock_id} {table:<26} rows={n:>6}  {r[0]} → {r[1]}")
            else:
                print(f"  {stock_id} {table:<26} rows=     0  ★TPEx 無覆蓋？")

    print("[5] 融資融券單位量級核對（2330 最新一筆 vs 發行股數）")
    m = storage_chips.load_chips(conn, "chip_margin", stock_id="2330")
    s = storage_chips.load_chips(conn, "chip_foreign_shareholding", stock_id="2330")
    if not m.empty and not s.empty:
        latest_m, latest_s = m.iloc[-1], s.iloc[-1]
        print(f"  margin_balance_today={latest_m['margin_balance_today']:,} "
              f"margin_limit={latest_m['margin_limit']:,} "
              f"number_of_shares_issued={latest_s['number_of_shares_issued']:,}")
        print("  → 若 balance 量級為萬~十萬且 limit ≈ 發行股數/4/1000，單位為「張」")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FinMind 台股籌碼 ingest + POC 驗證")
    parser.add_argument("--stocks", default=None, help="逗號分隔股票代碼（預設 12 檔 POC 清單）")
    parser.add_argument("--datasets", default=None,
                        help=f"逗號分隔資料集（預設全部：{','.join(DATASET_PIPES)}）")
    parser.add_argument("--start", default=None, help="起日 YYYY-MM-DD（預設各資料集官方起始日）")
    parser.add_argument("--end", default=None, help="迄日 YYYY-MM-DD（預設今日）")
    parser.add_argument("--db", default=None, help="DB 路徑（預設 data/chips_tw.sqlite）")
    parser.add_argument("--no-checks", action="store_true", help="只 ingest，跳過 POC 檢查")
    args = parser.parse_args(argv)

    stocks = ({s: POC_STOCKS.get(s, "?") for s in args.stocks.split(",")}
              if args.stocks else dict(POC_STOCKS))
    datasets = args.datasets.split(",") if args.datasets else list(DATASET_PIPES)
    unknown = set(datasets) - set(DATASET_PIPES)
    if unknown:
        parser.error(f"未知資料集: {sorted(unknown)}")

    conn = storage_chips.connect(args.db)
    try:
        if not args.no_checks:
            check_stock_universe(stocks)
        print("=" * 78)
        print(f"開始 ingest：{len(stocks)} 檔 × {len(datasets)} 資料集 → "
              f"{args.db or storage_chips.DEFAULT_DB_PATH}")
        failures = ingest(stocks, datasets, args.start, args.end, conn)

        if not args.no_checks:
            poc_margin_identity(conn)
            poc_dispersion_timing(conn, stocks)
            poc_misc_checks(conn)

        print("=" * 78)
        if failures:
            print(f"完成，但 {len(failures)} 筆失敗：")
            for stock_id, ds, err in failures:
                print(f"  {stock_id} {ds}: {err}")
            return 1
        print("全部完成，無失敗。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
