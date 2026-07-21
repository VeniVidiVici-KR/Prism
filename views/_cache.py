"""views/ 전용 st.cache_data 래퍼.

valley/ 계층은 Streamlit 의존성이 없는 순수 조회/계산 레이어로 유지하고,
캐싱은 이 모듈에서만 담당한다. 여러 페이지가 같은 티커를 반복 조회하는 경우
(예: "조회하기" 이후 다른 버튼 클릭 시 스크립트 전체 재실행) 불필요한 네트워크
요청을 막기 위함이다.
"""

import streamlit as st

from valley.data import get_company_snapshot as _get_company_snapshot
from valley.data import get_financial_history as _get_financial_history
from valley.data import get_free_cash_flow_history as _get_free_cash_flow_history
from valley.data import get_latest_free_cash_flow as _get_latest_free_cash_flow
from valley.data import get_price_history as _get_price_history
from valley.data import get_quarterly_financial_history as _get_quarterly_financial_history
from valley.data import resolve_ticker as _resolve_ticker
from valley.dart import get_corp_code as _get_corp_code
from valley.dart import get_dart_disclosure_list as _get_dart_disclosure_list
from valley.dart import get_dart_financial_statements as _get_dart_financial_statements
from valley.edgar import get_cik as _get_cik
from valley.edgar import get_recent_filings as _get_recent_filings
from valley.krx import get_kr_ticker_directory as _get_kr_ticker_directory
from valley.peers import get_historical_per_pbr as _get_historical_per_pbr
from valley.peers import get_peer_tickers as _get_peer_tickers
from valley.peers import get_roe_history as _get_roe_history
from valley.peers import get_valuation_snapshot as _get_valuation_snapshot
from valley.supply import get_investor_net_trading as _get_investor_net_trading


@st.cache_data(ttl=None, show_spinner=False)
def resolve_ticker(user_input: str) -> str:
    """종목코드 -> 심볼 매핑은 사실상 불변이라 만료 없이 캐싱한다."""
    return _resolve_ticker(user_input)


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_snapshot(ticker: str) -> dict:
    return _get_company_snapshot(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_financial_history(ticker: str):
    return _get_financial_history(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_quarterly_financial_history(ticker: str):
    return _get_quarterly_financial_history(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_price_history(ticker: str, period: str = "1y"):
    return _get_price_history(ticker, period)


@st.cache_data(ttl=3600, show_spinner=False)
def get_latest_free_cash_flow(ticker: str) -> float:
    return _get_latest_free_cash_flow(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_free_cash_flow_history(ticker: str, years: int = 4):
    return _get_free_cash_flow_history(ticker, years)


@st.cache_data(ttl=3600, show_spinner=False)
def get_valuation_snapshot(ticker: str) -> dict:
    return _get_valuation_snapshot(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_peer_tickers(ticker: str, limit: int = 5):
    return _get_peer_tickers(ticker, limit)


@st.cache_data(ttl=3600, show_spinner=False)
def get_historical_per_pbr(ticker: str, years: int = 5):
    return _get_historical_per_pbr(ticker, years)


@st.cache_data(ttl=3600, show_spinner=False)
def get_roe_history(ticker: str, years: int = 3):
    return _get_roe_history(ticker, years)


@st.cache_data(ttl=86400, show_spinner=False)
def get_kr_ticker_directory():
    """코스피/코스닥 전 종목 코드-이름 매핑. 하루 단위로도 거의 안 바뀌어 ttl을 길게 잡는다."""
    return _get_kr_ticker_directory()


@st.cache_data(ttl=86400, show_spinner=False)
def get_corp_code(stock_code: str) -> str:
    """DART corp_code 매핑도 거의 안 바뀌므로 ttl을 길게 잡는다 (valley/dart.py에 로컬 파일 캐시도 있음)."""
    return _get_corp_code(stock_code)


@st.cache_data(ttl=86400, show_spinner=False)
def get_dart_financial_statements(corp_code: str, bsns_year: int | None = None) -> dict:
    """DART 사업보고서는 확정 공시라 사실상 불변 — ttl을 길게 잡는다."""
    return _get_dart_financial_statements(corp_code, bsns_year)


@st.cache_data(ttl=3600, show_spinner=False)
def get_dart_disclosure_list(corp_code: str, months: int = 6, page_count: int = 30) -> list[dict]:
    """공시 목록은 신규 공시가 계속 생길 수 있어 재무제표보다 ttl을 짧게 잡는다."""
    return _get_dart_disclosure_list(corp_code, months=months, page_count=page_count)


@st.cache_data(ttl=3600, show_spinner=False)
def get_investor_net_trading(stock_code: str, days: int = 20):
    """당일 수급은 장중에도 갱신될 수 있어 다른 yfinance 조회들과 같은 ttl(1시간)로 맞춘다."""
    return _get_investor_net_trading(stock_code, days)


@st.cache_data(ttl=86400, show_spinner=False)
def get_cik(ticker: str) -> str:
    """티커-CIK 매핑도 DART corp_code처럼 거의 안 바뀌므로 ttl을 길게 잡는다(valley/edgar.py에
    로컬 파일 캐시도 있음)."""
    return _get_cik(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def get_recent_filings(cik: str, limit: int = 30) -> list[dict]:
    """공시 목록은 신규 공시가 계속 생길 수 있어 티커-CIK 매핑보다 ttl을 짧게 잡는다
    (DART 공시 목록 캐싱과 동일한 기준)."""
    return _get_recent_filings(cik, limit)
