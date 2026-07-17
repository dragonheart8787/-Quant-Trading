"""storage_sqlite（K 線落地）測試。

驗證關鍵不變量：
  - WAL journal mode 實際生效
  - canonical DataFrame 無損往返（ts/ingested_at 到奈秒、float 精確相等）
  - upsert 冪等：同一批資料存兩次列數不變
  - 增量匯入重疊區段不產生重複列
  - start/end 時間範圍過濾（含端點）
  - 亂序分批寫入後讀回仍依 ts 遞增
  - vwap NaN（volume=0）往返仍為 NaN
  - 查無資料回傳空表但欄位齊全
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cleaning import CANONICAL_COLUMNS, to_canonical_from_binance, validate_canonical
from data.storage_sqlite import connect, count_klines, load_klines, save_klines
from tests.test_indicators_and_cleaning import _fake_binance_raw

SYMBOL, INTERVAL = "TESTUSDT", "1h"


def _canonical(n: int = 50) -> pd.DataFrame:
    raw = _fake_binance_raw(n)
    raw.attrs["symbol"] = SYMBOL
    raw.attrs["interval"] = INTERVAL
    return to_canonical_from_binance(raw)


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test_klines.sqlite")
    yield c
    c.close()


def test_wal_mode_enabled(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_roundtrip_exact(conn):
    df = _canonical(50)
    save_klines(conn, df)
    loaded = load_klines(conn, SYMBOL, INTERVAL)

    validate_canonical(loaded)
    assert list(loaded.columns) == CANONICAL_COLUMNS
    assert len(loaded) == len(df)
    # 時間戳到奈秒無損
    pd.testing.assert_series_equal(
        loaded["ts"].dt.as_unit("ns"), df["ts"].dt.as_unit("ns"), check_names=False
    )
    # 數值欄位精確相等（不是近似）
    for col in ["open", "high", "low", "close", "volume", "turnover", "vwap"]:
        np.testing.assert_array_equal(
            loaded[col].to_numpy(), df[col].to_numpy(), err_msg=f"{col} 往返後不相等"
        )
    for col in ["symbol", "exchange", "interval", "source"]:
        assert (loaded[col] == df[col]).all()


def test_upsert_idempotent(conn):
    df = _canonical(50)
    assert save_klines(conn, df) == 50
    assert save_klines(conn, df) == 50  # 第二次仍回報寫入列數
    assert count_klines(conn, SYMBOL, INTERVAL) == 50  # 但 DB 不重複


def test_incremental_overlap_no_duplicates(conn):
    df = _canonical(150)
    save_klines(conn, df.iloc[:100].reset_index(drop=True))
    save_klines(conn, df.iloc[50:].reset_index(drop=True))  # 與前批重疊 50 根
    assert count_klines(conn, SYMBOL, INTERVAL) == 150
    loaded = load_klines(conn, SYMBOL, INTERVAL)
    pd.testing.assert_series_equal(
        loaded["ts"].dt.as_unit("ns"), df["ts"].dt.as_unit("ns"), check_names=False
    )


def test_time_range_filter_inclusive(conn):
    df = _canonical(50)
    save_klines(conn, df)
    start, end = df["ts"].iloc[10], df["ts"].iloc[19]
    loaded = load_klines(conn, SYMBOL, INTERVAL, start=start, end=end)
    assert len(loaded) == 10  # 含兩端點
    assert loaded["ts"].iloc[0] == start
    assert loaded["ts"].iloc[-1] == end


def test_out_of_order_batches_load_sorted(conn):
    df = _canonical(100)
    save_klines(conn, df.iloc[60:].reset_index(drop=True))  # 後段先寫
    save_klines(conn, df.iloc[:60].reset_index(drop=True))  # 前段後寫
    loaded = load_klines(conn, SYMBOL, INTERVAL)
    assert loaded["ts"].is_monotonic_increasing
    assert len(loaded) == 100


def test_vwap_nan_roundtrip(conn):
    raw = _fake_binance_raw(30)
    raw.attrs["symbol"] = SYMBOL
    raw.attrs["interval"] = INTERVAL
    raw.loc[5, "volume"] = 0.0  # cleaning 會給 vwap NaN
    df = to_canonical_from_binance(raw)
    assert df["vwap"].isna().sum() == 1

    save_klines(conn, df)
    loaded = load_klines(conn, SYMBOL, INTERVAL)
    assert loaded["vwap"].isna().sum() == 1
    assert bool(loaded["vwap"].isna().iloc[5])


def test_missing_symbol_returns_empty_canonical(conn):
    save_klines(conn, _canonical(10))
    loaded = load_klines(conn, "NOSUCH", INTERVAL)
    assert loaded.empty
    assert list(loaded.columns) == CANONICAL_COLUMNS


def test_save_rejects_non_canonical(conn):
    with pytest.raises(ValueError):
        save_klines(conn, pd.DataFrame({"close": [1.0, 2.0]}))
