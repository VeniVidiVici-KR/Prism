"""한국거래소(KRX) 상장종목 코드/이름 매핑 조회 레이어.

⚠️ pykrx의 공개 함수 get_market_ticker_list()는 (2026-07 기준 설치 버전 1.2.8) 벌크 종목
리스트를 data.krx.co.kr의 getJsonData.cmd(MDCSTAT01501)로 조회하는데, 이 엔드포인트가
KRX 로그인 세션을 요구하도록 바뀌어 있어 KRX_ID/KRX_PW 환경변수 없이는 "LOGOUT" 오류로
실패한다(직접 재현 확인함). 반면 종목명 단건 조회(get_market_ticker_name)가 내부적으로
쓰는 상장종목검색 엔드포인트는 로그인 없이 정상 동작하며, 이 엔드포인트가 전체 상장종목
목록(코드/이름/시장)을 한 번에 반환하는 StockTicker 싱글턴을 채운다. 그래서 공개 API 대신
이 내부 모듈(pykrx.website.krx.market.ticker)을 직접 쓴다 — pykrx의 비공개 구현이라
향후 버전이 바뀌면 깨질 수 있음을 감안한 선택이다.
"""

from pykrx.website.krx.market.ticker import StockTicker

_MARKET_LABELS = {"STK": "코스피", "KSQ": "코스닥"}


class KrxDataError(Exception):
    """KRX 종목 목록 조회에 실패했을 때 발생."""


def get_kr_ticker_directory() -> list[dict]:
    """코스피+코스닥 전 종목의 [code, name, market] 목록을 반환한다."""
    try:
        df = StockTicker().listed
    except Exception as e:
        raise KrxDataError(f"KRX 종목 목록 조회에 실패했습니다: {e}") from e

    if df is None or df.empty:
        raise KrxDataError("KRX 종목 목록이 비어 있습니다.")

    df = df[df["시장"].isin(_MARKET_LABELS)]
    return [
        {"code": code, "name": row["종목"], "market": _MARKET_LABELS[row["시장"]]}
        for code, row in df.iterrows()
    ]


def search_kr_stock_by_name(query: str, directory: list[dict]) -> list[dict]:
    """종목명 부분일치로 검색한다 (대소문자/앞뒤 공백 무시).

    우선주("삼성전자우" 등)도 이름에 검색어가 포함되면 그대로 후보에 남긴다 — 별도로
    걸러내지 않는다.
    """
    q = query.strip().lower()
    if not q:
        return []
    return [item for item in directory if q in item["name"].lower()]
