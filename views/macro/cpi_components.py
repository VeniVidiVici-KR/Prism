"""CPI 구성항목 한눈에 보기 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.composite import yoy_percent
from valley.macro import INDICATORS, MacroDataError
from views.macro._shared import load_series

# ⚠️ 동일 조정(SA/NSA) 방식만 묶는다 — 미국은 원계열(NSA)로 통일한 항목만, PCE 재화/서비스
# (계절조정)는 조정 방식이 달라 이 비교표에서 제외한다(valley/macro.py의 "물가 구성" 카탈로그
# 주석 참고). 한국은 기존 KR_CPI(원계열)와 새 구성항목이 원래 같은 조정 방식이라 그대로 묶는다.
CPI_COMPARISON_KEYS = {
    "US": [
        "US_CPI_NSA", "US_CPI_SHELTER", "US_CPI_FOOD", "US_CPI_ENERGY",
        "US_CPI_CORE_GOODS", "US_CPI_CORE_SERVICES",
    ],
    "KR": ["KR_CPI", "KR_CPI_FOOD", "KR_CPI_HOUSING", "KR_CPI_TRANSPORT", "KR_CPI_DINING"],
}


def render(country_code: str) -> None:
    st.divider()
    st.header("CPI 구성항목 한눈에 보기")
    st.caption(
        "\"무엇이 물가를 밀어올리는가\"를 보려고, 구성항목들의 전년동월대비(YoY %) 상승률을 한 표로 "
        "비교합니다. 총지수도 비교 기준으로 함께 표시합니다."
    )

    _cpi_comparison_keys = CPI_COMPARISON_KEYS.get(country_code, [])
    if not _cpi_comparison_keys:
        st.info("이 국가는 아직 CPI 구성항목 비교를 지원하지 않습니다.")
    else:
        is_us = country_code == "US"
        st.caption(
            "미국은 원계열(NSA) 기준으로 통일했습니다 — 전년동월대비 비교는 계절성이 12개월 차분에서 "
            "상쇄되는 원계열이 표준적입니다. (PCE 재화/서비스 물가지수는 계절조정(SA) 기준이라 이 "
            "비교표에는 포함하지 않았습니다 — 위 '물가 구성'에서 개별로 볼 수 있습니다.)"
            if is_us
            else "한국 CPI는 원래 원계열 기준으로 발표되어 총지수·구성항목이 모두 같은 기준입니다."
        )

        # ⚠️ 비용 통제: 구성항목이 5~6개라 버튼 클릭 시에만 조회한다(자동 로드 금지). _load_series는
        # 이미 st.cache_data(ttl=3600)이라 같은 세션 안 재클릭·재실행은 재호출 없이 캐시를 탄다.
        if st.button("CPI 구성항목 비교 불러오기", key="btn_cpi_components"):
            st.session_state.macro_cpi_components_loaded = True

        if st.session_state.get("macro_cpi_components_loaded"):
            _label_by_key = {
                ind["key"]: ind["label"] for indicators in INDICATORS[country_code].values() for ind in indicators
            }
            rows = []
            failed = []
            for key in _cpi_comparison_keys:
                label = _label_by_key.get(key, key)
                try:
                    raw = load_series(key)
                    yoy = yoy_percent(raw)
                    if len(yoy) < 2:
                        raise MacroDataError("YoY를 비교할 만큼 데이터가 충분하지 않습니다.")
                except MacroDataError as e:
                    failed.append(f"{label}: {e}")
                    continue
                rows.append(
                    {
                        "항목": label,
                        "최근 YoY%": yoy.iloc[-1],
                        "전월 YoY%": yoy.iloc[-2],
                        "기준월": yoy.index[-1].strftime("%Y-%m"),
                    }
                )

            if failed:
                st.warning("일부 항목 조회에 실패해 제외했습니다: " + " / ".join(failed))

            if not rows:
                st.info("표시할 수 있는 구성항목이 없습니다.")
            else:
                comparison_df = pd.DataFrame(rows).set_index("항목")
                st.dataframe(
                    comparison_df.style.format({"최근 YoY%": "{:+.2f}%", "전월 YoY%": "{:+.2f}%"}),
                    width="stretch",
                )
                st.caption(
                    "⚠️ 항목마다 발표 시차로 기준월이 다를 수 있습니다 — 표의 '기준월' 열을 확인하세요. "
                    "최근 N개월 추이를 함께 보려면 아래 '지표 겹쳐보기'에서 같은 항목들을 선택하세요."
                )
