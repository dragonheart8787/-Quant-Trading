"""技術指標與資料清洗測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cleaning import (
    CANONICAL_COLUMNS,
    to_canonical_from_binance,
    validate_canonical,
)
from indicators.technical import atr, rsi, sma
from strategy.ma_rsi import MaRsiStrategy


def test_sma_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_rsi_all_gains_is_100():
    """單調上漲序列 RSI 應趨近 100。"""
    s = pd.Series(np.arange(1, 50, dtype=float))
    r = rsi(s, 14)
    assert r.dropna().iloc[-1] == pytest.approx(100.0)


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumsum(rng.standard_normal(200)) + 100)
    r = rsi(s, 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def _fake_binance_raw(n: int = 100) -> pd.DataFrame:
    open_time = np.arange(n, dtype="int64") * 3_600_000 + 1_700_000_000_000
    base = np.linspace(100, 120, n)
    df = pd.DataFrame(
        {
            "open_time": open_time,
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base + 0.5,
            "volume": np.full(n, 10.0),
            "close_time": open_time + 3_599_999,
            "quote_asset_volume": np.full(n, 10.0) * base,
            "num_trades": np.full(n, 5),
            "taker_buy_base": np.full(n, 5.0),
            "taker_buy_quote": np.full(n, 5.0) * base,
            "ignore": np.zeros(n),
        }
    )
    df.attrs.update(symbol="BTCUSDT", interval="1h", source="binance", exchange="BINANCE")
    return df


def test_to_canonical_schema_and_validate():
    raw = _fake_binance_raw()
    canon = to_canonical_from_binance(raw)
    assert list(canon.columns) == CANONICAL_COLUMNS
    assert canon["symbol"].unique().tolist() == ["BTCUSDT"]
    assert pd.api.types.is_datetime64_any_dtype(canon["ts"])
    # vwap = turnover / volume = base
    assert canon["vwap"].iloc[0] == pytest.approx(raw["close"].iloc[0] - 0.5 + 0.0, abs=1.0)
    validate_canonical(canon)  # 不應丟例外


def test_validate_canonical_rejects_unsorted():
    raw = _fake_binance_raw()
    canon = to_canonical_from_binance(raw)
    shuffled = canon.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError):
        validate_canonical(shuffled)


def test_ma_rsi_strategy_runs_on_canonical():
    raw = _fake_binance_raw(200)
    canon = to_canonical_from_binance(raw)
    strat = MaRsiStrategy(fast_window=10, slow_window=30, rsi_window=14)
    sig = strat.generate_signals(canon)
    assert len(sig) == len(canon)
    assert set(sig.unique()).issubset({-1, 0, 1})
    # 暖機期一定是 0
    assert sig.iloc[0] == 0


def test_ma_rsi_invalid_windows():
    with pytest.raises(ValueError):
        MaRsiStrategy(fast_window=60, slow_window=20)


# ---------- ATR ----------

def _make_ohlc(n: int = 50, high_offset: float = 1.0, low_offset: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(100.0, index=idx)
    return pd.DataFrame(
        {"high": close + high_offset, "low": close - low_offset, "close": close},
        index=idx,
    )


def test_atr_warmup_nan():
    """前 window-1 根窗口不足應為 NaN，第 window 根開始有值。

    TR[0] 使用 max(high-low, NaN, NaN)，pandas skipna=True 讓 TR[0] = high-low（非 NaN），
    因此 ATR 的 window=14 從 iloc[13]（0-indexed）開始有效。
    """
    data = _make_ohlc(n=50)
    result = atr(data, window=14)
    assert result.iloc[:13].isna().all()
    assert not pd.isna(result.iloc[13])


def test_atr_constant_range():
    """固定振幅資料：ATR 應收斂至 True Range。"""
    data = _make_ohlc(n=50, high_offset=2.0, low_offset=2.0)
    result = atr(data, window=14)
    # True Range 固定為 4.0（high-low=4, |high-prev_close|=2, |low-prev_close|=2）
    assert result.dropna().iloc[-1] == pytest.approx(4.0)


def test_atr_invalid_window():
    data = _make_ohlc()
    with pytest.raises(ValueError):
        atr(data, window=0)


def test_atr_missing_column():
    idx = pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")
    bad_df = pd.DataFrame({"high": 101.0, "close": 100.0}, index=idx)
    with pytest.raises(ValueError, match="缺少欄位"):
        atr(bad_df, window=5)
