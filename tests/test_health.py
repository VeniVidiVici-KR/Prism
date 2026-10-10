"""valley/health.py 순수 함수 단위테스트 (건강검진 라운드, 2026-10).

⚠️ 임계값은 추측이 아니라 실측으로 정했다 — 아래 테스트들은 그 실측 사례(진짜 멈춘 계열 vs
발표가 원래 늦는 계열)를 그대로 고정해, 나중에 배수를 건드렸을 때 어느 쪽이 깨지는지 바로
드러나게 한다. 네트워크 없이 합성 시계열로만 검증한다.
"""

from datetime import date

import pandas as pd

from valley.health import (
    classify_freshness,
    describe_source,
    frequency_label,
    infer_frequency_days,
    missing_credentials,
    used_fred_fallback,
)


def _series(last: str, periods: int, freq: str) -> pd.Series:
    idx = pd.date_range(end=pd.Timestamp(last), periods=periods, freq=freq)
    return pd.Series(range(periods), index=idx, dtype=float)


# ── 주기 추론 ──────────────────────────────────────────────────────────────


def test_infer_frequency_monthly():
    assert infer_frequency_days(_series("2026-09-01", 24, "MS")) == 31.0


def test_infer_frequency_daily():
    assert infer_frequency_days(_series("2026-10-08", 30, "D")) == 1.0


def test_infer_frequency_annual():
    assert infer_frequency_days(_series("2025-01-01", 10, "YS")) == 365.0


def test_infer_frequency_needs_two_observations():
    assert infer_frequency_days(_series("2026-09-01", 1, "MS")) is None


def test_frequency_label_buckets():
    assert frequency_label(1.0) == "일별"
    assert frequency_label(31.0) == "월별"
    assert frequency_label(91.0) == "분기"
    assert frequency_label(365.0) == "연간"
    assert frequency_label(None) == "알 수 없음"


# ── 신선도 판정 ─────────────────────────────────────────────────────────────


def test_fresh_monthly_series_is_not_stale():
    """미국 FRED 월별 지표의 전형(발표 지연 ~40일)."""
    result = classify_freshness(_series("2026-09-01", 24, "MS"), as_of=date(2026, 10, 10))
    assert result["frequency"] == "월별"
    assert result["age_days"] == 39
    assert result["stale"] is False


def test_lagging_international_monthly_mirror_is_not_flagged():
    """⚠️ 실측 기반 — OECD/ECOS 경유 월별(한국 실업률·산업생산·M2)은 간격은 30일로
    규칙적인데 최신값만 101일 지난다. 멈춘 게 아니라 원래 발표가 늦는 계열이라 '지연'으로
    잡으면 안 된다(일괄 3배였을 때 이 3건이 전부 오탐이었다)."""
    result = classify_freshness(_series("2026-07-01", 24, "MS"), as_of=date(2026, 10, 10))
    assert result["age_days"] == 101
    assert result["stale"] is False, "자연 발표 지연을 '지연'으로 오탐하면 안 된다"


def test_genuinely_stopped_monthly_mirror_is_flagged():
    """⚠️ 실측 기반 — 한국 CPI의 FRED 미러가 2023-11에 멈춘 사례. 자연 지연(101일)과
    자릿수가 달라 월별 배수를 4배로 올려도 확실히 걸린다."""
    result = classify_freshness(_series("2023-11-01", 24, "MS"), as_of=date(2026, 10, 10))
    assert result["age_days"] > 1000
    assert result["stale"] is True


def test_quarterly_series_missing_two_quarters_is_flagged():
    """⚠️ 실측 기반 — 한국 명목 GDP(분기)가 2026-01이 최신이라 2개 분기가 비어 있었다."""
    result = classify_freshness(_series("2026-01-01", 20, "QS"), as_of=date(2026, 10, 10))
    assert result["frequency"] == "분기"
    assert result["stale"] is True


def test_annual_series_lagging_one_year_is_not_flagged():
    """⚠️ 실측 기반 — IMF WEO 실적은 1~2년 늦게 확정된다(미국 국가부채/GDP가 1,013일
    경과로 정상 범위). 연간 지표의 통상적 지연을 오탐하면 안 된다."""
    result = classify_freshness(_series("2024-01-01", 30, "YS"), as_of=date(2026, 10, 10))
    assert result["frequency"] == "연간"
    assert result["stale"] is False


def test_annual_series_lagging_three_years_is_flagged():
    """⚠️ 실측 기반 — 한국 국가부채/GDP는 2023-01이 최신(1,378일)으로, 같은 IMF WEO의
    미국 계열(2024-01)보다 한 해 더 뒤쳐져 있었다."""
    result = classify_freshness(_series("2023-01-01", 30, "YS"), as_of=date(2026, 10, 10))
    assert result["stale"] is True


