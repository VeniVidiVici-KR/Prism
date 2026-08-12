"""매크로 지표(물가/금리/고용/성장/통화·환율) 조회 레이어.

- 미국 및 대부분의 한국 지표는 FRED 공개 CSV 엔드포인트(API 키 불필요)에서 가져온다.
- 한국 CPI/M2는 FRED 미러링 데이터가 갱신을 멈춰서, 한국은행 ECOS OpenAPI로 우선 조회하고
  키가 없거나 실패하면 FRED 데이터로 폴백한다. ECOS는 같은 지표라도 "신지표"/"구지표"로
  테이블이 나뉘어 있고 구지표는 대부분 2000년대 초반에 갱신이 끊겼으니, 새 지표를 추가할
  때는 반드시 최근 데이터가 실제로 나오는 stat_code인지 확인해야 한다.
"""

import os
from datetime import date

import pandas as pd
import requests

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
ECOS_URL = (
    "https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/5000/"
    "{stat_code}/{cycle}/{start}/{end}/{item_code}"
)
ECOS_ITEM_LIST_URL = "https://ecos.bok.or.kr/api/StatisticItemList/{api_key}/json/kr/1/{count}/{stat_code}"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# ⚠️ "CPI 구성 비중 & 기여도" 뷰 전용 — 기존 INDICATORS 카탈로그(개별 지표 버튼 조회용)와는
# 별도로 관리한다. 배타적이고 망라적인(mutually exclusive & exhaustive) 항목 집합이어야 하는데,
# 기존 카탈로그는 층위가 섞여 있어(예: 미국 주거비는 근원서비스의 하위, 한국 K01 외식은 K
# 음식·숙박의 하위) 그대로 못 쓴다 — valley/macro.py 상단 주석/각 항목 주석 참고.

# 미국: Food + Energy + Core goods + Core services = All items(정확히 100%가 되는 BLS의 표준
# 4분할 정의). BLS의 relative importance(상대중요도) 메타데이터는 등록키 없이는 API가 막혀
# 있고(catalog=true 요청 시 "catalog has been disabled" 실측 확인) HTML/원본파일 서버도 봇
# 정책상 전부 403이라(우회 시도 대신 정책 존중), get_bls_relative_importance()가 BLS_API_KEY로
# 직접 조회한다 — flat 파일(download.bls.gov)도 실측 시도했으나 동일하게 403이라 API 경로만
# 유일하게 동작함을 확인했다. 아래 딕셔너리는 FRED 카탈로그 key -> BLS 원본 series ID
# 매핑이다 — FRED가 쓰는 별칭(CPIUFDNS/CPIENGNS/CPIAUCNS)과 BLS 원본 ID
# (CUUR0000SAF1/CUUR0000SA0E/CUUR0000SA0)가 달라서 혼동하기 쉽다. api.bls.gov를 직접 조회해
# 같은 월(2026-06)의 지수값이 소수점까지 정확히 일치함을 실측 대조 확인했다(349.731=349.731,
# 328.95=328.950, 333.952=333.952) — 값 대조까지 마친 매핑이다. ⚠️ FRED에 있는
# "RIW..."(Relative Importance Weights) 시리즈는 연준 G.17 산업생산지수 가중치이지 CPI
# 가중치가 아니라서 이름이 비슷해도 절대 쓰지 않는다.
US_CPI_COMPONENT_BLS_IDS = {
    "US_CPI_NSA": "CUUR0000SA0",
    "US_CPI_FOOD": "CUUR0000SAF1",
    "US_CPI_ENERGY": "CUUR0000SA0E",
    "US_CPI_CORE_GOODS": "CUUR0000SACL1E",
    "US_CPI_CORE_SERVICES": "CUUR0000SASLE",
}

# MoM(전월비) 기여도 분해 전용 — 위 US_CPI_COMPONENT_BLS_IDS와 같은 논리적 카테고리 키를
# 그대로 써서(예: "US_CPI_FOOD"), 그 카테고리의 계절조정(SA) 버전 INDICATORS 카탈로그 key로
# 연결한다. ⚠️ 가중치(상대중요도)는 계절조정 여부와 무관한 "카테고리" 속성이라, MoM 뷰도
# US_CPI_COMPONENT_BLS_IDS를 통해 얻은 동일 가중치를 그대로 재사용한다(새로 조회하지 않음) —
# 값(가격지수)만 SA로 바꿔 쓴다. "물가 세부" 카탈로그의 SA 항목 주석 참고.
US_CPI_COMPONENT_SA_KEYS = {
    "US_CPI_FOOD": "US_CPI_FOOD_SA",
    "US_CPI_ENERGY": "US_CPI_ENERGY_SA",
    "US_CPI_CORE_GOODS": "US_CPI_CORE_GOODS_SA",
    "US_CPI_CORE_SERVICES": "US_CPI_CORE_SERVICES_SA",
}

# 한국: ECOS StatisticItemList가 901Y009(소비자물가지수)의 지출목적별 12개 대분류(A~L)
# 가중치(WEIGHT 필드)를 실측으로 직접 제공한다(실측: item_code "0"의 WEIGHT="1000" — 1000분비
# 기준, 12개 대분류 WEIGHT 합계 = 1000.0 정확히 일치 확인). K01(외식)은 K(음식·숙박)의 하위
# 중분류라 이 배타적 집합에서 제외한다(기존 "물가 세부" 카탈로그엔 그대로 남겨둠).
KR_CPI_MAJOR_ITEM_CODES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]


class MacroDataError(Exception):
    """매크로 지표를 조회하지 못했을 때 발생."""


