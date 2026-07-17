"""短倉強平近似（short_risk_overlay）單元測試。

驗證：
  - 純下跌段空頭不觸發強平（無虧損）
  - 固定安全網（15%）正確觸發並截斷
  - ATR 動態防線正確觸發並截斷
  - ATR 比 15% 更早觸發時以 ATR 為準
  - ATR NaN 時不誤判強平
  - 多頭/空手倉位不受影響
  - 回傳序列已完成 shift(1)（第一根永遠為 0）
  - index 不對齊時拋 ValueError
  - run_start=0 邊界（前無可用 close 時用當根）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.short_risk_overlay import apply_short_forced_liquidation


def _make_series(values, name="s"):
    idx = pd.RangeIndex(len(values))
    return pd.Series(values, index=idx, name=name, dtype=float)


# ===== 基本輸出屬性 =====

def test_output_length_matches_input():
    sig = _make_series([0, -1, -1, -1, 0])
    close = _make_series([100, 99, 98, 97, 96])
    atr = _make_series([2.0, 2.0, 2.0, 2.0, 2.0])
    result = apply_short_forced_liquidation(sig, close, atr)
    assert len(result) == len(sig)


def test_first_bar_always_zero_after_shift():
    """回傳值已含 shift(1)，第一根必須為 0。"""
    sig = _make_series([-1, -1, -1, -1])
    close = _make_series([100, 99, 98, 97])
    atr = _make_series([2.0, 2.0, 2.0, 2.0])
    result = apply_short_forced_liquidation(sig, close, atr)
    assert result.iloc[0] == 0.0


# ===== 純下跌段：不觸發強平 =====

def test_downtrend_no_liquidation():
    """空頭持倉期間價格持續下跌 → 強平條件永不成立 → 倉位不被截斷。"""
    # signal: 從 bar1 開始 -1
    # position after shift: [0, 0, -1, -1, -1, -1]
    sig = _make_series([0, -1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 98.0, 95.0, 92.0, 90.0])
    # entry_price ≈ close[1] = 100（position 從 bar2 開始）
    atr = _make_series([3.0, 3.0, 3.0, 3.0, 3.0, 3.0])
    result = apply_short_forced_liquidation(sig, close, atr)
    # position 應完整保留（無強平）
    expected = _make_series([0.0, 0.0, -1.0, -1.0, -1.0, -1.0])
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ===== 固定安全網（15%）觸發 =====

def test_fixed_pct_triggers_liquidation():
    """價格從進場點反彈超過 15% → 固定安全網觸發強平。

    position runs: bar2 ~ bar5 皆為 -1
    entry_price = close[1] = 100
    bar5: (118 - 100)/100 = 18% >= 15% → 觸發
    """
    sig = _make_series([0, -1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 102.0, 105.0, 108.0, 118.0])
    # ATR 設大以確保不先觸發 ATR 防線
    atr = _make_series([50.0, 50.0, 50.0, 50.0, 50.0, 50.0])
    result = apply_short_forced_liquidation(sig, close, atr)
    # bar5（index=5）應被強平為 0
    assert result.iloc[5] == 0.0
    # bar2~bar4 不受影響
    assert (result.iloc[2:5] == -1.0).all()


def test_fixed_pct_zeros_only_from_trigger():
    """強平只從觸發那根開始清零，之前的倉位不動。"""
    sig = _make_series([0, -1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 100.0, 100.0, 100.0, 116.0])
    atr = _make_series([50.0] * 6)
    result = apply_short_forced_liquidation(sig, close, atr)
    assert result.tolist() == [0.0, 0.0, -1.0, -1.0, -1.0, 0.0]


# ===== ATR 動態防線觸發 =====

def test_atr_triggers_liquidation():
    """price >= entry + 3*ATR → ATR 防線觸發（優先於固定安全網）。

    entry_price = close[1] = 100
    ATR = 4 → 防線 = 100 + 3*4 = 112
    bar4: close=113 >= 112 → 觸發（遠未到 15% 固定安全網 115）
    """
    sig = _make_series([0, -1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 103.0, 107.0, 113.0, 120.0])
    atr = _make_series([4.0, 4.0, 4.0, 4.0, 4.0, 4.0])
    result = apply_short_forced_liquidation(sig, close, atr, atr_n=3.0, fixed_pct=0.15)
    # bar4 (index=4) 觸發強平
    assert result.iloc[4] == 0.0
    assert result.iloc[5] == 0.0
    # bar2, bar3 應仍為 -1
    assert result.iloc[2] == -1.0
    assert result.iloc[3] == -1.0


def test_atr_triggers_before_fixed_pct():
    """ATR 防線比固定安全網更早觸發，應以 ATR 時間點為準。"""
    # entry = 100, ATR=6 → 防線 = 118, fixed 15% = 115
    # bar2: 119 >= 118 → ATR 先觸發（fixed 115 也同時 >= 但相同根）
    # 讓 ATR 在 bar2 觸發，fixed 在 bar3 才到
    sig = _make_series([0, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 119.0, 116.0, 100.0])
    atr = _make_series([6.0, 6.0, 6.0, 6.0, 6.0])
    result = apply_short_forced_liquidation(sig, close, atr, atr_n=3.0, fixed_pct=0.20)
    # ATR 防線 = 100 + 3*6 = 118，bar2 (119) >= 118 → 觸發
    assert result.iloc[2] == 0.0


# ===== ATR NaN 不誤判 =====

def test_atr_nan_skipped():
    """ATR 為 NaN 的根不觸發強平（不能用 NaN 做比較）。

    前 13 根 ATR=NaN（window=14 暖機期），即使 price 反彈也不強平，
    等到 ATR 有值才開始判斷。
    """
    sig = _make_series([0, -1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 200.0, 300.0, 400.0, 500.0])  # 極端反彈
    atr = _make_series([np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
    result = apply_short_forced_liquidation(sig, close, atr)
    # ATR 全 NaN → 沒有觸發 → 倉位不被截斷
    expected = _make_series([0.0, 0.0, -1.0, -1.0, -1.0, -1.0])
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ===== 多頭 / 空手不受影響 =====

def test_long_positions_unchanged():
    """多頭（+1）與空手（0）的倉位不被強平邏輯修改。"""
    sig = _make_series([1, 1, 1, 0, 0])
    close = _make_series([100.0, 90.0, 80.0, 70.0, 60.0])
    atr = _make_series([2.0, 2.0, 2.0, 2.0, 2.0])
    result = apply_short_forced_liquidation(sig, close, atr)
    # shift(1): [0, 1, 1, 1, 0]
    expected = _make_series([0.0, 1.0, 1.0, 1.0, 0.0])
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ===== 多段空頭獨立計算 =====

def test_two_separate_short_runs_independent():
    """兩段不相連的 -1 區段，各自獨立計算進場價與強平觸發。

    第一段：entry=100，bar2~3，無觸發（不反彈）
    第二段：entry=90（close[4]），bar5~6，bar6 反彈 > 15%
    """
    sig = _make_series([0, -1, -1, 0, -1, -1, -1])
    close = _make_series([100.0, 100.0, 95.0, 90.0, 90.0, 92.0, 105.0])
    atr = _make_series([50.0] * 7)
    result = apply_short_forced_liquidation(sig, close, atr, fixed_pct=0.15)
    # 第一段 bar2~3：95, 90 < 100 → 無觸發
    assert result.iloc[2] == -1.0
    assert result.iloc[3] == -1.0
    # 第二段 bar5~6：entry=90（close[4]）
    # bar5: 92 → (92-90)/90=2.2% < 15%
    # bar6: 105 → (105-90)/90=16.7% >= 15% → 觸發
    assert result.iloc[5] == -1.0
    assert result.iloc[6] == 0.0


# ===== 邊界：run_start=0（fold 開頭就是空頭）=====

def test_run_start_at_zero():
    """第 0 根直接是 -1（signal[0]=-1）→ shift 後 position[0]=0，
    所以 run_start=1，entry_price=close[0]，正常觸發。"""
    sig = _make_series([-1, -1, -1, -1, -1])
    close = _make_series([100.0, 100.0, 100.0, 100.0, 116.0])
    atr = _make_series([50.0] * 5)
    result = apply_short_forced_liquidation(sig, close, atr, fixed_pct=0.15)
    # position after shift: [0, -1, -1, -1, -1]
    # run_start=1, entry_price=close[0]=100
    # bar4: 116 → 16% >= 15% → 強平
    assert result.iloc[0] == 0.0  # shift 後第一根永遠 0
    assert result.iloc[4] == 0.0  # 強平


# ===== index 不對齊拋 ValueError =====

def test_mismatched_close_index_raises():
    sig = _make_series([0, -1, -1])
    close = pd.Series([100.0, 99.0, 98.0], index=[0, 1, 99])  # 不同 index
    atr = _make_series([2.0, 2.0, 2.0])
    with pytest.raises(ValueError, match="close"):
        apply_short_forced_liquidation(sig, close, atr)


def test_mismatched_atr_index_raises():
    sig = _make_series([0, -1, -1])
    close = _make_series([100.0, 99.0, 98.0])
    atr = pd.Series([2.0, 2.0, 2.0], index=[0, 1, 99])  # 不同 index
    with pytest.raises(ValueError, match="atr"):
        apply_short_forced_liquidation(sig, close, atr)
