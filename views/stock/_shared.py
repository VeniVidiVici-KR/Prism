"""Stock Research 상단부 섹션 여러 곳이 공유하는 작은 헬퍼 (파일 분할 라운드, 2026-08).

⚠️ 매크로 분할과 달리 이 페이지는 원래부터 로컬 st.cache_data 래퍼가 없었다(전부 views._cache의
캐시된 함수를 직접 import해 쓴다) — 그래서 여기엔 캐시 identity를 지키기 위한 재수출이 필요
없다. 대신 4개 섹션(재무 추이·DART 공시·투자자별 수급·리스크 지표)이 토씨 하나 다르지 않게
반복하던 "한국 종목코드 추출"만 모은다.
"""


def kr_stock_code(resolved_ticker: str) -> str:
    """resolve_ticker가 반환한 티커에서 DART/KRX/토스가 쓰는 6자리 코드만 뗀다.

    한국 종목은 resolved_ticker가 "005930.KS"처럼 거래소 접미사가 붙어 오므로, 마침표
    앞부분만 쓴다(미국 종목은 애초에 이 함수를 호출하는 섹션이 currency == "KRW" 분기
    안에서만 부르므로 접미사 유무를 신경 쓰지 않아도 된다).
    """
    return resolved_ticker.split(".")[0]
