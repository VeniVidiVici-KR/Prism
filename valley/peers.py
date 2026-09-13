"""Peer Group 비교 및 PER/PBR 밸류에이션 밴드 조회 레이어."""

from datetime import date

import pandas as pd
import requests
import yfinance as yf

from valley.data import build_snapshot, find_row
from valley.formatting import format_money, format_multiple, format_percent

RECOMMENDATIONS_URL = "https://query1.finance.yahoo.com/v6/finance/recommendationsbysymbol/{ticker}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class ValuationDataError(Exception):
    """Peer/밸류에이션 데이터를 조회하지 못했을 때 발생."""


def get_peer_tickers(ticker: str, limit: int = 5) -> list[str] | None:
    """Yahoo Finance의 '유사 종목' 데이터에서 관련 티커 목록을 가져온다.

    엄밀한 동일산업 분류가 아닌 Yahoo의 관련도 기반 추천이다.
    API 호출 자체가 실패하면 None을, 정상 응답이지만 유사 종목이 없으면 빈 리스트를
    반환한다 (호출부가 "실패"와 "결과 없음"을 구분해 다른 메시지를 보여줄 수 있도록).
    """
    try:
        resp = requests.get(RECOMMENDATIONS_URL.format(ticker=ticker), headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        result = resp.json()["finance"]["result"]
    except Exception:
        return None

    if not result:
        return []
    symbols = [s["symbol"] for s in result[0].get("recommendedSymbols", [])]
    return symbols[:limit]


def _latest_fundamentals(ticker_obj: yf.Ticker) -> dict:
    """가장 최근 공통 회계연도의 EPS/BVPS/순이익/자본총계를 계산한다.

    손익계산서(financials)와 재무상태표(balance_sheet)의 최신 결산일이 어긋나는 종목이
    있을 수 있어, get_historical_per_pbr와 동일하게 두 표에 공통으로 존재하는 결산일 중
    가장 최근 것을 함께 쓴다 (독립적으로 각자 최신 컬럼을 쓰면 EPS·BVPS·ROE가 서로 다른
    회계연도로 짝지어질 수 있다). net_income/equity를 원본 그대로도 반환해 payout_ratio/roe가
    재계산 없이 재사용할 수 있게 한다.
    """
    empty = {"eps": None, "bvps": None, "net_income": None, "equity": None}
    fin = ticker_obj.financials
    bs = ticker_obj.balance_sheet
    if fin is None or fin.empty or bs is None or bs.empty:
        return empty

    common_cols = sorted(set(fin.columns) & set(bs.columns))
    if not common_cols:
        return empty
    latest_col = common_cols[-1]

    net_income_row = find_row(fin, ["Net Income", "NetIncome", "Net Income Common Stockholders"])
    diluted_shares_row = find_row(fin, ["Diluted Average Shares", "Basic Average Shares"])
    equity_row = find_row(bs, ["Stockholders Equity", "Common Stock Equity"])
    shares_row = find_row(bs, ["Ordinary Shares Number", "Share Issued"])

    net_income = equity = eps = bvps = None
    if net_income_row is not None:
        ni = net_income_row.get(latest_col)
        if pd.notna(ni):
            net_income = float(ni)
    if equity_row is not None:
        eq = equity_row.get(latest_col)
        if pd.notna(eq):
            equity = float(eq)
    if net_income is not None and diluted_shares_row is not None:
        sh = diluted_shares_row.get(latest_col)
        if pd.notna(sh) and sh:
            eps = net_income / float(sh)
    if equity is not None and shares_row is not None:
        sh = shares_row.get(latest_col)
        if pd.notna(sh) and sh:
            bvps = equity / float(sh)

    return {"eps": eps, "bvps": bvps, "net_income": net_income, "equity": equity}


def _dividend_per_share(ticker_obj: yf.Ticker, info: dict) -> float | None:
    """연간 주당배당(DPS)을 구한다.

    info의 dividendRate(연환산 주당배당, 이미 통화 단위 실수라 스케일 문제 없음)를 우선 쓰고,
    없으면 최근 배당 지급 이력(t.dividends)에서 최근 4회 지급액 합으로 근사한다
    (분기배당 기업 기준 약 1년치 — 반기/월배당 기업은 근사치가 다소 어긋날 수 있다).
    """
    dps = info.get("dividendRate")
    if dps:
        return float(dps)

    dividends = ticker_obj.dividends
    if dividends is None or dividends.empty:
        return None
    recent = dividends.tail(4)
    if recent.empty:
        return None
    return float(recent.sum())


def _dividend_yield(info: dict, dps: float | None, price: float) -> float | None:
    """배당수익률을 비율(0.024 = 2.4%)로 반환한다.

    ⚠️ info["dividendYield"]는 실측 확인 결과 이미 '퍼센트 값'으로 온다 (예: AAPL 0.32,
    AT&T 5.09, 삼성전자 0.58, KB금융 2.54 — 전부 dividendRate/price로 역산한 실제 배당수익률(%)과
    일치). 즉 0.32는 "0.32%"라는 뜻이라 100으로 나눠야 우리 내부 비율 표기(0.0032)가 된다.
    그대로 쓰면(예: 0.32를 32%로 잘못 해석) 수익률이 100배 부풀려진다.
    info에 값이 없는 종목(주로 한국 소형주)은 dividendRate/t.dividends 기반 DPS를 현재가로
    나눠 직접 계산한다.
    """
    raw = info.get("dividendYield")
    if raw is not None:
        return float(raw) / 100
    if dps is None or not price:
        return None
    return dps / price


def _payout_ratio(info: dict, dps: float | None, shares_outstanding: float | None, net_income: float | None) -> float | None:
    """배당성향을 비율(0.35 = 35%)로 반환한다.

    info["payoutRatio"]는 실측 확인 결과 이미 비율(0.1259 = 12.59%)로 온다 — dividendYield와
    달리 스케일 보정이 필요 없다. 값이 없으면 DPS × 발행주식수(배당총액) / 순이익으로 계산한다.
    순이익이 0 이하이거나 배당(DPS)이 없으면 배당성향은 의미가 없어 None을 반환한다.
    """
    raw = info.get("payoutRatio")
    if raw is not None:
        return float(raw)
    if not dps or not shares_outstanding or net_income is None or net_income <= 0:
        return None
    return (dps * shares_outstanding) / net_income


def _roe(net_income: float | None, equity: float | None) -> float | None:
    """자기자본이익률을 비율(0.15 = 15%)로 반환한다. 자본총계가 0 이하면 None.

    순이익이 음수인 경우는 None으로 막지 않는다 — 적자로 인한 음(-)의 ROE도 유효한 정보다.
    """
    if net_income is None or equity is None or equity <= 0:
        return None
    return net_income / equity


def get_valuation_snapshot(ticker: str) -> dict:
    """이름/현재가/PER/PBR/시가총액을 담은 밸류에이션 스냅샷을 반환한다.

    PER/PBR은 항상 최근 확정 회계연도(FY) 재무제표로 직접 계산한다. Yahoo의
    trailingPE/priceToBook(TTM 기준)을 쓰면 미국은 TTM, 한국은 데이터가 없어
    FY 기준으로 계산되는 등 같은 표 안에서 종목마다 EPS 정의가 달라지는 문제가
    있어, 두 시장 모두에서 안정적으로 구할 수 있는 FY 기준으로 통일했다.

    forward_per은 Yahoo가 제공하는 향후 12개월 컨센서스 기반 PER(forwardPE)로,
    직접 계산이 아닌 Yahoo 원본 값을 그대로 쓴다. 애널리스트 추정치가 부족한
    종목(주로 소형주)은 None이 반환된다.

    dividend_yield/payout_ratio/roe는 모두 info 우선 → 재무제표/배당이력 직접계산 폴백
    구조이며, 내부적으로는 전부 "비율"(0.024 = 2.4%)로 통일해 저장한다 (스케일 확정 근거는
    각 헬퍼 함수 docstring 참고). Fwd(예상) 배당수익률은 넣지 않는다 — 한국 종목 커버리지가
    얕아 반쪽 지표가 된다.
    """
    t = yf.Ticker(ticker)
    info = t.info
    snapshot = build_snapshot(ticker, info)
    price = snapshot["price"]

    fundamentals = _latest_fundamentals(t)
    eps, bvps = fundamentals["eps"], fundamentals["bvps"]
    per = price / eps if eps and eps > 0 else None
    pbr = price / bvps if bvps and bvps > 0 else None

    forward_per = info.get("forwardPE")
    if not forward_per or forward_per <= 0:
        forward_per = None

    dps = _dividend_per_share(t, info)
    dividend_yield = _dividend_yield(info, dps, price)
    payout_ratio = _payout_ratio(info, dps, snapshot.get("shares_outstanding"), fundamentals["net_income"])
    roe = _roe(fundamentals["net_income"], fundamentals["equity"])

    return {
        "ticker": snapshot["ticker"],
        "name": snapshot["name"],
        "price": price,
        "currency": snapshot["currency"],
        # ⚠️ 통화 불일치 라운드(2026-09) — per/pbr/bvps는 raw 재무제표(공시통화) ÷ 가격(거래통화)
        # 이라 currency_mismatch면 무의미해진다(실측: KT PER=0.0056). 호출부(뷰)가 이 플래그로
        # 표시 여부를 판단하게, 계산 자체는 그대로 반환한다(데이터 레이어는 정직하게 값을
        # 내놓고, 게이팅은 뷰가 한다는 이 프로젝트의 기존 원칙 — is_financial_industry와 동일).
        "currency_mismatch": snapshot.get("currency_mismatch", False),
        "financial_currency": snapshot.get("financial_currency"),
        "market_cap": snapshot.get("market_cap"),
        "per": per,
        "pbr": pbr,
        "bvps": bvps,
        "forward_per": forward_per,
        "dividend_yield": dividend_yield,
        "payout_ratio": payout_ratio,
        "roe": roe,
    }


def get_roe_history(ticker: str, years: int = 3) -> pd.Series:
    """연도별 ROE(자기자본이익률) 시계열을 반환한다 (get_historical_per_pbr와 동일하게
    손익계산서·재무상태표에 공통으로 존재하는 결산일만 사용).

    적자 연도(ROE<0)도 그대로 포함한다 — FCF 3개년 평균(음수 연도 제외)과 달리, ROE는
    이미 정규화된 비율이라 음수 연도를 빼도 "평균 계산이 성립 안 하는" 문제가 생기지
    않는다. 오히려 적자 연도를 빼면 은행의 실제 리스크(충당금 등으로 인한 손실 가능성)를
    과소평가하게 되므로, 단순 평균에 그대로 포함시켜 호출부가 "들쭉날쭉함" 자체를 보게 한다.
    """
    t = yf.Ticker(ticker)
    fin = t.financials
    bs = t.balance_sheet
    if fin is None or fin.empty or bs is None or bs.empty:
        raise ValuationDataError(f"'{ticker}'의 재무제표 데이터를 찾을 수 없습니다.")

    net_income = find_row(fin, ["Net Income", "NetIncome", "Net Income Common Stockholders"])
    equity = find_row(bs, ["Stockholders Equity", "Common Stock Equity"])
    if net_income is None or equity is None:
        raise ValuationDataError(f"'{ticker}'의 ROE 계산에 필요한 순이익/자본총계 항목을 찾을 수 없습니다.")

    fiscal_years = sorted(set(fin.columns) & set(bs.columns))[-years:]

    rows = {}
    for fye in fiscal_years:
        ni, eq = net_income.get(fye), equity.get(fye)
        roe = _roe(float(ni) if pd.notna(ni) else None, float(eq) if pd.notna(eq) else None)
        if roe is not None:
            rows[fye.strftime("%Y")] = roe

    if not rows:
        raise ValuationDataError(f"'{ticker}'의 ROE를 계산할 수 있는 연도가 없습니다 (자본총계가 0 이하이거나 데이터 누락).")

    return pd.Series(rows)


def get_historical_per_pbr(ticker: str, years: int = 5) -> pd.DataFrame:
    """연도별 PER/PBR을 담은 DataFrame을 반환한다 (회계연도 말 시점 종가 기준)."""
    t = yf.Ticker(ticker)
    fin = t.financials
    bs = t.balance_sheet
    if fin is None or fin.empty or bs is None or bs.empty:
        raise ValuationDataError(f"'{ticker}'의 재무제표 데이터를 찾을 수 없습니다.")

    net_income = find_row(fin, ["Net Income", "NetIncome", "Net Income Common Stockholders"])
    diluted_shares = find_row(fin, ["Diluted Average Shares", "Basic Average Shares"])
    equity = find_row(bs, ["Stockholders Equity", "Common Stock Equity"])
    shares = find_row(bs, ["Ordinary Shares Number", "Share Issued"])

    if net_income is None or diluted_shares is None or equity is None or shares is None:
        raise ValuationDataError(f"'{ticker}'의 EPS/BVPS 계산에 필요한 항목을 찾을 수 없습니다.")

    fiscal_years = sorted(set(fin.columns) & set(bs.columns))[-years:]

    rows = {}
    for fye in fiscal_years:
        ni, dsh = net_income.get(fye), diluted_shares.get(fye)
        eq, sh = equity.get(fye), shares.get(fye)

        eps = float(ni) / float(dsh) if pd.notna(ni) and pd.notna(dsh) and dsh else None
        bvps = float(eq) / float(sh) if pd.notna(eq) and pd.notna(sh) and sh else None

        # auto_adjust=False: 기본값(True)은 배당/분할을 반영해 과거 종가를 낮춰 조정하므로,
        # 당시 실제 종가가 아니라 배당 재투자 기준 수정주가가 되어 PER이 실제보다 낮게 나온다.
        price_hist = t.history(start=fye, end=fye + pd.Timedelta(days=7), auto_adjust=False)
        price = float(price_hist["Close"].iloc[0]) if not price_hist.empty else None

        per = price / eps if price and eps and eps > 0 else None
        pbr = price / bvps if price and bvps and bvps > 0 else None
        rows[fye.strftime("%Y")] = {"PER": per, "PBR": pbr}

    return pd.DataFrame.from_dict(rows, orient="index")


def build_valuation_table(rows: list[dict]) -> pd.DataFrame:
    """get_valuation_snapshot() 결과 리스트를 화면 표시용 DataFrame으로 포맷한다.

    ⚠️ 통화 불일치 라운드(2026-09) — PER/PBR은 raw 재무제표(공시통화)로 계산돼 currency_mismatch
    행에서는 무의미하다(실측: KT PER=0.0056). 그 행만 PER/PBR을 "N/A"로 비운다 — Fwd PER(Yahoo
    forwardPE, 이미 거래통화 기준이라 실측으로 정상값 확인)·배당수익률·배당성향·ROE·시가총액은
    전부 비율이거나 이미 거래통화 기준이라 그대로 둔다(같은 표 안에서 어떤 열은 믿을 수 있고
    어떤 열은 아닌지 명확히 구분된다).
    """
    df = pd.DataFrame(rows)
    df["price"] = df.apply(lambda r: format_money(r["price"], r["currency"]), axis=1)
    df["market_cap"] = df.apply(
        lambda r: format_money(r["market_cap"], r["currency"]) if r["market_cap"] else "N/A", axis=1
    )
    if "currency_mismatch" in df.columns:
        df["per"] = df.apply(lambda r: None if r.get("currency_mismatch") else r["per"], axis=1)
        df["pbr"] = df.apply(lambda r: None if r.get("currency_mismatch") else r["pbr"], axis=1)
    df["per"] = df["per"].map(format_multiple)
    df["forward_per"] = df["forward_per"].map(format_multiple)
    df["pbr"] = df["pbr"].map(format_multiple)
    df["dividend_yield"] = df["dividend_yield"].map(format_percent)
    df["payout_ratio"] = df["payout_ratio"].map(format_percent)
    df["roe"] = df["roe"].map(format_percent)
    return df.rename(
        columns={
            "ticker": "티커",
            "name": "종목명",
            "price": "현재가",
            "market_cap": "시가총액",
            "per": "PER",
            "forward_per": "Fwd PER",
            "pbr": "PBR",
            "dividend_yield": "배당수익률",
            "payout_ratio": "배당성향",
            "roe": "ROE",
        }
    )[["티커", "종목명", "현재가", "PER", "Fwd PER", "PBR", "배당수익률", "배당성향", "ROE", "시가총액"]]


def get_annual_dividend_history(ticker: str, years: int = 5) -> pd.Series:
    """연도별 배당 합계(연간 DPS) 시계열을 반환한다 (완결된 연도만, 오름차순).

    ⚠️ 분기배당 기업은 연 4회 지급되므로(_dividend_per_share의 "최근 4회 합" 근사와 달리)
    지급일 기준으로 연도별 합계를 내야 정확한 "연간 DPS"가 된다.

    ⚠️ 진행 중인 올해(조회 시점의 달력 연도)는 실측 확인 결과 아직 예정된 지급이 다 끝나지
    않은 상태다(예: 105560.KS는 이 조회 시점 기준 2026년 들어 2회만 지급됨, JPM도 마찬가지로
    4회 중 일부만 지급된 상태) — 그대로 합산하면 실제 연간 배당보다 낮게 나와 D0(최근 연간
    DPS)와 배당 CAGR을 왜곡한다. 그래서 올해는 완결된 해가 아니므로 제외하고, 가장 최근
    "완결된" 연도까지만 남긴다.
    """
    t = yf.Ticker(ticker)
    dividends = t.dividends
    if dividends is None or dividends.empty:
        raise ValuationDataError(f"'{ticker}'의 배당 이력을 찾을 수 없습니다 (무배당 종목일 수 있습니다).")

    annual = dividends.groupby(dividends.index.year).sum()
    current_year = date.today().year
    annual = annual[annual.index < current_year]
    if annual.empty:
        raise ValuationDataError(f"'{ticker}'의 완결된 연도 배당 이력이 없습니다.")

    annual = annual.tail(years)
    annual.index = [str(y) for y in annual.index]
    return annual
