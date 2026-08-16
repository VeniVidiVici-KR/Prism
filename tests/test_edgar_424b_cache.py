"""424B(발행설명서) 요약 라운드(2026-08) — 순수 상수/캐싱 검증.

⚠️ tests/test_apptest_regression.py는 "AI 요약/해설 생성하기" 버튼을 절대 클릭하지 않는다는
명시적 정책이 있다(파일 상단 docstring). 캐싱 동작(같은 인자 재호출 시 재호출 없음, engine이
바뀌면 새로 호출) 검증은 그 정책을 지키면서도 확인할 수 있어야 하므로, AppTest로 버튼을
누르는 대신 views._cache의 캐시 래퍼를 직접 호출해 마킹한다(실제 LLM 호출 없음 — 전부 mock).
"""

from unittest.mock import patch

from valley.edgar import FORM_424B_TYPES_WITH_TERMS
from views import _cache as cache_mod


def test_form_424b_types_with_terms_is_conservative_allowlist():
    """2026-08 라운드 실측: 424B2(AAPL·AMZN)·424B5(AMZN·ORCL)만 신규 발행조건(금액·쿠폰·
    만기)을 담고 있었다. 424B3(MSFT, 교환제안)·424B7(DLR, 기존주주 매도)은 해당 개념이
    없어 제외했고, 실측하지 않은 다른 하위유형(424B1/424B4/424B6/424B8)도 추측으로
    포함하지 않는다 — 화이트리스트가 정확히 이 두 개만 담고 있는지 고정해둔다."""
    assert FORM_424B_TYPES_WITH_TERMS == {"424B2", "424B5"}


def test_summarize_424b_filing_caches_by_text_and_engine():
    """같은 (text, engine) 조합으로 재호출하면 캐시 히트(재호출 없음), engine이 바뀌면
    새로 호출된다 — summarize_8k_filing과 동일한 캐시 키 설계를 재사용했는지 확인한다."""
    calls: list[tuple] = []

    def _fake(*args, **kwargs):
        calls.append(args)
        return "MOCK SUMMARY"

    cache_mod.summarize_424b_filing.clear()
    with patch("views._cache._summarize_424b_filing", side_effect=_fake):
        r1 = cache_mod.summarize_424b_filing("Apple Inc.", "424B2", "2025-05-06", "sample text", False, engine="school")
        r2 = cache_mod.summarize_424b_filing("Apple Inc.", "424B2", "2025-05-06", "sample text", False, engine="school")
        r3 = cache_mod.summarize_424b_filing("Apple Inc.", "424B2", "2025-05-06", "sample text", False, engine="gemini")

    assert r1 == r2 == r3 == "MOCK SUMMARY"
    assert len(calls) == 2  # school 1회 + gemini 1회 (재클릭에 해당하는 두 번째 school 호출은 캐시 히트)


def test_get_424b_filing_text_caches_by_accession_and_form():
    """같은 (cik, accession_number, form_type)로 재호출하면 재조회하지 않는다(SEC 재요청 없음)."""
    calls: list[tuple] = []

    def _fake(*args, **kwargs):
        calls.append(args)
        return {"text": "cover page terms...", "truncated": False}

    cache_mod.get_424b_filing_text.clear()
    with patch("views._cache._get_424b_filing_text", side_effect=_fake):
        r1 = cache_mod.get_424b_filing_text("0000320193", "0001140361-25-017543", "424B2")
        r2 = cache_mod.get_424b_filing_text("0000320193", "0001140361-25-017543", "424B2")
        r3 = cache_mod.get_424b_filing_text("0000320193", "0001140361-25-017544", "424B2")  # 다른 accession

    assert r1 == r2 == {"text": "cover page terms...", "truncated": False}
    assert len(calls) == 2  # 첫 accession 1회 + 다른 accession 1회
