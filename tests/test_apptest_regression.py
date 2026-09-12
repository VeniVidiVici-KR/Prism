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
    # ⚠️ 디자인 라운드(2026-08)에서 섹션 제목의 이모지 아이콘을 일괄 제거했다(전문 대시보드
    # 인상 — 기존엔 13개 헤더 중 일부만 이모지가 있어 이미 불일치했음). 회귀가 아니라 의도된
    # 변경이라 새 문구로 갱신한다.
    assert at.title[0].value == "PRISM"


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
        "KRX_ID", "KRX_PW", "KOFIA_API_KEY", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
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
def test_macro_indicator_price_category_reorg_structure():
    """물가 재편 라운드(2026-08) — 미국은 4개 물가 카테고리(물가/물가 구성/기저 인플레/
    물가 파급)가 서로 인접하게 나오고, 한국은 항목이 적은 카테고리(기저 인플레/물가 파급)를
    만들지 않아 2개(물가/물가 구성)만 있어야 한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    assert at.exception == []

    us_subheaders = [s.value for s in at.subheader]
    price_categories = [s for s in us_subheaders if s in ("물가", "물가 구성", "기저 인플레", "물가 파급")]
    assert price_categories == ["물가", "물가 구성", "기저 인플레", "물가 파급"]  # 순서까지 인접

    us_labels = [b.label for b in at.button]
    assert "생산자물가지수(PPI)" in us_labels  # "물가"에서 "물가 파급"으로 이동, 사라지지 않음
    assert "실질 평균 시간당 임금" in us_labels  # 신규 추가

    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)
    assert at.exception == []
    kr_subheaders = [s.value for s in at.subheader]
    assert "물가" in kr_subheaders and "물가 구성" in kr_subheaders
    assert "기저 인플레" not in kr_subheaders  # 빈 카테고리를 만들지 않음
    assert "물가 파급" not in kr_subheaders


@pytest.mark.network
def test_macro_indicator_new_derived_rate_composites_render():
    """물가 재편 라운드 STEP1 — 근원 CPI MoM(이 라운드가 명시적으로 지적한 빠진 지표) 등
    새 파생지표 7종 + 실질임금 YoY가 혼합지표 섹션에서 정상 렌더링되는지, 값이 상식적
    범위인지(CPI류 YoY 2~5%대, MoM -1~1%대) 확인한다. 혼합지표가 3개→11개로 늘어난 뒤에도
    버튼 줄바꿈이 깨지지 않는지(레이아웃 회귀)도 함께 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    composite_labels = {b.label for b in at.button if b.key and b.key.startswith("btn_composite_")}
    expected = {
        "근원 CPI 상승률 (전년동월대비 %)", "근원 CPI 상승률 (전월대비 %)",
        "CPI 상승률 (전월대비 %)", "PCE 상승률 (전년동월대비 %)", "PCE 상승률 (전월대비 %)",
        "근원 PCE 상승률 (전년동월대비 %)", "근원 PCE 상승률 (전월대비 %)",
        "실질임금 상승률 (전년동월대비 %)",
    }
    assert expected <= composite_labels

    btn = next(b for b in at.button if b.label == "근원 CPI 상승률 (전월대비 %)")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []
    assert any("근원 CPI 상승률 (전월대비 %)" in m.value for m in at.markdown)

    btn2 = next(b for b in at.button if b.label == "실질임금 상승률 (전년동월대비 %)")
    btn2.click()
    at.run(timeout=90)
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
    # ⚠️ 원래 여기서 "주택·수도·전기와 교통이 마이너스 기여"라는 부호를 고정했는데(2026-07
    # 실측 기준), MoM 부호는 달마다 정상적으로 뒤집힌다 — 실제로 2026-08엔 주택·수도·전기가
    # +0.256%(기여 +0.044%p)로 바뀌어 이 단정이 깨졌다(중분류 라운드 전 HEAD에서도 동일하게
    # 실패함을 확인). 특정 달의 부호가 아니라 "구조가 성립하는지"를 검증하도록 바꾼다.
    assert len(mom_df) == 12  # ECOS 지출목적별 12개 대분류
    assert mom_df["기여도(%p)"].notna().all()
    assert 99.0 < mom_df["가중치(%)"].sum() < 101.0  # 1000분비 → % 환산이 100에 수렴
    # 기여도 ≈ 가중치 × 변화율 관계가 실제로 성립하는지(표가 엉뚱한 열을 보여주지 않는지).
    recomputed = mom_df["가중치(%)"] / 100 * mom_df["MoM(%)"]
    assert (recomputed - mom_df["기여도(%p)"]).abs().max() < 1e-6
    assert any("원계열" in c.value and "계절조정" in c.value for c in at.caption)  # NSA 한계 명시
    assert any("비교하지" in c.value for c in at.caption)  # 미국-한국 비교 금지 캡션


