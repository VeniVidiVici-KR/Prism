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
    # ⚠️ 물가 재편 라운드(2026-08) — 근원 CPI에 MoM 변화율이 없는 등 시장이 CPI 발표일에
    # 보는 변화율 조합이 카탈로그에 빠져 있었다. 기존 US_CPI_YOY(이미 있음, 확인만)를 빼면
    # 7개가 새로 필요했다: 근원 CPI YoY/MoM, 헤드라인 CPI MoM, PCE YoY/MoM, 근원 PCE
    # YoY/MoM. 전부 "물가" 카테고리의 기존 SA 지수(US_CPI/US_CORE_CPI/US_PCE/US_CORE_PCE,
    # CPIAUCSL 등 전부 계절조정)에서 yoy_percent()/mom_percent()로 파생하므로 새 데이터
    # 소스가 필요 없다. ⚠️ MoM은 SA 필수, YoY는 SA/NSA 무관 — 4개 원천이 전부 이미 SA라
    # 둘 다 같은 시리즈에서 뽑아도 원칙 위반이 아니다(SA를 NSA와 섞는 게 문제이지, SA를
    # YoY에 쓰는 것 자체는 허용된다).
    "US": [
        {"key": "US_CPI_YOY", "label": "CPI 상승률 (전년동월대비 %)"},
        {"key": "US_CPI_MOM", "label": "CPI 상승률 (전월대비 %)"},
        {"key": "US_CORE_CPI_YOY", "label": "근원 CPI 상승률 (전년동월대비 %)"},
        {"key": "US_CORE_CPI_MOM", "label": "근원 CPI 상승률 (전월대비 %)"},
        {"key": "US_PCE_YOY", "label": "PCE 상승률 (전년동월대비 %)"},
        {"key": "US_PCE_MOM", "label": "PCE 상승률 (전월대비 %)"},
        {"key": "US_CORE_PCE_YOY", "label": "근원 PCE 상승률 (전년동월대비 %)"},
        {"key": "US_CORE_PCE_MOM", "label": "근원 PCE 상승률 (전월대비 %)"},
        {"key": "US_REAL_WAGE_YOY", "label": "실질임금 상승률 (전년동월대비 %)"},
        {"key": "US_REAL_RATE", "label": "실질금리 (기준금리 - CPI YoY%)"},
        {"key": "US_M2_GDP", "label": "M2 / 명목GDP"},
        # ⚠️ 성장·소비 변화율 라운드(2026-08) — GDP QoQ(연율)는 복리 환산이 필요해 BEA 공식
        # 시리즈를 "성장·소비" 카탈로그에 직접 등록했지만(valley/macro.py 주석 참고), YoY는
        # 단순 pct_change(4)로 안전하게 재현되어(실측: BEA 공식 YoY 시리즈와 소수점까지 일치)
        # 여기서 자체 계산한다. 소매판매(RSAFS)는 실측 확인 결과 이미 계절조정(SA) 계열이라
        # (Dec→Jan 급락 없음, Census의 NSA 대응 계열 MRTSSM44X72USN과 대조해 SA임을 확인)
        # MoM에 별도 SA 계열을 찾을 필요 없이 그대로 mom_percent/yoy_percent를 적용한다.
        {"key": "US_GDP_REAL_YOY", "label": "실질 GDP 성장률 (전년동기비%)"},
        {"key": "US_GDP_NOMINAL_YOY", "label": "명목 GDP 성장률 (전년동기비%)"},
        {"key": "US_RSAFS_MOM", "label": "소매판매 (전월비%)"},
        {"key": "US_RSAFS_YOY", "label": "소매판매 (전년동월비%)"},
        # 주택착공건수(HOUST)는 %변화 대신 3개월 이동평균(수준)을 제공한다 — 실측 MoM이
        # ±15~19%p까지 튀어(2026-05 -15.2%, 2026-06 +19.0%) 원계열 %변화 버튼은 오독을 부른다고
        # 판단했다. 소비자심리지수(UMCSENT)는 지역 연준 확산지수와 같은 이유로(레벨 자체가
        # "심리 수준"으로 읽히는 지표) 변화율을 추가하지 않았다 — 기존 지역 연준 서베이가 YoY
        # 없이 레벨만 제공하는 것과 동일 원칙.
        {"key": "US_HOUST_MA3", "label": "주택착공건수 (3개월 이동평균)"},
    ],
    "KR": [
        {"key": "KR_CPI_YOY", "label": "CPI 상승률 (전년동월대비 %)"},
        # ⚠️ 물가 재편 라운드에서 미국만 CPI MoM(US_CPI_MOM)을 추가했었다 — 한국은 ECOS에
        # CPI 계절조정(SA) 계열이 없어(실측 확인) 원계열(NSA) 그대로 mom_percent를 적용한다.
        # description이 없는 혼합지표 버튼 구조상 이 사실을 라벨에 못 담아 아래 캡션(뷰)에서
        # 안내한다. 카탈로그 파생(INDICATORS)이 아니라 여기 혼합 지표에 둔 이유: 같은 KR_CPI_YOY
        # 옆에 나란히 있어야 "이미 YoY가 있는데 MoM만 빠졌다"는 사용자 관찰과 정확히 대응되고,
        # 새로운 원본 데이터 소스가 아니라 기존 KR_CPI를 mom_percent()로 변환한 값이라 카탈로그
        # (원본 지표 전용)보다 혼합 지표(파생 지표 전용) 성격에 맞다.
        {"key": "KR_CPI_MOM", "label": "CPI 상승률 (전월대비 %)"},
        # 한국 GDP는 미국과 달리 연율화(SAAR)하지 않고 전분기비를 그대로 쓰는 것이 한국은행의
        # 관행이다 — 실측 확인: FRED 공식 시리즈 NAEXKP01KRQ657S(전분기비)·KORGDPRQPSMEI
        # (전년동기비)가 KR_GDP 레벨에서 직접 계산한 pct_change(1)/(4)와 소수점까지 정확히
        # 일치했다(연율화 없는 단순 비율이라 자체 계산이 안전 — 미국 QoQ와 다른 지점). 그래서
        # 별도 fred_id 등록 없이 여기서 자체 계산한다.
        {"key": "KR_GDP_REAL_QOQ", "label": "실질 GDP 성장률 (전분기비%)"},
        {"key": "KR_GDP_REAL_YOY", "label": "실질 GDP 성장률 (전년동기비%)"},
        {"key": "KR_REAL_RATE", "label": "실질금리 (기준금리 - CPI YoY%)"},
        {"key": "KR_M2_GDP", "label": "M2 / 명목GDP"},
        {"key": "KR_EXPORTS_YOY", "label": "수출 증가율 (전년동월대비 %)"},
        # ⚠️ 국채 라운드(2026-08) — 미국은 FRED가 이미 계산해 제공하는 장단기 금리차
        # 시리즈(T10Y2Y/T10Y3M, INDICATORS["US"]["금리"]에 fred_id로 직접 등록)를 그대로
        # 쓰지만, FRED는 한국 국채 스프레드를 제공하지 않는다 — 그래서 기존
        # US_KR_RATE_SPREAD(한미 정책금리차)와 같은 "직접 빼는" 방식으로 계산한다. 토스
        # 국채 카탈로그(KR_BOND_2Y/3Y/10Y)를 재사용하므로 TOSS_CLIENT_ID가 필요하다(없으면
        # MacroDataError가 그대로 노출됨 — 새 에러 처리 불필요). ⚠️ 물가 재편 라운드에서
        # 라벨의 ", 토스 일별" 표기를 뺐다 — 혼합 지표 버튼은 애초에 description/툴팁이 없어
        # (기존 카탈로그 버튼과 다른 구조) 소스 표기를 옮길 곳이 없지만, "10Y-2Y"/"10Y-3Y"
        # 자체가 이미 다른 항목과 헷갈릴 일이 없는 충분한 구분이라 소스 없이도 명확하다.
        {"key": "KR_YIELD_SPREAD_10Y2Y", "label": "국채 장단기 금리차 (10Y-2Y)"},
        {"key": "KR_YIELD_SPREAD_10Y3Y", "label": "국채 장단기 금리차 (10Y-3Y)"},
    ],
}

