"""CPI 중분류 라운드(2026-09) — 배치 조회 캐싱 검증 (네트워크 없음, 전부 mock).

⚠️ 뷰(AppTest)로 버튼을 눌러 검증하면 4분할 표를 띄우기 위한 실제 BLS/FRED 호출이 먼저
나가므로 fast tier에 둘 수 없다. 캐시 동작 자체는 views.macro.cpi_contribution의 캐시 래퍼를
직접 호출해 확인한다(424B 캐시 테스트와 같은 방식).
"""

from unittest.mock import patch

import pandas as pd

from views.macro import cpi_contribution as mod


def _fake_entry(value: float, ri: float | None = None) -> dict:
    return {
        "series": pd.Series([value], index=pd.to_datetime(["2026-08-01"])),
        "relative_importance": ri,
        "ri_as_of": "2026-08" if ri is not None else None,
    }


def test_bls_batch_cached_by_ids_and_aspects_flag():
    """같은 (시리즈 목록, aspects) 조합으로 다시 부르면 재호출이 없어야 하고, 조합이 달라지면
    새로 나가야 한다 — MoM 뷰가 값(SA)과 가중치(NSA)를 각각 다른 배치로 받기 때문에 두 조합이
    서로 캐시를 덮어쓰면 안 된다."""
    calls: list[tuple] = []

    def _fake(ids, years=3, with_aspects=False):
        calls.append((tuple(ids), with_aspects))
        return {sid: _fake_entry(100.0, 1.0 if with_aspects else None) for sid in ids}

    nsa = ("CUUR0000SAF11", "CUUR0000SEFV")
    sa = ("CUSR0000SAF11", "CUSR0000SEFV")

    mod._load_bls_batch.clear()
    with patch("views.macro.cpi_contribution.get_bls_series_batch", side_effect=_fake):
        mod._load_bls_batch(nsa, True)
        mod._load_bls_batch(nsa, True)  # 캐시 히트 — 재호출 없음
        mod._load_bls_batch(sa, False)  # 다른 시리즈 목록 — 새 호출
        mod._load_bls_batch(nsa, False)  # 같은 목록이어도 aspects가 다르면 새 호출

    assert len(calls) == 3
    assert calls[0] == (nsa, True)
    assert calls[1] == (sa, False)
    assert calls[2] == (nsa, False)


def test_bls_batch_wrapper_passes_three_year_window():
    """YoY(12개월 차분)에 필요한 최소치보다 넉넉하되 20년 잘림 함정에 걸리지 않는 3년 창을
    쓰는지 고정한다."""
    seen: list[int] = []

    def _fake(ids, years=3, with_aspects=False):
        seen.append(years)
        return {}

    mod._load_bls_batch.clear()
    with patch("views.macro.cpi_contribution.get_bls_series_batch", side_effect=_fake):
        mod._load_bls_batch(("CUUR0000SAF11",), True)

    assert seen == [3]
