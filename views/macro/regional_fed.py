"""지역 연준 제조업 서베이 세부항목 한눈에 보기 — 미국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.macro import INDICATORS, MacroDataError
from views.macro._shared import load_series

# "지역 연준 제조업 서베이 세부항목 한눈에 보기" 뷰 전용. ⚠️ 두 서베이를 한 표에 섞지 않는다 —
# 지역(필라델피아 제3연준구 vs 뉴욕주)도 다르고 항목 구성 수도 달라(6개 vs 4개) 나란히 놓으면
# 마치 같은 전국 지표의 하위항목처럼 오해된다. 종합지수(GA/GBC)도 표에 포함하되, 세부항목의
# 가중합성이 아니라 별도 설문 문항이라는 걸 뷰에서 캡션으로 명시한다(valley/macro.py의
# "경기·심리" 카테고리 주석 참고).
REGIONAL_FED_SURVEY_GROUPS = {
    "필라델피아 연준 (제3연준구: 펜실베이니아 동부·뉴저지 남부·델라웨어)": [
        "US_PHILLY_GA", "US_PHILLY_NEWORDERS", "US_PHILLY_SHIPMENTS",
        "US_PHILLY_EMPLOYMENT", "US_PHILLY_PRICES_PAID", "US_PHILLY_INVENTORIES",
    ],
    "뉴욕 연준 엠파이어스테이트 (뉴욕주)": [
        "US_EMPIRE_GA", "US_EMPIRE_NEWORDERS", "US_EMPIRE_EMPLOYMENT", "US_EMPIRE_PRICES_PAID",
    ],
}


def render(country_code: str) -> None:
    if country_code != "US":
        return

    st.divider()
    st.header("지역 연준 제조업 서베이 세부항목 한눈에 보기")
    st.caption(
        "ISM 제조업 PMI는 FRED에서 라이선스 문제로 삭제되어(2016년) 대신 지역 연준 제조업 "
        "서베이(공공 데이터)를 씁니다. 필라델피아·뉴욕 두 서베이는 지역·세부항목 구성이 달라 "
        "표를 섞지 않고 따로 보여줍니다."
    )
    st.caption(
        "⚠️ 종합지수(General Activity/General Business Conditions)는 세부항목의 가중평균이 "
        "아니라 설문의 별도 문항입니다 — CPI처럼 세부항목을 합쳐도 종합지수와 맞지 않습니다. "
        "⚠️ 이 지수들은 ISM(50 기준 확산지수)과 달리 0을 기준으로 ±로 움직입니다. ⚠️ 확산지수는 "
        "레벨 자체가 이미 '변화'를 뜻해 전년동월대비(YoY %)를 적용하지 않았고, 0을 넘나드는 "
        "값이라 퍼센트 변화 대신 전월 대비 포인트 차이로 비교합니다."
    )

    if st.button("지역 연준 제조업 서베이 불러오기", key="btn_regional_fed_survey"):
        st.session_state.macro_regional_fed_loaded = True

    if st.session_state.get("macro_regional_fed_loaded"):
        _regional_fed_label_by_key = {
            ind["key"]: ind["label"] for indicators in INDICATORS["US"].values() for ind in indicators
        }
        # ⚠️ 카드 레이아웃 라운드(2026-08) — 처음엔 st.columns(2)로 나란히 배치했으나, 실측
        # 결과(Playwright) 반쪽 폭(약 370px)에서 표의 "기준월" 열이 "2026-07"이 "202"로
        # 잘려 보였다(글리드 데이터 그리드가 열 너비를 자동으로 좁혀 텍스트를 자름 — 스크롤이
        # 아니라 진짜 잘림). "columns가 깨지지 않는 선에서"라는 지시에 따라 나란히 배치를
        # 포기하고, 두 서베이를 섞지 않는다는 원칙(코드 상단 주석 참고)은 각자 전체 폭 카드로
        # 세로로 쌓아 지킨다.
        for region_label, keys in REGIONAL_FED_SURVEY_GROUPS.items():
            with st.container(border=True):
                st.subheader(region_label)
                rows = []
                failed = []
                for key in keys:
                    label = _regional_fed_label_by_key.get(key, key)
                    try:
                        series = load_series(key)
                        if len(series) < 2:
                            raise MacroDataError("전월 대비를 계산할 만큼 데이터가 충분하지 않습니다.")
                    except MacroDataError as e:
                        failed.append(f"{label}: {e}")
                        continue
                    latest = float(series.iloc[-1])
                    previous = float(series.iloc[-2])
                    rows.append(
                        {
                            "항목": label,
                            "최근 값": latest,
                            "전월 값": previous,
                            "전월비 변화(p)": latest - previous,
                            "기준월": series.index[-1].strftime("%Y-%m"),
                        }
                    )

                if failed:
                    st.warning("일부 항목을 제외했습니다: " + " / ".join(failed))

                if not rows:
                    st.info("표시할 수 있는 항목이 없습니다.")
                else:
                    region_df = pd.DataFrame(rows).set_index("항목")
                    st.dataframe(
                        region_df.style.format({"최근 값": "{:+.1f}", "전월 값": "{:+.1f}", "전월비 변화(p)": "{:+.1f}p"}),
                        width="stretch",
                    )
