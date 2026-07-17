"""fold1-5 對照：long-only / 雙向v2(單視窗) / 雙向A2(多尺度) × 無風控/風控近似。

核心實驗：
  1. long-only baseline：MA/RSI 多頭訊號，無 regime 過濾
  2. 雙向 v2（單視窗 120）：+ trend_down 空頭（long_regime_window=None，預設 = 當前基準）
  3. 雙向 A2（多尺度 120+300）：空頭需短視窗與長視窗同時 trend_down（選配，本腳本顯式啟用作對照）
  每組各跑「無風控」與「風控近似」（空頭 ATR*3 動態防線 + 15% 固定安全網，強平後空手）

限制聲明（見 HANDOFF.md）：
  - 風控近似欄是「近似」不是精確模擬：進場價以前一根收盤近似，再進場用簡化規則
  - 所有報酬數字都不含保證金機制或真實滑點，只能作相對比較
  - 完整精確驗證需要 backtest/event_engine.py（Phase 2）

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python run_diagnosis_bidir.py

資料來源優先序（固定資料集原則，見 HANDOFF.md）：
    1. 落地 DB data/klines.sqlite（用 run_ingest.py 建立；KLINE_DB 可覆寫路徑）
    2. KLINE_CACHE_CSV 環境變數指定的 CSV 快取
    3. Binance 即時抓取（僅在前兩者都不存在時；數字會隨抓取時間漂移）
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.grid_search import label_fold_regimes
from backtest.short_risk_overlay import walk_forward_with_risk_overlay
from backtest.walk_forward import walk_forward
from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOL, INTERVAL, LIMIT = "BTCUSDT", "1h", 3000
FEE_BPS = 5.0
BASE_PARAMS = {"fast_window": 20, "slow_window": 60, "rsi_overbought": 70.0}
SHORT_REGIME_WINDOW = 120
LONG_REGIME_WINDOW = 300  # 待用歷史資料校正，非最終值

# 可用環境變數覆寫（沿用 TimeSeriesSplit 邏輯，只改 fold 數與輸入資料範圍）：
#   N_SPLITS     fold 數（預設 5）
#   KLINE_START  只用 ts >= 此時間的資料（ISO，UTC）
#   KLINE_END    只用 ts <= 此時間的資料（ISO，UTC）
N_SPLITS = int(os.environ.get("N_SPLITS", "5"))


def _load_data() -> pd.DataFrame:
    from data.storage_sqlite import DEFAULT_DB_PATH, connect, load_klines

    db_path = os.environ.get("KLINE_DB", "") or DEFAULT_DB_PATH
    if os.path.exists(db_path):
        conn = connect(db_path)
        try:
            data = load_klines(conn, SYMBOL, INTERVAL)
        finally:
            conn.close()
        if not data.empty:
            print(f"[落地DB] 讀取 {db_path}（固定資料集）")
            validate_canonical(data)
            return data

    cache = os.environ.get("KLINE_CACHE_CSV", "")
    if cache and os.path.exists(cache):
        data = pd.read_csv(cache, parse_dates=["ts", "ingested_at"])
        print(f"[CSV快取] 讀取 {cache}")
    else:
        raw = fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=LIMIT)
        data = to_canonical_from_binance(raw)
        print("[即時抓取] Binance（注意：數字會隨抓取時間漂移，建議先 run_ingest.py 落地）")
        if cache:
            data.to_csv(cache, index=False)
            print(f"[CSV快取] 已存 {cache}")
    validate_canonical(data)
    return data


def _apply_time_window(data: pd.DataFrame) -> pd.DataFrame:
    """依 KLINE_START / KLINE_END 環境變數裁切資料範圍（含端點）。"""
    start, end = os.environ.get("KLINE_START", ""), os.environ.get("KLINE_END", "")
    if start:
        data = data[data["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        data = data[data["ts"] <= pd.Timestamp(end, tz="UTC")]
    if start or end:
        data = data.reset_index(drop=True)
        print(f"[時間窗] {data['ts'].iloc[0]} -> {data['ts'].iloc[-1]}（{len(data)} 根）")
    return data


def _fold_short_counts(data: pd.DataFrame, strategy: MaRsiBidirectionalStrategy) -> list[int]:
    """每個 fold 測試窗內的空頭訊號 bar 數（訊號層統計，與風控無關）。"""
    indexed = data.reset_index(drop=True)
    full_signal = strategy.generate_signals(indexed)
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    return [
        int((full_signal.iloc[test_idx] == -1).sum())
        for _, test_idx in tscv.split(np.arange(len(indexed)))
    ]


def main() -> None:
    print("資料載入中...")
    data = _apply_time_window(_load_data())
    print(f"{SYMBOL} {INTERVAL}  {len(data)} 根  {data['ts'].iloc[0]} -> {data['ts'].iloc[-1]}"
          f"  n_splits={N_SPLITS}\n")

    regimes = label_fold_regimes(data, n_splits=N_SPLITS)

    strat_v2 = MaRsiBidirectionalStrategy(
        **BASE_PARAMS, regime_window=SHORT_REGIME_WINDOW, long_regime_window=None
    )
    strat_a2 = MaRsiBidirectionalStrategy(
        **BASE_PARAMS,
        regime_window=SHORT_REGIME_WINDOW,
        long_regime_window=LONG_REGIME_WINDOW,
    )

    print("計算 long-only baseline walk-forward...")
    base_wf = walk_forward(data, MaRsiStrategy(**BASE_PARAMS), n_splits=N_SPLITS, fee_bps=FEE_BPS)

    print("計算雙向 v2（單視窗）無風控 / 風控近似...")
    v2_wf = walk_forward(data, strat_v2, n_splits=N_SPLITS, fee_bps=FEE_BPS)
    v2_risk_wf = walk_forward_with_risk_overlay(data, strat_v2, n_splits=N_SPLITS, fee_bps=FEE_BPS)

    print("計算雙向 A2（多尺度）無風控 / 風控近似...")
    a2_wf = walk_forward(data, strat_a2, n_splits=N_SPLITS, fee_bps=FEE_BPS)
    a2_risk_wf = walk_forward_with_risk_overlay(data, strat_a2, n_splits=N_SPLITS, fee_bps=FEE_BPS)

    v2_shorts = _fold_short_counts(data, strat_v2)
    a2_shorts = _fold_short_counts(data, strat_a2)

    # ===== Section 1：每 fold 完整對照 =====
    print()
    print("=" * 108)
    print("(1) 每 fold：long-only / v2(120) / A2(120+300) 年化報酬，風控近似 = 空頭雙層強平")
    print("=" * 108)
    hdr = (
        f"{'fold':>4} {'regime':>11}  {'long-only':>9}  "
        f"{'v2無風控':>9} {'v2風控':>8}  {'A2無風控':>9} {'A2風控':>8}  "
        f"{'v2空bar':>7} {'A2空bar':>7}"
    )
    print(hdr)
    print("-" * len(hdr))

    for k in range(N_SPLITS):
        fr = regimes[k]
        print(
            f"{fr.fold:>4} {fr.regime.label:>11}  "
            f"{base_wf.folds[k].report.annual_return:>+9.1%}  "
            f"{v2_wf.folds[k].report.annual_return:>+9.1%} "
            f"{v2_risk_wf.folds[k].report.annual_return:>+8.1%}  "
            f"{a2_wf.folds[k].report.annual_return:>+9.1%} "
            f"{a2_risk_wf.folds[k].report.annual_return:>+8.1%}  "
            f"{v2_shorts[k]:>7d} {a2_shorts[k]:>7d}"
        )

    def _stats(wf) -> tuple[float, float]:
        anns = [f.report.annual_return for f in wf.folds]
        return float(np.mean(anns)), float(np.std(anns, ddof=1))

    print("-" * len(hdr))
    rows = [
        ("long-only", base_wf), ("v2無風控", v2_wf), ("v2風控", v2_risk_wf),
        ("A2無風控", a2_wf), ("A2風控", a2_risk_wf),
    ]
    for name, wf in rows:
        m, s = _stats(wf)
        print(f"  {name:<10} mean={m:+.1%}  std={s:.1%}")

    # ===== Section 2：A2 長視窗過濾影響（驗收重點）=====
    print()
    print("=" * 108)
    print("(2) A2 過濾影響：每 fold 被長視窗濾掉的空頭 bar 數與報酬變化（vs v2 無風控）")
    print("    驗收：逆勢空單 fold 應大減；正確空頭 fold（trend_down 賺錢者）不可誤殺")
    print("=" * 108)
    hdr2 = (
        f"{'fold':>4} {'regime':>11}  {'v2空bar':>7} {'A2空bar':>7} {'濾掉':>5}  "
        f"{'v2 vs base':>10} {'A2 vs base':>10} {'A2-v2':>8}"
    )
    print(hdr2)
    print("-" * len(hdr2))
    for k in range(N_SPLITS):
        fr = regimes[k]
        v2_gap = v2_wf.folds[k].report.annual_return - base_wf.folds[k].report.annual_return
        a2_gap = a2_wf.folds[k].report.annual_return - base_wf.folds[k].report.annual_return
        print(
            f"{fr.fold:>4} {fr.regime.label:>11}  "
            f"{v2_shorts[k]:>7d} {a2_shorts[k]:>7d} {v2_shorts[k] - a2_shorts[k]:>5d}  "
            f"{v2_gap:>+10.1%} {a2_gap:>+10.1%} "
            f"{a2_wf.folds[k].report.annual_return - v2_wf.folds[k].report.annual_return:>+8.1%}"
        )

    # ===== Section 3：同 regime 分組的 v2/A2 相對表現穩定性 =====
    # 問題：同一種 regime 的多個 fold 之間，v2 與 A2 的相對表現（A2-v2）
    # 是穩定一致，還是 fold 間依然大幅擺盪（後者代表訊號雜訊比在此尺度就是高）。
    print()
    print("=" * 108)
    print("(3) 同 regime 分組：v2 vs base / A2 vs base / A2-v2 的組內 mean±std（年化 pp）")
    print("=" * 108)
    groups: dict[str, list[int]] = {}
    for k in range(N_SPLITS):
        groups.setdefault(regimes[k].regime.label, []).append(k)

    hdr3 = (f"{'regime':>11} {'n_fold':>6}  {'v2-base mean±std':>18}  "
            f"{'A2-base mean±std':>18}  {'A2-v2 mean±std':>18}")
    print(hdr3)
    print("-" * len(hdr3))
    for label, idxs in sorted(groups.items()):
        v2_gaps = [v2_wf.folds[k].report.annual_return - base_wf.folds[k].report.annual_return
                   for k in idxs]
        a2_gaps = [a2_wf.folds[k].report.annual_return - base_wf.folds[k].report.annual_return
                   for k in idxs]
        deltas = [a2_wf.folds[k].report.annual_return - v2_wf.folds[k].report.annual_return
                  for k in idxs]

        def _ms(vals: list[float]) -> str:
            m = float(np.mean(vals))
            s = float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")
            return f"{m:+7.1%}±{s:6.1%}" if len(vals) > 1 else f"{m:+7.1%}   n/a "

        print(f"{label:>11} {len(idxs):>6d}  {_ms(v2_gaps):>18}  {_ms(a2_gaps):>18}  {_ms(deltas):>18}")


if __name__ == "__main__":
    main()