@pytest.mark.network
def test_macro_indicator_cpi_midlevel_breakdown_us():
    """CPI 중분류 라운드(2026-09) — 4분할 아래에 중분류(2단계) 분해가 붙는지 확인한다.
    (1) 버튼을 누르기 전엔 안 뜨고(자동 조회 금지), (2) 누르면 대분류 4개가 각각 expander로
    펼쳐지며, (3) 표의 열 구성이 기존 4분할 표와 동일해(4열) 좁은 화면 잘림 위험을 늘리지
    않고, (4) 계층 검증(자식 합 vs 부모) 캡션이 가중치·기여도 양쪽으로 표시되는지 본다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=180)
    at.run(timeout=180)
    next(b for b in at.button if b.label == "구성 비중 & 기여도 불러오기").click()
    at.run(timeout=180)
    assert at.exception == []

    # 4분할만 로드된 상태 — 중분류는 아직 조회되지 않아야 한다(비용 통제).
    assert not any("계층 검증" in c.value for c in at.caption)
    midlevel_btn = next(b for b in at.button if b.label == "중분류까지 분해하기")

    midlevel_btn.click()
    at.run(timeout=180)
    assert at.exception == []

    # 대분류 4개 각각의 중분류 묶음(expander)
    assert len(at.expander) == 4

    # ⚠️ 표 열 구성이 기존 4분할 표와 같아야 한다(열을 늘려 좁은 화면에서 잘리게 만들지 않음).
    yoy_tables = [df.value for df in at.dataframe if "YoY(%)" in df.value.reset_index().columns]
    assert len(yoy_tables) == 5  # 대분류 표 1개 + 중분류 표 4개
    for table in yoy_tables:
        assert list(table.reset_index().columns) == ["항목", "가중치(%)", "YoY(%)", "기여도(%p)"]
    # 식품(2) 에너지(2) 근원상품(8) 근원서비스(7) — 대분류 4행과 함께
    assert sorted(len(t) for t in yoy_tables) == [2, 2, 4, 7, 8]

    weight_checks = [c.value for c in at.caption if "계층 검증(가중치)" in c.value]
    contrib_checks = [c.value for c in at.caption if "계층 검증(기여도)" in c.value]
    assert len(weight_checks) == 4 and len(contrib_checks) == 4

    # 값이 없어 뺀 중분류(가사서비스)는 이유와 함께 화면에 드러나야 한다 — 근원 서비스의
    # 가중치 차이(-0.92%p)가 설명 없이 남지 않도록.
    assert any("가사서비스" in c.value and "제외된 중분류" in c.value for c in at.caption)


@pytest.mark.network
def test_macro_indicator_cpi_midlevel_weight_sums_match_parents():
    """⚠️ 이 라운드의 급소 — 중분류 가중치 합이 대분류와 맞는지(실측: 식품·에너지·근원상품은
    반올림 오차 0.01%p 이내, 근원 서비스만 가사서비스 미공표로 약 0.92%p 부족)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=180)
    at.run(timeout=180)
    next(b for b in at.button if b.label == "구성 비중 & 기여도 불러오기").click()
    at.run(timeout=180)
    next(b for b in at.button if b.label == "중분류까지 분해하기").click()
    at.run(timeout=180)
    assert at.exception == []

    weight_checks = [c.value for c in at.caption if "계층 검증(가중치)" in c.value]
    diffs = [float(c.rsplit("차이 ", 1)[1].replace("%p", "")) for c in weight_checks]
    small, big = [d for d in diffs if abs(d) < 0.05], [d for d in diffs if abs(d) >= 0.05]
    assert len(small) == 3, weight_checks  # 식품·에너지·근원 상품
    assert len(big) == 1 and -1.0 < big[0] < -0.8, weight_checks  # 근원 서비스(가사서비스 결측)


@pytest.mark.network
def test_macro_indicator_cpi_headline_metric_shown_top():
    """헤드라인 표시 라운드(2026-08) — 이전엔 헤드라인 CPI 변화율이 맨 아래 자기검증 캡션에만
    작은 글씨로 있었다. YoY/MoM 두 모드 모두 표 위에 st.metric으로 먼저 보이는지 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    btn = next(b for b in at.button if b.label == "구성 비중 & 기여도 불러오기")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []

    yoy_metric = next(m for m in at.metric if m.label == "헤드라인 CPI YoY")
    assert yoy_metric.value.endswith("%")

    mode_radio = next(r for r in at.radio if r.label == "기여도 기준")
    mode_radio.set_value("MoM (전월비)")
    at.run(timeout=90)
    assert at.exception == []

    mom_metric = next(m for m in at.metric if m.label == "헤드라인 CPI MoM")
    assert mom_metric.value.endswith("%")


@pytest.mark.network
def test_macro_indicator_growth_consumption_category_expanded():
    """성장·소비 변화율 라운드(2026-08) — GDP 실질·명목 QoQ(연율) 2개가 "성장·소비" 카테고리에
    직접 추가돼(8개 버튼) 배치가 깨지지 않는지, 실제로 클릭했을 때 예외 없이 렌더링되는지
    확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    growth_labels = {b.label for b in at.button if b.key and b.key.startswith("btn_US_")}
    assert {
        "실질 GDP", "실질 GDP 성장률 (전분기비 연율%)", "명목 GDP", "명목 GDP 성장률 (전분기비 연율%)",
        "소매판매", "소비자심리지수(미시간대)", "주택착공건수",
    } <= growth_labels

    btn = next(b for b in at.button if b.label == "실질 GDP 성장률 (전분기비 연율%)")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []
    assert any("실질 GDP 성장률 (전분기비 연율%)" in m.value for m in at.markdown)


