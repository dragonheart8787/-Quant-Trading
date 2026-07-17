"""向量化技術指標。

硬性規範：禁止 DataFrame.apply(axis=1)，一律用 rolling/ewm/NumPy 向量運算。
所有指標為因果計算（causal）：只使用當前及過去資料，禁止用未來資料。
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """簡單移動平均。"""
    if window <= 0:
        raise ValueError("window 必須為正整數")
    return series.rolling(window=window, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI（向量化，用 ewm 近似 Wilder 平滑）。

    回傳 0~100 的 RSI 序列；前 window 根因資料不足為 NaN。
    """
    if window <= 0:
        raise ValueError("window 必須為正整數")

    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # Wilder 平滑等價於 alpha = 1/window 的 ewm
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss 為 0（全漲）時 rs=inf → rsi=100；用 fillna 補上
    rsi_val = rsi_val.where(avg_loss != 0, 100.0)
    rsi_val[avg_gain == 0] = rsi_val[avg_gain == 0].where(avg_loss == 0, 0.0)
    return rsi_val


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """向量化 ATR（Average True Range）。

    因果計算：True Range 使用 prev_close = close.shift(1)，rolling mean 向後看，
    絕不使用當前 bar 之後的任何資料。

    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    ATR = rolling mean(True Range, window)

    前 window 根因資料不足回傳 NaN（min_periods=window）。
    """
    if window <= 0:
        raise ValueError("window 必須為正整數")
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame 缺少欄位: {missing}")

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(window=window, min_periods=window).mean()
