"""valley/composite.py 순수 함수 단위테스트 (yoy_percent, compute_cpi_contributions,
mom_change, mom_percent — 고용보고서 라운드에서 추가)."""

from unittest.mock import patch

import pandas as pd
import pytest

from valley.composite import MacroDataError, compute_cpi_contributions, get_composite_series, mom_change, mom_percent, yoy_percent


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


# ── 국채 라운드(2026-08)에서 추가한 금리차 3종. get_indicator_series를 mock해 실제 네트워크
# 없이 "직접 빼는" 계산 로직만 검증한다(⚠️ valley.composite는 get_indicator_series를
# `from valley.macro import ... get_indicator_series`로 직접 바인딩하므로, mock 대상은
# "valley.composite.get_indicator_series"여야 한다 — KOFIA 라운드에서 겪은 import-alias
# 문제와 동일한 이유). ──────────────────────────────────────────────────────────────


def _series(values, dates):
    return pd.Series(values, index=pd.to_datetime(dates))


def test_kr_yield_spread_10y2y_subtracts_directly():
    """⚠️ 핵심 회귀 — 미국 T10Y2Y(FRED 사전계산)와 달리 한국은 FRED에 없어 10Y-2Y를 직접
    뺀다(기존 한미 정책금리차와 동일한 패턴)."""
    long_series = _series([4.30, 4.29], ["2026-08-11", "2026-08-12"])
    short_series = _series([3.62, 3.63], ["2026-08-11", "2026-08-12"])

    def fake_get_indicator_series(key):
        return {"KR_BOND_10Y": long_series, "KR_BOND_2Y": short_series}[key]

    with patch("valley.composite.get_indicator_series", side_effect=fake_get_indicator_series):
        result = get_composite_series("KR_YIELD_SPREAD_10Y2Y")
    assert result.iloc[-1] == pytest.approx(4.29 - 3.63)
    assert result.iloc[0] == pytest.approx(4.30 - 3.62)


def test_kr_yield_spread_10y3y_uses_3y_not_2y():
    """10Y-3Y와 10Y-2Y가 서로 다른 단기물을 참조하는지(오타로 같은 걸 두 번 빼지 않는지) 확인."""
    long_series = _series([4.29], ["2026-08-12"])
    short_series = _series([3.80], ["2026-08-12"])

    def fake_get_indicator_series(key):
        assert key in ("KR_BOND_10Y", "KR_BOND_3Y")
        return {"KR_BOND_10Y": long_series, "KR_BOND_3Y": short_series}[key]

    with patch("valley.composite.get_indicator_series", side_effect=fake_get_indicator_series):
        result = get_composite_series("KR_YIELD_SPREAD_10Y3Y")
    assert result.iloc[-1] == pytest.approx(4.29 - 3.80)


def test_us_kr_bond10y_spread_distinct_from_policy_spread():
    """⚠️ 핵심 회귀 — 국고채10년 기준 한미 금리차(US_DGS10 - KR_BOND_10Y)는 기존
    US_KR_RATE_SPREAD(정책금리차, FEDFUNDS - KR_BASE_RATE)와 다른 키·다른 소스여야 한다."""
    us_series = _series([4.69], ["2026-08-12"])
    kr_series = _series([4.295], ["2026-08-12"])

    def fake_get_indicator_series(key):
        assert key in ("US_DGS10", "KR_BOND_10Y")  # 정책금리(FEDFUNDS/KR_BASE_RATE)를 쓰면 안 됨
        return {"US_DGS10": us_series, "KR_BOND_10Y": kr_series}[key]

    with patch("valley.composite.get_indicator_series", side_effect=fake_get_indicator_series):
        result = get_composite_series("US_KR_BOND10Y_SPREAD")
    assert result.iloc[-1] == pytest.approx(4.69 - 4.295)


def test_kr_yield_spread_raises_macro_data_error_when_no_overlap():
    empty = pd.Series([], dtype=float, index=pd.DatetimeIndex([]))
    with patch("valley.composite.get_indicator_series", return_value=empty):
        with pytest.raises(MacroDataError):
            get_composite_series("KR_YIELD_SPREAD_10Y2Y")


# ── 물가 재편 라운드(2026-08) — CPI/PCE YoY·MoM 파생지표 7종 + 실질임금 YoY ──────────────────


def test_core_cpi_mom_uses_mom_percent_not_yoy():
    """⚠️ 핵심 회귀 — 이 라운드가 명시적으로 지적한 빠진 지표(근원 CPI MoM)다. mom_percent()를
    쓰지 yoy_percent()를 잘못 쓰지 않는지 값으로 구분해서 확인한다."""
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx)  # 매월 +1

    with patch("valley.composite.get_indicator_series", return_value=series) as mock_fn:
        result = get_composite_series("US_CORE_CPI_MOM")
    mock_fn.assert_called_once_with("US_CORE_CPI")
    # MoM(전월대비)이면 마지막 값(112 vs 111)의 변화율이지, YoY(전년동월비, 100 vs 112)가 아니다.
    assert result.iloc[-1] == pytest.approx((112 / 111 - 1) * 100)
    assert result.iloc[-1] != pytest.approx((112 / 100 - 1) * 100)  # YoY였다면 나올 값(오답 확인)


