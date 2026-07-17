"""ATR 強平防線校正——機制層量測（Mechanism-layer instrumentation）。

目的：對 v2 空頭區段量測「N 倍數 × ATR window 網格」下的強平觸發行為，
供 forced_liq_atr_n / forced_liq_safety_pct（risk/manager.py 規則三）的
歷史資料校正使用。純量測、不改倉位、不算策略報酬（結果層另由
event_engine E2 承擔）。

慣例與既有模組嚴格對齊（可交叉驗證，tests 有背書）：
  - 區段定義 = short_risk_overlay：position = signal.shift(1).fillna(0)，
    連續 -1 為一段；進場價 = 區段首根前一收盤近似。
  - 觸發定義 = overlay / event_engine E2（決策③）：依據當根 close；
    ATR NaN 的根整個跳過（含固定安全網）。
  - trigger_source="high"（2026-07-14 high 觸價敏感度輪）：改用當根 high
    偵測盤中觸線。這是對 close-only 簡化假設的壓力測試，不是新的校正候選；
    high ≥ close 保證觸發集合 ⊇ close 模式、觸發時點 ≤（有 property test）。
    浮虧回報兩個界：loss_at_line_pct = 觸線價近似（entry + N×ATR 或固定網線，
    取先碰到的較低線；未含滑點與開盤跳空——若當根開盤即高於線，實際成交價
    會比線價差，此處低估）；loss_at_trigger_pct = 當根 trigger price
    （high 模式即盤中最壞上界）。

誤殺/保護分類（反事實，事後全知視角——已知偏差，報告時須併陳觸發當下浮虧）：
  - 誤殺 = 觸發，且該空單持有到訊號自然翻轉其實是賺的（held_to_end_return > 0）。
  - 保護 = 觸發，且持有到自然翻轉是虧的。
  - held_to_end_return 用固定倉位空單近似：(entry - close[end-1]) / entry。

已知近似（沿用 overlay 的文件化限制）：進場價非真實滑點成交價；
不模擬強平後再進場。量測結論只用於防線參數的相對比較。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

_DEFAULT_FIXED_PCT: float = 0.15


@dataclass(frozen=True)
class ShortSegment:
    """一段連續空頭持倉（切片內 index，end 為 exclusive）。"""

    start: int
    end: int
    entry_price: float


@dataclass(frozen=True)
class AdverseStats:
    """區段的不利波幅統計與反事實持有結果。"""

    max_adverse_pct: float      # max (close-entry)/entry，正 = 對空單不利
    max_atr_mult: float         # max (close-entry)/ATR[t]，ATR NaN 跳過；全 NaN → NaN
    held_to_end_return: float   # 固定倉位空單持有到區段自然結束的報酬


@dataclass(frozen=True)
class TriggerOutcome:
    """第一次觸發強平的紀錄。"""

    bar: int                    # 觸發 bar（切片內 index）
    line: str                   # "atr" / "fixed" / "both"（同根同時越過）
    loss_at_trigger_pct: float  # 觸發當下未實現虧損 (trigger_price-entry)/entry
    bars_from_start: int        # 距區段起點的 bar 數
    loss_at_line_pct: float = float("nan")  # 觸線價近似浮虧（先碰到的較低線）


def extract_short_segments(signal: pd.Series, close: pd.Series) -> list[ShortSegment]:
    """從未 shift 的訊號抽取空頭持倉區段（與 overlay 同語意）。

    position = signal.shift(1).fillna(0)：首根必為 0，故區段起點恆 ≥ 1，
    進場價 close[start-1] 永遠有值（overlay 的 fallback 分支僅防禦用）。
    """
    if not signal.index.equals(close.index):
        raise ValueError("signal 與 close 的 index 必須對齊")

    pos = signal.shift(1).fillna(0.0).to_numpy(dtype=float)
    close_arr = close.to_numpy(dtype=float)
    n = len(pos)

    segments: list[ShortSegment] = []
    i = 0
    while i < n:
        if pos[i] != -1.0:
            i += 1
            continue
        run_start = i
        while i < n and pos[i] == -1.0:
            i += 1
        entry = close_arr[run_start - 1] if run_start > 0 else close_arr[run_start]
        segments.append(ShortSegment(start=run_start, end=i, entry_price=float(entry)))
    return segments


def segment_adverse_stats(
    seg: ShortSegment, close: pd.Series, atr: pd.Series
) -> AdverseStats:
    """量測區段最大不利波幅（% 與 ATR 倍數）與持有到自然翻轉的反事實報酬。"""
    close_arr = close.to_numpy(dtype=float)
    atr_arr = atr.to_numpy(dtype=float)
    entry = seg.entry_price

    adverse = (close_arr[seg.start:seg.end] - entry) / entry
    atr_slice = atr_arr[seg.start:seg.end]
    valid = ~np.isnan(atr_slice)
    if valid.any():
        max_mult = float(
            ((close_arr[seg.start:seg.end][valid] - entry) / atr_slice[valid]).max()
        )
    else:
        max_mult = float("nan")

    held = (entry - close_arr[seg.end - 1]) / entry
    return AdverseStats(
        max_adverse_pct=float(adverse.max()),
        max_atr_mult=max_mult,
        held_to_end_return=float(held),
    )


def detect_trigger(
    seg: ShortSegment,
    close: pd.Series,
    atr: pd.Series,
    atr_n: float,
    fixed_pct: float = _DEFAULT_FIXED_PCT,
    trigger_price: Optional[pd.Series] = None,
) -> Optional[TriggerOutcome]:
    """找出區段內第一根觸發強平的 bar；掃描語意與 overlay/E2 逐位一致。

    ATR NaN 的根整個跳過（含固定安全網檢查），與
    short_risk_overlay.apply_short_forced_liquidation 及 event_engine 決策③相同。

    trigger_price：越線判定用的價格序列，None（預設）= close（現行 E2 語意，
    行為逐位不變）；傳 high 即盤中觸價敏感度模式（見模組 docstring）。
    """
    close_arr = close.to_numpy(dtype=float)
    tp_arr = (
        trigger_price.to_numpy(dtype=float) if trigger_price is not None else close_arr
    )
    atr_arr = atr.to_numpy(dtype=float)
    entry = seg.entry_price

    for t in range(seg.start, seg.end):
        atr_val = atr_arr[t]
        if np.isnan(atr_val):
            continue
        price = tp_arr[t]
        atr_level = entry + atr_n * atr_val
        atr_hit = price >= atr_level
        fixed_hit = entry > 0 and (price - entry) / entry >= fixed_pct
        if not (atr_hit or fixed_hit):
            continue
        line = "both" if (atr_hit and fixed_hit) else ("atr" if atr_hit else "fixed")
        hit_levels = [
            lv
            for lv, hit in (
                (atr_level, atr_hit),
                (entry * (1.0 + fixed_pct), fixed_hit),
            )
            if hit
        ]
        return TriggerOutcome(
            bar=t,
            line=line,
            loss_at_trigger_pct=float((price - entry) / entry),
            bars_from_start=t - seg.start,
            loss_at_line_pct=float((min(hit_levels) - entry) / entry),
        )
    return None


def run_calibration(
    data: pd.DataFrame,
    strategy,
    n_splits: int,
    atr_ns: Sequence[float],
    atr_windows: Sequence[int],
    fixed_pct: float = _DEFAULT_FIXED_PCT,
    trigger_source: str = "close",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """對單一時間窗跑機制層全網格量測。

    與既有 runner 慣例一致：訊號與各 window 的 ATR 都在全窗計算
    （暖機用窗內歷史），再按 TimeSeriesSplit 的 test fold 切片；
    fold 內 shift(1) 重新落地（首根必空手），與 overlay walk-forward 相同。

    Args:
        data: canonical schema（需含 ts / high / low / close）。
        strategy: 任何有 generate_signals(data) 的策略物件。
        n_splits: TimeSeriesSplit fold 數。
        atr_ns: N 倍數網格。
        atr_windows: ATR window 網格。
        fixed_pct: 固定安全網（本輪不校正，維持 0.15）。
        trigger_source: "close"（預設，行為逐位不變）或 "high"（盤中觸價
            敏感度模式；segments_df 加註 max_atr_mult_high_w{w} 與
            max_adverse_pct_high 盤中安全邊際欄）。

    Returns:
        (segments_df, triggers_df)：
        segments_df 一列 = 一個 (fold, 區段)，含最大不利波幅（% 與各 window
        的 ATR 倍數 max_atr_mult_w{w}）、反事實持有報酬。
        triggers_df 一列 = 一個 (fold, 區段, N, window) 組合的觸發結果，
        含哪條線先觸發、觸發當下浮虧、誤殺/保護分類。
    """
    from sklearn.model_selection import TimeSeriesSplit

    from indicators.technical import atr as compute_atr

    if n_splits < 2:
        raise ValueError("n_splits 至少為 2")
    if trigger_source not in ("close", "high"):
        raise ValueError(f"trigger_source 只允許 'close'/'high'，收到: {trigger_source!r}")

    indexed = data.reset_index(drop=True)
    full_signal = strategy.generate_signals(indexed)
    full_close = indexed["close"].astype(float)
    full_high = indexed["high"].astype(float)
    ts_arr = indexed["ts"]

    ohlc = indexed[["high", "low", "close"]]
    full_atrs = {w: compute_atr(ohlc, window=w) for w in atr_windows}

    seg_rows: list[dict] = []
    trig_rows: list[dict] = []
    splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(indexed)))

    for fold, (_, test_idx) in enumerate(splits, start=1):
        sig = full_signal.iloc[test_idx].reset_index(drop=True)
        close = full_close.iloc[test_idx].reset_index(drop=True)
        high = full_high.iloc[test_idx].reset_index(drop=True)
        ts_fold = ts_arr.iloc[test_idx].reset_index(drop=True)
        trig_price = high if trigger_source == "high" else None
        atrs = {
            w: full_atrs[w].iloc[test_idx].reset_index(drop=True)
            for w in atr_windows
        }

        for seg_id, seg in enumerate(extract_short_segments(sig, close), start=1):
            seg_row: dict = {
                "fold": fold,
                "seg_id": seg_id,
                "start_ts": ts_fold.iloc[seg.start],
                "end_ts": ts_fold.iloc[seg.end - 1],
                "n_bars": seg.end - seg.start,
                "entry_price": seg.entry_price,
            }
            held_ret: float | None = None
            for w in atr_windows:
                stats = segment_adverse_stats(seg, close, atrs[w])
                seg_row[f"max_atr_mult_w{w}"] = stats.max_atr_mult
                # % 波幅與持有報酬與 window 無關，取一次即可
                seg_row["max_adverse_pct"] = stats.max_adverse_pct
                held_ret = stats.held_to_end_return
            seg_row["held_to_end_return"] = held_ret
            if trigger_source == "high":
                # 盤中安全邊際：high 距觸發線最近幾倍 ATR（held 欄不適用、忽略）
                for w in atr_windows:
                    hi_stats = segment_adverse_stats(seg, high, atrs[w])
                    seg_row[f"max_atr_mult_high_w{w}"] = hi_stats.max_atr_mult
                    seg_row["max_adverse_pct_high"] = hi_stats.max_adverse_pct
            seg_rows.append(seg_row)

            for w in atr_windows:
                for n_val in atr_ns:
                    out = detect_trigger(
                        seg, close, atrs[w], n_val, fixed_pct,
                        trigger_price=trig_price,
                    )
                    triggered = out is not None
                    trig_rows.append(
                        {
                            "fold": fold,
                            "seg_id": seg_id,
                            "atr_n": float(n_val),
                            "atr_window": int(w),
                            "triggered": triggered,
                            "line": out.line if triggered else "",
                            "bars_to_trigger": out.bars_from_start if triggered else -1,
                            "loss_at_trigger_pct": (
                                out.loss_at_trigger_pct if triggered else float("nan")
                            ),
                            "loss_at_line_pct": (
                                out.loss_at_line_pct if triggered else float("nan")
                            ),
                            "false_kill": bool(triggered and held_ret > 0),
                            "protect": bool(triggered and held_ret <= 0),
                        }
                    )

    return pd.DataFrame(seg_rows), pd.DataFrame(trig_rows)


def stratified_forced_liq_n(
    train_ohlc: pd.DataFrame,
    target_extreme_pct: float = 0.12,
    n_min: float = 1.5,
    n_max: float = 5.0,
    atr_window: int = 14,
    lookback: int = 252,
    min_valid: int = 30,
) -> float:
    """ATR 分層 N：從 train 側資料的 trailing ATR/price 分布自我校準 N 倍數。

    背景（台股 regime/ATR 校正輪 2026-07-16 的 P1 發現）：單一 N=3 對大型
    權值股設計意圖完好，對 TPEx 小型股動態線長期坐在 15% 固定網之上、兩層
    防線角色反轉。分層依據 = 股票自身波動特性（非市值/交易所別等武斷分類）：

        N = clamp( target_extreme_pct / q95(ATR/close 於 trailing lookback 段),
                   n_min, n_max )

    ★錨定統計量 = trailing q95（2026-07-16 同日修正，第一版用 median 於 9%
    經 P4 診斷 FAIL 後使用者拍板改此版）★：median 錨定回答的是「平常日子
    線在哪」，但這個機制要守的是「極端日子線會不會跑到 15% 固定網之上」——
    統計量與問題目標錯位（與 RSI/regime 時間錯位同類教訓）。q95 錨定於
    target_extreme_pct（預設 12%，保守端、留固定網餘裕）把「連 95 分位
    波動日的線都留在網下」直接寫進公式。已知結構性限制：任何 trailing
    校正都無法預見 train 段之後才發生的劇烈 regime 突變（如 2603 fold9
    q95 位移 4.45 倍的案例），殘留風險記錄不消除，見 HANDOFF「台股放空
    第一輪」節。

    因果紀律：呼叫端必須只傳 train 側資料（如 TimeSeriesSplit 的訓練段），
    測試窗資料零參與；本函式只取傳入資料的**最後 lookback 根**計算。

    Args:
        train_ohlc: 需含 high/low/close 欄位（train 側切片）。
        target_extreme_pct: 動態線的 trailing q95 目標水位（佔價格比例）。
        n_min / n_max: N 的上下夾限（防極端值）。
        atr_window: ATR 視窗（與 risk/manager.py 預設 14 同步）。
        lookback: 只用最後這麼多根的 ATR/close 分布（預設 252 ≈ 一年交易日）。
        min_valid: 有效 ATR/close 值的最低數量，不足拋錯（不靜默給預設值）。

    Returns:
        該股票（該 fold）的 forced_liq_atr_n 建議值，供 RiskConfig 注入。
    """
    if target_extreme_pct <= 0:
        raise ValueError("target_extreme_pct 必須為正")
    if not 0 < n_min <= n_max:
        raise ValueError("需滿足 0 < n_min <= n_max")
    missing = [c for c in ("high", "low", "close") if c not in train_ohlc.columns]
    if missing:
        raise ValueError(f"train_ohlc 缺少欄位: {missing}")

    from indicators.technical import atr as compute_atr

    tail = train_ohlc[["high", "low", "close"]].reset_index(drop=True).iloc[-lookback:]
    atr_series = compute_atr(tail.reset_index(drop=True), window=atr_window)
    ratio = (atr_series / tail["close"].reset_index(drop=True)).dropna()
    ratio = ratio[ratio > 0]
    if len(ratio) < min_valid:
        raise ValueError(
            f"有效 ATR/close 樣本不足（{len(ratio)} < {min_valid}），無法校準分層 N"
        )
    raw_n = target_extreme_pct / float(ratio.quantile(0.95))
    return float(min(max(raw_n, n_min), n_max))


def summarize_grid(triggers_df: pd.DataFrame) -> pd.DataFrame:
    """把 triggers_df 彙總成每組合一列的權衡表。

    欄位含觸發防線分工（atr/fixed/both）、誤殺/保護計數，以及
    「觸發當下未實現虧損」分布（min/median/max）——後者是校正紀律
    要求的新增欄：不能只用事後全知的誤殺判準，需同時呈現觸發當下
    面對多大浮虧。
    """
    rows: list[dict] = []
    for (n_val, w), sub in triggers_df.groupby(["atr_n", "atr_window"], sort=True):
        trig = sub[sub["triggered"]]
        losses = trig["loss_at_trigger_pct"]
        rows.append(
            {
                "atr_n": float(n_val),
                "atr_window": int(w),
                "n_segments": len(sub),
                "n_triggered": int(sub["triggered"].sum()),
                "n_false_kill": int(sub["false_kill"].sum()),
                "n_protect": int(sub["protect"].sum()),
                "n_line_atr": int((trig["line"] == "atr").sum()),
                "n_line_fixed": int((trig["line"] == "fixed").sum()),
                "n_line_both": int((trig["line"] == "both").sum()),
                "loss_at_trigger_min": float(losses.min()) if len(losses) else float("nan"),
                "loss_at_trigger_med": float(losses.median()) if len(losses) else float("nan"),
                "loss_at_trigger_max": float(losses.max()) if len(losses) else float("nan"),
            }
        )
    return pd.DataFrame(rows)
