"""금융투자협회(KOFIA) 신용공여·증시자금 데이터 — 공공데이터포털(data.go.kr)
"금융위원회_금융투자협회종합통계정보" API를 통해 조회한다. Streamlit 의존성 없는 순수 조회.

일별·시장별(코스피/코스닥) 신용거래융자·신용거래대주 잔고와 증시자금(투자자예탁금 등)을
제공한다 — ECOS 901Y056(증시주변자금동향)이 월별·시장 합계만 주는 것과 달리 일별·시장별
해상도를 준다.

⚠️ ECOS와 실측 대조(2026-07-31 기준) 결과, 이 API의 crdTrFingWhl(신용거래융자 전체)이
ECOS S23E(신용융자 잔고) 2026-07 값과, invrDpsgAmt(투자자예탁금)가 ECOS S23A와 각각
원(KRW) 단위까지 정확히 일치했다 — 즉 ECOS 901Y056은 이 KOFIA 일별 시계열의 "매월 마지막
거래일" 스냅샷을 그대로 재게시하는 것이다(정의가 같다). 응답 단위는 원(KRW)이다 — KOFIA
공식 웹 화면은 백만원 단위로 보여주지만(실측: 웹 30,023,768백만원 == API 30,023,768,000,000원
규모), API 자체는 원 단위라 ECOS(원 단위)와 그대로 나란히 쓸 수 있다(별도 환산 불필요).

⚠️ XML이 기본 응답이다 — "_type=json"을 붙여도 무시되고 XML이 온다(실측 확인). 인증
오류(서비스키 없음/미등록)는 XML이 아니라 HTTP 401/403과 함께 별도 JSON 에러 포맷
(OpenAPI_ServiceResponse.cmmMsgHeader)으로 온다 — 성공 응답(XML, header.resultCode)과
스키마가 완전히 다르므로 반드시 분기해서 파싱한다(공공데이터포털 공통 에러 포맷, 이 API만의
특이사항이 아니다).

⚠️ 데이터 지연: 실측 결과 API의 최신 관측치가 "오늘"은 물론 KOFIA 공식 웹 화면보다도 며칠
늦을 수 있다(실측 시점 기준 웹은 2026-08-10 자료를 보여줬지만 API는 2026-08-07까지만 있었다,
2 영업일 이상 차이). 호출부가 반환된 basDt를 반드시 표시해 "며칠 전 자료"임을 드러내야 한다 —
"오늘 기준"이라고 임의로 표기하면 안 된다.

⚠️ 소급 가능 기간: 실측 결과 2021-11-09부터(최근 약 1160영업일) 조회된다. 일일 트래픽
한도(10,000)에 여유가 있으니, 여러 기간을 나눠 여러 번 호출하는 대신 한 번에 넓게 받아
캐싱하는 편이 낫다(호출부가 numOfRows를 충분히 크게 잡아 한 번만 호출).
"""

import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import requests

_BASE_URL = "https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService"

# 실측 결과 전체 소급 가능 기간이 약 1160영업일(2021-11-09~)이라, 기본값을 넉넉히 잡아
# 한 번의 호출로 전체 역사를 다 받는다(추가 페이지네이션 호출 없음 — STEP 0 실측 근거는
# 위 모듈 docstring 참고).
_DEFAULT_NUM_ROWS = 1500

# 신용공여잔고추이(getGrantingOfCreditBalanceInfo) 응답 필드 — 실측으로 확인한 그대로.
CREDIT_BALANCE_FIELDS = [
    "basDt",  # YYYYMMDD 기준일자
    "crdTrFingWhl",  # 신용거래융자 전체(유가증권+코스닥) 금액
    "crdTrFingScrs",  # 신용거래융자 유가증권 금액
    "crdTrFingKosdaq",  # 신용거래융자 코스닥 금액
    "crdTrLndrWhl",  # 신용거래대주 전체(유가증권+코스닥) 금액
    "crdTrLndrScrs",  # 신용거래대주 유가증권 금액
    "crdTrLndrKosdaq",  # 신용거래대주 코스닥 금액
    "sbscCapLn",  # 청약자금대출 금액
    "dpsgScrtMogFing",  # 예탁증권담보융자 금액
]

# 증시자금추이(getSecuritiesMarketTotalCapitalInfo) 응답 필드 — 실측으로 확인한 그대로.
MARKET_CAPITAL_FIELDS = [
    "basDt",  # YYYYMMDD 기준일자
    "invrDpsgAmt",  # 투자자예탁금(장내파생상품 거래예수금 제외) — ECOS S23A와 대응
    "onbdDrvPrdTrRcAdvAmt",  # 장내파생상품 거래 예수금
    "toCstRpchCndBndSlgBal",  # 대고객 환매조건부채권(RP) 매도잔고
    "brkTrdUcolMny",  # 위탁매매 미수금
    "brkTrdUcolMnyVsOppsTrdAmt",  # 위탁매매 미수금 대비 실제 반대매매금액
    "ucolMnyVsOppsTrdRlImpt",  # 미수금 대비 반대매매비중(%)
]


