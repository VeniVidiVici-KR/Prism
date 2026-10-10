"""데이터 건강검진 — 카탈로그 시리즈의 신선도(최신 관측월) 판정 로직 (건강검진 라운드, 2026-10).

⚠️ 왜 만들었나 — 2026-09~10 한 달 동안 터진 버그가 전부 "데이터가 조용히 틀려 있었다"는 같은
패턴이었다: 한국 CPI의 FRED 미러가 2023-11에 멈춤, M2 미러가 2017-05에 멈춤, "기준금리"가 실은
콜금리(1991년부터 있는 다른 계열), ADR의 주가/재무제표 통화 불일치. 전부 사람이 수동으로
캐물어서 발견했다 — 이 모듈은 그 점검을 상시화한다.

⚠️ 주기(일별/월별/분기/연간)는 카탈로그에 메타데이터가 없어 **관측 간격에서 추론**한다. 105개
항목에 frequency 필드를 일일이 다는 대신 실제 데이터로 판단하므로, 소스가 발표 주기를 바꿔도
따로 손댈 필요가 없다(실측: 월별 31일·일별 1일·연간 365일로 깔끔하게 갈린다).

이 모듈은 네트워크를 타지 않는 순수 함수만 둔다 — 실제 조회·반복은 뷰(views/data_health.py)가
맡는다(데이터 레이어는 계산, 게이팅·조회는 뷰라는 이 프로젝트의 기존 분업 그대로).
"""

import os
from datetime import date

import pandas as pd

# 추론된 중앙 관측 간격(일) -> 사람이 읽는 주기 이름. 경계는 느슨하게 잡는다 — 월별이
# 28~31일로 흔들리고 분기도 90~92일로 흔들리기 때문이다.
_FREQUENCY_BUCKETS = [
    (4, "일별"),
    (10, "주별"),
    (45, "월별"),
    (120, "분기"),
    (400, "연간"),
]

# 허용 지연 = 중앙 간격 × 주기별 배수.
#
# ⚠️ 배수를 주기마다 다르게 둔 건 실측 때문이다. 처음엔 일괄 3배였는데, 그러면 월별 허용이
# 93일이라 OECD/ECOS 경유 월별 지표(한국 실업률·산업생산·M2)가 전부 걸렸다 — 셋 다 관측
# 간격은 30일로 규칙적이고 최신값만 101일 지난, 즉 "멈춘 게 아니라 원래 발표가 늦는" 계열
# 이었다(국제 미러는 각국 통계를 모아 재공표하느라 90~100일대 지연이 일상이다). 월별만
# 4배(약 122일)로 올려 그 자연 지연을 통과시킨다 — 진짜 멈춘 사례(한국 CPI 미러 1,050일,
# M2 미러 3,400일)와는 자릿수가 달라 여전히 확실히 걸린다.
#
# 분기·연간은 3배를 유지한다. 실측으로 잡아야 할 것들이 그 선에 걸려 있다: 한국 명목 GDP는
# 분기 지표인데 2개 분기가 비어 282일(허용 274일), 한국 국가부채/GDP는 연간인데 1,378일
# (허용 1,095일, 같은 IMF WEO의 미국 계열은 1,013일로 정상 범위) 경과였다.
_TOLERANCE_BY_FREQUENCY = {
    "일별": 3.0,
    "주별": 3.0,
    "월별": 4.0,
    "분기": 3.0,
    "연간": 3.0,
}
_DEFAULT_TOLERANCE = 3.0

# 일별 지표가 연휴로 며칠 비는 건 정상이라 하한을 둔다(중앙 간격 1일 × 3 = 3일이면
# 긴 연휴마다 오탐한다). 10일 넘게 비면 그건 주말 문제가 아니다.
_MIN_ALLOWED_DAYS = 10


def infer_frequency_days(series: pd.Series, window: int = 13) -> float | None:
    """최근 관측치들의 간격(일) 중앙값을 반환한다. 관측치가 2개 미만이면 None.

    평균이 아니라 중앙값을 쓴다 — 중간에 결측이 한 번 끼면 평균은 통째로 끌려가지만
    중앙값은 버틴다. window를 최근 구간으로 제한하는 것도 같은 이유다(과거에 주기가
    달랐던 계열이 현재 주기 판정을 흐리지 않게).
    """
    if series is None or len(series) < 2:
        return None
    recent = series.index[-window:]
    gaps = pd.Series(recent).diff().dt.days.dropna()
    if gaps.empty:
        return None
    return float(gaps.median())


def frequency_label(median_gap_days: float | None) -> str:
    """중앙 간격(일)을 '일별/주별/월별/분기/연간/불규칙'으로 옮긴다."""
    if median_gap_days is None:
        return "알 수 없음"
    for upper, name in _FREQUENCY_BUCKETS:
        if median_gap_days <= upper:
            return name
    return "불규칙"


