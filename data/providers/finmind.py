"""FinMind 台股籌碼資料來源 adapter。

FinMind 是小時配額制（註冊 token 600 req/hr、無 token 300 req/hr），
配額用盡回 HTTP 402（不是 429）。與 binance.py 的被動退避不同，這裡以
「主動節流」（相鄰 request 強制最小間隔）為主、402 長等待為輔——
402 代表整個小時窗的配額用盡，短退避沒有意義。

單一 request 可拉整段日期範圍（無分頁），請求數 ≈ 檔數 × 資料集數。

回傳 FinMind 原始欄位的 DataFrame，canonical schema 轉換交給 data/cleaning.py。
官方文件：https://finmind.github.io/tutor/TaiwanMarket/Chip/（2026-07 查證）
"""

from __future__ import annotations

import time
from typing import Final, Optional

import pandas as pd
import requests

from config.settings import settings

# ---- 資料集名稱（dataset 參數值）----
DATASET_INSTITUTIONAL: Final = "TaiwanStockInstitutionalInvestorsBuySell"  # 三大法人買賣超（長表）
DATASET_MARGIN: Final = "TaiwanStockMarginPurchaseShortSale"  # 融資融券
DATASET_SHAREHOLDING: Final = "TaiwanStockShareholding"  # 外資持股
DATASET_HOLDING_DISPERSION: Final = "TaiwanStockHoldingSharesPer"  # 集保股權分散（週頻）
DATASET_STOCK_INFO: Final = "TaiwanStockInfo"  # 全市場股票清單（產業別/上市上櫃）
DATASET_DIVIDEND_RESULT: Final = "TaiwanStockDividendResult"  # 除權息結果（POC 恆等式對照用）
DATASET_PRICE: Final = "TaiwanStockPrice"  # 台股日K（上市/上櫃/興櫃同表；日/high=max/low=min）

# 各資料集的官方資料起始日（省略 start_date 時抓全歷史用）
DATASET_START_DATES: Final = {
    DATASET_INSTITUTIONAL: "2005-01-01",
    DATASET_MARGIN: "2001-01-01",
    DATASET_SHAREHOLDING: "2004-02-01",
    DATASET_HOLDING_DISPERSION: "2010-01-29",
    DATASET_DIVIDEND_RESULT: "2001-01-01",
    DATASET_PRICE: "1994-10-01",
}

_QUOTA_MAX_WAITS: Final = 2  # 402 長等待重試上限


class FinMindQuotaError(RuntimeError):
    """HTTP 402：小時配額用盡，且長等待重試後仍未恢復。"""


class FinMindAPIError(RuntimeError):
    """FinMind API 呼叫失敗（非配額問題；含重試耗盡）。"""


_last_request_at: float = 0.0


def _throttle() -> None:
    """主動節流：確保相鄰 request 間隔 >= finmind_min_request_interval_sec。"""
    global _last_request_at
    interval = settings.finmind_min_request_interval_sec
    wait = _last_request_at + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _request_with_backoff(params: dict) -> list:
    """對 FinMind 發送 GET。402 → 長等待；5xx/連線錯誤 → 指數退避；其他 4xx → 直接拋。"""
    last_exc: Exception | None = None
    quota_waits = 0
    attempt = 0
    while attempt < settings.http_max_retries:
        _throttle()
        try:
            resp = requests.get(
                settings.finmind_base_url, params=params, timeout=settings.http_timeout_sec
            )
        except requests.RequestException as exc:  # 連線層級錯誤
            last_exc = exc
            time.sleep(min(2**attempt, 30))
            attempt += 1
            continue

        if resp.status_code == 402:
            # 小時配額用盡：長等待（不消耗一般 attempt），最多 _QUOTA_MAX_WAITS 次
            if quota_waits >= _QUOTA_MAX_WAITS:
                raise FinMindQuotaError(
                    f"FinMind 配額用盡（HTTP 402），等待 {quota_waits} 次後仍未恢復"
                )
            quota_waits += 1
            time.sleep(settings.finmind_quota_wait_sec)
            continue

        if resp.status_code >= 500:
            last_exc = FinMindAPIError(f"HTTP {resp.status_code} from FinMind")
            time.sleep(min(2**attempt, 30))
            attempt += 1
            continue

        if resp.status_code != 200:
            raise FinMindAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        js = resp.json()
        # FinMind 在 HTTP 200 內另有自己的 status 欄位
        if js.get("status") != 200:
            raise FinMindAPIError(f"FinMind status {js.get('status')}: {js.get('msg')}")
        return js.get("data", [])

    raise FinMindAPIError(
        f"FinMind request failed after {settings.http_max_retries} retries: {last_exc}"
    )


def fetch_dataset(
    dataset: str,
    data_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """抓取單一資料集，回傳 FinMind 原始欄位的 DataFrame（可為空表）。

    - start_date 省略時使用該資料集的官方起始日（= 抓全歷史）。
    - token 只在環境變數有設定時附上（無 token 為 300 req/hr 公開配額）。
    - 查詢上下文附在 df.attrs，供 cleaning 階段使用。
    """
    params: dict = {"dataset": dataset}
    if data_id is not None:
        params["data_id"] = data_id
    if start_date is None:
        start_date = DATASET_START_DATES.get(dataset)
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if settings.finmind_token:
        params["token"] = settings.finmind_token

    data = _request_with_backoff(params)
    df = pd.DataFrame(data)
    df.attrs["dataset"] = dataset
    df.attrs["source"] = "finmind"
    if data_id is not None:
        df.attrs["stock_id"] = data_id
    return df


def fetch_institutional(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（個股、長表：date, stock_id, name, buy, sell）。"""
    return fetch_dataset(DATASET_INSTITUTIONAL, stock_id, start_date, end_date)


def fetch_margin_short(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """融資融券（個股，寬表 14 數值欄）。"""
    return fetch_dataset(DATASET_MARGIN, stock_id, start_date, end_date)


def fetch_shareholding(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """外資持股表（個股）。"""
    return fetch_dataset(DATASET_SHAREHOLDING, stock_id, start_date, end_date)


def fetch_holding_dispersion(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """集保股權分散表（個股、週頻、每 date × 持股級距一列）。"""
    return fetch_dataset(DATASET_HOLDING_DISPERSION, stock_id, start_date, end_date)


def fetch_stock_info() -> pd.DataFrame:
    """全市場股票清單（stock_id, stock_name, industry_category, type）。一次抓回本地過濾。"""
    return fetch_dataset(DATASET_STOCK_INFO)


def fetch_price(
    stock_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    exchange: str = "TWSE",
) -> pd.DataFrame:
    """台股日K（個股，寬表：date, stock_id, open, max, min, close,
    Trading_Volume, Trading_money, spread, Trading_turnover）。

    籌碼是「交易日」概念、無盤中時間；日K 有 OHLCV，屬 K 線 canonical schema
    （非籌碼 schema）。canonical 轉換交給 data/cleaning.finmind_price_to_canonical。
    exchange 由呼叫端提供（TaiwanStockPrice 本身不含市場別；TWSE 上市 / TPEx 上櫃），
    寫入 df.attrs 供 cleaning 填 canonical 的 exchange 欄（比照 binance.py 慣例）。
    """
    df = fetch_dataset(DATASET_PRICE, stock_id, start_date, end_date)
    df.attrs["exchange"] = exchange
    return df
