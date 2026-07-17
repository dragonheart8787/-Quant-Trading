"""統一的設定與環境變數讀取點。

所有 API key、密碼、DSN 一律從環境變數讀取，絕不寫死在程式碼裡。
Phase 1 只用到 Binance 公開 REST API，不需要金鑰，但仍預先定義讀取點，
之後 Phase 2+ 接私有端點時直接沿用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """全域設定。值來自環境變數，缺省時用安全的研究期預設值。"""

    # Binance 公開端點（Phase 1 只用公開 K 線，不需簽名）
    binance_base_url: str = field(
        default_factory=lambda: _get("BINANCE_BASE_URL", "https://api.binance.com")  # type: ignore[arg-type]
    )
    # 私有金鑰（Phase 1 用不到，預留欄位，永遠從環境變數讀）
    binance_api_key: str | None = field(default_factory=lambda: _get("BINANCE_API_KEY"))
    binance_api_secret: str | None = field(default_factory=lambda: _get("BINANCE_API_SECRET"))

    # FinMind 台股籌碼 API（Phase 3；公開資料無 token=300 req/hr，註冊 token=600 req/hr，
    # 超限回 HTTP 402。token 永遠從環境變數讀，不寫死。）
    finmind_base_url: str = field(
        default_factory=lambda: _get("FINMIND_BASE_URL", "https://api.finmindtrade.com/api/v4/data")  # type: ignore[arg-type]
    )
    finmind_token: str | None = field(default_factory=lambda: _get("FINMIND_TOKEN"))
    # 主動節流間隔：6.5s ≈ 550 req/hr，貼著註冊上限留安全邊際；POC 少量請求可調低
    finmind_min_request_interval_sec: float = field(
        default_factory=lambda: float(_get("FINMIND_MIN_INTERVAL", "6.5"))  # type: ignore[arg-type]
    )
    # 402（小時配額用盡）的長等待秒數；短退避對配額制沒有意義
    finmind_quota_wait_sec: float = field(
        default_factory=lambda: float(_get("FINMIND_QUOTA_WAIT", "900"))  # type: ignore[arg-type]
    )

    # 本地研究資料庫位置
    sqlite_path: str = field(default_factory=lambda: _get("QUANT_SQLITE_PATH", "data/research.db"))  # type: ignore[arg-type]

    # HTTP 行為
    http_timeout_sec: float = field(default_factory=lambda: float(_get("QUANT_HTTP_TIMEOUT", "15")))  # type: ignore[arg-type]
    http_max_retries: int = field(default_factory=lambda: int(_get("QUANT_HTTP_RETRIES", "5")))  # type: ignore[arg-type]


settings = Settings()
