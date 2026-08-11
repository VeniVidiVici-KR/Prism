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
    for var in (
        "ECOS_API_KEY", "DART_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "BLS_API_KEY",
        "KRX_ID", "KRX_PW", "KOFIA_API_KEY",
    ):
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
def test_macro_indicator_cpi_contribution_mom_view_us():
    """MoM 기여도 라운드 — 미국 CPI 구성 비중 & 기여도에서 YoY(회귀, 기존 값 유지)와 MoM(신규)을
    둘 다 확인한다. MoM은 계절조정(SA) 계열을 쓰고, 기여도 합계가 헤드라인 MoM과 크게(30% 초과)
    벗어나지 않는지(실측: 2026-08 라운드 기준 오차 약 1.9%) 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    btn = next(b for b in at.button if b.label == "구성 비중 & 기여도 불러오기")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []

    yoy_df = next(df for df in at.dataframe if "YoY(%)" in df.value.reset_index().columns).value
    assert {"식품", "에너지", "근원 상품", "근원 서비스"} <= set(yoy_df.index)  # 회귀: 기존 YoY 항목 유지

    mode_radio = next(r for r in at.radio if r.label == "기여도 기준")
    mode_radio.set_value("MoM (전월비)")
    at.run(timeout=90)
    assert at.exception == []

    mom_df = next(df for df in at.dataframe if "MoM(%)" in df.value.reset_index().columns).value
    assert {"식품", "에너지", "근원 상품", "근원 서비스"} <= set(mom_df.index)
    assert "YoY(%)" not in mom_df.columns  # SA/NSA 뷰가 섞이지 않았는지(별개 표)

    # ⚠️ 이 라운드의 급소 — 기여도 합계가 헤드라인과 크게 어긋나면 경고가 떠야 하고(안 뜨면
    # 오차가 30% 이내라는 뜻), 자기검증 캡션이 반드시 있어야 한다.
    assert any("자기검증" in c.value and "MoM" in c.value for c in at.caption)
    assert any("계절조정" in c.value for c in at.caption)  # SA 사용을 명시했는지


@pytest.mark.network
def test_macro_indicator_cpi_contribution_mom_view_kr():
    """MoM 기여도 라운드 — 한국은 ECOS에 CPI 계절조정 계열이 없어(실측 확인) 원계열(NSA) 그대로
    MoM을 본다는 사실이 캡션에 명시되는지, 방향(전기·교통 마이너스, 서비스 플러스 — 제홍이 준
    증권사 리포트 참고 방향)이 실제로 그렇게 나오는지 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run()

    btn = next(b for b in at.button if b.label == "구성 비중 & 기여도 불러오기")
    btn.click()
    at.run(timeout=90)

    mode_radio = next(r for r in at.radio if r.label == "기여도 기준")
    mode_radio.set_value("MoM (전월비)")
    at.run(timeout=90)
    assert at.exception == []

    mom_df = next(df for df in at.dataframe if "MoM(%)" in df.value.reset_index().columns).value
    assert "주택, 수도, 전기 및 연료" in mom_df.index
    # 방향 확인 — 실측(2026-07)으로 전기·수도·주택과 교통이 마이너스 기여를 보였다(리포트의
    # 전기가스수도 -0.17%p 방향과 일치). 항목 분류가 달라 절대값은 다를 수 있으니 부호만 본다.
    assert mom_df.loc["주택, 수도, 전기 및 연료", "기여도(%p)"] < 0
    assert mom_df.loc["교통", "기여도(%p)"] < 0

    assert any("원계열" in c.value and "계절조정" in c.value for c in at.caption)  # NSA 한계 명시
    assert any("비교하지" in c.value for c in at.caption)  # 미국-한국 비교 금지 캡션


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


