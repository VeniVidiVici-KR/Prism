"""Macro Indicator — 미국/한국 매크로 지표(물가·금리·고용·성장·통화) 조회 페이지."""

import os

import streamlit as st
from dotenv import load_dotenv

import pandas as pd

from valley.composite import COMPOSITE_INDICATORS, CROSS_COUNTRY_INDICATORS, get_composite_series
from valley.macro import INDICATORS, MacroDataError, get_indicator_series, normalize_indexed

MAX_OVERLAY_INDICATORS = 3

load_dotenv(override=True)

st.title("🌐 Macro Indicator")
st.caption("가능한 긴 시계열의 물가/금리/고용/성장/통화 지표 · 정보 제공 목적이며 투자자문이 아닙니다.")

country = st.radio("국가", ["미국", "한국"], horizontal=True)
country_code = "US" if country == "미국" else "KR"

# 국가를 바꾸면 이전에 선택했던 지표는 초기화한다.
if st.session_state.get("macro_country") != country_code:
    st.session_state.macro_country = country_code
    st.session_state.macro_selected = None
    st.session_state.macro_composite_selected = None
    st.session_state.macro_overlay_selection = []

if country_code == "KR" and not os.environ.get("ECOS_API_KEY"):
    st.info(
        "ECOS_API_KEY가 설정되어 있지 않아 한국 CPI/M2가 FRED의 미러링 데이터(갱신이 멈춘 과거 "
        "시점까지)로 표시됩니다. 최신 데이터를 보려면 https://ecos.bok.or.kr/api/ 에서 API 키를 "
        "발급받아 .env에 추가하세요."
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _load_series(key: str):
    return get_indicator_series(key)


st.caption("아래 버튼을 눌러 보고 싶은 지표의 차트만 불러옵니다.")

for category, indicators in INDICATORS[country_code].items():
    st.subheader(category)
    cols = st.columns(len(indicators))
    for col, indicator in zip(cols, indicators):
        if col.button(indicator["label"], key=f"btn_{indicator['key']}", width="stretch"):
            st.session_state.macro_selected = indicator["key"]

st.divider()

selected_key = st.session_state.get("macro_selected")
if not selected_key:
    st.info("위에서 지표 버튼을 눌러주세요.")
else:
    indicator = next(
        ind for indicators in INDICATORS[country_code].values() for ind in indicators if ind["key"] == selected_key
    )
    try:
        series = _load_series(selected_key)
    except MacroDataError as e:
        st.warning(f"{indicator['label']}: {e}")
    else:
        st.markdown(f"**{indicator['label']}**")
        st.line_chart(series)
        st.caption(f"데이터 기준일: {series.index[-1]:%Y-%m} · {len(series)}개 관측치")

st.divider()
st.header("혼합 지표")
st.caption(
    "서로 다른 원본 지표를 조합해 만든 파생 지표입니다. GDP 등 분기 지표가 섞이면 "
    "월별 지표보다 갱신이 늦게 반영될 수 있습니다."
)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_composite_series(key: str):
    return get_composite_series(key)


composite_cols = st.columns(len(COMPOSITE_INDICATORS[country_code]))
for col, indicator in zip(composite_cols, COMPOSITE_INDICATORS[country_code]):
    if col.button(indicator["label"], key=f"btn_composite_{indicator['key']}", width="stretch"):
        st.session_state.macro_composite_selected = indicator["key"]

st.caption("교차국가")
cross_cols = st.columns(len(CROSS_COUNTRY_INDICATORS))
for col, indicator in zip(cross_cols, CROSS_COUNTRY_INDICATORS):
    if col.button(indicator["label"], key=f"btn_composite_{indicator['key']}", width="stretch"):
        st.session_state.macro_composite_selected = indicator["key"]

composite_selected_key = st.session_state.get("macro_composite_selected")
if not composite_selected_key:
    st.info("위에서 혼합 지표 버튼을 눌러주세요.")
else:
    all_composite = [*COMPOSITE_INDICATORS["US"], *COMPOSITE_INDICATORS["KR"], *CROSS_COUNTRY_INDICATORS]
    composite_indicator = next(ind for ind in all_composite if ind["key"] == composite_selected_key)
    try:
        composite_series = _load_composite_series(composite_selected_key)
    except MacroDataError as e:
        st.warning(f"{composite_indicator['label']}: {e}")
    else:
        st.markdown(f"**{composite_indicator['label']}**")
        st.line_chart(composite_series)
        st.caption(f"데이터 기준일: {composite_series.index[-1]:%Y-%m} · {len(composite_series)}개 관측치")

st.divider()
st.header("지표 겹쳐보기")
st.caption(
    "서로 다른 단위의 지표를 비교할 수 있도록, 겹치는 기간의 첫 관측치를 100으로 지수화해 "
    "정규화한 뒤 겹쳐 그립니다. 절대 수준(원계열)이 궁금하면 위 단일/혼합 지표 보기를 이용하세요."
)

_overlay_composite_keys = {ind["key"] for ind in COMPOSITE_INDICATORS[country_code]}
_overlay_options = [
    (ind["key"], ind["label"])
    for indicators in INDICATORS[country_code].values()
    for ind in indicators
] + [(ind["key"], ind["label"]) for ind in COMPOSITE_INDICATORS[country_code]]
_overlay_label_by_key = dict(_overlay_options)

overlay_selection = st.multiselect(
    "겹쳐볼 지표 선택 (2~3개)",
    options=[key for key, _ in _overlay_options],
    format_func=lambda k: _overlay_label_by_key[k],
    key="macro_overlay_selection",
)

if len(overlay_selection) > MAX_OVERLAY_INDICATORS:
    st.warning(f"최대 {MAX_OVERLAY_INDICATORS}개까지 선택할 수 있습니다. 앞의 {MAX_OVERLAY_INDICATORS}개만 사용합니다.")
    overlay_selection = overlay_selection[:MAX_OVERLAY_INDICATORS]

if len(overlay_selection) < 2:
    st.info("2개 이상 선택하면 정규화해 겹쳐 그린 차트를 보여드립니다.")
else:
    overlay_series = {}
    overlay_failed = []
    for key in overlay_selection:
        label = _overlay_label_by_key[key]
        try:
            raw = _load_composite_series(key) if key in _overlay_composite_keys else _load_series(key)
        except MacroDataError as e:
            overlay_failed.append(f"{label}: {e}")
            continue
        overlay_series[label] = raw

    if overlay_failed:
        st.warning("일부 지표 조회에 실패해 제외했습니다: " + " / ".join(overlay_failed))

    if len(overlay_series) < 2:
        st.info("겹쳐 그릴 수 있는 지표가 2개 미만입니다.")
    else:
        # 서로 주기(월별/분기별 등)가 다를 수 있어, 겹치는 구간만 남긴 뒤(join="inner") 그 구간의
        # 첫 관측치를 기준으로 정규화한다 — 이래야 차트에서 두 선이 같은 시작점(=100)에서 출발한다.
        aligned = pd.concat(overlay_series, axis=1, join="inner")
        if aligned.empty:
            st.warning("선택한 지표들 사이에 겹치는 기간이 없습니다.")
        else:
            normalized = aligned.apply(normalize_indexed)
            st.line_chart(normalized)
            st.caption(
                f"겹치는 구간({aligned.index[0]:%Y-%m} ~ {aligned.index[-1]:%Y-%m})의 첫 관측치 = 100 기준 지수화."
            )
