"""yfinance를 감싸는 데이터 조회 레이어. UI(app.py)는 이 모듈의 함수만 호출한다."""

import re

import pandas as pd
import yfinance as yf


class TickerNotFoundError(Exception):
    """존재하지 않거나 데이터가 없는 티커를 조회했을 때 발생."""


# ⚠️ 클래스 주식(BRK.B, BF.B 등)은 yfinance가 점(.)이 아니라 하이픈(-) 표기를 쓴다(실측 확인:
# BRK-B는 정상 조회되지만 BRK.B는 이름/가격 없이 실패하고, BF.B는 엉뚱한 MUTUALFUND로 오매칭
# 됨 — BF-B가 진짜 Brown-Forman 주식). "영문자만으로 된 앞부분 + 점 + 영문자 한 글자"만 좁게
# 매칭해 하이픈으로 바꾼다 — .KS/.KQ처럼 점 뒤가 두 글자 이상인 한국 종목 접미사나, 앞부분에
# 숫자가 섞인 입력(예: "005930.KS")은 이 정규식에 아예 매칭되지 않아 절대 안 건드린다.
_CLASS_SHARE_PATTERN = re.compile(r"^([A-Z]+)\.([A-Z])$")


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
            if (info.get("longName") or info.get("shortName")) and info.get("quoteType") == "EQUITY":
                return candidate
        raise TickerNotFoundError(f"'{code}' 종목코드를 KOSPI/KOSDAQ에서 찾을 수 없습니다.")

    match = _CLASS_SHARE_PATTERN.match(code)
    if match:
        code = f"{match.group(1)}-{match.group(2)}"
    return code


