"""valley/toss.py 단위테스트 — 토스증권 Open API 응답 파싱/토큰 캐싱/에러 처리 (실제 네트워크
없이 mock).

실측(2026-08 라운드)으로 확인한 실제 응답 형태를 그대로 재현한 고정 데이터로 테스트한다:
- 토큰 발급: {"access_token": "...", "token_type": "Bearer", "expires_in": 86399}
- investor-trading: {"result": {"nextUntil": "...", "records": [...]}}, 단위는 거래량(주식 수)
  (buyVolume/sellVolume/netBuyVolume 필드명 그대로 수량임을 실측 확인 — 원화 금액 아님)
- 오류: {"error": {"requestId": "...", "code": "...", "message": "..."}}
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import valley.toss as toss_mod
from valley.toss import TossDataError, get_institution_breakdown_history, get_investor_trading_history


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """토큰 캐시는 모듈 전역 싱글턴이라 테스트 간 상태가 새면 안 된다 — 매 테스트 전에 초기화."""
    toss_mod._token_cache = toss_mod._TokenCache()
    yield
    toss_mod._token_cache = toss_mod._TokenCache()


def _mock_response(status_code=200, json_data=None, headers=None, text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text if text is not None else ""
    if json_data is not None:
        resp.json.return_value = json_data
        if text is None:
            resp.text = str(json_data)
    return resp


_TOKEN_SUCCESS = {"access_token": "dummy.jwt.token", "token_type": "Bearer", "expires_in": 86399}

_TRADING_PAGE1 = {
    "result": {
        "nextUntil": "2026-07-30",
        "records": [
            {
                "date": "2026-08-13",
                "individual": None,
                "foreigner": {"buyVolume": "100", "sellVolume": "40", "netBuyVolume": "60"},
                "institution": {"buyVolume": "50", "sellVolume": "45", "netBuyVolume": "5", "breakdown": None},
            },
            {
                "date": "2026-08-12",
                "individual": {"buyVolume": "10", "sellVolume": "30", "netBuyVolume": "-20"},
                "foreigner": {"buyVolume": "200", "sellVolume": "100", "netBuyVolume": "100"},
                "institution": {
                    "buyVolume": "80",
                    "sellVolume": "60",
                    "netBuyVolume": "20",
                    "breakdown": {
                        "financialInvestment": {"buyVolume": "30", "sellVolume": "10", "netBuyVolume": "20"},
                        "insurance": {"buyVolume": "5", "sellVolume": "5", "netBuyVolume": "0"},
                        "trust": {"buyVolume": "5", "sellVolume": "5", "netBuyVolume": "0"},
                        "privateEquityFund": {"buyVolume": "5", "sellVolume": "5", "netBuyVolume": "0"},
                        "bank": {"buyVolume": "5", "sellVolume": "5", "netBuyVolume": "0"},
                        "otherFinancialInstitution": {"buyVolume": "5", "sellVolume": "5", "netBuyVolume": "0"},
                        "pensionFund": {"buyVolume": "25", "sellVolume": "25", "netBuyVolume": "0"},
                    },
                },
            },
        ],
    }
}


def test_token_refresh_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    with pytest.raises(TossDataError, match="TOSS_CLIENT_ID"):
        toss_mod._token_cache.get()


def test_token_refresh_403_reports_ip_allowlist_hint(monkeypatch):
    """⚠️ 실측 확인된 케이스는 아니지만(테스트 환경 IP가 허용돼 있었음), 라운드 지시대로
    403을 IP 미등록 가능성으로 안내하는지 구조를 확인한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(403)):
        with pytest.raises(TossDataError, match="허용 IP"):
            toss_mod._token_cache.get()


def test_token_refresh_success_and_reuse(monkeypatch):
    """⚠️ AUTH는 초당 5회 제한이라 매 API 호출마다 토큰을 새로 받으면 안 된다 — 여러 번
    get()해도 토큰 발급 POST는 1회만 일어나야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)) as mock_post:
        token1 = toss_mod._token_cache.get()
        token2 = toss_mod._token_cache.get()
        token3 = toss_mod._token_cache.get()
    assert token1 == token2 == token3 == "dummy.jwt.token"
    assert mock_post.call_count == 1


def test_token_invalidate_forces_refresh(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)) as mock_post:
        toss_mod._token_cache.get()
        toss_mod._token_cache.invalidate()
        toss_mod._token_cache.get()
    assert mock_post.call_count == 2


def test_request_retries_once_on_401(monkeypatch):
    """만료된 토큰으로 401을 받으면 토큰을 재발급하고 1회 재시도해야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    responses = [_mock_response(401, {"error": {"code": "invalid-token", "message": "만료"}}), _mock_response(200, _TRADING_PAGE1)]
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", side_effect=responses) as mock_get:
        data = toss_mod._request("/stocks/005930/investor-trading", {"count": 2})
    assert mock_get.call_count == 2
    assert data["result"]["records"][0]["date"] == "2026-08-13"


