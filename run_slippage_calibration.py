"""滑價敏感度壓力測試（2026-07-15 立項）：E2「零滑價」簡化假設的壓力測試。

任務定位（比照 high 觸價敏感度輪的紀律，與 ATR/multiplier 校正輪的「選候選」
判讀邏輯不同）：這不是找「最優」滑價值——市價單滑價方向沒有懸念（恆不利）。
問題是「零滑價這個簡化假設，有沒有系統性低估交易成本、進而高估報酬」，
只有「增量有限（接受簡化）」與「增量顯著或核心案例質變（另開基準討論）」
兩個出口，沒有「選哪個候選」的模糊空間。

★ event_engine.py 範圍性解鎖（2026-07-15）★：slippage_bps 新增前只存在於
docstring 註解（「滑價目前為 0，敏感度另開任務」），並非已預留的實際參數
——HANDOFF.md 舊版「slippage_bps 已預留」用詞不準確，已於本輪更正。新增
`slippage_bps: float = 0.0`，只調整 `_execute()` 送進 broker 的實際成交價
（BUY 買貴、SELL 賣賤；強平單同樣套用不豁免），不觸碰 close/open 本身
（訊號生成／mark-to-market／強平觸發判定／sizing 基準價全部用未滑價原始
價格），預設值逐位不變（byte-diff 回歸見 tests/test_event_engine_slippage.py）。

滑價網格 {0, 2, 5, 10, 20} bps：0=基準；2=BTCUSDT 正常流動性市價單量級；
5=與現行 fee_bps=5.0 同量級（「多付一次手續費」的直覺對照點）；10=高波動/
偏大單相對簿深的壓力情境（空頭觸發與強平集中的正是這類 bar）；20=極端流動性
驟降尾端情境，用來看斷裂點。只鎖定加密（BTCUSDT）；台股尚未走進 E2 事件驅動
管線，不外推。

變因控制：sizing_mode="leverage_cap"（現行正式基準）、ATR N=3.0/window=14
（已定案不動）、固定網 15%（不動）、trigger_source 維持 close-only（不動）、
fee_bps=5.0（滑價是疊加成本非替代手續費）、fill_mode="next_open"（現行正式）、
訊號固定用 v2（Phase 4 已結案 v2 續任，不用 ML）。三窗 C/D/A × 15-fold ＋
乾淨基準 5-fold（乾淨基準與窗A重疊，只作定錨展示，不入獨立判準——同 Phase 4/
ATR 輪慣例）。

判讀維度（雙層，比照 high 觸價輪）：
  量級：Δ(bps) = annual(E2_slip) − annual(E2_base) 逐 fold 配對，彙總
        mean/std/SE；事前門檻 = Δmean 相對 SE 的比值作深入檢驗觸發線。
  強度：① fold4 核心案例（規則一輪錨點，cap 路徑現值 +27.5%）逐 bps 追蹤是否
        仍維持獲利；② 全部窗逐 fold 掃「獲利→虧損」翻轉數；③★換手拆解
        （固定呈現，不因結論是「增量有限」而省略——比照 Δsizing規則二教訓，
        彙總數字影響有限底下可能藏著集中在少數 fold 的劇烈侵蝕，此拆解正是
        用來防範彙總平均掩蓋掉這種情況）★：侵蝕是否與成交筆數相關、集中在
        高換手 fold 還是普遍分布。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_slippage_calibration.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.report import build_report
from indicators.technical import atr as compute_atr
from risk.manager import RiskManager
from run_atr_calibration import CLEAN_BASELINE, FEE_BPS, WINDOWS, _load, _v2_strategy

SLIP_GRID: tuple[float, ...] = (0.0, 2.0, 5.0, 10.0, 20.0)
FOLD4_LABEL = "fold4 核心案例（規則一輪錨點，cap 路徑現值 +27.5%）"
SIG_THRESHOLD_SE = 1.0  # Δmean 相對 SE 的比值 ≥ 此值 → 觸發「深入檢驗」標記


def _run_window(name: str, start: str, end: str, n_splits: int) -> pd.DataFrame:
    """回傳 long-format DataFrame：columns = fold, bps, annual, n_fills。"""
    data = _load(start, end)
    strat = _v2_strategy()
    full_signal = strat.generate_signals(data)
    full_atr = compute_atr(data[["high", "low", "close"]], window=14)
    splits = list(TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data))))

    print(f"  [{name}] {start} → {end}（{len(data)} 根，{n_splits}-fold）")
    hdr = f"    {'fold':>4} | " + " | ".join(f"{b:>6.0f}bp" for b in SLIP_GRID) + "   成交@0bp"
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))

    rows = []
    for k, (_, test_idx) in enumerate(splits, start=1):
        td = data.iloc[test_idx].reset_index(drop=True)
        sig = full_signal.iloc[test_idx].reset_index(drop=True)
        atr_s = full_atr.iloc[test_idx].reset_index(drop=True)

        annuals: list[float] = []
        n_fills_base = 0
        for bps in SLIP_GRID:
            ev: EventBacktestResult = run_event_backtest(
                td, sig, fee_bps=FEE_BPS, fill_mode="next_open",
                risk=RiskManager(equity=1.0), atr_series=atr_s,
                sizing_mode="leverage_cap", slippage_bps=bps,
            )
            annual = build_report(ev.to_backtest_result(td["close"])).annual_return
            annuals.append(annual)
            rows.append({"fold": k, "bps": bps, "annual": annual, "n_fills": len(ev.fills)})
            if bps == 0.0:
                n_fills_base = len(ev.fills)

        print(
            f"    {k:>4} | " + " | ".join(f"{a:>+7.1%}" for a in annuals)
            + f"   {n_fills_base:>4d}"
        )

    print()
    return pd.DataFrame(rows)


def _summarize(name: str, df: pd.DataFrame) -> None:
    """量級：逐 bps 的 Δmean/std/SE vs bps=0，事前門檻標注。"""
    base = df[df["bps"] == 0.0].set_index("fold")["annual"]
    print(f"  ── [{name}] 量級：Δ(bps) = annual(bps) − annual(0)（逐 fold 配對）──")
    for bps in SLIP_GRID:
        if bps == 0.0:
            continue
        cur = df[df["bps"] == bps].set_index("fold")["annual"]
        d = (cur - base).to_numpy()
        se = d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else float("nan")
        flag = "←深入檢驗" if se > 0 and abs(d.mean()) / se >= SIG_THRESHOLD_SE else ""
        print(
            f"    {bps:>5.0f}bp: Δmean {d.mean():+.2%}  std {d.std(ddof=1):.2%}  "
            f"SE {se:.2%}  |Δmean|/SE {abs(d.mean()) / se if se > 0 else float('nan'):.2f}  {flag}"
        )
    print()


def _fold4_tracking(clean_df: pd.DataFrame) -> None:
    print(f"  ── 強度①：{FOLD4_LABEL} 逐 bps 追蹤 ──")
    sub = clean_df[clean_df["fold"] == 4].set_index("bps")["annual"]
    for bps in SLIP_GRID:
        a = sub.loc[bps]
        status = "獲利" if a > 0 else "轉虧"
        print(f"    {bps:>5.0f}bp: {a:+.1%}  [{status}]")
    all_profit = bool((sub > 0).all())
    print(f"    全網格維持獲利: {'是' if all_profit else '否——質變事件'}")
    print()


def _flip_scan(name: str, df: pd.DataFrame) -> None:
    print(f"  ── 強度②：[{name}] 獲利→虧損翻轉掃描 ──")
    base = df[df["bps"] == 0.0].set_index("fold")["annual"]
    for bps in SLIP_GRID:
        if bps == 0.0:
            continue
        cur = df[df["bps"] == bps].set_index("fold")["annual"]
        flips = int(((base > 0) & (cur <= 0)).sum())
        n_profit_base = int((base > 0).sum())
        print(f"    {bps:>5.0f}bp: 翻轉 {flips}/{n_profit_base} 個原獲利 fold")
    print()


def _turnover_decomposition(name: str, df: pd.DataFrame) -> None:
    """★固定呈現段，不因「增量有限」結論而省略★：侵蝕是否與成交筆數相關。

    比照 Δsizing規則二教訓——彙總數字看似影響有限，底下可能藏著集中在少數
    高換手 fold 的劇烈侵蝕，被彙總平均掩蓋。逐 fold 列出「成交筆數 vs
    Δ(20bp)」，並回報侵蝕與換手的相關係數（分散 vs 集中的量化依據）。
    """
    print(f"  ── ★強度③（固定呈現）：[{name}] 換手拆解——侵蝕是否集中在少數 fold ★")
    base = df[df["bps"] == 0.0].set_index("fold")
    top = df[df["bps"] == SLIP_GRID[-1]].set_index("fold")
    n_fills = base["n_fills"]
    erosion = base["annual"] - top["annual"]  # 20bp 侵蝕（正值 = annual 下降）
    hdr = f"    {'fold':>4} | {'成交@0bp':>8} | {f'annual@0bp':>11} | {'侵蝕@20bp(pp)':>13}"
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    for k in base.index:
        print(f"    {k:>4} | {int(n_fills.loc[k]):>8d} | {base['annual'].loc[k]:>+10.1%}  | "
              f"{erosion.loc[k] * 100:>+12.2f}")
    if len(base) >= 3 and n_fills.std(ddof=0) > 0 and erosion.std(ddof=0) > 0:
        corr = float(np.corrcoef(n_fills.to_numpy(), erosion.to_numpy())[0, 1])
    else:
        corr = float("nan")
    total_erosion = float(erosion.sum())
    max_share = float(erosion.max() / total_erosion) if total_erosion != 0 else float("nan")
    print(f"    成交筆數 vs 侵蝕 相關係數 = {corr:+.2f}；"
          f"單一 fold 佔總侵蝕最大比例 = {max_share:.0%}"
          f"（{'集中' if max_share > 0.5 else '分散'}）")
    print()


def main() -> None:
    print("=" * 110)
    print("★ 滑價敏感度壓力測試：E2「零滑價」簡化假設是否系統性低估成本 ★")
    print("  sizing=leverage_cap／ATR N=3.0-14／固定網15%／close-only／fee=5bps／next_open 全部不動")
    print("  網格 {0,2,5,10,20}bp；只鎖定 BTCUSDT（台股未走 E2，不外推）")
    print("=" * 110)

    start, end, n_splits = CLEAN_BASELINE
    clean_df = _run_window("乾淨基準（定錨展示+fold4追蹤，與窗A重疊不入判準）", start, end, n_splits)
    _summarize("乾淨基準", clean_df)
    _fold4_tracking(clean_df)
    _flip_scan("乾淨基準", clean_df)
    _turnover_decomposition("乾淨基準", clean_df)

    window_dfs: dict[str, pd.DataFrame] = {}
    for name, (w_start, w_end) in WINDOWS.items():
        window_dfs[name] = _run_window(f"窗{name}", w_start, w_end, 15)
        _summarize(f"窗{name}", window_dfs[name])
        _flip_scan(f"窗{name}", window_dfs[name])
        _turnover_decomposition(f"窗{name}", window_dfs[name])

    print("=" * 110)
    print("★ 跨窗一致性總表（C/D/A 判準；5bp=fee同量級參照、20bp=極端壓力）★")
    print("=" * 110)
    for bps in (5.0, 10.0, 20.0):
        print(f"  {bps:.0f}bp:")
        deltas = []
        for name, df in window_dfs.items():
            base = df[df["bps"] == 0.0].set_index("fold")["annual"]
            cur = df[df["bps"] == bps].set_index("fold")["annual"]
            d = (cur - base).to_numpy()
            deltas.append(d.mean())
            print(f"    窗{name}: Δmean {d.mean():+.2%}")
        signs_consistent = all(d < 0 for d in deltas) or all(d >= 0 for d in deltas)
        print(f"    三窗方向一致: {'是' if signs_consistent else '否（混合，符合預期——滑價恆不利，'
              '差異來自曝險路徑非方向本身有懸念）'}")
    print()
    print("  判讀請見上方各窗量級/強度輸出；判決依兩個出口見交付報告，本檔不自動下結論。")


if __name__ == "__main__":
    main()
