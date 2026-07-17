"""short_risk_multiplier 校正跑批（2026-07-14 立項，網格 4 點使用者定案）。

任務規格（使用者 2026-07-14 確認）：
  - 校正對象：RiskConfig.short_risk_multiplier（現值 0.5）。只在
    sizing_mode="risk_per_trade"（r1 路徑）生效，正式基準 leverage_cap
    不受影響；規則二 max_short_leverage 的 0.5x 是另一個獨立參數，本輪凍結。
  - 網格：m ∈ {0.25, 0.5, 0.75, 1.0}（RiskConfig 驗證強制 m ≤ 1；
    1.0 = 「空頭風險預算不打折」參照點，直接檢驗折減前提本身）。
  - 兩層流程沿用 ATR 校正輪：機制層純量測 + 結果層 E2_r1 對照；
    E2_cap 數字列旁邊當定錨參照，不參與校正比較（對照軸 = r1(m) vs r1(0.5)）。
  - 判準：預設維持 0.5；改動需 (a) 三窗方向一致且乾淨基準不反例、
    (b) 風險端指標有實質差異（不能只是報酬端的線性放大）、(c) 量級對
    fold std 站得住。風險端與報酬端分開呈現，避免報酬排序干擾風險判讀。

事前預期（可證偽，報告須逐條對照實測）：
  P1 觸發 bar 集合跨 m 不變（check_forced_liquidation 只看 quantity 正負號）。
  P2 未封頂強平的損失/預算比率跨 m 逐位不變（損失 ∝ m、預算 ∝ m 約掉）；
     封頂觸發的比率 < 未封頂（封頂壓低實際風險）。★P2 若不成立 = 有未知
     非線性，停下深究，不與其他結果混報★。
  P3 封頂生效率隨 m 上升（封頂 ⟺ 3×ATR/price < 2%×m）；未封頂段曝險 ∝ m。
  P4 報酬端 Δ(m−0.5) 逐窗方向 = 該窗空頭損益方向 × sign(m−0.5)，跨窗
     大概率不一致 → 誠實預期結論 = 維持 0.5。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_multiplier_calibration.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.report import build_report
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from run_atr_calibration import CLEAN_BASELINE, FEE_BPS, WINDOWS, _load, _v2_strategy
from run_rule1_sizing import _entry_stats, _liq_loss_details, _short_exposure

GRID_M = (0.25, 0.5, 0.75, 1.0)
BASE_M = 0.5
RISK_PER_TRADE = 0.01  # 多頭每筆風險比例（RiskConfig 預設，本輪不動）


def _collect_window(name: str, start: str, end: str, n_splits: int) -> dict:
    """對一個時間窗收集 E2_cap 定錨 + 全網格 E2_r1 的逐 fold 量測。"""
    data = _load(start, end)
    strat = _v2_strategy()
    full_signal = strat.generate_signals(data)
    full_atr = compute_atr(data[["high", "low", "close"]], window=14)
    splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))

    folds: list[dict] = []
    for k, (_, test_idx) in enumerate(splits, start=1):
        td = data.iloc[test_idx].reset_index(drop=True)
        sig = full_signal.iloc[test_idx].reset_index(drop=True)
        atr_s = full_atr.iloc[test_idx].reset_index(drop=True)
        open_arr = td["open"].to_numpy(dtype=float)
        ts_arr = list(td["ts"])

        def _run(mode: str, m: float) -> EventBacktestResult:
            # slippage_bps=0.0 pin：保留本輪（2026-07-14 結案）數字逐位可
            # 重現性，不隨新預設值（2.0，2026-07-15 基準切換）變動。
            return run_event_backtest(
                td, sig, fee_bps=FEE_BPS, fill_mode="next_open", slippage_bps=0.0,
                risk=RiskManager(
                    equity=1.0, config=RiskConfig(short_risk_multiplier=m)
                ),
                atr_series=atr_s, sizing_mode=mode,
            )

        ev_cap = _run("leverage_cap", BASE_M)
        a_cap = build_report(ev_cap.to_backtest_result(td["close"])).annual_return

        per_m: dict[float, dict] = {}
        for m in GRID_M:
            ev = _run("risk_per_trade", m)
            budget = RISK_PER_TRADE * m
            n_ent, n_capped, n_fb = _entry_stats(ev, open_arr, budget=budget)
            per_m[m] = {
                "annual": build_report(ev.to_backtest_result(td["close"])).annual_return,
                "liq_bars": list(ev.liquidation_bars),
                "n_entries": n_ent,
                "n_capped": n_capped,
                "n_fallback": n_fb,
                "expo": _short_exposure(ev, td["close"]),
                "details": _liq_loss_details(ev, ts_arr, budget=budget),
            }
        folds.append({"k": k, "a_cap": a_cap, "per_m": per_m})
    return {"name": name, "range": f"{start} → {end}", "n_splits": n_splits,
            "folds": folds}


def _section_mechanism(results: list[dict]) -> None:
    print("=" * 100)
    print("(1) 機制層純量測（每窗 × m：進場/封頂/fallback/空頭曝險/強平數）")
    print("    封頂 ⟺ 3×ATR/price < 2%×m（規則二 0.5x 安全繩生效，m 越大越常介入）")
    print("=" * 100)
    for res in results:
        print(f"  [{res['name']}]（{res['n_splits']}-fold）")
        print(f"    {'m':>5} | {'進場':>4} {'封頂':>4} {'封頂率':>6} {'補':>2} | "
              f"{'空曝險(fold均)':>12} | {'強平':>4}")
        for m in GRID_M:
            n_ent = sum(f["per_m"][m]["n_entries"] for f in res["folds"])
            n_cap = sum(f["per_m"][m]["n_capped"] for f in res["folds"])
            n_fb = sum(f["per_m"][m]["n_fallback"] for f in res["folds"])
            n_liq = sum(len(f["per_m"][m]["liq_bars"]) for f in res["folds"])
            expos = [f["per_m"][m]["expo"] for f in res["folds"]]
            expo = float(np.nanmean(expos)) if not all(np.isnan(expos)) else float("nan")
            cur = "←現值" if m == BASE_M else ""
            print(f"    {m:>5.2f} | {n_ent:>4d} {n_cap:>4d} "
                  f"{(n_cap / n_ent * 100 if n_ent else 0):>5.1f}% {n_fb:>2d} | "
                  f"{expo:>12.3f} | {n_liq:>4d}  {cur}")
        print()


def _section_predictions(results: list[dict]) -> None:
    print("=" * 100)
    print("(2) ★ 事前預期檢核（P1/P2 為本輪最重要判讀；P2 不成立 = 停下深究的信號）★")
    print("=" * 100)

    # P1：觸發 bar 集合跨 m 不變
    p1_ok = True
    for res in results:
        bad = [
            f["k"] for f in res["folds"]
            if any(f["per_m"][m]["liq_bars"] != f["per_m"][BASE_M]["liq_bars"]
                   for m in GRID_M)
        ]
        status = "✓ 全 fold 一致" if not bad else f"✗ 不一致 fold: {bad}"
        p1_ok &= not bad
        print(f"  P1 觸發bar跨m不變 | {res['name']:>6}: {status}")
    print(f"  → P1 {'成立' if p1_ok else '★不成立（需深究）★'}")
    print()

    # P2：未封頂比率跨 m 不變；封頂事件另列
    p2_ok = True
    any_cap_event = False
    for res in results:
        max_dev = 0.0
        n_pairs = 0
        cap_lines: list[str] = []
        for f in res["folds"]:
            base_details = f["per_m"][BASE_M]["details"]
            for j in range(len(base_details)):
                r0, c0 = base_details[j]
                for m in GRID_M:
                    if m == BASE_M:
                        continue
                    r, c = f["per_m"][m]["details"][j]
                    if not c0 and not c:
                        n_pairs += 1
                        max_dev = max(max_dev, abs(r - r0))
                    else:
                        any_cap_event = True
                        cap_lines.append(
                            f"      fold{f['k']} 事件{j + 1} m={m:.2f}: "
                            f"比率 {r:.2f}x vs 現值 {r0:.2f}x"
                            f"（封頂: m={'是' if c else '否'}/0.5={'是' if c0 else '否'}）"
                        )
        ok = max_dev < 1e-9
        p2_ok &= ok
        print(f"  P2 未封頂比率不變 | {res['name']:>6}: 比較對 {n_pairs:>3d} 筆, "
              f"最大偏差 {max_dev:.2e} → {'✓' if ok else '★✗ 非線性！★'}")
        for line in cap_lines:
            print(line)
    print(f"  → P2 {'成立（未封頂域零非線性）' if p2_ok else '★不成立——優先深究，勿與其他結果混讀★'}"
          + ("" if any_cap_event else "；本資料域無封頂觸發事件"))
    print()


def _section_returns(results: list[dict]) -> None:
    print("=" * 100)
    print("(3) 結果層——報酬端（E2_cap 為定錨參照，不參與校正比較；對照軸 = r1(m) vs r1(0.5)）")
    print("=" * 100)
    for res in results:
        print(f"  [{res['name']}] {res['range']}")
        print(f"    {'fold':>4} | {'cap定錨':>8} | "
              + " ".join(f"{'r1@' + format(m, '.2f'):>9}" for m in GRID_M))
        for f in res["folds"]:
            print(f"    {f['k']:>4} | {f['a_cap']:>+8.1%} | "
                  + " ".join(f"{f['per_m'][m]['annual']:>+9.1%}" for m in GRID_M))
        print(f"    {'mean':>4} | {np.mean([f['a_cap'] for f in res['folds']]):>+8.1%} | "
              + " ".join(
                  f"{np.mean([f['per_m'][m]['annual'] for f in res['folds']]):>+9.1%}"
                  for m in GRID_M))
        print(f"    {'std':>4} | {np.std([f['a_cap'] for f in res['folds']], ddof=1):>8.1%} | "
              + " ".join(
                  f"{np.std([f['per_m'][m]['annual'] for f in res['folds']], ddof=1):>9.1%}"
                  for m in GRID_M))
        base = np.array([f["per_m"][BASE_M]["annual"] for f in res["folds"]])
        deltas = " ".join(
            f"{np.mean(np.array([f['per_m'][m]['annual'] for f in res['folds']]) - base):>+9.1%}"
            if m != BASE_M else f"{'—':>9}"
            for m in GRID_M
        )
        print(f"    Δmean(m−0.5)     | {deltas}")
        print()


def _section_risk(results: list[dict]) -> None:
    print("=" * 100)
    print("(4) 結果層——風險端（與報酬端分開判讀：損失/預算分布 + 逐fold逐筆超越）")
    print("=" * 100)
    for res in results:
        print(f"  [{res['name']}]")
        print(f"    {'m':>5} | {'強平':>4} | {'比率min':>7} {'med':>6} {'max':>6} | "
              f"{'>1x':>4} {'>2x':>4}")
        for m in GRID_M:
            ratios = [r for f in res["folds"] for r, _ in f["per_m"][m]["details"]]
            arr = np.array(ratios) if ratios else np.array([np.nan])
            cur = "←現值" if m == BASE_M else ""
            print(f"    {m:>5.2f} | {len(ratios):>4d} | "
                  f"{np.nanmin(arr):>6.2f}x {np.nanmedian(arr):>5.2f}x "
                  f"{np.nanmax(arr):>5.2f}x | "
                  f"{int((arr > 1.0).sum()):>4d} {int((arr > 2.0).sum()):>4d}  {cur}")
        # 逐 fold 逐筆（僅列現值與 1.0 兩端，避免噪音；封頂事件加 [封] 標記）
        for m in (BASE_M, 1.0):
            lines = []
            for f in res["folds"]:
                det = f["per_m"][m]["details"]
                if det:
                    lines.append(
                        f"f{f['k']}:" + " ".join(
                            f"{r:.2f}x{'[封]' if c else ''}" for r, c in det
                        )
                    )
            if lines:
                print(f"    逐fold逐筆 m={m:.2f}: " + "  ".join(lines))
        print()


def _section_fold4(results: list[dict]) -> None:
    clean = next(r for r in results if r["name"] == "乾淨基準")
    f4 = next(f for f in clean["folds"] if f["k"] == 4)
    print("=" * 100)
    print("(5) 乾淨基準 fold4 聚焦（已知本質權衡案例：cap +27.5% vs r1@0.5 -1.7%，"
          "參與度 vs 固定預算）")
    print("=" * 100)
    print(f"    cap 定錨: {f4['a_cap']:>+7.1%}（強平 0 次）")
    for m in GRID_M:
        pm = f4["per_m"][m]
        cur = "←現值" if m == BASE_M else ""
        print(f"    r1@{m:.2f}: 年化 {pm['annual']:>+7.1%}  空曝險 {pm['expo']:.3f}x  "
              f"強平 {len(pm['liq_bars'])} 次  {cur}")
    print("    （預期：曝險與年化隨 m 近似線性回向 cap——多冒險多參與的直接兌換，"
          "非免費改善）")
    print()


def main() -> None:
    print("=" * 100)
    print("★ short_risk_multiplier 校正（r1 路徑限定；正式基準 leverage_cap 不受影響）★")
    print(f"  網格 m ∈ {GRID_M}（現值 0.5）；規則二 0.5x 封頂與 N=3/w=14 凍結")
    print("=" * 100)
    print()

    results: list[dict] = []
    start, end, n_splits = CLEAN_BASELINE
    results.append(_collect_window("乾淨基準", start, end, n_splits))
    for name, (start, end) in WINDOWS.items():
        results.append(_collect_window(f"窗{name}", start, end, 15))

    _section_mechanism(results)
    _section_predictions(results)
    _section_returns(results)
    _section_risk(results)
    _section_fold4(results)
    print("  （判決建議與事前預期對照表見交付報告；預設立場 = 維持 m=0.5。）")


if __name__ == "__main__":
    main()
