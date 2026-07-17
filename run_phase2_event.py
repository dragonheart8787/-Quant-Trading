"""Phase 2 事件驅動對照：向量 vs E0/E1/E2 完整跑批 + Δ成分歸因總表。

對照矩陣（2026-07-06 設計確認）：
  向量無風控  = walk_forward + vector_engine（shift(1) 再平衡近似，既有基準）
  E0 = event, 無風控, fill=signal_close —— 與向量唯一差異是「再平衡 vs 固定倉位」
  E1 = event, 無風控, fill=next_open   —— 再隔離「成交時點」單一效應
  E2 = event, 完整風控(RiskManager 預設), fill=next_open —— 正式數字

Δ成分歸因鏈（對每個 fold，v2/A2）：
  C1  = 向量 + 風控近似（short_risk_overlay，既有「風控近似」欄）
  C2  = 事件 + 僅強平（sizing 1.0x、signal_close）→ Δ倉位模型 = C2−C1
        （路徑依賴 + 觸發根損益歸屬 + 費用時點；同觸發定義，機制已驗證一致）
  C3a = 事件 + 完整風控但熔斷診斷性關閉（max_daily_loss_pct=0.99，僅此對照用，
        不動任何預設值）→ Δsizing規則二 = C3a−C2（空頭曝險 0.5x）
  C3  = 事件 + 完整風控預設（signal_close）→ Δ熔斷 = C3−C3a
  E2  = 同 C3 但 next_open → Δ成交時點 = E2−C3（1h 尺度應≈0）

解讀紀律（HANDOFF.md「Phase 2」節）：E2 與向量/近似版的一致性只看強平觸發
bar 集合（機制層）；最終報酬差異屬歸因問題，不是 bug 訊號。

執行（預設 = 乾淨基準窗；可用 N_SPLITS / KLINE_START / KLINE_END 覆寫）：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_phase2_event.py
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.grid_search import label_fold_regimes
from backtest.report import build_report
from backtest.short_risk_overlay import apply_short_forced_liquidation
from backtest.vector_engine import run_backtest
from backtest.walk_forward import walk_forward
from data.cleaning import validate_canonical
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from strategy.base import BaseStrategy
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOL, INTERVAL = "BTCUSDT", "1h"
FEE_BPS = 5.0
BASE_PARAMS = {"fast_window": 20, "slow_window": 60, "rsi_overbought": 70.0}

# 預設 = 乾淨基準窗（HANDOFF.md「落地資料乾淨基準」，可精確複驗）
N_SPLITS = int(os.environ.get("N_SPLITS", "5"))
KLINE_START = os.environ.get("KLINE_START", "2026-02-28 07:00")
KLINE_END = os.environ.get("KLINE_END", "2026-07-03 06:00")


def _load_window() -> pd.DataFrame:
    from data.storage_sqlite import connect, load_klines

    conn = connect(os.environ.get("KLINE_DB") or None)
    try:
        data = load_klines(
            conn, SYMBOL, INTERVAL,
            start=pd.Timestamp(KLINE_START, tz="UTC"),
            end=pd.Timestamp(KLINE_END, tz="UTC"),
        )
    finally:
        conn.close()
    if data.empty:
        raise RuntimeError("落地 DB 查無資料；請先跑 run_ingest.py")
    validate_canonical(data)
    return data.reset_index(drop=True)


def _annual(ev: EventBacktestResult, close: pd.Series) -> float:
    return build_report(ev.to_backtest_result(close)).annual_return


def main() -> None:
    data = _load_window()
    print(
        f"{SYMBOL} {INTERVAL}  {len(data)} 根  "
        f"{data['ts'].iloc[0]} -> {data['ts'].iloc[-1]}  n_splits={N_SPLITS}"
    )
    regimes = label_fold_regimes(data, n_splits=N_SPLITS)
    full_atr = compute_atr(data[["high", "low", "close"]], window=14)
    splits = list(TimeSeriesSplit(n_splits=N_SPLITS).split(np.arange(len(data))))

    strategies: list[tuple[str, BaseStrategy]] = [
        ("long-only", MaRsiStrategy(**BASE_PARAMS)),
        ("v2", MaRsiBidirectionalStrategy(**BASE_PARAMS, regime_window=120)),
        ("A2", MaRsiBidirectionalStrategy(
            **BASE_PARAMS, regime_window=120, long_regime_window=300)),
    ]

    results: dict[str, dict] = {}
    for name, strat in strategies:
        print(f"計算 {name}（向量 / E0 / E1 / E2 / 歸因鏈）...")
        full_signal = strat.generate_signals(data)
        vec_wf = walk_forward(data, strat, n_splits=N_SPLITS, fee_bps=FEE_BPS)
        r: dict = {
            "vector": [f.report.annual_return for f in vec_wf.folds],
            "E0": [], "E1": [], "E2": [],
            "C1": [], "C2": [], "C3a": [], "C3": [],
            "e2_liq": [], "e2_rej": [], "e2_trip": [],
        }
        for _, test_idx in splits:
            td = data.iloc[test_idx].reset_index(drop=True)
            sig = full_signal.iloc[test_idx].reset_index(drop=True)
            atr_s = full_atr.iloc[test_idx].reset_index(drop=True)
            close_s = td["close"]

            def _run(fill: str, risk: Optional[RiskManager] = None,
                     forced_liq: Optional[bool] = None) -> EventBacktestResult:
                # slippage_bps=0.0 pin：保留本輪（2026-07-06/07 結案）數字
                # 逐位可重現性，不隨新預設值（2.0，2026-07-15 基準切換）變動。
                return run_event_backtest(
                    td, sig, fee_bps=FEE_BPS, fill_mode=fill, slippage_bps=0.0,
                    risk=risk, forced_liquidation=forced_liq, atr_series=atr_s,
                )

            r["E0"].append(_annual(_run("signal_close", forced_liq=False), close_s))
            r["E1"].append(_annual(_run("next_open", forced_liq=False), close_s))
            e2 = _run("next_open", risk=RiskManager(equity=1.0))
            r["E2"].append(_annual(e2, close_s))
            r["e2_liq"].append(len(e2.liquidation_bars))
            r["e2_rej"].append(len(e2.rejections))
            r["e2_trip"].append(len(e2.tripped_days))

            # 歸因鏈（signal_close 對齊 overlay 進場價定義）
            adj = apply_short_forced_liquidation(sig, close_s, atr_s)
            r["C1"].append(build_report(run_backtest(
                pd.DataFrame({"close": close_s}), adj,
                fee_bps=FEE_BPS, shift_signal=False,
            )).annual_return)
            r["C2"].append(_annual(_run("signal_close", forced_liq=True), close_s))
            r["C3a"].append(_annual(_run(
                "signal_close",
                risk=RiskManager(equity=1.0, config=RiskConfig(max_daily_loss_pct=0.99)),
            ), close_s))
            r["C3"].append(_annual(_run(
                "signal_close", risk=RiskManager(equity=1.0)), close_s))
        results[name] = r

    # ===== Section 1：完整跑批 =====
    print()
    print("=" * 108)
    print("(1) 每 fold 年化：向量無風控 / E0(事件·訊號根收盤) / E1(事件·次根開盤) / E2(事件·次根開盤+完整風控)")
    print("=" * 108)
    for name, _ in strategies:
        r = results[name]
        hdr = (f"[{name}]  {'fold':>4} {'regime':>11}  {'向量':>8} {'E0':>8} "
               f"{'E1':>8} {'E2':>8} {'E2-向量':>8}  {'觸發':>4} {'拒單':>4} {'熔斷日':>6}")
        print(hdr)
        print("-" * len(hdr))
        for k in range(N_SPLITS):
            print(
                f"        {k + 1:>4} {regimes[k].regime.label:>11}  "
                f"{r['vector'][k]:>+8.1%} {r['E0'][k]:>+8.1%} "
                f"{r['E1'][k]:>+8.1%} {r['E2'][k]:>+8.1%} "
                f"{r['E2'][k] - r['vector'][k]:>+8.1%}  "
                f"{r['e2_liq'][k]:>4d} {r['e2_rej'][k]:>4d} {r['e2_trip'][k]:>6d}"
            )
        for col in ("vector", "E0", "E1", "E2"):
            vals = np.array(r[col])
            print(f"        {col:<7} mean={vals.mean():+.1%}  std={vals.std(ddof=1):.1%}")
        print()

    # ===== Section 2：Δ成分歸因總表（v2 / A2 全 fold）=====
    print("=" * 108)
    print("(2) ★ Δ成分歸因總表：C1(向量+近似) →Δ倉位模型→ C2 →Δsizing規則二→ C3a →Δ熔斷→ C3 →Δ時點→ E2 ★")
    print("    主成分 = |Δ| 最大者。Δ倉位模型含路徑依賴+觸發根歸屬+費用時點；Δsizing = 空頭曝險 0.5x。")
    print("=" * 108)
    for name in ("v2", "A2"):
        r = results[name]
        hdr = (f"[{name}]  {'fold':>4} {'regime':>11}  {'C1':>8}  {'Δ倉位':>8} "
               f"{'Δsizing':>8} {'Δ熔斷':>7} {'Δ時點':>7}  {'E2':>8} {'E2-C1':>8}  {'主成分':>7}")
        print(hdr)
        print("-" * len(hdr))
        abs_sums = {"倉位": 0.0, "sizing": 0.0, "熔斷": 0.0}
        for k in range(N_SPLITS):
            d_pos = r["C2"][k] - r["C1"][k]
            d_sz = r["C3a"][k] - r["C2"][k]
            d_cb = r["C3"][k] - r["C3a"][k]
            d_tm = r["E2"][k] - r["C3"][k]
            comps = {"倉位": d_pos, "sizing": d_sz, "熔斷": d_cb}
            for key, val in comps.items():
                abs_sums[key] += abs(val)
            main = (max(comps, key=lambda c: abs(comps[c]))
                    if max(abs(v) for v in comps.values()) > 1e-4 else "—")
            print(
                f"        {k + 1:>4} {regimes[k].regime.label:>11}  "
                f"{r['C1'][k]:>+8.1%}  {d_pos:>+8.1%} {d_sz:>+8.1%} "
                f"{d_cb:>+7.1%} {d_tm:>+7.1%}  {r['E2'][k]:>+8.1%} "
                f"{r['E2'][k] - r['C1'][k]:>+8.1%}  {main:>7}"
            )
        n = N_SPLITS
        print(f"        平均|Δ|:  倉位模型 {abs_sums['倉位']/n:.1%}   "
              f"sizing規則二 {abs_sums['sizing']/n:.1%}   熔斷 {abs_sums['熔斷']/n:.1%}")
        print()


if __name__ == "__main__":
    main()
