"""台股籌碼本地 DB（SQLite, WAL 模式）——與 klines.sqlite 完全分離。

為什麼是獨立 DB 檔（2026-07-07 使用者核可的設計）：
  - klines.sqlite 已凍結且有校驗和驗證紀律，日常寫入活動不進同一檔案，
    隔離失誤爆炸半徑。
  - 生命週期不同：K 線凍結不動 vs 籌碼日更活躍。
  - 兩條資料流在 indicators/features.py 才依時間對齊合併，儲存層不合併。

設計比照 storage_sqlite.py：WAL、INSERT OR REPLACE 冪等 upsert、
主鍵含 source（未來換 TWSE 官方源時可雙源並存比對）。
date 為 ISO 字串（YYYY-MM-DD，台北交易日），理由見 data/cleaning.py 籌碼區段。

用法：
    from data import storage_chips
    conn = storage_chips.connect()                    # 預設 data/chips_tw.sqlite
    storage_chips.save_chips(conn, df, "chip_margin") # upsert，回傳寫入列數
    df = storage_chips.load_chips(conn, "chip_margin", stock_id="2330")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final, Optional

import pandas as pd

from data.cleaning import (
    CHIP_HOLDING_DISPERSION_COLUMNS,
    CHIP_INSTITUTIONAL_COLUMNS,
    CHIP_MARGIN_COLUMNS,
    CHIP_SHAREHOLDING_COLUMNS,
)

DEFAULT_DB_PATH: Final = Path(__file__).resolve().parent / "chips_tw.sqlite"

# 每資料集一張表；共用前綴 date/stock_id/source/ingested_at（見 cleaning.py）
CHIP_TABLES: Final = {
    "chip_institutional": {
        "columns": CHIP_INSTITUTIONAL_COLUMNS,
        "pk": ("date", "stock_id", "investor_type", "source"),
        "ddl": """
            CREATE TABLE IF NOT EXISTS chip_institutional (
                date          TEXT NOT NULL,      -- YYYY-MM-DD 台北交易日
                stock_id      TEXT NOT NULL,
                investor_type TEXT NOT NULL,      -- FinMind name 值（長格式）
                buy           INTEGER NOT NULL,
                sell          INTEGER NOT NULL,
                net           INTEGER NOT NULL,   -- buy - sell
                source        TEXT NOT NULL,
                ingested_at   TEXT,
                PRIMARY KEY (date, stock_id, investor_type, source)
            )
        """,
    },
    "chip_margin": {
        "columns": CHIP_MARGIN_COLUMNS,
        "pk": ("date", "stock_id", "source"),
        "ddl": """
            CREATE TABLE IF NOT EXISTS chip_margin (
                date                     TEXT NOT NULL,
                stock_id                 TEXT NOT NULL,
                margin_buy               INTEGER NOT NULL,
                margin_sell              INTEGER NOT NULL,
                margin_cash_repayment    INTEGER NOT NULL,
                margin_balance_yesterday INTEGER NOT NULL,
                margin_balance_today     INTEGER NOT NULL,
                margin_limit             INTEGER,           -- NULL=源頭無資料（哨兵 -1000000 轉換）
                short_buy                INTEGER NOT NULL,
                short_sell               INTEGER NOT NULL,
                short_cash_repayment     INTEGER NOT NULL,
                short_balance_yesterday  INTEGER NOT NULL,
                short_balance_today      INTEGER NOT NULL,
                short_limit              INTEGER,           -- NULL=同上（TPEx 早期）
                offset_loan_and_short    INTEGER,           -- NULL=同上（TPEx ~2008-09）
                note                     TEXT,
                source                   TEXT NOT NULL,
                ingested_at              TEXT,
                PRIMARY KEY (date, stock_id, source)
            )
        """,
    },
    "chip_foreign_shareholding": {
        "columns": CHIP_SHAREHOLDING_COLUMNS,
        "pk": ("date", "stock_id", "source"),
        "ddl": """
            CREATE TABLE IF NOT EXISTS chip_foreign_shareholding (
                date                      TEXT NOT NULL,
                stock_id                  TEXT NOT NULL,
                foreign_shares            INTEGER NOT NULL,
                foreign_remaining_shares  INTEGER NOT NULL,
                foreign_shares_ratio      REAL NOT NULL,
                foreign_remain_ratio      REAL NOT NULL,
                foreign_upper_limit_ratio REAL NOT NULL,
                chinese_upper_limit_ratio REAL NOT NULL,
                number_of_shares_issued   INTEGER NOT NULL,
                source                    TEXT NOT NULL,
                ingested_at               TEXT,
                PRIMARY KEY (date, stock_id, source)
            )
        """,
    },
    "chip_holding_dispersion": {
        "columns": CHIP_HOLDING_DISPERSION_COLUMNS,
        "pk": ("date", "stock_id", "holding_level", "source"),
        "ddl": """
            CREATE TABLE IF NOT EXISTS chip_holding_dispersion (
                date          TEXT NOT NULL,      -- 週頻（公布時點語意 POC 記錄中）
                stock_id      TEXT NOT NULL,
                holding_level TEXT NOT NULL,      -- 持股級距（原樣保留）
                people        INTEGER NOT NULL,
                percent       REAL NOT NULL,
                unit          INTEGER NOT NULL,
                source        TEXT NOT NULL,
                ingested_at   TEXT,
                PRIMARY KEY (date, stock_id, holding_level, source)
            )
        """,
    },
}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """開啟（必要時建立）籌碼 DB，設定 WAL 模式並確保四張表存在。"""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    for spec in CHIP_TABLES.values():
        conn.execute(spec["ddl"])
    conn.commit()
    return conn


def save_chips(conn: sqlite3.Connection, df: pd.DataFrame, table: str) -> int:
    """把籌碼 canonical DataFrame upsert 進指定表，回傳寫入列數。

    同主鍵既有列整列取代（冪等）。欄位不齊直接丟 ValueError。
    """
    if table not in CHIP_TABLES:
        raise ValueError(f"未知的籌碼表: {table}（可用: {sorted(CHIP_TABLES)}）")
    columns = CHIP_TABLES[table]["columns"]
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{table} 缺少欄位: {sorted(missing)}")
    if df.empty:
        return 0

    # pd.NA / NaN → None（nullable 欄位存 NULL；sqlite 不認得 pandas 缺值物件）；
    # numpy/nullable 標量 → Python 原生型別（.item()）——nullable Int64 經
    # itertuples 產出的 numpy 標量會被 sqlite fallback 成 8-byte blob
    # （2026-07-16 A1 技術債修復；歷史資料已遷移，見 HANDOFF「技術債清理」節）
    rows = [
        tuple(
            None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)
            for v in row
        )
        for row in df[columns].itertuples(index=False, name=None)
    ]
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return len(df)


def load_chips(
    conn: sqlite3.Connection,
    table: str,
    stock_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """讀回籌碼 canonical DataFrame（依 date 遞增；無資料回空表但欄位齊全）。

    start / end 為 ISO 日期字串（含端點）；ISO 字串的字典序 = 時間序。
    """
    if table not in CHIP_TABLES:
        raise ValueError(f"未知的籌碼表: {table}（可用: {sorted(CHIP_TABLES)}）")
    columns = CHIP_TABLES[table]["columns"]

    query = f"SELECT {', '.join(columns)} FROM {table} WHERE 1=1"
    params: list = []
    if stock_id is not None:
        query += " AND stock_id=?"
        params.append(stock_id)
    if start is not None:
        query += " AND date >= ?"
        params.append(start)
    if end is not None:
        query += " AND date <= ?"
        params.append(end)
    query += " ORDER BY date ASC"

    df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=columns)
    return df.reset_index(drop=True)


def count_chips(conn: sqlite3.Connection, table: str, stock_id: Optional[str] = None) -> int:
    """回傳指定表（可選指定個股）的列數（ingest 後檢查用）。"""
    if table not in CHIP_TABLES:
        raise ValueError(f"未知的籌碼表: {table}")
    query = f"SELECT COUNT(*) FROM {table}"
    params: list = []
    if stock_id is not None:
        query += " WHERE stock_id=?"
        params.append(stock_id)
    cur = conn.execute(query, params)
    return int(cur.fetchone()[0])
