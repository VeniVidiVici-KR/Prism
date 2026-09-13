"""재무 추이 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동).

⚠️ 통화 불일치 라운드(2026-09) — financial_history/get_quarterly_financial_history는 raw
재무제표(공시통화)를 그대로 준다. currency_mismatch 종목(ADR)에서 이를 거래통화(currency)로
라벨링하면 값은 맞는데 단위만 거짓말이 된다(실측: KT 매출 28.25조를 "$28,253,193,000,000.00"로
표시). 영업이익률/순이익률은 매출·이익 둘 다 같은(재무제표) 통화라 비율이 상쇄돼 안전하다 —
절대금액 열만 financial_currency로 라벨을 고친다. DART 대체 표시(아래 yfinance 실패 분기)는
currency=="KRW"일 때만 타는 경로라(한국 상장기업 전용) 애초에 통화 불일치가 성립하지 않는다
— 그 경로는 그대로 둔다.
"""

import pandas as pd
import streamlit as st

from valley.dart import DartDataError
from valley.data import TickerNotFoundError
from valley.formatting import format_money
from views._cache import get_corp_code, get_dart_financial_statements, get_quarterly_financial_history
from views._charts import line_chart_with_markers
from views.stock._shared import kr_stock_code


def render(
    resolved_ticker: str,
    currency: str,
    financial_history: pd.DataFrame | None,
    yfinance_financials_failed: bool,
    currency_mismatch: bool = False,
    financial_currency: str | None = None,
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
            amount_currency = financial_currency if currency_mismatch and financial_currency else currency
            with st.container(border=True):
                st.caption(f"단위: {amount_currency}")
                if currency_mismatch:
                    st.caption(
                        f"⚠️ 이 종목은 주가 통화({currency})와 재무제표 통화({financial_currency})가 "
                        f"달라, 위 금액은 재무제표 원본 통화({financial_currency}) 기준입니다"
                        "(환산되지 않음) — 영업이익률·순이익률은 비율이라 통화와 무관해 그대로 "
                        "신뢰할 수 있습니다."
                    )
                display_history = history_to_show.copy()
                for col in ["매출", "영업이익", "순이익"]:
                    display_history[col] = display_history[col].map(lambda v: format_money(v, amount_currency) if pd.notna(v) else "N/A")
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
