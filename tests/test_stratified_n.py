"""ATR 分層 N 純函式測試（台股放空第一輪，2026-07-16）。

設計（使用者確認；錨定統計量 2026-07-16 同日修正 median→q95，見下）：
分層依據 = 股票自身 trailing ATR/price 分布（自我校準、因果、無外部分類），
不用市值/上市上櫃等武斷分類：

    N = clamp( target_extreme_pct / trailing_q95(ATR/close), n_min, n_max )

- trailing 段 = 呼叫端傳入的 train 側資料（嚴格因果，測試窗零參與）。
- ★錨定統計量修正（P4 第一版 FAIL 的根因修復）★：第一版用 median 錨定
  「典型水位」在 9%，但機制要守的是「極端日子線會不會跑到 15% 固定網之上」
  ——median 回答平常水位、目標卻是極端水位，統計量與問題目標錯位（與
  RSI/regime 時間錯位同類教訓）。修正：改錨 trailing q95 於 12%（保守端，
  留固定網餘裕），把「連 95 分位波動日的線都留在網下」直接寫進公式。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.liq_calibration import stratified_forced_liq_n


def _ohlc(close: np.ndarray, spread_pct: float) -> pd.DataFrame:
    """固定振幅的 OHLC：high-low = close × spread_pct，讓 ATR/close ≈ spread_pct。"""
    return pd.DataFrame({
        "high": close * (1 + spread_pct / 2),
        "low": close * (1 - spread_pct / 2),
        "close": close,
    })


def test_hand_calculated_n_from_constant_ratio():
    """恆定 ATR/close 比例（q95==median==恆定值）→ N 精確等於 target/ratio。"""
    close = np.full(300, 100.0)
    df = _ohlc(close, spread_pct=0.03)  # TR 恆為 3 → ATR/close = 3%
    n = stratified_forced_liq_n(df, target_extreme_pct=0.12)
    assert n == pytest.approx(0.12 / 0.03, rel=1e-9)  # = 4.0


def test_q95_anchor_reacts_to_tail_not_median():
    """尾部波動變大（median 不變）→ N 應下降；這正是 median 錨定看不到、
    q95 錨定看得到的差異（P4 第一版 FAIL 的根因防護測試）。"""
    close = np.full(400, 100.0)
    calm = _ohlc(close, spread_pct=0.03)
    spiky = _ohlc(close, spread_pct=0.03)
    # 每 10 根插入一根 3 倍振幅的高波動 bar：median(ATR/close) 幾乎不動、q95 上移
    spike_idx = list(range(9, 400, 10))
    spiky.loc[spike_idx, "high"] = spiky.loc[spike_idx, "close"] * 1.045
    spiky.loc[spike_idx, "low"] = spiky.loc[spike_idx, "close"] * 0.955
    n_calm = stratified_forced_liq_n(calm)
    n_spiky = stratified_forced_liq_n(spiky)
    assert n_spiky < n_calm


def test_higher_volatility_gives_lower_n():
    close = np.full(300, 100.0)
    n_low_vol = stratified_forced_liq_n(_ohlc(close, 0.02), target_extreme_pct=0.12)
    n_high_vol = stratified_forced_liq_n(_ohlc(close, 0.05), target_extreme_pct=0.12)
    assert n_high_vol < n_low_vol


def test_clamped_at_n_max_for_very_low_vol():
    """大型低波動股（如 2330 型）：target/ratio 超過上限時夾在 n_max。"""
    close = np.full(300, 100.0)
    df = _ohlc(close, spread_pct=0.005)  # ATR/close=0.5% → 未夾=24
    n = stratified_forced_liq_n(df, target_extreme_pct=0.12, n_min=1.5, n_max=5.0)
    assert n == pytest.approx(5.0)


def test_clamped_at_n_min_for_very_high_vol():
    close = np.full(300, 100.0)
    df = _ohlc(close, spread_pct=0.10)  # ATR/close=10% → 未夾=1.2
    n = stratified_forced_liq_n(df, target_extreme_pct=0.12, n_min=1.5, n_max=5.0)
    assert n == pytest.approx(1.5)


def test_only_trailing_lookback_used():
    """lookback 之外的早期資料變動不影響 N（因果窗定義）。"""
    rng = np.random.default_rng(5)
    tail = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 300))
    head_a = np.full(200, 100.0)
    head_b = np.full(200, 500.0)  # 完全不同的早期資料
    df_a = _ohlc(np.concatenate([head_a, tail]), 0.03)
    df_b = _ohlc(np.concatenate([head_b, tail]), 0.03)
    # 早期段的 spread 也刻意弄不同
    df_b.loc[:150, "high"] = df_b.loc[:150, "close"] * 1.2
    n_a = stratified_forced_liq_n(df_a, lookback=252)
    n_b = stratified_forced_liq_n(df_b, lookback=252)
    assert n_a == pytest.approx(n_b)


def test_insufficient_data_raises():
    close = np.full(20, 100.0)  # 不足以算出足量有效 ATR
    with pytest.raises(ValueError, match="不足"):
        stratified_forced_liq_n(_ohlc(close, 0.03))


def test_invalid_params_raise():
    close = np.full(300, 100.0)
    df = _ohlc(close, 0.03)
    with pytest.raises(ValueError):
        stratified_forced_liq_n(df, target_extreme_pct=0.0)
    with pytest.raises(ValueError):
        stratified_forced_liq_n(df, n_min=3.0, n_max=2.0)
