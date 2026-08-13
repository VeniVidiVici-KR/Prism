"""토스증권 Open API(openapi.tossinvest.com) 조회 레이어 — 국내 종목 투자자별 수급(거래량 기준)
파일럿.

⚠️ 계좌·주문·조건주문 등 매매 관련 API(X-Tossinvest-Account 헤더가 필요한 엔드포인트)는 이
프로젝트가 리서치 도구이지 매매 도구가 아니므로 절대 호출하지 않는다 — 시세/수급 등 순수
조회성 엔드포인트만 쓴다.

⚠️ 단위 주의(실측 확인, 2026-08 라운드): 이 API의 investor-trading 응답은 "거래량(주식 수)"
기준이다("buyVolume"/"sellVolume"/"netBuyVolume" 필드명이 그대로 수량임을 실측으로 확인) —
반면 기존 valley/supply.py(KRX/pykrx)는 "거래대금(원화)" 기준이다. 두 소스는 절대 같은
차트/표에 값 그대로 섞지 않는다(views/stock_research.py의 소스 선택 UI 참고) — 삼성전자
2026-07-23~08-12 15영업일 대조 결과 부호(순매수/순매도 방향)는 45건 중 43건(95.6%) 일치했고,
불일치 2건 모두 두 소스 모두 순매수 규모가 0 근처인 날이라(거래대금 가중평균과 거래량 단순
합산의 차이로 자연스럽게 발생) 데이터 품질 문제로 보지 않는다.
"""

import os
import threading
import time

import pandas as pd
import requests

TOKEN_URL = "https://openapi.tossinvest.com/oauth2/token"
API_BASE = "https://openapi.tossinvest.com/api/v1"

DEFAULT_TRADING_DAYS = 20
_MAX_PAGE_COUNT = 100  # 실측 확인: count 파라미터는 1~100만 허용(초과 시 400 invalid-request)
_TOKEN_EXPIRY_BUFFER_SECONDS = 300  # 만료 5분 전에 미리 재발급(경계에서 401 왕복 방지)

# investor-trading 응답의 institution.breakdown 필드명 -> 한국어 라벨. 실측(005930) 확인 순서
# 그대로 유지 — 금융투자/보험/투신/사모펀드/은행/기타금융기관/연기금 7종.
INSTITUTION_BREAKDOWN_LABELS = {
    "financialInvestment": "금융투자",
    "insurance": "보험",
    "trust": "투신",
    "privateEquityFund": "사모펀드",
    "bank": "은행",
    "otherFinancialInstitution": "기타금융기관",
    "pensionFund": "연기금",
}


class TossDataError(Exception):
    """토스증권 API 조회에 실패했을 때 발생 (인증 실패/IP 미등록/rate limit/데이터 없음 등)."""


