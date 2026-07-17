"""Phase 1 垂直切片 end-to-end 執行腳本。

流程：抓 Binance BTCUSDT 1h K 線 → 清洗成 canonical schema →
MA/RSI 策略產生訊號 → 向量化回測（shift(1) 防未來函數）→ 績效報表，
並示範用 RiskManager 計算倉位、PaperBroker 模擬一筆成交。

執行：  python run_phase1.py
"""

from __future__ import annotations

from datetime import date

from backtest.report import build_report
from backtest.vector_engine import run_backtest
from broker.paper import PaperBroker, Side
from data.cleaning import to_canonical_from_binance, validate_canonical
from data.providers.binance import fetch_klines
from risk.manager import RiskConfig, RiskManager
from strategy.ma_rsi import MaRsiStrategy

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 1500          # 約 62 天的 1h K 線（會自動分頁）
FEE_BPS = 5.0         # 單邊 0.05%
INITIAL_EQUITY = 100_000.0


def main() -> None:
    print(f"[1/5] 抓取 {SYMBOL} {INTERVAL} K 線 (limit={LIMIT}) ...")
    raw = fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=LIMIT)
    print(f"      原始筆數: {len(raw)}")

    print("[2/5] 清洗成 canonical schema ...")
    data = to_canonical_from_binance(raw)
    validate_canonical(data)
    print(f"      canonical 筆數: {len(data)}")
    print(f"      區間: {data['ts'].iloc[0]}  →  {data['ts'].iloc[-1]}")

    print("[3/5] MA/RSI 策略產生訊號 ...")
    strat = MaRsiStrategy(fast_window=20, slow_window=60, rsi_window=14, rsi_overbought=70)
    signal = strat.generate_signals(data)
    print(f"      多單根數: {(signal == 1).sum()} / {len(signal)}")

    print("[4/5] 向量化回測（signal.shift(1)）...")
    data_idx = data.set_index("ts")
    signal_idx = signal.copy()
    signal_idx.index = data_idx.index
    result = run_backtest(data_idx[["close"]], signal_idx, fee_bps=FEE_BPS)

    print("[5/5] 績效報表 ...")
    report = build_report(result)
    print("\n===== 策略績效 (MA/RSI) =====")
    print(report.pretty())

    # 買進持有基準對照
    bh_signal = signal_idx.copy()
    bh_signal[:] = 1
    bh_result = run_backtest(data_idx[["close"]], bh_signal, fee_bps=FEE_BPS)
    bh_report = build_report(bh_result)
    print("\n===== 基準：買進持有 (Buy & Hold) =====")
    print(bh_report.pretty())

    # 風控 + paper broker 示範
    print("\n===== 風控 / Paper Broker 示範 =====")
    last_price = float(data["close"].iloc[-1])
    rm = RiskManager(equity=INITIAL_EQUITY, config=RiskConfig(risk_per_trade=0.01, max_leverage=1.0))
    stop = last_price * 0.97  # 3% 停損
    sizing = rm.position_size(entry_price=last_price, stop_price=stop)
    today = date.today()
    decision = rm.approve_order(sizing.quantity, last_price, today)
    print(f"最新價: {last_price:,.2f}  停損: {stop:,.2f}")
    print(f"建議數量: {sizing.quantity:.6f}  名目: {sizing.notional:,.2f}  風險金額: {sizing.risk_amount:,.2f}")
    print(f"風控核可: {decision.value}")

    broker = PaperBroker(cash=INITIAL_EQUITY, fee_bps=FEE_BPS)
    if decision.value == "approved":
        fill = broker.market_order(SYMBOL, Side.BUY, sizing.quantity, last_price)
        print(f"模擬成交: 買 {fill.quantity:.6f} @ {fill.price:,.2f}  手續費 {fill.fee:.2f}")
        print(f"成交後現金: {broker.cash:,.2f}  權益(市價): {broker.equity({SYMBOL: last_price}):,.2f}")


if __name__ == "__main__":
    main()