# 카테고리 -> [{key, label, description?, ...}] 형태의 카탈로그. get_indicator_series가 key로
# 조회한다. ⚠️ label은 버튼 한 줄에 들어가는 "짧은 이름"이고, description(선택 필드)은 지역
# 범위·기준선·계절조정 여부·출처 같은 부가정보다 — 예전엔 이 부가정보를 label 괄호 안에
# 욱여넣어 버튼 안에서 줄바꿈되며 삐져나왔다(예: "필라델피아 연준 제조업 지불가격 (지역
# 서베이, 0 기준, 재화 인플레 선행지표)"). description은 버튼 툴팁(help)과 지표 선택 후
# 캡션 두 곳에 노출되므로 정보를 삭제한 게 아니라 자리만 옮긴 것이다. description이 없는
# 항목은 label 자체로 충분히 짧고 명확하다고 판단해 그대로 뒀다.
INDICATORS = {
    "US": {
        "물가": [
            {"key": "US_CPI", "label": "CPI (소비자물가지수)", "fred_id": "CPIAUCSL"},
            {"key": "US_CORE_CPI", "label": "근원 CPI", "fred_id": "CPILFESL"},
            {"key": "US_PCE", "label": "PCE (개인소비지출물가)", "fred_id": "PCEPI"},
            {"key": "US_CORE_PCE", "label": "근원 PCE", "fred_id": "PCEPILFE"},
            {"key": "US_PPI", "label": "생산자물가지수(PPI)", "fred_id": "PPIACO"},
        ],
        # ⚠️ SA/NSA 통일 원칙(실측 후 결정, 자세한 근거는 valley/macro.py 상단 대신 여기 남김):
        # CPI 구성항목은 전부 원계열(NSA)로 통일했다. STEP 3의 "구성항목 한눈에 보기"가
        # 전년동월대비(YoY)% 비교인데, 12개월 차분에서 계절성이 상쇄되는 NSA가 YoY 비교의
        # 표준 관행이고(SA/NSA를 한 비교에 섞으면 예전 PER TTM/FY 혼재와 같은 문제가 난다),
        # 실측 결과 BLS/FRED가 CPI 구성항목 전부에 대해 "CUUR"(원계열) 계열을 안정적으로
        # 제공한다(CUSR=계절조정 대응 계열도 있지만 이번엔 안 씀). 위 "물가"의 기존
        # US_CPI(CPIAUCSL, SA)는 그대로 두되, 구성항목과 나란히 비교할 수 있도록 원계열
        # 헤드라인(US_CPI_NSA)을 이 카테고리에 별도로 추가했다 — 기존 지표는 손대지 않음.
        # PCE 재화/서비스는 원계열(NSA) 버전을 FRED에서 찾지 못해(실측 시도 결과 확인 불가),
        # 기존 PCE 헤드라인/근원(PCEPI/PCEPILFE)과 같은 계절조정(SA) 기준으로 통일해 그
        # 계열 안에서만 일관되게 뒀다 — CPI 원계열 그룹과는 섞이지 않도록 라벨에 각각
        # "(원계열)"/"(계절조정)"을 명시했고, STEP 3 비교표는 CPI 원계열 항목만 묶는다.
        "물가 세부": [
            {
                "key": "US_CPI_NSA", "label": "CPI 총지수(원계열)", "fred_id": "CPIAUCNS",
                "description": "원계열(NSA) · 구성항목 비교의 기준 시리즈",
            },
            {
                "key": "US_CPI_SHELTER", "label": "주거비", "fred_id": "CUUR0000SAH1",
                "description": "원계열(NSA) · 근원서비스의 하위 항목(배타적 4분할에는 미포함)",
            },
            {"key": "US_CPI_FOOD", "label": "식품", "fred_id": "CPIUFDNS", "description": "원계열(NSA)"},
            {"key": "US_CPI_ENERGY", "label": "에너지", "fred_id": "CPIENGNS", "description": "원계열(NSA)"},
            {
                "key": "US_CPI_CORE_GOODS", "label": "근원 상품", "fred_id": "CUUR0000SACL1E",
                "description": "식품·에너지 제외 상품 · 원계열(NSA)",
            },
            {
                "key": "US_CPI_CORE_SERVICES", "label": "근원 서비스", "fred_id": "CUUR0000SASLE",
                "description": "에너지서비스 제외 · 원계열(NSA)",
            },
            {
                "key": "US_PCE_GOODS", "label": "PCE 재화", "fred_id": "DGDSRG3M086SBEA",
                "description": "PCE 재화 물가지수 · 계절조정(SA)",
            },
            {
                "key": "US_PCE_SERVICES", "label": "PCE 서비스", "fred_id": "DSERRG3M086SBEA",
                "description": "PCE 서비스 물가지수 · 계절조정(SA)",
            },
            # ⚠️ MoM(전월비) 기여도 분해 전용 — 위 4개 원계열(NSA)과는 다른 계열이다. MoM은
            # 계절성(여름 전기요금 등)에 크게 좌우돼 NSA로 보면 "이번 달 특이사항"과 매년
            # 반복되는 계절 패턴이 구분되지 않는다 — 그래서 MoM 뷰에서만 SA를 쓴다(YoY 뷰는
            # 12개월 차분에서 계절성이 상쇄되므로 그대로 NSA 유지, 위 물가 세부 주석 참고).
            # 실측(2026-08 라운드, 최신 2026-06) 결과 FRED에 4분할 전부 SA 버전이 존재함을
            # 확인했다 — 헤드라인 SA는 이미 "물가" 카테고리의 US_CPI(CPIAUCSL)로 등록돼 있어
            # 별도 추가하지 않는다. ⚠️ 이 4개는 위 원계열 4개와 절대 같은 화면에서 섞지 않는다
            # (views/macro_indicator.py의 US_CPI_COMPONENT_SA_KEYS 매핑·MoM 뷰 참고).
            {
                "key": "US_CPI_FOOD_SA", "label": "식품 (계절조정)", "fred_id": "CPIUFDSL",
                "description": "계절조정(SA) · MoM 기여도 분해 전용 — YoY 비교는 위 원계열 '식품'을 쓴다",
            },
            {
                "key": "US_CPI_ENERGY_SA", "label": "에너지 (계절조정)", "fred_id": "CPIENGSL",
                "description": "계절조정(SA) · MoM 기여도 분해 전용",
            },
            {
                "key": "US_CPI_CORE_GOODS_SA", "label": "근원 상품 (계절조정)", "fred_id": "CUSR0000SACL1E",
                "description": "식품·에너지 제외 상품 · 계절조정(SA) · MoM 기여도 분해 전용",
            },
            {
                "key": "US_CPI_CORE_SERVICES_SA", "label": "근원 서비스 (계절조정)", "fred_id": "CUSR0000SASLE",
                "description": "에너지서비스 제외 · 계절조정(SA) · MoM 기여도 분해 전용",
            },
        ],
        "금리": [
            {"key": "US_FEDFUNDS", "label": "연방기금금리(기준금리)", "fred_id": "FEDFUNDS"},
            {"key": "US_DGS10", "label": "10년 국채금리", "fred_id": "DGS10"},
            {"key": "US_DGS2", "label": "2년 국채금리", "fred_id": "DGS2"},
            {"key": "US_DGS3MO", "label": "3개월 국채금리", "fred_id": "DGS3MO"},
            {"key": "US_T10Y2Y", "label": "장단기 금리차(10Y-2Y)", "fred_id": "T10Y2Y"},
            {
                "key": "US_T10Y3M", "label": "장단기 금리차(10Y-3M)", "fred_id": "T10Y3M",
                "description": "침체 예측력이 10Y-2Y보다 더 강한 것으로 알려진 스프레드",
            },
        ],
        "위험지표": [
            {
                "key": "US_HY_SPREAD", "label": "하이일드 스프레드", "fred_id": "BAMLH0A0HYM2",
                "description": "신용 리스크 프리미엄, 단위 %p",
            },
            {"key": "US_VIX", "label": "VIX 변동성지수", "fred_id": "VIXCLS", "description": "낮을수록 시장이 안정적"},
            {"key": "US_BREAKEVEN10", "label": "10년 기대인플레이션(BEI)", "fred_id": "T10YIE"},
            {"key": "US_REAL_RATE10", "label": "10년 실질금리(TIPS)", "fred_id": "DFII10"},
            {
                "key": "US_DOLLAR", "label": "달러인덱스", "fred_id": "DTWEXBGS",
                "description": "무역가중, 2006년 1월=100 기준",
            },
        ],
        # ⚠️ 하이일드 스프레드(US_HY_SPREAD)는 기존 "위험지표"에 그대로 둔다 — 여기서는 그
        # 위에 "투자등급" 층을 추가해 신용 국면을 두 층으로 본다(AI 데이터센터 등 대규모
        # 투자를 회사채로 조달하는 기업들의 조달 여건 신호). 실측(2026-08-07 기준) 전부
        # 최신·%(OAS) 단위 확인 — 이름이 비슷한 "Effective Yield" 계열(BAMLC0A0CMEY 등, 실측
        # 5.36%)은 OAS가 아니라 절대 쓰지 않는다(시리즈 설명에 "Option-Adjusted Spread"가
        # 명시된 것만 채택). 등급별을 다 넣으면 카탈로그가 비대해져(AAA/AA/A/BBB 4종),
        # 투자등급 전체 + AA + BBB 3개로 추렸다 — 하이퍼스케일러(GOOGL/MSFT/AAPL 등)는
        # AA~A 등급대, 오라클처럼 차입 의존도가 높은 기업은 BBB 근처에 있어 이 두 등급이
        # AI 인프라 조달 여건을 보는 데 실용적이다(AAA/A는 생략해도 정보 손실이 작다).
        # 실측 값(2026-08-07): 하이일드 2.70% > BBB 0.97% > AA 0.57% > 투자등급 전체 0.78%
        # (AA가 투자등급 전체 평균보다 낮은 건 정상 — 전체 평균에 BBB 비중이 크게 반영됨).
        # 등급이 낮을수록 스프레드가 커지는 정상적인 신용 위계(AAA<AA<A<BBB<HY)를 확인했다.
        # ⚠️ 별도 "신용시장" 카테고리로 신설한다 — "위험지표"는 이미 VIX·BEI·실질금리·달러
        # 인덱스 등 성격이 다른 5개 지표가 있어 여기 더 얹으면 카테고리가 과밀해지고, 이
        # 라운드가 "신용시장"을 하이일드+투자등급+기업 발행 동향을 아우르는 하나의 축으로
        # 명시적으로 요구했다. 아래 macro_indicator.py의 "신용 스프레드 비교" 섹션에서
        # 위험지표의 US_HY_SPREAD와 이 카테고리의 투자등급 스프레드를 함께 겹쳐 본다(둘 다
        # 이미 %(OAS) 동일 단위라 국가부채/GDP 비교와 같은 방식으로 정규화 없이 겹친다) —
        # 기존 "지표 겹쳐보기" 오버레이 후보에도 카테고리 구분 없이 전부 자동 포함된다.
        "신용시장": [
            {
                "key": "US_IG_SPREAD", "label": "투자등급 전체 스프레드", "fred_id": "BAMLC0A0CM",
                "description": "ICE BofA US Corporate OAS · 신용등급 전체(AAA~BBB) 평균",
            },
            {
                "key": "US_IG_SPREAD_AA", "label": "AA 등급 스프레드", "fred_id": "BAMLC0A2CAA",
                "description": "ICE BofA AA US Corporate OAS · 하이퍼스케일러(빅테크) 신용등급대",
            },
            {
                "key": "US_IG_SPREAD_BBB", "label": "BBB 등급 스프레드", "fred_id": "BAMLC0A4CBBB",
                "description": "ICE BofA BBB US Corporate OAS · 투자등급 최하단, 차입 의존 기업대",
            },
        ],
        # ⚠️ "기저 인플레(underlying inflation)" — 근원(core)이 식품·에너지를 "범주째" 빼는 것과
        # 달리, 이 계열들은 매달 극단적으로 오르내린 "품목"을 통계적으로 잘라내거나(trimmed
        # mean) 중앙값만 취해(median) 일시적 충격에 덜 휘둘리는 물가 추세를 본다. 실측(2026-08
        # 라운드, 최신 2026-06) 결과 보고:
        #
        # 1) 댈러스 연준 Trimmed Mean PCE — 후보 "PCETRIM12M159SFRCA"는 실측 결과 404(존재하지
        #    않는 ID, 오타로 추정)라 제외했다. 실제 존재하는 정확한 ID는
        #    PCETRIM12M159SFRBDAL(12개월, "Percent Change from Year Ago")과
        #    PCETRIM1M158SFRBDAL(1개월 연율, "Percent Change at Annual Rate")이다. 둘 다 FRED
        #    시리즈 페이지의 "Units:" 메타데이터를 실측 대조해 확인(추정 아님) — 이미 변화율
        #    (%)로 제공되는 계열이라 yoy_percent()를 적용하면 "변화율의 변화율"이 되어 틀린다.
        #    둘 다 계절조정(SA). 최신값(2026-06): 12개월 2.23%, 1개월 연율 1.44% — 서로 다른
        #    시계(smoothing)라 성격이 다르다(1개월 연율이 훨씬 변동성 큼: 2.94→2.53→2.66→1.44).
        #    시장은 발표일에 1개월 연율(빠른 신호)을, 추세 판단에는 12개월(완만한 신호)을 함께
        #    쓰므로 － 이 앱이 헤드라인 CPI에도 YoY/MoM 두 시계를 모두 노출하는 것과 같은
        #    논리로 － 둘 다 등록한다(단, 절대 같은 차트/표에 섞지 않는다).
        #
        # 2) 클리블랜드 연준 Median CPI / 16% Trimmed-Mean CPI — 후보 ID(*158*)는 실측 결과
        #    "Percent Change at Annual Rate"(1개월 연율)로 확인됐고, 사전에 없는지 걱정했던
        #    12개월 버전도 실측으로 실제 존재함을 확인했다(*159* 접미사,
        #    "Percent Change from Year Ago") — MEDCPIM159SFRBCLE·TRMMEANCPIM159SFRBCLE. 전부
        #    계절조정(SA), 전부 이미 변화율(%)로 제공(지수 아님). 최신값(2026-06): 중위 CPI
        #    12개월 2.71%(1개월 연율 2.11%), 절사평균 CPI 12개월 2.63%(1개월 연율 0.13% —
        #    1개월 연율의 변동성이 12개월판보다 훨씬 큼을 그대로 보여주는 사례).
        #
        # 3) 상식성 대조: 기존 근원 CPI YoY(대략 2~3%대), 근원 PCE YoY(대략 2~3%대)와 나란히
        #    놓으면 이 6개 시리즈의 12개월판(2.2~2.7%대)이 거의 같은 범위에 들어온다 — 단위·
        #    계열을 잘못 집었을 때 나타나는 큰 이탈(예: 10배 차이나 마이너스 부호 반전)이 없다.
        #    자세한 비교는 아래 macro_indicator.py의 "기저 인플레 비교" 섹션 참고.
        #
        # ⚠️ 카테고리 배치: "물가 세부"(이미 12개 항목)에 얹으면 과밀해지고, 이 계열들은
        # "구성항목의 하위 분해"가 아니라 "물가 전체를 다른 방법론으로 재는 대안 지표"라는
        # 점에서 성격이 달라 별도 "기저 인플레" 카테고리로 신설한다.
        "기저 인플레": [
            {
                "key": "US_TRIMMED_MEAN_PCE_12M", "label": "절사평균 PCE (12개월)",
                "fred_id": "PCETRIM12M159SFRBDAL",
                "description": "댈러스 연준 Trimmed Mean PCE · 전년동월대비(%) · 계절조정(SA) · 이미 변화율(지수 아님)",
            },
            {
                "key": "US_TRIMMED_MEAN_PCE_1M", "label": "절사평균 PCE (1개월연율)",
                "fred_id": "PCETRIM1M158SFRBDAL",
                "description": (
                    "댈러스 연준 Trimmed Mean PCE · 전월비 연율화(%) · 계절조정(SA) · 변동성 큼 · "
                    "12개월판과 같은 차트에서 비교하지 말 것"
                ),
            },
            {
                "key": "US_MEDIAN_CPI_12M", "label": "중위 CPI (12개월)", "fred_id": "MEDCPIM159SFRBCLE",
                "description": "클리블랜드 연준 Median CPI · 전년동월대비(%) · 계절조정(SA) · 이미 변화율(지수 아님)",
            },
            {
                "key": "US_MEDIAN_CPI_1M", "label": "중위 CPI (1개월연율)", "fred_id": "MEDCPIM158SFRBCLE",
                "description": (
                    "클리블랜드 연준 Median CPI · 전월비 연율화(%) · 계절조정(SA) · 변동성 큼 · "
                    "12개월판과 같은 차트에서 비교하지 말 것"
                ),
            },
            {
                "key": "US_TRIMMED_MEAN_CPI_12M", "label": "절사평균 CPI (12개월)",
                "fred_id": "TRMMEANCPIM159SFRBCLE",
                "description": "클리블랜드 연준 16% Trimmed-Mean CPI · 전년동월대비(%) · 계절조정(SA) · 이미 변화율(지수 아님)",
            },
            {
                "key": "US_TRIMMED_MEAN_CPI_1M", "label": "절사평균 CPI (1개월연율)",
                "fred_id": "TRMMEANCPIM158SFRBCLE",
                "description": (
                    "클리블랜드 연준 16% Trimmed-Mean CPI · 전월비 연율화(%) · 계절조정(SA) · 변동성 큼 · "
                    "12개월판과 같은 차트에서 비교하지 말 것"
                ),
            },
        ],
        "고용": [
            {"key": "US_UNRATE", "label": "실업률", "fred_id": "UNRATE"},
            {"key": "US_PAYEMS", "label": "비농업고용", "fred_id": "PAYEMS"},
            {"key": "US_ICSA", "label": "신규 실업수당 청구건수", "fred_id": "ICSA"},
        ],
        # ⚠️ 매달 첫 금요일 발표되는 고용보고서(Employment Situation)의 세부 지표. 기존
        # "고용"(실업률/비농업고용/신규청구)과 분리한 이유: (1) 업종별 분해까지 넣으면 16개라
        # 기존 카테고리에 합치면 버튼이 과밀해진다. (2) 이 카테고리는 서로 다른 두 표본조사가
        # 섞여 있다는 걸 항목마다 강조해야 하는 특수한 성격이라(사업체조사 Establishment/CES
        # vs 가계조사 Household/CPS — 표본·정의가 달라 같은 달에도 어긋날 수 있음),
        # description에 매번 출처를 명시한다. 실측(2026-07 기준) 전부 최신, 전부 계절조정(SA) —
        # FRED series 페이지의 "Units:" 메타데이터를 직접 조회해 확인했다(추정 아님). 단위는
        # 고용자수 계열이 천 명, 비율 계열이 %, 임금만 달러(수준, 원계열) — 임금은 수준 자체가
        # 아니라 변화율(MoM/YoY)로 봐야 하는 지표라 아래 "고용보고서 한눈에 보기" 뷰에서
        # 변환한다.
        # ⚠️ USPRIV(민간고용)는 아래 11개 업종 분해와 같은 집합이 아니다 — 정부를 제외한 민간
        # 합계라 업종별로 또 나누면 중복 계상이 된다. 업종별 분해(US_NFP_*)는 정부를 포함한
        # BLS supersector 11개로 배타적·망라적이다. 실측 자기검증: 11개 업종 전월비 증감의 합이
        # PAYEMS 전월비 증감과 소수점 이내로 거의 일치(2026-07 기준 -22.9천 명 vs -23.0천 명,
        # BLS가 시리즈별로 개별 계절조정해 생기는 정상적 오차)했고, PAYEMS-USPRIV 전월비 증감이
        # USGOVT 전월비 증감과 정확히 일치(-53.0천 명)함을 확인했다.
        "고용 세부": [
            {
                "key": "US_PRIV_PAYEMS", "label": "민간고용", "fred_id": "USPRIV",
                "description": "비농업 민간부문 고용자 수 · 사업체조사(Establishment/CES) · 정부 제외",
            },
            {
                "key": "US_CIVPART", "label": "경제활동참가율", "fred_id": "CIVPART",
                "description": "가계조사(Household/CPS) · 비농업고용과 표본·정의가 다른 별개 조사",
            },
            {
                "key": "US_EMRATIO", "label": "고용률", "fred_id": "EMRATIO",
                "description": "가계조사(Household/CPS) · 생산가능인구 대비 취업자 비율",
            },
            {
                "key": "US_U6RATE", "label": "광의실업률(U-6)", "fred_id": "U6RATE",
                "description": "가계조사(Household/CPS) · 실업자+한계근로자+경제적 사유 시간제 근로자 포함",
            },
            {
                "key": "US_AVG_HOURLY_EARNINGS", "label": "평균 시간당 임금", "fred_id": "CES0500000003",
                "description": "사업체조사(CES) · 달러 수준(원계열) · MoM·YoY 변화율은 '고용보고서 한눈에 보기'에서",
            },
            {
                "key": "US_NFP_MINING", "label": "광업·벌목", "fred_id": "CES1021000001",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_CONSTRUCTION", "label": "건설", "fred_id": "USCONS",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_MANUFACTURING", "label": "제조업", "fred_id": "MANEMP",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_TRADE_TRANSPORT_UTIL", "label": "무역·운송·유틸리티", "fred_id": "USTPU",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_INFO", "label": "정보", "fred_id": "USINFO",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_FINANCIAL", "label": "금융활동", "fred_id": "USFIRE",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_PROF_BIZ", "label": "전문·기업서비스", "fred_id": "USPBS",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_EDU_HEALTH", "label": "교육·보건(민간)", "fred_id": "USEHS",
                "description": (
                    "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES) · "
                    "공공 교육·보건 종사자는 '정부' 항목에 포함(중복 아님)"
                ),
            },
            {
                "key": "US_NFP_LEISURE", "label": "레저·접객", "fred_id": "USLAH",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_OTHER_SERVICES", "label": "기타서비스", "fred_id": "USSERV",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES)",
            },
            {
                "key": "US_NFP_GOVERNMENT", "label": "정부", "fred_id": "USGOVT",
                "description": "비농업고용 업종별 분해(11개 업종 중 1개) · 사업체조사(CES) · 공공 교육·보건 종사자 포함",
            },
        ],
        "성장·소비": [
            {"key": "US_GDPC1", "label": "실질 GDP", "fred_id": "GDPC1"},
            {"key": "US_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "GDP"},
            {"key": "US_RSAFS", "label": "소매판매", "fred_id": "RSAFS"},
            {"key": "US_UMCSENT", "label": "소비자심리지수(미시간대)", "fred_id": "UMCSENT"},
            {"key": "US_HOUST", "label": "주택착공건수", "fred_id": "HOUST"},
        ],
        "통화": [
            {"key": "US_M2SL", "label": "M2 통화량", "fred_id": "M2SL"},
        ],
        # ⚠️ ISM 제조업 PMI는 실측 조사로 FRED에서 완전히 삭제됐음을 확인했다(NAPM* 7종 전부
        # 404 — 2016-06-24 ISM의 라이선스 요청으로 FRED가 ISM 계열 22종을 일괄 삭제한 공식
        # 조치, 뒤늦게 되살아난 것도 아니고 대체 ID도 없음). 대안으로 지역 연준 제조업
        # 서베이(공공 데이터, FRED 무료 제공)를 실측 확인해 추가한다 — 필라델피아 연준(제3
        # 연준구: 펜실베이니아 동부·뉴저지 남부·델라웨어)과 뉴욕 연준(뉴욕주)의 두 서베이 전부
        # 2026-07까지 최신, 월별 계절조정(SA). "성장·소비"(UMCSENT 등 이미 존재)와는 성격이
        # 달라(제조업 한정 심리 서베이) 별도 카테고리로 분리했다 — 섞으면 "성장·소비"가
        # 과밀해지고, 이 지표들 고유의 "지역 한정·0 기준 확산지수"라는 주의사항이 묻힌다.
        # ⚠️ 라벨에 지역 범위를 반드시 명시한다 — 전국 지표가 아닌데 ISM 자리를 대신하다 보니
        # "미국 제조업"으로 오해되기 쉽다. ⚠️ 이 지수들은 ISM(50 기준 확산지수, 보통 40~65대)과
        # 달리 0을 기준으로 ±로 움직인다(실측: 필라델피아 재고 0.3, 때로 음수) — 라벨/캡션에
        # 스케일 기준을 명시해 ISM 감각으로 오독하지 않게 한다. ⚠️ 종합지수(General
        # Activity/General Business Conditions)는 세부항목의 가중합성이 아니라 설문의 별도
        # 문항이다(뉴욕 연준 FRED 설명에 "a distinct question posed on the survey"로 명시,
        # 필라델피아도 전반활동과 개별지표를 각각 따로 묻는 구조로 확인) — 그래서 CPI처럼
        # 기여도 분해·원 그래프를 만들지 않는다(산식 관계 자체가 없음, 억지로 맞추면 안 맞는다).
        # ⚠️ 라벨은 "필라"/"엠파이어"로 줄였다 — 같은 카테고리 안에서 두 서베이를 구분할 수
        # 있어야 해서 완전 삭제 대신 짧은 약칭을 남겼다("필라델피아 연준 제조업 지불가격"처럼
        # 풀네임+세부항목을 다 붙이면 버튼 한 줄을 넘겨 줄바꿈되던 문제를 해결). 지역
        # 범위·기준선·서베이 성격(별도 문항) 같은 부가정보는 description으로 옮겼다.
        "경기·심리": [
            {
                "key": "US_PHILLY_GA", "label": "필라 종합지수", "fred_id": "GACDFSA066MSFRBPHI",
                "description": (
                    "필라델피아 연준 제조업 서베이(제3연준구: 펜실베이니아 동부·뉴저지 남부·"
                    "델라웨어) · 0=확장/위축 경계 · 세부항목의 가중합성이 아닌 별도 설문 문항"
                ),
            },
            {
                "key": "US_PHILLY_NEWORDERS", "label": "필라 신규주문", "fred_id": "NOCDFSA066MSFRBPHI",
                "description": "필라델피아 연준 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_PHILLY_SHIPMENTS", "label": "필라 출하", "fred_id": "SHCDFSA066MSFRBPHI",
                "description": "필라델피아 연준 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_PHILLY_EMPLOYMENT", "label": "필라 고용", "fred_id": "NECDFSA066MSFRBPHI",
                "description": "필라델피아 연준 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_PHILLY_PRICES_PAID", "label": "필라 지불가격", "fred_id": "PPCDFSA066MSFRBPHI",
                "description": "필라델피아 연준 제조업 서베이 · 0 기준 · 재화 인플레 선행지표",
            },
            {
                "key": "US_PHILLY_INVENTORIES", "label": "필라 재고", "fred_id": "IVCDFSA066MSFRBPHI",
                "description": "필라델피아 연준 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_EMPIRE_GA", "label": "엠파이어 종합지수", "fred_id": "GACDISA066MSFRBNY",
                "description": (
                    "뉴욕 연준 엠파이어스테이트 제조업 서베이(뉴욕주) · 0=확장/위축 경계 · "
                    "세부항목의 가중합성이 아닌 별도 설문 문항"
                ),
            },
            {
                "key": "US_EMPIRE_NEWORDERS", "label": "엠파이어 신규주문", "fred_id": "NOCDISA066MSFRBNY",
                "description": "뉴욕 연준 엠파이어스테이트 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_EMPIRE_EMPLOYMENT", "label": "엠파이어 고용", "fred_id": "NECDISA066MSFRBNY",
                "description": "뉴욕 연준 엠파이어스테이트 제조업 서베이 · 0 기준 확산지수",
            },
            {
                "key": "US_EMPIRE_PRICES_PAID", "label": "엠파이어 지불가격", "fred_id": "PPCDISA066MSFRBNY",
                "description": "뉴욕 연준 엠파이어스테이트 제조업 서베이 · 0 기준 확산지수",
            },
        ],
        # ⚠️ 국가부채 정의는 여러 개(총부채/순부채, 중앙정부/일반정부)라 한미를 다른 정의로
        # 가져오면 비교가 무의미해진다. "GGGDTAUSA188N"/"GGGDTAKRA188N" 둘 다 실측 확인
        # 결과 IMF WEO(World Economic Outlook) 출처의 "일반정부 총부채(General government
        # gross debt, GFSM 2001 정의 — SDR·통화성예금·채무증권·차입금·보험/연금/표준화보증·
        # 기타미지급금 포함, 지분·투자펀드지분·파생상품 제외)"로 두 나라가 정확히 동일한
        # 정의·출처·단위(% of GDP)·주기(연간, NSA)임을 확인했다(시리즈 ID의 "188N" 접미사가
        # IMF WEO 지표 코드로 국가 전체에 공통 적용됨 — 임의 추정이 아니라 FRED 설명 대조로
        # 확인). 이미 IMF가 %로 계산해 발표한 값을 그대로 쓴다(지수/절대액에서 직접 나누지
        # 않음). 미국 최신 2024년 120.786%, 한국 최신 2023년 50.697%로 상식적 범위(미국
        # 100%+, 한국이 더 낮음). ⚠️ 연간 데이터라 두 나라의 최신 갱신 연도가 다를 수 있다
        # (실측 시점 기준 미국 2024년까지, 한국 2023년까지) — 화면에 반드시 기준연도를 표시한다.
        "재정": [
            {
                "key": "US_DEBT_GDP", "label": "국가부채/GDP", "fred_id": "GGGDTAUSA188N",
                "description": "일반정부 총부채, IMF WEO 기준 · 연간",
            },
        ],
    },
    "KR": {
        "물가": [
            {
                "key": "KR_CPI",
                "label": "CPI (소비자물가지수)",
                "fred_id": "KORCPIALLMINMEI",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "0"},
            },
        ],
        # ECOS 901Y009(소비자물가지수)의 하위 item_code를 StatisticItemList API로 실제
        # 조회해 확정했다(추측 아님) — 12개 지출목적별 대분류(A~L, 알파벳 코드)와 그 하위
        # 항목이 실제로 존재함을 확인. 과제에서 예시로 든 "식료품·주거수도광열·교통·외식"에
        # 정확히 대응하는 항목만 골랐다: A(식료품 및 비주류음료), D(주택,수도,전기및연료),
        # G(교통), K01(음식 서비스=외식). 전부 2026-06까지 갱신 확인, YoY도 상식적 범위
        # (식료품 0~2%대, 주거·수도·전기 1.7%대, 교통 9~11%대, 외식 2.6%대). 한국 CPI는
        # 원래 원계열(NSA) 기준으로 발표되므로(기존 KR_CPI와 동일 stat_code/item_code
        # 체계) 위 미국 CPI 구성항목(NSA로 통일)과 조정 방식 문제 없이 나란히 비교 가능하다.
        # ⚠️ ECOS 전용(FRED 대응 시리즈 없음) — ECOS_API_KEY 없으면 명확히 실패한다(경상수지/
        # 가계신용과 동일 패턴, get_indicator_series가 자동으로 MacroDataError를 던짐).
        "물가 세부": [
            {
                "key": "KR_CPI_FOOD",
                "label": "식료품",
                "description": "식료품 및 비주류음료 · ECOS 지출목적별 대분류 A",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "A"},
            },
            {
                "key": "KR_CPI_HOUSING",
                "label": "주택·수도·전기·연료",
                "description": "ECOS 지출목적별 대분류 D",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "D"},
            },
            {
                "key": "KR_CPI_TRANSPORT",
                "label": "교통",
                "description": "ECOS 지출목적별 대분류 G",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "G"},
            },
            {
                "key": "KR_CPI_DINING",
                "label": "외식",
                "description": "음식 서비스 · ECOS 중분류 K01(대분류 K 음식·숙박의 하위)",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "K01"},
            },
        ],
        "금리": [
            {"key": "KR_BASE_RATE", "label": "한국은행 기준금리", "fred_id": "IRSTCI01KRM156N"},
            {"key": "KR_DGS10", "label": "10년 국고채금리", "fred_id": "IRLTLT01KRM156N"},
        ],
        # ⚠️ KOFIA(금융투자협회) 신용공여 라운드 — 코스피/코스닥 신용융자 잔고는 두 경로로
        # 나뉜다. (1) 여기(ECOS 901Y056/S23E): 월별, 코스피+코스닥 합계만, 1998년부터 장기
        # 추이 — 기존 단일 시계열 버튼 구조에 맞는다. (2) views/macro_indicator.py의
        # "증시 주변자금 / 신용공여" 섹션(공공데이터포털 KOFIA API, valley/kofia.py): 일별,
        # 코스피·코스닥 분리, 2021-11-09부터 — 여러 시장별 계열이 한 번에 오므로 카탈로그
        # 버튼이 아니라 전용 섹션으로 뒀다. ⚠️ 실측 대조(2026-07-31) 결과 이 ECOS 계열
        # (S23E)이 KOFIA API의 crdTrFingWhl(신용거래융자 전체) 월말 스냅샷과 원 단위까지
        # 정확히 일치함을 확인했다 — 정의가 같다(ECOS가 KOFIA 일별 시계열을 월 1회 재게시).
        # 두 계열을 같은 차트에 섞으면 주기가 달라(월별 vs 일별) 오해를 부르므로, 라벨에
        # "월별·장기추이"를 명시해 아래 일별 섹션과 혼동하지 않게 한다.
        "신용융자": [
            {
                "key": "KR_MARGIN_LOAN_MONTHLY",
                "label": "신용융자 잔고 (월별·장기추이)",
                "description": (
                    "ECOS 증시주변자금동향(901Y056) · 코스피+코스닥 합계(시장 구분 없음) · "
                    "월말 스냅샷, 1998년부터 · 코스피/코스닥 분리·일별 최신 데이터는 "
                    "'증시 주변자금 / 신용공여' 섹션 참고"
                ),
                "ecos": {"stat_code": "901Y056", "cycle": "M", "item_code": "S23E"},
            },
        ],
        "고용": [
            {"key": "KR_UNRATE", "label": "실업률", "fred_id": "LRHUTTTTKRM156S"},
        ],
        "성장": [
            {"key": "KR_GDP", "label": "실질 GDP", "fred_id": "NGDPRSAXDCKRQ"},
            {"key": "KR_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "NGDPSAXDCKRQ"},
            {"key": "KR_IP", "label": "산업생산지수", "fred_id": "KORPRINTO01GYSAM"},
        ],
        "통화·환율": [
            {
                "key": "KR_M2",
                "label": "M2 통화량",
                "fred_id": "MYAGM2KRM189N",
                "ecos": {"stat_code": "161Y005", "cycle": "M", "item_code": "BBHS00"},
            },
            {"key": "KR_FX_USD", "label": "원/달러 환율", "fred_id": "DEXKOUS"},
        ],
        # 수출 주도·환율 민감형인 한국 경제 특성을 담는 대외 부문 지표. 전부 실측 확인
        # 완료(마지막 관측치가 몇 개월 이내로 최신이고, 단위/스케일이 상식적이며, 계정명이
        # 의도한 지표와 일치함) — 자세한 검증 근거는 각 항목 주석 참고.
        "대외": [
            {
                # ECOS "2.5.1.1. 국제수지(BPM6)" 표의 최상위 항목(경상수지 자체, item_code
                # 000000). 실측: 2026-05 38,607.4백만달러(월간)까지 갱신 확인, 최근 몇 개월도
                # 1.3~3.9조원대 흑자 규모로 상식적인 범위. FRED 후보(KORB6BLTT02STSAQ)는
                # 2024-10 이후 갱신이 끊긴 데다 단위도 GDP대비 %라 원계열(백만달러)과 안 맞아
                # 폴백으로 쓰지 않고 ECOS 전용으로 둔다.
                "key": "KR_CURRENT_ACCOUNT",
                "label": "경상수지",
                "description": "월별, 백만달러",
                "ecos": {"stat_code": "301Y013", "cycle": "M", "item_code": "000000"},
            },
            {
                # ECOS "3.2.1. 수출입 총괄"(통관기준) 표의 수출금액(item_code T002, 월중
                # 원계열 — 연간 합계가 월별 12개 합과 별도 계열로 존재해 누적계열이 아님을
                # 확인). 실측: 2026-05 87,821,386천불(=약 878억달러)까지 갱신 확인, 2024~2025년
                # 평균 월 550~650억달러대와 비교하면 2026년 들어 수출이 크게 늘어난 추세로
                # 보인다(이상치인지 실제 급증인지는 이 프로젝트 범위 밖). FRED
                # 폴백(XTEXVA01KRM667S)은 2026-04 기준 858.1억달러로 ECOS 원계열과 거의 일치
                # (같은 통관기준 수출 시계열의 OECD 미러로 판단) — 신뢰 가능한 폴백으로 채택.
                "key": "KR_EXPORTS",
                "label": "수출액",
                "description": "월별, 천달러, 통관기준",
                "fred_id": "XTEXVA01KRM667S",
                "ecos": {"stat_code": "901Y118", "cycle": "M", "item_code": "T002"},
            },
            {
                # ECOS "3.5. 외환보유액" 표의 합계(item_code 99). 실측: 2026-06
                # 427,359,263천달러(=약 4,274억달러)까지 갱신 확인, 절대 수준도 한국 외환보유액
                # 통상 범위(4천억달러 안팎)와 일치. FRED 폴백(TRESEGKRM052N)은 "금 제외"
                # 기준이라 ECOS 합계(금 포함)보다 약 1~1.5% 낮게 나오는 정의 차이가 있지만,
                # 두 계열 다 최신이고 스케일이 같은 단위(백만달러 상당)라 근사 폴백으로는
                # 허용 가능하다고 판단했다(정확한 회계 대조용이 아니라 추이 확인용 지표라서).
                "key": "KR_FX_RESERVES",
                "label": "외환보유액",
                "description": "월별, 천달러",
                "fred_id": "TRESEGKRM052N",
                "ecos": {"stat_code": "732Y001", "cycle": "M", "item_code": "99"},
            },
        ],
        "가계신용": [
            {
                # ECOS "1.2.4.1.1. 가계신용(업권별, 분기)" 표의 최상위 항목(가계신용 총계,
                # item_code 1000000, 분기별). "1.2.4.2. 예금취급기관 가계대출(월)"
                # (151Y002/151Y005)은 월별이라 더 자주 갱신되지만 예금취급기관 대출만 포함해
                # 보험사·여신전문기관·판매신용 등이 빠진 "가계신용"의 부분집합이라 쓰지 않았다.
                # 실측: 2026Q1 1,993,110.8십억원(약 1,993조원)까지 갱신 확인 — 분기 통계 특성상
                # 최근 분기 발표까지 통상 약 2개월 지연되는데(오늘 2026-07-20 기준으로는
                # 2026Q2가 아직 미발표) 이는 정상적인 지연이지 갱신이 끊긴 구지표가 아니다.
                # FRED에서 확인된 후보(HDTGPDKRQ163N 등)는 전부 "가계부채/GDP 비율(%)"이라
                # 십억원 단위 잔액과 단위가 달라 폴백으로 쓸 수 없다 — ECOS 전용으로 둔다.
                "key": "KR_HOUSEHOLD_CREDIT",
                "label": "가계신용 잔액",
                "description": "분기별, 십억원",
                "ecos": {"stat_code": "151Y001", "cycle": "Q", "item_code": "1000000"},
            },
        ],
        # 미국 US_DEBT_GDP(GGGDTAUSA188N)와 동일한 IMF WEO "일반정부 총부채, % of GDP" 정의 —
        # valley/macro.py 상단 US "재정" 카테고리 주석 참고. 한국은 FRED가 이 시리즈를 이미
        # ECOS 없이 IMF WEO 원본 그대로 미러링하므로 ECOS 폴백이 필요 없다(FRED 키도 필요
        # 없음 — 공개 CSV 엔드포인트).
        "재정": [
            {
                "key": "KR_DEBT_GDP", "label": "국가부채/GDP", "fred_id": "GGGDTAKRA188N",
                "description": "일반정부 총부채, IMF WEO 기준 · 연간",
            },
        ],
    },
}


