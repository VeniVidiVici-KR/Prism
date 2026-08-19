"""한미 국가부채/GDP 비교 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.macro import MacroDataError
from views._charts import actual_projection_line_chart
from views.macro._shared import DEBT_GDP_PROJECTION_OF, load_series

_DEBT_GDP_KEYS = {"미국": "US_DEBT_GDP", "한국": "KR_DEBT_GDP"}


def render(country_code: str) -> None:
    st.divider()
    st.header("한미 국가부채/GDP 비교")
    st.caption(
        "국제 비교 표준인 일반정부 총부채(General government gross debt, IMF WEO 기준, GFSM 2001 "
        "정의)로 미국·한국을 동일 기준으로 비교합니다. 이미 IMF가 %로 계산해 발표한 값을 그대로 "
        "쓰고(지수·절대액에서 직접 나누지 않음), 둘 다 같은 단위(%)라 정규화 없이 원값을 겹쳐 "
        "그립니다. ⚠️ 연간 데이터라 두 나라의 최신 발표 연도가 다를 수 있습니다 — 표의 '실적 "
        "기준연도' 열을 확인하세요."
    )

    if st.button("한미 국가부채/GDP 비교 보기", key="btn_debt_gdp_compare"):
        st.session_state.macro_debt_gdp_loaded = True

    if st.session_state.get("macro_debt_gdp_loaded"):
        with st.container(border=True):
            debt_series = {}
            debt_failed = []
            for country_label, key in _DEBT_GDP_KEYS.items():
                try:
                    debt_series[country_label] = load_series(key)
                except MacroDataError as e:
                    debt_failed.append(f"{country_label}: {e}")

            if debt_failed:
                st.warning("일부 국가 데이터를 가져오지 못했습니다: " + " / ".join(debt_failed))

            if not debt_series:
                st.info("표시할 수 있는 데이터가 없습니다.")
            else:
                # ⚠️ 전망 반영 라운드(2026-08) — 표에 "최신 실적"과 "최신 전망"을 구분된 열로 함께
                # 보여준다(값을 뭉뚱그리지 않는다). 전망 조회가 실패해도 실적 행 자체는 그대로 보여준다
                # (부분 실패를 표 전체 실패로 만들지 않음 — 기존 debt_failed 안내 패턴과 동일).
                debt_projection_series: dict[str, pd.Series] = {}
                debt_rows = []
                for country_label, s in debt_series.items():
                    row = {"국가": country_label, "최신 실적(%)": s.iloc[-1], "실적 기준연도": s.index[-1].strftime("%Y")}
                    projection_key = DEBT_GDP_PROJECTION_OF[_DEBT_GDP_KEYS[country_label]]
                    try:
                        projection = load_series(projection_key)
                    except MacroDataError as e:
                        row["최신 전망(%)"] = None
                        row["전망 기준연도"] = None
                        debt_failed.append(f"{country_label} 전망: {e}")
                    else:
                        debt_projection_series[country_label] = projection
                        row["최신 전망(%)"] = projection.iloc[-1]
                        row["전망 기준연도"] = projection.index[-1].strftime("%Y")
                    debt_rows.append(row)

                st.dataframe(
                    pd.DataFrame(debt_rows).set_index("국가").style.format(
                        {"최신 실적(%)": "{:.1f}%", "최신 전망(%)": "{:.1f}%"}, na_rep="—"
                    ),
                    width="stretch",
                )
                if len(debt_series) >= 2:
                    # 연간 데이터라 두 나라의 마지막 관측 연도가 다를 수 있어(실측: 미국 2024년까지,
                    # 한국 2023년까지) join="outer"로 겹쳐 그린다 — inner로 자르면 더 최신인 쪽의
                    # 마지막 해가 잘려나가 정보 손실이 생긴다. ⚠️ 이 차트는 실적만 겹쳐 그리는 기존
                    # 회귀 대상 차트로 그대로 둔다(값·동작 무변경) — 실적+전망 결합 뷰는 아래 별도로 둔다.
                    st.line_chart(pd.concat(debt_series, axis=1, join="outer"))
                st.caption("두 시리즈 모두 이미 %로 계산된 IMF WEO 원값입니다 — 정규화하지 않았습니다.")

                if debt_projection_series:
                    st.markdown("**실적 + IMF 전망**")
                    actual_projection_line_chart(
                        {label: (s, debt_projection_series.get(label)) for label, s in debt_series.items()}
                    )
                    boundary_notes = " · ".join(f"{label} {s.index[-1].year}년까지 실적" for label, s in debt_series.items())
                    st.caption(
                        f"⚠️ 나라마다 실적 경계연도가 다릅니다 — {boundary_notes}. ⚠️ 전망치는 확정치가 "
                        "아닙니다 — IMF 자체 추정이며 WEO 발표(연 2회) 때마다 수정될 수 있습니다."
                    )
