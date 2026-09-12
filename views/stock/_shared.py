"""Stock Research 상단부 섹션 여러 곳이 공유하는 작은 헬퍼 (파일 분할 라운드, 2026-08).

⚠️ 매크로 분할과 달리 이 페이지는 원래부터 로컬 st.cache_data 래퍼가 없었다(전부 views._cache의
캐시된 함수를 직접 import해 쓴다) — 그래서 여기엔 캐시 identity를 지키기 위한 재수출이 필요
없다. 대신 4개 섹션(재무 추이·DART 공시·투자자별 수급·리스크 지표)이 토씨 하나 다르지 않게
반복하던 "한국 종목코드 추출"만 모은다.

⚠️ 하단부 분할 라운드(2026-09) — is_financial_industry도 여기로 옮겼다. P/B×ROE·DDM(섹션
2·3)과 정식 DCF(섹션 5) 둘 다 이 판정이 필요한데, 판정 로직 자체가 두 곳에 따로 있으면
나중에 한쪽만 고치는 사고가 나기 쉽다(흩어짐 방지) — 그래서 판정 함수는 여기 하나만 두고,
"이 종목이 금융업인가"라는 최종 계산은 조립부(stock_research.py)에서 한 번만 해서 각 섹션에
bool로 넘긴다. 반면 "그 bool로 무엇을 보여줄지" 분기는 원본 코드에서 있던 자리 그대로 각
섹션 모듈 안에 남겨둔다(P/B×ROE·DDM은 최상위 if라 모듈 맨 앞에서, 정식 DCF는
valuation_method 분기 안쪽에 한 번 더 있던 자리 그대로).
"""


def kr_stock_code(resolved_ticker: str) -> str:
    """resolve_ticker가 반환한 티커에서 DART/KRX/토스가 쓰는 6자리 코드만 뗀다.

    한국 종목은 resolved_ticker가 "005930.KS"처럼 거래소 접미사가 붙어 오므로, 마침표
    앞부분만 쓴다(미국 종목은 애초에 이 함수를 호출하는 섹션이 currency == "KRW" 분기
    안에서만 부르므로 접미사 유무를 신경 쓰지 않아도 된다).
    """
    return resolved_ticker.split(".")[0]


# ⚠️ sector 단위("Financial Services")는 너무 거칠다 — 실측 확인 결과 은행(JPM/BAC/KB금융/
# 신한지주)·보험(삼성화재/삼성생명)뿐 아니라 결제 네트워크(V/MA, industry="Credit Services")
# 까지 같은 섹터로 묶여 있었다. V/MA는 자산이 가벼운 수수료 기반 사업이라 P/B×ROE·DDM이
# 부적합하고, 오히려 FCF가 튼튼해 DCF가 잘 맞는데 sector 기준으로는 "DCF 부적합" 경고까지
# 잘못 붙는 정반대 상황이었다. industry 단위로 좁혀 이 두 오류를 한 번에 고친다.
#
# 실측 industry 문자열: JPM/BAC="Banks - Diversified", KB금융/신한지주="Banks - Regional",
# 삼성화재="Insurance - Property & Casualty", 삼성생명="Insurance - Life", V/MA="Credit
# Services"(제외), AAPL/삼성전자="Consumer Electronics"(제외, sector도 Technology).
# "Banks - "/"Insurance - "(하이픈 구분 하위분류) 접두어만 좁게 매칭한다 — 예를 들어
# "Insurance Brokers"(수수료 기반 보험중개, 대차대조표가 가벼움)는 이 형식이 아니라서
# 자연히 제외된다(실측 대상엔 없었지만 사업모형이 언더라이팅 보험사와 달라 의도적으로 뺌).
_FINANCIAL_INDUSTRY_PREFIXES = ("Banks - ", "Insurance - ")


def is_financial_industry(industry: str | None) -> bool:
    """industry가 은행·보험 계열인지 판별한다.

    industry가 없거나("N/A" 포함, build_snapshot의 기본값) 비어있으면 비금융으로 간주한다 —
    데이터가 없다고 sector 기준으로 되돌아가면 이번에 고치려는 오탐(V/MA류)이 되살아날 수 있고,
    잘못된 P/B×ROE/DDM 노출·잘못된 DCF 경고보다는 그냥 일반 DCF로 처리하는 쪽이 안전하다
    (어떤 종목이든 일반 DCF 경로는 항상 유효해 이 기본값이 앱을 죽이지 않는다).
    """
    if not industry or industry == "N/A":
        return False
    return industry.startswith(_FINANCIAL_INDUSTRY_PREFIXES)
