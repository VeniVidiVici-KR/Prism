"""Macro Indicator — 미국/한국 매크로 지표(물가·금리·고용·성장·통화) 조회 페이지."""

import os

import streamlit as st
from dotenv import load_dotenv

import pandas as pd

from valley.composite import (
    COMPOSITE_INDICATORS,
    CROSS_COUNTRY_INDICATORS,
    compute_cpi_contributions,
    get_composite_series,
    yoy_percent,
)
from valley.macro import (
    INDICATORS,
    KR_CPI_MAJOR_ITEM_CODES,
    US_CPI_COMPONENT_BLS_IDS,
    MacroDataError,
    get_bls_relative_importance,
    get_ecos_item_weights,
    get_ecos_series,
    get_indicator_series,
    normalize_indexed,
)
from views._charts import contribution_bar_chart, pie_chart

MAX_OVERLAY_INDICATORS = 3
MAX_BUTTONS_PER_ROW = 4

# STEP 3 "CPI 구성항목 한눈에 보기" 비교표에 포함할 key 목록. ⚠️ 동일 조정(SA/NSA) 방식만
# 묶는다 — 미국은 원계열(NSA)로 통일한 항목만, PCE 재화/서비스(계절조정)는 조정 방식이 달라
# 이 비교표에서 제외한다(valley/macro.py의 "물가 세부" 카탈로그 주석 참고). 한국은 기존
# KR_CPI(원계열)와 새 구성항목이 원래 같은 조정 방식이라 그대로 묶는다.
CPI_COMPARISON_KEYS = {
    "US": [
        "US_CPI_NSA", "US_CPI_SHELTER", "US_CPI_FOOD", "US_CPI_ENERGY",
        "US_CPI_CORE_GOODS", "US_CPI_CORE_SERVICES",
    ],
    "KR": ["KR_CPI", "KR_CPI_FOOD", "KR_CPI_HOUSING", "KR_CPI_TRANSPORT", "KR_CPI_DINING"],
}

# "CPI 구성 비중 & 기여도" 뷰 전용 — 배타적이고 망라적인 4분할(주거비 등 하위 항목은 근원서비스
# 안에 포함되므로 이 집합에서 제외, valley/macro.py의 US_CPI_COMPONENT_BLS_IDS 주석 참고).
US_CPI_BREAKDOWN_KEYS = ["US_CPI_FOOD", "US_CPI_ENERGY", "US_CPI_CORE_GOODS", "US_CPI_CORE_SERVICES"]

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
    st.session_state.macro_cpi_components_loaded = False
    st.session_state.macro_cpi_breakdown_loaded = False

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


def _render_indicator_buttons(indicators: list[dict]) -> None:
    """카탈로그가 커져 한 줄에 다 못 넣을 수 있어(예: '물가 세부') 최대
    MAX_BUTTONS_PER_ROW개씩 여러 줄로 나눠 그린다 — 항목이 적은 카테고리는 기존과
    동일하게 한 줄로 보인다."""
    for i in range(0, len(indicators), MAX_BUTTONS_PER_ROW):
        chunk = indicators[i : i + MAX_BUTTONS_PER_ROW]
        cols = st.columns(MAX_BUTTONS_PER_ROW)
        for col, indicator in zip(cols, chunk):
            if col.button(indicator["label"], key=f"btn_{indicator['key']}", width="stretch"):
                st.session_state.macro_selected = indicator["key"]


for category, indicators in INDICATORS[country_code].items():
    st.subheader(category)
    _render_indicator_buttons(indicators)

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
        "비교표에는 포함하지 않았습니다 — 위 '물가 세부'에서 개별로 볼 수 있습니다.)"
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
                raw = _load_series(key)
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

st.divider()
st.header("CPI 구성 비중 & 기여도")
st.caption(
    "이번 달 헤드라인 인플레이션 중 무엇이 얼마나 밀어올렸는지 분해합니다. "
    "기여도(%p) ≈ 가중치(%) × YoY(%). 위 '구성항목 한눈에 보기'와 달리, 여기서는 겹치거나 "
    "층위가 섞인 항목(미국 주거비-근원서비스, 한국 외식-음식/숙박)을 제외한 배타적·망라적 "
    "집합만 써서 비중 합이 100%에 가깝게 검산되도록 했습니다."
)
st.caption(
    "⚠️ 가중치 갱신 주기: 한국은 통계청 기준년 개편 시에만 바뀌는 고정 가중치입니다. 미국은 "
    "실측 결과, 기초 지출 비중(CE 서베이) 자체는 매년 12월에만 개편되지만 BLS가 화면에 보여주는 "
    "'상대중요도(Relative Importance)'는 그 위에 가격 변동을 반영해 매월 소폭 재계산되는 "
    "공식 값입니다(BLS 정의) — 물가지표처럼 매번 바뀌는 값은 아니지만 \"연 1회\"는 아닙니다."
)


