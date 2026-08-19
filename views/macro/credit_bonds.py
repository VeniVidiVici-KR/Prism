"""신용시장 스프레드 비교 + 주요 기업 채권 발행 동향(424B) — 미국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import time

import pandas as pd
import streamlit as st

from valley.edgar import EdgarDataError, FORM_424B_TYPES_WITH_TERMS, describe_form_type
from valley.edgar_summary import EdgarSummaryError
from valley.llm import ENGINE_GEMINI, ENGINE_LABELS, ENGINE_SCHOOL, is_engine_available
from valley.macro import MacroDataError
from views._cache import get_424b_filing_text, get_cik, get_filings_by_type, summarize_424b_filing
from views.macro._shared import load_series

# "신용 스프레드 비교" 뷰 전용 — 하이일드(위험지표 카테고리)와 투자등급(신용시장 카테고리)을
# 함께 겹쳐본다. 둘 다 옵션조정스프레드(OAS, %) 동일 단위라 국가부채/GDP 비교 섹션과 같은
# 방식으로 정규화 없이 원값을 그대로 겹친다 — valley/macro.py의 "신용시장" 카테고리 주석 참고.
CREDIT_SPREAD_COMPARISON_KEYS = {
    "하이일드(HY)": "US_HY_SPREAD",
    "투자등급 전체(IG)": "US_IG_SPREAD",
    "AA": "US_IG_SPREAD_AA",
    "BBB": "US_IG_SPREAD_BBB",
}

# ⚠️ "주요 기업 채권 발행 동향" 뷰 전용 — DEFAULT_UNIVERSE(Screener)와 같은 패턴으로, 사용자가
# 이 목록을 직접 편집해 관심 있는 발행 주체로 바꿀 수 있게 상수로 노출한다. 초기 목록은 AI
# 데이터센터 capex 주체인 빅테크(GOOGL/MSFT/AMZN/META/ORCL/NVDA/AAPL)에 데이터센터 REIT인
# EQIX·DLR(실제 시설을 짓는 회사채 발행 당사자, AI 인프라 조달과 가장 직접 연결됨)을 더했다.
# 9개 전부 실측으로 EDGAR 424B 조회가 됨을 확인했다(GOOGL/AMZN/ORCL/NVDA/EQIX/DLR은 최근
# 몇 주~두 달 이내 발행 기록, MSFT/AAPL은 상대적으로 오래전 — 최근엔 회사채보다 자체
# 현금흐름으로 조달 중일 가능성).
BOND_ISSUANCE_UNIVERSE = ["GOOGL", "MSFT", "AMZN", "META", "ORCL", "NVDA", "AAPL", "EQIX", "DLR"]


def render(country_code: str) -> None:
    if country_code != "US":
        return

    st.divider()
    st.header("신용시장 — 하이일드 vs 투자등급 스프레드")
    st.caption(
        "AI 데이터센터 등 대규모 투자를 회사채로 조달하는 기업들의 조달 여건 변화를 신용 사이클 "
        "신호로 봅니다. 하이일드(투기등급)와 투자등급 스프레드를 함께 보면 신용 국면이 뚜렷해 "
        "집니다 — 둘 다 옵션조정스프레드(OAS, %) 동일 단위라, 위 한미 국가부채/GDP 비교와 같은 "
        "방식으로 정규화 없이 원값을 그대로 겹쳐 그립니다."
    )

    if st.button("신용 스프레드 비교 보기", key="btn_credit_spread_compare"):
        st.session_state.macro_credit_spread_loaded = True

    if st.session_state.get("macro_credit_spread_loaded"):
        with st.container(border=True):
            spread_series = {}
            spread_failed = []
            for label, key in CREDIT_SPREAD_COMPARISON_KEYS.items():
                try:
                    spread_series[label] = load_series(key)
                except MacroDataError as e:
                    spread_failed.append(f"{label}: {e}")

            if spread_failed:
                st.warning("일부 항목을 제외했습니다: " + " / ".join(spread_failed))

            if not spread_series:
                st.info("표시할 수 있는 항목이 없습니다.")
            else:
                spread_rows = [
                    {"구분": label, "최신값(%)": s.iloc[-1], "기준일": s.index[-1].strftime("%Y-%m-%d")}
                    for label, s in spread_series.items()
                ]
                st.dataframe(
                    pd.DataFrame(spread_rows).set_index("구분").style.format({"최신값(%)": "{:.2f}%"}),
                    width="stretch",
                )
                aligned = pd.concat(spread_series, axis=1, join="inner")
                if aligned.empty:
                    st.warning("선택한 항목들 사이에 겹치는 기간이 없습니다.")
                else:
                    st.line_chart(aligned)
                    st.caption(
                        f"겹치는 구간({aligned.index[0]:%Y-%m-%d} ~ {aligned.index[-1]:%Y-%m-%d}) · "
                        "모두 OAS(%) 동일 단위라 정규화하지 않았습니다."
                    )

    st.divider()
    st.subheader("주요 기업 채권 발행 동향")
    st.caption(
        "AI 인프라 투자 주체들의 '개별 실제 조달 활동'을 SEC EDGAR의 발행설명서(424B 계열)로 "
        "봅니다."
    )
    st.caption(
        "⚠️ 424B는 '발행이 있었다'는 사실과 최종 조건 문서를 알려주지만, 청약 수요(주문잔고· "
        "가이던스 대비 스프레드 축소폭)는 이 공시에 담기지 않습니다 — 유료 데이터 영역입니다."
    )

    # AI 요약 엔진 선택 — 이 페이지에서 AI를 쓰는 곳이 424B 요약 하나뿐이라 그 근처(사이드바
    # 대신 섹션 안)에 둔다. stock_research.py 사이드바의 "AI 엔진"과 동일한 자동 폴백 없음
    # 원칙(직접 고른 엔진으로만 생성, 조용히 안 바뀜)을 그대로 따른다.
    bond_engine_options = [ENGINE_SCHOOL, ENGINE_GEMINI]
    if "macro_bond_ai_engine" not in st.session_state:
        st.session_state.macro_bond_ai_engine = ENGINE_SCHOOL
    bond_selected_engine = st.radio(
        "424B 요약에 사용할 AI 엔진", bond_engine_options,
        format_func=lambda e: ENGINE_LABELS[e] + ("" if is_engine_available(e) else " (키 미설정)"),
        key="macro_bond_ai_engine", horizontal=True,
    )

    def _render_424b_filings(filings: list[dict], cik: str, company_label: str) -> None:
        """필링 목록을 표+건별 요약 UI로 그린다. 티커 직접 조회·통합 뷰가 공유한다."""
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "접수일": f["filing_date"],
                        "유형": describe_form_type(f["form"]),
                        "설명": f["description"] or "-",
                        "원문": f["url"],
                    }
                    for f in filings
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")},
        )

        if "macro_bond_summaries" not in st.session_state:
            st.session_state.macro_bond_summaries = {}

        for f in filings:
            form = f["form"]
            accession_number = f.get("accession_number")
            if form not in FORM_424B_TYPES_WITH_TERMS or not accession_number:
                continue
            summary_key = (accession_number, bond_selected_engine)
            with st.expander(f"{f['filing_date']} · {describe_form_type(form)} 요약"):
                stored = st.session_state.macro_bond_summaries.get(summary_key)
                if stored is None:
                    if st.button(
                        "AI 요약 생성하기",
                        key=f"macro_bond_summarize_btn_{accession_number}_{bond_selected_engine}",
                    ):
                        with st.spinner(f"발행설명서 원문을 가져와 {ENGINE_LABELS[bond_selected_engine]}로 요약하는 중..."):
                            try:
                                doc = get_424b_filing_text(cik, accession_number, form)
                                summary_text = summarize_424b_filing(
                                    company_label, form, f["filing_date"],
                                    doc["text"], doc["truncated"], engine=bond_selected_engine,
                                )
                            except (EdgarDataError, EdgarSummaryError) as e:
                                st.session_state.macro_bond_summaries[summary_key] = {"error": str(e)}
                            else:
                                st.session_state.macro_bond_summaries[summary_key] = {
                                    "summary": summary_text, "truncated": doc["truncated"],
                                }
                        st.rerun()
                elif "error" in stored:
                    st.error(f"요약 생성에 실패했습니다: {stored['error']}")
                else:
                    st.markdown(f"**AI 생성 요약 · {ENGINE_LABELS[bond_selected_engine]}**")
                    st.markdown(stored["summary"])
                    if stored["truncated"]:
                        st.caption("⚠️ 문서가 길어 앞부분만(표지·요약·위험요인·자금사용목적 위주) 요약했습니다.")

        no_term_forms = sorted({f["form"] for f in filings if f["form"] not in FORM_424B_TYPES_WITH_TERMS})
        if no_term_forms:
            st.caption(
                f"⚠️ {', '.join(no_term_forms)}은(는) 신규 발행 조건이 없는 유형(교환제안·"
                "기존주주 매도 등)이라 요약을 제공하지 않습니다 — 원문 링크로 직접 확인하세요."
            )

    # --- 티커 직접 조회 (기본 사용 방식, 호출 1회) ---
    st.markdown("**티커로 직접 조회**")
    bond_ticker_input = st.text_input(
        "미국 상장 티커", key="macro_bond_ticker_input", placeholder="예: TSLA",
        help="SEC EDGAR 조회이므로 미국 상장 티커만 지원합니다. 한국 종목코드는 지원하지 않습니다.",
    )
    if st.button("조회하기", key="btn_bond_ticker_query"):
        raw = bond_ticker_input.strip().upper()
        if not raw:
            st.session_state.macro_bond_ticker_result = None
        elif raw.isdigit():
            st.session_state.macro_bond_ticker_result = {"kind": "kr_only", "ticker": raw}
        else:
            try:
                bond_cik = get_cik(raw)
            except EdgarDataError as e:
                st.session_state.macro_bond_ticker_result = {"kind": "not_mapped", "ticker": raw, "error": str(e)}
            else:
                try:
                    bond_filings = get_filings_by_type(bond_cik, "424B", limit=10)
                except EdgarDataError as e:
                    st.session_state.macro_bond_ticker_result = {
                        "kind": "fetch_failed", "ticker": raw, "error": str(e)
                    }
                else:
                    if not bond_filings:
                        st.session_state.macro_bond_ticker_result = {"kind": "zero", "ticker": raw}
                    else:
                        st.session_state.macro_bond_ticker_result = {
                            "kind": "ok", "ticker": raw, "cik": bond_cik, "filings": bond_filings,
                        }

    ticker_result = st.session_state.get("macro_bond_ticker_result")
    if ticker_result:
        with st.container(border=True):
            rk = ticker_result["kind"]
            if rk == "kr_only":
                st.info(f"'{ticker_result['ticker']}'은(는) 한국 종목코드로 보입니다 — 이 섹션은 SEC EDGAR 기반이라 미국 상장 티커만 지원합니다.")
            elif rk == "not_mapped":
                st.warning(f"'{ticker_result['ticker']}'를 SEC EDGAR 티커 목록에서 찾지 못했습니다: {ticker_result['error']}")
            elif rk == "fetch_failed":
                st.warning(f"'{ticker_result['ticker']}' 424B 조회에 실패했습니다(네트워크 문제일 수 있음): {ticker_result['error']}")
            elif rk == "zero":
                st.info(f"'{ticker_result['ticker']}'의 424B(발행설명서) 이력이 없습니다 — 최근 공모로 증권을 발행하지 않았을 수 있습니다.")
            else:
                st.caption(f"'{ticker_result['ticker']}' 최근 424B 최대 10건 · 최신순.")
                _render_424b_filings(ticker_result["filings"], ticker_result["cik"], ticker_result["ticker"])

    # --- 기존 통합 뷰(9개 기업, 호출 9회) — 삭제하지 않고 별도 버튼으로 보존 ---
    st.divider()
    st.markdown("**주요 기업 한눈에 보기 (9개 기업 통합)**")
    st.caption(f"대상 기업(코드 상단 BOND_ISSUANCE_UNIVERSE에서 편집 가능): {', '.join(BOND_ISSUANCE_UNIVERSE)}")

    if st.button("주요 기업 한눈에 보기 불러오기", key="btn_bond_issuance"):
        st.session_state.macro_bond_issuance_loaded = True

    if st.session_state.get("macro_bond_issuance_loaded"):
        with st.container(border=True):
            issuance_rows = []
            issuance_failed = []
            zero_result_tickers = []
            # ⚠️ 기업 하나 조회 실패해도 나머지는 계속 진행한다(Comparison 종목별 격리와 동일
            # 원칙). SEC 속도 제한을 배려해 기업 사이에 짧게 쉰다(문서 자체는 st.cache_data로
            # 캐싱되어 재실행·재클릭 시엔 이 루프조차 다시 돌지 않는다).
            for i, ticker in enumerate(BOND_ISSUANCE_UNIVERSE):
                if i > 0:
                    time.sleep(0.2)
                try:
                    cik = get_cik(ticker)
                    filings = get_filings_by_type(cik, "424B", limit=5)
                except EdgarDataError as e:
                    issuance_failed.append(f"{ticker}: {e}")
                    continue
                if not filings:
                    zero_result_tickers.append(ticker)
                    continue
                for f in filings:
                    issuance_rows.append(
                        {
                            "기업": ticker,
                            "접수일": f["filing_date"],
                            "유형": describe_form_type(f["form"]),
                            "설명": f["description"] or "-",
                            "원문": f["url"],
                        }
                    )

            if issuance_failed:
                st.warning("일부 기업 조회에 실패했습니다(네트워크/CIK 매핑 문제일 수 있음): " + " / ".join(issuance_failed))
            if zero_result_tickers:
                st.caption(
                    "⚠️ 조회는 성공했지만 424B 발행 이력이 없는 기업: " + ", ".join(zero_result_tickers)
                    + " — 최근 회사채를 발행하지 않았을 수 있습니다(조회 실패와는 다른 상황입니다)."
                )

            if not issuance_rows:
                st.info("표시할 수 있는 발행 공시가 없습니다.")
            else:
                issuance_rows.sort(key=lambda r: r["접수일"], reverse=True)
                st.dataframe(
                    pd.DataFrame(issuance_rows),
                    width="stretch",
                    hide_index=True,
                    column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")},
                )
                st.caption(
                    f"기업당 최근 5건까지, 총 {len(issuance_rows)}건 · 최신순 통합 정렬(기업 구분 없음) · "
                    "⚠️ 이 통합 뷰는 일괄 AI 요약을 제공하지 않습니다 — 개별 종목 조건이 궁금하면 위 "
                    "'티커로 직접 조회'를 쓰세요."
                )
