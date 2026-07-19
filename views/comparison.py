"""Comparison — 여러 종목의 밸류에이션과 성장/수익성 추이를 나란히 비교."""

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from valley.data import TickerNotFoundError
from valley.peers import build_valuation_table
from views._cache import get_financial_history, get_valuation_snapshot, resolve_ticker

load_dotenv(override=True)

st.title("⚖️ Comparison")
st.caption("최대 4개 종목의 밸류에이션과 성장률/수익성 추이를 비교합니다 · 정보 제공 목적이며 투자자문이 아닙니다.")

MAX_TICKERS = 4

tickers_input = st.text_input("비교할 티커 (쉼표로 구분, 최대 4개, 예: AAPL, MSFT, 005930)", value="AAPL, MSFT, 005930")
run = st.button("비교하기", type="primary")

if not run:
    st.info("티커를 입력하고 '비교하기'를 눌러주세요.")
    st.stop()

raw_tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
unique_tickers = list(dict.fromkeys(raw_tickers))[:MAX_TICKERS]

if not unique_tickers:
    st.error("티커를 입력해주세요.")
    st.stop()

valuation_rows, growth_series, margin_series, failed, failed_detail = [], {}, {}, [], []
for raw in unique_tickers:
    try:
        resolved = resolve_ticker(raw)
        valuation_rows.append(get_valuation_snapshot(resolved))
        history = get_financial_history(resolved)
        growth_series[resolved] = history["매출"].pct_change() * 100
        margin_series[resolved] = history["순이익률"] * 100
    except TickerNotFoundError as e:
        failed.append(raw)
        failed_detail.append(f"{raw}: {e}")
    except Exception as e:
        failed.append(raw)
        failed_detail.append(f"{raw}: {type(e).__name__} - {e}")

if failed:
    st.warning(f"{len(failed)}개 종목은 데이터를 가져오지 못해 제외되었습니다: {', '.join(failed)}")
    with st.expander("실패 상세"):
        for line in failed_detail:
            st.text(line)

if not valuation_rows:
    st.stop()

st.subheader("밸류에이션 요약")
st.dataframe(build_valuation_table(valuation_rows), width="stretch", hide_index=True)

st.subheader("매출 성장률 비교 (YoY %)")
st.caption("통화가 다른 종목을 절대금액으로 겹쳐 그리면 스케일이 왜곡되어, 통화에 무관한 성장률(%)로 비교합니다.")
growth_df = pd.DataFrame(growth_series)
if growth_df.dropna(how="all").empty:
    st.info("계산 가능한 성장률 데이터가 없습니다.")
else:
    st.line_chart(growth_df)

st.subheader("순이익률 비교 (%)")
margin_df = pd.DataFrame(margin_series)
if margin_df.dropna(how="all").empty:
    st.info("계산 가능한 수익성 데이터가 없습니다.")
else:
    st.line_chart(margin_df)
