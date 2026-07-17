"""Δ規則一歸因跑批：E2 槓桿上限全倉 vs 規則一風險比例倉位（2026-07-13）。

比照 Δsizing 歸因先例：同 fill（next_open）、同風控（RiskManager 預設，
N=3/w=14 已定案）、同強平；唯一差異 = 空頭進場倉位公式：
  E2_cap = sizing_mode="leverage_cap"（現行基準，0.5x 全倉）
  E2_r1  = sizing_mode="risk_per_trade"（規則一：qty = E×0.5% / (3×ATR)，
           規則二 0.5x 上限仍為封頂安全繩；多頭不變 = 選項A）

逐 fold 診斷（使用者要求，不只彙總）：
  - Δ規則一 = annual(E2_r1) − annual(E2_cap)
  - 空頭平均名目曝險（vs 固定 0.5x）
  - 槓桿封頂次數（規則一與規則二的分工頻率；capped ⟺ 3×ATR/price < 1%）
  - ★每筆強平的實際損失 / 0.5% 預算（逐 fold 逐筆列出——彙總平均會掩蓋
    個別 fold 超越幅度特別大的市況，比照 2603 fold3 被彙總掩蓋的教訓★

範圍：乾淨基準 5-fold + 三零重疊窗 C/D/A × 15-fold（皆有現成 E2_cap 基準）。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_rule1_sizing.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.report import build_report
from broker.paper import Side
from indicators.technical import atr as compute_atr
from risk.manager import RiskManager
from run_atr_calibration import CLEAN_BASELINE, FEE_BPS, WINDOWS, _load, _v2_strategy

RISK_BUDGET = 0.005  # risk_per_trade_short = 0.01 × 0.5（預設）
# 2026-07-14 multiplier 校正輪：_entry_stats / _liq_loss_ratios 的預算改為參數
# （預設 = RISK_BUDGET，本檔輸出逐位不變），供 run_multiplier_calibration.py
# 以 budget = 0.01×m 重用；新增 _liq_loss_details 回報逐筆 (比率, 是否封頂)。


def _short_exposure(ev: EventBacktestResult, close: pd.Series) -> float:
    """空頭持倉 bar 的平均名目曝險（|qty|×close / 當根權益）；無空頭 bar 回 NaN。"""
    qty = ev.position_qty.to_numpy()
    mask = qty < 0
    if not mask.any():
        return float("nan")
    notional = np.abs(qty[mask]) * close.to_numpy()[mask]
    return float((notional / ev.equity_curve.to_numpy()[mask]).mean())


def _entry_stats(
    ev: EventBacktestResult, open_arr: np.ndarray, budget: float = RISK_BUDGET
) -> tuple[int, int, int]:
    """(空頭進場單數, 規則二封頂數, NaN fallback 數)。

    封頂判定與權益無關：uncapped 名目比例 = budget×price/D > 0.5 ⟺
    D/price < 2×budget（budget=0.5% 時即 D/price < 1%）。
    """
    n_entries = n_capped = n_fallback = 0
    for o in ev.orders:
        if o.reason != "entry_short":
            continue
        n_entries += 1
        if o.stop_distance is None:
            n_fallback += 1
            continue
        fill_idx = o.created_index + 1  # next_open 成交
        if fill_idx < len(open_arr) and budget * open_arr[fill_idx] / o.stop_distance > 0.5:
            n_capped += 1
    return n_entries, n_capped, n_fallback


def _liq_loss_details(
    ev: EventBacktestResult, ts_arr: list, budget: float = RISK_BUDGET
) -> list[tuple[float, bool]]:
    """每筆強平的 (實際權益損失/預算, 進場是否被規則二封頂)。

    走訪 fills 重建空頭進出：進場 SELL（空手→空）記 p_in/q/進場權益基準，
    平倉 BUY 若時間戳屬強平單的成交時點 → ratio = q×(p_out−p_in) / (budget×E)。
    封頂判定與 _entry_stats 同式（budget×fill_price/D > 0.5），用進場單的
    stop_distance；leverage_cap 模式（stop_distance=None）一律記未封頂。
    """
    n = len(ts_arr)
    liq_fill_ts = set()
    stop_by_fill_ts: dict = {}
    for o in ev.orders:
        if o.created_index + 1 >= n:
            continue
        fill_ts = ts_arr[o.created_index + 1]  # next_open 成交時點
        if o.reason == "forced_liquidation":
            liq_fill_ts.add(fill_ts)
        elif o.reason == "entry_short":
            stop_by_fill_ts[fill_ts] = o.stop_distance

    ts_to_idx = {ts: i for i, ts in enumerate(ts_arr)}
    eq = ev.equity_curve.to_numpy()

    details: list[tuple[float, bool]] = []
    pos = 0.0
    p_in = e_base = float("nan")
    capped = False
    for f in ev.fills:
        if f.side is Side.SELL and pos == 0.0:  # 進空
            p_in = f.price
            idx = ts_to_idx.get(f.ts, 0)
            e_base = eq[idx - 1] if idx > 0 else ev.initial_equity
            stop = stop_by_fill_ts.get(f.ts)
            capped = bool(stop is not None and budget * f.price / stop > 0.5)
            pos = -f.quantity
        elif f.side is Side.BUY and pos < 0:  # 平空
            if f.ts in liq_fill_ts:
                loss = (-pos) * (f.price - p_in)
                details.append((loss / (budget * e_base), capped))
            pos = 0.0
        elif f.side is Side.SELL and pos > 0:
            pos = 0.0  # 平多
        elif f.side is Side.BUY and pos == 0.0:
            pos = f.quantity  # 進多
    return details


def _liq_loss_ratios(
    ev: EventBacktestResult, ts_arr: list, budget: float = RISK_BUDGET
) -> list[float]:
    """每筆強平的實際權益損失 / 預算（_liq_loss_details 的比率投影）。"""
    return [r for r, _ in _liq_loss_details(ev, ts_arr, budget)]


def _run_window(name: str, start: str, end: str, n_splits: int) -> None:
    data = _load(start, end)
    strat = _v2_strategy()
    full_signal = strat.generate_signals(data)
    full_atr = compute_atr(data[["high", "low", "close"]], window=14)
    splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))

    print(f"  [{name}] {start} → {end}（{len(data)} 根，{n_splits}-fold）")
    hdr = (f"    {'fold':>4} | {'E2_cap':>8} {'E2_r1':>8} {'Δ規則一':>8} | "
           f"{'強平c/r1':>8} | {'空曝險':>6} {'進場':>4} {'封頂':>4} {'補':>2} | 強平損失/預算(逐筆)")
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))

    caps, r1s = [], []
    for k, (_, test_idx) in enumerate(splits, start=1):
        td = data.iloc[test_idx].reset_index(drop=True)
        sig = full_signal.iloc[test_idx].reset_index(drop=True)
        atr_s = full_atr.iloc[test_idx].reset_index(drop=True)
        open_arr = td["open"].to_numpy(dtype=float)
        ts_arr = list(td["ts"])

        def _run(mode: str) -> EventBacktestResult:
            # slippage_bps=0.0 pin：保留本輪（2026-07-13 結案）數字逐位可
            # 重現性，不隨新預設值（2.0，2026-07-15 基準切換）變動。
            return run_event_backtest(
                td, sig, fee_bps=FEE_BPS, fill_mode="next_open", slippage_bps=0.0,
                risk=RiskManager(equity=1.0), atr_series=atr_s, sizing_mode=mode,
            )

        ev_cap = _run("leverage_cap")
        ev_r1 = _run("risk_per_trade")
        a_cap = build_report(ev_cap.to_backtest_result(td["close"])).annual_return
        a_r1 = build_report(ev_r1.to_backtest_result(td["close"])).annual_return
        caps.append(a_cap)
        r1s.append(a_r1)

        expo = _short_exposure(ev_r1, td["close"])
        n_ent, n_cap, n_fb = _entry_stats(ev_r1, open_arr)
        ratios = _liq_loss_ratios(ev_r1, ts_arr)
        ratio_str = " ".join(f"{r:.2f}x" for r in ratios) if ratios else "—"
        print(
            f"    {k:>4} | {a_cap:>+8.1%} {a_r1:>+8.1%} {a_r1 - a_cap:>+8.1%} | "
            f"{len(ev_cap.liquidation_bars):>3d}/{len(ev_r1.liquidation_bars):>3d}  | "
            f"{expo:>6.2f} {n_ent:>4d} {n_cap:>4d} {n_fb:>2d} | {ratio_str}"
        )

    cap_a, r1_a = np.array(caps), np.array(r1s)
    d = r1_a - cap_a
    print(
        f"    mean: cap {cap_a.mean():+.1%} / r1 {r1_a.mean():+.1%}   "
        f"std: cap {cap_a.std(ddof=1):.1%} / r1 {r1_a.std(ddof=1):.1%}   "
        f"Δ mean {d.mean():+.1%}  平均|Δ| {np.abs(d).mean():.1%}"
    )
    print()


def main() -> None:
    print("=" * 110)
    print("★ Δ規則一歸因：E2 槓桿上限全倉（cap）vs 風險比例倉位（r1）；"
          "空曝險=空頭bar平均名目/權益（cap 模式恆 0.5）★")
    print("  封頂 ⟺ 3×ATR/price < 1%（規則二安全繩生效）；補 = ATR NaN fallback 次數")
    print("=" * 110)
    start, end, n_splits = CLEAN_BASELINE
    _run_window("乾淨基準", start, end, n_splits)
    for name, (start, end) in WINDOWS.items():
        _run_window(f"窗{name}", start, end, 15)


if __name__ == "__main__":
    main()
