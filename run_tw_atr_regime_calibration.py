"""台股 regime/ATR 參數校正（2026-07-16 立項）：BTC 校正值在台股日頻下是否退化。

背景：現行 ATR N=3/window=14、regime_window=120 都是用 BTC 1h 資料校正出來
的（2026-07-13 定案）。套到台股日K上，bar 數字沒變但代表的實際時間長度
完全不同（ATR window 14 小時→14 個交易日≈3 週；regime_window 120 小時=
5 天→120 個交易日≈6 個月）。這輪只驗證這組現行數字在台股日頻下是否退化，
**不做網格搜尋找台股最佳參數**（那是更大範圍的校正輪，另外立項）。

★P0（動工前已確認，見設計回報）★：剛結案的「台股 E2 化第一輪基準」用
`ma_rsi_regime.py`（純多頭）+ `forced_liquidation=False`，ATR 強平機制
完全沒被呼叫過——這輪校正不影響那批基準數字，是為未來放空輪預先鋪路的
機制層驗證。regime_window 則相反，已經活在那批基準數字的訊號生成裡，
若這輪發現明顯問題會回頭牽動已交付的基準。

三個判準（P1/P2/P3，仿 ETH 驗證輪格式）：
  - P1（機制層，重用 backtest/liq_calibration.py，不改動）：只測現行值
    N=3.0/window=14，用 ma_rsi_bidirectional.py（不改動）的空頭訊號段，
    看觸發密度是否非零非病態、固定網 15% 是否仍是異常備援角色。
  - P2（E2 診斷，機制層比對，非結果層／非實際部位）：比對 liq_calibration
    純機制預測的觸發 bar 集合 vs 真正跑 E2（含風控/熔斷）的實際觸發 bar
    集合，找差異、查根因（比照 ETH 輪 P1 手法：日頻下熔斷擋單導致部位
    根本不存在的邊界情況是否比 1h 頻率更常見）。
  - P3（regime_window 頻率轉換，描述性掃描，不改動 backtest/regime.py）：
    window∈{60,120,180,252}（≈3/6/9/12 個月）的 regime 標籤分布，看 120
    是否落在平滑過渡帶、不是孤立退化斷點。

資料範圍：沿用「台股 E2 化第一輪基準」相同 4 檔股票、各自完整歷史、
`TimeSeriesSplit(n_splits=15)`；股價全程用還原價。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tw_atr_regime_calibration.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import tw_stock_costs
from backtest.event_engine import run_event_backtest
from backtest.liq_calibration import detect_trigger, extract_short_segments, run_calibration, summarize_grid
from backtest.regime import classify_regime
from data import storage_events_tw
from data.adjustment import apply_back_adjustment
from data.storage_sqlite import connect, load_klines
from indicators.technical import atr as compute_atr
from risk.manager import RiskManager
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOLS: dict[str, str] = {
    "2330": "TWSE",
    "2603": "TWSE",
    "8069": "TPEx",
    "6446": "TPEx",
}
N_SPLITS = 15
TW_DB = "data/klines_tw.sqlite"

ATR_N = 3.0
ATR_WINDOW = 14
REGIME_WINDOWS = (60, 120, 180, 252)


def _v2_strategy() -> MaRsiBidirectionalStrategy:
    return MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_overbought=70.0, regime_window=120
    )


def _load_adjusted(symbol: str, exchange: str) -> pd.DataFrame:
    conn = connect(TW_DB)
    try:
        raw = load_klines(conn, symbol, "1d", exchange=exchange)
    finally:
        conn.close()
    if raw.empty:
        raise RuntimeError(f"{symbol} 查無資料")
    econn = storage_events_tw.connect()
    try:
        dividends = storage_events_tw.load_dividend_results(econn, symbol)
    finally:
        econn.close()
    if dividends.empty:
        raise RuntimeError(f"{symbol} 事件快照無資料；請先跑 run_ingest_tw_events.py")
    return apply_back_adjustment(raw, dividends).reset_index(drop=True)


# ===== P1：機制層（只測現行值 N=3.0/window=14） =====

def run_p1(adjusted: pd.DataFrame) -> pd.DataFrame:
    strat = _v2_strategy()
    _, triggers_df = run_calibration(
        adjusted, strat, n_splits=N_SPLITS, atr_ns=[ATR_N], atr_windows=[ATR_WINDOW],
    )
    return summarize_grid(triggers_df)


# ===== P2：E2 診斷（機制預測 vs 實際 E2 觸發 bar 集合比對） =====

def run_p2(adjusted: pd.DataFrame) -> list[dict]:
    strat = _v2_strategy()
    full_signal = strat.generate_signals(adjusted)
    full_atr = compute_atr(adjusted[["high", "low", "close"]], window=ATR_WINDOW)
    splits = list(TimeSeriesSplit(n_splits=N_SPLITS).split(np.arange(len(adjusted))))
    costs = tw_stock_costs()

    mismatches: list[dict] = []
    for k, (_, test_idx) in enumerate(splits):
        adj_slice = adjusted.iloc[test_idx].reset_index(drop=True)
        sig_slice = full_signal.iloc[test_idx].reset_index(drop=True)
        atr_slice = full_atr.iloc[test_idx].reset_index(drop=True)
        close_slice = adj_slice["close"]

        # 純機制預測（liq_calibration，無風控、無熔斷）
        predicted_bars: set[int] = set()
        for seg in extract_short_segments(sig_slice, close_slice):
            out = detect_trigger(seg, close_slice, atr_slice, ATR_N)
            if out is not None:
                predicted_bars.add(seg.start + out.bars_from_start)

        # 真正跑 E2（含風控/熔斷）；fill_mode=signal_close 對齊 liq_calibration
        # 的進場價近似（決策③），才是同一套假設下的比較，不是新增執行寫實度
        ev = run_event_backtest(
            adj_slice, sig_slice,
            fill_mode="signal_close",
            risk=RiskManager(equity=1.0),
            atr_series=atr_slice,
            costs=costs,
            slippage_bps=0.0,
        )
        actual_bars = set(ev.liquidation_bars)

        missing = predicted_bars - actual_bars
        extra = actual_bars - predicted_bars
        if missing or extra:
            mismatches.append({
                "fold": k + 1,
                "missing": sorted(missing),  # 機制預測會觸發，E2 實際沒有（部位可能被熔斷擋單）
                "extra": sorted(extra),      # E2 實際觸發，機制沒預測到
                "n_circuit_breaker_rejections": sum(
                    1 for r in ev.rejections if r[3] == "rejected_circuit_breaker"
                ),
            })
    return mismatches


# ===== P3：regime_window 頻率轉換敏感度掃描 =====

def run_p3(adjusted: pd.DataFrame) -> dict[int, dict[str, float]]:
    close = adjusted["close"].to_numpy(dtype=float)
    n = len(close)
    result: dict[int, dict[str, float]] = {}
    for w in REGIME_WINDOWS:
        labels = []
        for t in range(w - 1, n):
            window = pd.Series(close[t - w + 1 : t + 1])
            labels.append(classify_regime(window).label)
        counts = pd.Series(labels).value_counts(normalize=True)
        result[w] = counts.to_dict()
    return result


def main() -> None:
    p1_rows = []
    p2_all: dict[str, list[dict]] = {}
    p3_all: dict[str, dict[int, dict[str, float]]] = {}

    for symbol, exchange in SYMBOLS.items():
        print(f"跑 {symbol}（{exchange}）...")
        adjusted = _load_adjusted(symbol, exchange)

        p1 = run_p1(adjusted)
        p1["symbol"] = symbol
        p1_rows.append(p1)

        p2_all[symbol] = run_p2(adjusted)
        p3_all[symbol] = run_p3(adjusted)

    print("=" * 100)
    print("P1：機制層觸發密度（現行值 N=3.0/window=14，4 檔合計）")
    print("=" * 100)
    p1_df = pd.concat(p1_rows, ignore_index=True)
    for _, row in p1_df.iterrows():
        density = row["n_triggered"] / row["n_segments"] if row["n_segments"] else float("nan")
        print(
            f"[{row['symbol']}] 區段數={row['n_segments']:>4}  觸發數={row['n_triggered']:>4}  "
            f"密度={density:>6.1%}  atr線={row['n_line_atr']:>3}  fixed線={row['n_line_fixed']:>3}  "
            f"both={row['n_line_both']:>3}  誤殺={row['n_false_kill']:>3}  保護={row['n_protect']:>3}"
        )
    total_seg = p1_df["n_segments"].sum()
    total_trig = p1_df["n_triggered"].sum()
    total_fixed = p1_df["n_line_fixed"].sum()
    print(f"\n合計：區段數={total_seg}  觸發數={total_trig}  密度={total_trig/total_seg:.1%}"
          f"  fixed線觸發={total_fixed}（應接近0，異常備援角色）")
    print()

    print("=" * 100)
    print("P2：E2 診斷——機制預測 vs 實際 E2 觸發 bar 集合比對（機制層，非結果層）")
    print("=" * 100)
    any_mismatch = False
    for symbol, mismatches in p2_all.items():
        if not mismatches:
            print(f"[{symbol}] 60/60 fold 全部一致（機制預測 == E2 實際觸發 bar 集合）")
            continue
        any_mismatch = True
        for m in mismatches:
            print(
                f"[{symbol}] fold{m['fold']}：missing(機制預測有/E2無)={m['missing']}  "
                f"extra(E2有/機制無)={m['extra']}  同期熔斷拒單數={m['n_circuit_breaker_rejections']}"
            )
    if not any_mismatch:
        print("全部 4 檔 × 15-fold 逐位一致，無邊界情況。")
    print()

    print("=" * 100)
    print("P3：regime_window 頻率轉換敏感度（window={60,120,180,252} 交易日）")
    print("=" * 100)
    for symbol, dist_by_w in p3_all.items():
        print(f"[{symbol}]")
        for w in REGIME_WINDOWS:
            d = dist_by_w[w]
            print(
                f"    window={w:>3}  trend_up={d.get('trend_up', 0.0):>6.1%}  "
                f"range={d.get('range', 0.0):>6.1%}  trend_down={d.get('trend_down', 0.0):>6.1%}"
            )
        print()


if __name__ == "__main__":
    main()
