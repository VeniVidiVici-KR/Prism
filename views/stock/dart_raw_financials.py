"""DART 원본 재무제표 (실험) (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ DCF 성공 여부와 무관하게 항상 도달 가능한 독립 섹션이다 — 원본에서도 위 DCF 블록들과
같은 들여쓰기 단계(최상위)에 있어 st.stop()이 발동하지 않는 한 항상 렌더링됐다.
"""

import pandas as pd
import streamlit as st

from valley.dart import DartDataError
from valley.formatting import format_money
from views._cache import get_corp_code, get_dart_financial_statements
from views.stock._shared import kr_stock_code


def render(
    *,
    yfinance_financials_failed: bool,
    currency: str,
    resolved_ticker: str,
    financial_history,
) -> None:
    st.divider()
    st.subheader("DART 원본 재무제표 (실험)")
    st.caption(
        "OpenDART 전자공시 원본 재무제표를 yfinance 기반 수치와 대조해보는 파일럿 기능입니다. "
        "위의 다른 분석 결과에는 전혀 반영되지 않으며, 검증 목적으로만 존재합니다."
    )

    if yfinance_financials_failed:
        # yfinance 재무가 아예 없으면 대조할 게 없다 — 위 "재무 추이" 섹션에서 이미 DART 대체를
        # 보여줬으므로(성공했든 실패했든) 여기서 또 시도하지 않는다(financial_history가 None이라
        # 아래 대조 로직 자체가 성립하지 않기도 한다).
        st.caption(
            "이 종목은 yfinance 재무제표가 없어 위 '재무 추이' 섹션에서 이미 DART로 대체 표시를 "
            "시도했습니다. 대조할 yfinance 값이 없어 이 섹션은 생략합니다."
        )
        return
    if currency != "KRW":
        st.info("DART는 한국 상장기업 재무제표만 제공합니다. 한국 종목(6자리 코드)을 조회했을 때만 표시됩니다.")
        return

    dart_stock_code = kr_stock_code(resolved_ticker)
    try:
        dart_corp_code = get_corp_code(dart_stock_code)
    except DartDataError as e:
        st.warning(str(e))
        return

    try:
        dart_data = get_dart_financial_statements(dart_corp_code)
    except DartDataError as e:
        st.warning(str(e))
        return

    # ⚠️ 카드 레이아웃 라운드(2026-09) — DART 원본 표와 DART vs yfinance 대조 표를 한 카드로
    # 묶는다(둘 다 "이 DART 데이터가 무엇이고 yfinance와 얼마나 다른가"라는 같은 검증 목적).
    with st.container(border=True):
        fs_label = "연결재무제표(CFS)" if dart_data["fs_div_used"] == "CFS" else "별도재무제표(OFS)"
        st.caption(f"기준: {fs_label} · DART corp_code {dart_corp_code}")

        years_sorted = sorted(dart_data["by_year"].keys())
        dart_table = pd.DataFrame({year: dart_data["by_year"][year] for year in years_sorted}).T
        dart_table.index.name = "연도"
        display_dart = dart_table.copy()
        for col in display_dart.columns:
            display_dart[col] = display_dart[col].map(lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A")
        st.dataframe(display_dart, width="stretch")

        matched = dart_data["matched_account_names"]
        matched_desc = ", ".join(f"{label}→'{name}'" for label, name in matched.items() if name)
        if matched_desc:
            st.caption(f"매칭된 DART 계정명: {matched_desc}")
        unmatched = [label for label, name in matched.items() if name is None]
        if unmatched:
            st.caption(f"매칭 실패(N/A 처리): {', '.join(unmatched)}")

        st.markdown("**DART vs yfinance 대조**")
        compare_pairs = [("매출액", "매출"), ("영업이익", "영업이익"), ("당기순이익", "순이익")]
        compare_rows = []
        for year in years_sorted:
            year_str = str(year)
            if year_str not in financial_history.index:
                continue  # yfinance 쪽에 없는 회계연도는 대조 불가 -> 건너뜀
            for dart_label, yf_label in compare_pairs:
                dart_val = dart_data["by_year"][year].get(dart_label)
                yf_raw = financial_history.loc[year_str, yf_label]
                yf_val = float(yf_raw) if pd.notna(yf_raw) else None
                diff_pct = (dart_val - yf_val) / yf_val * 100 if (dart_val is not None and yf_val) else None
                compare_rows.append(
                    {"연도": year_str, "항목": dart_label, "DART": dart_val, "yfinance": yf_val, "차이(%)": diff_pct}
                )

        if not compare_rows:
            st.info("yfinance 재무 추이와 회계연도가 겹치지 않아 대조할 수 없습니다.")
        else:
            compare_df = pd.DataFrame(compare_rows)
            display_compare = compare_df.copy()
            display_compare["DART"] = display_compare["DART"].map(
                lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A"
            )
            display_compare["yfinance"] = display_compare["yfinance"].map(
                lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A"
            )
            display_compare["차이(%)"] = display_compare["차이(%)"].map(
                lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
            )
            st.dataframe(display_compare, width="stretch", hide_index=True)
            st.caption(
                "차이(%) = (DART − yfinance) / yfinance × 100. 연결/별도 기준 차이, 회계연도 정의 차이 "
                "(사업연도 vs TTM), 계정 분류 차이 등으로 벌어질 수 있습니다."
            )