# 국가 구분이 없는(두 나라를 함께 봐야 하는) 지표.
CROSS_COUNTRY_INDICATORS = [
    {"key": "US_KR_RATE_SPREAD", "label": "한미 정책금리차 (미국 - 한국)"},
    # ⚠️ 국채 라운드(2026-08) — 위 정책금리차와는 다른 지표다. 정책금리는 중앙은행이
    # 결정하는 값(자주 안 바뀜)이고, 국채10년은 시장이 매일 새로 매기는 장기금리라 성격이
    # 다르다(예: 정책금리가 그대로여도 국채금리는 매일 움직인다). 둘 다 남겨 라벨로
    # 구분한다 — 기존 정책금리차를 대체하지 않는다. ⚠️ 물가 재편 라운드에서 ", 토스 일별"
    # 표기를 뺐다 — "정책금리"/"국고채 10년"이라는 지표 종류 자체가 이미 두 항목을 구분하는
    # 충분한 정보라 소스 표기 없이도 헷갈리지 않는다.
    {"key": "US_KR_BOND10Y_SPREAD", "label": "한미 금리차 (국고채 10년)"},
]


def yoy_percent(series: pd.Series, periods: int = 12) -> pd.Series:
    """월별 시계열을 N개월 전 대비 변화율(%)로 변환한다 (기본 12=전년동월대비/YoY).

    CPI 구성항목(주거비/식품/에너지 등)마다 별도 혼합지표를 등록하는 대신, 어떤 카탈로그
    지표든 이 범용 함수 하나로 YoY 변환할 수 있게 한다(Macro Indicator 페이지의 "CPI
    구성항목 한눈에 보기" 뷰가 이걸 재사용한다). 데이터가 periods개월보다 짧으면 빈
    시리즈가 나올 수 있으니 호출부가 빈 결과를 처리해야 한다.
    """
    return (series.pct_change(periods) * 100).dropna()


