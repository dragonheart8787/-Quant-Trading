"""研究用本地 K 線 DB（SQLite, WAL 模式）。

目的：把 canonical schema 的 K 線落地，讓所有回測/fold 對照使用同一份
固定資料集，不再受 Binance 即時抓取的資料漂移或跨環境重建誤差影響。

設計：
  - WAL journal mode：讀寫並行友善，研究期單機使用足夠。
  - 主鍵 (symbol, exchange, interval, ts)：INSERT OR REPLACE upsert，
    重複 ingest 同一段資料是冪等操作，不會產生重複列。
  - 時間戳（ts / ingested_at）以 int64 UTC 奈秒存放：無損往返、排序穩定，
    不依賴任何字串格式或 pandas 版本的序列化行為。
  - vwap 可為 NaN（volume=0 時），存為 NULL，讀回為 NaN。

用法：
    from data.storage_sqlite import connect, save_klines, load_klines
    conn = connect()                      # 預設 data/klines.sqlite
    save_klines(conn, canonical_df)       # upsert，回傳寫入列數
    df = load_klines(conn, "BTCUSDT", "1h")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final, Optional

import pandas as pd

from data.cleaning import CANONICAL_COLUMNS, validate_canonical

DEFAULT_DB_PATH: Final = Path(__file__).resolve().parent / "klines.sqlite"

_TABLE: Final = "klines"

_CREATE_SQL: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    ts          INTEGER NOT NULL,   -- UTC epoch 奈秒
    symbol      TEXT    NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL,
    turnover    REAL,
    vwap        REAL,               -- volume=0 時為 NULL（讀回 NaN）
    exchange    TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    source      TEXT,
    ingested_at INTEGER,            -- UTC epoch 奈秒
    PRIMARY KEY (symbol, exchange, interval, ts)
)
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """開啟（必要時建立）K 線 DB，設定 WAL 模式並確保 schema 存在。"""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def _ts_to_ns(series: pd.Series) -> pd.Series:
    """tz-aware datetime → int64 UTC 奈秒（顯式 as_unit 確保跨 pandas 版本一致）。"""
    dt = pd.to_datetime(series, utc=True)
    return dt.dt.as_unit("ns").astype("int64")


def save_klines(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """把 canonical DataFrame upsert 進 DB，回傳寫入列數。

    先過 validate_canonical（欄位齊全、ts 遞增無重複）才寫入。
    同主鍵（symbol, exchange, interval, ts）的既有列會被整列取代（冪等）。
    """
    validate_canonical(df)
    if df.empty:
        return 0

    payload = df.copy()
    payload["ts"] = _ts_to_ns(payload["ts"])
    payload["ingested_at"] = _ts_to_ns(payload["ingested_at"])

    rows = payload[CANONICAL_COLUMNS].itertuples(index=False, name=None)
    placeholders = ", ".join("?" for _ in CANONICAL_COLUMNS)
    conn.executemany(
        f"INSERT OR REPLACE INTO {_TABLE} ({', '.join(CANONICAL_COLUMNS)}) "
        f"VALUES ({placeholders})",
        list(rows),
    )
    conn.commit()
    return len(payload)


def load_klines(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    exchange: Optional[str] = None,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """讀回 canonical DataFrame（依 ts 遞增排序；無資料回傳空表但欄位齊全）。

    Args:
        symbol / interval: 必填過濾條件。
        exchange: 選填；None 表示不過濾（單一交易所資料時等價）。
        start / end: 選填時間範圍（含端點），tz-aware 或可被 to_datetime 解析。
    """
    query = f"SELECT {', '.join(CANONICAL_COLUMNS)} FROM {_TABLE} WHERE symbol=? AND interval=?"
    params: list = [symbol, interval]
    if exchange is not None:
        query += " AND exchange=?"
        params.append(exchange)
    if start is not None:
        query += " AND ts >= ?"
        params.append(int(pd.Timestamp(start).as_unit("ns").value))
    if end is not None:
        query += " AND ts <= ?"
        params.append(int(pd.Timestamp(end).as_unit("ns").value))
    query += " ORDER BY ts ASC"

    df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df["ts"] = pd.to_datetime(df["ts"], unit="ns", utc=True)
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], unit="ns", utc=True)
    validate_canonical(df)
    return df.reset_index(drop=True)


def count_klines(conn: sqlite3.Connection, symbol: str, interval: str) -> int:
    """回傳 DB 中該 symbol/interval 的列數（ingest 後檢查用）。"""
    cur = conn.execute(
        f"SELECT COUNT(*) FROM {_TABLE} WHERE symbol=? AND interval=?", (symbol, interval)
    )
    return int(cur.fetchone()[0])
