"""valley/peers.py 순수 함수 단위테스트 — build_valuation_table의 통화 불일치 행 처리.

⚠️ 통화 불일치 라운드(2026-09) — currency_mismatch 행은 PER/PBR(raw 재무제표 ÷ 거래통화
주가)만 "N/A"로 비우고, Fwd PER(Yahoo forwardPE, 실측으로 정상 스케일 확인)·배당수익률·
배당성향·ROE·시가총액(전부 비율이거나 이미 거래통화 기준)은 그대로 둔다. 실측(005930.KS
페이지의 Peer 표): 이 fix가 대상 티커(KT 등) 뿐 아니라 정상 종목의 "유사 종목" 목록에 섞여
들어온 통화 불일치 peer(SK hynix ADR "SKHY", 삼성전자 런던 GDR "SMSN.IL")도 자동으로
바로잡는다 — 그 두 peer도 이 클래스의 버그였다(예전엔 PER "0.0배"로 표시돼 있었음).
"""

from valley.peers import _payout_ratio, build_valuation_table


def _row(**overrides) -> dict:
    base = {
        "ticker": "AAPL",
        "name": "Apple",
        "price": 200.0,
        "currency": "USD",
        "currency_mismatch": False,
        "financial_currency": "USD",
        "market_cap": 1_000_000.0,
        "per": 30.0,
        "pbr": 40.0,
        "bvps": 5.0,
        "forward_per": 25.0,
        "dividend_yield": 0.005,
        "payout_ratio": 0.15,
        "roe": 0.5,
    }
    base.update(overrides)
    return base


def test_normal_row_shows_per_pbr_unchanged():
    df = build_valuation_table([_row()])
    assert df.iloc[0]["PER"] == "30.0배"
    assert df.iloc[0]["PBR"] == "40.0배"


def test_currency_mismatch_row_blanks_only_per_and_pbr():
    """⚠️ 핵심 회귀 — PER/PBR만 N/A, 나머지(Fwd PER·배당수익률·배당성향·ROE·시가총액)는
    그대로 표시돼야 한다(비율이거나 이미 거래통화 기준이라 통화 불일치와 무관)."""
    row = _row(
        ticker="KT", currency="USD", currency_mismatch=True, financial_currency="KRW",
        per=0.0056, pbr=0.00027, roe=0.0976, dividend_yield=0.0409, payout_ratio=0.4422,
    )
    df = build_valuation_table([row])
    assert df.iloc[0]["PER"] == "N/A"
    assert df.iloc[0]["PBR"] == "N/A"
    assert df.iloc[0]["Fwd PER"] == "25.0배"
    assert df.iloc[0]["ROE"] == "9.8%"
    assert df.iloc[0]["배당수익률"] == "4.1%"
    assert df.iloc[0]["배당성향"] == "44.2%"
    assert df.iloc[0]["시가총액"] == "$1,000,000.00"


def test_mixed_table_only_blanks_the_mismatched_row():
    """정상 티커와 통화 불일치 티커가 같은 표(Peer Group)에 섞여도 서로 영향을 주지 않는다."""
    df = build_valuation_table([_row(ticker="AAPL"), _row(ticker="KT", currency_mismatch=True)])
    assert df.iloc[0]["PER"] == "30.0배"
    assert df.iloc[0]["PBR"] == "40.0배"
    assert df.iloc[1]["PER"] == "N/A"
    assert df.iloc[1]["PBR"] == "N/A"


def test_missing_currency_mismatch_key_defaults_to_shown():
    """⚠️ 하위호환 — currency_mismatch 키 자체가 없는 rows(과거 캐시된 값 등)가 섞여도
    깨지지 않고 PER/PBR을 정상 표시해야 한다."""
    row = _row()
    del row["currency_mismatch"]
    df = build_valuation_table([row])
    assert df.iloc[0]["PER"] == "30.0배"


def test_row_missing_key_is_not_blanked_when_sharing_table_with_mismatched_row():
    """⚠️ 핵심 회귀 — 일부 행에만 currency_mismatch 키가 있으면(코드 변경 전 캐시된 스냅샷이
    새 스냅샷과 한 표에 섞이는 경우) pandas가 없는 행을 NaN으로 채우는데, bool(nan)이 True라
    단순 truthy 검사로는 정상 종목까지 PER/PBR이 N/A로 비워졌다(실측 재현). `is True` 엄격
    비교로 막는다."""
    normal = _row(ticker="AAPL")
    del normal["currency_mismatch"]
    mismatched = _row(ticker="KT", currency_mismatch=True, financial_currency="KRW")

    df = build_valuation_table([normal, mismatched])

    assert df.iloc[0]["PER"] == "30.0배", "키가 없는 정상 행이 잘못 비워짐"
    assert df.iloc[0]["PBR"] == "40.0배"
    assert df.iloc[1]["PER"] == "N/A"
    assert df.iloc[1]["PBR"] == "N/A"


# ── 배당성향 폴백의 통화 혼합 가드 (통화 불일치 후속 라운드, 2026-10) ──────────────────


def test_payout_ratio_prefers_yahoo_value_even_when_currency_mismatched():
    """Yahoo info["payoutRatio"]는 거래통화 기준으로 일관되게 계산된 비율이라 통화 불일치
    종목에서도 그대로 쓴다(실측: ADR 10종 전부 이 필드를 제공)."""
    ratio = _payout_ratio({"payoutRatio": 0.4422}, 0.81, 476_263_616, 1.7e12, currency_mismatch=True)
    assert ratio == 0.4422


def test_payout_ratio_fallback_blocked_when_currency_mismatched():
    """⚠️ 핵심 — 폴백 식(DPS×주식수÷순이익)은 DPS(거래통화)와 순이익(공시통화)을 섞어
    환율 배수만큼 틀린다. 통화 불일치면 계산하지 않고 None을 반환해야 한다."""
    ratio = _payout_ratio({}, 0.81, 476_263_616, 1.7e12, currency_mismatch=True)
    assert ratio is None


def test_payout_ratio_fallback_still_works_for_normal_ticker():
    """⚠️ 회귀 — 통화가 일치하는 정상 종목은 폴백 동작이 예전 그대로여야 한다."""
    ratio = _payout_ratio({}, 2.0, 1000.0, 10_000.0)
    assert ratio == 0.2
