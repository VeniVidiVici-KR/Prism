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
from valley.toss import (
    TossDataError,
    get_bond_yield_history,
    get_bond_yield_snapshot,
    get_credit_trades_history,
    get_institution_breakdown_history,
    get_investor_trading_history,
    get_securities_lending_history,
    get_short_selling_history,
)


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
    """days가 한 페이지(최대 100, 실측 확인)를 넘으면 nextUntil 커서로 이어서 조회해야 한다.
    ⚠️ 리스크지표 라운드에서 _fetch_investor_trading_records를 범용 _fetch_paginated_records로
    일반화했다(investor-trading/short-selling/credit-trades/securities-lending 4개 엔드포인트가
    전부 동일한 페이지네이션 구조를 공유함을 실측 확인했기 때문)."""
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
        records = toss_mod._fetch_paginated_records("/stocks/005930/investor-trading", days=3)
    assert len(records) == 3
    assert mock_get.call_count == 2
    second_call_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_call_params["until"] == "2026-08-11"


# ── 종목 리스크 지표(공매도·신용·대차) — 2026-08 라운드에서 추가. 고정 데이터는 실측
# (005930)으로 확인한 실제 응답 구조를 그대로 재현한다. ──────────────────────────────

_SHORT_SELLING_PAGE = {
    "result": {
        "nextUntil": "2026-07-29",
        "records": [
            {
                "date": "2026-08-12",
                "shortSellingVolume": "1984289",
                "shortSellingAmount": "502596237000",
                "shortSellingVolumeRate": "0.07321",
                "shortSellingAmountRate": "0.0732",
            },
            {
                # 실측 확인된 케이스 — 비중 필드가 간헐적으로 null(거래량/거래대금은 항상 존재).
                "date": "2026-07-30",
                "shortSellingVolume": "2546783",
                "shortSellingAmount": "536397250750",
                "shortSellingVolumeRate": None,
                "shortSellingAmountRate": None,
            },
        ],
    }
}

_CREDIT_TRADES_PAGE = {
    "result": {
        "nextUntil": "2026-07-29",
        "records": [
            {
                "date": "2026-08-12",
                "marginLoan": {
                    "newQuantity": "2309763",
                    "returnQuantity": "1314116",
                    "balanceQuantity": "24280842",
                    "balanceRate": "0.004",
                    "tradingRate": "0.1414",
                },
                "stockLoan": {
                    "newQuantity": "1481",
                    "returnQuantity": "1880",
                    "balanceQuantity": "7359",
                    "balanceRate": "0",
                    "tradingRate": "0",
                },
            },
            {
                "date": "2026-08-11",
                "marginLoan": {
                    "newQuantity": "1608980",
                    "returnQuantity": "2115700",
                    "balanceQuantity": "23308543",
                    "balanceRate": "0.0039",
                    "tradingRate": "0.0782",
                },
                "stockLoan": {
                    "newQuantity": "4705",
                    "returnQuantity": "4833",
                    "balanceQuantity": "7758",
                    "balanceRate": "0",
                    "tradingRate": "0.0001",
                },
            },
        ],
    }
}

_SECURITIES_LENDING_PAGE = {
    "result": {
        "nextUntil": "2026-07-29",
        "records": [
            {
                "date": "2026-08-12",
                "executionQuantity": "799209",
                "repaymentQuantity": "972343",
                "balanceQuantity": "89405665",
                "balanceAmount": "22843147407500",
            },
            {
                "date": "2026-08-11",
                "executionQuantity": "378424",
                "repaymentQuantity": "471320",
                "balanceQuantity": "89578799",
                "balanceAmount": "21454122360500",
            },
        ],
    }
}


def _patched_toss(response_json):
    return (
        patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)),
        patch("valley.toss.requests.get", return_value=_mock_response(200, response_json)),
    )


