"""valley/composite.py 순수 함수 단위테스트 (yoy_percent, compute_cpi_contributions)."""

import pandas as pd
import pytest

from valley.composite import compute_cpi_contributions, yoy_percent


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
