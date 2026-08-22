"""회사 기본정보 + 회사 개요 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음)."""

import streamlit as st

from valley.company_summary import CompanySummaryError
from valley.formatting import format_money
from valley.llm import ENGINE_LABELS
from views._cache import summarize_company_overview


def render(snapshot: dict, currency: str, resolved_ticker: str, selected_engine: str) -> None:
    st.subheader(f"{snapshot['name']} ({snapshot['ticker']})")
    # ⚠️ 카드 레이아웃 라운드(2026-08) — 헤드라인 지표(현재가·섹터·산업·시가총액)와 상세
    # 정보(직원수·본사·사업 서술)가 "이 회사가 무엇인가"라는 같은 질문에 답하는 한 덩어리라
    # 카드로 묶는다. 제목(subheader)은 카드 밖에 둔다(다른 섹션들과 동일한 배치 원칙).
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재가", format_money(snapshot["price"], currency))
        col2.metric("섹터", snapshot["sector"])
        col3.metric("산업", snapshot["industry"])
        market_cap = snapshot.get("market_cap")
        col4.metric("시가총액", format_money(market_cap, currency) if market_cap else "N/A")

        # 회사 개요 — 미국 종목 전용(한국은 DART 사업보고서 본문 파싱이 필요해 다음 라운드 범위).
        # ⚠️ get_company_snapshot이 이미 받아온 info를 build_snapshot에서 그대로 확장한 필드만 쓴다
        # (.info 추가 호출 없음). 섹터/산업은 위에서 이미 표시했으니 여기서 반복하지 않는다.
        if currency != "KRW":
            overview_fields = []
            if snapshot.get("employees"):
                overview_fields.append(("직원 수", f"{snapshot['employees']:,}명"))
            location_parts = [p for p in (snapshot.get("city"), snapshot.get("state"), snapshot.get("country")) if p]
            if location_parts:
                overview_fields.append(("본사", ", ".join(location_parts)))
            if snapshot.get("website"):
                overview_fields.append(("웹사이트", snapshot["website"]))

            business_summary = snapshot.get("business_summary")

            # 필드가 전부 없는 종목(예: 상장폐지·데이터 미제공 소형주)은 섹션 자체를 띄우지 않는다 —
            # 있는 것만 개별 표시하되, 아예 아무것도 없으면 빈 subheader만 남는 걸 피한다.
            if overview_fields or business_summary:
                st.subheader("회사 개요")
                if overview_fields:
                    cols = st.columns(len(overview_fields))
                    for col, (label, value) in zip(cols, overview_fields):
                        col.metric(label, value)

                if business_summary:
                    with st.expander("사업 개요 원문 보기 (영문)"):
                        st.write(business_summary)

                    st.caption(
                        f"🤖 회사 개요 한국어 요약({ENGINE_LABELS[selected_engine]}) — 버튼을 눌러야 생성됩니다 "
                        "(자동 요약 없음)."
                    )
                    if "sr_company_overview_summaries" not in st.session_state:
                        st.session_state.sr_company_overview_summaries = {}
                    # ⚠️ (ticker, engine) 조합으로 캐시 키를 잡는다 — 8-K 요약과 동일한 이유로 engine을
                    # 빠뜨리면 엔진을 바꿔도 이전 엔진 결과가 그대로 남아있는 것처럼 보이는 사고가 난다.
                    overview_summary_key = (resolved_ticker, selected_engine)
                    stored_overview_summary = st.session_state.sr_company_overview_summaries.get(overview_summary_key)
                    if stored_overview_summary is None:
                        if st.button("한국어로 요약", key=f"sr_company_overview_summarize_btn_{resolved_ticker}_{selected_engine}"):
                            with st.spinner(f"{ENGINE_LABELS[selected_engine]}로 회사 개요를 요약하는 중..."):
                                try:
                                    summary_text = summarize_company_overview(
                                        snapshot["name"], business_summary, engine=selected_engine,
                                    )
                                except CompanySummaryError as e:
                                    st.session_state.sr_company_overview_summaries[overview_summary_key] = {"error": str(e)}
                                else:
                                    st.session_state.sr_company_overview_summaries[overview_summary_key] = {
                                        "summary": summary_text
                                    }
                            # 8-K 요약과 동일한 이유(방금 저장한 결과를 이 run 안에서 바로 보여주려면 재실행 필요).
                            st.rerun()
                    elif "error" in stored_overview_summary:
                        st.error(f"요약 생성에 실패했습니다: {stored_overview_summary['error']}")
                    else:
                        st.markdown(f"**🤖 AI 생성 요약 · {ENGINE_LABELS[selected_engine]}**")
                        st.markdown(stored_overview_summary["summary"])
                else:
                    st.caption("이 종목은 사업 개요 원문이 제공되지 않아 한국어 요약을 만들 수 없습니다.")

                st.caption(
                    "💡 세그먼트별 매출 구조(제품·서비스별 매출 비중)는 아래 '최근 공시 (SEC EDGAR)'의 "
                    "10-K MD&A 요약에서 확인할 수 있습니다."
                )