@pytest.mark.network
def test_macro_indicator_growth_consumption_composites_render():
    """성장·소비 변화율 라운드(2026-08) — GDP YoY(분기)·소매판매 MoM/YoY·주택착공 3개월
    이동평균(미국), CPI MoM·GDP QoQ/YoY(한국)가 혼합 지표 섹션에 뜨고 클릭 시 예외가 없는지,
    값이 상식 범위인지(소매판매 MoM 대략 -1~+1%대, YoY 2~5%대) 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    composite_labels = {b.label for b in at.button if b.key and b.key.startswith("btn_composite_")}
    expected_us = {
        "실질 GDP 성장률 (전년동기비%)", "명목 GDP 성장률 (전년동기비%)",
        "소매판매 (전월비%)", "소매판매 (전년동월비%)", "주택착공건수 (3개월 이동평균)",
    }
    assert expected_us <= composite_labels

    rsafs_mom_btn = next(b for b in at.button if b.label == "소매판매 (전월비%)")
    rsafs_mom_btn.click()
    at.run(timeout=90)
    assert at.exception == []

    houst_btn = next(b for b in at.button if b.label == "주택착공건수 (3개월 이동평균)")
    houst_btn.click()
    at.run(timeout=90)
    assert at.exception == []

    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)
    kr_composite_labels = {b.label for b in at.button if b.key and b.key.startswith("btn_composite_")}
    assert {"CPI 상승률 (전월대비 %)", "실질 GDP 성장률 (전분기비%)", "실질 GDP 성장률 (전년동기비%)"} <= kr_composite_labels

    kr_cpi_mom_btn = next(b for b in at.button if b.label == "CPI 상승률 (전월대비 %)")
    kr_cpi_mom_btn.click()
    at.run(timeout=90)
    assert at.exception == []


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
def test_macro_indicator_underlying_inflation_indicator_buttons():
    """기저 인플레(절사평균/중위) 라운드 — 카탈로그에 추가한 6개 버튼(12개월/1개월연율 x
    Trimmed Mean PCE·Median CPI·Trimmed-Mean CPI)이 예외 없이 렌더링되고, 이미 FRED가
    변화율(%)로 제공하는 계열이라 뷰가 yoy_percent()·pct_change() 등으로 이중 계산하지
    않는지(단일 지표 보기는 원 시리즈를 그대로 그린다) 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    btn = next(b for b in at.button if b.label == "절사평균 CPI (1개월연율)")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []

    assert any("절사평균 CPI (1개월연율)" in m.value for m in at.markdown)
    # ⚠️ 물가 재편 라운드(2026-08)에서 description을 "한 문장으로 읽히게" 정돈하며 영문
    # "16% Trimmed-Mean CPI"를 "16% 절사평균 CPI"로 바꿨다(라벨 정리 STEP3) — 회귀가 아니라
    # 의도된 문구 변경이라 이 테스트도 새 문구로 갱신한다.
    assert any(
        "16% 절사평균 CPI" in c.value and "계절조정" in c.value and "비교하지" in c.value
        for c in at.caption
    )


@pytest.mark.network
def test_macro_indicator_underlying_inflation_comparison_full_flow():
    """기저 인플레 비교 뷰 — 헤드라인/근원 CPI·PCE(지수에서 yoy_percent로 계산)와 절사평균/
    중위(FRED가 이미 12개월 변화율로 제공, 그대로 사용) 6개를 한 표·차트에 겹친다.
    ⚠️ 핵심 회귀: 최근 값들이 전부 상식적 범위(2~5%대)에 들어와야 한다 — 계열을 잘못
    집었거나(예: 1개월 연율판을 실수로 넣음) 이중 계산했다면 이 범위를 벗어난다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    btn = next(b for b in at.button if b.label == "기저 인플레 비교 보기")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []

    df = next(df for df in at.dataframe if "최신값(%)" in df.value.reset_index().columns).value
    expected_rows = {
        "헤드라인 CPI (YoY)", "근원 CPI (YoY)", "근원 PCE (YoY)",
        "절사평균 PCE (12개월)", "중위 CPI (12개월)", "절사평균 CPI (12개월)",
    }
    assert expected_rows <= set(df.index)
    for label in expected_rows:
        value = df.loc[label, "최신값(%)"]
        assert 1.0 < value < 6.0, f"{label}={value}가 상식적 범위(1~6%)를 벗어났습니다."

    assert any("1개월" in c.value and "제외" in c.value for c in at.caption)  # 시계 통일 캡션
    assert any("정규화하지 않았습니다" in c.value for c in at.caption)


@pytest.mark.network
def test_macro_indicator_underlying_inflation_in_overlay_options():
    """오버레이(지표 겹쳐보기) 자동 포함 확인 — 새 카탈로그 항목은 별도 등록 없이
    INDICATORS["US"]를 순회하는 기존 로직만으로 겹쳐보기 후보에 자동으로 들어와야 한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    assert at.exception == []

    overlay_ms = next(m for m in at.multiselect if m.label.startswith("겹쳐볼 지표"))
    expected_labels = {
        "절사평균 PCE (12개월)", "절사평균 PCE (1개월연율)",
        "중위 CPI (12개월)", "중위 CPI (1개월연율)",
        "절사평균 CPI (12개월)", "절사평균 CPI (1개월연율)",
    }
    assert expected_labels <= set(overlay_ms.options)


