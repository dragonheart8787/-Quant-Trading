"""Phase 1 驗證階段 end-to-end 腳本：walk-forward + lookahead 對照。

執行：  python run_validation.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.report import build_report
from backtest.vector_engine import run_backtest
from backtest.walk_forward import walk_forward
from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from strategy.ma_rsi import MaRsiStrategy

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 3000      # 約 125 天 1h K 線
N_SPLITS = 5
FEE_BPS = 5.0


def main() -> None:
    print(f"抓取 {SYMBOL} {INTERVAL} K 線 (limit={LIMIT}) ...")
    raw = fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=LIMIT)
    data = to_canonical_from_binance(raw)
    validate_canonical(data)
    print(f"canonical 筆數: {len(data)}  區間: {data['ts'].iloc[0]} → {data['ts'].iloc[-1]}\n")

    strat = MaRsiStrategy(fast_window=20, slow_window=60, rsi_window=14, rsi_overbought=70)

    print(f"===== Walk-forward ({N_SPLITS} folds, TimeSeriesSplit) =====")
    summary = walk_forward(data, strat, n_splits=N_SPLITS, fee_bps=FEE_BPS)
    print(summary.pretty())

    # --- Lookahead bias 示範 ---
    # 注意：MA/RSI 是「慢速、持續性」趨勢訊號，shift 一根幾乎不改變它，
    # 因此拿它做 shift 對照看不出未來函數的破壞力。要清楚示範，必須用一個
    # 「同根未來函數」訊號：signal[t] = sign(close[t]-close[t-1])，
    # 即用當根漲跌當訊號（現實中收盤才知道，來不及交易當根）。
    # 這與 tests/test_lookahead_bias.py 的自動化檢查同源。
    print("\n===== Lookahead bias 示範（同根未來函數訊號，真實價格）=====")
    indexed = data.set_index("ts")[["close"]]
    same_bar = indexed["close"].diff()
    cheat_signal = np.sign(same_bar).fillna(0.0).clip(lower=0).astype(int)
    cheat_signal = pd.Series(cheat_signal.to_numpy(), index=indexed.index, name="signal")
    with_shift = build_report(run_backtest(indexed, cheat_signal, fee_bps=0.0, shift_signal=True))
    without_shift = build_report(run_backtest(indexed, cheat_signal, fee_bps=0.0, shift_signal=False))
    print(f"{'metric':>14} {'有 shift(正式)':>16} {'無 shift(作弊)':>16}")
    print(f"{'total_return':>14} {with_shift.total_return:>15.2%} {without_shift.total_return:>15.2%}")
    print(f"{'sharpe':>14} {with_shift.sharpe:>16.2f} {without_shift.sharpe:>16.2f}")
    print(f"{'max_drawdown':>14} {with_shift.max_drawdown:>15.2%} {without_shift.max_drawdown:>15.2%}")
    print(f"{'win_rate':>14} {with_shift.win_rate:>15.2%} {without_shift.win_rate:>15.2%}")
    print("→ 無 shift 時績效不合理暴增即為未來函數警訊；正式回測一律走 shift(1)。")


if __name__ == "__main__":
    main()