def test_core_cpi_yoy_uses_yoy_percent():
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx)

    with patch("valley.composite.get_indicator_series", return_value=series) as mock_fn:
        result = get_composite_series("US_CORE_CPI_YOY")
    mock_fn.assert_called_once_with("US_CORE_CPI")
    assert result.iloc[-1] == pytest.approx(12.0)  # 100 -> 112, 12개월 전 대비 12%


@pytest.mark.parametrize(
    "key,expected_source",
    [
        ("US_PCE_YOY", "US_PCE"),
        ("US_PCE_MOM", "US_PCE"),
        ("US_CORE_PCE_YOY", "US_CORE_PCE"),
        ("US_CORE_PCE_MOM", "US_CORE_PCE"),
        ("US_REAL_WAGE_YOY", "US_REAL_AVG_HOURLY_EARNINGS"),
    ],
)
def test_new_derived_indicators_read_correct_source_key(key, expected_source):
    """새로 추가한 5개 파생지표가 각각 의도한 원천 카탈로그 key에서 읽는지 확인한다(엉뚱한
    시리즈를 잘못 참조하는 복사-붙여넣기 실수를 잡기 위함)."""
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series) as mock_fn:
        get_composite_series(key)
    mock_fn.assert_called_once_with(expected_source)


def test_us_cpi_mom_uses_headline_not_core():
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series) as mock_fn:
        get_composite_series("US_CPI_MOM")
    mock_fn.assert_called_once_with("US_CPI")


# ── 성장·소비 변화율 라운드(2026-08) — GDP YoY(분기, periods=4)·소매판매 MoM/YoY·주택착공건수
# 이동평균(HOUST_MA3)·한국 CPI MoM·한국 GDP QoQ/YoY. ──────────────────────────────────────


@pytest.mark.parametrize(
    "key,expected_source",
    [
        ("US_GDP_REAL_YOY", "US_GDPC1"),
        ("US_GDP_NOMINAL_YOY", "US_GDP_NOMINAL"),
        ("US_RSAFS_MOM", "US_RSAFS"),
        ("US_RSAFS_YOY", "US_RSAFS"),
        ("KR_CPI_MOM", "KR_CPI"),
        ("KR_GDP_REAL_QOQ", "KR_GDP"),
        ("KR_GDP_REAL_YOY", "KR_GDP"),
    ],
)
def test_growth_consumption_indicators_read_correct_source_key(key, expected_source):
    """복사-붙여넣기로 엉뚱한 원천 키를 참조하지 않는지 확인한다."""
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    series = pd.Series([100 + i for i in range(13)], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series) as mock_fn:
        get_composite_series(key)
    mock_fn.assert_called_once_with(expected_source)


def test_gdp_real_yoy_uses_periods_4_not_12():
    """⚠️ 핵심 회귀 — GDP는 분기 데이터라 YoY는 4분기 전 대비여야 한다. 기존 _index_yoy
    기본값(periods=12, 월별 가정)을 그대로 썼다면 5개 분기 데이터로는 빈 시리즈가 나와
    MacroDataError가 터진다 — 그 경우 이 테스트가 실패로 잡아낸다."""
    idx = pd.date_range("2025-01-01", periods=5, freq="QS")
    series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series):
        result = get_composite_series("US_GDP_REAL_YOY")
    assert result.iloc[-1] == pytest.approx(4.0)  # (104-100)/100 * 100


def test_kr_gdp_real_yoy_uses_periods_4():
    idx = pd.date_range("2025-01-01", periods=5, freq="QS")
    series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series):
        result = get_composite_series("KR_GDP_REAL_YOY")
    assert result.iloc[-1] == pytest.approx(4.0)


def test_kr_gdp_real_qoq_uses_periods_1_not_4():
    """⚠️ 핵심 회귀 — 한국 GDP는 미국과 달리 연율화하지 않고 전분기비를 그대로 쓴다
    (valley/composite.py COMPOSITE_INDICATORS 주석의 실측 근거 참고). periods=4(YoY)와
    값으로 구분되는지 확인한다."""
    idx = pd.date_range("2025-01-01", periods=5, freq="QS")
    series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series):
        qoq = get_composite_series("KR_GDP_REAL_QOQ")
        yoy = get_composite_series("KR_GDP_REAL_YOY")
    assert qoq.iloc[-1] == pytest.approx((104 / 103 - 1) * 100)
    assert qoq.iloc[-1] != pytest.approx(yoy.iloc[-1])


def test_houst_ma3_is_rolling_mean_not_percent_change():
    """주택착공건수는 변동성이 극심해(실측 MoM ±15~19%p) %변화 대신 3개월 이동평균(수준)을
    제공한다 — 값이 %가 아니라 원래 단위 그대로의 평균인지 확인한다."""
    idx = pd.date_range("2025-01-01", periods=5, freq="MS")
    series = pd.Series([1000.0, 1100.0, 1200.0, 1300.0, 1400.0], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series):
        result = get_composite_series("US_HOUST_MA3")
    assert len(result) == 3  # 5개월 - (window-1) = 3개
    assert result.iloc[-1] == pytest.approx((1200 + 1300 + 1400) / 3)


def test_houst_ma3_raises_when_shorter_than_window():
    idx = pd.date_range("2025-01-01", periods=2, freq="MS")
    series = pd.Series([1000.0, 1100.0], index=idx)
    with patch("valley.composite.get_indicator_series", return_value=series):
        with pytest.raises(MacroDataError):
            get_composite_series("US_HOUST_MA3")
