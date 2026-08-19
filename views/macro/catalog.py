"""지표 카탈로그 버튼 + 단일 지표 뷰 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import streamlit as st

from valley.macro import INDICATORS, MacroDataError
from views._charts import actual_projection_line_chart
from views.macro._shared import (
    DEBT_GDP_ACTUAL_OF,
    DEBT_GDP_COUNTRY_LABEL,
    DEBT_GDP_PROJECTION_OF,
    balanced_row_sizes,
    load_series,
)


def _render_indicator_buttons(indicators: list[dict]) -> None:
    """_balanced_row_sizes로 계산한 줄 구성대로 버튼을 그린다.

    ⚠️ 현재 선택된 지표는 type="primary"로 강조해 어떤 지표를 보고 있는지 한눈에
    드러나게 하고(이전엔 버튼이라 선택 상태가 안 보였다), description이 있으면 버튼
    마우스오버 툴팁(help)으로 보여준다 — 라벨을 짧게 줄이며 뺀 부가정보를 삭제한 게
    아니라 자리를 옮긴 것이다(캡션 쪽은 아래 지표 선택 후 표시 영역에서 담당).
    """
    row_sizes = balanced_row_sizes(len(indicators))
    idx = 0
    for row_size in row_sizes:
        chunk = indicators[idx : idx + row_size]
        idx += row_size
        cols = st.columns(row_size)
        for col, indicator in zip(cols, chunk):
            is_selected = indicator["key"] == st.session_state.get("macro_selected")
            if col.button(
                indicator["label"],
                key=f"btn_{indicator['key']}",
                width="stretch",
                help=indicator.get("description"),
                type="primary" if is_selected else "secondary",
            ):
                st.session_state.macro_selected = indicator["key"]
                # ⚠️ session_state 갱신만으로는 방금 클릭한 버튼의 type="primary" 강조가 이번
                # 렌더링에 반영되지 않는다(위 is_selected는 이 버튼을 그리기 "전"에 이미 계산돼
                # 있었다) — AI 요약 결과를 바로 보여줄 때와 같은 이유로 재실행해 강조를 즉시
                # 반영한다(타이머 기반 자동 새로고침과는 다른, 클릭에 종속된 1회성 재실행).
                st.rerun()


def _render_debt_gdp_actual_projection(actual_key: str) -> None:
    """국가부채/GDP 실적+IMF 전망을 실적(실선)+전망(점선) 결합 차트로 그린다.

    ⚠️ "국가부채/GDP"·"국가부채/GDP (전망)" 버튼 중 어느 쪽을 눌러도 이 함수가 불려 항상 같은
    결합 뷰를 보여준다 — 전망만 따로 보고 싶어 "(전망)" 버튼을 눌렀더라도 실적 없이 전망만
    보여주면 어디서부터가 전망인지 기준이 안 잡혀 오히려 헷갈린다.
    """
    country_label = DEBT_GDP_COUNTRY_LABEL[actual_key]
    projection_key = DEBT_GDP_PROJECTION_OF[actual_key]
    try:
        actual = load_series(actual_key)
    except MacroDataError as e:
        st.warning(f"국가부채/GDP({country_label}): {e}")
        return

    st.markdown("**국가부채/GDP (실적 + IMF 전망)**")
    st.caption("일반정부 총부채, IMF WEO 기준 · 연간")

    try:
        projection = load_series(projection_key)
    except MacroDataError as e:
        st.line_chart(actual)
        st.caption(f"⚠️ 전망 데이터를 가져오지 못해 실적만 표시합니다: {e}")
        st.caption(f"데이터 기준일: {actual.index[-1]:%Y-%m} · {len(actual)}개 관측치")
        return

    actual_projection_line_chart({country_label: (actual, projection)})
    boundary_year = actual.index[-1].year
    st.caption(
        f"⚠️ {country_label}은 {boundary_year}년까지 실적(실선)이고, {boundary_year + 1}~"
        f"{projection.index[-1].year}년은 IMF 전망(점선)입니다. ⚠️ 전망치는 확정치가 아닙니다 — "
        "IMF 자체 추정이며 WEO 발표(연 2회) 때마다 수정될 수 있습니다."
    )


def render(country_code: str) -> None:
    st.caption("아래 버튼을 눌러 보고 싶은 지표의 차트만 불러옵니다.")

    for category, indicators in INDICATORS[country_code].items():
        st.subheader(category)
        _render_indicator_buttons(indicators)

    st.divider()

    selected_key = st.session_state.get("macro_selected")
    if not selected_key:
        st.info("위에서 지표 버튼을 눌러주세요.")
    else:
        # ⚠️ 카드 레이아웃 라운드(2026-08) — 선택한 지표의 제목·설명·차트·기준일 캡션은 항상
        # 함께 읽는 한 덩어리라 카드로 묶는다(버튼 목록은 네비게이션이라 카드 밖에 둔다).
        with st.container(border=True):
            _debt_gdp_actual_key = selected_key if selected_key in DEBT_GDP_PROJECTION_OF else DEBT_GDP_ACTUAL_OF.get(selected_key)
            if _debt_gdp_actual_key:
                _render_debt_gdp_actual_projection(_debt_gdp_actual_key)
            else:
                indicator = next(
                    ind for indicators in INDICATORS[country_code].values() for ind in indicators if ind["key"] == selected_key
                )
                try:
                    series = load_series(selected_key)
                except MacroDataError as e:
                    st.warning(f"{indicator['label']}: {e}")
                else:
                    st.markdown(f"**{indicator['label']}**")
                    if indicator.get("description"):
                        st.caption(indicator["description"])
                    st.line_chart(series)
                    st.caption(f"데이터 기준일: {series.index[-1]:%Y-%m} · {len(series)}개 관측치")
