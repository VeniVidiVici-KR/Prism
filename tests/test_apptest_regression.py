"""4페이지(Stock Research/Screener/Comparison/Macro Indicator) AppTest 회귀.

그동안 라운드마다 스크래치패드의 임시 스크립트로 돌리던 걸 저장소에 고정한다.

두 티어로 나뉜다:
- 기본(빠른) 티어: "최초 진입"(아무 버튼도 안 누른 상태) 케이스. ⚠️ 과거 Comparison 페이지
  크래시가 정확히 이 케이스가 테스트에 없어서 놓친 것이었다 — 그래서 반드시 포함한다.
  버튼 클릭 시에만 조회하는 원칙 덕에 이 상태는 원래 네트워크가 필요 없다(Stock Research의
  시장 스냅샷만 예외라 그 부분만 mock).
- network 티어(@pytest.mark.network): 실제 티커를 조회하는 전체 플로우. 실제
  yfinance/DART/EDGAR/KRX/FRED/ECOS를 호출하므로 느리고 환경(KRX_ID/KRX_PW, 네트워크
  가용성)에 좌우된다 — 기본 `pytest` 실행에서는 제외된다(pytest.ini). 전체 포함 실행:
  `pytest -m network`.

⚠️ AI 호출(ai_commentary/edgar_summary/company_summary)은 이 파일 전체 어디서도 트리거하지
않는다 — "AI 요약/해설 생성하기" 버튼을 클릭하는 테스트가 하나도 없다(실제 API 호출·크레딧
소모 방지가 이 라운드의 명시적 요구사항이다).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

VIEWS_DIR = Path(__file__).resolve().parent.parent / "views"


def _load(page: str, timeout: int = 90) -> AppTest:
    at = AppTest.from_file(str(VIEWS_DIR / page), default_timeout=timeout)
    at.run()
    return at


# ── 최초 진입 (버튼 없음) — 기본 티어, 네트워크 없음 ─────────────────────────


def test_stock_research_initial_entry_no_click():
    """⚠️ 시장 스냅샷 섹션만 페이지 진입과 동시에 자동 조회되는 유일한 예외라(다른 모든
    조회는 버튼 클릭 시에만) mock한다 — 나머지 섹션은 '조회하기'를 눌러야 하므로 이 상태에서
    추가 네트워크를 타지 않는다."""
    with patch("valley.market_snapshot.get_market_snapshot", return_value=[]), \
         patch("valley.market_snapshot.get_us_market_state", return_value=None):
        at = _load("stock_research.py")
    assert at.exception == []
    assert at.title[0].value == "📈 PRISM"


def test_screener_initial_entry_no_click():
    at = _load("screener.py")
    assert at.exception == []


def test_comparison_initial_entry_no_click():
    """⚠️ 과거 Comparison 크래시가 정확히 이 케이스(최초 진입, 버튼 없음, Stock Research의
    세션 키가 남아있는 상태)에서 났다 — 이 테스트가 그 회귀를 막는 안전망이다."""
    at = _load("comparison.py")
    assert at.exception == []


def test_macro_indicator_initial_entry_no_click():
    at = _load("macro_indicator.py")
    assert at.exception == []


def test_macro_indicator_country_switch_no_click():
    """국가 라디오 전환은 버튼 클릭이 아니라 즉시 반영되는 조작이라 기본 티어에 포함한다
    (카탈로그 딕셔너리 조회만 있고 네트워크 조회는 없다)."""
    at = _load("macro_indicator.py")
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run()
    assert at.exception == []


def test_pages_do_not_crash_when_env_keys_missing(monkeypatch):
    """⚠️ CI 대비 — .env가 아예 없거나 키가 비어 있어도(ECOS/DART/OpenAI/Gemini/KRX 등)
    페이지가 죽지 않고 안내 메시지로 대체돼야 한다. 각 페이지는 load_dotenv(override=True)를
    다시 부르므로, dotenv 자체를 no-op으로 막아 실제 .env 파일이 있어도 무시되게 한다."""
    for var in ("ECOS_API_KEY", "DART_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "BLS_API_KEY", "KRX_ID", "KRX_PW"):
        monkeypatch.delenv(var, raising=False)
    with patch("dotenv.load_dotenv", return_value=True), \
         patch("valley.market_snapshot.get_market_snapshot", return_value=[]), \
         patch("valley.market_snapshot.get_us_market_state", return_value=None):
        for page in ("stock_research.py", "screener.py", "comparison.py", "macro_indicator.py"):
            at = _load(page)
            assert at.exception == [], f"{page}가 키 없는 환경에서 예외를 던짐"


# ── 전체 플로우 (실제 티커 조회) — network 티어 ──────────────────────────────


@pytest.mark.network
@pytest.mark.parametrize("ticker", ["AAPL", "005930", "295310", "493330"])
def test_stock_research_full_flow(ticker):
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input(ticker)
    next(b for b in at.button if b.label == "조회하기").click()
    at.run()
    assert at.exception == [], f"{ticker} 조회 중 예외: {at.exception}"


@pytest.mark.network
def test_stock_research_fcff_wacc_method_msft():
    """정식 DCF(FCFF→WACC) 경로도 회귀 대상에 포함한다 — MSFT는 이자비용이 정상 공시돼
    WACC<COE(부채 세금절감 효과)가 성립하는 대표 케이스다."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("MSFT")
    method_radio = next(r for r in at.radio if r.label == "밸류에이션 방식")
    method_radio.set_value("정식 (FCFF → WACC)")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run()
    assert at.exception == []
    wacc_metrics = [m for m in at.metric if m.label == "WACC"]
    assert wacc_metrics, "MSFT 정식 DCF에서 WACC 지표가 렌더링되지 않음"


