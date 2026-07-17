"""比較 long-only baseline (MA/RSI) vs regime-filtered (MA/RSI+regime) 的
walk-forward 穩定性。沿用上一輪同一組 fold 與同一個 3x3 grid。

執行：  python run_diagnosis_regime.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.grid_search import default_param_grid, grid_search, label_fold_regimes
from backtest.walk_forward import walk_forward
from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_regime import MaRsiRegimeStrategy

SYMBOL, INTERVAL, LIMIT = "BTCUSDT", "1h", 3000
N_SPLITS, FEE_BPS = 5, 5.0
BASE_PARAMS = {"fast_window": 20, "slow_window": 60, "rsi_overbought": 70.0}


def _std_summary(table: pd.DataFrame) -> str:
    s = table["sharpe_std"]
    return f"min={s.min():.2f}  median={s.median():.2f}  max={s.max():.2f}"


def main() -> None:
    raw = fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=LIMIT)
    data = to_canonical_from_binance(raw)
    validate_canonical(data)
    print(f"{SYMBOL} {INTERVAL}  {len(data)} 根  {data['ts'].iloc[0]} → {data['ts'].iloc[-1]}\n")

    regimes = label_fold_regimes(data, n_splits=N_SPLITS)

    base = walk_forward(data, MaRsiStrategy(**BASE_PARAMS), n_splits=N_SPLITS, fee_bps=FEE_BPS)
    filt = walk_forward(data, MaRsiRegimeStrategy(**BASE_PARAMS), n_splits=N_SPLITS, fee_bps=FEE_BPS)

    # (1) 每 fold：regime + baseline vs filtered（用 20/60 參數）
    print("===== (1) 每 fold：baseline vs regime-filtered (20/60) =====")
    print(f"{'fold':>4} {'regime':>11} "
          f"{'base_sh':>8} {'filt_sh':>8} {'base_ann':>9} {'filt_ann':>9} "
          f"{'base_tr':>8} {'filt_tr':>8}")
    base_sh, filt_sh = [], []
    for fr, b, f in zip(regimes, base.folds, filt.folds):
        base_sh.append(b.report.sharpe)
        filt_sh.append(f.report.sharpe)
        print(f"{fr.fold:>4} {fr.regime.label:>11} "
              f"{b.report.sharpe:>8.2f} {f.report.sharpe:>8.2f} "
              f"{b.report.annual_return:>8.1%} {f.report.annual_return:>8.1%} "
              f"{b.report.num_trades:>8d} {f.report.num_trades:>8d}")
    print(f"{'mean':>4} {'':>11} {np.mean(base_sh):>8.2f} {np.mean(filt_sh):>8.2f}")
    print(f"{'std':>4} {'':>11} {np.std(base_sh, ddof=1):>8.2f} {np.std(filt_sh, ddof=1):>8.2f}")

    # (2) 3x3 grid：baseline vs filtered 並排
    grid = default_param_grid()
    base_tbl = grid_search(data, grid, n_splits=N_SPLITS, fee_bps=FEE_BPS, strategy_cls=MaRsiStrategy)
    filt_tbl = grid_search(data, grid, n_splits=N_SPLITS, fee_bps=FEE_BPS, strategy_cls=MaRsiRegimeStrategy)

    print("\n===== (2) 3x3 grid：跨 fold sharpe mean/std（baseline vs filtered）=====")
    print(f"{'fast':>4} {'slow':>4} | {'base_mean':>9} {'base_std':>8} | {'filt_mean':>9} {'filt_std':>8}")
    for (_, br), (_, fr) in zip(base_tbl.iterrows(), filt_tbl.iterrows()):
        print(f"{br['fast']:>4.0f} {br['slow']:>4.0f} | "
              f"{br['sharpe_mean']:>9.2f} {br['sharpe_std']:>8.2f} | "
              f"{fr['sharpe_mean']:>9.2f} {fr['sharpe_std']:>8.2f}")

    print("\n--- 跨 fold sharpe_std 範圍（越小越穩）---")
    print(f"baseline : {_std_summary(base_tbl)}")
    print(f"filtered : {_std_summary(filt_tbl)}")

    # (3) filtered 的每 fold x 跨參數分布（看 fold4 是否被救起來）
    print("\n===== (3) filtered：每 fold 跨所有參數的 sharpe 分布 =====")
    print(f"{'fold':>4} {'regime':>11} {'mean':>8} {'std':>7} {'min':>7} {'max':>7}")
    for i, fr in enumerate(regimes, start=1):
        col = filt_tbl[f"fold{i}"]
        print(f"{i:>4} {fr.regime.label:>11} {col.mean():>8.2f} {col.std(ddof=1):>7.2f} "
              f"{col.min():>7.2f} {col.max():>7.2f}")


if __name__ == "__main__":
    main()
