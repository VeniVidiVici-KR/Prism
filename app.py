"""PRISM 엔트리포인트 — 페이지 라우팅만 담당."""

import streamlit as st

st.set_page_config(page_title="PRISM", page_icon="📈", layout="wide")

stock_research = st.Page("views/stock_research.py", title="Stock Research", icon="📈", default=True)
screener = st.Page("views/screener.py", title="Screener", icon="🔍")
comparison = st.Page("views/comparison.py", title="Comparison", icon="⚖️")
macro_indicator = st.Page("views/macro_indicator.py", title="Macro Indicator", icon="🌐")
data_health = st.Page("views/data_health.py", title="Data Health", icon="🩺")

pg = st.navigation([stock_research, screener, comparison, macro_indicator, data_health])
pg.run()
