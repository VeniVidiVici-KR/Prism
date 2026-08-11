"""views/ 전용 st.cache_data 래퍼.

valley/ 계층은 Streamlit 의존성이 없는 순수 조회/계산 레이어로 유지하고,
캐싱은 이 모듈에서만 담당한다. 여러 페이지가 같은 티커를 반복 조회하는 경우
(예: "조회하기" 이후 다른 버튼 클릭 시 스크립트 전체 재실행) 불필요한 네트워크
요청을 막기 위함이다.
"""

import streamlit as st

from valley.data import get_company_snapshot as _get_company_snapshot
from valley.data import get_fcff_inputs as _get_fcff_inputs
from valley.data import get_financial_history as _get_financial_history
from valley.data import get_free_cash_flow_history as _get_free_cash_flow_history
from valley.data import get_latest_free_cash_flow as _get_latest_free_cash_flow
from valley.data import get_price_history as _get_price_history
from valley.data import get_quarterly_financial_history as _get_quarterly_financial_history
from valley.data import resolve_ticker as _resolve_ticker
from valley.dart import get_corp_code as _get_corp_code
from valley.dart import get_dart_disclosure_list as _get_dart_disclosure_list
from valley.dart import get_dart_financial_statements as _get_dart_financial_statements
from valley.edgar import get_8k_filing_text as _get_8k_filing_text
from valley.edgar import get_cik as _get_cik
from valley.edgar import get_filings_by_type as _get_filings_by_type
from valley.edgar import get_mda_section_text as _get_mda_section_text
from valley.edgar import get_recent_filings as _get_recent_filings
from valley.company_summary import summarize_company_overview as _summarize_company_overview
from valley.edgar_summary import summarize_8k_filing as _summarize_8k_filing
from valley.edgar_summary import summarize_mda_section as _summarize_mda_section
from valley.kofia import get_credit_balance_history as _get_credit_balance_history
from valley.kofia import get_securities_market_capital_history as _get_securities_market_capital_history
from valley.krx import get_kr_ticker_directory as _get_kr_ticker_directory
from valley.market_snapshot import get_market_snapshot as _get_market_snapshot
from valley.market_snapshot import get_us_market_state as _get_us_market_state
from valley.peers import get_annual_dividend_history as _get_annual_dividend_history
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
def get_fcff_inputs(ticker: str) -> dict:
    return _get_fcff_inputs(ticker)


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


@st.cache_data(ttl=3600, show_spinner=False)
def get_annual_dividend_history(ticker: str, years: int = 5):
    return _get_annual_dividend_history(ticker, years)


@st.cache_data(ttl=300, show_spinner=False)
def get_market_snapshot() -> list[dict]:
    """⚠️ ttl=300(5분) — 이 프로젝트 다른 캐시들(1시간·1일)보다 훨씬 짧다. 페이지 진입만으로
    자동 조회되는 유일한 예외라(다른 모든 조회는 버튼 클릭 시에만), 신선도와 호출 부담을
    절충하는 값으로 짧게 잡았다 — 매크로 지표(86400)와 혼동해 늘리지 않는다."""
    return _get_market_snapshot()


@st.cache_data(ttl=300, show_spinner=False)
def get_us_market_state() -> str | None:
    """market_snapshot과 같은 ttl(300)로 맞춘다 — 장 상태도 시세만큼 자주 바뀔 수 있다."""
    return _get_us_market_state()


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


@st.cache_data(ttl=3600, show_spinner=False)
def get_filings_by_type(cik: str, form_type: str, limit: int = 10) -> list[dict]:
    """form_type이 캐시 키에 포함되어 유형별로 캐시가 분리된다(같은 CIK라도 10-K 조회와
    10-Q 조회는 별개로 캐시됨). ttl은 get_recent_filings와 동일하게 맞춘다."""
    return _get_filings_by_type(cik, form_type, limit)


@st.cache_data(ttl=86400, show_spinner=False)
def get_8k_filing_text(cik: str, accession_number: str) -> dict:
    """이미 접수된 공시의 원문은 사실상 불변이라(DART 재무제표 캐싱과 같은 이유) ttl을
    길게 잡는다. accession_number가 캐시 키에 포함돼 공시별로 분리된다."""
    return _get_8k_filing_text(cik, accession_number)


