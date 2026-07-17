"""MA/RSI 雙向策略：多頭 + 多尺度 regime 過濾空頭。

訊號定義：
  +1（多頭）：快線 > 慢線（黃金交叉）且 RSI 未超買（< rsi_overbought）
              多頭沿用既有 ma_rsi.py 邏輯，不加 regime 過濾。
  -1（空頭）：快線 < 慢線（死亡交叉）
              且 RSI > rsi_not_oversold（排除深度超賣，預設 > 30）
              且 短視窗（120 根）rolling regime = trend_down（因果計算）
              且（選配 A2）長視窗 rolling regime = trend_down（多尺度一致性）
   0（空手）：其餘情況

多尺度一致性（A2, 2026-07-03，預設停用）：
  短視窗 120 根可能在整體 trend_up/range 中誤判出假性 trend_down 子段，
  產生逆勢空頭。傳入 long_regime_window（如 300）啟用長視窗同向確認。
  預設 None = v2 單視窗模式（當前基準）；A2 去留未定案，
  乾淨基準與代價分析見 HANDOFF.md「落地資料乾淨基準」。

設計決策紀錄（2026-06-25）：
  原始版本用 RSI >= rsi_overbought（70）作為空頭 RSI 條件，在 fold4 實驗中
  n_short=0，因為 regime_window=120 確認 trend_down 的滯後性（120 根才確認），
  與 RSI 超買（即時反轉信號）的時間窗完全不重疊：trend_down 確立時 RSI 早已下行至
  低水位。這是訊號時間特性互斥，不是參數問題。

  修正邏輯：改用 RSI > rsi_not_oversold（30）作為空頭條件，語意為
  「RSI 不在深度超賣區（避免在最大悲觀點加空）+ 死亡交叉 + trend_down 已確認」。
  這與 trend_down 的滯後性完全相容：trend_down 確立時 RSI 通常在 30~70 之間波動。

多頭、空頭的 MA 條件互斥（快>慢 vs 快<慢），訊號天然無法同時為 +1 與 -1。

防未來函數：
  - 指標（SMA/RSI）因果計算（rolling）。
  - 空頭 regime 判斷用 trailing 視窗（close[t-w+1..t]），不偷看未來。
  - 最終訊號由 backtest/vector_engine.py 的 shift(1) 才轉成持倉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from backtest.regime import rolling_trend_down_mask
from indicators.technical import rsi, sma
from strategy.base import BaseStrategy


@dataclass
class MaRsiBidirectionalStrategy(BaseStrategy):
    """雙向 MA/RSI + 多尺度 trend_down 空頭 regime 過濾策略。"""

    fast_window: int = 20
    slow_window: int = 60
    rsi_window: int = 14
    rsi_overbought: float = 70.0    # 多頭上限：RSI 超買時不做多
    rsi_not_oversold: float = 30.0  # 空頭下限：RSI 深度超賣時不做空（避免逆勢）
    regime_window: int = 120        # 空頭 regime 短視窗（根）
    # 空頭 regime 長視窗（根）。None（預設）= 停用長視窗過濾 = v2 模式（當前基準）。
    # 傳入 DEFAULT_LONG_REGIME_WINDOW(300) 啟用 A2 多尺度過濾；A2 去留未定案
    # （2026-07-03 乾淨基準下 A2 彙總 mean/std 皆劣於 v2，但 5-fold 檢定力不足），
    # 程式碼與測試保留，等更大資料集重驗後決定。
    long_regime_window: Optional[int] = None

    name: str = "ma_rsi_bidirectional"

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必須小於 slow_window")
        if self.regime_window < 3:
            raise ValueError("regime_window 至少為 3")
        if self.long_regime_window is not None and (
            self.long_regime_window <= self.regime_window
        ):
            raise ValueError("long_regime_window 必須大於 regime_window")
        if not 0 <= self.rsi_not_oversold < self.rsi_overbought <= 100:
            raise ValueError(
                "rsi_not_oversold 必須介於 0~100 之間，且小於 rsi_overbought"
            )

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        self._check_input(data)
        close = data["close"]

        fast = sma(close, self.fast_window)
        slow = sma(close, self.slow_window)
        r = rsi(close, self.rsi_window)

        # 多頭條件：黃金交叉 + RSI 未超買（沿用 ma_rsi.py，不加 regime 過濾）
        long_cond = (fast > slow) & (r < self.rsi_overbought)

        # 空頭條件：死亡交叉 + RSI 不在深度超賣區 → 再過多尺度 trend_down regime 過濾
        short_cond_raw = (fast < slow) & (r > self.rsi_not_oversold)
        trend_down_mask = rolling_trend_down_mask(close, self.regime_window)
        if self.long_regime_window is not None:
            trend_down_mask = trend_down_mask & rolling_trend_down_mask(
                close, self.long_regime_window
            )
        short_cond = short_cond_raw & trend_down_mask

        warmup = fast.isna() | slow.isna() | r.isna()

        signal = np.where(long_cond, 1, np.where(short_cond, -1, 0))
        signal = np.where(warmup, 0, signal)

        return pd.Series(signal, index=data.index, name="signal", dtype="int64")