@pytest.mark.network
def test_macro_indicator_kr_bond_catalog_buttons_and_fred_relabel():
    """국채 라운드(2026-08) — 한국 '금리' 카테고리에 기존 FRED 월별(KR_DGS10)과 새 토스 일별
    6종이 함께 있어야 하고, 둘 다 정상 동작해야 한다(회귀 없음). ⚠️ 물가 재편 라운드(2026-08)
    에서 라벨의 출처 표기("FRED"/"토스")를 뺐다 — 10년물만 (월별)/(일별) 구분자를 남기고
    (KR_DGS10과 KR_BOND_10Y가 같은 "국고채 10년"이라 구분이 필요), 나머지 5개 만기는
    다른 항목과 안 겹쳐 완전히 clean한 라벨("국고채 2년" 등)로 바뀌었다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)

    labels = [b.label for b in at.button]
    assert "국고채 10년 (월별)" in labels  # 기존 KR_DGS10, 라벨 재정리(회귀 아님)
    assert "국고채 10년 (일별)" in labels  # KR_BOND_10Y
    for maturity in ["2년", "3년", "5년", "20년", "30년"]:
        assert f"국고채 {maturity}" in labels  # 겹치는 항목이 없어 구분자 없이 clean

    # 새 토스 일별 버튼 클릭 — 정상 렌더링
    next(b for b in at.button if b.label == "국고채 10년 (일별)").click()
    at.run(timeout=90)
    assert at.exception == []
    assert any("국고채 10년 (일별)" in m.value for m in at.markdown)

    # 기존 FRED 월별 버튼도 여전히 정상 동작(회귀 확인)
    next(b for b in at.button if b.label == "국고채 10년 (월별)").click()
    at.run(timeout=90)
    assert at.exception == []
    assert any("국고채 10년 (월별)" in m.value for m in at.markdown)


@pytest.mark.network
def test_macro_indicator_kr_yield_curve_full_flow():
    """수익률곡선 스냅샷 — 6개 만기가 순서대로 나오고, 상식적인 범위(1~10%)이며, 우상향/역전
    판정이 뜨는지 확인한다. ⚠️ 카드 레이아웃 라운드(2026-08)에서 이 판정을 맨 아래 캡션에서
    카드 상단 st.metric으로 옮겼다(값 자체는 동일, 표시 위치만 변경) — 의도된 변경."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)

    next(b for b in at.button if b.label == "수익률곡선 불러오기").click()
    at.run(timeout=90)
    assert at.exception == []

    curve_df = next(df for df in at.dataframe if "수익률(%)" in df.value.reset_index().columns).value
    assert list(curve_df.index) == ["2Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
    assert (curve_df["수익률(%)"] > 1.0).all() and (curve_df["수익률(%)"] < 10.0).all()

    assert any(m.label == "곡선 형태 (10Y-2Y)" for m in at.metric)
    assert any("기준 시각" in c.value and "timestamp" in c.value for c in at.caption)


@pytest.mark.network
def test_macro_indicator_kr_yield_spread_composite_buttons():
    """국채 라운드 STEP 2-1 — 새 혼합지표 3종(국채10Y-2Y, 10Y-3Y, 한미 국고채10년 금리차)이
    기존 한미 정책금리차와 함께 렌더링되고 정상 클릭되는지 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)

    labels = [b.label for b in at.button]
    assert "국채 장단기 금리차 (10Y-2Y)" in labels
    assert "국채 장단기 금리차 (10Y-3Y)" in labels
    assert "한미 금리차 (국고채 10년)" in labels
    assert "한미 정책금리차 (미국 - 한국)" in labels  # 기존 지표, 대체되지 않음(회귀 확인)

    next(b for b in at.button if "10Y-2Y" in b.label).click()
    at.run(timeout=90)
    assert at.exception == []


def test_macro_indicator_kr_bond_missing_key_hides_load_button(monkeypatch):
    """토스 키가 없으면 수익률곡선 섹션은 안내만 뜨고 로드 버튼이 없어야 하며, 앱이 죽지
    않아야 한다(fast tier — 네트워크 불필요)."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "")
    with patch("dotenv.load_dotenv", return_value=True):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        country_radio = next(r for r in at.radio if r.label == "국가")
        country_radio.set_value("한국")
        at.run(timeout=90)
    assert at.exception == []
    assert any("TOSS_CLIENT_ID" in i.value for i in at.info)
    assert next((b for b in at.button if b.label == "수익률곡선 불러오기"), None) is None


@pytest.mark.network
def test_stock_research_coe_source_label_toss_vs_fred_fallback():
    """국채 라운드 STEP 3 — COE 자동제안 안내 문구에 소스가 명시돼야 한다: 토스 키가 있으면
    "토스 일별", 없으면 "FRED 월별 폴백"으로 자동 전환되고 값도 계속 계산돼야 한다(앱이
    죽지 않음)."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("005930")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run(timeout=90)
    checkbox = next(c for c in at.checkbox if "COE 자동제안" in c.label)
    checkbox.set_value(True)
    at.run(timeout=90)
    assert at.exception == []
    assert any("토스 일별" in i.value for i in at.info if "무위험이자율" in i.value)

    # ⚠️ valley.toss._token_cache는 프로세스 전역 싱글턴이라, 위 at에서 이미 발급받은 토큰이
    # 남아있으면 아래에서 TOSS_CLIENT_ID를 지워도 재발급을 시도하지 않고 캐시된 토큰을 그대로
    # 재사용해버려 폴백이 트리거되지 않는다 — 강제로 무효화해 실제로 빈 키로 재발급을
    # 시도하게 만든다.
    import valley.toss as toss_mod

    toss_mod._token_cache = toss_mod._TokenCache()

    with patch.dict("os.environ", {"TOSS_CLIENT_ID": "", "TOSS_CLIENT_SECRET": ""}), \
         patch("dotenv.load_dotenv", return_value=True):
        at2 = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
        at2.run()
        at2.text_input[0].input("005930")
        next(b for b in at2.button if b.label == "조회하기").click()
        at2.run(timeout=90)
        checkbox2 = next(c for c in at2.checkbox if "COE 자동제안" in c.label)
        checkbox2.set_value(True)
        at2.run(timeout=90)
    toss_mod._token_cache = toss_mod._TokenCache()  # 다른 테스트에 영향 없게 복구
    assert at2.exception == []
    assert any("FRED 월별 폴백" in i.value for i in at2.info if "무위험이자율" in i.value)


@pytest.mark.network
def test_macro_indicator_bond_issuance_full_flow():
    """신용시장 라운드 PART B — 여러 기업의 424B(발행설명서) 공시를 통합 조회한다.
    (1) 자동 로드 없이 버튼 클릭 시에만 조회되는지, (2) 재실행 시 캐시로 재조회 없이
    동일 결과가 유지되는지(mock으로 실제 SEC 호출 횟수 확인), (3) 개별 기업 실패가 나머지를
    막지 않는지(격리)를 확인한다."""
    from unittest.mock import patch

    import valley.edgar as edgar_mod
    from views.macro.credit_bonds import BOND_ISSUANCE_UNIVERSE

    calls: list[str] = []
    _orig = edgar_mod.get_filings_by_type

    def _counting(cik, form_type, limit=10):
        calls.append(cik)
        return _orig(cik, form_type, limit)

    with patch("valley.edgar.get_filings_by_type", side_effect=_counting):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=120)
        at.run()
        assert len(calls) == 0  # 자동 로드 금지

        # ⚠️ 424B 요약/티커 직접 조회 라운드(2026-08)에서 "주요 기업 한눈에 보기 불러오기"로
        # 개명됐다(9개 기업 통합 뷰를 티커 직접 조회의 보조 버튼으로 재배치) — 의도된 변경.
        btn = next(b for b in at.button if b.label == "주요 기업 한눈에 보기 불러오기")
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


# ── 424B 티커 직접 조회 라운드(2026-08) ──────────────────────────────────────
# ⚠️ 이 구간도 파일 상단 정책(AI 요약 버튼 클릭 금지)을 지킨다 — 요약 버튼의 "존재 여부"만
# 확인하고 클릭하지 않는다(클릭 후 캐싱 검증은 tests/test_edgar_424b_cache.py가 담당).


def test_macro_indicator_bond_ticker_kr_code_short_circuits_no_network():
    """6자리 숫자(한국 종목코드)를 넣으면 EDGAR 조회 자체를 하지 않고 바로 안내한다 —
    get_cik가 아예 호출되지 않는지 확인한다(불필요한 네트워크 호출 방지)."""
    import views._cache as cache_mod

    with patch.object(cache_mod, "_get_cik", side_effect=AssertionError("get_cik가 호출되면 안 됨")):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        at.text_input(key="macro_bond_ticker_input").input("005930")
        next(b for b in at.button if b.key == "btn_bond_ticker_query").click()
        at.run()
        assert at.exception == []

    assert any("미국 상장 티커만 지원" in i.value for i in at.info)


def test_macro_indicator_bond_ticker_zero_result_message():
    """424B 이력이 0건인 티커는 "조회 실패"와 구분되는 안내를 보여준다(mock으로 결정적
    재현 — 장기 상장기업은 실측으로 진짜 0건 사례를 찾기 어려웠다)."""
    import views._cache as cache_mod

    with patch.object(cache_mod, "_get_cik", return_value="0000000001"), \
         patch.object(cache_mod, "_get_filings_by_type", return_value=[]):
        cache_mod.get_cik.clear()
        cache_mod.get_filings_by_type.clear()
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        at.text_input(key="macro_bond_ticker_input").input("ZEROTEST")
        next(b for b in at.button if b.key == "btn_bond_ticker_query").click()
        at.run()
        assert at.exception == []

    assert any("발행설명서) 이력이 없습니다" in i.value for i in at.info)


def test_macro_indicator_bond_ticker_summarize_button_only_for_terms_bearing_types():
    """424B2(발행조건 있음)엔 요약 버튼이 뜨고, 424B3(발행조건 없음, 교환제안류)엔 뜨지
    않으며 대신 안내 캡션이 뜨는지 확인한다 — 버튼은 존재 여부만 확인하고 클릭하지 않는다."""
    import views._cache as cache_mod

    fake_filings = [
        {
            "form": "424B2", "filing_date": "2025-05-06", "description": "424B2",
            "accession_number": "0001140361-25-017543", "url": "https://example.com/424b2",
        },
        {
            "form": "424B3", "filing_date": "2024-05-06", "description": "424B3",
            "accession_number": "0001193125-24-131951", "url": "https://example.com/424b3",
        },
    ]
    with patch.object(cache_mod, "_get_cik", return_value="0000320193"), \
         patch.object(cache_mod, "_get_filings_by_type", return_value=fake_filings):
        cache_mod.get_cik.clear()
        cache_mod.get_filings_by_type.clear()
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        at.text_input(key="macro_bond_ticker_input").input("AAPL")
        next(b for b in at.button if b.key == "btn_bond_ticker_query").click()
        at.run()
        assert at.exception == []

    button_keys = {b.key for b in at.button if b.key}
    assert "macro_bond_summarize_btn_0001140361-25-017543_school" in button_keys  # 424B2 → 있음
    assert "macro_bond_summarize_btn_0001193125-24-131951_school" not in button_keys  # 424B3 → 없음
    assert any("신규 발행 조건이 없는 유형" in c.value for c in at.caption)


@pytest.mark.network
def test_macro_indicator_bond_ticker_normal_case_real_data():
    """실제 SEC EDGAR로 티커 직접 조회(호출 1회) — 표가 뜨고, 424B2/424B5 필링이 있으면
    요약 버튼도 함께 뜨는지 확인한다(클릭은 하지 않음)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=120)
    at.run()
    at.text_input(key="macro_bond_ticker_input").input("AAPL")
    next(b for b in at.button if b.key == "btn_bond_ticker_query").click()
    at.run(timeout=120)
    assert at.exception == []

    bond_df = next((df for df in at.dataframe if "유형" in df.value.columns and "원문" in df.value.columns), None)
    assert bond_df is not None
    assert len(bond_df.value) > 0

    forms = set(bond_df.value["유형"])
    summarize_keys = [b.key for b in at.button if b.key and b.key.startswith("macro_bond_summarize_btn_")]
    has_terms_type = any(f.startswith("424B2") or f.startswith("424B5") for f in forms)
    assert bool(summarize_keys) == has_terms_type  # 발행조건 있는 유형이 있을 때만 요약 버튼 존재


