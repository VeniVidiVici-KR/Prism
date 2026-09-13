"""valley/peers.py 순수 함수 단위테스트 — build_valuation_table의 통화 불일치 행 처리.

⚠️ 통화 불일치 라운드(2026-09) — currency_mismatch 행은 PER/PBR(raw 재무제표 ÷ 거래통화
주가)만 "N/A"로 비우고, Fwd PER(Yahoo forwardPE, 실측으로 정상 스케일 확인)·배당수익률·
배당성향·ROE·시가총액(전부 비율이거나 이미 거래통화 기준)은 그대로 둔다. 실측(005930.KS
페이지의 Peer 표): 이 fix가 대상 티커(KT 등) 뿐 아니라 정상 종목의 "유사 종목" 목록에 섞여
들어온 통화 불일치 peer(SK hynix ADR "SKHY", 삼성전자 런던 GDR "SMSN.IL")도 자동으로
바로잡는다 — 그 두 peer도 이 클래스의 버그였다(예전엔 PER "0.0배"로 표시돼 있었음).
"""

import pandas as pd

from valley.peers import build_valuation_table


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
