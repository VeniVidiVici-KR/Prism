"""valley/kofia.py 단위테스트 — 공공데이터포털 응답 파싱/에러 처리 (실제 네트워크 없이 mock).

실측(2026-08 라운드)으로 확인한 실제 응답 형태를 그대로 재현한 고정 데이터로 테스트한다:
- 성공: XML, <header><resultCode>00</resultCode></header>
- 인증 오류(키 없음/미등록): HTTP 401/403 + XML이 아닌 별도 JSON 에러 포맷
"""

from unittest.mock import MagicMock, patch

import pytest

from valley.kofia import (
    CREDIT_BALANCE_FIELDS,
    KofiaDataError,
    _get_service_key,
    _parse_items,
    _request,
    get_credit_balance_history,
)

_SUCCESS_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response>
    <header>
        <resultCode>00</resultCode>
        <resultMsg>NORMAL SERVICE.</resultMsg>
    </header>
    <body>
        <numOfRows>2</numOfRows>
        <pageNo>1</pageNo>
        <totalCount>2</totalCount>
        <items>
            <item>
                <basDt>20260807</basDt>
                <crdTrFingWhl>29230368011949</crdTrFingWhl>
                <crdTrFingScrs>23099945976826</crdTrFingScrs>
                <crdTrFingKosdaq>6130422035123</crdTrFingKosdaq>
                <crdTrLndrWhl>27780333687</crdTrLndrWhl>
                <crdTrLndrScrs>24486014917</crdTrLndrScrs>
                <crdTrLndrKosdaq>3294318770</crdTrLndrKosdaq>
                <sbscCapLn>0</sbscCapLn>
                <dpsgScrtMogFing>24415045326904</dpsgScrtMogFing>
            </item>
        </items>
    </body>
</response>
"""

_EMPTY_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response>
    <header>
        <resultCode>00</resultCode>
        <resultMsg>NORMAL SERVICE.</resultMsg>
    </header>
    <body>
        <numOfRows>1</numOfRows>
        <pageNo>1</pageNo>
        <totalCount>0</totalCount>
        <items/>
    </body>
</response>
"""

_LOGICAL_ERROR_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response>
    <header>
        <resultCode>03</resultCode>
        <resultMsg>NODATA_ERROR</resultMsg>
    </header>
</response>
"""


def _mock_response(status_code=200, content=b"", json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8") if content else ""
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_get_service_key_missing_raises(monkeypatch):
    monkeypatch.delenv("KOFIA_API_KEY", raising=False)
    with pytest.raises(KofiaDataError, match="KOFIA_API_KEY"):
        _get_service_key()


def test_get_service_key_decodes_url_encoding(monkeypatch):
    # 실측 확인: 공공데이터포털 서비스키는 이미 URL-인코딩된 형태로 발급된다(예: "+"가 "%2B").
    # ⚠️ 실제 발급키가 아니라 인코딩 패턴만 흉낸 더미 값이다(커밋되는 테스트 파일이라
    # 진짜 키 조각과 겹치지 않는 값을 의도적으로 골랐다).
    monkeypatch.setenv("KOFIA_API_KEY", "dummy%2Bkey%3D%3D")
    assert _get_service_key() == "dummy+key=="


def test_request_parses_success_xml(monkeypatch):
    monkeypatch.setenv("KOFIA_API_KEY", "dummy-key")
    with patch("valley.kofia.requests.get", return_value=_mock_response(200, _SUCCESS_XML)):
        root = _request("getGrantingOfCreditBalanceInfo", {"numOfRows": 2, "pageNo": 1})
    rows = _parse_items(root, CREDIT_BALANCE_FIELDS)
    assert len(rows) == 1
    assert rows[0]["basDt"] == "20260807"  # 날짜는 문자열 그대로 유지
    assert rows[0]["crdTrFingWhl"] == 29230368011949.0  # 금액은 숫자로 변환
    # 전체 = 유가증권 + 코스닥 (실측으로 확인된 내부 정합성)
    assert rows[0]["crdTrFingWhl"] == pytest.approx(
        rows[0]["crdTrFingScrs"] + rows[0]["crdTrFingKosdaq"]
    )


def test_request_raises_on_auth_error_json_format(monkeypatch):
    """⚠️ 인증 오류는 XML이 아니라 별도 JSON 포맷으로 온다(실측 확인) — 성공 응답과
    스키마가 다르므로 분기 처리가 실제로 동작하는지 확인한다."""
    monkeypatch.setenv("KOFIA_API_KEY", "invalid-key")
    error_json = {
        "OpenAPI_ServiceResponse": {
            "cmmMsgHeader": {
                "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
                "returnAuthMsg": "등록되지 않은 서비스키",
                "returnReasonCode": "30",
            }
        }
    }
    resp = _mock_response(403, b"{}", json_data=error_json)
    with patch("valley.kofia.requests.get", return_value=resp):
        with pytest.raises(KofiaDataError, match="등록되지 않은 서비스키"):
            _request("getGrantingOfCreditBalanceInfo", {"numOfRows": 1, "pageNo": 1})


def test_request_raises_on_logical_result_code(monkeypatch):
    monkeypatch.setenv("KOFIA_API_KEY", "dummy-key")
    with patch("valley.kofia.requests.get", return_value=_mock_response(200, _LOGICAL_ERROR_XML)):
        with pytest.raises(KofiaDataError, match="NODATA_ERROR"):
            _request("getGrantingOfCreditBalanceInfo", {"numOfRows": 1, "pageNo": 1})


def test_get_credit_balance_history_raises_on_empty_items(monkeypatch):
    monkeypatch.setenv("KOFIA_API_KEY", "dummy-key")
    with patch("valley.kofia.requests.get", return_value=_mock_response(200, _EMPTY_XML)):
        with pytest.raises(KofiaDataError):
            get_credit_balance_history()


def test_get_credit_balance_history_success(monkeypatch):
    monkeypatch.setenv("KOFIA_API_KEY", "dummy-key")
    with patch("valley.kofia.requests.get", return_value=_mock_response(200, _SUCCESS_XML)):
        rows = get_credit_balance_history()
    assert len(rows) == 1
    assert rows[0]["crdTrFingKosdaq"] == 6130422035123.0