@pytest.mark.network
def test_macro_indicator_bond_ticker_not_mapped_real():
    """SEC EDGAR 티커 목록에 없는 문자열은 명확한 경고로 구분된다(조회 실패와 다름)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    at.text_input(key="macro_bond_ticker_input").input("ZZZZZZZ99")
    next(b for b in at.button if b.key == "btn_bond_ticker_query").click()
    at.run(timeout=90)
    assert at.exception == []
    assert any("찾지 못했습니다" in w.value for w in at.warning)


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
def test_stock_research_supply_source_default_is_krx_regression():
    """토스증권 파일럿 라운드 — 소스 선택 라디오를 추가해도 기본값은 여전히 KRX(거래대금)
    라야 하고, 건드리지 않으면 기존 화면(단위 억원, 외국인/기관/개인 3개 지표)이 그대로
    나와야 한다(순수 추가, 회귀 없음)."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("005930")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run(timeout=90)
    assert at.exception == []

    source_radio = next((r for r in at.radio if r.label == "데이터 소스"), None)
    assert source_radio is not None, "TOSS 키가 있는데 소스 선택 라디오가 없음"
    assert source_radio.value == "KRX (거래대금)"  # 기본값 회귀

    metric_labels = [m.label for m in at.metric]
    assert {"외국인", "기관", "개인"} <= set(metric_labels)
    assert any("억원" in c.value for c in at.caption)  # 기존 KRX 단위 표기 유지


