"""재무 추이 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.dart import DartDataError
from valley.data import TickerNotFoundError
from valley.formatting import format_money
from views._cache import get_corp_code, get_dart_financial_statements, get_quarterly_financial_history
from views._charts import line_chart_with_markers
from views.stock._shared import kr_stock_code


def render(
    resolved_ticker: str, currency: str, financial_history: pd.DataFrame | None, yfinance_financials_failed: bool
) -> None:
    st.subheader("재무 추이")

    dart_fallback_used = False  # 아래 파일럿 섹션에서 중복 표시를 피하기 위해 기억해둔다.

    if yfinance_financials_failed:
        st.warning("yfinance에서 이 종목의 재무제표를 가져오지 못했습니다.")
        if currency != "KRW":
            st.info("DART는 한국 상장기업 재무제표만 제공해 이 종목(한국 외)은 대체 표시가 불가합니다.")
        else:
            dart_stock_code = kr_stock_code(resolved_ticker)
            try:
                dart_corp_code = get_corp_code(dart_stock_code)
                dart_fallback_data = get_dart_financial_statements(dart_corp_code)
            except DartDataError as e:
                st.error(f"DART 대체 조회도 실패했습니다: {e}")
            else:
                with st.container(border=True):
                    dart_fallback_used = True
                    fs_label = "연결재무제표(CFS)" if dart_fallback_data["fs_div_used"] == "CFS" else "별도재무제표(OFS)"
                    st.info(f"📄 yfinance 데이터 없음 → DART 원본 재무제표({fs_label})로 대체 표시합니다.")

                    years_sorted = sorted(dart_fallback_data["by_year"].keys())
                    fallback_rows = {}
                    for y in years_sorted:
                        acc = dart_fallback_data["by_year"][y]
                        fallback_rows[str(y)] = {"매출": acc.get("매출액"), "영업이익": acc.get("영업이익"), "순이익": acc.get("당기순이익")}
                    fallback_df = pd.DataFrame(fallback_rows).T
                    fallback_df["영업이익률"] = fallback_df["영업이익"] / fallback_df["매출"]
                    fallback_df["순이익률"] = fallback_df["순이익"] / fallback_df["매출"]

                    st.caption(f"단위: {currency} (DART 원본, 연간)")
                    display_fallback = fallback_df.copy()
                    for col in ["매출", "영업이익", "순이익"]:
                        display_fallback[col] = display_fallback[col].map(lambda v: format_money(v, currency) if pd.notna(v) else "N/A")
                    for col in ["영업이익률", "순이익률"]:
                        display_fallback[col] = display_fallback[col].map(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "N/A")
                    st.dataframe(display_fallback, width="stretch")
                    # 매출과 순이익을 한 축에 겹치면 매출≫순이익인 종목은 순이익선이 바닥에 깔려 안
                    # 보인다 — 각자 스케일에 맞는 별도 차트로 분리한다(이중축은 새 차트 로직이라
                    # 다음 과제로 남기고, 이번엔 기존 line_chart_with_markers를 그대로 재사용).
                    rev_col, ni_col = st.columns(2)
                    with rev_col:
                        st.caption("매출")
                        line_chart_with_markers(fallback_df[["매출"]], height=240)
                    with ni_col:
                        st.caption("순이익")
                        line_chart_with_markers(fallback_df[["순이익"]], height=240)

                    matched = dart_fallback_data["matched_account_names"]
                    matched_desc = ", ".join(f"{label}→'{name}'" for label, name in matched.items() if name)
                    if matched_desc:
                        st.caption(f"매칭된 DART 계정명: {matched_desc}")
    else:
        period_view = st.radio("기간", ["연간", "분기별"], horizontal=True, label_visibility="collapsed")

        if period_view == "분기별":
            try:
                history_to_show = get_quarterly_financial_history(resolved_ticker)
            except TickerNotFoundError as e:
                st.warning(str(e))
                history_to_show = None
            else:
                st.caption("최근 4~5개 분기 (yfinance 무료 API 제공 범위 한계)")
        else:
            history_to_show = financial_history

        if history_to_show is not None:
            with st.container(border=True):
                st.caption(f"단위: {currency}")
                display_history = history_to_show.copy()
                for col in ["매출", "영업이익", "순이익"]:
                    display_history[col] = display_history[col].map(lambda v: format_money(v, currency) if pd.notna(v) else "N/A")
                for col in ["영업이익률", "순이익률"]:
                    display_history[col] = display_history[col].map(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "N/A")
                st.dataframe(display_history, width="stretch")
                # 매출과 순이익을 한 축에 겹치면 매출≫순이익인 종목은 순이익선이 바닥에 깔려 안
                # 보인다 — 각자 스케일에 맞는 별도 차트로 분리한다(이중축은 새 차트 로직이라
                # 다음 과제로 남기고, 이번엔 기존 line_chart_with_markers를 그대로 재사용).
                rev_col, ni_col = st.columns(2)
                with rev_col:
                    st.caption("매출")
                    line_chart_with_markers(history_to_show[["매출"]], height=240)
                with ni_col:
                    st.caption("순이익")
                    line_chart_with_markers(history_to_show[["순이익"]], height=240)
