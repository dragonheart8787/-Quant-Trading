"""FinMind provider 的重試/節流/錯誤路徑測試（全部 mock HTTP，不打真 API）。

重點：402 是配額用盡（長等待路徑，非指數退避）、5xx 退避重試、
其他 4xx 立即失敗、token 只在有設定時附上。
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from data.providers import finmind


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "msg": "success",
            "status": 200,
            "data": [{"date": "2024-01-02", "stock_id": "2330", "name": "Foreign_Investor",
                      "buy": 100, "sell": 40}],
        }
        self.text = text

    def json(self):
        return self._payload


def _stub_settings(token=None, retries=3, quota_wait=123.0):
    return SimpleNamespace(
        finmind_base_url="https://fake.test/api/v4/data",
        finmind_token=token,
        finmind_min_request_interval_sec=0.0,
        finmind_quota_wait_sec=quota_wait,
        http_timeout_sec=1.0,
        http_max_retries=retries,
    )


@pytest.fixture
def http(monkeypatch):
    """把 requests.get 換成回應佇列，並記錄 params 與 sleep 呼叫。"""
    state = SimpleNamespace(queue=[], calls=[], sleeps=[])

    def fake_get(url, params=None, timeout=None):
        state.calls.append({"url": url, "params": dict(params or {})})
        if not state.queue:
            raise AssertionError("回應佇列已空，測試設定的回應數不足")
        item = state.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(finmind.requests, "get", fake_get)
    monkeypatch.setattr(finmind.time, "sleep", lambda s: state.sleeps.append(s))
    monkeypatch.setattr(finmind, "settings", _stub_settings())
    state.set_settings = lambda **kw: monkeypatch.setattr(finmind, "settings", _stub_settings(**kw))
    return state


def test_fetch_dataset_success_and_params(http):
    http.queue = [_FakeResp()]
    df = finmind.fetch_dataset(finmind.DATASET_INSTITUTIONAL, data_id="2330")

    assert len(df) == 1
    assert df.attrs["dataset"] == finmind.DATASET_INSTITUTIONAL
    assert df.attrs["source"] == "finmind"
    assert df.attrs["stock_id"] == "2330"

    params = http.calls[0]["params"]
    assert params["dataset"] == finmind.DATASET_INSTITUTIONAL
    assert params["data_id"] == "2330"
    # start_date 未指定 → 用官方起始日抓全歷史
    assert params["start_date"] == finmind.DATASET_START_DATES[finmind.DATASET_INSTITUTIONAL]
    # 無 token 時不可出現 token 參數
    assert "token" not in params


def test_token_included_when_configured(http):
    http.set_settings(token="secret-token")
    http.queue = [_FakeResp()]
    finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330", start_date="2024-01-01")
    assert http.calls[0]["params"]["token"] == "secret-token"


def test_explicit_dates_passed_through(http):
    http.queue = [_FakeResp()]
    finmind.fetch_dataset(
        finmind.DATASET_MARGIN, data_id="2330",
        start_date="2024-01-01", end_date="2024-06-30",
    )
    params = http.calls[0]["params"]
    assert params["start_date"] == "2024-01-01"
    assert params["end_date"] == "2024-06-30"


def test_quota_402_waits_then_succeeds(http):
    http.queue = [_FakeResp(status_code=402), _FakeResp()]
    df = finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330")
    assert len(df) == 1
    # 402 走長等待（quota_wait 秒），不是指數退避
    assert 123.0 in http.sleeps


def test_quota_402_exhausted_raises(http):
    http.queue = [_FakeResp(status_code=402)] * 3  # _QUOTA_MAX_WAITS=2 → 第三次拋出
    with pytest.raises(finmind.FinMindQuotaError):
        finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330")
    assert http.sleeps.count(123.0) == finmind._QUOTA_MAX_WAITS


def test_5xx_backoff_then_success(http):
    http.queue = [_FakeResp(status_code=500), _FakeResp()]
    df = finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330")
    assert len(df) == 1
    assert len(http.calls) == 2


def test_4xx_fails_immediately(http):
    http.queue = [_FakeResp(status_code=404, text="not found")]
    with pytest.raises(finmind.FinMindAPIError):
        finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330")
    assert len(http.calls) == 1  # 不重試


def test_inner_status_error_raises(http):
    http.queue = [_FakeResp(payload={"msg": "bad dataset", "status": 400, "data": []})]
    with pytest.raises(finmind.FinMindAPIError, match="bad dataset"):
        finmind.fetch_dataset("NoSuchDataset")


def test_retries_exhausted_raises(http):
    http.set_settings(retries=2)
    http.queue = [_FakeResp(status_code=500)] * 2
    with pytest.raises(finmind.FinMindAPIError, match="after 2 retries"):
        finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="2330")


def test_empty_data_returns_empty_df(http):
    http.queue = [_FakeResp(payload={"msg": "success", "status": 200, "data": []})]
    df = finmind.fetch_dataset(finmind.DATASET_MARGIN, data_id="9999")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert df.attrs["dataset"] == finmind.DATASET_MARGIN
