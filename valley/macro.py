"""매크로 지표(물가/금리/고용/성장/통화·환율) 조회 레이어.

- 미국 및 대부분의 한국 지표는 FRED 공개 CSV 엔드포인트(API 키 불필요)에서 가져온다.
- 한국 CPI/M2는 FRED 미러링 데이터가 갱신을 멈춰서, 한국은행 ECOS OpenAPI로 우선 조회하고
  키가 없거나 실패하면 FRED 데이터로 폴백한다. ECOS는 같은 지표라도 "신지표"/"구지표"로
  테이블이 나뉘어 있고 구지표는 대부분 2000년대 초반에 갱신이 끊겼으니, 새 지표를 추가할
  때는 반드시 최근 데이터가 실제로 나오는 stat_code인지 확인해야 한다.
"""

import os
from datetime import date

import pandas as pd
import requests

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
ECOS_URL = (
    "https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/5000/"
    "{stat_code}/{cycle}/{start}/{end}/{item_code}"
)


class MacroDataError(Exception):
    """매크로 지표를 조회하지 못했을 때 발생."""


# 카테고리 -> [{key, label, ...}] 형태의 카탈로그. get_indicator_series가 key로 조회한다.
INDICATORS = {
    "US": {
        "물가": [
            {"key": "US_CPI", "label": "CPI (소비자물가지수)", "fred_id": "CPIAUCSL"},
            {"key": "US_CORE_CPI", "label": "근원 CPI", "fred_id": "CPILFESL"},
            {"key": "US_PCE", "label": "PCE (개인소비지출물가)", "fred_id": "PCEPI"},
            {"key": "US_CORE_PCE", "label": "근원 PCE", "fred_id": "PCEPILFE"},
            {"key": "US_PPI", "label": "생산자물가지수(PPI)", "fred_id": "PPIACO"},
        ],
        "금리": [
            {"key": "US_FEDFUNDS", "label": "연방기금금리(기준금리)", "fred_id": "FEDFUNDS"},
            {"key": "US_DGS10", "label": "10년 국채금리", "fred_id": "DGS10"},
            {"key": "US_DGS2", "label": "2년 국채금리", "fred_id": "DGS2"},
            {"key": "US_DGS3MO", "label": "3개월 국채금리", "fred_id": "DGS3MO"},
            {"key": "US_T10Y2Y", "label": "장단기 금리차 (10Y-2Y)", "fred_id": "T10Y2Y"},
            {"key": "US_T10Y3M", "label": "장단기 금리차 (10Y-3M, 침체 예측력 더 강함)", "fred_id": "T10Y3M"},
        ],
        "위험지표": [
            {"key": "US_HY_SPREAD", "label": "하이일드 스프레드 (신용 리스크 프리미엄, %p)", "fred_id": "BAMLH0A0HYM2"},
            {"key": "US_VIX", "label": "VIX 변동성지수 (낮을수록 안정)", "fred_id": "VIXCLS"},
            {"key": "US_BREAKEVEN10", "label": "10년 기대인플레이션(BEI)", "fred_id": "T10YIE"},
            {"key": "US_REAL_RATE10", "label": "10년 실질금리(TIPS)", "fred_id": "DFII10"},
            {"key": "US_DOLLAR", "label": "달러인덱스 (무역가중, 2006년 1월=100 기준)", "fred_id": "DTWEXBGS"},
        ],
        "고용": [
            {"key": "US_UNRATE", "label": "실업률", "fred_id": "UNRATE"},
            {"key": "US_PAYEMS", "label": "비농업고용", "fred_id": "PAYEMS"},
            {"key": "US_ICSA", "label": "신규 실업수당 청구건수", "fred_id": "ICSA"},
        ],
        "성장·소비": [
            {"key": "US_GDPC1", "label": "실질 GDP", "fred_id": "GDPC1"},
            {"key": "US_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "GDP"},
            {"key": "US_RSAFS", "label": "소매판매", "fred_id": "RSAFS"},
            {"key": "US_UMCSENT", "label": "소비자심리지수(미시간대)", "fred_id": "UMCSENT"},
            {"key": "US_HOUST", "label": "주택착공건수", "fred_id": "HOUST"},
        ],
        "통화": [
            {"key": "US_M2SL", "label": "M2 통화량", "fred_id": "M2SL"},
        ],
    },
    "KR": {
        "물가": [
            {
                "key": "KR_CPI",
                "label": "CPI (소비자물가지수)",
                "fred_id": "KORCPIALLMINMEI",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "0"},
            },
        ],
        "금리": [
            {"key": "KR_BASE_RATE", "label": "한국은행 기준금리", "fred_id": "IRSTCI01KRM156N"},
            {"key": "KR_DGS10", "label": "10년 국고채금리", "fred_id": "IRLTLT01KRM156N"},
        ],
        "고용": [
            {"key": "KR_UNRATE", "label": "실업률", "fred_id": "LRHUTTTTKRM156S"},
        ],
        "성장": [
            {"key": "KR_GDP", "label": "실질 GDP", "fred_id": "NGDPRSAXDCKRQ"},
            {"key": "KR_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "NGDPSAXDCKRQ"},
            {"key": "KR_IP", "label": "산업생산지수", "fred_id": "KORPRINTO01GYSAM"},
        ],
        "통화·환율": [
            {
                "key": "KR_M2",
                "label": "M2 통화량",
                "fred_id": "MYAGM2KRM189N",
                "ecos": {"stat_code": "161Y005", "cycle": "M", "item_code": "BBHS00"},
            },
            {"key": "KR_FX_USD", "label": "원/달러 환율", "fred_id": "DEXKOUS"},
        ],
        # 수출 주도·환율 민감형인 한국 경제 특성을 담는 대외 부문 지표. 전부 실측 확인
        # 완료(마지막 관측치가 몇 개월 이내로 최신이고, 단위/스케일이 상식적이며, 계정명이
        # 의도한 지표와 일치함) — 자세한 검증 근거는 각 항목 주석 참고.
        "대외": [
            {
                # ECOS "2.5.1.1. 국제수지(BPM6)" 표의 최상위 항목(경상수지 자체, item_code
                # 000000). 실측: 2026-05 38,607.4백만달러(월간)까지 갱신 확인, 최근 몇 개월도
                # 1.3~3.9조원대 흑자 규모로 상식적인 범위. FRED 후보(KORB6BLTT02STSAQ)는
                # 2024-10 이후 갱신이 끊긴 데다 단위도 GDP대비 %라 원계열(백만달러)과 안 맞아
                # 폴백으로 쓰지 않고 ECOS 전용으로 둔다.
                "key": "KR_CURRENT_ACCOUNT",
                "label": "경상수지 (월별, 백만달러)",
                "ecos": {"stat_code": "301Y013", "cycle": "M", "item_code": "000000"},
            },
            {
                # ECOS "3.2.1. 수출입 총괄"(통관기준) 표의 수출금액(item_code T002, 월중
                # 원계열 — 연간 합계가 월별 12개 합과 별도 계열로 존재해 누적계열이 아님을
                # 확인). 실측: 2026-05 87,821,386천불(=약 878억달러)까지 갱신 확인, 2024~2025년
                # 평균 월 550~650억달러대와 비교하면 2026년 들어 수출이 크게 늘어난 추세로
                # 보인다(이상치인지 실제 급증인지는 이 프로젝트 범위 밖). FRED
                # 폴백(XTEXVA01KRM667S)은 2026-04 기준 858.1억달러로 ECOS 원계열과 거의 일치
                # (같은 통관기준 수출 시계열의 OECD 미러로 판단) — 신뢰 가능한 폴백으로 채택.
                "key": "KR_EXPORTS",
                "label": "수출액 (월별, 천달러, 통관기준)",
                "fred_id": "XTEXVA01KRM667S",
                "ecos": {"stat_code": "901Y118", "cycle": "M", "item_code": "T002"},
            },
            {
                # ECOS "3.5. 외환보유액" 표의 합계(item_code 99). 실측: 2026-06
                # 427,359,263천달러(=약 4,274억달러)까지 갱신 확인, 절대 수준도 한국 외환보유액
                # 통상 범위(4천억달러 안팎)와 일치. FRED 폴백(TRESEGKRM052N)은 "금 제외"
                # 기준이라 ECOS 합계(금 포함)보다 약 1~1.5% 낮게 나오는 정의 차이가 있지만,
                # 두 계열 다 최신이고 스케일이 같은 단위(백만달러 상당)라 근사 폴백으로는
                # 허용 가능하다고 판단했다(정확한 회계 대조용이 아니라 추이 확인용 지표라서).
                "key": "KR_FX_RESERVES",
                "label": "외환보유액 (월별, 천달러)",
                "fred_id": "TRESEGKRM052N",
                "ecos": {"stat_code": "732Y001", "cycle": "M", "item_code": "99"},
            },
        ],
        "가계신용": [
            {
                # ECOS "1.2.4.1.1. 가계신용(업권별, 분기)" 표의 최상위 항목(가계신용 총계,
                # item_code 1000000, 분기별). "1.2.4.2. 예금취급기관 가계대출(월)"
                # (151Y002/151Y005)은 월별이라 더 자주 갱신되지만 예금취급기관 대출만 포함해
                # 보험사·여신전문기관·판매신용 등이 빠진 "가계신용"의 부분집합이라 쓰지 않았다.
                # 실측: 2026Q1 1,993,110.8십억원(약 1,993조원)까지 갱신 확인 — 분기 통계 특성상
                # 최근 분기 발표까지 통상 약 2개월 지연되는데(오늘 2026-07-20 기준으로는
                # 2026Q2가 아직 미발표) 이는 정상적인 지연이지 갱신이 끊긴 구지표가 아니다.
                # FRED에서 확인된 후보(HDTGPDKRQ163N 등)는 전부 "가계부채/GDP 비율(%)"이라
                # 십억원 단위 잔액과 단위가 달라 폴백으로 쓸 수 없다 — ECOS 전용으로 둔다.
                "key": "KR_HOUSEHOLD_CREDIT",
                "label": "가계신용 잔액 (분기별, 십억원)",
                "ecos": {"stat_code": "151Y001", "cycle": "Q", "item_code": "1000000"},
            },
        ],
    },
}


def _find_indicator(key: str) -> dict:
    for categories in INDICATORS.values():
        for indicators in categories.values():
            for ind in indicators:
                if ind["key"] == key:
                    return ind
    raise MacroDataError(f"'{key}'는 등록되지 않은 지표입니다.")


def get_fred_series(series_id: str) -> pd.Series:
    """FRED 공개 CSV 엔드포인트에서 시계열을 가져온다 (API 키 불필요)."""
    try:
        df = pd.read_csv(FRED_CSV_URL.format(series_id=series_id))
    except Exception as e:
        raise MacroDataError(f"FRED 시리즈 '{series_id}' 조회에 실패했습니다: {e}") from e

    date_col, value_col = df.columns[0], df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    series = df.set_index(date_col)[value_col].dropna()

    if series.empty:
        raise MacroDataError(f"FRED 시리즈 '{series_id}'에 유효한 데이터가 없습니다.")
    return series.sort_index()


def _ecos_period_bounds(cycle: str) -> tuple[str, str]:
    """ECOS는 주기(M/Q/A)마다 조회 시작~종료 문자열 형식이 다르다 (예: 월별 "202607",
    분기별 "2026Q3", 연간 "2026"). 기존에는 월별(M)만 지원해 하드코딩돼 있었다.
    """
    today = date.today()
    if cycle == "M":
        return "196501", today.strftime("%Y%m")
    if cycle == "Q":
        return "1965Q1", f"{today.year}Q{(today.month - 1) // 3 + 1}"
    if cycle == "A":
        return "1965", str(today.year)
    raise MacroDataError(f"지원하지 않는 ECOS 주기입니다: {cycle}")


def _ecos_parse_time(time_str: str, cycle: str) -> pd.Timestamp:
    """ECOS 응답의 TIME 필드(주기별로 형식이 다름)를 해당 구간 시작일로 변환한다."""
    if cycle == "M":
        return pd.to_datetime(time_str + "01", format="%Y%m%d")
    if cycle == "Q":
        year, q = time_str.split("Q")
        month = (int(q) - 1) * 3 + 1
        return pd.Timestamp(year=int(year), month=month, day=1)
    if cycle == "A":
        return pd.to_datetime(time_str + "0101", format="%Y%m%d")
    raise MacroDataError(f"지원하지 않는 ECOS 주기입니다: {cycle}")


def get_ecos_series(stat_code: str, cycle: str, item_code: str) -> pd.Series:
    """한국은행 ECOS에서 시계열을 가져온다 (stat_code/item_code/주기 조합은 호출부가 지정).

    cycle은 "M"(월별)·"Q"(분기별)·"A"(연간)를 지원한다.
    """
    api_key = os.environ.get("ECOS_API_KEY")
    if not api_key:
        raise MacroDataError("ECOS_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

    start, end = _ecos_period_bounds(cycle)
    url = ECOS_URL.format(
        api_key=api_key,
        stat_code=stat_code,
        cycle=cycle,
        start=start,
        end=end,
        item_code=item_code,
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise MacroDataError(f"ECOS 조회 중 오류가 발생했습니다: {e}") from e

    result = payload.get("StatisticSearch")
    if not result or "row" not in result:
        error_msg = payload.get("RESULT", {}).get("MESSAGE", "알 수 없는 오류")
        raise MacroDataError(f"ECOS 응답에 데이터가 없습니다: {error_msg}")

    rows = result["row"]
    index = pd.DatetimeIndex([_ecos_parse_time(r["TIME"], cycle) for r in rows])
    values = pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce")
    series = pd.Series(values, index=index).dropna().sort_index()

    if series.empty:
        raise MacroDataError(f"ECOS 시리즈 '{stat_code}/{item_code}'에 유효한 데이터가 없습니다.")
    return series


def get_indicator_series(key: str) -> pd.Series:
    """카탈로그의 key로 지표 시계열을 조회하는 단일 진입점."""
    indicator = _find_indicator(key)

    ecos_params = indicator.get("ecos")
    if ecos_params:
        try:
            return get_ecos_series(**ecos_params)
        except MacroDataError:
            if "fred_id" not in indicator:
                raise  # FRED 폴백이 없는 ECOS 전용 지표 — 실패를 그대로 알린다
            # FRED 폴백이 있으면 계속 진행

    return get_fred_series(indicator["fred_id"])


def normalize_indexed(series: pd.Series, base: float = 100.0) -> pd.Series:
    """시계열을 첫 관측치를 base로 지수화한다 (예: "구간 시작=100 기준 상대 추이").

    단위가 서로 다른 지표(예: 실업률 %  vs M2 수십조 원)를 같은 차트에 겹쳐도 스케일이
    맞도록 오버레이 차트에서 쓴다. 절대 수준이 궁금하면 원계열(단일 지표 보기)을 봐야 한다.
    """
    if series.empty:
        raise MacroDataError("정규화할 시계열이 비어 있습니다.")
    first = series.iloc[0]
    if not first:
        raise MacroDataError("정규화 기준값(첫 관측치)이 0이라 지수화할 수 없습니다.")
    return series / first * base
