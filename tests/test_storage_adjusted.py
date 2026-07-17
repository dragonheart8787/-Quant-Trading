"""storage_adjusted（還原價凍結快照，方案B選配）測試。

驗證重點：
  - WAL journal mode 實際生效
  - save/load 往返（ts 到奈秒、adj_factor 精確相等）
  - upsert 冪等：同一批快照存兩次列數不變
  - 同一 symbol 可並存多個不同 as_of_date 的快照，互不覆蓋
  - 缺必要欄位拋錯、空表存入回傳 0
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.adjustment import apply_back_adjustment
from data.storage_adjusted import (
    connect,
    list_snapshot_as_of_dates,
    load_adjusted_snapshot,
    save_adjusted_snapshot,
)
from tests.test_price_adjustment import _2603_EVENT, _2603_JUN2023, _dividends, _klines

SYMBOL, INTERVAL = "2603", "1d"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test_klines_tw_adjusted.sqlite")
    yield c
    c.close()


def _adjusted_df() -> pd.DataFrame:
    klines = _klines(_2603_JUN2023)
    dividends = _dividends(_2603_EVENT)
    return apply_back_adjustment(klines, dividends)


def test_wal_mode_enabled(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_roundtrip_exact(conn):
    adjusted = _adjusted_df()
    n = save_adjusted_snapshot(conn, adjusted, as_of_date="2026-07-16")
    assert n == len(adjusted)

    loaded = load_adjusted_snapshot(conn, SYMBOL, INTERVAL, "2026-07-16")
    assert len(loaded) == len(adjusted)
    pd.testing.assert_series_equal(
        loaded["ts"].dt.as_unit("ns"), adjusted["ts"].dt.as_unit("ns"), check_names=False
    )
    for col in ["open", "high", "low", "close", "adj_factor"]:
        np.testing.assert_array_equal(loaded[col].to_numpy(), adjusted[col].to_numpy())


def test_upsert_idempotent(conn):
    adjusted = _adjusted_df()
    save_adjusted_snapshot(conn, adjusted, as_of_date="2026-07-16")
    save_adjusted_snapshot(conn, adjusted, as_of_date="2026-07-16")
    loaded = load_adjusted_snapshot(conn, SYMBOL, INTERVAL, "2026-07-16")
    assert len(loaded) == len(adjusted)


def test_multiple_as_of_dates_coexist(conn):
    """同一 symbol 之後又發生一次除權息，重新計算的新快照不會覆蓋舊快照
    （重現性：舊分析輪引用舊 as_of_date 仍能拿到原本的數字）。"""
    klines = _klines(_2603_JUN2023)
    old_snapshot = apply_back_adjustment(klines, _dividends([]))          # 舊錨點：還不知道有這個事件
    new_snapshot = apply_back_adjustment(klines, _dividends(_2603_EVENT))  # 新錨點：事件已發生

    save_adjusted_snapshot(conn, old_snapshot, as_of_date="2023-06-29")
    save_adjusted_snapshot(conn, new_snapshot, as_of_date="2026-07-16")

    loaded_old = load_adjusted_snapshot(conn, SYMBOL, INTERVAL, "2023-06-29")
    loaded_new = load_adjusted_snapshot(conn, SYMBOL, INTERVAL, "2026-07-16")

    assert not np.array_equal(loaded_old["close"].to_numpy(), loaded_new["close"].to_numpy())
    assert sorted(list_snapshot_as_of_dates(conn, SYMBOL, INTERVAL)) == ["2023-06-29", "2026-07-16"]


def test_save_missing_columns_raises(conn):
    bad = pd.DataFrame({"symbol": ["2603"], "ts": [pd.Timestamp.now(tz="UTC")]})
    with pytest.raises(ValueError, match="缺少必要欄位"):
        save_adjusted_snapshot(conn, bad, as_of_date="2026-07-16")


def test_save_empty_returns_zero(conn):
    empty = _adjusted_df().iloc[0:0]
    assert save_adjusted_snapshot(conn, empty, as_of_date="2026-07-16") == 0


def test_load_missing_returns_empty_with_columns(conn):
    loaded = load_adjusted_snapshot(conn, "NOPE", INTERVAL, "2026-07-16")
    assert loaded.empty
    assert "adj_factor" in loaded.columns
