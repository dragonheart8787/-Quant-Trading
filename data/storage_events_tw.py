"""台股公司行動事件快照 DB（A4 技術債輪，2026-07-16）——與 K 線/籌碼分檔。

動機：除權息（TaiwanStockDividendResult）與停券日曆
（TaiwanStockMarginShortSaleSuspension）先前由各 runner 即時抓取。FinMind
是不受我們控制的外部依賴（TaiwanStockPriceAdj 鎖付費牆為既有先例），API
改版/收費/配額變動即斷「已交付數字可重現」的鏈條——與 utcnow deprecation
「環境可能被迫改變」同類的外部風險。比照 storage_adjusted（方案B）與
storage_chips 的既有慣例落地快照：

  - 獨立 DB 檔 `data/events_tw.sqlite`（隔離失誤爆炸半徑，生命週期=低頻
    append：除權息結果與停券公告只增不改）。
  - INSERT OR REPLACE 冪等 upsert；`fetched_at` 記錄抓取時間。
  - 抓取入口 `run_ingest_tw_events.py`；分析 runner 一律讀本地 DB，
    不再即時打 API。

用法：
    from data import storage_events_tw as ev
    conn = ev.connect()
    ev.save_dividend_results(conn, fetch_dataset(DATASET_DIVIDEND_RESULT, "2603"))
    df = ev.load_dividend_results(conn, "2603")
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Optional

import pandas as pd

DEFAULT_DB_PATH: Final = Path(__file__).resolve().parent / "events_tw.sqlite"

DIVIDEND_COLUMNS: Final = [
    "date", "stock_id", "before_price", "after_price",
    "stock_and_cache_dividend", "stock_or_cache_dividend",
    "max_price", "min_price", "open_price", "reference_price",
]
SUSPENSION_COLUMNS: Final = ["stock_id", "date", "end_date", "reason"]

_CREATE_DIVIDEND: Final = """
CREATE TABLE IF NOT EXISTS dividend_result (
    date                      TEXT NOT NULL,   -- 除權息交易日（YYYY-MM-DD）
    stock_id                  TEXT NOT NULL,
    before_price              REAL NOT NULL,
    after_price               REAL NOT NULL,
    stock_and_cache_dividend  REAL,
    stock_or_cache_dividend   TEXT,
    max_price                 REAL,
    min_price                 REAL,
    open_price                REAL,
    reference_price           REAL,
    fetched_at                TEXT NOT NULL,
    PRIMARY KEY (stock_id, date)
)
"""
_CREATE_SUSPENSION: Final = """
CREATE TABLE IF NOT EXISTS short_sale_suspension (
    stock_id    TEXT NOT NULL,
    date        TEXT NOT NULL,   -- 停券起始日 = 最後回補日
    end_date    TEXT NOT NULL,   -- 停券結束日
    reason      TEXT NOT NULL,   -- 除息/除權/除權息/股東常會/現金增資/減資
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (stock_id, date)
)
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """開啟（必要時建立）事件快照 DB，設定 WAL 並確保 schema 存在。"""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_DIVIDEND)
    conn.execute(_CREATE_SUSPENSION)
    conn.commit()
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(conn: sqlite3.Connection, df: pd.DataFrame, table: str, columns: list[str]) -> int:
    if df.empty:
        return 0
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{table} 缺少必要欄位: {sorted(missing)}")
    payload = df[columns].copy()
    payload["fetched_at"] = _utc_now_iso()
    cols = columns + ["fetched_at"]
    # object 化 + 原生型別轉換，避免 numpy/nullable 型別落地成 blob
    # （chip_margin short_limit blob 事故的教訓，見 storage_chips.py）
    rows = [
        tuple(v.item() if hasattr(v, "item") else v for v in row)
        for row in payload[cols].itertuples(index=False, name=None)
    ]
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return len(rows)


def save_dividend_results(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """upsert TaiwanStockDividendResult 原始欄位 DataFrame，回傳寫入列數。"""
    return _save(conn, df, "dividend_result", DIVIDEND_COLUMNS)


def save_suspensions(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """upsert TaiwanStockMarginShortSaleSuspension 原始欄位 DataFrame。"""
    return _save(conn, df, "short_sale_suspension", SUSPENSION_COLUMNS)


def _load(
    conn: sqlite3.Connection, table: str, columns: list[str], stock_id: Optional[str]
) -> pd.DataFrame:
    query = f"SELECT {', '.join(columns)} FROM {table} WHERE 1=1"
    params: list = []
    if stock_id is not None:
        query += " AND stock_id=?"
        params.append(stock_id)
    query += " ORDER BY date ASC"
    df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=columns)
    return df.reset_index(drop=True)


def load_dividend_results(
    conn: sqlite3.Connection, stock_id: Optional[str] = None
) -> pd.DataFrame:
    """讀回除權息事件（依 date 遞增；欄位相容 apply_back_adjustment）。"""
    return _load(conn, "dividend_result", DIVIDEND_COLUMNS, stock_id)


def load_suspensions(
    conn: sqlite3.Connection, stock_id: Optional[str] = None
) -> pd.DataFrame:
    """讀回停券日曆（依 date 遞增）。"""
    return _load(conn, "short_sale_suspension", SUSPENSION_COLUMNS, stock_id)
