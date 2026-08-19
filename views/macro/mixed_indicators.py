"""혼합 지표 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import streamlit as st

from valley.composite import COMPOSITE_INDICATORS, CROSS_COUNTRY_INDICATORS
from valley.macro import MacroDataError
from views.macro._shared import balanced_row_sizes, load_composite_series


def _render_composite_buttons(indicators: list[dict]) -> None:
    """혼합지표 버튼을 _balanced_row_sizes로 줄바꿈해 그린다.

    ⚠️ 물가 재편 라운드(2026-08) — 이전엔 st.columns(len(indicators))로 항목 수만큼 열을
    만들었는데, 이번 라운드에서 미국 혼합지표가 3개→11개로 늘어(CPI/PCE YoY·MoM 파생지표
    추가) 한 줄에 11칸이 되면 버튼 글자가 다 안 보일 정도로 좁아진다 — 위 개별 지표
    버튼(_render_indicator_buttons)이 이미 쓰는 _balanced_row_sizes를 재사용해 여러 줄로
    감싼다. ⚠️ 디자인 변경이 아니라 줄바꿈만 고치는 것이라, 강조 표시(type="primary")나
    rerun 같은 개별 지표 버튼의 다른 동작은 가져오지 않았다(기존 혼합지표 버튼의 시각적
    동작을 그대로 유지).
    """
    row_sizes = balanced_row_sizes(len(indicators))
    idx = 0
    for row_size in row_sizes:
        chunk = indicators[idx : idx + row_size]
        idx += row_size
        cols = st.columns(row_size)
        for col, indicator in zip(cols, chunk):
            if col.button(indicator["label"], key=f"btn_composite_{indicator['key']}", width="stretch"):
                st.session_state.macro_composite_selected = indicator["key"]


def render(country_code: str) -> None:
    st.divider()
    st.header("혼합 지표")
    st.caption(
        "서로 다른 원본 지표를 조합해 만든 파생 지표입니다. GDP 등 분기 지표가 섞이면 "
        "월별 지표보다 갱신이 늦게 반영될 수 있습니다."
    )

    _render_composite_buttons(COMPOSITE_INDICATORS[country_code])

    st.caption("교차국가")
    _render_composite_buttons(CROSS_COUNTRY_INDICATORS)

    composite_selected_key = st.session_state.get("macro_composite_selected")
    if not composite_selected_key:
        st.info("위에서 혼합 지표 버튼을 눌러주세요.")
    else:
        with st.container(border=True):
            all_composite = [*COMPOSITE_INDICATORS["US"], *COMPOSITE_INDICATORS["KR"], *CROSS_COUNTRY_INDICATORS]
            composite_indicator = next(ind for ind in all_composite if ind["key"] == composite_selected_key)
            try:
                composite_series = load_composite_series(composite_selected_key)
            except MacroDataError as e:
                st.warning(f"{composite_indicator['label']}: {e}")
            else:
                st.markdown(f"**{composite_indicator['label']}**")
                st.line_chart(composite_series)
                st.caption(f"데이터 기준일: {composite_series.index[-1]:%Y-%m} · {len(composite_series)}개 관측치")
