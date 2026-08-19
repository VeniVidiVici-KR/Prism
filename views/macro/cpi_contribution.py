"""CPI 구성 비중 & 기여도 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import os

import pandas as pd
import streamlit as st

from valley.composite import compute_cpi_contributions, mom_percent, yoy_percent
from valley.macro import (
    INDICATORS,
    KR_CPI_MAJOR_ITEM_CODES,
    US_CPI_COMPONENT_BLS_IDS,
    US_CPI_COMPONENT_SA_KEYS,
    MacroDataError,
    get_bls_relative_importance,
    get_ecos_item_weights,
    get_ecos_series,
)
from views._charts import contribution_bar_chart, pie_chart
from views.macro._shared import load_series

# "CPI 구성 비중 & 기여도" 뷰 전용 — 배타적이고 망라적인 4분할(주거비 등 하위 항목은 근원서비스
# 안에 포함되므로 이 집합에서 제외, valley/macro.py의 US_CPI_COMPONENT_BLS_IDS 주석 참고).
US_CPI_BREAKDOWN_KEYS = ["US_CPI_FOOD", "US_CPI_ENERGY", "US_CPI_CORE_GOODS", "US_CPI_CORE_SERVICES"]


# ⚠️ 가중치는 지표값과 달리 자주 바뀌지 않는다(미국은 매월 소폭 갱신되지만 물가처럼 매시간
# 갱신되는 값이 아니고, 한국은 기준년 개편 시에만 바뀐다) — ttl을 1시간(3600)보다 길게(1일,
# 86400) 잡아 불필요한 재조회를 줄인다. YoY 계열(load_series)은 기존대로 3600 유지.
@st.cache_data(ttl=86400, show_spinner=False)
def _load_bls_weight(bls_series_id: str):
    return get_bls_relative_importance(bls_series_id)


@st.cache_data(ttl=86400, show_spinner=False)
def _load_ecos_weights(stat_code: str):
    return get_ecos_item_weights(stat_code)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_kr_cpi_component(item_code: str):
    return get_ecos_series("901Y009", "M", item_code)


def _render_cpi_breakdown(
    components: list[dict],
    headline_value: float | None,
    *,
    rate_key: str = "yoy",
    rate_label: str = "YoY(%)",
    headline_label: str = "YoY",
    warn_threshold_pct: float | None = None,
    as_of_month: str | None = None,
) -> None:
    """헤드라인 변화율(st.metric) + 구성 비중 원 그래프 + 기여도 막대그래프 + 자기검증(기여도
    합 vs 헤드라인)을 그린다.

    components 각 항목은 이미 "공통으로 존재하는 최신 월" 기준으로 정렬된 {"label",
    "weight_pct", rate_key}이고, 호출부(국가별 분기)가 그 정렬을 책임진다.

    ⚠️ rate_key/rate_label/headline_label/warn_threshold_pct는 MoM 기여도 라운드에서
    추가했다 — 기본값(yoy/YoY(%)/YoY/None)이 기존 YoY 호출부 두 곳과 완전히 동일해 회귀가
    없다. MoM 호출부만 rate_key="mom" 등으로 명시적으로 넘긴다. warn_threshold_pct는 MoM처럼
    변화폭 자체가 작아(0.1~0.4%) 같은 절대 오차라도 상대오차가 커지는 뷰에서만 넘겨(30) 헤드라인
    대비 오차 비율이 크면 추가 경고를 띄운다 — 넘기지 않으면(YoY) 기존과 동일하게 조용하다.

    ⚠️ as_of_month는 헤드라인 표시 라운드(2026-08)에서 추가 — 이전엔 헤드라인 변화율이 맨 아래
    자기검증 캡션에만 작은 글씨로 있어 "이번 달 물가가 몇 % 움직였나"라는 가장 기본적인 숫자가
    파묻혀 있었다. st.metric으로 표 위 최상단에 크게 보여준다(YoY/MoM 공통 적용). 자기검증
    캡션은 그대로 두되(검증 목적은 유지), 헤드라인 값 자체는 여기서 먼저 보여준다.
    """
    with st.container(border=True):
        if headline_value is not None:
            st.metric(f"헤드라인 CPI {headline_label}", f"{headline_value:+.2f}%", help=f"기준월: {as_of_month}" if as_of_month else None)

        contrib_df, total_contribution = compute_cpi_contributions(components, rate_key=rate_key, rate_label=rate_label)
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
                {"가중치(%)": "{:.1f}%", rate_label: "{:+.2f}%", "기여도(%p)": "{:+.2f}%p"}
            ),
            width="stretch",
        )

        if headline_value is None:
            st.warning(f"헤드라인 {headline_label}와 기준월이 겹치지 않아 자기검증(기여도 합 vs 헤드라인)을 생략합니다.")
        else:
            diff = total_contribution - headline_value
            st.caption(
                f"⚠️ 자기검증 — 기여도 합계 {total_contribution:+.2f}%p vs 헤드라인 {headline_label} "
                f"{headline_value:+.2f}% → 차이 {diff:+.2f}%p. (반올림·가중치 기준년 시차로 인한 작은 "
                "차이는 정상이나, 크게 벗어나면 항목 집합/가중치가 잘못됐다는 뜻입니다.)"
            )
            if warn_threshold_pct is not None and headline_value != 0:
                diff_ratio = abs(diff) / abs(headline_value) * 100
                if diff_ratio > warn_threshold_pct:
                    st.warning(
                        f"⚠️ 기여도 합계와 헤드라인의 차이({diff:+.2f}%p)가 헤드라인 크기의 "
                        f"{diff_ratio:.0f}%에 달합니다({warn_threshold_pct:.0f}% 초과) — 이 조합에서는 "
                        "기여도 분해를 참고용으로만 보고, 방향성(플러스/마이너스) 위주로 해석하세요."
                    )


def render(country_code: str) -> None:
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

    # ⚠️ YoY/MoM 전환 — MoM은 "이번 달 물가가 왜 이렇게 나왔나"(증권사 리포트가 발표 당일 보는
    # 형태)를 보여준다. 두 뷰는 조정 방식이 다를 수 있어(미국 SA, 한국 NSA — 아래 각 분기 캡션
    # 참고) 절대 같은 화면에서 섞지 않는다. 기존 YoY 코드 경로는 이 라디오 추가 외에 전혀
    # 손대지 않았다(순수 분기 추가, 회귀 없음).
    cpi_breakdown_mode = st.radio(
        "기여도 기준", ["YoY (전년동월비)", "MoM (전월비)"], horizontal=True, key="macro_cpi_breakdown_mode",
        help="MoM은 계절조정 여부가 나라마다 달라 미국(SA)·한국(NSA) 결과를 서로 비교하면 안 됩니다.",
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
                if cpi_breakdown_mode == "YoY (전년동월비)":
                    yoy_by_label: dict[str, pd.Series] = {}
                    weight_by_label: dict[str, float] = {}
                    weight_as_of = None
                    errors = []
                    for key in US_CPI_BREAKDOWN_KEYS:
                        label = _breakdown_label_by_key.get(key, key)
                        try:
                            weight_pct, weight_as_of = _load_bls_weight(US_CPI_COMPONENT_BLS_IDS[key])
                            yoy = yoy_percent(load_series(key))
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
                                headline_yoy_series = yoy_percent(load_series("US_CPI_NSA"))
                                if common_month in headline_yoy_series.index:
                                    headline_value = float(headline_yoy_series.loc[common_month])
                            except MacroDataError:
                                pass

                            _render_cpi_breakdown(components, headline_value, as_of_month=f"{common_month:%Y-%m}")
                else:  # MoM (전월비) — ⚠️ 계절조정(SA) 계열 사용, valley/macro.py의
                    # US_CPI_COMPONENT_SA_KEYS 주석 참고. 가중치는 위 YoY와 동일하게
                    # US_CPI_COMPONENT_BLS_IDS를 재사용한다(새로 조회하지 않음).
                    mom_by_label: dict[str, pd.Series] = {}
                    weight_by_label: dict[str, float] = {}
                    weight_as_of = None
                    errors = []
                    for nsa_key, sa_key in US_CPI_COMPONENT_SA_KEYS.items():
                        label = _breakdown_label_by_key.get(nsa_key, nsa_key)
                        try:
                            weight_pct, weight_as_of = _load_bls_weight(US_CPI_COMPONENT_BLS_IDS[nsa_key])
                            mom = mom_percent(load_series(sa_key))
                            if mom.empty:
                                raise MacroDataError("MoM을 계산할 만큼 데이터가 충분하지 않습니다.")
                        except MacroDataError as e:
                            errors.append(f"{label}: {e}")
                            continue
                        mom_by_label[label] = mom
                        weight_by_label[label] = weight_pct

                    if errors:
                        st.warning("일부 항목을 제외했습니다: " + " / ".join(errors))

                    if len(mom_by_label) < 2:
                        st.info("표시할 수 있는 구성항목이 부족합니다.")
                    else:
                        aligned = pd.concat(mom_by_label, axis=1, join="inner")
                        if aligned.empty:
                            st.warning("구성항목들 사이에 겹치는 기준월이 없습니다.")
                        else:
                            common_month = aligned.index[-1]
                            latest_row = aligned.loc[common_month]
                            components = [
                                {"label": label, "weight_pct": weight_by_label[label], "mom": latest_row[label]}
                                for label in mom_by_label
                            ]
                            st.caption(
                                f"기준월: {common_month:%Y-%m} (구성항목 공통 최신월) · 가중치 기준: "
                                f"{weight_as_of}(BLS 상대중요도) · ⚠️ 계절조정(SA) 기준 — MoM은 계절성"
                                "(여름 전기요금 등)에 크게 좌우돼 원계열이 아닌 SA로 봅니다."
                            )

                            headline_value = None
                            try:
                                headline_mom_series = mom_percent(load_series("US_CPI"))
                                if common_month in headline_mom_series.index:
                                    headline_value = float(headline_mom_series.loc[common_month])
                            except MacroDataError:
                                pass

                            _render_cpi_breakdown(
                                components, headline_value,
                                rate_key="mom", rate_label="MoM(%)", headline_label="MoM",
                                warn_threshold_pct=30.0, as_of_month=f"{common_month:%Y-%m}",
                            )
                            st.caption(
                                "⚠️ 기여도는 근사치입니다 — 합계와 헤드라인의 차이는 가중치 시차· "
                                "반올림에서 발생합니다. 미국(SA)과 한국(NSA)은 조정 방식이 달라 두 "
                                "나라의 MoM 기여도를 직접 비교하지 마세요."
                            )
    else:
        if st.button("구성 비중 & 기여도 불러오기", key="btn_cpi_breakdown"):
            st.session_state.macro_cpi_breakdown_loaded = True

        if st.session_state.get("macro_cpi_breakdown_loaded"):
            try:
                weights = _load_ecos_weights("901Y009")
            except MacroDataError as e:
                st.warning(f"가중치 조회에 실패했습니다: {e}")
            else:
                if cpi_breakdown_mode == "YoY (전년동월비)":
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
                                headline_yoy_series = yoy_percent(load_series("KR_CPI"))
                                if common_month in headline_yoy_series.index:
                                    headline_value = float(headline_yoy_series.loc[common_month])
                            except MacroDataError:
                                pass

                            _render_cpi_breakdown(components, headline_value, as_of_month=f"{common_month:%Y-%m}")
                else:  # MoM (전월비) — ⚠️ ECOS에 CPI 계절조정(SA) 계열이 없어(실측 확인, STEP 0
                    # 보고 참고) 원계열(NSA)을 그대로 쓴다. 위 YoY와 같은 컴포넌트 시리즈·가중치를
                    # 재사용하고 mom_percent()만 다르게 적용한다(새 데이터 소스 없음).
                    mom_by_label: dict[str, pd.Series] = {}
                    weight_by_label: dict[str, float] = {}
                    errors = []
                    for code in KR_CPI_MAJOR_ITEM_CODES:
                        if code not in weights:
                            errors.append(f"{code}: 가중치 메타데이터에 없음")
                            continue
                        name, weight_permille = weights[code]
                        try:
                            mom = mom_percent(_load_kr_cpi_component(code))
                            if mom.empty:
                                raise MacroDataError("MoM을 계산할 만큼 데이터가 충분하지 않습니다.")
                        except MacroDataError as e:
                            errors.append(f"{name}: {e}")
                            continue
                        mom_by_label[name] = mom
                        weight_by_label[name] = weight_permille / 10

                    if errors:
                        st.warning("일부 항목을 제외했습니다: " + " / ".join(errors))

                    if len(mom_by_label) < 2:
                        st.info("표시할 수 있는 구성항목이 부족합니다.")
                    else:
                        aligned = pd.concat(mom_by_label, axis=1, join="inner")
                        if aligned.empty:
                            st.warning("구성항목들 사이에 겹치는 기준월이 없습니다.")
                        else:
                            common_month = aligned.index[-1]
                            latest_row = aligned.loc[common_month]
                            components = [
                                {"label": label, "weight_pct": weight_by_label[label], "mom": latest_row[label]}
                                for label in mom_by_label
                            ]
                            st.caption(
                                f"기준월: {common_month:%Y-%m} (구성항목 공통 최신월) · 가중치 출처: "
                                "ECOS StatisticItemList(901Y009) 실시간 메타데이터 · ⚠️ 원계열(NSA) "
                                "기준입니다 — ECOS에 CPI 계절조정(SA) 계열이 없어(실측 확인) 계절 "
                                "패턴이 그대로 섞여 있으니 해석에 주의하세요."
                            )

                            headline_value = None
                            try:
                                headline_mom_series = mom_percent(load_series("KR_CPI"))
                                if common_month in headline_mom_series.index:
                                    headline_value = float(headline_mom_series.loc[common_month])
                            except MacroDataError:
                                pass

                            _render_cpi_breakdown(
                                components, headline_value,
                                rate_key="mom", rate_label="MoM(%)", headline_label="MoM",
                                warn_threshold_pct=30.0, as_of_month=f"{common_month:%Y-%m}",
                            )
                            st.caption(
                                "⚠️ 기여도는 근사치입니다 — 합계와 헤드라인의 차이는 가중치 시차· "
                                "반올림에서 발생합니다. 미국(SA)과 한국(NSA)은 조정 방식이 달라 두 "
                                "나라의 MoM 기여도를 직접 비교하지 마세요."
                            )
