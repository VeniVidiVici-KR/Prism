"""views/_charts.py의 actual_projection_line_chart 순수 로직 테스트 (전망 반영 라운드, 2026-08).

st.altair_chart를 mock해 실제 렌더링 없이 alt.Chart에 전달된 DataFrame만 검사한다 —
"실적/전망이 경계 연도에서 단절·중복 없이 이어지는지"가 이 기능의 핵심 요구사항이라
차트가 아니라 이 데이터 구성 로직 자체를 고정해둔다.
"""

from unittest.mock import patch

import pandas as pd
import pytest

import views._charts as charts_mod


def _capture_chart_data(series_pairs):
    captured = {}

    def fake_altair_chart(chart, **kwargs):
        captured["df"] = chart.data

    with patch.object(charts_mod.st, "altair_chart", side_effect=fake_altair_chart):
        charts_mod.actual_projection_line_chart(series_pairs)
    return captured["df"]


def test_boundary_year_appears_in_both_actual_and_projection_no_gap():
    """⚠️ 핵심 회귀 — 실적의 마지막 연도가 전망 구간에도 복제돼(값 동일) 시각적 공백이 없어야
    한다. 국가부채/GDP 조사 라운드에서 실측한 실제 경계(미국 2024→2025)와 같은 형태로 검증."""
    idx_actual = pd.date_range("2022-01-01", periods=3, freq="YS")
    actual = pd.Series([118.8, 119.0, 120.786], index=idx_actual)  # ...~2024
    idx_proj = pd.date_range("2025-01-01", periods=2, freq="YS")
    projection = pd.Series([122.464, 123.692], index=idx_proj)  # 2025~2026

    df = _capture_chart_data({"미국": (actual, projection)})

    boundary_rows = df[df["기간"] == "2024"]
    assert len(boundary_rows) == 2  # 실적 1행 + 전망 쪽 시작점(bridge) 1행 = 총 2행
    assert set(boundary_rows["구분"]) == {"실적", "전망"}
    # 두 행 모두 값이 실적의 마지막 값과 정확히 같아야 한다(단절 없음).
    assert all(v == pytest.approx(120.786) for v in boundary_rows["값"])

    # 경계 연도 이전(실적 전용)·이후(전망 전용)에는 각 연도가 한 번씩만 나와야 한다(중복 없음).
    assert len(df[df["기간"] == "2022"]) == 1
    assert len(df[df["기간"] == "2026"]) == 1
    assert len(df) == 3 + 1 + 2  # 실적 3행 + bridge 1행 + 전망 2행


def test_no_projection_renders_actual_only():
    """전망 데이터가 없으면(None) 실적만 그리고 bridge 행을 만들지 않는다."""
    idx_actual = pd.date_range("2022-01-01", periods=2, freq="YS")
    actual = pd.Series([50.0, 50.7], index=idx_actual)

    df = _capture_chart_data({"한국": (actual, None)})

    assert len(df) == 2
    assert set(df["구분"]) == {"실적"}


def test_multi_series_keeps_series_separate():
    """두 나라(계열)를 동시에 넣어도 서로 섞이지 않고 계열 컬럼으로 구분된다."""
    idx_a = pd.date_range("2023-01-01", periods=2, freq="YS")
    us_actual = pd.Series([119.0, 120.786], index=idx_a)
    idx_a_kr = pd.date_range("2022-01-01", periods=2, freq="YS")
    kr_actual = pd.Series([49.8, 50.697], index=idx_a_kr)
    idx_p_us = pd.date_range("2025-01-01", periods=1, freq="YS")
    us_proj = pd.Series([122.464], index=idx_p_us)
    idx_p_kr = pd.date_range("2024-01-01", periods=1, freq="YS")
    kr_proj = pd.Series([52.488], index=idx_p_kr)

    df = _capture_chart_data({"미국": (us_actual, us_proj), "한국": (kr_actual, kr_proj)})

    assert set(df["계열"]) == {"미국", "한국"}
    us_rows = df[df["계열"] == "미국"]
    kr_rows = df[df["계열"] == "한국"]
    # 각 나라의 실적 경계연도가 서로 다르다는 조사 결과(미국 2024, 한국 2023)와 일치하는지.
    us_bridge = us_rows[(us_rows["기간"] == "2024") & (us_rows["구분"] == "전망")]
    kr_bridge = kr_rows[(kr_rows["기간"] == "2023") & (kr_rows["구분"] == "전망")]
    assert len(us_bridge) == 1
    assert len(kr_bridge) == 1
