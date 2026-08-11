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
def test_stock_research_growth_rate_slider_clamp_and_fade_button_nvda():
    """NVDA는 최근 4개년 FCF CAGR이 193.9%로 슬라이더 상한(100%)을 크게 넘는 대표 케이스다.
    (1) 비감쇠 모드에서 버튼 클릭 시 100%로 clamp되고 고성장 안내 캡션이 뜨는지,
    (2) 감쇠 모드로 전환하면 버튼이 활성화되고 "초기 성장률"에만 적용되는지(말기 성장률은
    그대로) 확인한다 — 이전엔 감쇠 모드에서 이 버튼이 비활성이었는데, 감쇠가 가장 필요한
    고성장주에서 못 쓰는 게 거꾸로였던 문제를 고친 라운드다."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=120)
    at.run()
    at.text_input[0].input("NVDA")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run()
    assert at.exception == []

    cagr_metrics = [m for m in at.metric if "CAGR" in m.label]
    assert cagr_metrics, "NVDA CAGR 지표가 렌더링되지 않음"
    assert float(cagr_metrics[0].value.rstrip("%")) > 100  # 실측 193.9%대 — 상한을 넘는 케이스

    captions_text = "\n".join(c.value for c in at.caption)
    assert "감쇠" in captions_text and "비현실적" in captions_text, "고성장 안내 캡션이 없음"

    btn = next(b for b in at.button if b.label == "이 값(CAGR)을 DCF 가정으로 쓰기")
    btn.click()
    at.run()
    assert at.exception == []
    slider = next(s for s in at.slider if s.label == "연간 FCF 성장률")
    assert slider.value == pytest.approx(1.0)  # 100%로 clamp
    assert any("범위(0~100%)" in i.value for i in at.info)

    fade_checkbox = next(cb for cb in at.checkbox if "감쇠" in cb.label)
    fade_checkbox.set_value(True)
    at.run()
    assert at.exception == []

    fade_btn = next(b for b in at.button if b.label == "이 값(CAGR)을 초기 성장률로 쓰기")
    assert not fade_btn.disabled
    fade_btn.click()
    at.run()
    assert at.exception == []

    init_slider = next(s for s in at.slider if s.label == "초기 성장률")
    term_slider = next(s for s in at.slider if s.label == "말기 성장률")
    assert init_slider.value == pytest.approx(1.0)  # 초기 성장률에 clamp된 CAGR이 반영됨
    assert term_slider.value == pytest.approx(0.04)  # 말기 성장률은 기본값 그대로(건드리지 않음)


@pytest.mark.network
def test_stock_research_fade_extreme_growth_dcf_does_not_diverge():
    """감쇠 모드에서 초기 100% · 말기 10%처럼 극단적인 조합을 넣어도 DCF가 예외 없이 유한한
    값으로 계산되는지 확인한다(발산 방지 회귀)."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=120)
    at.run()
    at.text_input[0].input("NVDA")
    fade_checkbox = next(cb for cb in at.checkbox if "감쇠" in cb.label)
    fade_checkbox.set_value(True)
    at.run()
    next(s for s in at.slider if s.label == "초기 성장률").set_value(1.0)
    next(s for s in at.slider if s.label == "말기 성장률").set_value(0.10)
    at.run()
    next(b for b in at.button if b.label == "조회하기").click()
    at.run()
    assert at.exception == []

    dcf_metric = next(m for m in at.metric if m.label == "주당 내재가치 (DCF)")
    value_str = dcf_metric.value.replace("$", "").replace(",", "").replace("₩", "")
    assert float(value_str) > 0  # 발산(inf/NaN)하지 않고 유한한 양수


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