@st.cache_data(ttl=86400, show_spinner=False)
def summarize_8k_filing(
    company_name: str, form: str, filing_date: str, text: str, truncated: bool, engine: str | None = None
) -> str:
    """⚠️ 비용 통제 핵심 지점 — 이 함수가 실제 OpenAI 호환 API 호출이다. text(원문 전체)와
    engine이 모두 캐시 키에 포함되므로, 같은 공시(같은 원문)를 같은 엔진으로 두 번 요청하면
    API를 다시 부르지 않고 캐시된 요약을 그대로 반환한다.

    ⚠️ engine을 캐시 키에서 빠뜨리면 학교 게이트웨이로 요약한 뒤 Gemini로 바꿔 같은 공시를
    다시 요약해도 이전(학교) 결과가 그대로 반환되는 사고가 난다 — 반드시 인자로 받아 캐시
    키에 자연히 포함되게 한다(별도 처리 불필요, st.cache_data는 함수 인자 전체를 키로 씀).

    버튼 클릭 시에만 호출되도록 하는 건 UI(views/stock_research.py) 쪽 책임이고, 이 캐싱은
    그 위에 얹는 이중 방어(같은 세션 안 재실행·재클릭 시 재호출 방지)다."""
    return _summarize_8k_filing(company_name, form, filing_date, text, truncated, engine)


@st.cache_data(ttl=86400, show_spinner=False)
def get_mda_section_text(cik: str, accession_number: str, form_type: str) -> dict:
    """10-K/10-Q의 MD&A 섹션 추출(SEC 문서 조회 + 파싱)은 LLM 호출이 아니라 무료지만, 같은
    원문을 반복 파싱하지 않도록 get_8k_filing_text와 동일한 기준(ttl 길게)으로 캐싱한다.
    "요약 전 크기 확인" 흐름에서 추출 버튼을 다시 눌러도 재파싱하지 않는다."""
    return _get_mda_section_text(cik, accession_number, form_type)


@st.cache_data(ttl=86400, show_spinner=False)
def summarize_company_overview(company_name: str, business_summary: str, engine: str | None = None) -> str:
    """⚠️ 비용 통제 지점 — summarize_8k_filing과 동일한 이유로 business_summary(원문)와
    engine이 모두 캐시 키에 포함된다. business_summary는 같은 티커라면 재조회해도 내용이
    거의 불변이라(회사 사업 서술은 자주 안 바뀜) company_name+business_summary가 사실상
    ticker 역할을 한다 — 같은 종목·같은 엔진으로 재클릭해도 API를 다시 부르지 않는다."""
    return _summarize_company_overview(company_name, business_summary, engine)


@st.cache_data(ttl=43200, show_spinner=False)
def get_credit_balance_history(num_rows: int = 1500) -> list[dict]:
    """일별 데이터라 ttl을 하루(86400)보다는 짧게, 그렇다고 시세처럼 자주 바뀌는 값도
    아니라(실측 결과 KOFIA API 자체가 며칠 지연되어 갱신된다) ttl=3600처럼 짧게 잡을 이유도
    없다 — 반나절(43200)로 절충해 같은 날 재클릭·재실행 시 불필요한 재호출을 막는다."""
    return _get_credit_balance_history(num_rows)


@st.cache_data(ttl=43200, show_spinner=False)
def get_securities_market_capital_history(num_rows: int = 1500) -> list[dict]:
    """get_credit_balance_history와 동일한 ttl 기준(일별 데이터, 며칠 지연 갱신)."""
    return _get_securities_market_capital_history(num_rows)


@st.cache_data(ttl=86400, show_spinner=False)
def summarize_mda_section(
    company_name: str, form: str, filing_date: str, text: str, truncated: bool, engine: str | None = None
) -> str:
    """⚠️ 비용 통제 핵심 지점(10-K/10-Q용) — summarize_8k_filing과 동일한 이유로 text와 engine을
    전부 캐시 키에 포함한다. MD&A 텍스트가 8-K보다 훨씬 커서(수만자) 캐시 히트의 가치가 더
    크다 — 실수로 같은 문서를 재요약하면 8-K보다 몇 배 큰 비용이 든다."""
    return _summarize_mda_section(company_name, form, filing_date, text, truncated, engine)