def test_get_short_selling_history_parses_volume_amount_and_rate(monkeypatch):
    """⚠️ 핵심 회귀 — 거래량(주)/거래대금(원)/비중(0~1 소수, 실측 대조로 '당일 전체
    거래량·거래대금 대비 비중'임을 확인) 필드가 그대로 와야 하고, 비중이 null인 날(실측
    확인된 케이스)은 NaN으로 남아야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    p1, p2 = _patched_toss(_SHORT_SELLING_PAGE)
    with p1, p2:
        df = get_short_selling_history("005930", days=2)

    assert list(df.columns) == ["거래량", "거래대금", "거래량비중", "거래대금비중"]
    latest = df.iloc[-1]  # 2026-08-12
    assert latest["거래량"] == 1984289.0
    assert latest["거래대금"] == 502596237000.0
    assert latest["거래량비중"] == pytest.approx(0.07321)

    null_day = df.iloc[0]  # 2026-07-30, 비중 null
    assert null_day["거래량"] == 2546783.0  # 거래량 자체는 존재
    assert pd.isna(null_day["거래량비중"])
    assert pd.isna(null_day["거래대금비중"])


def test_get_credit_trades_history_separates_margin_and_stock_loan(monkeypatch):
    """⚠️ 핵심 회귀 — 융자(marginLoan)와 대주(stockLoan)는 반대 성격의 상품인데 같은
    엔드포인트에 오므로, 절대 하나로 합치지 않고 별도 컬럼으로 유지해야 한다. balanceRate는
    실측 대조로 확인한 공여율(잔고/상장주식수) 그대로 통과시킨다(재계산하지 않음)."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    p1, p2 = _patched_toss(_CREDIT_TRADES_PAGE)
    with p1, p2:
        df = get_credit_trades_history("005930", days=2)

    assert list(df.columns) == ["융자잔고", "융자공여율", "대주잔고", "대주공여율"]
    latest = df.iloc[-1]  # 2026-08-12
    assert latest["융자잔고"] == 24280842.0
    assert latest["융자공여율"] == pytest.approx(0.004)
    assert latest["대주잔고"] == 7359.0
    assert latest["대주공여율"] == 0.0
    # tradingRate는 정확한 산식을 실측으로 확정 못해 이 함수가 노출하지 않는다(추측 방지).
    assert "융자tradingRate" not in df.columns
    assert "tradingRate" not in df.columns


def test_get_securities_lending_history_parses_execution_repayment_balance(monkeypatch):
    """⚠️ 핵심 회귀 — 체결/상환/잔고수량(주) + 잔고금액(원, 실측 대조로 잔고수량×종가와
    정확히 일치함을 확인한 값). 단위 혼동 없이 그대로 통과해야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    p1, p2 = _patched_toss(_SECURITIES_LENDING_PAGE)
    with p1, p2:
        df = get_securities_lending_history("005930", days=2)

    assert list(df.columns) == ["체결수량", "상환수량", "잔고수량", "잔고금액"]
    latest = df.iloc[-1]  # 2026-08-12
    assert latest["체결수량"] == 799209.0
    assert latest["상환수량"] == 972343.0
    assert latest["잔고수량"] == 89405665.0
    assert latest["잔고금액"] == 22843147407500.0


def test_risk_indicator_functions_reuse_shared_request_and_pagination(monkeypatch):
    """⚠️ 요구사항 — 세 함수 다 토큰 캐싱/401·429 재시도 로직을 새로 만들지 않고 기존
    _request()/_fetch_paginated_records()를 그대로 재사용해야 한다. 토큰 발급이 함수마다
    따로 일어나면 안 된다(같은 프로세스 내 재사용 확인)."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)) as mock_post, \
         patch(
             "valley.toss.requests.get",
             side_effect=[
                 _mock_response(200, _SHORT_SELLING_PAGE),
                 _mock_response(200, _CREDIT_TRADES_PAGE),
                 _mock_response(200, _SECURITIES_LENDING_PAGE),
             ],
         ):
        get_short_selling_history("005930", days=2)
        get_credit_trades_history("005930", days=2)
        get_securities_lending_history("005930", days=2)
    assert mock_post.call_count == 1  # 3개 지표를 조회해도 토큰 발급은 1회뿐


# ── 한국 국채(수익률곡선) — 2026-08 국채 라운드에서 추가. 고정 데이터는 실측(KR_BOND_10Y)으로
# 확인한 실제 응답 구조를 그대로 재현한다. ──────────────────────────────────────────

_CANDLES_PAGE1 = {
    "result": {
        "candles": [
            {
                "timestamp": "2026-08-12T00:00:00.000+09:00",
                "openPrice": "4.29",
                "highPrice": "4.319",
                "lowPrice": "4.288",
                "closePrice": "4.295",
                "volume": "509000000000",
            },
            {
                "timestamp": "2026-08-11T00:00:00.000+09:00",
                "openPrice": "4.295",
                "highPrice": "4.306",
                "lowPrice": "4.27",
                "closePrice": "4.303",
                "volume": "480000000000",
            },
        ],
        "nextBefore": "2026-08-11T00:00:00.000+09:00",
    }
}

