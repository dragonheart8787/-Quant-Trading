"""ATR 強平防線校正跑批（2026-07-13 立項）：機制層全網格 + 結果層 E2 對照。

任務規格（使用者 2026-07-13 確認）：
  - 校正對象：check_forced_liquidation 的 N 倍數 × ATR window
    （RiskConfig.forced_liq_atr_n / atr window；固定安全網 15% 本輪不動）。
  - 觸發依據維持 close-only（決策③；盤中 high 觸價敏感度另開一輪）。
  - 網格：N ∈ {1.5, 2, 3, 4} × window ∈ {7, 14, 21}，共 12 組合。
  - 資料：凍結 data/klines.sqlite（25300 根），三個互相零重疊的 8000 根窗
    （C / D / A；窗B 與 A 重疊 86% 依教訓排除），各 15-fold。
  - 訊號：v2 基準策略（MaRsiBidirectional 預設單視窗）的空頭區段。
  - 紀律：任何參數主張需三窗方向一致；預設立場 = 維持現值 N=3/14，
    「跨窗一致」不夠，需附具體改善量級供使用者拍板。
  - 誤殺判準的反事實偏差：權衡表必含「觸發當下未實現虧損」分布欄，
    不只看事後全知的誤殺/保護分類。

兩層流程：
  --stage mech（預設）：第一層機制層，全 12 組合純量測。
  --stage e2 --candidates "3.0:14,..."：第二層結果層，入圍組合跑
    事件引擎 E2（三窗 15-fold + 乾淨基準 5-fold 重點案例）。
  --stage high（2026-07-14 high 觸價敏感度輪）：close-only 簡化假設的
    壓力測試——N=3.0/w=14 凍結、唯一變因 = 觸發依據 close vs 盤中 high。
    不是參數校正（high ≥ close 是數學保證，沒有「哪個較好」的問題），
    只量化 Δ：多觸發幾次、提早幾根、fold4 是否翻轉、固定網是否仍未先觸發。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_atr_calibration.py
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_atr_calibration.py \
        --stage e2 --candidates "3.0:14,2.0:14"
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import run_event_backtest
from backtest.liq_calibration import run_calibration, summarize_grid
from backtest.report import build_report
from data.cleaning import validate_canonical
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOL, INTERVAL = "BTCUSDT", "1h"
FEE_BPS = 5.0
N_SPLITS = 15
GRID_NS = (1.5, 2.0, 3.0, 4.0)
GRID_WINDOWS = (7, 14, 21)
FIXED_PCT = 0.15

# 三個互相零重疊的 8000 根窗（HANDOFF「固定資料集原則」；依時間順序 C→D→A）
WINDOWS: dict[str, tuple[str, str]] = {
    "C": ("2023-08-22 12:00", "2024-07-20 19:00"),
    "D": ("2024-07-20 20:00", "2025-06-19 03:00"),
    "A": ("2025-06-19 04:00", "2026-05-18 11:00"),
}
# 乾淨基準窗（重點案例：fold1/2/5 已知 v2 觸發、fold4 空頭大賺不應誤殺）
CLEAN_BASELINE = ("2026-02-28 07:00", "2026-07-03 06:00", 5)


def _v2_strategy() -> MaRsiBidirectionalStrategy:
    return MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_overbought=70.0, regime_window=120
    )


def _load(start: str, end: str) -> pd.DataFrame:
    from data.storage_sqlite import connect, load_klines

    conn = connect(os.environ.get("KLINE_DB") or None)
    try:
        data = load_klines(
            conn, SYMBOL, INTERVAL,
            start=pd.Timestamp(start, tz="UTC"),
            end=pd.Timestamp(end, tz="UTC"),
        )
    finally:
        conn.close()
    if data.empty:
        raise RuntimeError("落地 DB 查無資料；請先跑 run_ingest.py")
    validate_canonical(data)
    return data.reset_index(drop=True)


# ===== 第一層：機制層（全網格純量測）=====

def stage_mechanism() -> None:
    strat = _v2_strategy()
    all_summaries: dict[str, pd.DataFrame] = {}
    all_segments: dict[str, pd.DataFrame] = {}

    for win, (start, end) in WINDOWS.items():
        data = _load(start, end)
        segments_df, triggers_df = run_calibration(
            data, strat, n_splits=N_SPLITS,
            atr_ns=GRID_NS, atr_windows=GRID_WINDOWS, fixed_pct=FIXED_PCT,
        )
        all_summaries[win] = summarize_grid(triggers_df)
        all_segments[win] = segments_df

    # --- 樣本量誠實標注 ---
    print("=" * 100)
    print("(0) 樣本量盤點（校正紀律：樣本偏少時 12 組合比較退化為小樣本問題，最終報告必須標注）")
    print("=" * 100)
    total = 0
    for win, seg_df in all_segments.items():
        n_seg = len(seg_df)
        total += n_seg
        lens = seg_df["n_bars"]
        per_fold = seg_df.groupby("fold").size()
        print(
            f"  窗{win}: 區段 {n_seg:>3d}  空頭bar {int(lens.sum()):>5d}  "
            f"區段長度 min/med/max = {int(lens.min())}/{lens.median():.0f}/{int(lens.max())}  "
            f"單fold區段數 min/max = {int(per_fold.min()) if len(per_fold) else 0}/"
            f"{int(per_fold.max()) if len(per_fold) else 0}"
        )
    print(f"  三窗合計區段 = {total}")
    print()

    # --- 逐區段不利波幅分布（機制層第一部分：與網格無關的原始量測）---
    print("=" * 100)
    print("(1) 逐區段最大不利波幅分布（觸發防線的『原料』；ATR 倍數對 window 敏感故分列）")
    print("=" * 100)
    for win, seg_df in all_segments.items():
        adv = seg_df["max_adverse_pct"] * 100
        print(f"  窗{win}: max不利波幅%  p50={adv.median():.2f}  p75={adv.quantile(0.75):.2f}  "
              f"p90={adv.quantile(0.90):.2f}  max={adv.max():.2f}")
        for w in GRID_WINDOWS:
            m = seg_df[f"max_atr_mult_w{w}"].dropna()
            print(f"        maxATR倍數(w={w:>2d})  p50={m.median():.2f}  p75={m.quantile(0.75):.2f}  "
                  f"p90={m.quantile(0.90):.2f}  max={m.max():.2f}")
    print()

    # --- 權衡總表（含觸發當下浮虧分布欄）---
    print("=" * 100)
    print("(2) ★ 機制層權衡總表：觸發/誤殺/保護 × 防線分工 × 觸發當下浮虧分布 ★")
    print("    誤殺 = 觸發但持有到訊號自然翻轉其實是賺的（事後全知判準，有系統性偏鬆風險）；")
    print("    浮虧欄 = 觸發當下 unrealized loss %（併陳，校正紀律新增要求）。")
    print("=" * 100)
    hdr = (f"  {'N':>4} {'win':>4} | {'觸發':>4} {'誤殺':>4} {'保護':>4} | "
           f"{'ATR線':>5} {'固定網':>6} {'同根':>4} | {'浮虧med':>8} {'浮虧max':>8}")
    for win in WINDOWS:
        summary = all_summaries[win]
        n_seg = int(summary["n_segments"].iloc[0]) if len(summary) else 0
        print(f"  [窗{win}]（{n_seg} 區段）")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for _, row in summary.iterrows():
            cur = "←現值" if (row["atr_n"] == 3.0 and row["atr_window"] == 14) else ""
            print(
                f"  {row['atr_n']:>4.1f} {int(row['atr_window']):>4d} | "
                f"{int(row['n_triggered']):>4d} {int(row['n_false_kill']):>4d} "
                f"{int(row['n_protect']):>4d} | "
                f"{int(row['n_line_atr']):>5d} {int(row['n_line_fixed']):>6d} "
                f"{int(row['n_line_both']):>4d} | "
                f"{row['loss_at_trigger_med'] * 100:>7.2f}% "
                f"{row['loss_at_trigger_max'] * 100:>7.2f}%  {cur}"
            )
        print()

    # --- 跨窗一致性檢查（相對現值 N=3/14 的方向）---
    print("=" * 100)
    print("(3) 跨窗一致性：各組合相對現值 N=3.0/14 的（誤殺增減, 保護增減）——三窗方向一致才可主張")
    print("=" * 100)
    base = {
        win: all_summaries[win].set_index(["atr_n", "atr_window"]).loc[(3.0, 14)]
        for win in WINDOWS
    }
    print(f"  {'N':>4} {'win':>4} | " + " | ".join(f"窗{w}(Δ誤殺,Δ保護)" for w in WINDOWS)
          + " | 誤殺方向一致 | 保護方向一致")
    for n_val in GRID_NS:
        for w in GRID_WINDOWS:
            if (n_val, w) == (3.0, 14):
                continue
            d_fk, d_pr = [], []
            cells = []
            for win in WINDOWS:
                row = all_summaries[win].set_index(["atr_n", "atr_window"]).loc[(n_val, w)]
                dfk = int(row["n_false_kill"] - base[win]["n_false_kill"])
                dpr = int(row["n_protect"] - base[win]["n_protect"])
                d_fk.append(dfk)
                d_pr.append(dpr)
                cells.append(f"{dfk:>+5d},{dpr:>+5d}    ")
            fk_consistent = all(x > 0 for x in d_fk) or all(x < 0 for x in d_fk) or all(x == 0 for x in d_fk)
            pr_consistent = all(x > 0 for x in d_pr) or all(x < 0 for x in d_pr) or all(x == 0 for x in d_pr)
            print(f"  {n_val:>4.1f} {w:>4d} | " + " | ".join(cells)
                  + f" | {'一致' if fk_consistent else '不一致':>6} | {'一致' if pr_consistent else '不一致':>6}")
    print()
    print("  （判讀與入圍決定見交付報告；預設立場 = 維持現值，除非三窗一致且量級足夠。）")


# ===== high 觸價敏感度（close-only 簡化假設壓力測試，2026-07-14）=====

CURRENT_N, CURRENT_W = 3.0, 14  # 已定案現值，本 stage 凍結不掃


def _high_one_window(
    data: pd.DataFrame, n_splits: int
) -> dict[str, pd.DataFrame]:
    """對一個時間窗跑 close / high 兩種觸發依據（N=3.0/w=14 單組合）並合併。"""
    strat = _v2_strategy()
    kwargs = dict(
        n_splits=n_splits, atr_ns=(CURRENT_N,), atr_windows=(CURRENT_W,),
        fixed_pct=FIXED_PCT,
    )
    seg_c, trig_c = run_calibration(data, strat, trigger_source="close", **kwargs)
    seg_h, trig_h = run_calibration(data, strat, trigger_source="high", **kwargs)
    merged = trig_c.merge(
        trig_h, on=["fold", "seg_id"], suffixes=("_c", "_h"), validate="1:1"
    )
    # 支配性斷言（property test 的實資料再驗證）：close 觸發 ⇒ high 觸發且不晚於
    both = merged[merged["triggered_c"]]
    assert both["triggered_h"].all(), "close 觸發但 high 未觸發，違反支配性"
    assert (both["bars_to_trigger_h"] <= both["bars_to_trigger_c"]).all()
    return {"segments_high": seg_h, "merged": merged}


def stage_high_sensitivity() -> None:
    print("=" * 100)
    print(f"★ high 觸價敏感度（N={CURRENT_N}/w={CURRENT_W} 凍結；唯一變因 = 觸發依據 close vs high）★")
    print("  定調：這是 close-only 簡化假設的壓力測試，不是參數校正——沒有『哪個較好』，")
    print("  只有『差多少、差在哪』。浮虧兩個界：觸線價近似（真實強平成交）與 high（當根最壞上界）。")
    print("=" * 100)
    print()

    runs: dict[str, dict[str, pd.DataFrame]] = {}
    for win, (start, end) in WINDOWS.items():
        runs[win] = _high_one_window(_load(start, end), N_SPLITS)
    c_start, c_end, c_splits = CLEAN_BASELINE
    clean_data = _load(c_start, c_end)
    runs["乾淨"] = _high_one_window(clean_data, c_splits)

    print("(1) 逐窗觸發數對照與新增觸發拆解（誤殺 = 事後全知判準，慣例同機制層）")
    hdr = (f"  {'窗':>4} | {'close觸發':>8} {'high觸發':>8} {'增幅':>7} | "
           f"{'新增':>4} {'新增誤殺':>8} {'新增保護':>8} | "
           f"{'新增觸線浮虧med/max':>18}")
    print(hdr)
    print("  " + "-" * 92)
    for win, r in runs.items():
        m = r["merged"]
        n_c = int(m["triggered_c"].sum())
        n_h = int(m["triggered_h"].sum())
        new = m[~m["triggered_c"] & m["triggered_h"]]
        line_loss = new["loss_at_line_pct_h"] * 100
        loss_txt = (
            f"{line_loss.median():>7.2f}% /{line_loss.max():>6.2f}%" if len(new) else "      —"
        )
        pct = (n_h - n_c) / n_c * 100 if n_c else float("nan")
        print(
            f"  {win:>4} | {n_c:>8d} {n_h:>8d} {pct:>+6.1f}% | "
            f"{len(new):>4d} {int(new['false_kill_h'].sum()):>8d} "
            f"{int(new['protect_h'].sum()):>8d} | {loss_txt}"
        )
    print()

    print("(2) 共同觸發區段的提早量分布（Δbars = close 觸發 bar − high 觸發 bar，逐檔計數不只平均）")
    for win, r in runs.items():
        m = r["merged"]
        both = m[m["triggered_c"] & m["triggered_h"]]
        delta = (both["bars_to_trigger_c"] - both["bars_to_trigger_h"]).astype(int)
        counts = delta.value_counts().sort_index()
        dist = "  ".join(f"Δ{k}:{v}筆" for k, v in counts.items())
        print(f"  {win:>4} | 共同觸發 {len(both):>3d} 筆 | {dist}")
    print()

    print("(3) 固定網 15% 在 high 下的防線分工（校正輪結論『從未先觸發』的重驗）")
    for win, r in runs.items():
        m = r["merged"]
        trig = m[m["triggered_h"]]
        n_fixed = int((trig["line_h"] == "fixed").sum())
        n_both = int((trig["line_h"] == "both").sum())
        n_atr = int((trig["line_h"] == "atr").sum())
        verdict = "仍未先觸發" if (n_fixed + n_both) == 0 else "★出現固定網觸發★"
        print(f"  {win:>4} | atr:{n_atr:>3d}  fixed:{n_fixed}  both:{n_both} → {verdict}")
    print()

    print("(4) 未觸發區段的盤中安全邊際（high 距觸發線最近幾倍 ATR；越接近 3.0 = 邊際越薄）")
    for win, r in runs.items():
        m = r["merged"]
        seg_h = r["segments_high"]
        untrig_keys = m.loc[~m["triggered_h"], ["fold", "seg_id"]]
        un = seg_h.merge(untrig_keys, on=["fold", "seg_id"])
        mult = un[f"max_atr_mult_high_w{CURRENT_W}"].dropna()
        print(
            f"  {win:>4} | 未觸發 {len(un):>3d} 段 | maxATR倍數(high) "
            f"p50={mult.median():.2f}  p90={mult.quantile(0.90):.2f}  max={mult.max():.2f}"
        )
    print()

    print("(5) ★ 乾淨基準 fold4 檢驗（本輪最重要檢驗點：close-only 下 0 觸發、無誤殺是否仍成立）★")
    m = runs["乾淨"]["merged"]
    seg_h = runs["乾淨"]["segments_high"]
    f4 = seg_h[seg_h["fold"] == 4].merge(
        m.loc[m["fold"] == 4, ["fold", "seg_id", "triggered_c", "triggered_h"]],
        on=["fold", "seg_id"],
    )
    if f4.empty:
        print("  fold4 無空頭區段（異常，請檢查訊號）")
    else:
        print(f"  {'seg':>4} {'bars':>5} {'close觸發':>8} {'high觸發':>8} | "
              f"{'maxATR倍數(close)':>16} {'maxATR倍數(high)':>16} | 距線邊際(高)")
        for _, row in f4.iterrows():
            margin = CURRENT_N - row[f"max_atr_mult_high_w{CURRENT_W}"]
            print(
                f"  {int(row['seg_id']):>4d} {int(row['n_bars']):>5d} "
                f"{'觸發' if row['triggered_c'] else '—':>8} "
                f"{'觸發' if row['triggered_h'] else '—':>8} | "
                f"{row[f'max_atr_mult_w{CURRENT_W}']:>16.2f} "
                f"{row[f'max_atr_mult_high_w{CURRENT_W}']:>16.2f} | "
                f"{margin:>+5.2f}×ATR"
            )
        n_trig_f4 = int(f4["triggered_h"].sum())
        print(f"  → fold4 high 觸發 {n_trig_f4} 次"
              + ("（close-only 結論維持）" if n_trig_f4 == 0 else "（★close-only 結論翻轉，需回報使用者★）"))
    print()
    print("  （判讀屬交付報告：增幅 ≤10% 且 fold4 維持 0 觸發 → close-only 簡化影響很小。）")


# ===== 第二層：結果層（入圍組合跑 E2）=====

def _e2_fold_runs(
    data: pd.DataFrame, n_splits: int, atr_n: float, atr_window: int
) -> tuple[list[float], list[int]]:
    """對一個時間窗跑 E2（next_open + 完整風控），回傳每 fold 年化與強平次數。"""
    strat = _v2_strategy()
    full_signal = strat.generate_signals(data)
    full_atr = compute_atr(data[["high", "low", "close"]], window=atr_window)
    splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))

    annuals: list[float] = []
    liq_counts: list[int] = []
    for _, test_idx in splits:
        td = data.iloc[test_idx].reset_index(drop=True)
        sig = full_signal.iloc[test_idx].reset_index(drop=True)
        atr_s = full_atr.iloc[test_idx].reset_index(drop=True)
        ev = run_event_backtest(
            td, sig, fee_bps=FEE_BPS, fill_mode="next_open", slippage_bps=0.0,
            # ↑ 2026-07-15 pin 0bp：保留本輪（2026-07-13 結案）數字的逐位可
            # 重現性，不隨 slippage_bps 新預設值（2.0，2026-07-15 基準切換
            # 拍板）變動。新正式基準見 HANDOFF「滑價敏感度」節。
            risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=atr_n)),
            atr_series=atr_s,
        )
        annuals.append(build_report(ev.to_backtest_result(td["close"])).annual_return)
        liq_counts.append(len(ev.liquidation_bars))
    return annuals, liq_counts


def stage_e2(candidates: list[tuple[float, int]]) -> None:
    print("=" * 100)
    print(f"(4) ★ 結果層 E2 對照（入圍組合：{candidates}；現值 = N=3.0/14）★")
    print("    E2 = 事件引擎 next_open + 完整風控（規則二 sizing 0.5x、熔斷、強平）")
    print("=" * 100)

    # 三個零重疊窗 15-fold
    for win, (start, end) in WINDOWS.items():
        data = _load(start, end)
        print(f"  [窗{win}] {start} → {end}（{len(data)} 根，{N_SPLITS}-fold）")
        for n_val, w in candidates:
            annuals, liqs = _e2_fold_runs(data, N_SPLITS, n_val, w)
            arr = np.array(annuals)
            cur = "←現值" if (n_val, w) == (3.0, 14) else ""
            print(
                f"    N={n_val:>3.1f} w={w:>2d}: 年化 mean={arr.mean():>+7.1%} "
                f"std={arr.std(ddof=1):>6.1%}  強平總次數={sum(liqs):>3d} "
                f"（觸發fold數={sum(1 for x in liqs if x > 0)}/{N_SPLITS}） {cur}"
            )
        print()

    # 乾淨基準 5-fold 重點案例
    start, end, n_splits = CLEAN_BASELINE
    data = _load(start, end)
    print(f"  [乾淨基準] {start} → {end}（{len(data)} 根，{n_splits}-fold）")
    print("    重點案例：fold1/2/5 已知 v2 有觸發；fold4 空頭大賺、不應被誤殺洗出場")
    for n_val, w in candidates:
        annuals, liqs = _e2_fold_runs(data, n_splits, n_val, w)
        cur = "←現值" if (n_val, w) == (3.0, 14) else ""
        per_fold = "  ".join(
            f"f{k + 1}:{a:>+7.1%}/{c}次" for k, (a, c) in enumerate(zip(annuals, liqs))
        )
        print(f"    N={n_val:>3.1f} w={w:>2d}: {per_fold}  {cur}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="ATR 強平防線校正跑批")
    parser.add_argument("--stage", choices=("mech", "e2", "high", "all"), default="mech")
    parser.add_argument(
        "--candidates", default="3.0:14",
        help='結果層入圍組合，格式 "N:window,N:window"（現值 3.0:14 建議恆列入）',
    )
    args = parser.parse_args()

    if args.stage in ("mech", "all"):
        stage_mechanism()
    if args.stage in ("high", "all"):
        stage_high_sensitivity()
    if args.stage in ("e2", "all"):
        cands = [
            (float(pair.split(":")[0]), int(pair.split(":")[1]))
            for pair in args.candidates.split(",")
        ]
        stage_e2(cands)


if __name__ == "__main__":
    main()
