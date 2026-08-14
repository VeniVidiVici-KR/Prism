"""KOSIS(국가통계포털) 공유서비스 OpenAPI 조회 레이어 — ⚠️ 검증 전용, 카탈로그/UI 미연결.

2026-08 KOSIS 검증 라운드에서 확정한 내용(다음 라운드에서 전환 여부를 판단할 때 재사용):

- 엔드포인트는 `statisticsData.do`가 아니라 `/openapi/Param/statisticsParameterData.do`다
  (전자는 항상 "필수 요청 파라미터 누락" 오류만 반환 — 존재하지 않는/잘못된 경로였다).
- CPI 지출목적별 표는 orgId=101, tblId=DT_1J22001. 파라미터는 3중 분류 구조다:
  - itmId="T" (고정값 — 소비자물가지수 그 자체를 가리키는 항목 ID. "T10"이 아니다.)
  - objL1="T10" (시도별 분류에서 "전국"을 가리키는 코드. 다른 지역 코드가 있지만 이 프로젝트는
    항상 전국만 쓴다.)
  - objL2="0+A+B+C+D+E+F+G+H+I+J+K+L" (지출목적별 대분류 — "+"로 구분해 여러 개를
    한 번에 요청 가능. 코드가 ECOS 901Y009의 item_code와 정확히 동일하다: "0"=총지수,
    "A"~"L"=12개 대분류. KOSIS 웹 화면에 보이는 "01 식료품"·"02 주류및담배"... 같은 번호는
    화면 표시용 라벨일 뿐, 실제 API 코드는 A~L이다.)
  - objL3~objL8은 빈 문자열로라도 반드시 포함해야 한다(생략하면 "필수 파라미터 누락").
- 응답은 JSON **배열**이며(에러 시에만 `{"err":..., "errMsg":...}` 객체), 값(`DT`)은 **문자열**로
  온다 — 호출부가 float() 변환해야 한다. 결측은 행 자체가 없는 형태로 나타났다(별도 결측
  표기 문자열은 실측에서 관찰되지 않음).
- ⚠️ 가중치(WGT) 필드는 이 응답에 없다. KOSIS 통계설명자료에도 가중치는
  "지출목적별 품목 및 가중치(2000년-2022년).xlsx" 다운로드 파일로만 안내되어 있고, 사이트
  통계표 검색(`소비자물가지수 가중치`)에도 조회 가능한 표가 없다 — API로 실시간 조회할 방법이
  없다는 뜻이다. 가중치는 계속 `macro.get_ecos_item_weights("901Y009")`를 쓴다(가중치는
  2~3년에 한 번만 개편되므로, 지수는 KOSIS에서 받고 가중치만 ECOS에서 받아도 시점 불일치
  리스크가 낮다 — 실측: 동일 기간 총지수+12개 대분류 156개 데이터포인트가 ECOS와 소수점까지
  전부 일치했다).
- 최대 40,000행/호출(ECOS는 5,000행) — 대분류 12개+총지수를 이 함수처럼 한 번에 받을 수
  있어 ECOS 대비 호출 수가 1/13로 줄어든다.
- Rate limit 관련 공식 수치 문서는 못 찾았다. 8회 연속 호출(간격 ~0.2초)에서 429/차단은
  관찰되지 않았으나, 소량 실측이라 확정적 결론은 아니다.
"""

import urllib.parse

import pandas as pd
import requests

KOSIS_PARAM_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

KOSIS_CPI_TABLE = {"orgId": "101", "tblId": "DT_1J22001"}

# ECOS 901Y009의 item_code(A~L)와 정확히 동일한 코드 체계 — 값 대조로 검증 완료(2026-08).
KOSIS_CPI_MAJOR_ITEM_CODES = ["0", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]


class KosisDataError(Exception):
    pass


def _kosis_api_key() -> str:
    import os

    api_key = os.environ.get("KOSIS_API_KEY")
    if not api_key:
        raise KosisDataError("KOSIS_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    return api_key


def get_kosis_cpi_series(item_codes: list[str], start_ym: str, end_ym: str) -> dict[str, pd.Series]:
    """한국 CPI(지출목적별, DT_1J22001)를 KOSIS OpenAPI에서 가져온다.

    item_codes는 ECOS item_code와 동일한 체계("0"=총지수, "A"~"L"=12개 대분류)를 쓴다.
    start_ym/end_ym은 "YYYYMM" 형식. 여러 item_code를 한 번의 HTTP 요청으로 받아
    {item_code: pd.Series} 딕셔너리로 반환한다.
    """
    api_key = _kosis_api_key()
    params = {
        "method": "getList",
        "apiKey": api_key,
        "orgId": KOSIS_CPI_TABLE["orgId"],
        "tblId": KOSIS_CPI_TABLE["tblId"],
        "itmId": "T",
        "objL1": "T10",
        "objL2": "+".join(item_codes),
        "objL3": "", "objL4": "", "objL5": "", "objL6": "", "objL7": "", "objL8": "",
        "prdSe": "M",
        "startPrdDe": start_ym,
        "endPrdDe": end_ym,
        "format": "json",
        "jsonVD": "Y",
    }
    url = KOSIS_PARAM_URL + "?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise KosisDataError(f"KOSIS 조회 중 오류가 발생했습니다: {e}") from e

    if isinstance(payload, dict) and "err" in payload:
        raise KosisDataError(f"KOSIS 오류 {payload.get('err')}: {payload.get('errMsg')}")

    by_code: dict[str, list[tuple[str, float]]] = {}
    for row in payload:
        by_code.setdefault(row["C2"], []).append((row["PRD_DE"], float(row["DT"])))

    result: dict[str, pd.Series] = {}
    for code, points in by_code.items():
        points.sort()
        index = pd.DatetimeIndex([pd.Timestamp(f"{ym[:4]}-{ym[4:]}-01") for ym, _ in points])
        result[code] = pd.Series([v for _, v in points], index=index)

    if not result:
        raise KosisDataError("KOSIS 응답에 유효한 데이터가 없습니다.")
    return result
