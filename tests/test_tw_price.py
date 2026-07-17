"""台股日K（FinMind TaiwanStockPrice）provider + cleaning + storage 測試。

重點：
- fetch_price 打的是 TaiwanStockPrice、把 exchange 寫進 attrs（TWSE/TPEx 由呼叫端定）。
- canonical 映射：high←max、low←min、turnover←Trading_money（**不是 Trading_turnover=
  成交筆數**）、interval="1d"。
- ts 錨定當地 09:00 Asia/Taipei 開盤 → 存 UTC（跨市場 ts 統一為開盤語意）。
- 產出通過 validate_canonical 且可往返 klines_tw.sqlite（重用 storage_sqlite）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from data import storage_sqlite
from data.cleaning import (
    CANONICAL_COLUMNS,
    finmind_price_to_canonical,
    validate_canonical,
)
from data.providers import finmind


# --- provider（mock HTTP，比照 test_finmind_provider.py 的 fixture）---

class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "msg": "success",
            "status": 200,
            "data": [{
                "date": "2024-01-02", "stock_id": "2330",
                "Trading_Volume": 30000000, "Trading_money": 18000000000,
                "open": 590.0, "max": 600.0, "min": 588.0, "close": 598.0,
                "spread": 5.0, "Trading_turnover": 45000,
            }],
        }
        self.text = text

    def json(self):
        return self._payload


def _stub_settings():
    return SimpleNamespace(
        finmind_base_url="https://fake.test/api/v4/data",
        finmind_token=None,
        finmind_min_request_interval_sec=0.0,
        finmind_quota_wait_sec=1.0,
        http_timeout_sec=1.0,
        http_max_retries=3,
    )


@pytest.fixture
def http(monkeypatch):
    state = SimpleNamespace(queue=[], calls=[])

    def fake_get(url, params=None, timeout=None):
        state.calls.append({"url": url, "params": dict(params or {})})
        if not state.queue:
            raise AssertionError("回應佇列已空")
        item = state.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(finmind.requests, "get", fake_get)
    monkeypatch.setattr(finmind.time, "sleep", lambda s: None)
    monkeypatch.setattr(finmind, "settings", _stub_settings())
    return state


def test_fetch_price_uses_price_dataset_and_stamps_exchange(http):
    http.queue = [_FakeResp()]
    df = finmind.fetch_price("8069", start_date="2024-01-01", end_date="2024-03-31", exchange="TPEx")

    params = http.calls[0]["params"]
    assert params["dataset"] == finmind.DATASET_PRICE == "TaiwanStockPrice"
    assert params["data_id"] == "8069"
    assert params["start_date"] == "2024-01-01"
    assert params["end_date"] == "2024-03-31"
    # exchange 不是 API 欄位，由呼叫端帶入 attrs（cleaning 據此填 canonical）
    assert df.attrs["exchange"] == "TPEx"
    assert df.attrs["stock_id"] == "8069"
    assert df.attrs["source"] == "finmind"


def test_fetch_price_default_start_is_dataset_history(http):
    http.queue = [_FakeResp()]
    finmind.fetch_price("2330")
    assert http.calls[0]["params"]["start_date"] == finmind.DATASET_START_DATES[finmind.DATASET_PRICE]
    assert http.calls[0]["params"].get("exchange") is None  # exchange 不進 API params


# --- cleaning（純函式，不打網路）---

def _raw_price(exchange="TWSE", stock_id="2330"):
    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "stock_id": [stock_id, stock_id],
        "Trading_Volume": [30_000_000, 25_000_000],   # 股數
        "Trading_money": [18_000_000_000, 15_000_000_000],  # 成交金額
        "open": [590.0, 599.0],
        "max": [600.0, 605.0],
        "min": [588.0, 597.0],
        "close": [598.0, 604.0],
        "spread": [5.0, 6.0],
        "Trading_turnover": [45_000, 40_000],  # 成交筆數（陷阱：非成交金額）
    })
    raw.attrs["stock_id"] = stock_id
    raw.attrs["exchange"] = exchange
    raw.attrs["source"] = "finmind"
    return raw


def test_price_to_canonical_field_mapping():
    out = finmind_price_to_canonical(_raw_price(exchange="TPEx", stock_id="8069"))
    assert list(out.columns) == CANONICAL_COLUMNS
    assert len(out) == 2
    r0 = out.iloc[0]
    assert r0["open"] == 590.0
    assert r0["high"] == 600.0   # high ← max
    assert r0["low"] == 588.0    # low ← min
    assert r0["close"] == 598.0
    assert r0["volume"] == 30_000_000            # ← Trading_Volume
    assert r0["turnover"] == 18_000_000_000      # ← Trading_money（不是 Trading_turnover=45000）
    assert r0["turnover"] != 45_000
    assert r0["symbol"] == "8069"
    assert r0["exchange"] == "TPEx"
    assert r0["interval"] == "1d"
    assert r0["source"] == "finmind"


def test_price_ts_anchored_at_taipei_open():
    out = finmind_price_to_canonical(_raw_price())
    ts0 = out.iloc[0]["ts"]
    # 交易日 2024-01-02 的 bar，ts = 09:00 Asia/Taipei = 01:00 UTC
    assert ts0 == pd.Timestamp("2024-01-02 01:00:00", tz="UTC")
    # 轉回台北應為 09:00（開盤語意）
    assert ts0.tz_convert("Asia/Taipei").strftime("%H:%M") == "09:00"


def test_price_vwap_is_price_scale():
    out = finmind_price_to_canonical(_raw_price())
    r0 = out.iloc[0]
    vwap = r0["turnover"] / r0["volume"]
    assert r0["vwap"] == vwap
    # vwap（成交均價）應落在當日 low~high 之間，作為單位/映射正確的健檢
    assert r0["low"] <= r0["vwap"] <= r0["high"]


def test_price_nan_ohlc_row_dropped():
    raw = _raw_price()
    raw.loc[1, "close"] = None  # 缺 close 的列應被 _sanitize 丟棄
    out = finmind_price_to_canonical(raw)
    assert len(out) == 1
    assert out.iloc[0]["ts"] == pd.Timestamp("2024-01-02 01:00:00", tz="UTC")


def test_price_all_zero_ohlcv_row_dropped():
    """6446 藥華藥 2016-12-05 真實資料異常：OHLCV 全部等於 0.0，前後交易日
    正常（台股 E2 化前置查證任務發現，非除權息相關的獨立資料品質問題）。
    真實交易日的收盤價不可能是 0（去除意義的股票代表零價值），這是資料源
    的缺漏列，比照既有『已知情況丟棄，非預期殘留才拋錯』紀律整列丟棄。
    """
    raw = _raw_price()  # 兩列正常資料
    zero_row = raw.iloc[[0]].copy()
    zero_row["date"] = "2016-12-05"
    for col in ["open", "max", "min", "close", "Trading_Volume", "Trading_money"]:
        zero_row[col] = 0.0
    raw = pd.concat([raw, zero_row], ignore_index=True)
    raw.attrs["stock_id"] = "6446"
    raw.attrs["exchange"] = "TPEx"
    raw.attrs["source"] = "finmind"

    out = finmind_price_to_canonical(raw)

    assert len(out) == 2  # 全零列被丟棄，只留下兩筆正常資料
    assert (out["close"] > 0).all()
    assert "2016-12-05" not in out["ts"].dt.strftime("%Y-%m-%d").tolist()


def test_validate_canonical_rejects_non_positive_price():
    """保護性斷言：即使繞過 finmind_price_to_canonical 的 _sanitize（例如未來
    新資料源的轉換函式忘記處理這個情況），任何非正值的 O/H/L/C 一律在
    validate_canonical 這道最終關卡被攔下，不靜默放行進 DB。"""
    out = finmind_price_to_canonical(_raw_price())
    bad = out.copy()
    bad.loc[0, "close"] = 0.0
    with pytest.raises(ValueError, match="非正值"):
        validate_canonical(bad)


def test_price_empty_returns_empty_canonical():
    out = finmind_price_to_canonical(pd.DataFrame())
    assert list(out.columns) == CANONICAL_COLUMNS
    assert out.empty


def test_price_canonical_roundtrips_klines_tw_storage(tmp_path):
    out = finmind_price_to_canonical(_raw_price(exchange="TPEx", stock_id="8069"))
    validate_canonical(out)  # 不拋錯即通過 schema

    db = tmp_path / "klines_tw.sqlite"
    conn = storage_sqlite.connect(db)
    try:
        n = storage_sqlite.save_klines(conn, out)
        assert n == 2
        back = storage_sqlite.load_klines(conn, "8069", "1d", exchange="TPEx")
    finally:
        conn.close()

    assert len(back) == 2
    assert back.iloc[0]["ts"] == pd.Timestamp("2024-01-02 01:00:00", tz="UTC")
    assert back.iloc[0]["high"] == 600.0
    assert (back["interval"] == "1d").all()
    assert (back["exchange"] == "TPEx").all()