_CANDLES_PAGE2 = {
    "result": {
        "candles": [
            {
                "timestamp": "2026-08-10T00:00:00.000+09:00",
                "openPrice": "4.24",
                "highPrice": "4.25",
                "lowPrice": "4.22",
                "closePrice": "4.241",
                "volume": "300000000000",
            },
        ],
        "nextBefore": None,
    }
}

_PRICES_ALL_BONDS = {
    "result": [
        {"symbol": "KR_BOND_2Y", "timestamp": None, "lastPrice": "3.63"},
        {"symbol": "KR_BOND_3Y", "timestamp": None, "lastPrice": "3.799"},
        {"symbol": "KR_BOND_5Y", "timestamp": None, "lastPrice": "4.029"},
        {"symbol": "KR_BOND_10Y", "timestamp": None, "lastPrice": "4.295"},
        {"symbol": "KR_BOND_20Y", "timestamp": None, "lastPrice": "4.654"},
        {"symbol": "KR_BOND_30Y", "timestamp": None, "lastPrice": "4.686"},
    ]
}


def test_get_bond_yield_history_parses_close_price_as_percent(monkeypatch):
    """⚠️ 핵심 회귀 — closePrice는 이미 퍼센트(%) 값 그대로 온다(실측 확인, FRED 지수 계열과
    달리 변환 불필요). 값 자체를 100배/소수 변환 없이 그대로 통과시켜야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", return_value=_mock_response(200, _CANDLES_PAGE1)):
        series = get_bond_yield_history("KR_BOND_10Y", days=2)

    assert len(series) == 2
    assert series.iloc[-1] == pytest.approx(4.295)  # 2026-08-12 종가, 소수/퍼센트 변환 없음
    assert series.index[-1] == pd.Timestamp("2026-08-12")


def test_get_bond_yield_history_paginates_with_before_cursor(monkeypatch):
    """⚠️ 핵심 회귀 — candles는 investor-trading류(until/nextUntil)와 커서 파라미터명이
    다르다(실측 확인: before/nextBefore). 페이지네이션이 올바른 파라미터명을 쓰는지 확인한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch(
             "valley.toss.requests.get",
             side_effect=[_mock_response(200, _CANDLES_PAGE1), _mock_response(200, _CANDLES_PAGE2)],
         ) as mock_get:
        series = get_bond_yield_history("KR_BOND_10Y", days=3)

    assert len(series) == 3
    assert series.iloc[0] == pytest.approx(4.241)  # 가장 오래된(2026-08-10) 값
    second_call_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_call_params["before"] == "2026-08-11T00:00:00.000+09:00"
    assert second_call_params["interval"] == "1d"  # interval 파라미터 필수(실측 확인)


def test_get_bond_yield_snapshot_returns_all_symbols_in_one_call(monkeypatch):
    """⚠️ 핵심 회귀 — market-indicators/prices는 콤마로 구분한 심볼을 한 번에 조회할 수 있다
    (실측 확인) — 6개 심볼을 심볼당 1회씩 6번 부르지 않고 1번만 불러야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)), \
         patch("valley.toss.requests.get", return_value=_mock_response(200, _PRICES_ALL_BONDS)) as mock_get:
        snapshot = get_bond_yield_snapshot()

    assert mock_get.call_count == 1
    assert snapshot == {
        "KR_BOND_2Y": 3.63,
        "KR_BOND_3Y": 3.799,
        "KR_BOND_5Y": 4.029,
        "KR_BOND_10Y": 4.295,
        "KR_BOND_20Y": 4.654,
        "KR_BOND_30Y": 4.686,
    }
    # 만기가 길수록 수익률이 높은 정상(우상향) 곡선인지 — 상식성 확인
    ordered = [snapshot[k] for k in toss_mod.KR_BOND_SYMBOLS]
    assert ordered == sorted(ordered)


def test_bond_functions_reuse_shared_request_no_new_auth_logic(monkeypatch):
    """⚠️ 요구사항 — 국채 함수들도 토큰 캐싱을 새로 만들지 않고 기존 _request()를 재사용해야
    한다(토큰 발급은 1회만)."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "dummy-secret")
    with patch("valley.toss.requests.post", return_value=_mock_response(200, _TOKEN_SUCCESS)) as mock_post, \
         patch(
             "valley.toss.requests.get",
             side_effect=[_mock_response(200, _CANDLES_PAGE1), _mock_response(200, _PRICES_ALL_BONDS)],
         ):
        get_bond_yield_history("KR_BOND_10Y", days=2)
        get_bond_yield_snapshot()
    assert mock_post.call_count == 1
