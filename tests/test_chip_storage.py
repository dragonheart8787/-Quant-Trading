"""籌碼 DB（storage_chips.py）測試：WAL、冪等 upsert、讀回過濾。"""

from __future__ import annotations

import pandas as pd
import pytest

from data import storage_chips
from data.cleaning import finmind_institutional_to_canonical, finmind_margin_to_canonical


@pytest.fixture
def conn(tmp_path):
    c = storage_chips.connect(tmp_path / "chips_test.sqlite")
    yield c
    c.close()


def _institutional_df():
    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-02", "2024-01-03"],
        "stock_id": ["2330", "2330", "2330"],
        "name": ["Foreign_Investor", "Investment_Trust", "Foreign_Investor"],
        "buy": [1000, 50, 300],
        "sell": [400, 80, 100],
    })
    raw.attrs["source"] = "finmind"
    return finmind_institutional_to_canonical(raw)


def test_connect_sets_wal_and_creates_tables(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert set(storage_chips.CHIP_TABLES) <= tables


def test_save_and_load_roundtrip(conn):
    df = _institutional_df()
    n = storage_chips.save_chips(conn, df, "chip_institutional")
    assert n == 3
    back = storage_chips.load_chips(conn, "chip_institutional", stock_id="2330")
    assert len(back) == 3
    assert list(back.columns) == list(df.columns)
    assert back["date"].is_monotonic_increasing


def test_upsert_idempotent(conn):
    df = _institutional_df()
    storage_chips.save_chips(conn, df, "chip_institutional")
    storage_chips.save_chips(conn, df, "chip_institutional")  # 重複 ingest
    assert storage_chips.count_chips(conn, "chip_institutional") == 3


def test_upsert_replaces_on_conflict(conn):
    df = _institutional_df()
    storage_chips.save_chips(conn, df, "chip_institutional")
    df2 = df.copy()
    df2.loc[0, "buy"] = 9999
    df2.loc[0, "net"] = 9999 - df2.loc[0, "sell"]
    storage_chips.save_chips(conn, df2, "chip_institutional")
    back = storage_chips.load_chips(conn, "chip_institutional")
    assert storage_chips.count_chips(conn, "chip_institutional") == 3  # 沒有多列
    row = back[(back["date"] == "2024-01-02") & (back["investor_type"] == "Foreign_Investor")]
    assert row["buy"].iloc[0] == 9999  # 整列被取代


def test_load_date_range_filter(conn):
    storage_chips.save_chips(conn, _institutional_df(), "chip_institutional")
    back = storage_chips.load_chips(
        conn, "chip_institutional", start="2024-01-03", end="2024-01-03"
    )
    assert set(back["date"]) == {"2024-01-03"}


def test_load_empty_returns_columns(conn):
    back = storage_chips.load_chips(conn, "chip_margin", stock_id="0000")
    assert back.empty
    assert list(back.columns) == list(storage_chips.CHIP_TABLES["chip_margin"]["columns"])


def test_unknown_table_raises(conn):
    with pytest.raises(ValueError, match="未知的籌碼表"):
        storage_chips.save_chips(conn, pd.DataFrame(), "no_such_table")
    with pytest.raises(ValueError, match="未知的籌碼表"):
        storage_chips.load_chips(conn, "no_such_table")


def test_save_missing_column_raises(conn):
    df = _institutional_df().drop(columns=["net"])
    with pytest.raises(ValueError, match="缺少欄位"):
        storage_chips.save_chips(conn, df, "chip_institutional")


def test_save_empty_returns_zero(conn):
    df = _institutional_df().iloc[0:0]
    assert storage_chips.save_chips(conn, df, "chip_institutional") == 0


def test_margin_roundtrip(conn):
    raw = pd.DataFrame({
        "date": ["2024-01-02"],
        "stock_id": ["2330"],
        "MarginPurchaseBuy": [100],
        "MarginPurchaseSell": [30],
        "MarginPurchaseCashRepayment": [10],
        "MarginPurchaseYesterdayBalance": [5000],
        "MarginPurchaseTodayBalance": [5060],
        "MarginPurchaseLimit": [10000],
        "ShortSaleBuy": [5],
        "ShortSaleSell": [8],
        "ShortSaleCashRepayment": [0],
        "ShortSaleYesterdayBalance": [200],
        "ShortSaleTodayBalance": [203],
        "ShortSaleLimit": [10000],
        "OffsetLoanAndShort": [2],
        "Note": [""],
    })
    raw.attrs["source"] = "finmind"
    df = finmind_margin_to_canonical(raw)
    storage_chips.save_chips(conn, df, "chip_margin")
    back = storage_chips.load_chips(conn, "chip_margin", stock_id="2330")
    assert back["margin_balance_today"].iloc[0] == 5060


def test_margin_nullable_columns_stored_as_integer_not_blob(conn):
    """★A1 技術債修復的防護測試（2026-07-16）★：nullable Int64 欄位
    （margin_limit/short_limit/offset_loan_and_short）的有效數值必須以
    sqlite INTEGER 落地、讀回為可運算的數值——不是 8-byte blob。

    背景：台股放空 groundwork item 5 查證時發現全表 ~65k 列這三欄全部
    落地成 blob（nullable Int64 經 itertuples 產出 numpy 標量、sqlite
    不認得而 fallback 成 bytes）；既有 roundtrip 測試只驗 NULL 語意、
    沒驗數值型別，此為當時漏掉的測試縫隙，本測試補上。"""
    raw = pd.DataFrame({
        "date": ["2024-01-02"],
        "stock_id": ["2330"],
        "MarginPurchaseBuy": [100],
        "MarginPurchaseSell": [30],
        "MarginPurchaseCashRepayment": [10],
        "MarginPurchaseYesterdayBalance": [5000],
        "MarginPurchaseTodayBalance": [5060],
        "MarginPurchaseLimit": [2922341],
        "ShortSaleBuy": [5],
        "ShortSaleSell": [8],
        "ShortSaleCashRepayment": [0],
        "ShortSaleYesterdayBalance": [200],
        "ShortSaleTodayBalance": [203],
        "ShortSaleLimit": [2922341],
        "OffsetLoanAndShort": [7],
        "Note": [""],
    })
    raw.attrs["source"] = "finmind"
    df = finmind_margin_to_canonical(raw)
    storage_chips.save_chips(conn, df, "chip_margin")

    # 底層型別必須是 integer（不是 blob）
    for col in ("margin_limit", "short_limit", "offset_loan_and_short"):
        typeof = conn.execute(
            f"SELECT typeof({col}) FROM chip_margin WHERE stock_id='2330'"
        ).fetchone()[0]
        assert typeof == "integer", f"{col} 落地型別應為 integer，實際 {typeof}"

    # 讀回可直接做算術（blob 會在這裡炸 TypeError）
    back = storage_chips.load_chips(conn, "chip_margin", stock_id="2330")
    assert float(back["short_limit"].iloc[0]) == 2922341.0
    assert float(back["short_limit"].iloc[0] / 2) == pytest.approx(1461170.5)


def test_margin_nullable_sentinel_roundtrip(conn):
    # 哨兵 -1000000 → NULL 應能存入並讀回為缺值（NULL），核心欄位不受影響
    raw = pd.DataFrame({
        "date": ["2024-01-02"],
        "stock_id": ["8069"],
        "MarginPurchaseBuy": [100],
        "MarginPurchaseSell": [30],
        "MarginPurchaseCashRepayment": [10],
        "MarginPurchaseYesterdayBalance": [5000],
        "MarginPurchaseTodayBalance": [5060],
        "MarginPurchaseLimit": [10000],
        "ShortSaleBuy": [5],
        "ShortSaleSell": [8],
        "ShortSaleCashRepayment": [0],
        "ShortSaleYesterdayBalance": [200],
        "ShortSaleTodayBalance": [203],
        "ShortSaleLimit": [-1_000_000],       # 哨兵
        "OffsetLoanAndShort": [-1_000_000],   # 哨兵
        "Note": [""],
    })
    raw.attrs["source"] = "finmind"
    df = finmind_margin_to_canonical(raw)
    storage_chips.save_chips(conn, df, "chip_margin")
    back = storage_chips.load_chips(conn, "chip_margin", stock_id="8069")
    assert len(back) == 1
    assert pd.isna(back["short_limit"].iloc[0])
    assert pd.isna(back["offset_loan_and_short"].iloc[0])
    assert back["short_balance_today"].iloc[0] == 203
