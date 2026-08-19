"""기저 인플레 비교 — 헤드라인·근원·절사평균/중위, 미국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.composite import yoy_percent
from valley.macro import MacroDataError
from views._charts import navy_shades
from views.macro._shared import load_series

# "기저 인플레 비교" 뷰 전용 — 헤드라인/근원은 지수 카탈로그라 yoy_percent()로 12개월 변화율을
# 계산해야 하고("index_yoy"), 절사평균/중위 계열은 FRED가 이미 12개월 변화율(%)로 주므로 그대로
# 쓴다("rate", 이중계산 방지). ⚠️ 전부 12개월 시계·계절조정(SA)으로 통일했다 — 1개월 연율판
# (예: US_TRIMMED_MEAN_PCE_1M)은 시계가 달라 여기 포함하지 않는다(valley/macro.py의 "기저
# 인플레" 카테고리 주석 참고). 헤드라인/근원 CPI·PCE 모두 이미 계절조정(SA) 지수라 절사평균/
# 중위(둘 다 SA)와 조정 방식이 일치한다.
UNDERLYING_INFLATION_COMPARISON_KEYS = [
    ("헤드라인 CPI (YoY)", "index_yoy", "US_CPI"),
    ("근원 CPI (YoY)", "index_yoy", "US_CORE_CPI"),
    ("근원 PCE (YoY)", "index_yoy", "US_CORE_PCE"),
    ("절사평균 PCE (12개월)", "rate", "US_TRIMMED_MEAN_PCE_12M"),
    ("중위 CPI (12개월)", "rate", "US_MEDIAN_CPI_12M"),
    ("절사평균 CPI (12개월)", "rate", "US_TRIMMED_MEAN_CPI_12M"),
]


def render(country_code: str) -> None:
    if country_code != "US":
        return

    st.divider()
    st.subheader("기저 인플레 비교 — 헤드라인·근원·절사평균/중위")
    st.caption(
        "근원(core)은 식품·에너지를 범주째 제외하지만, 절사평균(trimmed mean)·중위(median)는 "
        "매달 극단적으로 오르내린 품목을 통계적으로 걸러내 '특정 품목 때문인지 전반적인지'를 "
        "다른 각도에서 봅니다. ⚠️ 전부 12개월 변화율·계절조정(SA)으로 통일했습니다 — 1개월 "
        "연율판은 시계가 달라 이 비교에서 제외했습니다(위 '기저 인플레' 카테고리에서 개별로 "
        "볼 수 있습니다)."
    )

    if st.button("기저 인플레 비교 보기", key="btn_underlying_inflation_compare"):
        st.session_state.macro_underlying_inflation_loaded = True

    if st.session_state.get("macro_underlying_inflation_loaded"):
        with st.container(border=True):
            underlying_series = {}
            underlying_failed = []
            for label, kind, key in UNDERLYING_INFLATION_COMPARISON_KEYS:
                try:
                    raw = load_series(key)
                    underlying_series[label] = yoy_percent(raw) if kind == "index_yoy" else raw
                except MacroDataError as e:
                    underlying_failed.append(f"{label}: {e}")

            if underlying_failed:
                st.warning("일부 항목을 제외했습니다: " + " / ".join(underlying_failed))

            if not underlying_series:
                st.info("표시할 수 있는 항목이 없습니다.")
            else:
                underlying_rows = [
                    {"구분": label, "최신값(%)": s.iloc[-1], "기준월": s.index[-1].strftime("%Y-%m")}
                    for label, s in underlying_series.items()
                    if not s.empty
                ]
                st.dataframe(
                    pd.DataFrame(underlying_rows).set_index("구분").style.format({"최신값(%)": "{:.2f}%"}),
                    width="stretch",
                )
                aligned = pd.concat(underlying_series, axis=1, join="inner")
                if aligned.empty:
                    st.warning("선택한 항목들 사이에 겹치는 기간이 없습니다.")
                else:
                    # ⚠️ 디자인 라운드 — 이 뷰는 6개 시리즈라(헤드라인/근원 CPI·PCE·절사평균·중위)
                    # 기본 네이비 램프(4색)가 순환돼 두 쌍이 같은 색으로 겹친다. 겹치는 구간이
                    # 40년치 월별 데이터라(아래 캡션 참고) line_chart_with_markers로 바꾸면 점
                    # 표식이 수백~수천 개 찍혀 무거워진다 — 차트 종류는 그대로 두고
                    # st.line_chart의 color 인자(1.59에서 지원, wide-format 컬럼 수만큼 색 리스트)
                    # 로 navy_shades(6)만 명시적으로 넘긴다. 레이아웃·차트 종류 변경 없음.
                    st.line_chart(aligned, color=navy_shades(len(aligned.columns)))
                    st.caption(
                        f"겹치는 구간({aligned.index[0]:%Y-%m} ~ {aligned.index[-1]:%Y-%m}) · "
                        "모두 12개월 변화율(%)·계절조정(SA) 동일 기준이라 정규화하지 않았습니다."
                    )