@pytest.mark.network
def test_stock_research_supply_source_toss_pilot_full_flow():
    """토스 소스로 전환하면 (1) 단위가 주(거래량)로 바뀌고 KRX와 직접 비교 불가 경고가
    뜨는지, (2) 기관 세부분류(금융투자/보험/투신/사모펀드/은행/기타금융기관/연기금) 표가
    토스 선택 시에만 나오는지 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("005930")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run(timeout=90)

    source_radio = next(r for r in at.radio if r.label == "데이터 소스")
    source_radio.set_value("토스증권 (거래량, 파일럿)")
    at.run(timeout=90)
    assert at.exception == []

    metric_labels_with_unit = [m.value for m in at.metric if m.label in ("외국인", "기관", "개인")]
    assert any("주" in v for v in metric_labels_with_unit)
    assert any("직접 비교할 수 없습니다" in c.value for c in at.caption)

    breakdown_df = next(
        (df for df in at.dataframe if "금융투자" in df.value.reset_index().columns), None
    )
    assert breakdown_df is not None, "기관 세부분류 표가 렌더링되지 않음"
    for label in ["금융투자", "보험", "투신", "사모펀드", "은행", "기타금융기관", "연기금"]:
        assert label in breakdown_df.value.reset_index().columns


@pytest.mark.network
def test_stock_research_supply_source_toss_caching_no_duplicate_network_calls():
    """캐싱 회귀 — 소스를 토스로 바꾼 뒤 재실행해도 토스 API가 다시 호출되지 않아야 한다
    (st.cache_data 히트). views/_cache.py의 import-alias 문제(2026-08 KOFIA 라운드에서 겪음)를
    피하려고 valley.toss.requests 레벨에서 직접 호출 횟수를 센다. ⚠️ 같은 프로세스의 다른
    테스트가 이미 "005930"을 조회해 캐시가 미리 데워져 있을 수 있어(st.cache_data는 프로세스
    전역), 이 테스트만 쓰는 다른 종목코드(000660)로 콜드 스타트를 보장한다."""
    import valley.toss as toss_mod

    get_calls = []
    _orig_get = toss_mod.requests.get

    def counting_get(*args, **kwargs):
        get_calls.append(1)
        return _orig_get(*args, **kwargs)

    with patch("valley.toss.requests.get", side_effect=counting_get):
        at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
        at.run()
        at.text_input[0].input("000660")
        next(b for b in at.button if b.label == "조회하기").click()
        at.run(timeout=90)

        source_radio = next(r for r in at.radio if r.label == "데이터 소스")
        source_radio.set_value("토스증권 (거래량, 파일럿)")
        at.run(timeout=90)
        assert at.exception == []
        calls_after_first_switch = len(get_calls)
        assert calls_after_first_switch > 0

        at.run(timeout=90)  # 재실행 — 캐시 히트라 추가 호출 없어야 함
        assert len(get_calls) == calls_after_first_switch


@pytest.mark.network
def test_stock_research_risk_indicators_hidden_for_us_ticker():
    """종목 리스크 지표 라운드 — 미국 종목은 섹션 헤더는 보이되(정보 안내), 로드 버튼은
    아예 없어야 한다(국내 종목 전용 API)."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("AAPL")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run(timeout=90)
    assert at.exception == []

    assert any("리스크 지표" in h.value for h in at.subheader)
    assert any("국내 상장기업 전용" in i.value for i in at.info)
    assert next((b for b in at.button if b.label == "리스크 지표 불러오기"), None) is None


@pytest.mark.network
def test_stock_research_risk_indicators_missing_key_shows_guidance_no_crash(monkeypatch):
    """토스 키가 없으면 앱이 죽지 않고 명확한 안내가 떠야 하며, 로드 버튼도 없어야 한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "")
    with patch("dotenv.load_dotenv", return_value=True):
        at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
        at.run()
        at.text_input[0].input("005930")
        next(b for b in at.button if b.label == "조회하기").click()
        at.run(timeout=90)
    assert at.exception == []
    assert any("TOSS_CLIENT_ID" in i.value for i in at.info)
    assert next((b for b in at.button if b.label == "리스크 지표 불러오기"), None) is None


@pytest.mark.network
def test_stock_research_risk_indicators_full_flow_005930():
    """삼성전자로 세 지표(공매도/신용거래/대차거래)를 실제 조회해 (1) 표 컬럼이 기대대로
    나오는지, (2) 값이 상식적 범위인지(공매도 비중은 통상 한 자릿수~10%대, 신용융자
    공여율은 대형주라 매우 낮음), (3) 세 캡션(T+1 지연·종목별 잔고임을 구분·대차의
    헤지/차익거래 한계 고지)이 전부 뜨는지 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
    at.run()
    at.text_input[0].input("005930")
    next(b for b in at.button if b.label == "조회하기").click()
    at.run(timeout=90)

    next(b for b in at.button if b.label == "리스크 지표 불러오기").click()
    at.run(timeout=90)
    assert at.exception == []

    short_selling_df = next(df for df in at.dataframe if "거래량비중" in df.value.reset_index().columns).value
    assert not short_selling_df.empty
    # 비중은 0~1 소수(퍼센트 아님) — 실측 기준 상식적 범위(0~30%, 스파이크 감안 여유있게)
    valid_rates = short_selling_df["거래량비중"].dropna()
    assert len(valid_rates) > 0
    assert (valid_rates >= 0).all() and (valid_rates < 0.3).all()

    credit_df = next(df for df in at.dataframe if "융자잔고" in df.value.reset_index().columns).value
    assert not credit_df.empty
    assert (credit_df["융자공여율"].dropna() < 0.05).all()  # 대형주 신용공여율은 낮은 편

    lending_df = next(df for df in at.dataframe if "잔고수량" in df.value.reset_index().columns).value
    assert not lending_df.empty
    assert (lending_df["잔고수량"].dropna() > 0).all()

    assert any("T+1" in c.value for c in at.caption)
    assert any("종목만의 잔고" in c.value for c in at.caption)
    assert any("헤지·차익거래" in c.value for c in at.caption)


