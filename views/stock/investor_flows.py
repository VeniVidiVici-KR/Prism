"""투자자별 수급 (외국인·기관·개인) (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 공시 섹션과 마찬가지로 yfinance 성공/실패와 무관하게 한국 종목이면 항상 표시하는 상시
섹션이다. 개별종목 투자자별 매매동향은 KRX 로그인(KRX_ID/KRX_PW)이 있어야 조회되므로, 로그인
정보가 없거나 KRX가 막으면 이 섹션만 경고로 안내하고 나머지 섹션(재무/DCF/Peer/공시)은 정상
동작한다.
"""

import os

import streamlit as st

from valley.supply import SupplyDataError, summarize_net_trading
from valley.toss import TossDataError
from views._cache import get_institution_breakdown_history, get_investor_net_trading, get_investor_trading_history
from views._charts import line_chart_with_markers
from views.stock._shared import kr_stock_code


def render(resolved_ticker: str, currency: str) -> None:
    st.subheader("투자자별 수급 (외국인·기관·개인)")
    if currency != "KRW":
        st.info("현재는 한국 상장기업 수급만 제공합니다. 해외 종목은 투자자별 매매동향 공개 데이터가 없습니다.")
    else:
        supply_stock_code = kr_stock_code(resolved_ticker)
        # ⚠️ 토스증권 파일럿(2026-08 라운드) — TOSS_CLIENT_ID/SECRET이 없으면 선택지 자체를 감춰
        # 기존 KRX 전용 동작을 그대로 유지한다(다른 키 게이팅 섹션과 동일 패턴). 두 소스는 단위가
        # 다르다(KRX=거래대금 원화, 토스=거래량 주식 수 — 실측 확인, valley/toss.py 모듈 docstring
        # 참고) — 그래서 한 차트에 섞지 않고 라디오로 완전히 분리된 뷰를 그린다.
        toss_available = bool(os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET"))

        supply_source = "KRX (거래대금)"
        if toss_available:
            supply_source = st.radio(
                "데이터 소스",
                ["KRX (거래대금)", "토스증권 (거래량, 파일럿)"],
                horizontal=True,
                key="sr_supply_source",
                help="⚠️ 두 소스는 단위가 다릅니다(KRX=원화 거래대금, 토스=주식 수) — 값을 직접 비교하지 마세요.",
            )

        if supply_source == "KRX (거래대금)":
            try:
                daily_supply = get_investor_net_trading(supply_stock_code, days=20)
            except SupplyDataError as e:
                st.warning(f"투자자별 수급 조회에 실패했습니다: {e}")
            else:
                supply_summary = summarize_net_trading(daily_supply)
                st.caption(f"최근 {len(daily_supply)}영업일 누적 순매수 (단위: 억원 · +매수우위 / -매도우위)")
                s1, s2, s3 = st.columns(3)
                s1.metric("외국인", f"{supply_summary['외국인'] / 1e8:+,.0f}억원")
                s2.metric("기관", f"{supply_summary['기관'] / 1e8:+,.0f}억원")
                s3.metric("개인", f"{supply_summary['개인'] / 1e8:+,.0f}억원")

                st.caption(f"일별 순매수 추이 (최근 {len(daily_supply)}영업일, 단위: 억원)")
                line_chart_with_markers((daily_supply / 1e8).rename(index=lambda d: d.strftime("%m-%d")))
        else:
            try:
                toss_supply = get_investor_trading_history(supply_stock_code, days=20)
            except TossDataError as e:
                st.warning(f"토스증권 수급 조회에 실패했습니다: {e}")
            else:
                toss_summary = toss_supply.sum()
                st.caption(
                    f"최근 {len(toss_supply)}영업일 누적 순매수 (단위: 주 · +매수우위 / -매도우위) · "
                    "⚠️ 거래량(주식 수) 기준 — 위 KRX(거래대금)와 단위가 달라 직접 비교할 수 없습니다."
                )
                t1, t2, t3 = st.columns(3)
                t1.metric("외국인", f"{toss_summary['외국인']:+,.0f}주")
                t2.metric("기관", f"{toss_summary['기관']:+,.0f}주")
                t3.metric("개인", f"{toss_summary['개인']:+,.0f}주")

                st.caption(f"일별 순매수 추이 (최근 {len(toss_supply)}영업일, 단위: 주)")
                line_chart_with_markers(toss_supply.rename(index=lambda d: d.strftime("%m-%d")))

                try:
                    institution_breakdown = get_institution_breakdown_history(supply_stock_code, days=20)
                except TossDataError as e:
                    st.caption(f"기관 세부분류 조회에 실패했습니다: {e}")
                else:
                    st.caption("기관 세부분류 (금융투자·보험·투신·사모펀드·은행·기타금융기관·연기금, 단위: 주)")
                    st.dataframe(
                        institution_breakdown.rename(index=lambda d: d.strftime("%m-%d")).style.format("{:+,.0f}"),
                        width="stretch",
                    )

                st.caption(
                    "출처: 토스증권 Open API(파일럿) · 조회 당일은 개인 투자자 수치가 아직 집계되지 "
                    "않아 비어 있을 수 있습니다."
                )
