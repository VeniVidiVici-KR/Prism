"""Macro Indicator — 미국/한국 매크로 지표(물가·금리·고용·성장·통화) 조회 페이지.

⚠️ 파일 분할 라운드(2026-08) — 섹션별 렌더 로직은 views/macro/ 아래로 옮겼다. 이 파일은
공통 골격(제목·국가 선택·세션 초기화)과 섹션 조립만 담당한다. 각 섹션 모듈의 render()는
country_code 하나만 받고, 내부에서 국가별로 보여줄지 말지·무엇을 보여줄지 스스로 판단한다
(암묵적 전역 의존 없이 인자 하나로 동작이 결정됨).
"""

import os

import streamlit as st
from dotenv import load_dotenv

from views.macro import (
    catalog,
    cpi_components,
    cpi_contribution,
    credit_bonds,
    debt_gdp,
    employment,
    market_flows,
    mixed_indicators,
    overlay,
    regional_fed,
    underlying_inflation,
    yield_curve,
)
from views._style import inject_navy_headings

load_dotenv(override=True)
inject_navy_headings()

st.title("Macro Indicator")
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
    st.session_state.macro_cpi_midlevel_loaded = False
    st.session_state.macro_regional_fed_loaded = False
    st.session_state.macro_employment_report_loaded = False
    st.session_state.macro_credit_spread_loaded = False
    st.session_state.macro_bond_issuance_loaded = False
    st.session_state.macro_bond_ticker_result = None
    st.session_state.macro_kofia_loaded = False
    st.session_state.macro_underlying_inflation_loaded = False
    st.session_state.macro_yield_curve_loaded = False

if country_code == "KR" and not os.environ.get("ECOS_API_KEY"):
    st.info(
        "ECOS_API_KEY가 설정되어 있지 않아 한국 CPI가 FRED의 미러링 데이터(2023-11 이후 갱신 "
        "중단)로 표시되고, 한국 M2는 대응하는 FRED 계열이 없어(2017-05 이후 멈춘 미러라 폴백을 "
        "없앴습니다) 조회에 실패합니다. 최신 데이터를 보려면 https://ecos.bok.or.kr/api/ 에서 "
        "API 키를 발급받아 .env에 추가하세요."
    )

# ⚠️ 섹션 순서는 분할 전 원래 순서를 그대로 유지한다(임의 변경 없음).
catalog.render(country_code)
mixed_indicators.render(country_code)
regional_fed.render(country_code)
employment.render(country_code)
credit_bonds.render(country_code)
underlying_inflation.render(country_code)
cpi_components.render(country_code)
cpi_contribution.render(country_code)
market_flows.render(country_code)
yield_curve.render(country_code)
debt_gdp.render(country_code)
overlay.render(country_code)