def mom_change(series: pd.Series, periods: int = 1) -> pd.Series:
    """월별 시계열의 전월 대비 절대 증감(수준 차분)을 반환한다.

    고용자 수(천 명) 같은 "수준" 계열은 YoY %가 아니라 절대 증감(천 명)으로 보는 게 시장
    표준이다 — 퍼센트 변화는 이미 규모가 큰 기저값(1억 5천만 명대) 때문에 월별 증감의 체감
    규모를 가려버린다. 지역 연준 서베이의 point-diff(전월 대비 포인트 차이)와 같은 이유로,
    확산지수가 아니라 고용자 수 계열에도 차분을 그대로 적용한다.
    """
    return series.diff(periods).dropna()


def mom_percent(series: pd.Series) -> pd.Series:
    """전월 대비 변화율(%) — yoy_percent(periods=1)과 동일한 계산이지만, 호출부에서 "전월대비"라는
    의도를 이름으로 드러내기 위한 별칭이다. 평균 시간당 임금처럼 "수준(달러)"이 아니라
    변화율로 봐야 하는 지표는 MoM·YoY 둘 다 필요한데, 기존 yoy_percent가 이미 범용
    N개월전 대비 함수라 periods=1만 넘기면 새 계산 로직 없이 그대로 재사용할 수 있다.
    """
    return yoy_percent(series, periods=1)


