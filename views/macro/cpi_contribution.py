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
    US_CPI_MIDLEVEL_COMPONENTS,
    US_CPI_MIDLEVEL_UNAVAILABLE,
    MacroDataError,
    get_bls_relative_importance,
    get_bls_series_batch,
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


# ⚠️ 중분류 배치 조회 (중분류 라운드, 2026-09) — 19개 시리즈를 한 요청으로 받는다. 값과
# 가중치(NSA aspects)를 같은 응답에서 함께 뽑으므로 뷰당 호출은 1회다(SA 뷰도 1회).
# st.cache_data는 인자가 해시 가능해야 해서 list가 아니라 tuple로 받는다.
@st.cache_data(ttl=3600, show_spinner=False)
def _load_bls_batch(series_ids: tuple[str, ...], with_aspects: bool):
    return get_bls_series_batch(list(series_ids), years=3, with_aspects=with_aspects)


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


def _render_midlevel_section(*, is_mom: bool, common_month) -> None:
    """중분류 분해 섹션의 껍데기 — 안내 + 버튼 게이팅 + 카드. 실제 렌더는 아래 함수가 한다.

    ⚠️ 비용 통제: 중분류는 BLS 배치 호출이 (YoY 1회 / MoM 2회) 나가므로 4분할 표가 뜰 때
    자동으로 딸려오지 않게 별도 버튼으로 막는다(다른 조회 섹션과 같은 원칙).
    """
    st.markdown("**중분류(2단계)로 더 쪼개보기**")
    st.caption(
        "위 4개 대분류를 각각의 중분류로 한 단계 더 분해합니다 — 예를 들어 '식품'이 "
        "가정식·외식 중 어느 쪽 때문에 움직였는지까지 봅니다(에너지 상품=휘발유·연료유, "
        "에너지 서비스=전기·도시가스). ⚠️ 단계마다 '자식 합 vs 부모' 검증을 함께 표시하니, "
        "차이가 크면 항목이 빠졌거나 중복된 것으로 보세요. ⚠️ 3단계 이하(자가주거비 등)는 "
        "아직 지원하지 않습니다."
    )
    if st.button("중분류까지 분해하기", key="btn_cpi_midlevel"):
        st.session_state.macro_cpi_midlevel_loaded = True

    if not st.session_state.get("macro_cpi_midlevel_loaded"):
        return

    with st.container(border=True):
        _render_midlevel_breakdown(is_mom=is_mom, common_month=common_month)


