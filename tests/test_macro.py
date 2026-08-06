"""valley/macro.py 순수 함수 단위테스트 (normalize_indexed). 네트워크 조회 함수(get_fred_series/
get_ecos_series/get_indicator_series 등)는 여기서 다루지 않는다 — 실제 조회는 AppTest
회귀(@pytest.mark.network)로 간접 검증한다."""

import pandas as pd
import pytest

from valley.macro import MacroDataError, normalize_indexed


def test_normalize_indexed_first_observation_becomes_base():
    series = pd.Series([50.0, 55.0, 60.0], index=pd.date_range("2024-01-01", periods=3, freq="MS"))
    result = normalize_indexed(series)
    assert result.iloc[0] == pytest.approx(100.0)
    assert result.iloc[1] == pytest.approx(110.0)
    assert result.iloc[2] == pytest.approx(120.0)


def test_normalize_indexed_custom_base():
    series = pd.Series([10.0, 20.0], index=pd.date_range("2024-01-01", periods=2, freq="MS"))
    result = normalize_indexed(series, base=1.0)
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[1] == pytest.approx(2.0)


def test_normalize_indexed_rejects_empty_series():
    with pytest.raises(MacroDataError):
        normalize_indexed(pd.Series([], dtype=float))


def test_normalize_indexed_rejects_zero_first_observation():
    series = pd.Series([0.0, 10.0], index=pd.date_range("2024-01-01", periods=2, freq="MS"))
    with pytest.raises(MacroDataError):
        normalize_indexed(series)