# ⚠️ 가중치는 지표값과 달리 자주 바뀌지 않는다(미국은 매월 소폭 갱신되지만 물가처럼 매시간
# 갱신되는 값이 아니고, 한국은 기준년 개편 시에만 바뀐다) — ttl을 1시간(3600)보다 길게(1일,
# 86400) 잡아 불필요한 재조회를 줄인다. YoY 계열(_load_series)은 기존대로 3600 유지.
@st.cache_data(ttl=86400, show_spinner=False)
def _load_bls_weight(bls_series_id: str):
    return get_bls_relative_importance(bls_series_id)


@st.cache_data(ttl=86400, show_spinner=False)
def _load_ecos_weights(stat_code: str):
    return get_ecos_item_weights(stat_code)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_kr_cpi_component(item_code: str):
    return get_ecos_series("901Y009", "M", item_code)


def _render_cpi_breakdown(components: list[dict], headline_yoy: pd.Series | None) -> None:
    """구성 비중 원 그래프 + 기여도 막대그래프 + 자기검증(기여도 합 vs 헤드라인)을 그린다.

    components 각 항목은 이미 "공통으로 존재하는 최신 월" 기준으로 정렬된 {"label",
    "weight_pct", "yoy"}이고, 호출부(국가별 분기)가 그 정렬을 책임진다.
    """
    contrib_df, total_contribution = compute_cpi_contributions(components)
    weight_sum = contrib_df["가중치(%)"].sum()
    st.caption(f"가중치 합계: {weight_sum:.1f}% (100%에서 크게 벗어나면 항목 누락/중복 가능성 — 위 캡션의 출처 참고).")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**구성 비중**")
        pie_chart(contrib_df, "항목", "가중치(%)")
    with col2:
        st.markdown("**기여도 (%p, 큰 순 정렬)**")
        contribution_bar_chart(contrib_df, "항목", "기여도(%p)")

    st.dataframe(
        contrib_df.set_index("항목").style.format(
            {"가중치(%)": "{:.1f}%", "YoY(%)": "{:+.2f}%", "기여도(%p)": "{:+.2f}%p"}
        ),
        width="stretch",
    )

    if headline_yoy is None:
        st.warning("헤드라인 YoY와 기준월이 겹치지 않아 자기검증(기여도 합 vs 헤드라인)을 생략합니다.")
    else:
        diff = total_contribution - headline_yoy
        st.caption(
            f"⚠️ 자기검증 — 기여도 합계 {total_contribution:+.2f}%p vs 헤드라인 YoY {headline_yoy:+.2f}% "
            f"→ 차이 {diff:+.2f}%p. (반올림·가중치 기준년 시차로 인한 작은 차이는 정상이나, "
            "크게 벗어나면 항목 집합/가중치가 잘못됐다는 뜻입니다.)"
        )


if country_code == "US":
    if not os.environ.get("BLS_API_KEY"):
        st.info(
            "BLS_API_KEY가 설정되어 있지 않아 미국 구성 비중 & 기여도를 표시할 수 없습니다 — "
            "가중치(상대중요도)는 BLS 공식 API의 등록키 전용 메타데이터이고, BLS의 HTML/원본파일 "
            "서버는 봇 정책상 자동 접근이 막혀 있어 대체할 수 없습니다. "
            "https://www.bls.gov/developers/ 에서 무료 등록키를 발급받아 .env에 "
            "BLS_API_KEY로 추가하세요."
        )
    else:
        if st.button("구성 비중 & 기여도 불러오기", key="btn_cpi_breakdown"):
            st.session_state.macro_cpi_breakdown_loaded = True

        if st.session_state.get("macro_cpi_breakdown_loaded"):
            _breakdown_label_by_key = {
                ind["key"]: ind["label"] for indicators in INDICATORS["US"].values() for ind in indicators
            }
            yoy_by_label: dict[str, pd.Series] = {}
            weight_by_label: dict[str, float] = {}
            weight_as_of = None
            errors = []
            for key in US_CPI_BREAKDOWN_KEYS:
                label = _breakdown_label_by_key.get(key, key)
                try:
                    weight_pct, weight_as_of = _load_bls_weight(US_CPI_COMPONENT_BLS_IDS[key])
                    yoy = yoy_percent(_load_series(key))
                    if yoy.empty:
                        raise MacroDataError("YoY를 계산할 만큼 데이터가 충분하지 않습니다.")
                except MacroDataError as e:
                    errors.append(f"{label}: {e}")
                    continue
                yoy_by_label[label] = yoy
                weight_by_label[label] = weight_pct

            if errors:
                st.warning("일부 항목을 제외했습니다: " + " / ".join(errors))

            if len(yoy_by_label) < 2:
                st.info("표시할 수 있는 구성항목이 부족합니다.")
            else:
                aligned = pd.concat(yoy_by_label, axis=1, join="inner")
                if aligned.empty:
                    st.warning("구성항목들 사이에 겹치는 기준월이 없습니다.")
                else:
                    common_month = aligned.index[-1]
                    latest_row = aligned.loc[common_month]
                    components = [
                        {"label": label, "weight_pct": weight_by_label[label], "yoy": latest_row[label]}
                        for label in yoy_by_label
                    ]
                    st.caption(f"기준월: {common_month:%Y-%m} (구성항목 공통 최신월) · 가중치 기준: {weight_as_of}(BLS 상대중요도)")

                    headline_value = None
                    try:
                        headline_yoy_series = yoy_percent(_load_series("US_CPI_NSA"))
                        if common_month in headline_yoy_series.index:
                            headline_value = float(headline_yoy_series.loc[common_month])
                    except MacroDataError:
                        pass

                    _render_cpi_breakdown(components, headline_value)
