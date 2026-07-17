"""MA/RSI + 單一籌碼閘門的最小示範策略（Phase 3 訊號層驗證用）。

目的：乾淨驗證「籌碼特徵能否被策略邏輯正確讀取並影響訊號」，不是調 alpha。
在 baseline MA/RSI 做多條件（與 ma_rsi.py 同邏輯）上，疊加**單一**籌碼閘門
`foreign_net > 0`（外資買超）。不動 ma_rsi.py（★不要動★）。

訊號 {0, +1}（long-only，比照 baseline）：
    signal = 1  where  (SMA_fast > SMA_slow) AND (RSI < overbought) AND (foreign_net > 0)
             0  otherwise

籌碼閘門的 NaN 語意（重要）：foreign_net 在首個籌碼日之前、或 as-of 過期（stale）
後為 NaN。`NaN > 0 == False` → 閘門天然給 0（空手），是安全預設，且能誠實反映
「籌碼缺失/過期時策略停止進場」（例：6446 在 2024-01-25 籌碼斷尾後）。
但「因外資賣超濾掉」與「因籌碼缺失濾掉」是兩種不同成因——用 decompose_chip_filtering
在分析層把兩者分開計數，避免把資料品質空窗誤讀成策略邏輯效果。

N 日淨買超 smoothing（chip_window，2026-07-10）：當日閘門逐日翻正負會放大換手
（2330 實測 base 換手 3 次 vs chip 20 次），台股成本重在賣出端（證交稅），換手
放大直接侵蝕報酬。chip_window>1 時閘門改為「過去 N 根 bar 的 foreign_net 累加
> 0」。**防未來函數**：視窗含當根 bar 的 as-of 值，但該值本身是上游排他式
as-of（allow_exact_matches=False）的產物、恆為嚴格早於當日的籌碼——整個視窗
全是過去資料，rolling 向後看，與 ATR 的因果寫法同一原則。
**嚴格 NaN 語意（min_periods=chip_window）**：暖機期（前 N-1 個非 NaN）與視窗內
任一 NaN 都給 NaN → 關閘空手；單日籌碼缺失會關閘 N 天（保守的誠實預設）。
**已知近似**：rolling 對「對齊後 bar 序列」計算——中間籌碼日缺資料時，as-of 會讓
相鄰 bar 重複拿到同一籌碼日的值、被重複計入（重複上限受 max_age_days 封頂）；
精確版需在籌碼日曆上先 rolling 再對齊，屬未來工作。
chip_window=1（預設）與原「當日 foreign_net>0」逐位相同（保護性回歸測試固定）。
起點值 5（一個交易週）★待校正，非最終值★（比照 ATR N=3 慣例）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators.technical import rsi, sma
from strategy.base import BaseStrategy

FOREIGN_NET_COL = "foreign_net"


def rolling_chip_sum(foreign_net: pd.Series, window: int) -> pd.Series:
    """過去 window 根 bar（含當根）的 foreign_net 累加；因果 rolling 向後看。

    min_periods=window 嚴格語意：視窗內非 NaN 不足 window 個（暖機或單日缺失）
    一律給 NaN → 上層閘門 NaN>0==False 關閘。策略與分析層（runner 的過濾拆解）
    共用此函式，避免兩處 rolling 邏輯漂移。
    """
    fn = pd.to_numeric(foreign_net, errors="coerce")
    return fn.rolling(window, min_periods=window).sum()


@dataclass
class MaRsiChipStrategy(BaseStrategy):
    """雙均線 + RSI + 外資買超閘門（long-only）。MA/RSI 參數與 baseline 對齊，
    使 baseline 與本策略的唯一差異就是籌碼閘門，便於歸因。

    chip_window=1（預設）= 當日 as-of foreign_net > 0（原行為，逐位相同）；
    chip_window=N>1 = 過去 N 根 bar 累加 > 0（smoothing，起點 5 ★待校正★）。
    """

    fast_window: int = 5
    slow_window: int = 20
    rsi_window: int = 14
    rsi_overbought: float = 70.0
    chip_window: int = 1

    name: str = "ma_rsi_chip"

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必須小於 slow_window")
        if self.chip_window < 1:
            raise ValueError("chip_window 至少為 1")

    def baseline_long_mask(self, data: pd.DataFrame) -> np.ndarray:
        """baseline MA/RSI 做多條件（暖機期視為空手）；bool 陣列，與 data 等長。

        與 strategy/ma_rsi.py 的 long_cond 同邏輯——刻意重算而非 import，讓籌碼版
        自足，且保證兩者唯一差異是籌碼閘門。
        """
        close = data["close"]
        fast = sma(close, self.fast_window)
        slow = sma(close, self.slow_window)
        r = rsi(close, self.rsi_window)
        long_cond = (fast > slow) & (r < self.rsi_overbought)
        warmup = fast.isna() | slow.isna() | r.isna()
        return (long_cond & ~warmup).to_numpy()

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        self._check_input(data)
        if FOREIGN_NET_COL not in data.columns:
            raise ValueError(
                f"chip 策略需要 {FOREIGN_NET_COL} 欄；請先用 "
                f"indicators.features.align_chips_to_bars 併入籌碼特徵"
            )
        base_long = self.baseline_long_mask(data)
        chip_sum = rolling_chip_sum(data[FOREIGN_NET_COL], self.chip_window)
        chip_gate = (chip_sum > 0).to_numpy()  # NaN > 0 == False → 缺失/暖機自動空手
        signal = np.where(base_long & chip_gate, 1, 0)
        return pd.Series(signal, index=data.index, name="signal", dtype="int64")


def decompose_chip_filtering(
    baseline_signal: pd.Series, foreign_net: pd.Series
) -> dict[str, int]:
    """把「baseline 做多、但籌碼版沒做多」的 bar 依成因拆兩類（分析用，非策略邏輯）。

    - filtered_foreign_sell：foreign_net **有值且 <= 0**（外資沒買超）→ 閘門正常生效。
    - filtered_missing_chip：foreign_net **為 NaN**（籌碼缺失或 as-of 過期）→ 訊號因
      資料品質被迫消失，不是策略判斷。6446 在 2024-01-25 斷尾後應集中在此類。

    回傳計數 dict；恆等式 baseline_long == kept_long + filtered_foreign_sell +
    filtered_missing_chip（三類互斥且窮盡地劃分所有 baseline 做多 bar）。
    """
    base_long = (pd.Series(baseline_signal).to_numpy() == 1)
    fn = pd.to_numeric(pd.Series(foreign_net), errors="coerce").to_numpy()
    is_nan = np.isnan(fn)

    kept = base_long & ~is_nan & (fn > 0)
    foreign_sell = base_long & ~is_nan & (fn <= 0)
    missing_chip = base_long & is_nan
    return {
        "baseline_long": int(base_long.sum()),
        "kept_long": int(kept.sum()),
        "filtered_foreign_sell": int(foreign_sell.sum()),
        "filtered_missing_chip": int(missing_chip.sum()),
    }