def build_snapshot(ticker: str, info: dict) -> dict:
    """이미 받아온 info dict로부터 스냅샷을 만든다.

    peers.py처럼 같은 Ticker의 info를 다른 목적으로도 써야 하는 호출부가 있어,
    yf.Ticker(...).info를 중복 조회하지 않도록 조회와 가공을 분리했다.

    ⚠️ 통화 불일치 라운드(2026-09) — currency("USD" 등, 거래소 시세 통화)와
    financialCurrency(재무제표 공시 통화)는 외국 ADR(KT, PKX, TSM, BABA 등 실측 확인)에서
    서로 다르다: 주가·시가총액·배당(dividendRate/dividends)·payoutRatio·dividendYield·
    forwardPE는 이미 거래통화로 일관되게 오지만, .financials/.balance_sheet/.cashflow
    원본은 환산 없이 공시 통화 그대로(예: KT는 KRW) 온다. 이 프로젝트의 PER/PBR·DCF 계산은
    가격(거래통화)과 재무제표(공시통화)를 그대로 나누므로, 이 둘이 다르면 결과가 수십만%
    단위로 터진다(실측: KT 정식 DCF 괴리율 +18,692,553%). financialCurrency가 없는(None)
    종목은 비교 자체가 무의미하므로 불일치로 취급하지 않는다(정상 종목과 동일하게 통과).
    currency_mismatch는 여기서 한 번만 계산해 하위 섹션들이 각자 다시 판정하지 않고
    그대로 받아쓰게 한다(is_financial_industry와 같은 원칙).
    """
    name = info.get("longName") or info.get("shortName")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not name or price is None:
        raise TickerNotFoundError(f"'{ticker}' 티커에 대한 데이터를 찾을 수 없습니다.")

    currency = info.get("currency", "USD")
    financial_currency = info.get("financialCurrency")
    currency_mismatch = financial_currency is not None and financial_currency != currency

    return {
        "ticker": ticker.upper(),
        "name": name,
        "price": price,
        "currency": currency,
        "financial_currency": financial_currency,
        "currency_mismatch": currency_mismatch,
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "market_cap": info.get("marketCap"),
        # ⚠️ "회사 개요" 섹션 전용 — 이미 위에서 받은 info를 그대로 재사용한다(추가 .info
        # 호출 없음). 실측 확인(AAPL/MSFT/JPM): 전부 존재. 실측 확인(빈 종목 사례, 예:
        # 데이터 없는 티커): 전부 None — 호출부(뷰)가 없는 값만 개별적으로 생략해야 한다.
        "employees": info.get("fullTimeEmployees"),
        "website": info.get("website"),
        "city": info.get("city"),
        "state": info.get("state"),
        "country": info.get("country"),
        "business_summary": info.get("longBusinessSummary"),
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
    """연도별 잉여현금흐름(FCF) 시계열을 반환한다.

    yfinance가 제공하는 "Free Cash Flow" 행을 최우선으로 쓴다 — 실측 확인 결과 이 행은
    OCF-Capex(기존 계산식)와 완전히 동일한 값을 주지만(AAPL/MSFT/TSLA/삼성전자/KB금융/
    신한지주 전부 diff=0 확인), 은행 등 일부 종목(예: JPM, BAC)은 현금흐름표에 "Capital
    Expenditure" 행 자체가 없어 기존 방식으로는 계산이 아예 불가능했다 — 이 행을 쓰면
    그 경우도 해결된다. "Free Cash Flow" 행이 없는 (드문) 종목만 기존 OCF-Capex 방식으로
    폴백한다.
    """
    t = yf.Ticker(ticker)
    cf = t.cashflow

    if cf is None or cf.empty:
        raise TickerNotFoundError(f"'{ticker}'의 현금흐름표 데이터를 찾을 수 없습니다.")

    cols = sorted(cf.columns)[-years:]

    fcf_row = find_row(cf, ["Free Cash Flow", "FreeCashFlow"])
    if fcf_row is not None:
        fcf = fcf_row.reindex(cols).dropna()
    else:
        ocf = find_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = find_row(cf, ["Capital Expenditure", "CapitalExpenditure"])
        if ocf is None or capex is None:
            raise TickerNotFoundError(f"'{ticker}'의 현금흐름 항목을 찾을 수 없습니다.")
        # capex는 통상 음수로 표기되므로 더하면 자본적지출만큼 차감된다.
        fcf = (ocf.reindex(cols) + capex.reindex(cols)).dropna()

    if fcf.empty:
        raise TickerNotFoundError(f"'{ticker}'의 현금흐름 값이 비어있습니다.")

    fcf.index = [d.strftime("%Y") for d in fcf.index]
    return fcf


def get_latest_free_cash_flow(ticker: str) -> float:
    """가장 최근 회계연도의 잉여현금흐름(FCF = 영업활동현금흐름 - 자본적지출)을 반환한다."""
    return float(get_free_cash_flow_history(ticker, years=1).iloc[-1])


def get_fcff_inputs(ticker: str) -> dict:
    """정식 DCF(FCFF→WACC)에 필요한 최근 회계연도 재무 항목을 yfinance에서 가져온다.

    ⚠️ 실측 확인(AAPL/MSFT/005930): 대차대조표의 "Total Debt" 행이 세 종목 모두 그대로
    존재해(Long Term Debt + 단기차입을 직접 합칠 필요 없음) 그걸 총부채로 쓴다. 현금은
    "Cash Cash Equivalents And Short Term Investments"(단기투자자산 포함, 세 종목 모두
    존재)를 우선 쓴다 — 순부채 계산에 단기투자자산을 포함하는 게 통상적이다. 손익계산서의
    "Interest Expense"는 실측 결과 세 종목 모두 **양수(비용의 크기, 부호 반전 없음)**로
    온다(MSFT 30.51억달러, 삼성전자 6,057.83억원) — 단, ⚠️ AAPL 최신 회계연도(2025-09-30)는
    이 행 자체가 NaN이다(Interest Expense/Interest Expense Non Operating/Net Interest
    Income 전부 NaN, quarterly·TTM에서도 확인 안 됨 — Apple이 최근 10-K에서 이자비용을
    "Other income/(expense), net"에 합쳐 별도로 공시하지 않는 것으로 보인다. 2021년 이전
    데이터엔 있었지만 4년 이상 지난 값을 지금 끌어쓰는 건 왜곡이 크다고 판단해 폴백하지
    않았다). 이 함수는 interest_expense가 없으면 None으로 반환하니, 호출부(밸류에이션
    로직)가 "정식 DCF 계산 불가"를 명확히 알려야 한다 — 0으로 조용히 대체하면 부채가 있는
    종목에서 이자비용을 0으로 가정하는 셈이 되어 WACC·FCFF 둘 다 조용히 틀려진다.

    반환: {"total_debt", "cash_and_st_investments", "interest_expense"(None 가능),
    "tax_provision"(None 가능), "pretax_income"(None 가능), "as_of"}.
    """
    t = yf.Ticker(ticker)
    bs = t.balance_sheet
    fin = t.financials

    if bs is None or bs.empty:
        raise TickerNotFoundError(f"'{ticker}'의 대차대조표 데이터를 찾을 수 없습니다.")
    if fin is None or fin.empty:
        raise TickerNotFoundError(f"'{ticker}'의 손익계산서 데이터를 찾을 수 없습니다.")

    bs_col = bs.columns[0]
    fin_col = fin.columns[0]

    total_debt_row = find_row(bs, ["Total Debt"])
    if total_debt_row is None:
        raise TickerNotFoundError(f"'{ticker}'의 총부채(Total Debt) 항목을 찾을 수 없습니다.")
    total_debt_raw = total_debt_row.get(bs_col)
    # ⚠️ 무차입 기업은 이 행 값이 NaN으로 올 수 있다(합산할 부채 항목 자체가 없어서) — 0으로
    # 취급한다(실제 조회 실패가 아니라 "부채가 없다"는 정상적인 상태이므로 에러가 아니다).
    total_debt = 0.0 if pd.isna(total_debt_raw) else float(total_debt_raw)

    cash_row = find_row(bs, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    cash_raw = cash_row.get(bs_col) if cash_row is not None else None
    cash = 0.0 if cash_raw is None or pd.isna(cash_raw) else float(cash_raw)

    interest_row = find_row(fin, ["Interest Expense", "Interest Expense Non Operating"])
    interest_raw = interest_row.get(fin_col) if interest_row is not None else None
    interest_expense = None if interest_raw is None or pd.isna(interest_raw) else float(interest_raw)

    tax_row = find_row(fin, ["Tax Provision"])
    tax_raw = tax_row.get(fin_col) if tax_row is not None else None
    tax_provision = None if tax_raw is None or pd.isna(tax_raw) else float(tax_raw)

    pretax_row = find_row(fin, ["Pretax Income"])
    pretax_raw = pretax_row.get(fin_col) if pretax_row is not None else None
    pretax_income = None if pretax_raw is None or pd.isna(pretax_raw) else float(pretax_raw)

    return {
        "total_debt": total_debt,
        "cash_and_st_investments": cash,
        "interest_expense": interest_expense,
        "tax_provision": tax_provision,
        "pretax_income": pretax_income,
        "as_of": bs_col,
    }


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
