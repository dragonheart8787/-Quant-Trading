"""多 symbol 擴充：方向A（同幣種類別驗證，2026-07-15 立項）。

任務定位：驗證 v2 策略／ATR N=3.0/w=14／multiplier m=0.5 這些已在 BTC 上
拍板的結論，是否在 ETHUSDT 上依然成立，還是 BTC 特例。**驗證性任務**，
不為 ETH 重新校正參數——判準是「既有參數在 ETH 上機制層行為是否合理」，
不是「ETH 自己的最佳值是多少」。全程只跑一次，不做網格搜尋。

控制實驗設計：沿用 BTC 既有 C/D/A 三窗＋乾淨基準的 calendar timestamp
邊界（不用 ETH 自己的獨立切分）——固定市況背景，只換資產，才能乾淨回答
「差異來自資產還是來自市況」。ETH 與 BTC 在 Binance 歷史深度完全對稱
（皆自 2017-08-17 上線），已驗證涵蓋範圍零缺口。

正式基準（全部沿用現行拍板值，不覆寫）：slippage_bps 省略＝現行 2.0、
sizing_mode="leverage_cap"、RiskConfig 全預設（N=3.0/w=14、m=0.5、
固定網 15%）、fee_bps=5.0、fill_mode="next_open"、v2 策略。

三項檢查：
  (A) v2 策略 E2 表現方向性對照（乾淨基準+C/D/A，逐 fold，比照既有 2bp
      基準表格式）。
  (B) ATR N=3.0/w=14 機制層合理性（只測現值，不掃網格）：觸發率是否落在
      合理區間、固定網是否仍是異常備援角色。
  (C) multiplier 機制層不變量（P1 觸發bar集合跨m不變／P2 損失/預算比率
      跨m不變）是否在 ETH 上同樣成立，m=0.5 封頂率是否正常。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_multi_symbol_eth.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.liq_calibration import run_calibration, summarize_grid
from backtest.report import build_report
from data.cleaning import validate_canonical
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from run_atr_calibration import CLEAN_BASELINE, FEE_BPS, WINDOWS, _v2_strategy
from run_rule1_sizing import _entry_stats, _liq_loss_details, _short_exposure

SYMBOL = "ETHUSDT"
GRID_M = (0.25, 0.5, 0.75, 1.0)
BASE_M = 0.5
RISK_PER_TRADE = 0.01


def _load(symbol: str, start: str, end: str) -> pd.DataFrame:
    from data.storage_sqlite import connect, load_klines

    conn = connect(None)
    try:
        data = load_klines(
            conn, symbol, "1h",
            start=pd.Timestamp(start, tz="UTC"), end=pd.Timestamp(end, tz="UTC"),
        )
    finally:
        conn.close()
    if data.empty:
        raise RuntimeError(f"{symbol} 落地 DB 查無資料；請先跑 run_ingest.py --symbol {symbol}")
    validate_canonical(data)
    return data.reset_index(drop=True)


# ===== (A) v2 策略 E2 表現方向性對照 =====

def _section_a() -> dict[str, np.ndarray]:
    print("=" * 100)
    print(f"(A) v2 策略 E2 表現方向性對照（{SYMBOL}，正式基準：2bp滑價/leverage_cap/N=3.14/m=0.5）")
    print("=" * 100)
    results: dict[str, np.ndarray] = {}

    def _run_window(name: str, start: str, end: str, n_splits: int) -> None:
        data = _load(SYMBOL, start, end)
        strat = _v2_strategy()
        signal = strat.generate_signals(data)
        atr_s = compute_atr(data[["high", "low", "close"]], window=14)
        splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))

        annuals = []
        print(f"  [{name}] {start} → {end}（{len(data)} 根，{n_splits}-fold）")
        for k, (_, test_idx) in enumerate(splits, start=1):
            td = data.iloc[test_idx].reset_index(drop=True)
            sig = signal.iloc[test_idx].reset_index(drop=True)
            atr_f = atr_s.iloc[test_idx].reset_index(drop=True)
            ev = run_event_backtest(
                td, sig, fee_bps=FEE_BPS, fill_mode="next_open",
                risk=RiskManager(equity=1.0), atr_series=atr_f,
                sizing_mode="leverage_cap",
            )
            a = build_report(ev.to_backtest_result(td["close"])).annual_return
            annuals.append(a)
            print(f"    fold {k:>2d}: {a:+.1%}  (多/空 bar {int((ev.position_qty>0).sum())}/"
                  f"{int((ev.position_qty<0).sum())}, 強平 {len(ev.liquidation_bars)})")
        arr = np.array(annuals)
        print(f"    mean {arr.mean():+.1%}  std {arr.std(ddof=1):.1%}")
        print()
        results[name] = arr

    start, end, n_splits = CLEAN_BASELINE
    _run_window("乾淨基準", start, end, n_splits)
    for name, (w_start, w_end) in WINDOWS.items():
        _run_window(f"窗{name}", w_start, w_end, 15)
    return results


# ===== (B) ATR N=3.0/w=14 機制層合理性（只測現值） =====

def _section_b() -> None:
    print("=" * 100)
    print(f"(B) ATR N=3.0/w=14 機制層合理性（{SYMBOL}，只測現值，不掃網格）")
    print("=" * 100)
    strat = _v2_strategy()
    total_seg = total_trig = 0
    for name, (start, end) in {"乾淨基準": (CLEAN_BASELINE[0], CLEAN_BASELINE[1]), **WINDOWS}.items():
        n_splits = CLEAN_BASELINE[2] if name == "乾淨基準" else 15
        data = _load(SYMBOL, start, end)
        segments_df, triggers_df = run_calibration(
            data, strat, n_splits=n_splits, atr_ns=(3.0,), atr_windows=(14,), fixed_pct=0.15,
        )
        summary = summarize_grid(triggers_df)
        row = summary.iloc[0]
        n_seg = len(segments_df)
        n_trig = int(row["n_triggered"])
        total_seg += n_seg
        total_trig += n_trig
        print(
            f"  [{name}] 空頭區段 {n_seg:>3d}  觸發 {n_trig:>3d}（密度 "
            f"{(n_trig / n_seg * 100 if n_seg else 0):>5.1f}%）  "
            f"防線分工 atr/fixed/both = {int(row['n_line_atr'])}/"
            f"{int(row['n_line_fixed'])}/{int(row['n_line_both'])}  "
            f"誤殺/保護 {int(row['n_false_kill'])}/{int(row['n_protect'])}  "
            f"觸發浮虧 med/max {row['loss_at_trigger_med']*100:.2f}%/"
            f"{row['loss_at_trigger_max']*100:.2f}%"
        )
    print(f"  ── 合計：{total_seg} 個空頭區段、{total_trig} 次觸發"
          f"（整體密度 {(total_trig / total_seg * 100 if total_seg else 0):.1f}%）")
    print(f"  參考基準（BTC 同一 N=3/14）：三窗合計 165 區段、53 次觸發（密度 ≈32%），"
          f"固定網從未先於 ATR 線觸發")
    print()


# ===== (C) multiplier 機制層不變量 =====

def _section_c() -> None:
    print("=" * 100)
    print(f"(C) multiplier 機制層不變量（{SYMBOL}，m 網格 {GRID_M}，現值 m=0.5）")
    print("=" * 100)

    def _run(td, sig, atr_s, m: float) -> EventBacktestResult:
        return run_event_backtest(
            td, sig, fee_bps=FEE_BPS, fill_mode="next_open",
            risk=RiskManager(equity=1.0, config=RiskConfig(short_risk_multiplier=m)),
            atr_series=atr_s, sizing_mode="risk_per_trade",
        )

    p1_ok, p2_ok = True, True
    max_dev_overall = 0.0
    cap_summary: list[tuple[str, int, int]] = []

    for name, (start, end) in {"乾淨基準": (CLEAN_BASELINE[0], CLEAN_BASELINE[1]), **WINDOWS}.items():
        n_splits = CLEAN_BASELINE[2] if name == "乾淨基準" else 15
        data = _load(SYMBOL, start, end)
        strat = _v2_strategy()
        signal = strat.generate_signals(data)
        atr_s = compute_atr(data[["high", "low", "close"]], window=14)
        splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))

        n_ent_total = n_cap_total = 0
        for k, (_, test_idx) in enumerate(splits, start=1):
            td = data.iloc[test_idx].reset_index(drop=True)
            sig = signal.iloc[test_idx].reset_index(drop=True)
            atr_f = atr_s.iloc[test_idx].reset_index(drop=True)
            open_arr = td["open"].to_numpy(dtype=float)
            ts_arr = list(td["ts"])

            per_m = {}
            for m in GRID_M:
                ev = _run(td, sig, atr_f, m)
                budget = RISK_PER_TRADE * m
                n_ent, n_cap, n_fb = _entry_stats(ev, open_arr, budget=budget)
                per_m[m] = {
                    "liq_bars": list(ev.liquidation_bars),
                    "details": _liq_loss_details(ev, ts_arr, budget=budget),
                    "n_ent": n_ent, "n_cap": n_cap,
                }
                if m == BASE_M:
                    n_ent_total += n_ent
                    n_cap_total += n_cap

            # P1：觸發 bar 集合跨 m 不變
            base_bars = per_m[BASE_M]["liq_bars"]
            if any(per_m[m]["liq_bars"] != base_bars for m in GRID_M):
                p1_ok = False
                print(f"  ✗ P1 不一致：[{name}] fold{k}")

            # P2：未封頂比率跨 m 不變
            base_details = per_m[BASE_M]["details"]
            for j in range(len(base_details)):
                r0, c0 = base_details[j]
                for m in GRID_M:
                    if m == BASE_M:
                        continue
                    r, c = per_m[m]["details"][j]
                    if not c0 and not c:
                        dev = abs(r - r0)
                        max_dev_overall = max(max_dev_overall, dev)
                        if dev >= 1e-9:
                            p2_ok = False

        cap_rate = (n_cap_total / n_ent_total * 100) if n_ent_total else 0.0
        cap_summary.append((name, n_ent_total, n_cap_total))
        print(f"  [{name}] m=0.5：進場 {n_ent_total} 筆、封頂 {n_cap_total} 筆"
              f"（封頂率 {cap_rate:.1f}%）")

    print()
    print(f"  P1（觸發bar集合跨m不變）: {'✓ 成立' if p1_ok else '★不成立★'}")
    print(f"  P2（未封頂損失/預算比率跨m不變）: {'✓ 成立' if p2_ok else '★不成立★'}"
          f"（最大偏差 {max_dev_overall:.2e}）")
    print(f"  參考基準（BTC 同一 m=0.5，四範圍合計）：封頂率約 2.9~7.4%")
    print()


def main() -> None:
    print("#" * 100)
    print(f"多 symbol 擴充 方向A：{SYMBOL} 機制層合理性 + v2 E2 表現驗證（2026-07-15）")
    print("驗證性任務：檢查既有拍板參數是否可推廣，不重新校正")
    print("#" * 100)
    print()
    _section_a()
    _section_b()
    _section_c()


if __name__ == "__main__":
    main()