def test_daily_series_tolerates_long_weekend():
    """일별 지표는 중앙 간격이 1일이라 3배면 3일 — 연휴마다 오탐한다. 하한 10일로 막는다."""
    result = classify_freshness(_series("2026-10-05", 60, "D"), as_of=date(2026, 10, 10))
    assert result["age_days"] == 5
    assert result["stale"] is False


def test_empty_series_is_treated_as_stale():
    """판정 근거가 없으면 '정상'이 아니라 '지연'으로 둔다 — 모르는 걸 통과시키면
    건강검진의 의미가 없다."""
    result = classify_freshness(pd.Series([], dtype=float, index=pd.DatetimeIndex([])))
    assert result["stale"] is True
    assert result["observations"] == 0


# ── 소스 표기 · 폴백 판정 ────────────────────────────────────────────────────


def test_describe_source_shows_ecos_with_fred_fallback():
    indicator = {"fred_id": "KORCPIALLMINMEI", "ecos": {"stat_code": "901Y009", "item_code": "0", "cycle": "M"}}
    assert describe_source(indicator) == "ECOS 901Y009/0 → FRED KORCPIALLMINMEI (폴백)"


def test_describe_source_plain_fred():
    assert describe_source({"fred_id": "FEDFUNDS"}) == "FRED FEDFUNDS"


def test_describe_source_fed_target_combo():
    assert "조합" in describe_source({"fed_target_combo": True})


def test_used_fred_fallback_detects_mirror_path():
    """get_fred_series는 반환 시리즈 name에 fred_id를 남기고 get_ecos_series는 안 남긴다."""
    indicator = {"fred_id": "KORCPIALLMINMEI", "ecos": {"stat_code": "901Y009", "item_code": "0", "cycle": "M"}}
    fred_result = _series("2023-11-01", 12, "MS").rename("KORCPIALLMINMEI")
    ecos_result = _series("2026-09-01", 12, "MS")

    assert used_fred_fallback(fred_result, indicator) is True
    assert used_fred_fallback(ecos_result, indicator) is False


def test_used_fred_fallback_false_for_fred_only_indicator():
    """ECOS 폴백 구조가 아닌 항목은 애초에 '폴백'이라는 상태가 없다."""
    indicator = {"fred_id": "FEDFUNDS"}
    assert used_fred_fallback(_series("2026-09-01", 12, "MS").rename("FEDFUNDS"), indicator) is False


# ── 키 부재 판정 (환경변수 직접 확인) ────────────────────────────────────────


def test_missing_credentials_for_ecos_only_indicator(monkeypatch):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    indicator = {"ecos": {"stat_code": "301Y013", "item_code": "000000", "cycle": "M"}}
    assert missing_credentials(indicator) == ["ECOS_API_KEY"]


def test_ecos_with_fred_fallback_is_not_key_blocked(monkeypatch):
    """⚠️ 폴백이 있는 항목은 ECOS 키가 없어도 FRED로 값이 나오므로 '키 없음'이 아니다."""
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    indicator = {"fred_id": "KORCPIALLMINMEI", "ecos": {"stat_code": "901Y009", "item_code": "0", "cycle": "M"}}
    assert missing_credentials(indicator) == []


def test_missing_credentials_for_toss_bond(monkeypatch):
    """⚠️ 실측 기반 — 토스 에러 문구에는 'API_KEY'가 없어서 문자열 매칭으로는 '조회 실패'로
    오분류됐다(6건). 환경변수를 직접 보면 정확히 '키 없음'으로 잡힌다."""
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    assert missing_credentials({"toss_bond": "KR_BOND_10Y"}) == ["TOSS_CLIENT_ID/TOSS_CLIENT_SECRET"]


def test_no_missing_credentials_when_keys_present(monkeypatch):
    monkeypatch.setenv("ECOS_API_KEY", "x")
    monkeypatch.setenv("BLS_API_KEY", "x")
    monkeypatch.setenv("TOSS_CLIENT_ID", "x")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "x")
    assert missing_credentials({"ecos": {"stat_code": "1", "item_code": "1", "cycle": "M"}}) == []
    assert missing_credentials({"bls_id": "CES0500000013"}) == []
    assert missing_credentials({"toss_bond": "KR_BOND_10Y"}) == []


def test_plain_fred_indicator_never_needs_credentials(monkeypatch):
    """FRED 공개 CSV는 키가 필요 없다 — 환경이 비어 있어도 '키 없음'이 되면 안 된다."""
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    assert missing_credentials({"fred_id": "FEDFUNDS"}) == []
