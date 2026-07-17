"""還原股價（向後調整 back-adjustment）測試。

對應台股 E2 化前置查證任務的股價還原設計（2026-07-16 使用者確認方案）：
- 調整因子來源：TaiwanStockDividendResult 的 after_price/before_price
  （不是 reference_price——見 test_adjustment_uses_after_price_not_reference_price
  的具體反例，純配股事件 reference_price 會停滯於 before_price）。
- 錨點 = 最新一根 bar（cum_factor=1.0），越往回溯乘越多過去事件的 ratio。
- 這是**刻意**用「未來」的除權息事件回填「過去」的價格水位，屬產業標準做法，
  與本專案 shift(1) 訊號防未來函數紀律是兩件不同的事——
  見 test_adjustment_intentionally_uses_future_dividend_events。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.adjustment import apply_back_adjustment, compute_cum_factor
from data.cleaning import CANONICAL_COLUMNS


def _tw_ts(date_str: str) -> pd.Timestamp:
    local_open = pd.Timestamp(date_str) + pd.Timedelta(hours=9)
    return local_open.tz_localize("Asia/Taipei").tz_convert("UTC")


def _klines(rows: list[tuple], symbol: str = "2603") -> pd.DataFrame:
    """rows: list of (date_str, open, high, low, close, volume)。回傳 canonical schema。"""
    if not rows:
        ts = pd.Series([], dtype="datetime64[ns, UTC]")
    else:
        ts = pd.Series([_tw_ts(r[0]) for r in rows])
    df = pd.DataFrame({
        "ts": ts,
        "symbol": symbol,
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
        "turnover": [r[4] * r[5] for r in rows],
        "vwap": [r[4] for r in rows],
        "exchange": "TWSE",
        "interval": "1d",
        "source": "test",
        "ingested_at": ts,
    })
    return df[CANONICAL_COLUMNS]


def _dividends(events: list[tuple], stock_id: str = "2603") -> pd.DataFrame:
    """events: list of (date_str, before_price, after_price[, reference_price])。"""
    if not events:
        return pd.DataFrame(
            columns=["date", "stock_id", "before_price", "after_price", "reference_price"]
        )
    return pd.DataFrame({
        "date": [e[0] for e in events],
        "stock_id": stock_id,
        "before_price": [e[1] for e in events],
        "after_price": [e[2] for e in events],
        "reference_price": [e[3] if len(e) > 3 else e[2] for e in events],
    })


# --- 真實資料：2603 長榮 2023-06-30 現金股利 70 元（查證1發現的極端案例）---
# before_price=155.00、after_price=85.00（FinMind TaiwanStockDividendResult 實際值）
# 收盤序列取自 klines_tw.sqlite 真實資料（2023-06-27 ~ 2023-07-03）
_2603_JUN2023 = [
    ("2023-06-27", 160.5, 162.5, 160.5, 161.0, 40_548_901.0),
    ("2023-06-28", 161.5, 162.0, 156.0, 157.5, 49_412_842.0),
    ("2023-06-29", 158.0, 158.5, 153.0, 155.0, 116_946_504.0),
    ("2023-06-30", 91.9, 93.5, 89.5, 93.5, 317_803_134.0),
    ("2023-07-03", 98.8, 102.5, 98.5, 102.5, 75_242_468.0),
]
_2603_EVENT = [("2023-06-30", 155.00, 85.00, 85.00)]


# ---------------------------------------------------------------------------
# Test 1：精確數值斷言——除權息造成的假跳空被還原成真實市場報酬
# ---------------------------------------------------------------------------

def test_adjustment_removes_ex_dividend_jump_2603_2023_06_30():
    """原始報酬 93.5/155.0-1 = -39.68%（比 FinMind 理論參考價缺口 -45.2% 更
    貼近實際成交，因市場當天強勢承接、站上理論參考價 85.0）。調整後應精確
    等於 93.5/85.0-1 = +10.0%（解析恆等式，非模糊『比較不極端』的斷言）。
    """
    klines = _klines(_2603_JUN2023)
    dividends = _dividends(_2603_EVENT)

    raw_ret_0630 = klines["close"].iloc[3] / klines["close"].iloc[2] - 1.0
    assert raw_ret_0630 == pytest.approx(-0.396774, abs=1e-6)

    out = apply_back_adjustment(klines, dividends)
    adj_ret_0630 = out["close"].iloc[3] / out["close"].iloc[2] - 1.0
    assert adj_ret_0630 == pytest.approx(0.10, abs=1e-9)


# ---------------------------------------------------------------------------
# Test 2：沒有除權息事件跨越的相鄰交易日，調整後日報酬與原始日報酬逐位相同
# ---------------------------------------------------------------------------

def test_adjustment_preserves_returns_away_from_event():
    klines = _klines(_2603_JUN2023)
    dividends = _dividends(_2603_EVENT)
    out = apply_back_adjustment(klines, dividends)

    raw_close = klines["close"].to_numpy()
    adj_close = out["close"].to_numpy()

    # (0,1)=6/27→6/28、(1,2)=6/28→6/29：事件前，同一 cum_factor
    # (3,4)=6/30→7/3：事件後，同一 cum_factor
    for a, b in [(0, 1), (1, 2), (3, 4)]:
        raw_ret = raw_close[b] / raw_close[a] - 1.0
        adj_ret = adj_close[b] / adj_close[a] - 1.0
        assert adj_ret == pytest.approx(raw_ret, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 3：OHLC 內部一致性——同一因子縮放 open/high/low/close，不等式維持成立
# ---------------------------------------------------------------------------

def test_adjustment_preserves_ohlc_internal_consistency():
    klines = _klines(_2603_JUN2023)
    dividends = _dividends(_2603_EVENT)
    out = apply_back_adjustment(klines, dividends)

    assert (out["low"] <= out["open"]).all()
    assert (out["low"] <= out["close"]).all()
    assert (out["open"] <= out["high"]).all()
    assert (out["close"] <= out["high"]).all()


# ---------------------------------------------------------------------------
# Test 4：cum_factor 錨點於最新 bar=1.0，且是隨事件邊界變動的階梯函數
# ---------------------------------------------------------------------------

# 合成資料：b0/b1 在兩個事件之前 → 因子相同；b2 跨過事件1（前）；b3 跨過事件2（後，=1.0）
_MULTI_EVENT_KLINES = [
    ("2020-01-02", 49.0, 51.0, 48.0, 50.0, 1000.0),
    ("2020-06-01", 99.0, 101.0, 98.0, 100.0, 1000.0),   # = 事件1 before_price
    ("2020-06-02", 199.0, 201.0, 198.0, 200.0, 1000.0),  # 事件1 ex-date；= 事件2 before_price
    ("2021-01-04", 399.0, 401.0, 398.0, 400.0, 1000.0),  # 事件2 ex-date
]
_MULTI_EVENTS = [
    ("2020-06-02", 100.0, 90.0, 90.0),   # ratio1 = 0.9
    ("2021-01-04", 200.0, 180.0, 180.0),  # ratio2 = 0.9
]


def test_cum_factor_anchored_and_stepwise_monotonic():
    klines = _klines(_MULTI_EVENT_KLINES)
    bar_dates = klines["ts"].dt.tz_convert("Asia/Taipei").dt.date
    dividends = _dividends(_MULTI_EVENTS)

    factor = compute_cum_factor(bar_dates, dividends).to_numpy()

    expected = np.array([0.81, 0.81, 0.9, 1.0])
    assert factor == pytest.approx(expected, rel=1e-9)
    # 錨點：最新一根 bar 必為 1.0（無未來事件可乘）
    assert factor[-1] == pytest.approx(1.0)
    # 隨時間往回不遞增（越早的 bar 因子越小或相等）
    assert np.all(np.diff(factor) >= -1e-12)


# ---------------------------------------------------------------------------
# Test 5：使用 after_price 而非 reference_price（防呆，防止未來誤用回頭）
# ---------------------------------------------------------------------------

def test_adjustment_uses_after_price_not_reference_price():
    """2603 2017-11-07 純配股事件（真實資料）：FinMind reference_price 停滯於
    before_price（17.50 == 17.50，未反映稀釋），只有 after_price（17.22）
    正確反映稀釋幅度。若誤用 reference_price 計算 cum_factor 會得到 1.0
    （沒調整），必須是 17.22/17.50 才對。這個測試專門防止未來有人把
    after_price 改回 reference_price。"""
    klines = _klines([
        ("2017-11-06", 17.40, 17.60, 17.30, 17.50, 5_000_000.0),
        ("2017-11-07", 17.00, 17.30, 16.90, 17.10, 6_000_000.0),
    ])
    dividends = _dividends([("2017-11-07", 17.50, 17.22, 17.50)])  # reference == before(未反映稀釋)

    bar_dates = klines["ts"].dt.tz_convert("Asia/Taipei").dt.date
    factor = compute_cum_factor(bar_dates, dividends).to_numpy()

    expected_ratio = 17.22 / 17.50
    assert factor[0] == pytest.approx(expected_ratio, rel=1e-9)
    assert factor[0] != pytest.approx(1.0, abs=1e-6)  # 若誤用 reference_price 會變成 1.0


# ---------------------------------------------------------------------------
# Test 7：文件性斷言——刻意用未來事件回填過去價格，不是未來函數 bug
# ---------------------------------------------------------------------------

def test_adjustment_intentionally_uses_future_dividend_events():
    """還原價刻意用『未來』的除權息事件回填『過去』的價格水位，是產業標準
    做法，不是防未來函數 bug——與本專案 shift(1) 訊號防未來函數紀律是兩件
    不同的事（見 data/adjustment.py 模組 docstring）。

    本測試精確釘住邊界：加入一個日期**晚於**某根 bar 的事件會改變該 bar 的
    調整後價格（刻意的非因果行為）；加入一個日期**等於或早於**該 bar 的
    事件則不影響它。防止未來有人誤判這個行為是 bug 而『修正』掉。
    """
    klines = _klines(_2603_JUN2023)  # 事件日 2023-06-30 在 index 3

    no_event = apply_back_adjustment(klines, _dividends([]))
    with_future_event = apply_back_adjustment(klines, _dividends(_2603_EVENT))

    # bar 0~2（事件日之前）：加入「未來」事件後，調整後價格改變
    for i in (0, 1, 2):
        assert with_future_event["close"].iloc[i] != pytest.approx(
            no_event["close"].iloc[i]
        )

    # bar 3~4（事件日當天及之後）：事件只調整「早於」事件日的 bar，這裡不受影響
    for i in (3, 4):
        assert with_future_event["close"].iloc[i] == pytest.approx(
            no_event["close"].iloc[i]
        )


# ---------------------------------------------------------------------------
# 防呆／錯誤路徑
# ---------------------------------------------------------------------------

def test_apply_back_adjustment_missing_dividend_columns_raises():
    klines = _klines(_2603_JUN2023)
    bad = pd.DataFrame({"date": ["2023-06-30"], "before_price": [155.0]})  # 缺 after_price
    with pytest.raises(ValueError, match="缺少必要欄位"):
        apply_back_adjustment(klines, bad)


def test_apply_back_adjustment_symbol_mismatch_raises():
    klines = _klines(_2603_JUN2023, symbol="2603")
    dividends = _dividends(_2603_EVENT, stock_id="2330")
    with pytest.raises(ValueError, match="不一致"):
        apply_back_adjustment(klines, dividends)


def test_apply_back_adjustment_before_price_mismatch_raises():
    """事件 before_price 與資料中前一交易日收盤不符，代表日期定義或資料源
    不一致，必須拋錯而非默默算出錯誤的調整因子（比照融資融券哨兵值『已知
    情況轉換、未知情況拋錯』的既有紀律）。"""
    klines = _klines(_2603_JUN2023)
    bad_event = [("2023-06-30", 999.0, 85.0, 85.0)]  # before_price 故意錯
    with pytest.raises(ValueError, match="不符"):
        apply_back_adjustment(klines, _dividends(bad_event))


def test_apply_back_adjustment_multi_symbol_raises():
    df = pd.concat(
        [_klines(_2603_JUN2023[:2], symbol="2603"), _klines(_2603_JUN2023[2:], symbol="2330")]
    ).reset_index(drop=True)
    with pytest.raises(ValueError, match="單一 symbol"):
        apply_back_adjustment(df, _dividends([]))


def test_apply_back_adjustment_empty_dividends_is_noop():
    klines = _klines(_2603_JUN2023)
    out = apply_back_adjustment(klines, _dividends([]))
    assert np.array_equal(out["close"].to_numpy(), klines["close"].to_numpy())


def test_apply_back_adjustment_empty_klines_returns_empty():
    empty = _klines(_2603_JUN2023).iloc[0:0]
    out = apply_back_adjustment(empty, _dividends([]))
    assert out.empty


def test_compute_cum_factor_rejects_non_positive_price():
    klines = _klines(_2603_JUN2023)
    bar_dates = klines["ts"].dt.tz_convert("Asia/Taipei").dt.date
    bad = _dividends([("2023-06-30", 0.0, 85.0, 85.0)])
    with pytest.raises(ValueError, match="必須為正"):
        compute_cum_factor(bar_dates, bad)
