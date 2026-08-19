"""고용보고서 한눈에 보기 + 비농업고용 업종별 분해 — 미국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.composite import mom_change, mom_percent, yoy_percent
from valley.macro import INDICATORS, MacroDataError
from views._charts import contribution_bar_chart
from views.macro._shared import load_series

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


def render(country_code: str) -> None:
    if country_code != "US":
        return

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
        with st.container(border=True):
            headline_rows = []
            headline_failed = []

            for key, source in EMPLOYMENT_HEADLINE_ITEMS:
                label = _employment_label_by_key.get(key, key)
                try:
                    s = load_series(key)
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
                wage_raw = load_series("US_AVG_HOURLY_EARNINGS")
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

        with st.container(border=True):
            sector_rows = []
            sector_failed = []
            sector_total = 0.0
            for key in NFP_SECTOR_KEYS:
                label = _employment_label_by_key.get(key, key)
                try:
                    s = load_series(key)
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
                    payems_changes = mom_change(load_series("US_PAYEMS"))
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
                        uspriv_changes = mom_change(load_series("US_PRIV_PAYEMS"))
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