else:
    if st.button("구성 비중 & 기여도 불러오기", key="btn_cpi_breakdown"):
        st.session_state.macro_cpi_breakdown_loaded = True

    if st.session_state.get("macro_cpi_breakdown_loaded"):
        try:
            weights = _load_ecos_weights("901Y009")
        except MacroDataError as e:
            st.warning(f"가중치 조회에 실패했습니다: {e}")
        else:
            yoy_by_label: dict[str, pd.Series] = {}
            weight_by_label: dict[str, float] = {}
            errors = []
            for code in KR_CPI_MAJOR_ITEM_CODES:
                if code not in weights:
                    errors.append(f"{code}: 가중치 메타데이터에 없음")
                    continue
                name, weight_permille = weights[code]
                try:
                    yoy = yoy_percent(_load_kr_cpi_component(code))
                    if yoy.empty:
                        raise MacroDataError("YoY를 계산할 만큼 데이터가 충분하지 않습니다.")
                except MacroDataError as e:
                    errors.append(f"{name}: {e}")
                    continue
                yoy_by_label[name] = yoy
                weight_by_label[name] = weight_permille / 10

            if errors:
                st.warning("일부 항목을 제외했습니다: " + " / ".join(errors))

            if len(yoy_by_label) < 2:
                st.info("표시할 수 있는 구성항목이 부족합니다.")
            else:
                aligned = pd.concat(yoy_by_label, axis=1, join="inner")
                if aligned.empty:
                    st.warning("구성항목들 사이에 겹치는 기준월이 없습니다.")
                else:
                    common_month = aligned.index[-1]
                    latest_row = aligned.loc[common_month]
                    components = [
                        {"label": label, "weight_pct": weight_by_label[label], "yoy": latest_row[label]}
                        for label in yoy_by_label
                    ]
                    st.caption(
                        f"기준월: {common_month:%Y-%m} (구성항목 공통 최신월) · "
                        "가중치 출처: ECOS StatisticItemList(901Y009) 실시간 메타데이터"
                    )

                    headline_value = None
                    try:
                        headline_yoy_series = yoy_percent(_load_series("KR_CPI"))
                        if common_month in headline_yoy_series.index:
                            headline_value = float(headline_yoy_series.loc[common_month])
                    except MacroDataError:
                        pass

                    _render_cpi_breakdown(components, headline_value)

st.divider()
st.header("한미 국가부채/GDP 비교")
st.caption(
    "국제 비교 표준인 일반정부 총부채(General government gross debt, IMF WEO 기준, GFSM 2001 "
    "정의)로 미국·한국을 동일 기준으로 비교합니다. 이미 IMF가 %로 계산해 발표한 값을 그대로 "
    "쓰고(지수·절대액에서 직접 나누지 않음), 둘 다 같은 단위(%)라 정규화 없이 원값을 겹쳐 "
    "그립니다. ⚠️ 연간 데이터라 두 나라의 최신 발표 연도가 다를 수 있습니다 — 표의 '기준연도' "
    "열을 확인하세요."
)

_DEBT_GDP_KEYS = {"미국": "US_DEBT_GDP", "한국": "KR_DEBT_GDP"}

if st.button("한미 국가부채/GDP 비교 보기", key="btn_debt_gdp_compare"):
    st.session_state.macro_debt_gdp_loaded = True

if st.session_state.get("macro_debt_gdp_loaded"):
    debt_series = {}
    debt_failed = []
    for country_label, key in _DEBT_GDP_KEYS.items():
        try:
            debt_series[country_label] = _load_series(key)
        except MacroDataError as e:
            debt_failed.append(f"{country_label}: {e}")

    if debt_failed:
        st.warning("일부 국가 데이터를 가져오지 못했습니다: " + " / ".join(debt_failed))

    if not debt_series:
        st.info("표시할 수 있는 데이터가 없습니다.")
    else:
        debt_rows = [
            {"국가": label, "최신값(%)": s.iloc[-1], "기준연도": s.index[-1].strftime("%Y")}
            for label, s in debt_series.items()
        ]
        st.dataframe(
            pd.DataFrame(debt_rows).set_index("국가").style.format({"최신값(%)": "{:.1f}%"}),
            width="stretch",
        )
        if len(debt_series) >= 2:
            # 연간 데이터라 두 나라의 마지막 관측 연도가 다를 수 있어(실측: 미국 2024년까지,
            # 한국 2023년까지) join="outer"로 겹쳐 그린다 — inner로 자르면 더 최신인 쪽의
            # 마지막 해가 잘려나가 정보 손실이 생긴다.
            st.line_chart(pd.concat(debt_series, axis=1, join="outer"))
        st.caption("두 시리즈 모두 이미 %로 계산된 IMF WEO 원값입니다 — 정규화하지 않았습니다.")

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