@pytest.mark.network
def test_stock_research_risk_indicators_partial_failure_isolation():
    """개별 지표 실패 격리 — 공매도 조회가 실패해도 신용거래·대차거래는 정상 표시돼야 한다.
    ⚠️ 다른 테스트가 이미 "005930"/"000270"의 캐시를 데워놨을 수 있어(st.cache_data는
    프로세스 전역이라 mock을 patch해도 캐시 히트면 아예 호출되지 않는다), 이 테스트 전용
    종목코드(005380)로 콜드 스타트를 보장한다."""
    import valley.toss as toss_mod

    def failing_short_selling(*args, **kwargs):
        raise toss_mod.TossDataError("모의 실패 — 공매도 조회 불가")

    with patch("views._cache._get_short_selling_history", side_effect=failing_short_selling):
        at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
        at.run()
        at.text_input[0].input("005380")
        next(b for b in at.button if b.label == "조회하기").click()
        at.run(timeout=90)

        next(b for b in at.button if b.label == "리스크 지표 불러오기").click()
        at.run(timeout=90)
    assert at.exception == []
    assert any("공매도 데이터 조회에 실패했습니다" in w.value for w in at.warning)
    # 나머지 두 지표는 정상 렌더링
    assert any("융자잔고" in df.value.reset_index().columns for df in at.dataframe)
    assert any("잔고수량" in df.value.reset_index().columns for df in at.dataframe)


@pytest.mark.network
def test_stock_research_risk_indicators_button_gated_and_cached():
    """캐싱/호출 횟수 회귀 — (1) 페이지 진입(+조회하기 클릭)만으로는 토스 리스크 엔드포인트
    호출이 0회, (2) 리스크 지표 버튼 클릭 시 지표당 1회씩 총 3회, (3) 재실행 시 캐시 히트로
    추가 호출 0회여야 한다. ⚠️ valley.toss.requests.get을 patch하면 requests 모듈 자체가
    공유 객체라 DART/yfinance 등 무관한 호출까지 다 잡힌다(실측으로 확인) — 그래서 전체
    호출이 아니라 토스 리스크 엔드포인트 경로만 필터링해서 센다. 다른 테스트와 캐시가
    섞이지 않도록 이 테스트 전용 종목코드(000270)를 쓴다."""
    import valley.toss as toss_mod

    get_calls = []
    _orig_get = toss_mod.requests.get

    def counting_get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        get_calls.append(url)
        return _orig_get(*args, **kwargs)

    def risk_calls():
        return [c for c in get_calls if any(p in c for p in ("short-selling", "credit-trades", "securities-lending"))]

    with patch("valley.toss.requests.get", side_effect=counting_get):
        at = AppTest.from_file(str(VIEWS_DIR / "stock_research.py"), default_timeout=90)
        at.run()
        at.text_input[0].input("000270")
        next(b for b in at.button if b.label == "조회하기").click()
        at.run(timeout=90)
        assert len(risk_calls()) == 0  # 자동 로드 금지(리스크 지표 버튼을 아직 안 눌렀음)

        next(b for b in at.button if b.label == "리스크 지표 불러오기").click()
        at.run(timeout=90)
        assert at.exception == []
        assert len(risk_calls()) == 3  # 지표당 정확히 1회
        assert len({p.split("/stocks/")[-1].split("/")[-1] for p in risk_calls()}) == 3  # 세 엔드포인트 전부

        calls_after_click = len(risk_calls())
        at.run(timeout=90)  # 재실행 — 캐시 히트, 추가 호출 없어야 함
        assert len(risk_calls()) == calls_after_click


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


def test_macro_indicator_kr_m2_missing_key_shows_clear_error_no_crash(monkeypatch):
    """FRED 폴백 정리 라운드(2026-09) — KR_M2는 이제 ECOS 전용이라, 키가 없으면 (예전처럼
    2017-05 FRED 값으로 조용히 넘어가지 않고) 명확한 경고로 실패해야 하며 앱이 죽지 않아야
    한다. ECOS 키 체크가 HTTP 요청보다 먼저 실행돼(valley.macro.get_ecos_series) 실제
    네트워크 호출 없이 확인 가능하다 — fast tier."""
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    with patch("dotenv.load_dotenv", return_value=True):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        country_radio = next(r for r in at.radio if r.label == "국가")
        country_radio.set_value("한국")
        at.run()

        assert any("한국 M2는 대응하는 FRED 계열이 없어" in i.value for i in at.info)

        btn = next(b for b in at.button if b.label == "M2 통화량")
        btn.click()
        at.run()

    assert at.exception == []
    assert any("ECOS_API_KEY가 설정되어 있지 않습니다" in w.value for w in at.warning)


@pytest.mark.network
def test_macro_indicator_kr_cpi_fred_fallback_caption_shown_when_ecos_key_missing(monkeypatch):
    """FRED 폴백 정리 라운드(2026-09) — KR_CPI는 (KR_M2와 달리) 그대로 FRED 폴백을 유지하되,
    폴백이 실제로 쓰였을 때 사용자가 알 수 있는 캡션이 떠야 한다. get_ecos_series의 키 체크는
    네트워크 없이 통과하지만 이후 get_fred_series가 실제 FRED CSV를 조회하므로 network
    tier다. ⚠️ views.macro._shared.load_series가 st.cache_data(ttl=3600)라 이 프로세스 안의
    다른(더 먼저 실행된) 테스트가 이미 ECOS 키로 "KR_CPI"를 캐싱해놨으면 키를 지워도 캐시
    히트로 폴백이 아예 안 일어난다 — 캐시를 먼저 비운다."""
    from views.macro._shared import load_series

    load_series.clear()
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    with patch("dotenv.load_dotenv", return_value=True):
        at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
        at.run()
        country_radio = next(r for r in at.radio if r.label == "국가")
        country_radio.set_value("한국")
        at.run(timeout=90)

        btn = next(b for b in at.button if b.label == "CPI (소비자물가지수)")
        btn.click()
        at.run(timeout=90)

    assert at.exception == []
    fallback_captions = [c.value for c in at.caption if "FRED 미러 데이터로 대체" in c.value]
    assert len(fallback_captions) == 1
    assert "2023-11" in fallback_captions[0]


