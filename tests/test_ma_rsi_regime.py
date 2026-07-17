"""MA/RSI + regime filter 策略測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cleaning import to_canonical_from_binance
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_regime import MaRsiRegimeStrategy
from tests.test_indicators_and_cleaning import _fake_binance_raw


def _canonical_from_prices(prices: np.ndarray) -> pd.DataFrame:
    n = len(prices)
    raw = _fake_binance_raw(n)
    raw["open"] = prices
    raw["high"] = prices * 1.002
    raw["low"] = prices * 0.998
    raw["close"] = prices
    raw["quote_asset_volume"] = raw["volume"].astype(float) * prices
    return to_canonical_from_binance(raw)


def test_signals_are_long_only_subset():
    data = _canonical_from_prices(100.0 * 1.002 ** np.arange(400))
    strat = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert set(sig.unique()).issubset({0, 1})  # 不做空
    assert len(sig) == len(data)


def test_downtrend_forces_flat():
    """穩定下跌段：regime=trend_down，訊號應全被壓成 0。"""
    data = _canonical_from_prices(100.0 * 0.997 ** np.arange(400))
    strat = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig == 0).all()


def test_range_forces_flat():
    """盤整鋸齒段：regime=range，訊號應全被壓成 0。"""
    base = 100.0 + np.tile([0.0, 1.5, 0.0, -1.5], 100)
    data = _canonical_from_prices(base)
    strat = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig == 0).all()


def test_uptrend_keeps_some_base_signal():
    """帶雜訊的上漲段：regime=trend_up 且 RSI 會週期性回落 < 70，
    使 base MA/RSI 產生多單；filter 後在 trend_up 區段應保留部分多單。

    （純指數上漲會讓 RSI 一直 >70，base 本身就是 0，故須加雜訊。）
    """
    rng = np.random.default_rng(42)
    # 正漂移 + 雜訊：趨勢明確向上，但價格會回檔讓 RSI 降溫
    steps = 0.004 + rng.standard_normal(500) * 0.02
    prices = 100.0 * np.exp(np.cumsum(steps))
    data = _canonical_from_prices(prices)
    base = MaRsiStrategy(20, 60).generate_signals(data)
    filt = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120).generate_signals(data)
    assert (base == 1).sum() > 0   # 前提：base 在這段確實有多單
    assert (filt == 1).sum() > 0   # filter 後仍保留部分多單
    # filter 不可能無中生有：filtered==1 的地方 base 也必須==1
    assert ((filt == 1) <= (base == 1)).all()


def test_filter_never_exceeds_base():
    """任何資料下，regime filter 只會把訊號壓小或維持，不會放大。"""
    rng = np.random.default_rng(0)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(600) * 0.01))
    data = _canonical_from_prices(prices)
    base = MaRsiStrategy(20, 60).generate_signals(data)
    filt = MaRsiRegimeStrategy(fast_window=20, slow_window=60).generate_signals(data)
    # 對 long-only baseline（值為 0/1），filtered <= base 逐元素成立
    assert (filt.to_numpy() <= base.to_numpy()).all()


def test_regime_filter_is_causal_no_lookahead():
    """因果性：截掉未來資料不應改變較早 K 棒的訊號。

    若 regime 判斷偷看了未來，前段訊號會隨後段資料改變。
    """
    rng = np.random.default_rng(1)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(500) * 0.01))
    full = _canonical_from_prices(prices)
    truncated = _canonical_from_prices(prices[:300])

    strat = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig_full = strat.generate_signals(full)
    sig_trunc = strat.generate_signals(truncated)

    # 前 300 根的訊號在兩種情況下必須完全一致
    np.testing.assert_array_equal(sig_full.to_numpy()[:300], sig_trunc.to_numpy())


def test_warmup_is_flat():
    prices = 100.0 * 1.003 ** np.arange(400)
    data = _canonical_from_prices(prices)
    strat = MaRsiRegimeStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    # 暖機期（前 regime_window-1 根）regime 無法分類 → 必為 0
    assert (sig.iloc[:119] == 0).all()


def test_invalid_params_raise():
    with pytest.raises(ValueError):
        MaRsiRegimeStrategy(fast_window=60, slow_window=20)  # fast<slow 由內部 base 驗證
    with pytest.raises(ValueError):
        MaRsiRegimeStrategy(regime_window=2)