@pytest.mark.network
def test_screener_full_flow():
    at = AppTest.from_file(str(VIEWS_DIR / "screener.py"), default_timeout=120)
    at.run()
    at.text_area[0].input("AAPL, 005930")
    next(b for b in at.button if b.label == "스크리닝 실행").click()
    at.run()
    assert at.exception == []


@pytest.mark.network
def test_comparison_full_flow():
    at = AppTest.from_file(str(VIEWS_DIR / "comparison.py"), default_timeout=120)
    at.run()
    next(b for b in at.button if b.label == "비교하기").click()
    at.run()
    assert at.exception == []


@pytest.mark.network
def test_macro_indicator_full_flow():
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    # 개별 지표 하나 조회
    gdp_btn = next(b for b in at.button if "실질 GDP" in b.label)
    gdp_btn.click()
    at.run()
    assert at.exception == []
    # CPI 구성항목 비교(버튼 클릭 시에만 조회되는 뷰)
    load_btn = next(b for b in at.button if b.label == "CPI 구성항목 비교 불러오기")
    load_btn.click()
    at.run()
    assert at.exception == []


@pytest.mark.network
def test_macro_indicator_employment_report_full_flow():
    """고용보고서 한눈에 보기 + 비농업고용 업종별 분해(둘 다 실제 FRED 조회)가 예외 없이
    렌더링되는지 확인한다 — 두 조사(사업체/가계) 구분 표기와 자기검증(11개 업종 합 vs
    헤드라인)이 이 라운드의 핵심 요구사항이라 데이터프레임에 관련 컬럼이 실제로 있는지도 본다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    load_btn = next(b for b in at.button if b.label == "고용보고서 불러오기")
    load_btn.click()
    at.run()
    assert at.exception == []
    headline_df = next(df for df in at.dataframe if "출처" in df.value.columns).value
    assert {"출처", "최근 값", "전월", "변화", "기준월"} <= set(headline_df.columns)
    sector_df = next(df for df in at.dataframe if "전월비 증감(천 명)" in df.value.columns).value
    assert len(sector_df) <= 11  # 정부 포함 11개 업종, USPRIV는 미포함이어야 한다
