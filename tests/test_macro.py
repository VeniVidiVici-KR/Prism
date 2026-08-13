"""valley/macro.py 순수 함수 단위테스트 (normalize_indexed). 네트워크 조회 함수(get_fred_series/
get_ecos_series/get_indicator_series 등)는 여기서 다루지 않는다 — 실제 조회는 AppTest
회귀(@pytest.mark.network)로 간접 검증한다.

⚠️ 예외다 — get_indicator_series의 toss_bond 디스패치 분기(2026-08 국채 라운드 추가)는
실제 네트워크 없이 valley.toss.get_bond_yield_history를 mock해서 검증한다. "TossDataError를
MacroDataError로 변환한다"는 순수 디스패치 로직이라, 위 방침("네트워크 조회 함수는 여기서
다루지 않는다")이 막으려는 "느리고 크레덴셜에 좌우되는 실제 조회 테스트"에 해당하지 않는다."""

from unittest.mock import patch

import pandas as pd
import pytest

from valley.macro import MacroDataError, get_indicator_series, normalize_indexed


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


def test_get_indicator_series_dispatches_toss_bond_key():
    """국채 라운드(2026-08) — "toss_bond" 필드가 있는 카탈로그 항목(KR_BOND_10Y 등)은
    valley.toss.get_bond_yield_history로 위임돼야 한다(fred_id/ecos 경로를 타지 않음)."""
    series = pd.Series([4.295], index=pd.to_datetime(["2026-08-12"]))
    with patch("valley.toss.get_bond_yield_history", return_value=series) as mock_fn:
        result = get_indicator_series("KR_BOND_10Y")
    mock_fn.assert_called_once_with("KR_BOND_10Y")
    assert result.iloc[-1] == pytest.approx(4.295)


def test_get_indicator_series_converts_toss_data_error_to_macro_data_error():
    """⚠️ 핵심 회귀 — TOSS_CLIENT_ID가 없을 때 TossDataError가 그대로 새어나가지 않고
    기존 ECOS 전용 지표와 동일한 MacroDataError로 변환돼야 한다(호출부가 한 종류의
    예외만 처리하면 되도록)."""
    from valley.toss import TossDataError

    with patch("valley.toss.get_bond_yield_history", side_effect=TossDataError("키 없음")):
        with pytest.raises(MacroDataError, match="키 없음"):
            get_indicator_series("KR_BOND_10Y")


def test_get_indicator_series_kr_dgs10_unchanged_fred_path():
    """⚠️ 회귀 확인 — 기존 KR_DGS10(FRED 월별)은 라벨만 바뀌었을 뿐 fred_id 경로는 그대로다
    (toss_bond 분기를 타지 않는다)."""
    with patch("valley.macro.get_fred_series", return_value=pd.Series([3.5])) as mock_fred:
        get_indicator_series("KR_DGS10")
    mock_fred.assert_called_once_with("IRLTLT01KRM156N")
