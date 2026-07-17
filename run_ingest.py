"""K 線落地：抓取（或匯入 CSV）→ canonical → 存入 data/klines.sqlite（WAL）。

用法：
    # 從 Binance 抓最新 3000 根 BTCUSDT 1h 並落地（upsert，重跑冪等）
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python run_ingest.py

    # 從既有 canonical CSV 匯入（例如先前的快取檔）
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python run_ingest.py --from-csv path/to.csv

    # 指定 symbol（多 symbol 擴充，2026-07-15；同一 klines.sqlite 天然共存，
    # PRIMARY KEY 含 symbol，不需新 DB）
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python run_ingest.py --symbol ETHUSDT --limit 26000
"""

from __future__ import annotations

import argparse

import pandas as pd

from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from data.storage_sqlite import DEFAULT_DB_PATH, connect, count_klines, load_klines, save_klines

SYMBOL, INTERVAL, LIMIT = "BTCUSDT", "1h", 3000


def main() -> None:
    parser = argparse.ArgumentParser(description="K 線落地到本地 SQLite（WAL）")
    parser.add_argument("--from-csv", default=None, help="從 canonical CSV 匯入，不抓 Binance")
    parser.add_argument("--db", default=None, help=f"DB 路徑（預設 {DEFAULT_DB_PATH}）")
    parser.add_argument("--symbol", default=SYMBOL,
                        help=f"交易對（預設 {SYMBOL}；同一 DB 可存多 symbol，PRIMARY KEY 含 symbol）")
    parser.add_argument("--limit", type=int, default=LIMIT,
                        help=f"抓取根數（預設 {LIMIT}；>1000 自動分頁往前抓）")
    args = parser.parse_args()
    symbol = args.symbol

    if args.from_csv:
        data = pd.read_csv(args.from_csv, parse_dates=["ts", "ingested_at"])
        print(f"[來源] CSV {args.from_csv}")
    else:
        raw = fetch_klines(symbol=symbol, interval=INTERVAL, limit=args.limit)
        data = to_canonical_from_binance(raw)
        print(f"[來源] Binance {symbol} {INTERVAL} 最新 {args.limit} 根")
    validate_canonical(data)

    conn = connect(args.db)
    try:
        written = save_klines(conn, data)
        total = count_klines(conn, symbol, INTERVAL)
        stored = load_klines(conn, symbol, INTERVAL)
        print(f"[寫入] {written} 列（upsert）")
        print(f"[DB]   {symbol} {INTERVAL} 共 {total} 列  "
              f"{stored['ts'].iloc[0]} -> {stored['ts'].iloc[-1]}")
        print(f"[路徑] {args.db or DEFAULT_DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