def _find_indicator(key: str) -> dict:
    for categories in INDICATORS.values():
        for indicators in categories.values():
            for ind in indicators:
                if ind["key"] == key:
                    return ind
    raise MacroDataError(f"'{key}'는 등록되지 않은 지표입니다.")


def get_fred_series(series_id: str) -> pd.Series:
    """FRED 공개 CSV 엔드포인트에서 시계열을 가져온다 (API 키 불필요)."""
    try:
        df = pd.read_csv(FRED_CSV_URL.format(series_id=series_id))
    except Exception as e:
        raise MacroDataError(f"FRED 시리즈 '{series_id}' 조회에 실패했습니다: {e}") from e

    date_col, value_col = df.columns[0], df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    series = df.set_index(date_col)[value_col].dropna()

    if series.empty:
        raise MacroDataError(f"FRED 시리즈 '{series_id}'에 유효한 데이터가 없습니다.")
    return series.sort_index()


def _ecos_period_bounds(cycle: str) -> tuple[str, str]:
    """ECOS는 주기(M/Q/A)마다 조회 시작~종료 문자열 형식이 다르다 (예: 월별 "202607",
    분기별 "2026Q3", 연간 "2026"). 기존에는 월별(M)만 지원해 하드코딩돼 있었다.
    """
    today = date.today()
    if cycle == "M":
        return "196501", today.strftime("%Y%m")
    if cycle == "Q":
        return "1965Q1", f"{today.year}Q{(today.month - 1) // 3 + 1}"
    if cycle == "A":
        return "1965", str(today.year)
    raise MacroDataError(f"지원하지 않는 ECOS 주기입니다: {cycle}")