def _render_midlevel_breakdown(*, is_mom: bool, common_month) -> None:
    """대분류 4개를 각각의 중분류(2단계)로 한 단계 더 쪼개 보여준다 (중분류 라운드, 2026-09).

    ⚠️ 기존 4분할 뷰는 전혀 건드리지 않는다 — 이 함수는 그 아래에 별도 버튼으로 붙는 순수
    추가다. 기준월(common_month)은 위 4분할 표가 쓴 것을 그대로 받아서 쓴다 — 계층 검증(자식
    합 vs 부모)은 같은 달끼리 비교해야만 의미가 있어서, 중분류가 그 달을 갖고 있지 않으면 그
    항목만 빼고 이유를 표시한다.

    ⚠️ 표현 방식(판단 근거): 15개 넘는 항목을 한 표·한 막대그래프에 넣으면 읽기 어렵고 좁은
    화면에서 열이 잘린다. 그래서 (1) 부모별 st.expander로 나눠 한 번에 2~8개만 보이게 하고,
    (2) 표의 열 구성을 기존 4분할 표와 완전히 동일하게(항목/가중치/변화율/기여도 4열) 유지해
    열 추가로 인한 잘림 위험을 만들지 않는다. 계층은 열이 아니라 expander 제목(부모)으로
    표현한다.
    """
    rate_key = "mom" if is_mom else "yoy"
    rate_label = "MoM(%)" if is_mom else "YoY(%)"
    label_by_key = {
        ind["key"]: ind["label"] for indicators in INDICATORS["US"].values() for ind in indicators
    }

    id_field = "sa_id" if is_mom else "nsa_id"
    fetch_ids = tuple(
        c[id_field] for c in US_CPI_MIDLEVEL_COMPONENTS.values() if c[id_field] is not None
    )
    # ⚠️ 가중치는 항상 NSA(CUUR) 시리즈의 aspects에서만 온다(실측: SA에는 RI가 없다) — MoM
    # 뷰에서도 NSA 배치를 한 번 더 받아 가중치만 쓰고 값은 SA 배치에서 쓴다(대분류 MoM 뷰가
    # 이미 NSA 가중치를 재사용하는 것과 동일한 원칙).
    weight_ids = tuple(c["nsa_id"] for c in US_CPI_MIDLEVEL_COMPONENTS.values())
    try:
        value_batch = _load_bls_batch(fetch_ids, is_mom is False)
        weight_batch = value_batch if not is_mom else _load_bls_batch(weight_ids, True)
    except MacroDataError as e:
        st.warning(f"중분류 조회에 실패했습니다: {e}")
        return

    # BLS 계층엔 있지만 값이 없어 못 넣는 항목 — 아래 검증에서 "자식 합이 부모보다 모자란"
    # 이유가 되므로 화면에 먼저 밝힌다.
    excluded: list[str] = [
        f"{name}(가중치 약 {info['weight_pct']:.2f}%, {info['reason']})"
        for name, info in US_CPI_MIDLEVEL_UNAVAILABLE.items()
    ]
    by_parent: dict[str, list[dict]] = {k: [] for k in US_CPI_BREAKDOWN_KEYS}
    for child_key, meta in US_CPI_MIDLEVEL_COMPONENTS.items():
        series_id = meta[id_field]
        if series_id is None:
            excluded.append(f"{meta['label']}(계절조정 시리즈 미제공)")
            continue
        entry = value_batch.get(series_id)
        weight_entry = weight_batch.get(meta["nsa_id"])
        if entry is None or weight_entry is None or weight_entry["relative_importance"] is None:
            excluded.append(f"{meta['label']}(데이터·가중치 없음)")
            continue
        rates = mom_percent(entry["series"]) if is_mom else yoy_percent(entry["series"])
        if common_month not in rates.index:
            excluded.append(f"{meta['label']}(기준월 {common_month:%Y-%m} 값 없음)")
            continue
        by_parent[meta["parent"]].append(
            {
                "label": meta["label"],
                "weight_pct": weight_entry["relative_importance"],
                rate_key: float(rates.loc[common_month]),
            }
        )

    if excluded:
        st.caption("⚠️ 이 뷰에서 제외된 중분류: " + " / ".join(excluded))

    for parent_key in US_CPI_BREAKDOWN_KEYS:
        children = by_parent[parent_key]
        parent_label = label_by_key.get(parent_key, parent_key)
        if not children:
            st.caption(f"{parent_label}: 표시할 수 있는 중분류가 없습니다.")
            continue

        # 부모 값은 위 4분할 뷰와 완전히 같은 소스·같은 캐시를 쓴다(추가 호출 없음) — 그래야
        # "자식 합 vs 부모" 검증이 화면에 이미 보이는 숫자와 일관된다.
        try:
            parent_weight, _ = _load_bls_weight(US_CPI_COMPONENT_BLS_IDS[parent_key])
            parent_source_key = US_CPI_COMPONENT_SA_KEYS[parent_key] if is_mom else parent_key
            parent_rates = (
                mom_percent(load_series(parent_source_key))
                if is_mom
                else yoy_percent(load_series(parent_source_key))
            )
        except MacroDataError as e:
            st.caption(f"{parent_label}: 부모 값을 불러오지 못해 검증을 생략합니다 ({e}).")
            continue
        if common_month not in parent_rates.index:
            st.caption(f"{parent_label}: 부모의 {common_month:%Y-%m} 값이 없어 검증을 생략합니다.")
            continue

        parent_rate = float(parent_rates.loc[common_month])
        parent_contribution = parent_weight / 100 * parent_rate
        child_df, child_total = compute_cpi_contributions(children, rate_key=rate_key, rate_label=rate_label)
        child_weight_sum = float(child_df["가중치(%)"].sum())

        with st.expander(
            f"{parent_label} — 가중치 {parent_weight:.1f}% · {rate_label[:3]} {parent_rate:+.2f}% · "
            f"기여도 {parent_contribution:+.2f}%p (중분류 {len(children)}개)"
        ):
            st.dataframe(
                child_df.set_index("항목").style.format(
                    {"가중치(%)": "{:.2f}%", rate_label: "{:+.2f}%", "기여도(%p)": "{:+.3f}%p"}
                ),
                width="stretch",
            )
            # ⚠️ 항목 수가 2~8개로 부모마다 달라, 기본 높이(320)를 그대로 쓰면 2개짜리
            # (식품·에너지)는 막대가 화면을 가득 채워 우스꽝스러워진다 — 항목당 높이로 잡되
            # 최소치를 둔다(차트 종류·색상은 그대로, 높이만 조정).
            contribution_bar_chart(
                child_df, "항목", "기여도(%p)", height=max(140, 40 * len(children))
            )
            # ⚠️ 계층 검증 — 자식 합이 부모와 어긋나면 항목이 빠졌거나 중복된 것이다. 억지로
            # 맞추지 않고 차이를 그대로 드러낸다(기존 4분할 자기검증과 같은 원칙).
            st.caption(
                f"⚠️ 계층 검증(가중치) — 중분류 합 {child_weight_sum:.2f}% vs {parent_label} "
                f"{parent_weight:.2f}% → 차이 {child_weight_sum - parent_weight:+.2f}%p"
            )
            st.caption(
                f"⚠️ 계층 검증(기여도) — 중분류 합 {child_total:+.3f}%p vs {parent_label} "
                f"{parent_contribution:+.3f}%p → 차이 {child_total - parent_contribution:+.3f}%p "
                "(가중치는 이번 달 기준인데 변화율은 과거와 비교한 값이라, 지수 합성 방식 차이로 "
                "작은 오차는 정상입니다.)"
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
                            _render_midlevel_section(is_mom=False, common_month=common_month)
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
                            _render_midlevel_section(is_mom=True, common_month=common_month)
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
