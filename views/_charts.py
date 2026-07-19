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
