"""valley/edgar.py 순수 함수 단위테스트 (categorize_form, FORM_CATEGORIES 구조).

신용시장 라운드에서 "주주총회·기타"에 뭉쳐 있던 424B 계열·FWP를 "증권 발행"으로 분리했다 —
이 재분류가 의도대로 됐는지(424B/FWP는 증권 발행, S-1/S-3/S-8은 주주총회·기타에 남음,
매핑되지 않은 form은 여전히 "기타"로 폴백) 고정해둔다.
"""

from valley.edgar import (
    ALL_CATEGORIES,
    CATEGORY_PRIMARY_FORM_TYPES,
    FORM_CATEGORIES,
    OTHER_CATEGORY,
    categorize_form,
)


def test_424b_series_and_fwp_categorized_as_securities_issuance():
    for form in ["424B1", "424B2", "424B3", "424B4", "424B5", "424B8", "FWP"]:
        assert categorize_form(form) == "증권 발행"


def test_registration_statements_stay_in_shareholder_meeting_category():
    # S-1/S-3/S-8은 "발행 등록"이지 "실제 발행"이 아니라 증권 발행 범주로 옮기지 않았다.
    for form in ["S-1", "S-3", "S-8", "DEF 14A"]:
        assert categorize_form(form) == "주주총회·기타"


def test_unmapped_form_falls_back_to_other_category():
    assert categorize_form("13F-HR") == OTHER_CATEGORY
    assert categorize_form("ARS") == OTHER_CATEGORY


def test_no_form_appears_in_two_categories():
    seen: dict[str, str] = {}
    for category, forms in FORM_CATEGORIES.items():
        for form in forms:
            assert form not in seen, f"{form}이 {seen.get(form)}와 {category} 양쪽에 중복 등록됨"
            seen[form] = category


def test_securities_issuance_category_registered_in_all_categories():
    assert "증권 발행" in ALL_CATEGORIES
    assert "증권 발행" in CATEGORY_PRIMARY_FORM_TYPES
    assert CATEGORY_PRIMARY_FORM_TYPES["증권 발행"] == ["424B2"]
