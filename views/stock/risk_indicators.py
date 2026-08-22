"""종목 리스크 지표 (공매도·신용·대차) (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 토스증권 API 전용, 국내 종목만. 세 지표는 서로 다른 것을 보여준다(공매도=하락 베팅 직접
지표, 신용융자=개인 레버리지, 대차=공매도 선행 성격이나 전부가 공매도로 이어지진 않음 —
헤지·차익거래 목적도 포함) — 단위가 전부 "수량(주)"으로 같아 보여도 의미가 다르므로 절대 한
차트에 섞지 않고 각각 독립된 차트/표로 보여준다. ⚠️ KOFIA로 이미 붙인 시장 전체 신용융자
잔고(매크로 페이지 '신용융자' 카테고리)와 달리 이건 종목별 잔고다 — 혼동 방지용 캡션을
신용거래 쪽에 명시한다.

⚠️ 실측(2026-08 라운드, 005930) 결과 세 엔드포인트 다 investor-trading과 동일한 until/count
(최대 100) 페이지네이션 구조를 쓰지만, investor-trading과 달리 T+1 지연이 있다(조회 당일
데이터가 아직 없고 전일까지만 온다) — UI 캡션에 명시한다. 호출이 3회(지표당 1회) 나가므로
macro_indicator의 버튼 게이팅 섹션들과 같은 패턴으로 클릭 시에만 조회한다(위 수급 섹션은
자동 조회지만, 이 섹션은 요청에 따라 명시적으로 게이팅한다).
"""

import os

import streamlit as st

from valley.toss import TossDataError
from views._cache import get_credit_trades_history, get_securities_lending_history, get_short_selling_history
from views.stock._shared import kr_stock_code


def render(resolved_ticker: str, currency: str) -> None:
    st.subheader("종목 리스크 지표 (공매도·신용·대차)")
    if currency != "KRW":
        st.info("이 지표는 국내 상장기업 전용입니다(토스증권 API). 해외 종목은 지원하지 않습니다.")
    elif not (os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET")):
        st.info(
            "TOSS_CLIENT_ID/TOSS_CLIENT_SECRET가 설정되어 있지 않아 공매도·신용거래·대차거래 "
            "지표를 표시할 수 없습니다 — 토스증권 오픈 API 개발자센터에서 발급받은 키를 .env에 "
            "추가하세요."
        )
    else:
        risk_stock_code = kr_stock_code(resolved_ticker)

        if st.button("리스크 지표 불러오기", key="sr_risk_indicators_load"):
            st.session_state.sr_risk_indicators_loaded = True

        if st.session_state.get("sr_risk_indicators_loaded"):
            st.markdown("**공매도**")
            st.caption(
                "빌려서 판 물량 — 하락 베팅의 직접적인 지표입니다. ⚠️ T+1 지연이 있습니다(조회 "
                "당일 데이터는 다음 거래일에 반영됩니다, 실측 확인)."
            )
            try:
                short_selling = get_short_selling_history(risk_stock_code, days=20)
            except TossDataError as e:
                st.warning(f"공매도 데이터 조회에 실패했습니다: {e}")
            else:
                ss_display = short_selling.rename(index=lambda d: d.strftime("%m-%d"))
                st.caption("일별 공매도 비중 (당일 전체 거래량/거래대금 대비, %)")
                st.line_chart((ss_display[["거래량비중", "거래대금비중"]] * 100))
                st.dataframe(
                    ss_display.style.format(
                        {
                            "거래량": "{:,.0f}주",
                            "거래대금": "{:,.0f}원",
                            "거래량비중": "{:.2%}",
                            "거래대금비중": "{:.2%}",
                        }
                    ),
                    width="stretch",
                )

            st.divider()
            st.markdown("**신용거래 (융자·대주)**")
            st.caption(
                "융자: 빌린 돈으로 산 물량(개인 레버리지, 급락 시 반대매매 압력) · 대주: 빌린 "
                "주식을 판 물량. ⚠️ 이 종목만의 잔고입니다 — 시장 전체 신용융자 잔고(위 매크로 "
                "'신용융자' 카테고리, ECOS/KOFIA)와는 다릅니다."
            )
            try:
                credit_trades = get_credit_trades_history(risk_stock_code, days=20)
            except TossDataError as e:
                st.warning(f"신용거래 데이터 조회에 실패했습니다: {e}")
            else:
                ct_display = credit_trades.rename(index=lambda d: d.strftime("%m-%d"))
                st.caption("일별 융자·대주 잔고 추이 (단위: 주)")
                st.line_chart(ct_display[["융자잔고", "대주잔고"]])
                st.dataframe(
                    ct_display.style.format(
                        {
                            "융자잔고": "{:,.0f}주",
                            "융자공여율": "{:.2%}",
                            "대주잔고": "{:,.0f}주",
                            "대주공여율": "{:.2%}",
                        }
                    ),
                    width="stretch",
                )

            st.divider()
            st.markdown("**대차거래**")
            st.caption(
                "주식을 빌려간 잔고 — 공매도의 선행 성격이 있습니다(빌려야 팔 수 있음). ⚠️ 다만 "
                "대차 전부가 공매도로 이어지는 건 아닙니다(헤지·차익거래 목적도 포함) — 대차 잔고 "
                "증가를 곧바로 공매도 증가로 해석하지 마세요."
            )
            try:
                securities_lending = get_securities_lending_history(risk_stock_code, days=20)
            except TossDataError as e:
                st.warning(f"대차거래 데이터 조회에 실패했습니다: {e}")
            else:
                sl_display = securities_lending.rename(index=lambda d: d.strftime("%m-%d"))
                st.caption("일별 대차 잔고 추이 (단위: 주)")
                st.line_chart(sl_display["잔고수량"])
                st.dataframe(
                    sl_display.style.format(
                        {
                            "체결수량": "{:,.0f}주",
                            "상환수량": "{:,.0f}주",
                            "잔고수량": "{:,.0f}주",
                            "잔고금액": "{:,.0f}원",
                        }
                    ),
                    width="stretch",
                )

            st.caption(
                "출처: 토스증권 Open API(파일럿) · 세 지표는 단위·의미가 달라 서로 직접 비교하지 "
                "말고 각각의 추이로 참고하세요."
            )
