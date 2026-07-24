"""여러 매크로 지표를 조합한 파생 지표 (인플레이션율, 실질금리, M2/GDP, 금리차).

원본 지표(valley.macro)는 각자 다른 주기(월별/분기별)로 발표되므로, 조합하기 전에
주기를 맞추는 과정이 필요하다. 함수별 정렬 방식은 각 docstring에 적어둔다.
"""

import os

import pandas as pd

from valley.macro import MacroDataError, get_indicator_series

# 명목 GDP는 INDICATORS 카탈로그의 {country}_GDP_NOMINAL로 조회한다.
# 미국(fred_id "GDP")은 이미 연율화(SAAR)된 값이라 그대로 쓰고, 한국(fred_id "NGDPSAXDCKRQ")은
# 분기 원계열이라 rolling(4).sum()으로 직접 연율화한다.

# 한국 GDP(NGDPSAXDCKRQ)는 백만원 단위, M2(ECOS)는 십억원 단위로 서로 달라
# (각 시리즈의 절대 수준을 비교해 역산 확인함 — 보정 없이는 비율이 약 1000배 작게 나옴) 1000으로
# 나눠 십억원 기준으로 맞춘다. 미국은 GDP·M2SL 모두 십억달러 단위라 보정이 필요 없다.
_KR_GDP_UNIT_SCALE = 1000

COMPOSITE_INDICATORS = {
    "US": [
        {"key": "US_CPI_YOY", "label": "CPI 상승률 (전년동월대비 %)"},
        {"key": "US_REAL_RATE", "label": "실질금리 (기준금리 - CPI YoY%)"},
        {"key": "US_M2_GDP", "label": "M2 / 명목GDP"},
    ],
    "KR": [
        {"key": "KR_CPI_YOY", "label": "CPI 상승률 (전년동월대비 %)"},
        {"key": "KR_REAL_RATE", "label": "실질금리 (기준금리 - CPI YoY%)"},
        {"key": "KR_M2_GDP", "label": "M2 / 명목GDP"},
        {"key": "KR_EXPORTS_YOY", "label": "수출 증가율 (전년동월대비 %)"},
    ],
}

# 국가 구분이 없는(두 나라를 함께 봐야 하는) 지표.
CROSS_COUNTRY_INDICATORS = [
    {"key": "US_KR_RATE_SPREAD", "label": "한미 정책금리차 (미국 - 한국)"},
]


def yoy_percent(series: pd.Series, periods: int = 12) -> pd.Series:
    """월별 시계열을 N개월 전 대비 변화율(%)로 변환한다 (기본 12=전년동월대비/YoY).

    CPI 구성항목(주거비/식품/에너지 등)마다 별도 혼합지표를 등록하는 대신, 어떤 카탈로그
    지표든 이 범용 함수 하나로 YoY 변환할 수 있게 한다(Macro Indicator 페이지의 "CPI
    구성항목 한눈에 보기" 뷰가 이걸 재사용한다). 데이터가 periods개월보다 짧으면 빈
    시리즈가 나올 수 있으니 호출부가 빈 결과를 처리해야 한다.
    """
    return (series.pct_change(periods) * 100).dropna()


def compute_cpi_contributions(components: list[dict]) -> tuple[pd.DataFrame, float]:
    """구성항목별 (가중치, YoY)로 헤드라인 기여도(%p)를 분해한다.

    contribution_i ≈ (weight_pct_i / 100) × yoy_i — 항목별 실측 가중치와 이미 공통 기준월로
    정렬된 YoY%를 곱하는 단순 가산 근사다(호출부가 정렬/공통월 선택을 책임진다. 이 함수는
    이미 정렬된 값만 받아 곱셈만 한다). components는 각 {"label", "weight_pct", "yoy"} dict.
    반환: (표 DataFrame[항목/가중치(%)/YoY(%)/기여도(%p)], 기여도 합계(%p)) — 합계를 헤드라인
    YoY와 비교하는 자기검증은 호출부(뷰)가 담당한다(차이가 크면 억지로 맞추지 말고 그대로
    드러내야 하므로, 이 순수 함수는 판단 없이 계산 결과만 반환한다).
    """
    rows = [
        {
            "항목": c["label"],
            "가중치(%)": c["weight_pct"],
            "YoY(%)": c["yoy"],
            "기여도(%p)": c["weight_pct"] / 100 * c["yoy"],
        }
        for c in components
    ]
    df = pd.DataFrame(rows)
    return df, float(df["기여도(%p)"].sum())


def _cpi_yoy(country: str) -> pd.Series:
    """CPI 지수를 전년동월대비 상승률(%)로 변환한다 (월별 데이터 기준 12개월 전 대비)."""
    cpi = get_indicator_series(f"{country}_CPI")
    yoy = yoy_percent(cpi)
    if yoy.empty:
        raise MacroDataError(f"{country} CPI 상승률을 계산할 만큼 데이터가 충분하지 않습니다.")
    return yoy


