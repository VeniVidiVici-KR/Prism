"""yfinance를 감싸는 데이터 조회 레이어. UI(app.py)는 이 모듈의 함수만 호출한다."""

import pandas as pd
import yfinance as yf


class TickerNotFoundError(Exception):
    """존재하지 않거나 데이터가 없는 티커를 조회했을 때 발생."""


def find_row(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """yfinance 버전에 따라 행 이름이 달라질 수 있어 후보군 중 첫 매치를 반환."""
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def resolve_ticker(user_input: str) -> str:
    """사용자 입력을 실제 yfinance 심볼로 변환한다.

    6자리 숫자는 한국 종목코드로 간주해 코스피(.KS)를 먼저 시도하고,
    유효하지 않으면 코스닥(.KQ)으로 재시도한다. 그 외 입력(미국 티커 등)은 그대로 사용한다.
    """
    code = user_input.strip().upper()
    if code.isdigit() and len(code) == 6:
        for suffix in (".KS", ".KQ"):
            candidate = code + suffix
            info = yf.Ticker(candidate).info
            if info.get("longName") and info.get("quoteType") == "EQUITY":
                return candidate
        raise TickerNotFoundError(f"'{code}' 종목코드를 KOSPI/KOSDAQ에서 찾을 수 없습니다.")
    return code


def build_snapshot(ticker: str, info: dict) -> dict:
    """이미 받아온 info dict로부터 스냅샷을 만든다.

    peers.py처럼 같은 Ticker의 info를 다른 목적으로도 써야 하는 호출부가 있어,
    yf.Ticker(...).info를 중복 조회하지 않도록 조회와 가공을 분리했다.
    """
    name = info.get("longName") or info.get("shortName")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not name or price is None:
        raise TickerNotFoundError(f"'{ticker}' 티커에 대한 데이터를 찾을 수 없습니다.")

    return {
        "ticker": ticker.upper(),
        "name": name,
        "price": price,
        "currency": info.get("currency", "USD"),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "market_cap": info.get("marketCap"),
    }


def get_company_snapshot(ticker: str) -> dict:
    """회사 기본정보(이름/현재가/섹터/발행주식수 등)를 반환한다."""
    return build_snapshot(ticker, yf.Ticker(ticker).info)


def _build_income_history(fin: pd.DataFrame, ticker: str, periods: int) -> pd.DataFrame:
    """손익계산서 DataFrame(연간 또는 분기)에서 매출/영업이익/순이익 및 마진 추이를 뽑는다."""
    if fin is None or fin.empty:
        raise TickerNotFoundError(f"'{ticker}'의 재무제표 데이터를 찾을 수 없습니다.")

    revenue = find_row(fin, ["Total Revenue", "TotalRevenue"])
    operating_income = find_row(fin, ["Operating Income", "OperatingIncome"])
    net_income = find_row(fin, ["Net Income", "NetIncome", "Net Income Common Stockholders"])

    if revenue is None or net_income is None:
        raise TickerNotFoundError(f"'{ticker}'의 손익 항목을 찾을 수 없습니다.")

    cols = sorted(fin.columns)[-periods:]
    result = pd.DataFrame(index=cols)
    result["매출"] = revenue.reindex(cols)
    result["영업이익"] = operating_income.reindex(cols) if operating_income is not None else None
    result["순이익"] = net_income.reindex(cols)
    result["영업이익률"] = result["영업이익"] / result["매출"]
    result["순이익률"] = result["순이익"] / result["매출"]
    return result


def get_financial_history(ticker: str, years: int = 4) -> pd.DataFrame:
    """최근 매출/영업이익/순이익 및 마진 추이를 연도별 DataFrame으로 반환한다."""
    result = _build_income_history(yf.Ticker(ticker).financials, ticker, years)
    result.index = [d.strftime("%Y") for d in result.index]
    return result


def get_quarterly_financial_history(ticker: str, quarters: int = 5) -> pd.DataFrame:
    """최근 매출/영업이익/순이익 및 마진 추이를 분기별 DataFrame으로 반환한다.

    yfinance 무료 API는 분기 데이터를 최근 4~5개 분기(약 1~1.25년)까지만 제공한다.
    """
    result = _build_income_history(yf.Ticker(ticker).quarterly_financials, ticker, quarters)
    result.index = [f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}" for d in result.index]
    return result


def get_free_cash_flow_history(ticker: str, years: int = 4) -> pd.Series:
    """연도별 잉여현금흐름(FCF = 영업활동현금흐름 - 자본적지출) 시계열을 반환한다."""
    t = yf.Ticker(ticker)
    cf = t.cashflow

    if cf is None or cf.empty:
        raise TickerNotFoundError(f"'{ticker}'의 현금흐름표 데이터를 찾을 수 없습니다.")

    ocf = find_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = find_row(cf, ["Capital Expenditure", "CapitalExpenditure"])

    if ocf is None or capex is None:
        raise TickerNotFoundError(f"'{ticker}'의 현금흐름 항목을 찾을 수 없습니다.")

    cols = sorted(cf.columns)[-years:]
    # capex는 통상 음수로 표기되므로 더하면 자본적지출만큼 차감된다.
    fcf = (ocf.reindex(cols) + capex.reindex(cols)).dropna()
    if fcf.empty:
        raise TickerNotFoundError(f"'{ticker}'의 현금흐름 값이 비어있습니다.")

    fcf.index = [d.strftime("%Y") for d in fcf.index]
    return fcf


def get_latest_free_cash_flow(ticker: str) -> float:
    """가장 최근 회계연도의 잉여현금흐름(FCF = 영업활동현금흐름 - 자본적지출)을 반환한다."""
    return float(get_free_cash_flow_history(ticker, years=1).iloc[-1])


PRICE_HISTORY_PERIODS = ["1mo", "3mo", "6mo", "1y", "3y", "5y", "max"]


def get_price_history(ticker: str, period: str = "1y") -> pd.Series:
    """종가 시계열을 반환한다 (배당/분할 조정 종가, yfinance 기본값).

    PER/PBR 계산(peers.py)과 달리 여기서는 auto_adjust=True(기본값)를 그대로 쓴다 — 이건
    '당시 실제 종가'가 아니라 '지금 보기 좋은 추이'가 목적이라 배당 재투자 반영이 오히려 맞다.
    """
    if period not in PRICE_HISTORY_PERIODS:
        raise ValueError(f"지원하지 않는 기간입니다: {period}")

    hist = yf.Ticker(ticker).history(period=period)
    if hist is None or hist.empty:
        raise TickerNotFoundError(f"'{ticker}'의 주가 데이터를 찾을 수 없습니다.")

    return hist["Close"].rename("종가")
