"""valley/macro.py 순수 함수 단위테스트 (normalize_indexed). 네트워크 조회 함수(get_fred_series/
get_ecos_series/get_indicator_series 등)는 여기서 다루지 않는다 — 실제 조회는 AppTest
회귀(@pytest.mark.network)로 간접 검증한다.

⚠️ 예외다 — get_indicator_series의 toss_bond/bls_id 디스패치 분기와 get_bls_series의 응답
파싱 로직은 실제 네트워크 없이 mock해서 검증한다. "어느 소스로 위임하는가"·"BLS 응답을 어떻게
파싱하는가"는 순수 로직이라, 위 방침("네트워크 조회 함수는 여기서 다루지 않는다")이 막으려는
"느리고 크레덴셜에 좌우되는 실제 조회 테스트"에 해당하지 않는다."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from valley.macro import MacroDataError, get_bls_series, get_indicator_series, normalize_indexed


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


# ── get_bls_series / bls_id 디스패치 (물가 재편 라운드 2026-08, 실질임금) ────────────────────


def _bls_response(points, status="REQUEST_SUCCEEDED"):
    resp = MagicMock()
    resp.json.return_value = {"status": status, "Results": {"series": [{"data": points}]}}
    return resp


def test_get_bls_series_parses_monthly_points_and_excludes_m13(monkeypatch):
    """⚠️ 핵심 회귀 — M13(연간 평균)은 월별 시계열에서 제외해야 하고, 값은 그대로(단위 변환
    없이) 통과해야 한다."""
    monkeypatch.setenv("BLS_API_KEY", "dummy-key")
    points = [
        {"year": "2026", "period": "M07", "value": "11.30", "footnotes": [{}]},
        {"year": "2026", "period": "M06", "value": "11.31", "footnotes": [{}]},
        {"year": "2025", "period": "M13", "value": "11.20", "footnotes": [{}]},  # 연간 평균, 제외돼야 함
    ]
    with patch("valley.macro.requests.post", return_value=_bls_response(points)):
        series = get_bls_series("CES0500000013")
    assert len(series) == 2
    assert series.iloc[-1] == pytest.approx(11.30)
    assert pd.Timestamp("2025-12-01") not in series.index  # M13이 섞여들지 않았는지 확인


def test_get_bls_series_skips_unparseable_values_without_length_mismatch(monkeypatch):
    """⚠️ 핵심 회귀 — BLS가 결측치를 "-" 같은 숫자로 변환 불가능한 문자열로 줄 때(실측으로
    발견한 케이스, "2025년 예산 미승인으로 인한 데이터 공백"), 날짜와 값을 따로따로 리스트에
    append하면 한쪽만 실패해 길이가 어긋나 pd.Series 생성이 깨진다 — (날짜, 값) 튜플 단위로
    처리해 이 문제를 막았는지 확인한다."""
    monkeypatch.setenv("BLS_API_KEY", "dummy-key")
    points = [
        {"year": "2025", "period": "M12", "value": "11.35", "footnotes": [{}]},
        {"year": "2025", "period": "M10", "value": "-", "footnotes": [{"code": "A", "text": "unavailable"}]},
        {"year": "2025", "period": "M09", "value": "11.32", "footnotes": [{}]},
    ]
    with patch("valley.macro.requests.post", return_value=_bls_response(points)):
        series = get_bls_series("CES0500000013")  # 예외 없이 완료돼야 함
    assert len(series) == 2
    assert pd.Timestamp("2025-10-01") not in series.index


def test_get_bls_series_uses_inclusive_20_year_range_to_avoid_truncation(monkeypatch):
    """⚠️ 핵심 회귀 — BLS API는 21개년(예: 2006~2026) 요청 시 "20년으로 축소"하면서 최신
    쪽이 아니라 과거 쪽을 유지한다(실측으로 발견한 함정 — 최신 데이터가 잘림). startyear가
    `end_year - years + 1`로 정확히 20개년(포함 경계)이 되는지 확인한다."""
    monkeypatch.setenv("BLS_API_KEY", "dummy-key")
    with patch("valley.macro.requests.post", return_value=_bls_response([])) as mock_post, \
         patch("valley.macro.date") as mock_date:
        mock_date.today.return_value.year = 2026
        with pytest.raises(MacroDataError):  # 빈 데이터라 결국 실패하지만, 요청 파라미터만 확인
            get_bls_series("CES0500000013", years=20)
    payload = mock_post.call_args.kwargs["json"]
    assert payload["startyear"] == "2007"  # 2026 - 20 + 1
    assert payload["endyear"] == "2026"


def test_get_bls_series_missing_key_raises(monkeypatch):
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    with pytest.raises(MacroDataError, match="BLS_API_KEY"):
        get_bls_series("CES0500000013")


def test_get_indicator_series_dispatches_bls_id_key():
    """물가 재편 라운드 — "bls_id" 필드가 있는 카탈로그 항목(US_REAL_AVG_HOURLY_EARNINGS)은
    get_bls_series로 위임돼야 한다."""
    series = pd.Series([11.30], index=pd.to_datetime(["2026-07-01"]))
    with patch("valley.macro.get_bls_series", return_value=series) as mock_fn:
        result = get_indicator_series("US_REAL_AVG_HOURLY_EARNINGS")
    mock_fn.assert_called_once_with("CES0500000013")
    assert result.iloc[-1] == pytest.approx(11.30)