def _ecos_parse_time(time_str: str, cycle: str) -> pd.Timestamp:
    """ECOS 응답의 TIME 필드(주기별로 형식이 다름)를 해당 구간 시작일로 변환한다."""
    if cycle == "M":
        return pd.to_datetime(time_str + "01", format="%Y%m%d")
    if cycle == "Q":
        year, q = time_str.split("Q")
        month = (int(q) - 1) * 3 + 1
        return pd.Timestamp(year=int(year), month=month, day=1)
    if cycle == "A":
        return pd.to_datetime(time_str + "0101", format="%Y%m%d")
    raise MacroDataError(f"지원하지 않는 ECOS 주기입니다: {cycle}")


def get_ecos_series(stat_code: str, cycle: str, item_code: str) -> pd.Series:
    """한국은행 ECOS에서 시계열을 가져온다 (stat_code/item_code/주기 조합은 호출부가 지정).

    cycle은 "M"(월별)·"Q"(분기별)·"A"(연간)를 지원한다.
    """
    api_key = os.environ.get("ECOS_API_KEY")
    if not api_key:
        raise MacroDataError("ECOS_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

    start, end = _ecos_period_bounds(cycle)
    url = ECOS_URL.format(
        api_key=api_key,
        stat_code=stat_code,
        cycle=cycle,
        start=start,
        end=end,
        item_code=item_code,
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise MacroDataError(f"ECOS 조회 중 오류가 발생했습니다: {e}") from e

    result = payload.get("StatisticSearch")
    if not result or "row" not in result:
        error_msg = payload.get("RESULT", {}).get("MESSAGE", "알 수 없는 오류")
        raise MacroDataError(f"ECOS 응답에 데이터가 없습니다: {error_msg}")

    rows = result["row"]
    index = pd.DatetimeIndex([_ecos_parse_time(r["TIME"], cycle) for r in rows])
    values = pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce")
    series = pd.Series(values, index=index).dropna().sort_index()

    if series.empty:
        raise MacroDataError(f"ECOS 시리즈 '{stat_code}/{item_code}'에 유효한 데이터가 없습니다.")
    return series


def get_ecos_item_weights(stat_code: str) -> dict[str, tuple[str, float]]:
    """ECOS StatisticItemList에서 최상위(대분류, P_ITEM_CODE=="0") 항목들의 실제 가중치를
    실측으로 가져온다 (지수에서 가중치를 역산하는 게 아니라 ECOS가 메타데이터로 직접 제공하는
    WEIGHT 필드를 그대로 쓴다). 반환값은 {item_code: (item_name, weight_permille)} — WEIGHT는
    1000분비 기준이다(item_code "0"의 WEIGHT="1000"으로 실측 확인). 100 기준 %로 쓰려면
    호출부가 /10 해야 한다.
    """
    api_key = os.environ.get("ECOS_API_KEY")
    if not api_key:
        raise MacroDataError("ECOS_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

    url = ECOS_ITEM_LIST_URL.format(api_key=api_key, count=1800, stat_code=stat_code)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise MacroDataError(f"ECOS 품목 메타데이터 조회 중 오류가 발생했습니다: {e}") from e

    result = payload.get("StatisticItemList")
    if not result or "row" not in result:
        error_msg = payload.get("RESULT", {}).get("MESSAGE", "알 수 없는 오류")
        raise MacroDataError(f"ECOS 품목 메타데이터 응답에 데이터가 없습니다: {error_msg}")

    weights: dict[str, tuple[str, float]] = {}
    for row in result["row"]:
        if row.get("P_ITEM_CODE") == "0" and row.get("CYCLE") == "M" and row.get("ITEM_CODE") not in weights:
            try:
                weights[row["ITEM_CODE"]] = (row["ITEM_NAME"], float(row["WEIGHT"]))
            except (TypeError, ValueError, KeyError):
                continue

    if not weights:
        raise MacroDataError(f"ECOS 통계표 '{stat_code}'에서 대분류 가중치를 찾지 못했습니다.")
    return weights


def get_bls_relative_importance(bls_series_id: str) -> tuple[float, str]:
    """BLS 공식 API의 aspect 메타데이터에서 CPI 구성항목의 상대중요도(Relative Importance)를
    가져온다.

    ⚠️ 실측으로 확인한 응답 형식은 사전 조사(aspect_type/"I1" 코드)와 달랐다 — 실제 aspect
    항목은 {"name": "Relative Importance", "value": "13.447", ...} 형태로, "name" 필드 문자열이
    정확히 "Relative Importance"인 것을 찾아야 한다("Relative Importance for previous year"는
    전년 비교용이라 제외). 이 값은 매 관측월(데이터 포인트)마다 붙어 있고 매월 바뀐다 — 물가
    상승분을 반영해 매월 재계산되는 BLS 공식 개념이라(연 1회 CE 서베이로 정하는 "기초 지출
    비중"과는 별개), 이 함수는 항상 가장 최근 관측월의 값을 반환한다(household 대신 최신월
    데이터 포인트의 aspects를 그대로 신뢰 — API가 최신 데이터를 배열 맨 앞에 반환함을 확인).

    ⚠️ BLS_API_KEY(등록키)가 있어야 이 메타데이터가 활성화된다 — 등록키 없이 catalog=true로
    요청하면 "catalog has been disabled for this request"로 거부됨을 실측 확인했다. 동일 데이터를
    등록키 없이 받을 수 있는 flat 파일 경로(download.bls.gov/pub/time.series/cu/)도 실측
    시도했으나 봇 정책상 자동 접근이 전부 403으로 차단되어(우회 시도 대신 정책 존중) API 경로만
    유일하게 동작한다. https://www.bls.gov/developers/ 에서 무료 등록키를 발급받아야 한다.
    """
    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        raise MacroDataError(
            "BLS_API_KEY가 설정되어 있지 않습니다. https://www.bls.gov/developers/ 에서 무료 "
            "등록키를 발급받아 .env에 추가하세요."
        )

    payload = {"seriesid": [bls_series_id], "registrationkey": api_key, "aspects": True}
    try:
        resp = requests.post(BLS_API_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise MacroDataError(f"BLS API 조회 중 오류가 발생했습니다: {e}") from e

    if data.get("status") != "REQUEST_SUCCEEDED":
        raise MacroDataError(f"BLS API 오류: {data.get('message')}")

    series_list = data.get("Results", {}).get("series", [])
    if not series_list:
        raise MacroDataError(f"BLS 시리즈 '{bls_series_id}' 응답에 데이터가 없습니다.")

    for point in series_list[0].get("data", []):
        for aspect in point.get("aspects", []):
            if aspect.get("name") == "Relative Importance":
                try:
                    as_of = f"{point['year']}-{point['period'].lstrip('M')}"
                    return float(aspect["value"]), as_of
                except (TypeError, ValueError, KeyError):
                    continue

    raise MacroDataError(
        f"BLS 시리즈 '{bls_series_id}'에서 상대중요도(Relative Importance) 메타데이터를 찾지 "
        "못했습니다 (등록키 권한 또는 API 응답 형식을 확인하세요)."
    )


def get_indicator_series(key: str) -> pd.Series:
    """카탈로그의 key로 지표 시계열을 조회하는 단일 진입점."""
    indicator = _find_indicator(key)

    ecos_params = indicator.get("ecos")
    if ecos_params:
        try:
            return get_ecos_series(**ecos_params)
        except MacroDataError:
            if "fred_id" not in indicator:
                raise  # FRED 폴백이 없는 ECOS 전용 지표 — 실패를 그대로 알린다
            # FRED 폴백이 있으면 계속 진행

    return get_fred_series(indicator["fred_id"])


def normalize_indexed(series: pd.Series, base: float = 100.0) -> pd.Series:
    """시계열을 첫 관측치를 base로 지수화한다 (예: "구간 시작=100 기준 상대 추이").

    단위가 서로 다른 지표(예: 실업률 %  vs M2 수십조 원)를 같은 차트에 겹쳐도 스케일이
    맞도록 오버레이 차트에서 쓴다. 절대 수준이 궁금하면 원계열(단일 지표 보기)을 봐야 한다.
    """
    if series.empty:
        raise MacroDataError("정규화할 시계열이 비어 있습니다.")
    first = series.iloc[0]
    if not first:
        raise MacroDataError("정규화 기준값(첫 관측치)이 0이라 지수화할 수 없습니다.")
    return series / first * base