def test_request_retries_once_on_429_after_waiting_retry_after(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    responses = [
        _mock_response(429, headers={"Retry-After": "2"}),
        _mock_response(200, _TRADING_PAGE1),
    ]
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", side_effect=responses), \
         patch("valley.toss.time.sleep") as mock_sleep:
        data = toss_mod._request("/stocks/005930/investor-trading", {"count": 2})
    mock_sleep.assert_called_once_with(2.0)
    assert data["result"]["records"][0]["date"] == "2026-08-13"


def test_request_unsupported_market_raises_clear_message(monkeypatch):
    """해외 종목(예: AAPL) 조회 시 실측 확인된 unsupported-market 오류를 KR 전용 안내로 변환."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    error_body = '{"error":{"code":"unsupported-market","message":"지원하지 않는 시장의 종목입니다."}}'
    resp = _mock_response(400, text=error_body)
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", return_value=resp):
        with pytest.raises(TossDataError, match="국내\\(KR\\) 종목만"):
            toss_mod._request("/stocks/AAPL/investor-trading", {"count": 1})


def test_get_investor_trading_history_parses_volume_and_handles_null_individual(monkeypatch):
    """⚠️ 핵심 회귀 — 필드는 거래량(주식 수)이지 거래대금이 아니다(단위 변환 없이 그대로).
    조회 당일처럼 individual이 null인 레코드는 NaN으로 남아야 한다(0으로 채우지 않음)."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", return_value=_mock_response(200, _TRADING_PAGE1)):
        df = get_investor_trading_history("005930", days=2)

    assert list(df.columns) == ["외국인", "기관", "개인"]
    assert len(df) == 2
    latest = df.iloc[-1]  # 정렬 후 마지막 = 2026-08-13(개인 null)
    assert latest["외국인"] == 60.0
    assert latest["기관"] == 5.0
    assert pd.isna(latest["개인"])

    prev = df.iloc[0]  # 2026-08-12
    assert prev["개인"] == -20.0
    assert prev["외국인"] == 100.0


def test_get_institution_breakdown_history_labels_and_null_breakdown(monkeypatch):
    """기관 세부분류는 한국어 라벨 7종으로 나와야 하고, breakdown이 null인 날(조회 당일 등)은
    전 항목 NaN이어야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", return_value=_mock_response(200, _TRADING_PAGE1)):
        df = get_institution_breakdown_history("005930", days=2)

    expected_cols = ["금융투자", "보험", "투신", "사모펀드", "은행", "기타금융기관", "연기금"]
    assert list(df.columns) == expected_cols

    null_day = df.iloc[-1]  # 2026-08-13, breakdown=None
    assert null_day.isna().all()

    full_day = df.iloc[0]  # 2026-08-12
    assert full_day["금융투자"] == 20.0
    assert full_day["연기금"] == 0.0


def test_pagination_follows_next_until_cursor(monkeypatch):
    """days가 한 페이지(최대 100, 실측 확인)를 넘으면 nextUntil 커서로 이어서 조회해야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    page1 = {
        "result": {
            "nextUntil": "2026-08-11",
            "records": [{"date": f"2026-08-{13 - i}", "individual": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0"}, "foreigner": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0"}, "institution": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0", "breakdown": None}} for i in range(2)],
        }
    }
    page2 = {
        "result": {
            "nextUntil": None,
            "records": [{"date": "2026-08-11", "individual": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0"}, "foreigner": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0"}, "institution": {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": "0", "breakdown": None}}],
        }
    }
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", side_effect=[_mock_response(200, page1), _mock_response(200, page2)]) as mock_get:
        records = toss_mod._fetch_investor_trading_records("005930", days=3)
    assert len(records) == 3
    assert mock_get.call_count == 2
    second_call_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_call_params["until"] == "2026-08-11"
