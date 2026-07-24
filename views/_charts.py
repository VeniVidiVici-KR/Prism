"""views/ 전용 차트 헬퍼.

st.line_chart(내부적으로 Vega-Lite 라인)은 표식(마커) 옵션이 없다. 재무 추이처럼 데이터
포인트가 연 4~5개/분기 4~5개로 적은 시계열은 표식이 있어야 값을 점 단위로 읽기 쉬워서,
Altair(streamlit 번들 의존성이라 별도 설치 불필요)로 직접 그린다.
"""

import altair as alt
import pandas as pd
import streamlit as st


def line_chart_with_markers(data: pd.DataFrame, height: int = 320) -> None:
    """와이드 포맷 DataFrame(인덱스=기간, 컬럼=시리즈)을 표식이 있는 꺾은선 차트로 그린다."""
    long_df = data.reset_index(names="기간").melt("기간", var_name="지표", value_name="값")
    chart = (
        alt.Chart(long_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("기간:N", sort=list(data.index), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("값:Q", title=None),
            color=alt.Color("지표:N", legend=alt.Legend(title=None)),
            tooltip=["기간", "지표", "값"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def pie_chart(data: pd.DataFrame, category_col: str, value_col: str, height: int = 320) -> None:
    """항목별 비중(%)을 원 그래프로 그린다 — 항목명과 비중%를 툴팁/각도로 표시한다."""
    chart = (
        alt.Chart(data)
        .mark_arc()
        .encode(
            theta=alt.Theta(f"{value_col}:Q"),
            color=alt.Color(f"{category_col}:N", legend=alt.Legend(title=None)),
            tooltip=[category_col, alt.Tooltip(f"{value_col}:Q", format=".1f")],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def contribution_bar_chart(data: pd.DataFrame, category_col: str, value_col: str, height: int = 320) -> None:
    """기여도처럼 음수가 나올 수 있는 값을 막대그래프로 그린다.

    큰 값(가장 밀어올린 항목)이 위로 오도록 정렬하고, 0선을 굵은 회색 기준선으로 그려 양/음수
    경계가 바로 보이게 하며, 양수는 청록, 음수는 빨강으로 색을 구분한다.
    """
    sorted_data = data.sort_values(value_col, ascending=False)
    bars = (
        alt.Chart(sorted_data)
        .mark_bar()
        .encode(
            x=alt.X(f"{value_col}:Q", title=None),
            y=alt.Y(f"{category_col}:N", sort=list(sorted_data[category_col]), title=None),
            color=alt.condition(
                f"datum['{value_col}'] >= 0", alt.value("#2a9d8f"), alt.value("#e63946")
            ),
            tooltip=[category_col, alt.Tooltip(f"{value_col}:Q", format="+.2f")],
        )
    )
    zero_rule = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="gray").encode(x="x:Q")
    st.altair_chart((bars + zero_rule).properties(height=height), width="stretch")
