"""診斷 MA/RSI walk-forward 不穩定的原因。

輸出：
1. 每個 fold 的市場狀態標註（R²、效率比、趨勢/盤整）+ baseline sharpe。
2. 3x3 參數網格 × 每個 fold 的 sharpe + 跨 fold mean/std。
3. 「同狀態 fold 的 sharpe 一致性」彙總，輔助判斷 a/b/c。

執行：  python run_diagnosis.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.grid_search import default_param_grid, grid_search, label_fold_regimes
from backtest.walk_forward import walk_forward
from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from strategy.ma_rsi import MaRsiStrategy

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 3000
N_SPLITS = 5
FEE_BPS = 5.0

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def main() -> None:
    raw = fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=LIMIT)
    data = to_canonical_from_binance(raw)
    validate_canonical(data)
    print(f"{SYMBOL} {INTERVAL}  {len(data)} 根  {data['ts'].iloc[0]} → {data['ts'].iloc[-1]}\n")

    # --- (1) fold 市場狀態 + baseline sharpe ---
    baseline = MaRsiStrategy(fast_window=20, slow_window=60, rsi_window=14, rsi_overbought=70)
    base_summary = walk_forward(data, baseline, n_splits=N_SPLITS, fee_bps=FEE_BPS)
    regimes = label_fold_regimes(data, n_splits=N_SPLITS)

    print("===== (1) 各 fold 市場狀態 vs baseline(20/60) sharpe =====")
    print(f"{'fold':>4} {'regime':>11} {'R2':>6} {'eff':>6} {'base_sharpe':>12} {'annual':>9}")
    for fr, wf in zip(regimes, base_summary.folds):
        print(
            f"{fr.fold:>4} {fr.regime.label:>11} {fr.regime.r2:>6.2f} "
            f"{fr.regime.efficiency:>6.2f} {wf.report.sharpe:>12.2f} "
            f"{wf.report.annual_return:>8.1%}"
        )

    # --- (2) 3x3 參數網格 ---
    grid = default_param_grid()
    table = grid_search(data, grid, n_splits=N_SPLITS, fee_bps=FEE_BPS)
    fold_cols = [f"fold{i}" for i in range(1, N_SPLITS + 1)]
    print("\n===== (2) 參數網格：每組參數 × 每 fold sharpe + 跨 fold mean/std =====")
    disp = table.copy()
    for c in fold_cols + ["sharpe_mean", "sharpe_std"]:
        disp[c] = disp[c].map(lambda v: f"{v:6.2f}")
    print(disp.to_string(index=False))

    print("\n--- 跨 fold sharpe 標準差 摘要（衡量穩定性，越小越穩）---")
    print(f"所有參數組的 sharpe_std：min={table['sharpe_std'].min():.2f}  "
          f"median={table['sharpe_std'].median():.2f}  max={table['sharpe_std'].max():.2f}")
    best = table.loc[table["sharpe_mean"].idxmax()]
    most_stable = table.loc[table["sharpe_std"].idxmin()]
    print(f"最高平均 sharpe：fast={best['fast']:.0f} slow={best['slow']:.0f} "
          f"mean={best['sharpe_mean']:.2f} std={best['sharpe_std']:.2f}")
    print(f"最穩定(最小 std)：fast={most_stable['fast']:.0f} slow={most_stable['slow']:.0f} "
          f"mean={most_stable['sharpe_mean']:.2f} std={most_stable['sharpe_std']:.2f}")

    # --- (3) 同市場狀態內 fold 的 sharpe 一致性 ---
    print("\n===== (3) 跨『所有參數組』，同一 fold 的 sharpe 分布 =====")
    print("（若某 fold 不論參數都差，指向市場狀態問題而非參數）")
    print(f"{'fold':>4} {'regime':>11} {'sharpe_mean':>12} {'sharpe_std':>11} {'min':>7} {'max':>7}")
    for i, fr in enumerate(regimes, start=1):
        col = table[f"fold{i}"]
        print(
            f"{i:>4} {fr.regime.label:>11} {col.mean():>12.2f} {col.std(ddof=1):>11.2f} "
            f"{col.min():>7.2f} {col.max():>7.2f}"
        )

    # 同狀態分組的 fold 平均 sharpe（跨參數平均後再依 regime 分組）
    fold_param_mean = [table[f"fold{i}"].mean() for i in range(1, N_SPLITS + 1)]
    by_regime: dict[str, list[float]] = {}
    for i, fr in enumerate(regimes):
        by_regime.setdefault(fr.regime.label, []).append(fold_param_mean[i])
    print("\n--- 依市場狀態彙總（各 fold 跨參數平均 sharpe 再分組）---")
    for label, vals in by_regime.items():
        arr = np.array(vals)
        std = arr.std(ddof=1) if len(arr) > 1 else 0.0
        print(f"{label:>11}: n={len(arr)}  mean={arr.mean():>6.2f}  std={std:>5.2f}  vals={np.round(arr,2).tolist()}")


if __name__ == "__main__":
    main()
