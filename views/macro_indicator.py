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
    mom_change,
    mom_percent,
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
MAX_BUTTONS_PER_ROW = 5

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

# "고용보고서 한눈에 보기" 표 전용. (key, 출처 라벨) — 출처는 카탈로그에 없는 정보라 여기서
# 직접 매핑한다. ⚠️ 사업체조사(Establishment/CES, "일자리 수" 기준)와 가계조사(Household/CPS,
# "사람 수" 기준)는 표본·정의가 다른 별개 조사다 — 표에서 반드시 이 출처를 나란히 표시해
# "같은 조사에서 나온 수치가 아님"이 드러나게 한다. 평균 시간당 임금(수준→MoM/YoY 변환 필요)은
# 별도로 처리하므로 이 목록에 없다.
EMPLOYMENT_HEADLINE_ITEMS = [
    ("US_PAYEMS", "사업체조사(CES)"),
    ("US_PRIV_PAYEMS", "사업체조사(CES)"),
    ("US_UNRATE", "가계조사(CPS)"),
    ("US_CIVPART", "가계조사(CPS)"),
    ("US_EMRATIO", "가계조사(CPS)"),
    ("US_U6RATE", "가계조사(CPS)"),
]

# "비농업고용 업종별 분해" 전용. BLS 공식 supersector 11개(정부 포함), 배타적·망라적 —
# valley/macro.py의 "고용 세부" 카테고리 주석 참고. ⚠️ USPRIV(민간고용)는 정부를 뺀 민간
# 합계라 이 11개와 겹치므로 절대 포함하지 않는다.
NFP_SECTOR_KEYS = [
    "US_NFP_MINING", "US_NFP_CONSTRUCTION", "US_NFP_MANUFACTURING",
    "US_NFP_TRADE_TRANSPORT_UTIL", "US_NFP_INFO", "US_NFP_FINANCIAL",
    "US_NFP_PROF_BIZ", "US_NFP_EDU_HEALTH", "US_NFP_LEISURE",
    "US_NFP_OTHER_SERVICES", "US_NFP_GOVERNMENT",
]

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
    st.session_state.macro_regional_fed_loaded = False
    st.session_state.macro_employment_report_loaded = False

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