def _error_message(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"{err.get('code', '?')}: {err.get('message', resp.text[:200])}"
    except Exception:
        return resp.text[:200]


class _TokenCache:
    """OAuth2 access_token을 프로세스 메모리에 캐싱하고 만료 임박 시에만 재발급한다.

    ⚠️ AUTH 엔드포인트는 초당 5회 제한이라(실측: 응답 헤더 X-RateLimit-Limit=5) 매 API 호출마다
    새 토큰을 받으면 안 된다 — 실측 결과 expires_in=86399(약 24시간)라 하루 내내 재사용
    가능하다. threading.Lock으로 동시 갱신 경합을 막는다(Streamlit이 여러 세션에서 동시에
    호출할 수 있음).
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS:
                self._refresh()
            return self._token

    def invalidate(self) -> None:
        """401 응답을 받았을 때 강제로 만료 처리해 다음 get() 호출에서 재발급하게 한다."""
        with self._lock:
            self._token = None

    def _refresh(self) -> None:
        client_id = os.environ.get("TOSS_CLIENT_ID")
        client_secret = os.environ.get("TOSS_CLIENT_SECRET")
        if not (client_id and client_secret):
            raise TossDataError(
                "TOSS_CLIENT_ID/TOSS_CLIENT_SECRET가 설정되어 있지 않습니다. .env 파일을 확인하세요."
            )

        try:
            resp = requests.post(
                TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
        except Exception as e:
            raise TossDataError(f"토스증권 인증 서버 연결에 실패했습니다: {e}") from e

        if resp.status_code == 403:
            # 실측 확인된 케이스 — 앱 등록 시 지정한 허용 IP가 아니면 403이 온다.
            raise TossDataError(
                "토스증권 인증에 실패했습니다(403 — 허용 IP 미등록일 가능성). 토스증권 오픈 API "
                "개발자센터에서 이 서버의 IP가 허용 목록에 등록돼 있는지 확인하세요."
            )
        if resp.status_code != 200:
            raise TossDataError(f"토스증권 토큰 발급에 실패했습니다({resp.status_code}): {_error_message(resp)}")

        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 0)
        if not token:
            raise TossDataError("토스증권 토큰 응답에 access_token이 없습니다.")

        self._token = token
        self._expires_at = time.time() + expires_in


_token_cache = _TokenCache()


def _request(path: str, params: dict | None = None, *, _retried_auth: bool = False, _retried_rate: bool = False) -> dict:
    """토스 API GET 공통 처리. 401은 토큰 재발급 후 1회, 429는 Retry-After만큼 대기 후 1회
    재시도한다(둘 다 각각 최대 1회 — 무한 재시도로 rate limit을 더 태우지 않는다).
    """
    token = _token_cache.get()
    try:
        resp = requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
    except Exception as e:
        raise TossDataError(f"토스증권 API 조회 중 오류가 발생했습니다: {e}") from e

    if resp.status_code == 401 and not _retried_auth:
        _token_cache.invalidate()
        return _request(path, params, _retried_auth=True, _retried_rate=_retried_rate)

    if resp.status_code == 429 and not _retried_rate:
        retry_after = float(resp.headers.get("Retry-After", 1))
        time.sleep(retry_after)
        return _request(path, params, _retried_auth=_retried_auth, _retried_rate=True)

    if resp.status_code == 400 and "unsupported-market" in resp.text:
        raise TossDataError("토스증권 투자자별 수급은 국내(KR) 종목만 지원합니다.")
    if resp.status_code == 404:
        raise TossDataError(f"토스증권에서 해당 종목을 찾지 못했습니다: {_error_message(resp)}")
    if resp.status_code != 200:
        raise TossDataError(f"토스증권 API 오류({resp.status_code}): {_error_message(resp)}")

    return resp.json()


def _fetch_paginated_records(path: str, days: int) -> list[dict]:
    """count(최대 100, 실측 확인)를 넘는 기간이 필요하면 result.nextUntil 커서로 이어서
    페이지네이션한다. investor-trading/short-selling/credit-trades/securities-lending 네
    엔드포인트 전부 동일한 {result: {nextUntil, records}} + until/count 파라미터 구조를
    공유함을 실측으로 확인했다(2026-08 리스크지표 라운드) — path만 다르므로 하나의 헬퍼로
    재사용한다(중복 구현 금지)."""
    records: list[dict] = []
    until: str | None = None
    while len(records) < days:
        page_count = min(_MAX_PAGE_COUNT, days - len(records))
        params: dict = {"count": page_count}
        if until:
            params["until"] = until
        data = _request(path, params)
        result = data.get("result", {})
        page_records = result.get("records", [])
        if not page_records:
            break
        records.extend(page_records)
        until = result.get("nextUntil")
        if not until:
            break
    return records


def _to_float_or_nan(value) -> float:
    return float(value) if value is not None else float("nan")


def get_investor_trading_history(symbol: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """국내 종목의 최근 `days` 영업일 개인/외국인/기관 순매수(거래량, 주식 수)를 반환한다.

    ⚠️ 단위는 거래량(주식 수)이다 — valley.supply.get_investor_net_trading()(거래대금 원화)과
    단위가 다르므로 값을 직접 더하거나 같은 축에 겹쳐 그리지 않는다. 조회 당일(장중)은 개인
    투자자 수치가 아직 집계되지 않아 null로 오는 경우가 있다(실측 확인) — 그 항목은 NaN으로
    남긴다.
    """
    records = _fetch_paginated_records(f"/stocks/{symbol}/investor-trading", days)
    if not records:
        raise TossDataError(f"'{symbol}' 종목의 투자자별 수급 데이터가 없습니다.")

    rows = []
    for rec in records[:days]:
        def net(key: str) -> float:
            group = rec.get(key)
            if not group or group.get("netBuyVolume") is None:
                return float("nan")
            return float(group["netBuyVolume"])

        rows.append({"날짜": rec["date"], "외국인": net("foreigner"), "기관": net("institution"), "개인": net("individual")})

    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    df = df.set_index("날짜").sort_index()
    return df


def get_short_selling_history(symbol: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """국내 종목의 최근 `days` 영업일 공매도 거래량/거래대금/비중을 반환한다.

    ⚠️ 비중 필드(shortSellingVolumeRate/AmountRate)는 실측 대조로 "당일 전체 거래량(또는
    거래대금) 대비 공매도 비중"임을 확인했다 — 예: 2026-08-12 shortSellingVolume 1,984,289 /
    shortSellingVolumeRate 0.07321로 역산한 전체 거래량(27,106,529)이 같은 날 pykrx 실측
    거래량(27,102,479)과 거의 정확히 일치. 비중은 0~1 소수로 온다(퍼센트 표시는 호출부가
    ×100). ⚠️ investor-trading과 달리 공매도는 **T+1 지연**이 있다(조회 당일 데이터는 아직
    없고 전일까지만 온다 — 실측 확인). 비중 필드는 간헐적으로 null이 온다(거래량/거래대금은
    항상 존재) — NaN으로 남긴다(추측으로 채우지 않음).
    """
    records = _fetch_paginated_records(f"/stocks/{symbol}/short-selling", days)
    if not records:
        raise TossDataError(f"'{symbol}' 종목의 공매도 데이터가 없습니다.")

    rows = [
        {
            "날짜": rec["date"],
            "거래량": _to_float_or_nan(rec.get("shortSellingVolume")),
            "거래대금": _to_float_or_nan(rec.get("shortSellingAmount")),
            "거래량비중": _to_float_or_nan(rec.get("shortSellingVolumeRate")),
            "거래대금비중": _to_float_or_nan(rec.get("shortSellingAmountRate")),
        }
        for rec in records[:days]
    ]
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.set_index("날짜").sort_index()


def get_credit_trades_history(symbol: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """국내 종목의 최근 `days` 영업일 신용거래(융자/대주) 잔고·공여율을 반환한다.

    ⚠️ 융자(margin loan, 빌린 돈으로 매수)와 대주(개인 신용대주, 빌린 주식을 매도)는 반대
    성격의 상품인데 같은 엔드포인트에 함께 온다 — 혼동하지 말 것. valley.kofia로 이미 붙인
    시장 전체 신용공여 잔고와 달리 이건 **종목별** 잔고다. balanceRate(공여율)은 실측 대조
    (005930 10영업일)로 "잔고수량 / 상장주식수"에 근접함을 확인했다(오차 0.01%p 이내 —
    상장주식수를 investor-trading의 외국인 한도수량으로 근사해서 생기는 반올림 오차로
    추정). tradingRate(당일 거래 대비 비율로 추정되는 필드)는 정확한 분모를 실측으로
    확정하지 못해 이 함수가 반환하지 않는다(추측한 산식을 그대로 노출하지 않음).
    """
    records = _fetch_paginated_records(f"/stocks/{symbol}/credit-trades", days)
    if not records:
        raise TossDataError(f"'{symbol}' 종목의 신용거래 데이터가 없습니다.")

    rows = []
    for rec in records[:days]:
        margin = rec.get("marginLoan") or {}
        stock_loan = rec.get("stockLoan") or {}
        rows.append(
            {
                "날짜": rec["date"],
                "융자잔고": _to_float_or_nan(margin.get("balanceQuantity")),
                "융자공여율": _to_float_or_nan(margin.get("balanceRate")),
                "대주잔고": _to_float_or_nan(stock_loan.get("balanceQuantity")),
                "대주공여율": _to_float_or_nan(stock_loan.get("balanceRate")),
            }
        )
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.set_index("날짜").sort_index()


def get_securities_lending_history(symbol: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """국내 종목의 최근 `days` 영업일 대차거래(체결/상환/잔고)를 반환한다.

    ⚠️ 대차거래는 공매도의 "선행" 성격이 있다(빌려야 팔 수 있음)지만, 대차 전부가 공매도로
    이어지는 건 아니다(헤지·차익거래 목적도 포함) — 이 한계는 UI 캡션에서 명시한다.
    balanceAmount(잔고금액)는 실측 대조 결과 balanceQuantity × 당일 종가와 정확히 일치했다
    (별도 집계가 아니라 잔고수량의 단순 가치평가 필드).
    """
    records = _fetch_paginated_records(f"/stocks/{symbol}/securities-lending", days)
    if not records:
        raise TossDataError(f"'{symbol}' 종목의 대차거래 데이터가 없습니다.")

    rows = [
        {
            "날짜": rec["date"],
            "체결수량": _to_float_or_nan(rec.get("executionQuantity")),
            "상환수량": _to_float_or_nan(rec.get("repaymentQuantity")),
            "잔고수량": _to_float_or_nan(rec.get("balanceQuantity")),
            "잔고금액": _to_float_or_nan(rec.get("balanceAmount")),
        }
        for rec in records[:days]
    ]
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.set_index("날짜").sort_index()


def get_institution_breakdown_history(symbol: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """국내 종목의 최근 `days` 영업일 기관 세부분류(금융투자/보험/투신/사모펀드/은행/
    기타금융기관/연기금) 순매수(거래량, 주식 수)를 반환한다.

    ⚠️ 조회 당일(장중) 또는 기관 거래가 없는 날은 breakdown 자체가 null로 올 수 있다 —
    그런 날은 전 항목 NaN으로 남긴다(억지로 0으로 채우지 않음, 결측과 무거래를 구분).
    """
    records = _fetch_paginated_records(f"/stocks/{symbol}/investor-trading", days)
    if not records:
        raise TossDataError(f"'{symbol}' 종목의 투자자별 수급 데이터가 없습니다.")

    rows = []
    for rec in records[:days]:
        breakdown = (rec.get("institution") or {}).get("breakdown") or {}
        row = {"날짜": rec["date"]}
        for field, label in INSTITUTION_BREAKDOWN_LABELS.items():
            value = (breakdown.get(field) or {}).get("netBuyVolume")
            row[label] = float(value) if value is not None else float("nan")
        rows.append(row)

    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    df = df.set_index("날짜").sort_index()
    return df