class KofiaDataError(Exception):
    """KOFIA 신용공여·증시자금 데이터를 조회하지 못했을 때 발생."""


def _get_service_key() -> str:
    raw_key = os.environ.get("KOFIA_API_KEY")
    if not raw_key:
        raise KofiaDataError(
            "KOFIA_API_KEY가 설정되어 있지 않습니다. https://www.data.go.kr 에서 "
            "\"금융위원회_금융투자협회종합통계정보\" API 활용신청 후 발급받은 서비스키를 "
            ".env에 추가하세요."
        )
    # 공공데이터포털 서비스키는 이미 URL-인코딩된 형태로 발급된다 — requests의 params=가
    # 값을 다시 인코딩하므로, 여기서 한 번 디코딩해두지 않으면 이중 인코딩(%가 %25로) 되어
    # 인증에 실패한다.
    return unquote(raw_key)


def _request(operation: str, params: dict) -> ET.Element:
    """공공데이터포털 공통 오류 포맷(HTTP 401/403 + JSON)과 정상 XML 응답(header.resultCode)을
    구분해서 처리하는 공통 호출부. 두 오퍼레이션(신용공여잔고추이/증시자금추이)이 동일 구조라
    공유한다.
    """
    service_key = _get_service_key()
    url = f"{_BASE_URL}/{operation}"
    query = {"serviceKey": service_key, **params}
    try:
        resp = requests.get(url, params=query, timeout=15)
    except Exception as e:
        raise KofiaDataError(f"KOFIA API 조회 중 오류가 발생했습니다: {e}") from e

    if resp.status_code != 200:
        # ⚠️ 서비스키 없음/미등록·트래픽 초과 등 인증·요청 단계 오류는 XML이 아니라 공공데이터
        # 포털 공통 JSON 에러 포맷으로 온다(실측 확인: 키 없음 401 SERVICE_KEY_IS_NULL, 미등록
        # 키 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR) — 정상 응답의 XML 스키마와 다르다.
        try:
            payload = resp.json()
            msg = payload["OpenAPI_ServiceResponse"]["cmmMsgHeader"]["returnAuthMsg"]
        except Exception:
            msg = f"HTTP {resp.status_code}"
        raise KofiaDataError(f"KOFIA API 인증/요청 오류: {msg}")

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise KofiaDataError(f"KOFIA API 응답 파싱에 실패했습니다: {e}") from e

    result_code = root.findtext("header/resultCode")
    if result_code != "00":
        result_msg = root.findtext("header/resultMsg") or "알 수 없는 오류"
        raise KofiaDataError(f"KOFIA API 오류({result_code}): {result_msg}")

    return root


def _parse_items(root: ET.Element, fields: list[str]) -> list[dict]:
    """<items><item>...</item></items>를 필드 목록에 맞춰 dict 리스트로 변환한다.
    basDt는 문자열(YYYYMMDD) 그대로, 나머지는 숫자로 변환한다(호출부가 정렬/가공을 책임진다 —
    응답이 이미 최신순으로 오는지는 호출부가 basDt로 확인해야 한다)."""
    rows = []
    for item in root.findall("body/items/item"):
        row = {}
        for field in fields:
            text = item.findtext(field)
            row[field] = text if field == "basDt" else (float(text) if text is not None else None)
        rows.append(row)
    return rows


def get_credit_balance_history(num_rows: int = _DEFAULT_NUM_ROWS) -> list[dict]:
    """일별 신용공여 잔고 추이(신용거래융자·신용거래대주, 전체·유가증권·코스닥)를 최신순으로
    가져온다. 반환값은 basDt 내림차순(최신이 [0])."""
    root = _request("getGrantingOfCreditBalanceInfo", {"numOfRows": num_rows, "pageNo": 1})
    rows = _parse_items(root, CREDIT_BALANCE_FIELDS)
    if not rows:
        raise KofiaDataError("신용공여 잔고 데이터가 없습니다.")
    return rows


def get_securities_market_capital_history(num_rows: int = _DEFAULT_NUM_ROWS) -> list[dict]:
    """일별 증시자금추이(투자자예탁금·위탁매매미수금 등)를 최신순으로 가져온다.
    반환값은 basDt 내림차순(최신이 [0])."""
    root = _request("getSecuritiesMarketTotalCapitalInfo", {"numOfRows": num_rows, "pageNo": 1})
    rows = _parse_items(root, MARKET_CAPITAL_FIELDS)
    if not rows:
        raise KofiaDataError("증시자금 데이터가 없습니다.")
    return rows
