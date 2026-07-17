"""Binance 公開 REST API 資料來源 adapter。

Phase 1 只實作公開 K 線 (klines) 抓取，不需要簽名。
依硬性工程規範對 429/418 實作 exponential backoff + 重試，
不裸呼叫後讓例外往上炸。

回傳的是 Binance 原始格式的 DataFrame，canonical schema 轉換交給 data/cleaning.py。
"""

from __future__ import annotations

import time
from typing import Final

import pandas as pd
import requests

from config.settings import settings

_KLINES_ENDPOINT: Final = "/api/v3/klines"
_MAX_LIMIT: Final = 1000  # Binance 單次上限

# Binance klines 原始欄位順序
_RAW_COLUMNS: Final = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "num_trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


class BinanceError(RuntimeError):
    """Binance API 呼叫失敗（重試耗盡後）。"""


def _request_with_backoff(url: str, params: dict) -> list:
    """對 Binance 發送 GET，遇到 429/418/5xx 做 exponential backoff 重試。"""
    last_exc: Exception | None = None
    for attempt in range(settings.http_max_retries):
        try:
            resp = requests.get(url, params=params, timeout=settings.http_timeout_sec)
        except requests.RequestException as exc:  # 連線層級錯誤
            last_exc = exc
            wait = min(2 ** attempt, 30)
            time.sleep(wait)
            continue

        # 429 = rate limit, 418 = IP banned, 5xx = server error → 退避重試
        if resp.status_code in (429, 418) or resp.status_code >= 500:
            # 尊重 Retry-After（若有），否則指數退避
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else min(2 ** attempt, 30)
            last_exc = BinanceError(f"HTTP {resp.status_code} from Binance")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            raise BinanceError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        return resp.json()

    raise BinanceError(
        f"Binance request failed after {settings.http_max_retries} retries: {last_exc}"
    )


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 1000,
) -> pd.DataFrame:
    """抓取最近 `limit` 根 K 線。

    若 limit > 1000，會自動分頁往前抓並串接（用 endTime 游標）。
    回傳 Binance 原始欄位的 DataFrame（尚未轉 canonical schema）。
    """
    if limit <= 0:
        raise ValueError("limit 必須為正整數")

    url = settings.binance_base_url + _KLINES_ENDPOINT
    remaining = limit
    end_time: int | None = None
    chunks: list[list] = []

    while remaining > 0:
        batch = min(remaining, _MAX_LIMIT)
        params: dict = {"symbol": symbol, "interval": interval, "limit": batch}
        if end_time is not None:
            params["endTime"] = end_time

        raw = _request_with_backoff(url, params)
        if not raw:
            break

        chunks.append(raw)
        # 往更早的時間分頁：下一批的 endTime = 這批最早一根的 open_time - 1ms
        earliest_open = raw[0][0]
        end_time = int(earliest_open) - 1
        remaining -= len(raw)

        if len(raw) < batch:
            break  # 已抓到歷史最前端

    if not chunks:
        raise BinanceError(f"No kline data returned for {symbol} {interval}")

    # chunks 是由近到遠的分頁，攤平後依時間排序去重
    flat = [row for chunk in chunks for row in chunk]
    df = pd.DataFrame(flat, columns=_RAW_COLUMNS)
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)

    # 附加查詢上下文，供 cleaning 階段填 canonical 欄位
    df.attrs["symbol"] = symbol
    df.attrs["interval"] = interval
    df.attrs["source"] = "binance"
    df.attrs["exchange"] = "BINANCE"
    return df