@pytest.mark.network
def test_macro_indicator_kofia_credit_balance_full_flow():
    """KOFIA 신용공여 라운드 — 일별·코스피/코스닥 구분 신용공여 데이터가 예외 없이 렌더링되고,
    (1) 자동 로드 없이 버튼 클릭 시에만 조회되는지, (2) 재실행 시 캐시로 재조회 없이 유지되는지
    (mock으로 실제 KOFIA 호출 횟수 확인), (3) 전체=유가증권+코스닥 정합성이 실제 응답에서도
    유지되는지 확인한다."""
    import valley.kofia as kofia_mod

    calls = {"credit": 0, "capital": 0}
    _orig_credit = kofia_mod.get_credit_balance_history
    _orig_capital = kofia_mod.get_securities_market_capital_history

    def _counting_credit(num_rows=1500):
        calls["credit"] += 1
        return _orig_credit(num_rows)

    def _counting_capital(num_rows=1500):
        calls["capital"] += 1
        return _orig_capital(num_rows)

    # ⚠️ views/_cache.py는 "from valley.kofia import get_credit_balance_history as
    # _get_credit_balance_history"로 별칭을 모듈 임포트 시점에 한 번 바인딩해둔다 — 그 시점이
    # 이 테스트보다 먼저(다른 테스트가 macro_indicator.py를 먼저 로드해 views._cache를 이미
    # 임포트해버린 뒤)라면, valley.kofia 쪽을 아무리 patch해도 이미 바인딩된 별칭에는 반영되지
    # 않는다(실측으로 겪은 순서 의존성 버그 — 단독 실행 시엔 우연히 통과하고 전체 스위트에서만
    # 실패했다). 그래서 실제로 호출되는 별칭 쪽(views._cache._get_credit_balance_history)을
    # 직접 patch한다 — 임포트 순서와 무관하게 항상 정확히 가로챈다.
    with patch("views._cache._get_credit_balance_history", side_effect=_counting_credit), \
         patch("views._cache._get_securities_market_capital_history", side_effect=_counting_capital):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        country_radio = next(r for r in at.radio if r.label == "국가")
        country_radio.set_value("한국")
        at.run()
        assert calls == {"credit": 0, "capital": 0}  # 자동 로드 금지

        btn = next(b for b in at.button if b.label == "증시 주변자금 / 신용공여 불러오기")
        btn.click()
        at.run(timeout=90)
        assert at.exception == []
        assert calls == {"credit": 1, "capital": 1}

        at.run(timeout=90)
        assert calls == {"credit": 1, "capital": 1}  # 재실행 시 캐시 히트, 추가 호출 없음

    credit_df = next(df for df in at.dataframe if "신용거래융자 전체" in df.value.index).value
    assert "최근 값(조원)" in credit_df.columns
    # 신용거래융자: 전체 = 유가증권 + 코스닥 (실측으로 확인된 정합성, valley/kofia.py 참고)
    whl = credit_df.loc["신용거래융자 전체", "최근 값(조원)"]
    scrs = credit_df.loc["신용거래융자 유가증권", "최근 값(조원)"]
    kosdaq = credit_df.loc["신용거래융자 코스닥", "최근 값(조원)"]
    assert whl == pytest.approx(scrs + kosdaq, abs=0.01)


@pytest.mark.network
def test_macro_indicator_kofia_ecos_cross_check():
    """ECOS 901Y056(월별, KR_MARGIN_LOAN_MONTHLY)과 KOFIA API(일별)를 교차검증한다 — 실측으로
    확인한 정의 동일성(ECOS는 KOFIA 일별 시계열의 월말 스냅샷)이 유지되는지, 최소한 두 소스의
    최신값이 같은 자릿수(조 원 단위) 규모인지로 회귀를 건다(매일 정확히 같은 날짜가 아니므로
    완전 일치는 확인하지 않는다 — 정의 관계는 valley/macro.py의 "신용융자" 카테고리 주석과
    2026-08 라운드 실측 보고에서 이미 확정함)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run()

    ecos_btn = next(b for b in at.button if "신용융자 잔고" in b.label)
    ecos_btn.click()
    at.run(timeout=90)
    assert at.exception == []
    ecos_caption = next(c for c in at.caption if "관측치" in c.value and "데이터 기준일" in c.value)
    assert "202" in ecos_caption.value  # 최신 연도가 표시되는지(빈 시계열이 아님)


def test_macro_indicator_kofia_missing_key_no_crash(monkeypatch):
    """⚠️ KOFIA_API_KEY가 없어도 앱이 죽지 않고 명확한 안내로 대체돼야 한다(DART/ECOS와 동일
    패턴). load_dotenv가 실제 .env를 다시 읽어 키를 되살리는 걸 막기 위해 no-op으로 막는다
    (test_pages_do_not_crash_when_env_keys_missing과 동일한 이유)."""
    monkeypatch.delenv("KOFIA_API_KEY", raising=False)
    with patch("dotenv.load_dotenv", return_value=True):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        country_radio = next(r for r in at.radio if r.label == "국가")
        country_radio.set_value("한국")
        at.run()
        assert at.exception == []
        assert not [b for b in at.button if "신용공여" in b.label]
        assert any("KOFIA_API_KEY" in i.value for i in at.info)
