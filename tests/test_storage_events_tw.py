"""storage_events_tw（台股公司行動事件快照，A4 技術債輪 2026-07-16）測試。

動機：除權息（TaiwanStockDividendResult）與停券日曆
（TaiwanStockMarginShortSaleSuspension）先前由各 runner 即時抓取——FinMind
是不受我們控制的外部依賴（TaiwanStockPriceAdj 付費牆為先例），API 改版/
收費即斷已交付數字的重現鏈。比照 storage_adjusted（方案B）落地快照，
runner 改讀本地 DB。

驗證：WAL、往返無損、upsert 冪等、缺資料時空表欄位齊全。
"""

from __future__ import annotations

import pandas as pd
import pytest

from data.storage_events_tw import (
    connect,
    load_dividend_results,
    load_suspensions,
    save_dividend_results,
    save_suspensions,
)


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test_events_tw.sqlite")
    yield c
    c.close()


def _div_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2023-06-30", "2024-06-27"],
        "stock_id": ["2603", "2603"],
        "before_price": [155.0, 200.0],
        "after_price": [85.0, 190.03],
        "stock_and_cache_dividend": [70.0, 9.97],
        "stock_or_cache_dividend": ["息", "息"],
        "max_price": [93.5, 209.0],
        "min_price": [89.5, 190.5],
        "open_price": [91.9, 195.0],
        "reference_price": [85.0, 190.03],
    })


def _susp_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_id": ["2330", "2330"],
        "date": ["2023-06-09", "2023-03-28"],
        "end_date": ["2023-06-14", "2023-03-31"],
        "reason": ["除息", "股東常會"],
    })


def test_wal_mode(conn):
    assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"


def test_dividend_roundtrip_exact(conn):
    n = save_dividend_results(conn, _div_df())
    assert n == 2
    back = load_dividend_results(conn, "2603")
    assert len(back) == 2
    assert list(back["date"]) == ["2023-06-30", "2024-06-27"]  # 依 date 排序
    assert back["before_price"].tolist() == [155.0, 200.0]
    assert back["after_price"].tolist() == [85.0, 190.03]
    # apply_back_adjustment 需要的欄位齊全
    for col in ("date", "stock_id", "before_price", "after_price"):
        assert col in back.columns


def test_suspension_roundtrip_sorted(conn):
    n = save_suspensions(conn, _susp_df())
    assert n == 2
    back = load_suspensions(conn, "2330")
    assert list(back["date"]) == ["2023-03-28", "2023-06-09"]
    assert list(back["reason"]) == ["股東常會", "除息"]


def test_upsert_idempotent(conn):
    save_dividend_results(conn, _div_df())
    save_dividend_results(conn, _div_df())
    assert len(load_dividend_results(conn, "2603")) == 2
    save_suspensions(conn, _susp_df())
    save_suspensions(conn, _susp_df())
    assert len(load_suspensions(conn, "2330")) == 2


def test_missing_symbol_returns_empty_with_columns(conn):
    div = load_dividend_results(conn, "0000")
    susp = load_suspensions(conn, "0000")
    assert div.empty and "before_price" in div.columns
    assert susp.empty and "end_date" in susp.columns


def test_empty_save_returns_zero(conn):
    assert save_dividend_results(conn, pd.DataFrame()) == 0
    assert save_suspensions(conn, pd.DataFrame()) == 0


def test_save_missing_columns_raises(conn):
    with pytest.raises(ValueError, match="缺少"):
        save_dividend_results(conn, pd.DataFrame({"date": ["2024-01-01"]}))
    with pytest.raises(ValueError, match="缺少"):
        save_suspensions(conn, pd.DataFrame({"date": ["2024-01-01"]}))
