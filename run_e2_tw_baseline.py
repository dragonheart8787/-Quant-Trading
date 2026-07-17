"""台股 E2 化第一輪：多頭 baseline（2026-07-16 立項，使用者確認範圍）。

範圍（使用者定案，不在這輪解決的事項見下方「已知限制」）：
  - 策略：strategy/ma_rsi_regime.py（regime 過濾多頭，不含放空；不動這個
    既有檔案，只是拿它來跑 E2）。
  - 成本：台股真實不對稱成本（tw_stock_costs()，買 14.25bps／賣 44.25bps
    含證交稅）**必選**，見下方「成本必選介面設計」。
  - 股價：全程用還原價（data/adjustment.py::apply_back_adjustment()）——
    訊號生成與 E2 執行（成交、mark-to-market equity）全部餵還原價；原始價
    只用於本檔「M3 漲跌停曝險清點」的診斷性交叉比對，不進入回測執行路徑。
  - 資料範圍：4 檔股票各自完整歷史（不再受籌碼資料交集窗限制），各自獨立
    TimeSeriesSplit(n_splits=15)（比照 POC/chip 輪「雙位數獨立 fold」紀律）。
  - slippage_bps 明確 pin 0.0：引擎預設值 2.0 是 BTCUSDT 真實流動性量級
    的校正結果（2026-07-15 拍板），未經驗證不能沿用到台股——這是刻意的
    範圍節制，不是遺漏，台股滑價校正留給未來輪次。

成本必選介面設計（呼應「不能靜默用對稱假設跑出沒有意義的數字」）：
  1. `run_symbol_e2()` 簽名裡 `costs: TradeCosts` 沒有預設值——忘記傳在
     呼叫當下就是 TypeError，不會跑到一半才發現。
  2. 函式內第一行型別斷言，防止手滑傳 None／float／舊介面殘留寫法。
  3. 本檔所有 run_event_backtest 呼叫點都寫死 `costs=costs`（轉傳呼叫端
     給的值），不存在任何省略這個參數走引擎 fallback 的路徑。
  4. 全域唯一合法成本來源：模組層級常數 `COSTS = tw_stock_costs()`。

事前判準（動工前設計、跑批後逐項回報，機制層不通過不解讀報酬數字）：
  - M1 成本不對稱確實生效：所有 Fill 的 fee/notional 有效費率應精確等於
    14.25bps（BUY）或 44.25bps（SELL）。
  - M2 熔斷觸發密度非零也非全觸發：60 個 fold-案例中應有部分（非 0、非
    60）出現 tripped_days。**不重新論證「熔斷語意塌縮」**——那個結論已在
    前置查證階段定案，這裡只是健康檢查機制沒有失效或失控。
  - M3 漲跌停曝險清點（診斷性，不修正）：用原始價格清點每個 fold 的決策
    bar／實際成交 bar 有多少落在真實鎖死日，完整列出受影響的 (symbol,
    fold, 日期) 而非只給比例。
  - R1 60 個 fold-案例的年化報酬/Sharpe 不出現 NaN 或發散值。
  - R2 regime filter 確實生效：trend_up 類 fold 的多頭進場 bar 佔比應
    明顯高於 range/trend_down 類 fold。

已知限制（已在前置查證階段量化，本輪不解決，僅在此提醒、完整數字見
HANDOFF.md「台股 E2 化第一輪基準」節）：
  - 日內熔斷在日頻下的保護範圍塌縮成「同根 bar 自我攔截」，不是「當天
    剩餘 bar」保護（日頻沒有「當天剩餘 bar」這個概念）。
  - broker.market_order 永遠假設市價單全額成交，未建模漲跌停鎖死時
    可能無法成交的情況（M3 診斷數字量化這批結果的曝險範圍）。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_e2_tw_baseline.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import TradeCosts, tw_stock_costs
from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.grid_search import label_fold_regimes
from backtest.report import build_report
from broker.paper import Side
from data import storage_events_tw
from data.adjustment import apply_back_adjustment
from data.storage_sqlite import connect, load_klines
from risk.manager import RiskManager
from strategy.ma_rsi_regime import MaRsiRegimeStrategy

SYMBOLS: dict[str, str] = {
    "2330": "TWSE",
    "2603": "TWSE",
    "8069": "TPEx",
    "6446": "TPEx",
}
N_SPLITS = 15
PERIODS_PER_YEAR = 252
TW_DB = "data/klines_tw.sqlite"

# 全域唯一合法成本來源（見模組 docstring「成本必選介面設計」第4點）
COSTS: TradeCosts = tw_stock_costs()

# 漲跌停鎖死判定門檻（比照台股 E2 化前置查證任務查證4 的既有定義）
_LOCK_RETURN_THRESHOLD = 0.09


@dataclass
class FoldResult:
    symbol: str
    fold: int
    regime: str
    n_bars: int
    annual_return: float
    sharpe: float
    n_long_bars: int
    tripped_days: int
    buy_fee_bps: list[float] = field(default_factory=list)
    sell_fee_bps: list[float] = field(default_factory=list)
    decision_bar_locks: list[str] = field(default_factory=list)
    fill_bar_locks: list[tuple[str, str, float]] = field(default_factory=list)


def _strategy() -> MaRsiRegimeStrategy:
    return MaRsiRegimeStrategy()  # 沿用既有預設（fast=20 slow=60 rsi_window=14
    # overbought=70 regime_window=120），這輪不掃參數


def _load_raw(symbol: str, exchange: str) -> pd.DataFrame:
    conn = connect(TW_DB)
    try:
        data = load_klines(conn, symbol, "1d", exchange=exchange)
    finally:
        conn.close()
    if data.empty:
        raise RuntimeError(f"{symbol} 查無資料；請先跑 run_ingest_tw_price.py")
    return data.reset_index(drop=True)


def _load_dividends(symbol: str) -> pd.DataFrame:
    """讀本地事件快照（A4 技術債輪起不再即時打 FinMind，外部依賴風險隔離）。"""
    conn = storage_events_tw.connect()
    try:
        df = storage_events_tw.load_dividend_results(conn, symbol)
    finally:
        conn.close()
    if df.empty:
        raise RuntimeError(f"{symbol} 事件快照無資料；請先跑 run_ingest_tw_events.py")
    return df


def _lock_dates(raw: pd.DataFrame) -> pd.DataFrame:
    """全歷史一次算好的真實漲跌停鎖死日表（date, ret），用原始價格。"""
    close = raw["close"].to_numpy()
    high = raw["high"].to_numpy()
    low = raw["low"].to_numpy()
    ret = np.empty(len(raw))
    ret[0] = np.nan
    ret[1:] = close[1:] / close[:-1] - 1.0
    locked = (high == low) & (np.abs(ret) >= _LOCK_RETURN_THRESHOLD)
    dates = raw["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "ret": ret, "locked": locked})[locked].reset_index(drop=True)


def run_symbol_e2(
    symbol: str, exchange: str, costs: TradeCosts, n_splits: int = N_SPLITS
) -> list[FoldResult]:
    """單一 symbol 的 E2 baseline 跑批（15-fold）。

    costs 沒有預設值——見模組 docstring「成本必選介面設計」，忘記傳在
    呼叫當下就是 TypeError。
    """
    assert isinstance(costs, TradeCosts), (
        "台股 E2 baseline 必須傳入真實 tw_stock_costs()，不接受 None、float，"
        "或任何暗示對稱假設的呼叫方式"
    )

    raw = _load_raw(symbol, exchange)
    dividends = _load_dividends(symbol)
    adjusted = apply_back_adjustment(raw, dividends)
    lock_table = _lock_dates(raw)
    lock_date_set = set(lock_table["date"])

    strat = _strategy()
    full_signal = strat.generate_signals(adjusted)

    regimes = label_fold_regimes(adjusted, n_splits=n_splits)
    splits = list(TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(adjusted))))

    results: list[FoldResult] = []
    for k, (_, test_idx) in enumerate(splits):
        adj_slice = adjusted.iloc[test_idx].reset_index(drop=True)
        raw_slice = raw.iloc[test_idx].reset_index(drop=True)
        sig_slice = full_signal.iloc[test_idx].reset_index(drop=True)

        ev = run_event_backtest(
            adj_slice, sig_slice,
            risk=RiskManager(equity=1.0),
            forced_liquidation=False,  # 這輪多頭 baseline，不碰放空（決策①）
            slippage_bps=0.0,          # 未經台股校正，明確 pin，見模組 docstring
            costs=costs,
        )
        report = build_report(
            ev.to_backtest_result(adj_slice["close"]), periods_per_year=PERIODS_PER_YEAR
        )

        buy_bps = [f.fee / (f.quantity * f.price) * 10_000.0 for f in ev.fills if f.side is Side.BUY]
        sell_bps = [f.fee / (f.quantity * f.price) * 10_000.0 for f in ev.fills if f.side is Side.SELL]

        decision_dates = raw_slice["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
        decision_locks = sorted(set(decision_dates) & lock_date_set)

        fill_locks: list[tuple[str, str, float]] = []
        for f in ev.fills:
            d = pd.Timestamp(f.ts).tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
            if d in lock_date_set:
                fill_locks.append((d, f.side.value, f.price))

        results.append(FoldResult(
            symbol=symbol,
            fold=k + 1,
            regime=regimes[k].regime.label,
            n_bars=len(test_idx),
            annual_return=report.annual_return,
            sharpe=report.sharpe,
            n_long_bars=int((sig_slice == 1).sum()),
            tripped_days=len(ev.tripped_days),
            buy_fee_bps=buy_bps,
            sell_fee_bps=sell_bps,
            decision_bar_locks=decision_locks,
            fill_bar_locks=fill_locks,
        ))
    return results


def _check_m1(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("M1：不對稱成本是否確實生效（所有 Fill 的 fee/notional 有效費率）")
    print("=" * 100)
    all_buy = [r for fr in all_folds for r in fr.buy_fee_bps]
    all_sell = [r for fr in all_folds for r in fr.sell_fee_bps]
    buy_dev = max((abs(r - 14.25) for r in all_buy), default=0.0)
    sell_dev = max((abs(r - 44.25) for r in all_sell), default=0.0)
    ok = buy_dev < 1e-6 and sell_dev < 1e-6
    print(f"BUY 筆數={len(all_buy)}  最大偏差={buy_dev:.2e}bps（期望 14.25bps）")
    print(f"SELL 筆數={len(all_sell)}  最大偏差={sell_dev:.2e}bps（期望 44.25bps）")
    print(f"M1 判定：{'PASS' if ok else 'FAIL'}")
    print()


def _check_m2(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("M2：熔斷觸發密度非零也非全觸發（60 個 fold-案例，健康檢查）")
    print("=" * 100)
    n_total = len(all_folds)
    n_tripped = sum(1 for fr in all_folds if fr.tripped_days > 0)
    ok = 0 < n_tripped < n_total
    print(f"總 fold-案例數={n_total}  有觸發熔斷的 fold 數={n_tripped}")
    for fr in all_folds:
        if fr.tripped_days > 0:
            print(f"    {fr.symbol} fold{fr.fold}（{fr.regime}）：tripped_days={fr.tripped_days}")
    print(f"M2 判定：{'PASS' if ok else 'FAIL'}")
    print()


def _check_m3(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("M3：漲跌停曝險清點（診斷性，不修正——完整列出受影響的 fold）")
    print("=" * 100)
    any_decision = [fr for fr in all_folds if fr.decision_bar_locks]
    any_fill = [fr for fr in all_folds if fr.fill_bar_locks]
    print(f"決策 bar 落在真實鎖死日的 fold 數：{len(any_decision)} / {len(all_folds)}")
    for fr in any_decision:
        print(f"    {fr.symbol} fold{fr.fold}（{fr.regime}）：{fr.decision_bar_locks}")
    print(f"實際成交 bar 落在真實鎖死日的 fold 數：{len(any_fill)} / {len(all_folds)}")
    for fr in any_fill:
        for d, side, price in fr.fill_bar_locks:
            print(f"    {fr.symbol} fold{fr.fold}（{fr.regime}）：{d}  {side}  price={price:.2f}")
    print()


def _check_r1(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("R1：年化報酬/Sharpe 不出現 NaN 或發散值")
    print("=" * 100)
    bad = [
        fr for fr in all_folds
        if not np.isfinite(fr.annual_return) or not np.isfinite(fr.sharpe)
        or abs(fr.annual_return) > 50.0
    ]
    ok = len(bad) == 0
    print(f"異常 fold 數：{len(bad)} / {len(all_folds)}")
    for fr in bad:
        print(f"    {fr.symbol} fold{fr.fold}：annual_return={fr.annual_return} sharpe={fr.sharpe}")
    print(f"R1 判定：{'PASS' if ok else 'FAIL'}")
    print()


def _check_r2(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("R2：regime filter 確實生效（trend_up 多頭曝險佔比應高於 range/trend_down）")
    print("=" * 100)
    trend_up = [fr.n_long_bars / fr.n_bars for fr in all_folds if fr.regime == "trend_up"]
    other = [fr.n_long_bars / fr.n_bars for fr in all_folds if fr.regime != "trend_up"]
    mean_up = float(np.mean(trend_up)) if trend_up else float("nan")
    mean_other = float(np.mean(other)) if other else float("nan")
    ok = np.isfinite(mean_up) and np.isfinite(mean_other) and mean_up > mean_other
    print(f"trend_up fold 數={len(trend_up)}  平均多頭曝險佔比={mean_up:.1%}")
    print(f"非 trend_up fold 數={len(other)}  平均多頭曝險佔比={mean_other:.1%}")
    print(f"R2 判定：{'PASS' if ok else 'FAIL'}")
    print()


def _print_baseline_table(all_folds: list[FoldResult]) -> None:
    print("=" * 100)
    print("台股 E2 化第一輪基準：4 檔 × 15-fold 年化報酬/Sharpe")
    print("=" * 100)
    for symbol in SYMBOLS:
        rows = [fr for fr in all_folds if fr.symbol == symbol]
        print(f"[{symbol}]")
        for fr in rows:
            print(
                f"    fold{fr.fold:>2} {fr.regime:>11}  "
                f"年化={fr.annual_return:>+8.1%}  sharpe={fr.sharpe:>+6.2f}  "
                f"多頭bar={fr.n_long_bars:>4}/{fr.n_bars:<4}  熔斷日={fr.tripped_days}"
            )
        rets = np.array([fr.annual_return for fr in rows])
        print(f"    mean={rets.mean():+.1%}  std={rets.std(ddof=1):.1%}")
        print()


def main() -> None:
    all_folds: list[FoldResult] = []
    for symbol, exchange in SYMBOLS.items():
        print(f"跑 {symbol}（{exchange}）...")
        all_folds.extend(run_symbol_e2(symbol, exchange, costs=COSTS))

    _check_m1(all_folds)
    _check_m2(all_folds)
    _check_m3(all_folds)
    _check_r1(all_folds)
    _check_r2(all_folds)
    _print_baseline_table(all_folds)


if __name__ == "__main__":
    main()
