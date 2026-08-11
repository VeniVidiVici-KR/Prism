"""valley/composite.py 순수 함수 단위테스트 (yoy_percent, compute_cpi_contributions,
mom_change, mom_percent — 고용보고서 라운드에서 추가)."""

import pandas as pd
import pytest

from valley.composite import compute_cpi_contributions, mom_change, mom_percent, yoy_percent


def test_yoy_percent_basic():
    idx = pd.date_range("2023-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx, dtype=float)
    result = yoy_percent(series)
    # 12개월 전(100) 대비 마지막 값(112)의 변화율 = 12%
    assert result.iloc[-1] == pytest.approx(12.0)
    assert len(result) == 1  # 13개월 데이터 중 12개월 전 비교가 가능한 건 마지막 1개뿐


def test_yoy_percent_too_short_returns_empty():
    idx = pd.date_range("2023-01-01", periods=6, freq="MS")
    series = pd.Series(range(6), index=idx, dtype=float)
    result = yoy_percent(series)
    assert result.empty


def test_compute_cpi_contributions_sums_to_expected_total():
    components = [
        {"label": "식품", "weight_pct": 13.447, "yoy": 3.29},
        {"label": "에너지", "weight_pct": 7.791, "yoy": 17.44},
        {"label": "근원 상품", "weight_pct": 18.737, "yoy": 1.20},
        {"label": "근원 서비스", "weight_pct": 60.025, "yoy": 3.39},
    ]
    df, total = compute_cpi_contributions(components)
    assert len(df) == 4
    assert set(df.columns) == {"항목", "가중치(%)", "YoY(%)", "기여도(%p)"}
    expected_total = sum(c["weight_pct"] / 100 * c["yoy"] for c in components)
    assert total == pytest.approx(expected_total)
    # 실측(2026-07 라운드)과 근접한 헤드라인 대조 범위인지도 함께 고정해둔다.
    assert 3.5 < total < 4.5


def test_compute_cpi_contributions_single_component():
    df, total = compute_cpi_contributions([{"label": "단일", "weight_pct": 100.0, "yoy": 5.0}])
    assert total == pytest.approx(5.0)


def test_compute_cpi_contributions_mom_rate_key_reuses_same_function():
    """MoM 기여도 라운드에서 추가한 rate_key/rate_label — 기본값(yoy/YoY(%))을 안 넘기면
    기존 동작과 완전히 동일해야 한다(회귀 없음), rate_key="mom"을 넘기면 그 필드로 계산해야
    한다(새 계산 로직을 만들지 않고 같은 함수를 재사용)."""
    components = [
        {"label": "주택,수도,전기및연료", "weight_pct": 17.16, "mom": -1.062843},
        {"label": "교통", "weight_pct": 11.06, "mom": -1.648005},
    ]
    df, total = compute_cpi_contributions(components, rate_key="mom", rate_label="MoM(%)")
    assert set(df.columns) == {"항목", "가중치(%)", "MoM(%)", "기여도(%p)"}
    assert "YoY(%)" not in df.columns
    expected_total = sum(c["weight_pct"] / 100 * c["mom"] for c in components)
    assert total == pytest.approx(expected_total)
    # 실측(2026-08 라운드, 한국 2026-07 MoM)과 근접한 범위인지 고정.
    assert -0.4 < total < -0.3


def test_mom_change_basic():
    idx = pd.date_range("2026-01-01", periods=4, freq="MS")
    # 고용자 수(천 명) 계열을 흉내: 절대 수준 차분(YoY %가 아님)이어야 한다.
    series = pd.Series([158700.0, 158780.0, 158858.0, 158835.0], index=idx)
    result = mom_change(series)
    assert len(result) == 3  # 4개월 데이터 -> 전월 대비 차분 3개
    assert result.iloc[0] == pytest.approx(80.0)
    assert result.iloc[-1] == pytest.approx(-23.0)  # 감소도 그대로 음수로 나와야 한다


def test_mom_change_first_observation_dropped():
    idx = pd.date_range("2026-01-01", periods=2, freq="MS")
    series = pd.Series([100.0, 105.0], index=idx)
    result = mom_change(series)
    assert len(result) == 1
    assert result.iloc[0] == pytest.approx(5.0)


def test_mom_percent_matches_yoy_percent_with_periods_one():
    idx = pd.date_range("2026-01-01", periods=3, freq="MS")
    series = pd.Series([37.41, 37.49, 37.60], index=idx)
    mom = mom_percent(series)
    equivalent = yoy_percent(series, periods=1)
    pd.testing.assert_series_equal(mom, equivalent)
    # 평균 시간당 임금처럼 소폭(0.1~0.4%대) 변화가 나오는지도 확인.
    assert 0.0 < mom.iloc[-1] < 1.0