@pytest.mark.network
def test_macro_indicator_kr_cpi_and_m2_unchanged_when_ecos_key_present():
    """⚠️ 핵심 회귀 — ECOS_API_KEY가 있는 환경(이 프로젝트의 기본 상태)에서는 KR_CPI/KR_M2
    둘 다 항상 ECOS 경로를 타왔으므로, 이번 라운드의 카탈로그 변경(KR_M2 fred_id 제거,
    KR_CPI 폴백 캡션 추가)이 실제 표시값에 아무 영향을 주면 안 된다 — 폴백 캡션이 뜨지
    않아야 하고, 값도 valley.macro.get_indicator_series 직접 호출과 일치해야 한다. ⚠️ 앞
    테스트(키 없음)가 남긴 stale 폴백 캐시를 물려받지 않도록 먼저 비운다."""
    from valley.macro import get_indicator_series
    from views.macro._shared import load_series

    load_series.clear()
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run(timeout=90)

    for label, key in [("CPI (소비자물가지수)", "KR_CPI"), ("M2 통화량", "KR_M2")]:
        btn = next(b for b in at.button if b.label == label)
        btn.click()
        at.run(timeout=90)
        assert at.exception == []
        assert not [c for c in at.caption if "FRED 미러 데이터로 대체" in c.value]

        expected = get_indicator_series(key)
        as_of_caption = next(c for c in at.caption if "데이터 기준일" in c.value)
        assert f"{expected.index[-1]:%Y-%m}" in as_of_caption.value


@pytest.mark.network
def test_macro_indicator_debt_gdp_projection_button_shows_combined_view():
    """전망 반영 라운드(2026-08) — "재정" 카테고리에 실적(기존)·전망(신규) 버튼이 함께 있고,
    ⚠️ 전망 버튼을 눌러도 전망만 나오는 게 아니라 실적+전망 결합 뷰가 나와야 한다(전망만 보면
    어디서부터 전망인지 기준이 안 잡힘). 경계연도·"확정치 아님" 캡션도 확인한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    fiscal_labels = {b.label for b in at.button if b.key and b.key.startswith("btn_US_DEBT_GDP")}
    assert fiscal_labels == {"국가부채/GDP", "국가부채/GDP (전망)"}  # 실적 대체 아님, 순수 추가

    proj_btn = next(b for b in at.button if b.label == "국가부채/GDP (전망)")
    proj_btn.click()
    at.run(timeout=90)
    assert at.exception == []

    assert any("실적 + IMF 전망" in m.value for m in at.markdown)
    boundary_caption = next(c for c in at.caption if "년까지 실적" in c.value)
    assert "2024" in boundary_caption.value  # 조사 라운드 실측: 미국은 2024년까지 실적
    assert any("확정치가 아닙니다" in c.value for c in at.caption)


@pytest.mark.network
def test_macro_indicator_debt_gdp_actual_button_also_shows_combined_view():
    """실적 버튼("국가부채/GDP")을 눌러도 전망 버튼과 동일하게 결합 뷰가 나오는지 확인한다
    (둘 중 어느 쪽을 눌러도 같은 화면 — 라운드가 요구한 "실적+전망을 이어 그리되" 원칙)."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()
    country_radio = next(r for r in at.radio if r.label == "국가")
    country_radio.set_value("한국")
    at.run()

    actual_btn = next(b for b in at.button if b.label == "국가부채/GDP")
    actual_btn.click()
    at.run(timeout=90)
    assert at.exception == []

    assert any("실적 + IMF 전망" in m.value for m in at.markdown)
    boundary_caption = next(c for c in at.caption if "년까지 실적" in c.value)
    assert "2023" in boundary_caption.value  # 조사 라운드 실측: 한국은 2023년까지 실적


@pytest.mark.network
def test_macro_indicator_debt_gdp_comparison_shows_actual_and_projection_columns():
    """한미 비교 섹션 STEP 2 — 표에 최신 실적/최신 전망이 구분된 열로 함께 나오고, 값이 조사
    라운드에서 확인한 범위(2026년 전망: 미국 약 123.7%, 한국 약 55.7% — 단, 표의 "최신 전망"은
    시계열의 마지막 연도인 2030년 값이라 그보다 더 크다)와 상식적으로 일치하는지 확인한다.
    ⚠️ 기존 join="outer" 실적 전용 차트도 회귀 없이 그대로 있어야 한다."""
    at = AppTest.from_file(str(VIEWS_DIR / "macro_indicator.py"), default_timeout=90)
    at.run()

    btn = next(b for b in at.button if b.label == "한미 국가부채/GDP 비교 보기")
    btn.click()
    at.run(timeout=90)
    assert at.exception == []

    debt_df = next(df for df in at.dataframe if "최신 전망(%)" in df.value.reset_index().columns).value
    assert {"최신 실적(%)", "실적 기준연도", "최신 전망(%)", "전망 기준연도"} <= set(debt_df.columns)

    us_row = debt_df.loc["미국"]
    kr_row = debt_df.loc["한국"]
    assert us_row["실적 기준연도"] == "2024"
    assert kr_row["실적 기준연도"] == "2023"
    assert us_row["전망 기준연도"] == "2030"
    assert kr_row["전망 기준연도"] == "2030"
    # 조사 라운드 실측(2024년 미 120.8%, 2030년 전망까지 우상향)과 상식적으로 정합하는지.
    assert 100 < us_row["최신 실적(%)"] < 140
    assert us_row["최신 전망(%)"] > us_row["최신 실적(%)"]  # 전망이 실적보다 더 뒤 연도값
    assert 30 < kr_row["최신 실적(%)"] < 70
    assert kr_row["최신 전망(%)"] > kr_row["최신 실적(%)"]

    assert any("실적 + IMF 전망" in m.value for m in at.markdown)  # STEP 2 결합 뷰도 함께 렌더링