def classify_freshness(series: pd.Series, as_of: date | None = None) -> dict:
    """시계열의 신선도를 판정한다.

    반환: {"latest", "observations", "median_gap_days", "frequency", "age_days",
           "allowed_days", "stale"}. 판정 불가(빈 시계열)면 stale=True로 둔다 —
    "모르겠음"을 정상으로 넘기면 건강검진의 의미가 없다.
    """
    as_of = as_of or date.today()
    if series is None or series.empty:
        return {
            "latest": None, "observations": 0, "median_gap_days": None,
            "frequency": "알 수 없음", "age_days": None, "allowed_days": None, "stale": True,
        }

    latest = pd.Timestamp(series.index[-1]).date()
    median_gap = infer_frequency_days(series)
    frequency = frequency_label(median_gap)
    age_days = (as_of - latest).days
    tolerance = _TOLERANCE_BY_FREQUENCY.get(frequency, _DEFAULT_TOLERANCE)
    allowed = max(median_gap * tolerance, _MIN_ALLOWED_DAYS) if median_gap else None

    return {
        "latest": latest,
        "observations": len(series),
        "median_gap_days": median_gap,
        "frequency": frequency,
        "age_days": age_days,
        "allowed_days": allowed,
        # 간격을 못 구하면(관측 2개 미만) 판정 근거가 없으므로 지연으로 분류해 눈에 띄게 한다.
        "stale": True if allowed is None else age_days > allowed,
    }


def describe_source(indicator: dict) -> str:
    """카탈로그 항목에서 "실제로 어디서 받아오는지"를 한 줄로 만든다.

    ⚠️ 폴백이 걸린 항목(ECOS 우선 + FRED 폴백)은 둘 다 적는다 — 어느 쪽이 쓰였는지는
    조회 결과(series.name)를 봐야 알 수 있고, 그 판정은 호출부가 붙인다.
    """
    if indicator.get("fed_target_combo"):
        return "FRED 조합(DFEDTAR+DFEDTARU/L)"
    parts = []
    ecos = indicator.get("ecos")
    if ecos:
        parts.append(f"ECOS {ecos['stat_code']}/{ecos['item_code']}")
    if indicator.get("toss_bond"):
        parts.append(f"토스 {indicator['toss_bond']}")
    if indicator.get("bls_id"):
        parts.append(f"BLS {indicator['bls_id']}")
    if indicator.get("fred_id"):
        parts.append(f"FRED {indicator['fred_id']}" + (" (폴백)" if ecos else ""))
    return " → ".join(parts) if parts else "알 수 없음"


def missing_credentials(indicator: dict) -> list[str]:
    """이 항목을 조회하려면 필요한데 환경에 없는 크레덴셜 이름들을 반환한다.

    ⚠️ 조회 실패 사유를 에러 "문구"로 판별하면 소스마다 메시지 형식이 달라 샌다(실측: ECOS/
    BLS는 "..._API_KEY가 설정되어 있지 않습니다"인데 토스는 "TOSS_CLIENT_ID/TOSS_CLIENT_SECRET가
    ..."라 'API_KEY' 매칭에 안 걸려 토스 6건이 전부 '조회 실패'로 분류됐다). 카탈로그 항목이
    어느 소스를 쓰는지는 이미 알고 있으니 환경변수를 직접 보는 쪽이 정확하다.

    ⚠️ ECOS+FRED 폴백 항목(KR_CPI 등)은 키가 없어도 FRED로 값이 나오므로 "키 없음"이 아니다 —
    ECOS 전용(fred_id 없음)일 때만 조회가 막힌다.
    """
    missing = []
    if indicator.get("ecos") and not indicator.get("fred_id") and not os.environ.get("ECOS_API_KEY"):
        missing.append("ECOS_API_KEY")
    if indicator.get("bls_id") and not os.environ.get("BLS_API_KEY"):
        missing.append("BLS_API_KEY")
    if indicator.get("toss_bond") and not (
        os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET")
    ):
        missing.append("TOSS_CLIENT_ID/TOSS_CLIENT_SECRET")
    return missing


def used_fred_fallback(series: pd.Series, indicator: dict) -> bool:
    """ECOS 우선 항목이 실제로는 FRED 폴백으로 조회됐는지 판정한다.

    get_fred_series는 반환 시리즈의 name을 원본 fred_id로 남기고 get_ecos_series는 name을
    붙이지 않는다(실측 확인) — views/stock/catalog.py의 폴백 안내와 같은 판정 방식이다.
    """
    fred_id = indicator.get("fred_id")
    if not indicator.get("ecos") or not fred_id or series is None:
        return False
    return getattr(series, "name", None) == fred_id
