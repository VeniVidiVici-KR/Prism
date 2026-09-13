"""valley/data.py 순수 함수 단위테스트 — build_snapshot의 통화 불일치(currency_mismatch) 판정.

⚠️ 통화 불일치 라운드(2026-09) — 실측 확인 결과 외국 ADR(KT, PKX, SKM, LPL, TSM, ASML,
BABA, TM, SONY 등)은 전부 info["currency"](거래통화)와 info["financialCurrency"]
(재무제표 공시통화)가 다르다. 이 둘을 비교하는 판정 로직 자체는 네트워크 없이 info dict만
있으면 검증 가능한 순수 함수라 mock으로 고정한다(실제 조회는 AppTest 네트워크 회귀로
간접 검증).
"""

from valley.data import build_snapshot


def _info(**overrides) -> dict:
    base = {
        "longName": "Test Corp",
        "currentPrice": 100.0,
        "currency": "USD",
        "sector": "Technology",
        "industry": "Software",
        "sharesOutstanding": 1000,
        "marketCap": 100_000,
    }
    base.update(overrides)
    return base


def test_no_mismatch_when_currency_and_financial_currency_match():
    """정상 종목(AAPL 등) 대조군 — 실측: currency=USD, financialCurrency=USD."""
    snap = build_snapshot("AAPL", _info(financialCurrency="USD"))
    assert snap["financial_currency"] == "USD"
    assert snap["currency_mismatch"] is False


def test_mismatch_detected_for_adr():
    """실측(KT Corp ADR): currency=USD, financialCurrency=KRW."""
    snap = build_snapshot("KT", _info(financialCurrency="KRW"))
    assert snap["financial_currency"] == "KRW"
    assert snap["currency_mismatch"] is True


def test_no_mismatch_when_financial_currency_is_none():
    """⚠️ 핵심 회귀 — financialCurrency 필드 자체가 없는(None) 종목(실측: 지수 등 일부
    quoteType)은 비교 자체가 성립하지 않으므로 불일치로 취급하지 않는다(정상 종목과 동일하게
    통과) — None을 "다르다"로 오판하면 관련 없는 종목까지 밸류에이션이 막힌다."""
    snap = build_snapshot("XYZ", _info())  # financialCurrency 키 없음 -> None
    assert snap["financial_currency"] is None
    assert snap["currency_mismatch"] is False


def test_native_kr_listing_has_no_mismatch():
    """실측(005930.KS): currency=KRW, financialCurrency=KRW — 한국 원주는 항상 일치."""
    snap = build_snapshot("005930.KS", _info(currency="KRW", financialCurrency="KRW"))
    assert snap["currency_mismatch"] is False
