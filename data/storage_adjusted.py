"""還原股價的凍結快照儲存（方案B，選配）——與 klines_tw.sqlite 完全分離。

**預設路徑是 `data/adjustment.py` 的 `apply_back_adjustment()` 動態計算**，
不落地、不過期、原始資料保持不動。本模組只在需要「固定錨點、之後能逐位
重現」時才用（例如 fold 對照的正式分析輪，比照 `klines.sqlite` 已凍結、
`fold 對照一律用凍結資料集」的既有紀律）——因為 cum_factor 不是 append-only
穩定的：日後該股票只要再發生一次除權息，同一批歷史 bar 的還原價全部要變。

為什麼是獨立 DB 檔（比照 storage_chips.py 2026-07-07 的既有設計理由）：
  - klines_tw.sqlite 是原始資料，日常寫入活動不進同一檔案，隔離失誤爆炸半徑。
  - 生命週期不同：原始 K 線只增不改，快照是「某個時間點的衍生重新表述」，
    可以整批重算/丟棄重建。

快照不會自動更新——之後有新除權息事件不會反映在舊快照裡，這是刻意的
（重現性優先於即時性）；`as_of_date` 記錄快照計算當下的錨點日期，同一
symbol/interval 可以並存多個不同 as_of_date 的快照。

用法：
    from data import storage_adjusted
    conn = storage_adjusted.connect()               # 預設 data/klines_tw_adjusted.sqlite
    storage_adjusted.save_adjusted_snapshot(conn, adjusted_df, as_of_date="2026-07-16")
    df = storage_adjusted.load_adjusted_snapshot(conn, "2603", "1d", "2026-07-16")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final, Optional

import pandas as pd

DEFAULT_DB_PATH: Final = Path(__file__).resolve().parent / "klines_tw_adjusted.sqlite"

_TABLE: Final = "klines_adjusted"

_CREATE_SQL: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    symbol      TEXT    NOT NULL,
    exchange    TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    ts          INTEGER NOT NULL,   -- UTC epoch 奈秒，語意同 klines 表（bar 開盤時間）
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    vwap        REAL,
    adj_factor  REAL    NOT NULL,   -- 該 bar 的累積調整因子（除錯/驗證用）
    as_of_date  TEXT    NOT NULL,   -- 快照錨點日期（ISO 字串，計算當下的「現在」）
    computed_at INTEGER NOT NULL,   -- UTC epoch 奈秒
    PRIMARY KEY (symbol, exchange, interval, ts, as_of_date)
)
"""

_SNAPSHOT_COLUMNS: Final = [
    "symbol", "exchange", "interval", "ts",
    "open", "high", "low", "close", "vwap", "adj_factor",
]


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """開啟（必要時建立）還原價快照 DB，設定 WAL 模式並確保 schema 存在。"""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def save_adjusted_snapshot(conn: sqlite3.Connection, adjusted_df: pd.DataFrame, as_of_date: str) -> int:
    """把 `apply_back_adjustment()` 的輸出 upsert 成一個凍結快照，回傳寫入列數。

    Args:
        adjusted_df: 需含 _SNAPSHOT_COLUMNS 欄位（`apply_back_adjustment` 的輸出
            已包含全部必要欄位，多餘欄位如 volume/turnover 會被忽略不落地）。
        as_of_date: 這批快照的錨點日期（ISO 字串，如 "2026-07-16"），與
            (symbol, exchange, interval, ts) 共同組成主鍵，允許同一 symbol
            並存多個不同錨點的快照。
    """
    missing = set(_SNAPSHOT_COLUMNS) - set(adjusted_df.columns)
    if missing:
        raise ValueError(f"adjusted_df 缺少必要欄位: {sorted(missing)}")
    if adjusted_df.empty:
        return 0

    payload = adjusted_df[_SNAPSHOT_COLUMNS].copy()
    payload["ts"] = pd.to_datetime(payload["ts"], utc=True).dt.as_unit("ns").astype("int64")
    payload["as_of_date"] = as_of_date
    payload["computed_at"] = pd.Timestamp.now(tz="UTC").as_unit("ns").value

    columns = _SNAPSHOT_COLUMNS + ["as_of_date", "computed_at"]
    rows = payload[columns].itertuples(index=False, name=None)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT OR REPLACE INTO {_TABLE} ({', '.join(columns)}) VALUES ({placeholders})",
        list(rows),
    )
    conn.commit()
    return len(payload)


def load_adjusted_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    as_of_date: str,
    exchange: Optional[str] = None,
) -> pd.DataFrame:
    """讀回指定錨點日期的還原價快照（依 ts 遞增排序；無資料回傳空表但欄位齊全）。"""
    query = (
        f"SELECT {', '.join(_SNAPSHOT_COLUMNS)} FROM {_TABLE} "
        "WHERE symbol=? AND interval=? AND as_of_date=?"
    )
    params: list = [symbol, interval, as_of_date]
    if exchange is not None:
        query += " AND exchange=?"
        params.append(exchange)
    query += " ORDER BY ts ASC"

    df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=_SNAPSHOT_COLUMNS)

    df["ts"] = pd.to_datetime(df["ts"], unit="ns", utc=True)
    return df.reset_index(drop=True)


def list_snapshot_as_of_dates(
    conn: sqlite3.Connection, symbol: str, interval: str, exchange: Optional[str] = None
) -> list[str]:
    """列出某 symbol/interval 已存在的快照錨點日期（方便檢查是否已有可重用的快照）。"""
    query = f"SELECT DISTINCT as_of_date FROM {_TABLE} WHERE symbol=? AND interval=?"
    params: list = [symbol, interval]
    if exchange is not None:
        query += " AND exchange=?"
        params.append(exchange)
    cur = conn.execute(query, params)
    return sorted(row[0] for row in cur.fetchall())
