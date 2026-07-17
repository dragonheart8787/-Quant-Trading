"""MA/RSI 雙向策略單元測試。

驗證關鍵不變量：
  - 訊號值域為 {-1, 0, 1}
  - 暖機期必為 0
  - 非 trend_down 時 -1 訊號絕不出現（空頭 regime 過濾）
  - trend_down 且 RSI > rsi_not_oversold → 空頭訊號可出現
  - trend_down 但 RSI <= rsi_not_oversold（深度超賣）→ 不做空
  - 多頭訊號在上漲段可出現（不受 regime 過濾）
  - 多頭訊號與 ma_rsi.py 完全一致
  - 因果性：截掉未來資料不改變過去訊號
  - 無效參數丟出 ValueError

空頭觸發條件（v2 為預設；A2 多尺度為選配，2026-07-03 起去留未定案）：
  death_cross AND RSI > rsi_not_oversold(30) AND 短視窗(120) trend_down
  AND（選配 A2：long_regime_window=300）長視窗 trend_down
  （已移除原 RSI >= overbought 條件；原因：時間錯位，詳見 HANDOFF.md。
   預設 long_regime_window=None = v2 模式 = 當前基準。）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cleaning import to_canonical_from_binance
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy
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


# ===== 基本屬性 =====

def test_signal_values_in_valid_set():
    """任何資料下訊號值域必須是 {-1, 0, 1} 的子集。"""
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(500) * 0.01))
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert set(sig.unique()).issubset({-1, 0, 1})


def test_warmup_is_flat():
    """暖機期（前 slow_window-1 根）訊號必為 0。"""
    prices = 100.0 * 1.002 ** np.arange(400)
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig.iloc[:59] == 0).all()


def test_signal_length_matches_data():
    prices = 100.0 * 0.998 ** np.arange(300)
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60)
    sig = strat.generate_signals(data)
    assert len(sig) == len(data)


# ===== 空頭 regime 過濾：非 trend_down 時 -1 絕不出現 =====

def test_no_short_in_uptrend():
    """穩定上漲段（trend_up）：-1 訊號不得出現。"""
    prices = 100.0 * 1.002 ** np.arange(400)
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig == -1).sum() == 0, "上漲段不應出現空頭訊號"


def test_no_short_in_range():
    """盤整段（range）：-1 訊號不得出現。"""
    base = 100.0 + np.tile([0.0, 1.5, 0.0, -1.5], 150)
    data = _canonical_from_prices(base)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig == -1).sum() == 0, "盤整段不應出現空頭訊號"


def test_no_short_when_rsi_deeply_oversold():
    """純等速下跌：RSI 趨近 0（深度超賣），即使死亡交叉 + trend_down 也不做空。

    rsi_not_oversold=30 的語意：避免在市場最悲觀時（RSI<=30）加空，
    防止追空在反彈前夕的底部。純等速下跌 RSI → 0，條件不成立 → 無 -1。
    """
    prices = 100.0 * 0.997 ** np.arange(400)
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_not_oversold=30.0, regime_window=120
    )
    sig = strat.generate_signals(data)
    assert (sig == -1).sum() == 0, "深度超賣不應做空"


# ===== 新條件：trend_down + RSI > 30 時空頭訊號應能觸發 =====

def test_short_appears_in_trend_down_moderate_rsi():
    """帶雜訊的下跌段：trend_down + 死亡交叉 + RSI 在 30~70 間 → -1 可出現。

    使用負漂移+雜訊：RSI 不會降至極端超賣，death cross 成立，
    regime 120 根後認定 trend_down → 新條件下空頭應觸發。
    """
    rng = np.random.default_rng(42)
    steps = -0.003 + rng.standard_normal(500) * 0.012
    prices = 100.0 * np.exp(np.cumsum(steps))
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig = strat.generate_signals(data)
    assert (sig == -1).sum() > 0, "帶雜訊的下跌段應出現空頭訊號（新條件）"


def test_short_blocked_by_rsi_threshold():
    """提高 rsi_not_oversold 門檻至 60 → 下跌段中多數時候 RSI < 60 → -1 減少或消失。

    此測試驗證 rsi_not_oversold 參數真的影響空頭過濾效果。
    """
    rng = np.random.default_rng(42)
    steps = -0.003 + rng.standard_normal(500) * 0.012
    prices = 100.0 * np.exp(np.cumsum(steps))
    data = _canonical_from_prices(prices)

    strat_low = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, rsi_not_oversold=30.0)
    strat_high = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, rsi_not_oversold=60.0)
    sig_low = strat_low.generate_signals(data)
    sig_high = strat_high.generate_signals(data)
    # 門檻高 → 空頭訊號應 ≤ 門檻低的情況（更嚴苛的下限過濾掉更多空頭）
    assert (sig_high == -1).sum() <= (sig_low == -1).sum()


# ===== A2 多尺度 regime 一致性（2026-07-03）=====

def test_long_window_blocks_countertrend_short():
    """長期上漲中的局部下跌子段：v2（單視窗）會做空，A2（多尺度）應全部過濾。

    這正是上次執行 fold3/fold5 逆勢空頭的情境：rolling 120 根在整體
    trend_up/range 中誤判出假性 trend_down 子段。
    """
    rng = np.random.default_rng(42)
    up_steps = 0.004 + rng.standard_normal(400) * 0.008
    down_steps = -0.004 + rng.standard_normal(130) * 0.010
    prices = 100.0 * np.exp(np.cumsum(np.concatenate([up_steps, down_steps])))
    data = _canonical_from_prices(prices)

    strat_v2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, regime_window=120, long_regime_window=None
    )
    strat_a2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, regime_window=120, long_regime_window=300
    )
    sig_v2 = strat_v2.generate_signals(data)
    sig_a2 = strat_a2.generate_signals(data)

    assert (sig_v2 == -1).sum() > 0, "前提：單視窗 v2 在局部下跌子段應出現空頭"
    assert (sig_a2 == -1).sum() == 0, "A2 長視窗不同意時，逆勢空頭應被全部過濾"


def test_long_window_keeps_short_in_sustained_downtrend():
    """夠長的持續下跌：A2 不應把正確空頭誤殺（fold4 情境）。"""
    rng = np.random.default_rng(42)
    steps = -0.003 + rng.standard_normal(600) * 0.012
    prices = 100.0 * np.exp(np.cumsum(steps))
    data = _canonical_from_prices(prices)
    strat_a2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, regime_window=120, long_regime_window=300
    )
    sig = strat_a2.generate_signals(data)
    assert (sig == -1).sum() > 0, "持續下跌段 A2 仍應出現空頭訊號"


def test_long_window_none_equals_single_window():
    """long_regime_window=None（v2 相容模式）與加長視窗前行為等價：
    A2 的空頭必為 v2 空頭的子集，多頭完全相同。"""
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(600) * 0.01))
    data = _canonical_from_prices(prices)
    sig_v2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, long_regime_window=None
    ).generate_signals(data)
    sig_a2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, long_regime_window=300
    ).generate_signals(data)
    assert ((sig_a2 == 1) == (sig_v2 == 1)).all(), "多頭不受長視窗過濾影響"
    a2_short = set(np.flatnonzero((sig_a2 == -1).to_numpy()))
    v2_short = set(np.flatnonzero((sig_v2 == -1).to_numpy()))
    assert a2_short.issubset(v2_short), "A2 空頭必為 v2 空頭的子集"


# ===== 多頭訊號不受 regime 過濾 =====

def test_long_signal_appears_in_uptrend():
    """上漲段（trend_up）：多頭條件成立時 +1 應出現。"""
    rng = np.random.default_rng(42)
    steps = 0.004 + rng.standard_normal(500) * 0.02
    prices = 100.0 * np.exp(np.cumsum(steps))
    data = _canonical_from_prices(prices)
    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60)
    sig = strat.generate_signals(data)
    assert (sig == 1).sum() > 0, "上漲段應出現多頭訊號"


def test_long_signal_matches_ma_rsi():
    """多頭 +1 的集合與 ma_rsi.py 完全一致（long_cond 邏輯未改）。

    MA 條件互斥（快>慢 vs 快<慢），long_cond 與 short_cond_raw 不可能同時為 True，
    因此雙向策略的 +1 == ma_rsi.py 的 +1。
    """
    rng = np.random.default_rng(99)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(500) * 0.01))
    data = _canonical_from_prices(prices)
    base = MaRsiStrategy(fast_window=20, slow_window=60).generate_signals(data)
    bidir = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60).generate_signals(data)
    assert ((bidir == 1) == (base == 1)).all()


# ===== 因果性（無未來函數） =====

def test_no_lookahead_truncation_consistent():
    """截掉後段資料後，前段訊號不應改變（regime 過濾因果性驗證）。"""
    rng = np.random.default_rng(1)
    prices = 100.0 * np.exp(np.cumsum(rng.standard_normal(500) * 0.01))
    full = _canonical_from_prices(prices)
    truncated = _canonical_from_prices(prices[:300])

    strat = MaRsiBidirectionalStrategy(fast_window=20, slow_window=60, regime_window=120)
    sig_full = strat.generate_signals(full)
    sig_trunc = strat.generate_signals(truncated)

    np.testing.assert_array_equal(
        sig_full.to_numpy()[:300],
        sig_trunc.to_numpy(),
        err_msg="前 300 根訊號在截斷前後必須完全一致（無未來函數）",
    )


# ===== 無效參數 =====

def test_invalid_fast_slow_raise():
    with pytest.raises(ValueError, match="fast_window"):
        MaRsiBidirectionalStrategy(fast_window=60, slow_window=20)


def test_invalid_regime_window_raise():
    with pytest.raises(ValueError, match="regime_window"):
        MaRsiBidirectionalStrategy(regime_window=2)


def test_invalid_long_regime_window_raise():
    """long_regime_window 必須大於 regime_window（相等也不行）。"""
    with pytest.raises(ValueError, match="long_regime_window"):
        MaRsiBidirectionalStrategy(regime_window=120, long_regime_window=120)
    with pytest.raises(ValueError, match="long_regime_window"):
        MaRsiBidirectionalStrategy(regime_window=120, long_regime_window=100)


def test_invalid_rsi_thresholds_raise():
    """rsi_not_oversold >= rsi_overbought 應拒絕。"""
    with pytest.raises(ValueError, match="rsi_not_oversold"):
        MaRsiBidirectionalStrategy(rsi_not_oversold=70.0, rsi_overbought=70.0)
    with pytest.raises(ValueError, match="rsi_not_oversold"):
        MaRsiBidirectionalStrategy(rsi_not_oversold=80.0, rsi_overbought=70.0)