def compute_cpi_contributions(
    components: list[dict], rate_key: str = "yoy", rate_label: str = "YoY(%)"
) -> tuple[pd.DataFrame, float]:
    """구성항목별 (가중치, 변화율)로 헤드라인 기여도(%p)를 분해한다.

    contribution_i ≈ (weight_pct_i / 100) × rate_i — 항목별 실측 가중치와 이미 공통 기준월로
    정렬된 변화율(%)을 곱하는 단순 가산 근사다(호출부가 정렬/공통월 선택을 책임진다. 이 함수는
    이미 정렬된 값만 받아 곱셈만 한다). components는 각 {"label", "weight_pct", rate_key} dict.
    반환: (표 DataFrame[항목/가중치(%)/{rate_label}/기여도(%p)], 기여도 합계(%p)) — 합계를
    헤드라인 값과 비교하는 자기검증은 호출부(뷰)가 담당한다(차이가 크면 억지로 맞추지 말고
    그대로 드러내야 하므로, 이 순수 함수는 판단 없이 계산 결과만 반환한다).

    ⚠️ rate_key/rate_label은 MoM 기여도 라운드에서 추가했다 — 기본값(yoy/YoY(%))이 기존 YoY
    호출부와 완전히 동일해 회귀가 없다. MoM 기여도 뷰는 rate_key="mom", rate_label="MoM(%)"로
    같은 함수를 그대로 재사용한다(새 계산 로직을 만들지 않음).
    """
    rows = [
        {
            "항목": c["label"],
            "가중치(%)": c["weight_pct"],
            rate_label: c[rate_key],
            "기여도(%p)": c["weight_pct"] / 100 * c[rate_key],
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


def _index_yoy(key: str, label: str | None = None, periods: int = 12) -> pd.Series:
    """물가 재편 라운드(2026-08)에서 추가 — 임의의 지수 카탈로그 key를 전년동월대비 상승률(%)로
    변환하는 범용 헬퍼다. _cpi_yoy가 "{country}_CPI" 패턴으로 하드코딩됐던 것과 달리, 근원
    CPI·PCE·근원 PCE 등 어떤 지수 키에도 재사용한다(새 계산 로직 없이 yoy_percent() 재사용).

    ⚠️ periods는 성장·소비 변화율 라운드(2026-08)에서 추가한 파라미터다(기본값 12는 기존
    월별 호출부와 완전히 동일해 회귀 없음). GDP처럼 분기 데이터는 periods=4가 "전년동기비"이고,
    한국 GDP의 "전분기비"(연율화 없이 그대로, valley/macro.py "성장·소비" 카테고리 주석 참고)는
    periods=1로 이 함수를 재사용하는 대신 아래 _index_mom(동일 계산)을 쓴다.
    """
    series = get_indicator_series(key)
    yoy = yoy_percent(series, periods=periods)
    if yoy.empty:
        raise MacroDataError(f"{label or key} 상승률(YoY)을 계산할 만큼 데이터가 충분하지 않습니다.")
    return yoy


def _index_mom(key: str, label: str | None = None) -> pd.Series:
    """_index_yoy와 짝을 이루는 전월대비(%) 버전 — mom_percent() 재사용. ⚠️ 호출부가 넘기는
    key는 전부 "물가" 카테고리의 계절조정(SA) 지수(CPIAUCSL 등)라 MoM에 SA를 쓰라는 원칙을
    만족한다(호출부에서 원계열을 넘기지 않도록 주의)."""
    series = get_indicator_series(key)
    mom = mom_percent(series)
    if mom.empty:
        raise MacroDataError(f"{label or key} 상승률(MoM)을 계산할 만큼 데이터가 충분하지 않습니다.")
    return mom


def _moving_average(key: str, window: int, label: str | None = None) -> pd.Series:
    """성장·소비 변화율 라운드(2026-08)에서 추가 — 단일 지표 원계열의 N개월 이동평균(수준,
    %가 아님). 주택착공건수(HOUST)처럼 월별 변동성이 극심한(실측: MoM ±15~19%p) 계열은
    %변화 버튼을 그대로 붙이면 오히려 오독을 부른다고 판단해, 대신 판다스 rolling만 적용한
    스무딩된 수준을 제공한다(새 계산 로직 없이 rolling().mean() 재사용).
    """
    series = get_indicator_series(key)
    ma = series.rolling(window).mean().dropna()
    if ma.empty:
        raise MacroDataError(f"{label or key} {window}개월 이동평균을 계산할 만큼 데이터가 충분하지 않습니다.")
    return ma


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


def _kr_yield_spread(short_key: str) -> pd.Series:
    """한국 국채 장단기 금리차 = 국고채10년(토스 일별) - short_key(토스 일별), 겹치는 구간만.

    ⚠️ 미국의 T10Y2Y/T10Y3M은 FRED가 이미 계산해 제공하는 시리즈를 그대로 쓰지만(금리
    카탈로그에 fred_id로 직접 등록), FRED는 한국 국채 스프레드를 제공하지 않아 기존
    한미 정책금리차(_rate_spread)와 같은 "직접 빼는" 방식으로 계산한다.
    """
    long_rate = get_indicator_series("KR_BOND_10Y")
    short_rate = get_indicator_series(short_key)
    df = pd.concat([long_rate.rename("long"), short_rate.rename("short")], axis=1, join="inner")
    if df.empty:
        raise MacroDataError("한국 장단기 금리차를 계산할 만큼 겹치는 데이터가 없습니다.")
    return (df["long"] - df["short"]).dropna()


def _rate_spread_bond10y() -> pd.Series:
    """한미 금리차(국고채10년) = 미국 10년 국채금리(FRED, 일별) - 한국 국고채10년(토스, 일별).

    ⚠️ 기존 US_KR_RATE_SPREAD(정책금리차, 월별)와는 다른 지표다 — 정책금리는 중앙은행이
    결정하는 값이고 국채10년은 시장이 매기는 장기금리라 성격이 다르다. 둘 다 일별이라
    기존 정책금리차(월별 두 시리즈)보다 더 촘촘하게 겹친다.
    """
    us_rate = get_indicator_series("US_DGS10")
    kr_rate = get_indicator_series("KR_BOND_10Y")
    df = pd.concat([us_rate.rename("us"), kr_rate.rename("kr")], axis=1, join="inner")
    if df.empty:
        raise MacroDataError("한미 금리차(국고채10년)를 계산할 만큼 겹치는 데이터가 없습니다.")
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
    "KR_YIELD_SPREAD_10Y2Y": lambda: _kr_yield_spread("KR_BOND_2Y"),
    "KR_YIELD_SPREAD_10Y3Y": lambda: _kr_yield_spread("KR_BOND_3Y"),
    "US_KR_BOND10Y_SPREAD": _rate_spread_bond10y,
    # ⚠️ 물가 재편 라운드(2026-08) — 전부 "물가" 카테고리의 기존 SA 지수에서 파생(새 소스
    # 없음). 근원 CPI MoM은 이 라운드의 "현재 문제" 1번으로 명시된 바로 그 빠진 지표다.
    "US_CPI_MOM": lambda: _index_mom("US_CPI", "CPI"),
    "US_CORE_CPI_YOY": lambda: _index_yoy("US_CORE_CPI", "근원 CPI"),
    "US_CORE_CPI_MOM": lambda: _index_mom("US_CORE_CPI", "근원 CPI"),
    "US_PCE_YOY": lambda: _index_yoy("US_PCE", "PCE"),
    "US_PCE_MOM": lambda: _index_mom("US_PCE", "PCE"),
    "US_CORE_PCE_YOY": lambda: _index_yoy("US_CORE_PCE", "근원 PCE"),
    "US_CORE_PCE_MOM": lambda: _index_mom("US_CORE_PCE", "근원 PCE"),
    "US_REAL_WAGE_YOY": lambda: _index_yoy("US_REAL_AVG_HOURLY_EARNINGS", "실질임금"),
    # ⚠️ 성장·소비 변화율 라운드(2026-08) — 위 COMPOSITE_INDICATORS 목록의 주석 참고.
    "US_GDP_REAL_YOY": lambda: _index_yoy("US_GDPC1", "실질 GDP", periods=4),
    "US_GDP_NOMINAL_YOY": lambda: _index_yoy("US_GDP_NOMINAL", "명목 GDP", periods=4),
    "US_RSAFS_MOM": lambda: _index_mom("US_RSAFS", "소매판매"),
    "US_RSAFS_YOY": lambda: _index_yoy("US_RSAFS", "소매판매"),
    "US_HOUST_MA3": lambda: _moving_average("US_HOUST", 3, "주택착공건수"),
    "KR_CPI_MOM": lambda: _index_mom("KR_CPI", "CPI"),
    "KR_GDP_REAL_QOQ": lambda: _index_mom("KR_GDP", "실질 GDP"),
    "KR_GDP_REAL_YOY": lambda: _index_yoy("KR_GDP", "실질 GDP", periods=4),
}


def get_composite_series(key: str) -> pd.Series:
    """카탈로그의 key로 혼합 지표 시계열을 조회하는 단일 진입점."""
    if key not in _COMPUTE:
        raise MacroDataError(f"'{key}'는 등록되지 않은 혼합 지표입니다.")
    return _COMPUTE[key]()