def _balanced_row_sizes(n: int, max_cols: int = MAX_BUTTONS_PER_ROW) -> list[int]:
    """n개 항목을 max_cols 이하 열의 여러 줄에 최대한 고르게 나눈다.

    예전엔 max_cols개씩 고정으로 잘라(예: 10개를 4개씩) 마지막 줄만 휑하게 남았다
    (4+4+2). 대신 줄 수를 먼저 정하고(ceil(n/max_cols)) 그 줄 수로 최대한 균등하게
    나눠(10개, 최대 5열 → 5+5) 마지막 줄이 비어 보이지 않게 한다. 반환값은 각 줄에 넣을
    항목 수 리스트(예: [5, 5] 또는 [3, 3]).
    """
    if n <= 0:
        return []
    if n <= max_cols:
        return [n]
    rows = -(-n // max_cols)  # ceil division
    base, extra = divmod(n, rows)
    return [base + 1 if i < extra else base for i in range(rows)]


def _render_indicator_buttons(indicators: list[dict]) -> None:
    """_balanced_row_sizes로 계산한 줄 구성대로 버튼을 그린다.

    ⚠️ 현재 선택된 지표는 type="primary"로 강조해 어떤 지표를 보고 있는지 한눈에
    드러나게 하고(이전엔 버튼이라 선택 상태가 안 보였다), description이 있으면 버튼
    마우스오버 툴팁(help)으로 보여준다 — 라벨을 짧게 줄이며 뺀 부가정보를 삭제한 게
    아니라 자리를 옮긴 것이다(캡션 쪽은 아래 지표 선택 후 표시 영역에서 담당).
    """
    row_sizes = _balanced_row_sizes(len(indicators))
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
        if indicator.get("description"):
            st.caption(indicator["description"])
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

if country_code == "US":
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
        for region_label, keys in REGIONAL_FED_SURVEY_GROUPS.items():
            st.subheader(region_label)
            rows = []
            failed = []
            for key in keys:
                label = _regional_fed_label_by_key.get(key, key)
                try:
                    series = _load_series(key)
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

if country_code == "US":
    st.divider()
    st.header("고용보고서 한눈에 보기")
    st.caption(
        "매달 첫 금요일 발표되는 미국 고용보고서(Employment Situation)의 핵심 지표를 한 표로 "
        "봅니다. ⚠️ 서로 다른 두 조사가 섞여 있습니다 — 비농업고용·민간고용·평균 시간당 임금은 "
        "사업체를 대상으로 한 사업체조사(Establishment/CES, '일자리 수' 기준)이고, 실업률· "
        "경제활동참가율·고용률·광의실업률은 가구를 대상으로 한 가계조사(Household/CPS, '사람 "
        "수' 기준)입니다. 표본과 정의가 달라 같은 달에도 두 조사가 서로 어긋나는 흐름을 보일 수 "
        "있습니다(예: 고용은 줄었는데 실업률은 내려가는 경우)."
    )
    st.caption(
        "⚠️ 비농업고용은 매월 발표 시 직전 2개월치가 함께 수정됩니다(revision). 최근 값은 "
        "잠정치일 수 있습니다."
    )

    if st.button("고용보고서 불러오기", key="btn_employment_report"):
        st.session_state.macro_employment_report_loaded = True

    if st.session_state.get("macro_employment_report_loaded"):
        _employment_label_by_key = {
            ind["key"]: ind["label"] for indicators in INDICATORS["US"].values() for ind in indicators
        }
        headline_rows = []
        headline_failed = []

        for key, source in EMPLOYMENT_HEADLINE_ITEMS:
            label = _employment_label_by_key.get(key, key)
            try:
                s = _load_series(key)
                if len(s) < 2:
                    raise MacroDataError("전월 대비를 계산할 만큼 데이터가 충분하지 않습니다.")
            except MacroDataError as e:
                headline_failed.append(f"{label}: {e}")
                continue
            latest, previous = float(s.iloc[-1]), float(s.iloc[-2])
            is_count = key in ("US_PAYEMS", "US_PRIV_PAYEMS")
            headline_rows.append(
                {
                    "지표": label,
                    "출처": source,
                    "최근 값": f"{latest:,.0f}천 명" if is_count else f"{latest:.1f}%",
                    "전월": f"{previous:,.0f}천 명" if is_count else f"{previous:.1f}%",
                    "변화": f"{latest - previous:+,.0f}천 명" if is_count else f"{latest - previous:+.1f}%p",
                    "기준월": s.index[-1].strftime("%Y-%m"),
                }
            )

        try:
            wage_raw = _load_series("US_AVG_HOURLY_EARNINGS")
            wage_mom = mom_percent(wage_raw)
            wage_yoy = yoy_percent(wage_raw)
            if len(wage_mom) < 2 or len(wage_yoy) < 2:
                raise MacroDataError("변화율을 비교할 만큼 데이터가 충분하지 않습니다.")
        except MacroDataError as e:
            headline_failed.append(f"평균 시간당 임금: {e}")
        else:
            headline_rows.append(
                {
                    "지표": "평균 시간당 임금 (전월비)",
                    "출처": "사업체조사(CES)",
                    "최근 값": f"{wage_mom.iloc[-1]:+.2f}%",
                    "전월": f"{wage_mom.iloc[-2]:+.2f}%",
                    "변화": f"{wage_mom.iloc[-1] - wage_mom.iloc[-2]:+.2f}%p",
                    "기준월": wage_mom.index[-1].strftime("%Y-%m"),
                }
            )
            headline_rows.append(
                {
                    "지표": "평균 시간당 임금 (전년동월비)",
                    "출처": "사업체조사(CES)",
                    "최근 값": f"{wage_yoy.iloc[-1]:+.2f}%",
                    "전월": f"{wage_yoy.iloc[-2]:+.2f}%",
                    "변화": f"{wage_yoy.iloc[-1] - wage_yoy.iloc[-2]:+.2f}%p",
                    "기준월": wage_yoy.index[-1].strftime("%Y-%m"),
                }
            )

        if headline_failed:
            st.warning("일부 항목을 제외했습니다: " + " / ".join(headline_failed))

        if not headline_rows:
            st.info("표시할 수 있는 항목이 없습니다.")
        else:
            st.dataframe(pd.DataFrame(headline_rows).set_index("지표"), width="stretch")

        st.divider()
        st.subheader("비농업고용 업종별 분해")
        st.caption(
            "비농업고용(PAYEMS) 전월비 증감을 정부를 포함한 11개 업종(배타적·망라적)으로 나눠 "
            "봅니다. 전월비 증감(천 명) 기준이며 YoY %가 아닙니다 — 고용은 '몇 명 늘고 줄었나'로 "
            "보는 게 시장 표준입니다. 음수(감소)가 흔하니 0선과 색으로 구분해 표시합니다."
        )

        sector_rows = []
        sector_failed = []
        sector_total = 0.0
        for key in NFP_SECTOR_KEYS:
            label = _employment_label_by_key.get(key, key)
            try:
                s = _load_series(key)
                changes = mom_change(s)
                if changes.empty:
                    raise MacroDataError("전월 대비를 계산할 만큼 데이터가 충분하지 않습니다.")
            except MacroDataError as e:
                sector_failed.append(f"{label}: {e}")
                continue
            latest_change = float(changes.iloc[-1])
            sector_total += latest_change
            sector_rows.append(
                {
                    "업종": label,
                    "고용자 수(천 명)": float(s.iloc[-1]),
                    "전월비 증감(천 명)": latest_change,
                    "기준월": s.index[-1].strftime("%Y-%m"),
                }
            )

        if sector_failed:
            st.warning("일부 업종을 제외했습니다: " + " / ".join(sector_failed))

        if not sector_rows:
            st.info("표시할 수 있는 업종이 없습니다.")
        else:
            sector_df = pd.DataFrame(sector_rows)
            contribution_bar_chart(sector_df, "업종", "전월비 증감(천 명)")
            st.dataframe(
                sector_df.set_index("업종").style.format(
                    {"고용자 수(천 명)": "{:,.0f}", "전월비 증감(천 명)": "{:+,.0f}"}
                ),
                width="stretch",
            )

            headline_change = None
            try:
                payems_changes = mom_change(_load_series("US_PAYEMS"))
                if not payems_changes.empty:
                    headline_change = float(payems_changes.iloc[-1])
            except MacroDataError:
                pass

            if headline_change is None:
                st.warning("헤드라인 비농업고용 증감을 불러오지 못해 자기검증을 생략합니다.")
            else:
                diff = sector_total - headline_change
                st.caption(
                    f"⚠️ 자기검증 — 11개 업종 증감 합계 {sector_total:+,.1f}천 명 vs 헤드라인(PAYEMS) "
                    f"전월비 증감 {headline_change:+,.1f}천 명 → 차이 {diff:+,.1f}천 명. (BLS는 각 "
                    "시리즈를 개별 계절조정하므로 작은 차이는 정상입니다. 억지로 맞추지 않습니다.)"
                )

                uspriv_change = None
                try:
                    uspriv_changes = mom_change(_load_series("US_PRIV_PAYEMS"))
                    if not uspriv_changes.empty:
                        uspriv_change = float(uspriv_changes.iloc[-1])
                except MacroDataError:
                    pass
                govt_row = next((r for r in sector_rows if r["업종"] == "정부"), None)

                if uspriv_change is not None and govt_row is not None:
                    implied_govt = headline_change - uspriv_change
                    st.caption(
                        f"⚠️ 보조 검증 — 비농업고용({headline_change:+,.1f}천 명) − 민간고용"
                        f"({uspriv_change:+,.1f}천 명) = {implied_govt:+,.1f}천 명 vs 정부 업종 증감 "
                        f"{govt_row['전월비 증감(천 명)']:+,.1f}천 명."
                    )

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