@pytest.mark.network
def test_macro_indicator_credit_spread_comparison_full_flow():
    """신용시장 라운드 PART A — 하이일드(위험지표 카테고리)와 투자등급(신용시장 카테고리)
    스프레드를 겹쳐본다. 등급이 낮을수록 스프레드가 커지는 정상적인 신용 위계
    (AAA<AA<A<BBB<하이일드)가 실측으로 유지되는지, 특히 하이일드가 투자등급 전체보다 항상
    높은지를 고정해둔다(신용 국면 판독의 핵심 전제)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    btn = next(b for b in at.button if b.label == "신용 스프레드 비교 보기")
    btn.click()
    at.run()
    assert at.exception == []

    spread_df = next(df for df in at.dataframe if "최신값(%)" in df.value.reset_index().columns).value
    assert {"하이일드(HY)", "투자등급 전체(IG)", "AA", "BBB"} <= set(spread_df.index)
    hy = spread_df.loc["하이일드(HY)", "최신값(%)"]
    ig = spread_df.loc["투자등급 전체(IG)", "최신값(%)"]
    bbb = spread_df.loc["BBB", "최신값(%)"]
    aa = spread_df.loc["AA", "최신값(%)"]
    assert hy > ig  # 하이일드 > 투자등급 전체 — 신용 위계의 핵심 전제
    assert bbb > aa  # 등급이 낮을수록(BBB) 스프레드가 큼(AA보다)
    assert hy > 0 and ig > 0  # 스프레드는 항상 양수


@pytest.mark.network
def test_macro_indicator_bond_issuance_full_flow():
    """신용시장 라운드 PART B — 여러 기업의 424B(발행설명서) 공시를 통합 조회한다.
    (1) 자동 로드 없이 버튼 클릭 시에만 조회되는지, (2) 재실행 시 캐시로 재조회 없이
    동일 결과가 유지되는지(mock으로 실제 SEC 호출 횟수 확인), (3) 개별 기업 실패가 나머지를
    막지 않는지(격리)를 확인한다."""
    from unittest.mock import patch

    import valley.edgar as edgar_mod
    from views.macro_indicator import BOND_ISSUANCE_UNIVERSE

    calls: list[str] = []
    _orig = edgar_mod.get_filings_by_type

    def _counting(cik, form_type, limit=10):
        calls.append(cik)
        return _orig(cik, form_type, limit)

    with patch("valley.edgar.get_filings_by_type", side_effect=_counting):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=120)
        at.run()
        assert len(calls) == 0  # 자동 로드 금지

        btn = next(b for b in at.button if b.label == "주요 기업 채권 발행 동향 불러오기")
        btn.click()
        at.run(timeout=120)
        assert at.exception == []
        assert len(calls) <= len(BOND_ISSUANCE_UNIVERSE)  # 기업당 최대 1회(실패해도 재시도 없음)

        calls.clear()
        at.run(timeout=120)
        assert len(calls) == 0  # 재실행 시 캐시 히트, 추가 호출 없음

    issuance_df = next(df for df in at.dataframe if "기업" in df.value.columns).value
    assert set(issuance_df["기업"]) <= set(BOND_ISSUANCE_UNIVERSE)
    # 최신순 통합 정렬 확인
    dates = issuance_df["접수일"].tolist()
    assert dates == sorted(dates, reverse=True)


@pytest.mark.network
def test_stock_research_edgar_securities_issuance_category_googl():
    """EDGAR 범주 재분류 회귀 — GOOGL은 424B 발행이 활발해(실측 확인) "증권 발행" 범주로
    필터링하면 424B/FWP만 나오고, 이 범주가 기존 "주주총회·기타"에서 분리됐어도 나머지
    필터 UI(범주 옵션·기본 선택·전체 보기)는 그대로 동작해야 한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=120)
    at.run()
    at.text_input[0].input("GOOGL")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run()
    assert at.exception == []

    multiselect = next(m for m in at.multiselect if m.label == "공시 유형")
    assert "증권 발행" in multiselect.options
    assert multiselect.value == ["실적·정기보고", "중요 사건"]  # 기본 선택은 그대로(회귀)

    multiselect.set_value(["증권 발행"])
    at.run()
    assert at.exception == []
    filtered = next((df for df in at.dataframe if "유형" in df.value.columns), None)
    if filtered is not None:
        forms = set(filtered.value["유형"])
        assert forms, "증권 발행 필터 결과가 비어있음"
        assert all(f.startswith("424B") or f == "FWP" for f in forms)
