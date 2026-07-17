"""台股公司行動事件快照 ingest（A4 技術債輪，2026-07-16）。

抓 4 檔的除權息結果（TaiwanStockDividendResult）與停券日曆
（TaiwanStockMarginShortSaleSuspension）→ 落地 data/events_tw.sqlite。
落地後所有分析 runner 一律讀本地快照，不再即時打 FinMind（外部依賴風險，
見 data/storage_events_tw.py docstring）。upsert 冪等，可重跑刷新。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ingest_tw_events.py
"""

from __future__ import annotations

from data import storage_events_tw as ev
from data.providers.finmind import DATASET_DIVIDEND_RESULT, fetch_dataset

SYMBOLS = ["2330", "2603", "8069", "6446"]


def main() -> None:
    conn = ev.connect()
    try:
        for sym in SYMBOLS:
            div = fetch_dataset(DATASET_DIVIDEND_RESULT, sym, "2000-01-01", "2026-12-31")
            n_div = ev.save_dividend_results(conn, div)
            susp = fetch_dataset(
                "TaiwanStockMarginShortSaleSuspension", sym, "2000-01-01", "2026-12-31"
            )
            n_susp = ev.save_suspensions(conn, susp)
            back_div = ev.load_dividend_results(conn, sym)
            back_susp = ev.load_suspensions(conn, sym)
            print(
                f"{sym}: 除權息 寫入 {n_div} / 庫內 {len(back_div)} 筆"
                f"（{back_div['date'].min() if len(back_div) else '-'} ~ "
                f"{back_div['date'].max() if len(back_div) else '-'}）  "
                f"停券 寫入 {n_susp} / 庫內 {len(back_susp)} 筆"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
