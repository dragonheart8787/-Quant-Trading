"""MA/RSI + 籌碼閘門最小策略與過濾成因拆解測試（strategy/ma_rsi_chip.py）。

重點：
- 籌碼版 = baseline 做多 AND foreign_net>0（唯一差異是閘門）。
- NaN foreign_net（缺失/stale）→ 空手（NaN>0==False），不 crash 不誤判。
- decompose_chip_filtering 把「baseline 做多卻在 chip 版消失」的 bar 依成因拆兩類，
  且三類劃分恆等式成立。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cleaning import CANONICAL_COLUMNS
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_chip import (
    MaRsiChipStrategy,
    decompose_chip_filtering,
    rolling_chip_sum,
)

FAST, SLOW, RSI_W = 5, 20, 14


def _daily_canonical(close: np.ndarray) -> pd.DataFrame:
    """把 close 陣列包成合法日K canonical（其餘欄位填占位值）。"""
    n = len(close)
    ts = pd.date_range("2024-01-02 01:00:00", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({
        "ts": ts,
        "symbol": "TEST",
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
        "volume": 1000.0, "turnover": close * 1000.0, "vwap": close,
        "exchange": "TWSE", "interval": "1d", "source": "test",
        "ingested_at": ts,
    })
    return df[CANONICAL_COLUMNS]


def _oscillating_uptrend(n: int = 70) -> np.ndarray:
    """緩升 + 震盪：讓 fast>slow（趨勢向上）但 RSI 在下行段回落 <70，
    使 baseline 在暖機後有若干做多 bar（實測此形狀約 7 根）。"""
    i = np.arange(n)
    return 100.0 + 0.5 * i + 7.0 * np.sin(i / 4.0)


def test_chip_gate_equals_baseline_and_foreign_positive():
    """籌碼版逐位 == baseline AND (foreign_net>0)；且確實有 baseline 做多 bar 被濾掉。"""
    data = _daily_canonical(_oscillating_uptrend())
    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    long_idx = np.where(base.to_numpy() == 1)[0]
    assert len(long_idx) >= 3, "測試資料需有 >=3 根 baseline 做多 bar 以佈置三種情況"

    fn = np.full(len(data), 5.0)          # 預設外資買超（>0）→ 保留
    fn[long_idx[0]] = -3.0                # 外資賣超 → 第一類過濾
    fn[long_idx[1]] = np.nan              # 籌碼缺失 → 第二類過濾
    data["foreign_net"] = fn

    chip = MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)

    gate = (pd.Series(fn) > 0).fillna(False).to_numpy()
    expected = ((base.to_numpy() == 1) & gate).astype(int)
    assert (chip.to_numpy() == expected).all()

    # 具體：外資賣超與籌碼缺失的兩根 baseline=1 在 chip 版變 0；買超那根維持 1
    assert chip.iloc[long_idx[0]] == 0
    assert chip.iloc[long_idx[1]] == 0
    assert chip.iloc[long_idx[2]] == 1


def test_nan_foreign_net_forces_flat():
    """foreign_net 全 NaN（例：籌碼全缺失/stale）→ 籌碼版全空手，即使 baseline 有做多。"""
    data = _daily_canonical(_oscillating_uptrend())
    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    assert (base == 1).any()  # baseline 確實有做多

    data["foreign_net"] = np.nan
    chip = MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    assert (chip == 0).all()


def test_all_positive_foreign_net_equals_baseline():
    """foreign_net 全為正 → 閘門不濾任何東西 → 籌碼版逐位等於 baseline（不變量）。"""
    data = _daily_canonical(_oscillating_uptrend())
    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    data["foreign_net"] = 1.0
    chip = MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    assert (chip.to_numpy() == base.to_numpy()).all()


def test_missing_foreign_net_column_raises():
    data = _daily_canonical(_oscillating_uptrend())
    with pytest.raises(ValueError, match="foreign_net"):
        MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)


def test_signals_are_binary():
    data = _daily_canonical(_oscillating_uptrend())
    data["foreign_net"] = np.tile([1.0, -1.0, np.nan], len(data))[: len(data)]
    chip = MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    assert set(np.unique(chip.to_numpy())).issubset({0, 1})


# ---------- chip_window（N 日淨買超 smoothing，2026-07-10）----------


def test_chip_window_one_equals_current_behavior():
    """★保護性回歸★ chip_window=1（預設）與現行「當日 foreign_net>0」閘門
    逐位相同——含正值/負值/NaN 混合的 foreign_net。"""
    data = _daily_canonical(_oscillating_uptrend())
    data["foreign_net"] = np.tile([5.0, -3.0, np.nan, 0.0], len(data))[: len(data)]

    default_sig = MaRsiChipStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    explicit_sig = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=1).generate_signals(data)

    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    gate = (pd.Series(data["foreign_net"]) > 0).fillna(False).to_numpy()
    expected = ((base.to_numpy() == 1) & gate).astype(int)

    assert (default_sig.to_numpy() == expected).all()
    assert (explicit_sig.to_numpy() == default_sig.to_numpy()).all()


def test_chip_window_rolling_is_causal():
    """rolling 只看過去：竄改第 k 根之後的 foreign_net 成極大正值，
    第 k 根（含）之前的訊號必須逐位不變（未來資料打不進視窗）。"""
    data = _daily_canonical(_oscillating_uptrend())
    data["foreign_net"] = -1.0  # 全負 → 平滑閘門全關
    strat = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=5)
    sig_before = strat.generate_signals(data)

    k = 40
    tampered = data.copy()
    tampered.loc[tampered.index[k + 1]:, "foreign_net"] = 1e9  # 只改未來
    sig_after = strat.generate_signals(tampered)

    assert (sig_before.iloc[: k + 1].to_numpy() == sig_after.iloc[: k + 1].to_numpy()).all()


def test_chip_window_n_day_sum_semantics():
    """N 日累加語意（chip_window=5）：
    (a) 當日賣超但 5 日淨買超 → 開閘（當日版會關）；
    (b) 當日買超但 5 日淨賣超 → 關閘（當日版會開）。"""
    data = _daily_canonical(_oscillating_uptrend())
    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    long_idx = np.where(base.to_numpy() == 1)[0]
    i = int(long_idx[long_idx >= 5][0])   # 取一根視窗完整的 baseline 做多 bar

    # (a) 當日 -2、前 4 日各 +3 → 5 日合計 +10 > 0
    case_a = data.copy()
    fn_a = np.full(len(data), 3.0)
    fn_a[i] = -2.0
    case_a["foreign_net"] = fn_a
    sig_smooth = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=5).generate_signals(case_a)
    sig_daily = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=1).generate_signals(case_a)
    assert sig_smooth.iloc[i] == 1   # 5 日淨買超 → 開
    assert sig_daily.iloc[i] == 0    # 當日賣超 → 關

    # (b) 當日 +2、前 4 日各 -10 → 5 日合計 -38 < 0
    case_b = data.copy()
    fn_b = np.full(len(data), 3.0)
    fn_b[i - 4 : i] = -10.0
    fn_b[i] = 2.0
    case_b["foreign_net"] = fn_b
    sig_smooth = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=5).generate_signals(case_b)
    sig_daily = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=1).generate_signals(case_b)
    assert sig_smooth.iloc[i] == 0   # 5 日淨賣超 → 關
    assert sig_daily.iloc[i] == 1    # 當日買超 → 開


def test_chip_window_warmup_and_nan_force_flat():
    """min_periods=chip_window 嚴格語意：資料起頭不足 N 個非 NaN（暖機）與
    視窗內任一 NaN（單日缺失傳染 N 天）都關閘。"""
    data = _daily_canonical(_oscillating_uptrend())
    base = MaRsiStrategy(FAST, SLOW, RSI_W).generate_signals(data)
    long_idx = np.where(base.to_numpy() == 1)[0]
    assert len(long_idx) >= 2
    i, j = int(long_idx[0]), int(long_idx[1])

    # 暖機：首個籌碼值晚到（i-2 起才有值）→ bar i 視窗內非 NaN 不足 5 → 關閘
    fn = np.full(len(data), np.nan)
    fn[i - 2 :] = 3.0
    data["foreign_net"] = fn
    sig = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=5).generate_signals(data)
    assert sig.iloc[i] == 0

    # NaN 傳染：j-2 單日缺失 → j 的 5 日視窗含 NaN → 關閘；當日版不受影響
    fn2 = np.full(len(data), 3.0)
    fn2[j - 2] = np.nan
    data["foreign_net"] = fn2
    sig_smooth = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=5).generate_signals(data)
    sig_daily = MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=1).generate_signals(data)
    assert sig_smooth.iloc[j] == 0
    assert sig_daily.iloc[j] == 1


def test_chip_window_below_one_raises():
    with pytest.raises(ValueError, match="chip_window"):
        MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=0)


def test_rolling_chip_sum_matches_manual():
    """rolling_chip_sum 的視窗語意：含當根、往回共 N 根、不足 N 個非 NaN 給 NaN。"""
    fn = pd.Series([1.0, 2.0, 3.0, np.nan, 5.0, 6.0])
    out = rolling_chip_sum(fn, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])   # 暖機
    assert out.iloc[2] == 6.0                                 # 1+2+3
    # NaN 傳染精確範圍：idx3/4/5 的 3 根視窗都含 idx3 的 NaN
    assert np.isnan(out.iloc[3]) and np.isnan(out.iloc[4]) and np.isnan(out.iloc[5])


def test_decompose_partitions_baseline_long():
    """三類（保留/外資賣超/籌碼缺失）互斥且窮盡地劃分所有 baseline 做多 bar。"""
    base = pd.Series([0, 1, 1, 1, 0, 1, 1])
    fn = pd.Series([5.0, 8.0, -2.0, np.nan, 3.0, 0.0, np.nan])
    #                idx1 保留 idx2賣超 idx3缺失     idx5<=0賣超 idx6缺失
    d = decompose_chip_filtering(base, fn)
    assert d["baseline_long"] == 5
    assert d["kept_long"] == 1               # idx1（8>0）
    assert d["filtered_foreign_sell"] == 2   # idx2(-2)、idx5(0<=0)
    assert d["filtered_missing_chip"] == 2   # idx3、idx6（NaN）
    # 恆等式：三類相加 == baseline_long
    assert d["kept_long"] + d["filtered_foreign_sell"] + d["filtered_missing_chip"] == d["baseline_long"]
