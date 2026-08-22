"""최근 공시 (SEC EDGAR) (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 위 DART 공시와 대칭 위치, 미국(비한국) 종목 전용.
"""

from collections import Counter

import pandas as pd
import streamlit as st

from valley.edgar import (
    ALL_CATEGORIES,
    CATEGORY_PRIMARY_FORM_TYPES,
    EdgarDataError,
    categorize_form,
    describe_form_type,
)
from valley.edgar_summary import EdgarSummaryError
from valley.llm import ENGINE_LABELS, ENGINE_SCHOOL
from views._cache import (
    get_8k_filing_text,
    get_cik,
    get_filings_by_type,
    get_mda_section_text,
    get_recent_filings,
    summarize_8k_filing,
    summarize_mda_section,
)


def render(resolved_ticker: str, currency: str, snapshot: dict, selected_engine: str) -> None:
    st.subheader("최근 공시 (SEC EDGAR)")
    if currency == "KRW":
        st.info("한국 종목은 위 DART 공시를 이용하세요. SEC EDGAR는 미국 상장기업 전용입니다.")
    else:
        try:
            edgar_cik = get_cik(resolved_ticker)
            # ⚠️ 30건이면 대형주는 Form 4(임원 지분변동) 홍수에 10-K/10-Q가 파묻혀 안 보일 수
            # 있어(실측: AAPL 30건 중 10-K 0건, 100건 중에서야 1건 등장) 100건으로 늘렸다.
            # SEC submissions API의 filings.recent는 한 응답에 최대 1000건까지 오므로(실측 확인),
            # 추가 호출 없이 같은 캐시된 응답 안에서 더 넓은 범위를 확보한다.
            edgar_filings = get_recent_filings(edgar_cik, limit=100)
        except EdgarDataError as e:
            st.warning(f"SEC EDGAR 공시 목록을 가져오지 못했습니다: {e}")
        else:
            if not edgar_filings:
                st.info("최근 공시가 없습니다.")
            else:
                # 세션 키(sr_ 접두어)로 관리해 다른 위젯 조작으로 재실행돼도 필터 선택이 유지되게
                # 한다. "전체"를 선택하면 범주 구분 없이 다 보여준다(범주 설계에서 빠진 form도
                # 항상 "전체"로는 볼 수 있어야 한다는 요구사항).
                if "sr_edgar_form_categories" not in st.session_state:
                    st.session_state.sr_edgar_form_categories = ["실적·정기보고", "중요 사건"]
                category_options = ALL_CATEGORIES + ["전체"]
                selected_categories = st.multiselect(
                    "공시 유형", category_options, key="sr_edgar_form_categories",
                    help="기본은 실적·정기보고(10-K/10-Q)와 중요 사건(8-K) 위주입니다. "
                    "Form 4 등 임원 지분변동이 많은 대형주는 기본 필터에서 걸러집니다.",
                )

                counts_by_category = Counter(categorize_form(f["form"]) for f in edgar_filings)
                st.caption(
                    f"최근 {len(edgar_filings)}건 중 범주별 건수: "
                    + " · ".join(f"{cat} {counts_by_category.get(cat, 0)}건" for cat in ALL_CATEGORIES)
                )

                if "전체" in selected_categories:
                    filtered_filings = edgar_filings
                else:
                    # 선택이 비어 있으면(체크 다 해제) 의도적으로 빈 결과로 취급한다 — 아무 범주도
                    # 안 골랐는데 조용히 전체를 보여주면 "필터가 걸리긴 한 건지" 혼란스럽다. 아래
                    # 0건 안내가 "전체"를 켜라고 명확히 알려준다.
                    filtered_filings = [
                        f for f in edgar_filings if categorize_form(f["form"]) in selected_categories
                    ]

                if "sr_edgar_8k_summaries" not in st.session_state:
                    st.session_state.sr_edgar_8k_summaries = {}
                # 10-K/10-Q MD&A용 세션 키 — 8-K와 완전히 분리된 별도 딕셔너리(sr_ 접두어 준수).
                # sr_edgar_mda_extracted: accession_number -> 추출된 MD&A 텍스트(엔진 무관, 무료).
                # sr_edgar_mda_summaries: (accession_number, engine) -> 최종 요약(유료, 엔진별 분리).
                if "sr_edgar_mda_extracted" not in st.session_state:
                    st.session_state.sr_edgar_mda_extracted = {}
                if "sr_edgar_mda_summaries" not in st.session_state:
                    st.session_state.sr_edgar_mda_summaries = {}

                def _render_edgar_table(filings: list[dict]) -> None:
                    edgar_df = pd.DataFrame(
                        [
                            {
                                "접수일": f["filing_date"],
                                "유형": describe_form_type(f["form"]),
                                "설명": f["description"] or "-",
                                "원문": f["url"],
                            }
                            for f in filings
                        ]
                    )
                    st.dataframe(
                        edgar_df,
                        width="stretch",
                        hide_index=True,
                        column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")},
                    )

                    # 8-K 본문 AI 요약 — 이번 라운드는 8-K만(10-K/10-Q는 길이·비용 설계가 달라
                    # 다음 라운드). ⚠️ 비용 통제 핵심: 목록에 뜬다고 자동으로 요약하지 않는다 —
                    # 반드시 사용자가 건별로 "AI 요약 생성하기"를 눌러야만 호출된다(일괄 요약
                    # 버튼 없음). accession_number가 없는 필링(이론상 없어야 함)은 건너뛴다.
                    eight_k_filings = [f for f in filings if f["form"] == "8-K" and f.get("accession_number")]
                    if eight_k_filings:
                        st.caption(
                            f"🤖 8-K(수시공시) 본문 AI 요약({ENGINE_LABELS[selected_engine]}) — 건별로 버튼을 "
                            "눌러야 생성됩니다(자동/일괄 요약 없음)."
                        )
                        for f in eight_k_filings:
                            accession_number = f["accession_number"]
                            # ⚠️ (accession_number, engine) 조합으로 키를 잡는다 — accession_number만
                            # 쓰면 학교 게이트웨이로 요약한 뒤 사이드바에서 Gemini로 바꿔도 이전(학교)
                            # 결과가 그대로 남아있는 것처럼 보이는 사고가 난다. 엔진별로 독립 저장하면
                            # 서로 덮어쓰지 않고, 엔진을 되돌렸을 때도 그 엔진의 기존 결과가 재호출 없이
                            # 바로 나온다.
                            summary_key = (accession_number, selected_engine)
                            with st.expander(f"{f['filing_date']} · 8-K 요약"):
                                stored_summary = st.session_state.sr_edgar_8k_summaries.get(summary_key)
                                if stored_summary is None:
                                    if st.button(
                                        "AI 요약 생성하기",
                                        key=f"sr_edgar_8k_summarize_btn_{accession_number}_{selected_engine}",
                                    ):
                                        with st.spinner(f"공시 원문을 가져와 {ENGINE_LABELS[selected_engine]}로 요약하는 중..."):
                                            try:
                                                doc = get_8k_filing_text(edgar_cik, accession_number)
                                                summary_text = summarize_8k_filing(
                                                    snapshot["name"], f["form"], f["filing_date"],
                                                    doc["text"], doc["truncated"], engine=selected_engine,
                                                )
                                            except (EdgarDataError, EdgarSummaryError) as e:
                                                st.session_state.sr_edgar_8k_summaries[summary_key] = {
                                                    "error": str(e)
                                                }
                                            else:
                                                st.session_state.sr_edgar_8k_summaries[summary_key] = {
                                                    "summary": summary_text,
                                                    "truncated": doc["truncated"],
                                                    "used_exhibit": doc["used_exhibit"],
                                                }
                                        # 버튼 클릭 처리 직후엔 아직 이 run 안에서 else 분기(표시)가
                                        # 실행되지 않으므로, 재실행해 방금 저장한 결과를 바로 보여준다.
                                        st.rerun()
                                elif "error" in stored_summary:
                                    st.error(f"요약 생성에 실패했습니다: {stored_summary['error']}")
                                else:
                                    st.markdown(f"**🤖 AI 생성 요약 · {ENGINE_LABELS[selected_engine]}**")
                                    st.markdown(stored_summary["summary"])
                                    notes = []
                                    if stored_summary["used_exhibit"]:
                                        notes.append("첨부(EX-99.1) 포함")
                                    if stored_summary["truncated"]:
                                        notes.append("⚠️ 문서가 길어 앞부분만 요약했습니다")
                                    if notes:
                                        st.caption(" · ".join(notes))

                    # 10-K/10-Q MD&A(경영진의 논의 및 분석) 요약 — 8-K와 별도 경로다. 문서
                    # 전체(수백 페이지)를 넣을 수 없어 MD&A 섹션만 추출하고, 8-K보다 비용이
                    # 훨씬 커서(실측: AAPL 10-K 1.8만자~MSFT 10-Q 5.8만자, 8-K 대비 3~10배 토큰)
                    # "추출 → 크기 확인 → 사용자가 진행 확정" 2단계로 나눈다(추출 자체는 SEC
                    # 문서 조회일 뿐 LLM 호출이 아니라 무료 — 크레딧은 진행 확정 후에만 든다).
                    # ⚠️ 10-K/A, 10-Q/A(정정본)는 지원하지 않는다 — 실측 결과 TSLA의 최근
                    # 10-K/A는 Part III(임원보수 등) 정정만 담아 Item 7(MD&A) 자체가 없었다.
                    mda_filings = [
                        f for f in filings if f["form"] in ("10-K", "10-Q") and f.get("accession_number")
                    ]
                    if mda_filings:
                        st.caption(
                            f"🤖 10-K/10-Q MD&A(경영진의 논의 및 분석) AI 요약({ENGINE_LABELS[selected_engine]}) "
                            "— 건별로 버튼을 눌러야 생성됩니다(자동/일괄 요약 없음). 문서가 커서 8-K보다 "
                            "비용이 훨씬 큽니다."
                        )
                        for f in mda_filings:
                            accession_number = f["accession_number"]
                            form = f["form"]
                            # 추출(무료, SEC 문서 조회일 뿐)은 엔진과 무관해 accession_number만으로
                            # 캐시하고, 최종 요약(유료, LLM 호출)만 (accession_number, engine)로
                            # 분리한다 — 8-K 세션 키(sr_edgar_8k_summaries)와도 완전히 별개 딕셔너리라
                            # 서로 덮어쓸 일이 없다.
                            mda_summary_key = (accession_number, selected_engine)
                            with st.expander(f"{f['filing_date']} · {form} MD&A 요약"):
                                stored_mda_summary = st.session_state.sr_edgar_mda_summaries.get(mda_summary_key)
                                if stored_mda_summary is not None:
                                    if "error" in stored_mda_summary:
                                        st.error(f"요약 생성에 실패했습니다: {stored_mda_summary['error']}")
                                    else:
                                        st.markdown(f"**🤖 AI 생성 요약 · {ENGINE_LABELS[selected_engine]} · Item {'7' if form == '10-K' else '2'} MD&A 기준**")
                                        st.markdown(stored_mda_summary["summary"])
                                        if stored_mda_summary["truncated"]:
                                            st.caption("⚠️ MD&A 섹션 자체가 길어 앞부분만 요약했습니다.")
                                else:
                                    extracted = st.session_state.sr_edgar_mda_extracted.get(accession_number)
                                    if extracted is None:
                                        if st.button(
                                            "MD&A 추출 (크기 확인)",
                                            key=f"sr_edgar_mda_extract_btn_{accession_number}",
                                            help="아직 AI를 호출하지 않습니다 — SEC 원문에서 MD&A 섹션만 추출해 크기를 먼저 보여줍니다.",
                                        ):
                                            with st.spinner("MD&A 섹션을 추출하는 중..."):
                                                try:
                                                    mda_doc = get_mda_section_text(edgar_cik, accession_number, form)
                                                except EdgarDataError as e:
                                                    st.session_state.sr_edgar_mda_extracted[accession_number] = {
                                                        "error": str(e)
                                                    }
                                                else:
                                                    st.session_state.sr_edgar_mda_extracted[accession_number] = {
                                                        "text": mda_doc["text"],
                                                        "truncated": mda_doc["truncated"],
                                                    }
                                            st.rerun()
                                    elif "error" in extracted:
                                        st.error(f"MD&A 추출에 실패했습니다: {extracted['error']}")
                                    else:
                                        char_count = len(extracted["text"])
                                        approx_tokens = char_count // 4
                                        st.info(
                                            f"추출된 MD&A 약 {char_count:,}자(대략 {approx_tokens:,} 토큰 추정) — 요약하시겠습니까?"
                                        )
                                        if extracted["truncated"]:
                                            st.caption("⚠️ MD&A 섹션 자체가 길어 앞부분만 추출했습니다.")
                                        if selected_engine == ENGINE_SCHOOL:
                                            st.caption(
                                                "💡 긴 문서라 학교 게이트웨이 크레딧 소모가 큽니다. Gemini 엔진 "
                                                "사용을 고려해보세요(사이드바에서 전환 — 자동으로 바뀌지는 않습니다)."
                                            )
                                        if st.button(
                                            f"이 크기로 {ENGINE_LABELS[selected_engine]} 요약 진행",
                                            key=f"sr_edgar_mda_summarize_btn_{accession_number}_{selected_engine}",
                                        ):
                                            with st.spinner(f"{ENGINE_LABELS[selected_engine]}로 MD&A를 요약하는 중..."):
                                                try:
                                                    mda_summary_text = summarize_mda_section(
                                                        snapshot["name"], form, f["filing_date"],
                                                        extracted["text"], extracted["truncated"], engine=selected_engine,
                                                    )
                                                except EdgarSummaryError as e:
                                                    st.session_state.sr_edgar_mda_summaries[mda_summary_key] = {
                                                        "error": str(e)
                                                    }
                                                else:
                                                    st.session_state.sr_edgar_mda_summaries[mda_summary_key] = {
                                                        "summary": mda_summary_text,
                                                        "truncated": extracted["truncated"],
                                                    }
                                            st.rerun()

                if not filtered_filings:
                    st.info(f"선택한 유형의 공시가 최근 {len(edgar_filings)}건 내에 없습니다. '전체'를 선택하면 모두 볼 수 있습니다.")

                    # ⚠️ "최근 N건에서 거르기"의 근본 한계(JPM처럼 424B2 발행량이 압도적인 종목은
                    # 100건, 심지어 1000건 안에도 10-K/10-Q가 없을 수 있음, 실측 확인)를 여기서
                    # 해결한다 — 매 로드마다 자동 조회하면 호출이 늘어나므로, 사용자가 필요할 때만
                    # 누르는 버튼으로 한정한다(평소엔 기존 흐름 그대로 빠르게).
                    direct_fetchable = [c for c in selected_categories if c in CATEGORY_PRIMARY_FORM_TYPES]
                    if direct_fetchable:
                        if st.button("선택한 유형을 SEC에서 직접 조회하기", key="sr_edgar_direct_fetch_btn"):
                            direct_results = []
                            for cat in direct_fetchable:
                                for form_type in CATEGORY_PRIMARY_FORM_TYPES[cat]:
                                    try:
                                        direct_results.extend(get_filings_by_type(edgar_cik, form_type, limit=10))
                                    except EdgarDataError as e:
                                        st.warning(f"{form_type} 직접 조회에 실패했습니다: {e}")
                            direct_results.sort(key=lambda f: f["filing_date"], reverse=True)
                            # 티커·범주 조합까지 함께 저장해, 다른 종목을 조회하거나 필터를 바꾸면
                            # 이전 결과가 잘못 이어서 보이지 않도록 한다(다음 rerun에서 이 조합이
                            # 안 맞으면 아래에서 자동으로 무시됨).
                            st.session_state.sr_edgar_direct_results = {
                                "ticker": resolved_ticker,
                                "categories": tuple(sorted(direct_fetchable)),
                                "filings": direct_results,
                            }

                        stored = st.session_state.get("sr_edgar_direct_results")
                        if (
                            stored
                            and stored["ticker"] == resolved_ticker
                            and stored["categories"] == tuple(sorted(direct_fetchable))
                        ):
                            if not stored["filings"]:
                                st.info("직접 조회로도 해당 유형의 공시를 찾지 못했습니다.")
                            else:
                                _render_edgar_table(stored["filings"])
                                st.caption(
                                    f"SEC 직접 조회 결과 {len(stored['filings'])}건 "
                                    f"(선택 유형별 최신 10건까지, 최신순) · 최근 N건 필터가 아닌 유형별 직접 조회"
                                )
                else:
                    _render_edgar_table(filtered_filings)
                    st.caption(
                        f"{len(filtered_filings)}건 표시 (최근 {len(edgar_filings)}건 중 선택 유형 필터 적용, 최신순) · 출처: SEC EDGAR"
                    )
                st.caption("8-K는 행별로 AI 요약을 볼 수 있습니다. 10-K/10-Q 본문 요약은 다음 라운드 범위입니다.")
