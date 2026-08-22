"""주가 추이 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음)."""

import streamlit as st

from valley.data import TickerNotFoundError
from views._cache import get_price_history

PRICE_PERIOD_LABELS = {
    "1mo": "1개월", "3mo": "3개월", "6mo": "6개월",
    "1y": "1년", "3y": "3년", "5y": "5년", "max": "전체",
}


def render(resolved_ticker: str, currency: str) -> None:
    st.subheader("주가 추이")
    price_period = st.radio(
        "기간", list(PRICE_PERIOD_LABELS.values()), index=3, horizontal=True, label_visibility="collapsed"
    )
    selected_period = next(k for k, v in PRICE_PERIOD_LABELS.items() if v == price_period)
    try:
        price_history = get_price_history(resolved_ticker, selected_period)
    except (TickerNotFoundError, ValueError) as e:
        st.warning(str(e))
    else:
        with st.container(border=True):
            st.caption(f"단위: {currency} · 배당/분할 조정 종가")
            st.line_chart(price_history)
