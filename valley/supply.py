"""한국 KRX 투자자별 순매수(수급) 조회 — pykrx 기반.

⚠️ 개별종목 투자자별 매매동향(외국인/기관/개인) 조회는 KRX 로그인 세션(KRX_ID/KRX_PW)이
있어야 정상 동작한다 — 실측 확인 결과, 로그인 정보가 없거나 무효하면 pykrx가 내부적으로
에러를 표준출력에 찍고 "예외를 던지지 않은 채" 빈 DataFrame을 반환한다. 그래서 이 모듈은
호출 전에 환경변수 존재를 먼저 확인해 조기에 명확한 메시지를 주고, 빈 결과가 오면 그 사실도
별도로 알린다(로그인 실패와 "해당 기간 거래 데이터 없음"을 완벽히 구분할 수는 없지만,
최소한 무엇을 의심해야 하는지 알려준다).
"""

import os
from datetime import date, timedelta

import pandas as pd
from pykrx import stock

DEFAULT_TRADING_DAYS = 20

# 실측(005930): stock.get_market_trading_value_by_date(..., on="순매수")는 "기관합계/기타법인/
# 개인/외국인합계/전체" 컬럼을 일별(누적 아님) 원화 금액으로 준다. "전체"는 매수-매도 검산용이라
# 항상 0이라 버리고, "기타법인"은 이번 3분류(외국인/기관/개인) 범위 밖이라 버린다.
_COLUMN_RENAME = {"외국인합계": "외국인", "기관합계": "기관", "개인": "개인"}


class SupplyDataError(Exception):
    """투자자별 수급 조회에 실패했을 때 발생 (KRX 로그인 실패/네트워크/데이터 없음 등)."""


def _check_krx_credentials() -> None:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        raise SupplyDataError(
            "KRX_ID/KRX_PW가 설정되어 있지 않습니다. 개별종목 투자자별 수급 조회는 KRX 로그인이 "
            "필요합니다 (.env에 KRX_ID/KRX_PW를 추가하세요)."
        )


def get_investor_net_trading(stock_code: str, days: int = DEFAULT_TRADING_DAYS) -> pd.DataFrame:
    """최근 `days` 영업일의 외국인/기관/개인 순매수(원화 금액) 일별 시계열을 반환한다.

    조회 시작일은 영업일 수 기준으로 넉넉한 달력일수(주말·공휴일 버퍼)를 역산해 잡은 뒤,
    실제 반환된 행 중 최근 `days`개만 남긴다 — 주말/공휴일은 애초에 행 자체가 없어 자연히
    빠진다.
    """
    _check_krx_credentials()

    today = date.today()
    calendar_days = int(days * 1.6) + 15  # 주말·공휴일을 감안한 버퍼 (실측: 45일 조회 시 30영업일 확보됨)
    fromdate = (today - timedelta(days=calendar_days)).strftime("%Y%m%d")
    todate = today.strftime("%Y%m%d")

    try:
        df = stock.get_market_trading_value_by_date(fromdate, todate, stock_code, on="순매수")
    except Exception as e:
        raise SupplyDataError(f"KRX 수급 조회 중 오류가 발생했습니다: {e}") from e

    if df is None or df.empty:
        raise SupplyDataError(
            "KRX 수급 데이터를 가져오지 못했습니다 (KRX 로그인 실패 또는 해당 기간 거래 데이터가 "
            "없을 수 있습니다)."
        )

    result = df[list(_COLUMN_RENAME.keys())].rename(columns=_COLUMN_RENAME)
    return result.tail(days)


def summarize_net_trading(daily: pd.DataFrame) -> pd.Series:
    """get_investor_net_trading()이 반환한 일별 순매수의 기간 합계를 구한다 (순수 계산, I/O 없음).

    호출부가 이미 받아온 일별 DataFrame을 그대로 합산하는 방식이라, 합계를 보여주기 위해
    pykrx를 다시 호출할 필요가 없다(캐싱 레이어가 하나로 충분함).
    """
    return daily.sum()
