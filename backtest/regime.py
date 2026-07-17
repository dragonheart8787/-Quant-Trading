"""市場狀態（regime）客觀標註：趨勢市 vs 盤整市。

用兩個客觀、向量化、無外部依賴的指標，避免主觀判斷：

1. **log 價格線性回歸 R²**：把 log(close) 對時間（0..n-1）做最小平方線性擬合，
   R² 高代表價格穩定朝單一方向移動（趨勢）；R² 低代表上下震盪（盤整）。
   斜率符號給出趨勢方向。

2. **Kaufman 效率比 (Efficiency Ratio, ER)**：
   ER = |close[-1] - close[0]| / Σ|close.diff()|，介於 0~1。
   接近 1 = 路徑近乎直線（強趨勢）；接近 0 = 來回鋸齒（盤整）。

兩者一致時結論最可靠；分類門檻明確寫死並一併輸出原始數值，供人覆核。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# 分類門檻（客觀、寫死、可覆核）
R2_TREND_THRESHOLD = 0.50   # R² >= 0.50 視為具方向性
ER_TREND_THRESHOLD = 0.30   # ER >= 0.30 視為路徑有效率（趨勢）

# 多尺度 regime 一致性過濾的視窗（空頭觸發用）
# 短視窗沿用既有 120；長視窗 300 為起點值，待用歷史資料校正，非最終值
# （比照 risk/manager.py ATR N=3 的處理方式：先用合理起點，校正另開任務）
DEFAULT_SHORT_REGIME_WINDOW = 120
DEFAULT_LONG_REGIME_WINDOW = 300


@dataclass
class RegimeLabel:
    r2: float            # log 價格線性回歸 R²（0~1）
    slope: float         # 線性回歸斜率（log 價格 / 每根）
    efficiency: float    # Kaufman 效率比（0~1）
    label: str           # "trend_up" / "trend_down" / "range"

    @property
    def is_trend(self) -> bool:
        return self.label.startswith("trend")


def linreg_r2(close: pd.Series) -> tuple[float, float]:
    """對 log(close) 做線性回歸，回傳 (R², slope)。"""
    y = np.log(np.asarray(close, dtype=float))
    n = len(y)
    if n < 3:
        return 0.0, 0.0
    x = np.arange(n, dtype=float)
    # 斜率用 polyfit；R² 用相關係數平方（等價於線性擬合的判定係數）
    slope = float(np.polyfit(x, y, 1)[0])
    corr = np.corrcoef(x, y)[0, 1]
    r2 = float(corr ** 2) if np.isfinite(corr) else 0.0
    return r2, slope


def efficiency_ratio(close: pd.Series) -> float:
    """Kaufman 效率比：淨位移 / 總路徑長。"""
    arr = np.asarray(close, dtype=float)
    if len(arr) < 2:
        return 0.0
    net = abs(arr[-1] - arr[0])
    path = np.abs(np.diff(arr)).sum()
    if path == 0:
        return 0.0
    return float(net / path)


def classify_regime(close: pd.Series) -> RegimeLabel:
    """對一段 close 序列做趨勢/盤整分類。

    規則（兩指標投票）：
      - R² >= R2_TREND_THRESHOLD 或 ER >= ER_TREND_THRESHOLD → 趨勢市
        （方向由 slope 符號決定 trend_up / trend_down）
      - 否則 → 盤整市 (range)
    用 OR 是因為兩指標各自會漏判一類；任一強烈成立即視為趨勢。
    """
    r2, slope = linreg_r2(close)
    er = efficiency_ratio(close)

    is_trend = (r2 >= R2_TREND_THRESHOLD) or (er >= ER_TREND_THRESHOLD)
    if is_trend:
        label = "trend_up" if slope >= 0 else "trend_down"
    else:
        label = "range"
    return RegimeLabel(r2=r2, slope=slope, efficiency=er, label=label)


def rolling_trend_down_mask(close: pd.Series, window: int) -> pd.Series:
    """逐根因果 trend_down 判斷：True = 以當根為終點的 trailing window 為 trend_down。

    第 t 根只用 close[t-window+1 .. t]（不含未來）；暖機期（前 window-1 根）一律 False。
    原實作位於 strategy/ma_rsi_bidirectional.py，抽到本模組供多視窗共用。
    """
    if window < 3:
        raise ValueError("window 至少為 3")
    values = np.asarray(close, dtype=float)
    n = len(values)
    mask = np.zeros(n, dtype=bool)
    for t in range(window - 1, n):
        segment = pd.Series(values[t - window + 1 : t + 1])
        mask[t] = classify_regime(segment).label == "trend_down"
    return pd.Series(mask, index=close.index)


def multi_scale_trend_down_mask(
    close: pd.Series,
    short_window: int = DEFAULT_SHORT_REGIME_WINDOW,
    long_window: int = DEFAULT_LONG_REGIME_WINDOW,
) -> pd.Series:
    """多尺度 regime 一致性：短視窗與長視窗同時判定 trend_down 才為 True。

    動機（2026-07-03 教訓）：局部 rolling 視窗（120 根）可能在整體 trend_up/range
    中誤判出假性 trend_down 子段，產生逆勢空頭。要求更長視窗（300 根，待校正）
    同向確認，阻止「短期下跌 + 長期上漲/盤整」情境下的空頭觸發。
    """
    if long_window <= short_window:
        raise ValueError("long_window 必須大於 short_window")
    return rolling_trend_down_mask(close, short_window) & rolling_trend_down_mask(
        close, long_window
    )