def _real_rate(country: str) -> pd.Series:
    """실질금리 = 정책금리 - CPI 전년동월대비 상승률. 두 월별 시계열을 겹치는 구간만 뺀다."""
    rate_key = "US_FEDFUNDS" if country == "US" else "KR_BASE_RATE"
    rate = get_indicator_series(rate_key)
    cpi_yoy = _cpi_yoy(country)
    df = pd.concat([rate.rename("rate"), cpi_yoy.rename("cpi_yoy")], axis=1, join="inner")
    if df.empty:
        raise MacroDataError(f"{country} 실질금리를 계산할 만큼 겹치는 데이터가 없습니다.")
    return (df["rate"] - df["cpi_yoy"]).dropna()


def _m2_to_gdp(country: str) -> pd.Series:
    """M2(월별 스톡) / 명목GDP(분기별, 연율화)를 분기 기준 비율로 반환한다.

    M2는 분기 마지막 달 값으로 리샘플하고, GDP는 미국은 이미 연율화된 값을 그대로,
    한국은 직전 4개 분기 합으로 연율화해 두 나라 모두 '연간 GDP 대비 M2 스톡' 기준으로 맞춘다.

    한국은 ECOS_API_KEY가 반드시 필요하다: _KR_GDP_UNIT_SCALE은 ECOS(161Y005, 십억원 단위)
    기준으로 역산한 상수라, ECOS 호출이 실패해 FRED(MYAGM2KRM189N, 원 단위 그대로 + 2017년
    이후 갱신 중단)로 폴백되면 단위도 안 맞고 데이터도 9년 가까이 낡아 조용히 틀린 비율이
    나온다. get_ecos_series처럼 예외를 던지는 대신 폴백하는 CPI 계열과 달리, 이 지표는
    절대 수준을 직접 쓰기 때문에 폴백을 허용할 수 없다.
    """
    if country == "KR" and not os.environ.get("ECOS_API_KEY"):
        raise MacroDataError(
            "한국 M2/GDP는 ECOS_API_KEY가 있어야 정확합니다 (FRED 폴백은 단위가 달라 "
            "부정확한 값이 나옵니다). .env에 ECOS_API_KEY를 설정해주세요."
        )

    m2_key = "US_M2SL" if country == "US" else "KR_M2"
    m2 = get_indicator_series(m2_key).resample("QS").last()

    gdp = get_indicator_series(f"{country}_GDP_NOMINAL")
    if country == "KR":
        gdp = gdp.rolling(4).sum() / _KR_GDP_UNIT_SCALE

    df = pd.concat([m2.rename("m2"), gdp.rename("gdp")], axis=1, join="inner").dropna()
    if df.empty:
        raise MacroDataError(f"{country} M2/GDP를 계산할 만큼 겹치는 데이터가 없습니다.")
    return df["m2"] / df["gdp"]


def _exports_yoy() -> pd.Series:
    """수출액(KR_EXPORTS, 통관기준 월중 원계열)을 전년동월대비 상승률(%)로 변환한다.

    KR_EXPORTS는 월별 값이 연간 합계와 별도 계열로 존재해(연간 합계가 월별 12개월 합과
    가깝게 나옴) 누적계열이 아니라 월중 값임을 확인했다 — 그래서 CPI YoY와 동일하게
    pct_change(12)로 안전하게 전년동월대비를 계산할 수 있다.
    """
    exports = get_indicator_series("KR_EXPORTS")
    yoy = (exports.pct_change(12) * 100).dropna()
    if yoy.empty:
        raise MacroDataError("수출 증가율을 계산할 만큼 데이터가 충분하지 않습니다.")
    return yoy


def _rate_spread() -> pd.Series:
    """한미 정책금리차 = 미국 연방기금금리 - 한국 기준금리 (월별, 겹치는 구간만)."""
    us_rate = get_indicator_series("US_FEDFUNDS")
    kr_rate = get_indicator_series("KR_BASE_RATE")
    df = pd.concat([us_rate.rename("us"), kr_rate.rename("kr")], axis=1, join="inner")
    if df.empty:
        raise MacroDataError("한미 정책금리차를 계산할 만큼 겹치는 데이터가 없습니다.")
    return (df["us"] - df["kr"]).dropna()


_COMPUTE = {
    "US_CPI_YOY": lambda: _cpi_yoy("US"),
    "KR_CPI_YOY": lambda: _cpi_yoy("KR"),
    "US_REAL_RATE": lambda: _real_rate("US"),
    "KR_REAL_RATE": lambda: _real_rate("KR"),
    "US_M2_GDP": lambda: _m2_to_gdp("US"),
    "KR_M2_GDP": lambda: _m2_to_gdp("KR"),
    "KR_EXPORTS_YOY": _exports_yoy,
    "US_KR_RATE_SPREAD": _rate_spread,
}


def get_composite_series(key: str) -> pd.Series:
    """카탈로그의 key로 혼합 지표 시계열을 조회하는 단일 진입점."""
    if key not in _COMPUTE:
        raise MacroDataError(f"'{key}'는 등록되지 않은 혼합 지표입니다.")
    return _COMPUTE[key]()
