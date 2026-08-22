"""Stock Research — 종목 재무분석 + 간단 DCF 밸류에이션 + AI 해설.

⚠️ 파일 분할 라운드(2026-08) — 상단부(시장 스냅샷~PER/PBR 밴드) 섹션 렌더 로직은 views/stock/
아래로 옮겼다. 이 파일은 사이드바·게이팅(st.stop())·상단부 조립과, 아직 분할하지 않은
하단부(간단 DCF~DART 원본 재무제표, 다음 라운드 범위)를 담당한다.
"""

from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from valley.ai_commentary import generate_commentary
from valley.dart import DartDataError
from valley.data import TickerNotFoundError
from valley.formatting import format_money, format_multiple, format_percent
from valley.krx import search_kr_stock_by_name
from valley.llm import ENGINE_GEMINI, ENGINE_LABELS, ENGINE_SCHOOL, is_engine_available
from valley.peers import ValuationDataError
from valley.report import build_markdown_summary
from valley.valuation import (
    CoeSuggestionUnavailableError,
    FcffInputsUnavailableError,
    ImpliedGrowthNotFoundError,
    capm_coe,
    compute_cost_of_debt,
    compute_effective_tax_rate,
    compute_wacc,
    dividend_growth_history,
    fcf_growth_history,
    fcf_to_fcff,
    fcff_dcf,
    gordon_ddm,
    implied_growth_rate,
    justified_pbr,
    sensitivity_grid,
    simplified_dcf,
    suggest_coe,
)
from views._style import inject_navy_headings
from views._cache import (
    get_company_snapshot,
    get_corp_code,
    get_dart_financial_statements,
    get_fcff_inputs,
    get_financial_history,
    get_free_cash_flow_history,
    get_kr_ticker_directory,
    get_latest_free_cash_flow,
    get_roe_history,
    get_annual_dividend_history,
    resolve_ticker,
)
from views.stock import (
    company_overview,
    dart_disclosures,
    edgar_disclosures,
    financial_trend,
    investor_flows,
    market_snapshot,
    price_trend,
    risk_indicators,
    valuation,
)

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


def _is_financial_industry(industry: str | None) -> bool:
    """industry가 은행·보험 계열인지 판별한다.

    industry가 없거나("N/A" 포함, build_snapshot의 기본값) 비어있으면 비금융으로 간주한다 —
    데이터가 없다고 sector 기준으로 되돌아가면 이번에 고치려는 오탐(V/MA류)이 되살아날 수 있고,
    잘못된 P/B×ROE/DDM 노출·잘못된 DCF 경고보다는 그냥 일반 DCF로 처리하는 쪽이 안전하다
    (어떤 종목이든 일반 DCF 경로는 항상 유효해 이 기본값이 앱을 죽이지 않는다).
    """
    if not industry or industry == "N/A":
        return False
    return industry.startswith(_FINANCIAL_INDUSTRY_PREFIXES)

load_dotenv(override=True)
inject_navy_headings()

st.title("PRISM")
st.caption("종목 재무분석 + 간단 DCF 밸류에이션 + AI 해설 · 정보 제공 목적이며 투자자문이 아닙니다.")

# 시장 스냅샷 — ⚠️ 이 프로젝트에서 유일하게 "버튼 클릭 없이" 페이지 진입만으로 자동 조회되는
# 섹션이다(get_market_snapshot의 ttl=300초 캐싱으로 호출 부담을 억제). "열자마자 시장 상황
# 파악"이 목적이라 종목을 조회하기 전/후 둘 다 항상 보이도록, 티커 확정 여부로 갈리는
# st.stop() 게이트(아래 "왼쪽에서 티커를 입력하고..." 안내) 앞에서 부른다 — 그 게이트 뒤에
# 두면 종목 조회 전에는 이 섹션 자체가 안 보이게 된다.
market_snapshot.render()

with st.sidebar:
    st.header("조회 설정")
    raw_input = st.text_input(
        "티커, 한국 종목코드, 또는 한글 종목명 (예: AAPL, 005930, 삼성전자)", value="AAPL"
    ).strip()

    ticker = raw_input.upper()
    is_kr_code = raw_input.isdigit() and len(raw_input) == 6

    # 6자리 코드나 영문 티커는 기존 방식 그대로(직접입력), 그 외(한글 등)만 이름검색으로 라우팅.
    if raw_input and not is_kr_code and not raw_input.isascii():
        try:
            kr_directory = get_kr_ticker_directory()
        except Exception as e:
            st.warning(f"종목명 검색은 일시적으로 불가합니다 ({e}). 6자리 코드를 직접 입력하세요.")
        else:
            candidates = search_kr_stock_by_name(raw_input, kr_directory)
            if not candidates:
                st.error(f"'{raw_input}'와 일치하는 종목이 없습니다. 정확한 종목명이나 6자리 코드를 입력해보세요.")
                ticker = ""
            elif len(candidates) == 1:
                c = candidates[0]
                ticker = c["code"]
                st.caption(f"{c['code']} {c['name']}({c['market']})가 선택됩니다. '조회하기'를 눌러 확정하세요.")
            else:
                options = [f"{c['code']} {c['name']} ({c['market']})" for c in candidates]
                choice = st.selectbox(
                    "검색 결과에서 종목을 선택하세요", options, key="sr_kr_name_search_choice"
                )
                selected = candidates[options.index(choice)]
                ticker = selected["code"]
                st.caption(f"{ticker} {selected['name']}가 선택됩니다. '조회하기'를 눌러 확정하세요.")

    st.subheader("DCF 가정치")
    fcf_basis = st.radio(
        "FCF 기준",
        ["최근 1개년", "최근 3개년 평균 (양수만)"],
        index=0,
        help="capex 사이클 등으로 특정 연도 FCF가 튀는 종목이면 3개년 평균이 더 안정적일 수 있습니다.",
    )
    use_fade = st.checkbox(
        "성장률 감쇠 사용 (초기 → 말기 선형 감소)",
        value=False,
        help="1년차는 초기 성장률, 마지막 연도는 말기 성장률이 되도록 연도별로 선형 보간합니다.",
    )
    # ⚠️ 상한은 100%까지만 — NVDA 실측(최근 4개년 FCF CAGR 193.9%, 기저효과가 아니라 매년
    # 증가율이 609.6%→125.2%→58.9%로 체계적으로 감속하는 실제 고성장)처럼 슬라이더 표현이
    # 필요한 종목이 있지만, 200%까지 열면 5년 투영 시 FCF가 수백~수천 배로 발산해 내재가치가
    # 무의미해진다. 100%도 5년이면 32배라 이미 극단적인 상한이다.
    _GROWTH_RATE_MAX = 1.0
    # ⚠️ 세션 키로 관리한다 — 아래 DCF 섹션의 "과거 FCF 증가율(CAGR)을 가정으로 쓰기" 버튼이
    # 이 값을 반영하기 위해서다. 감쇠 on/off는 초기 성장률을 서로 다른 세션 키로 관리한다
    # (sr_growth_rate_initial_fade vs sr_growth_rate_initial) — 두 모드의 기본값(0.12 vs
    # 0.08)이 다르고, 토글할 때 한쪽 모드에서 조정한 값이 다른 모드로 새어 들어가면 혼란스럽다.
    # 버튼이 실제로 어느 키를 채울지는 지금 시점의 use_fade로 정해진다. 페이지 접두어
    # 규칙(sr_)을 지켜 다른 페이지의 세션 키와 겹치지 않게 한다.
    # ⚠️ Streamlit은 key가 있는 위젯이 "이미 렌더링된 뒤"에는 같은 run 안에서 그 session_state를
    # 직접 못 바꾸게 막는다(StreamlitAPIException). 그래서 버튼은 이 키를 직접 건드리지 않고,
    # 이 슬라이더가 렌더링되기 "전"인 여기서만 sr_growth_rate_pending(있다면)을 실제 값에
    # 반영한 뒤 지운다.
    _growth_initial_key = "sr_growth_rate_initial_fade" if use_fade else "sr_growth_rate_initial"
    _growth_initial_default = 0.12 if use_fade else 0.08  # 기존 각 모드의 기본값 그대로
    if "sr_growth_rate_pending" in st.session_state:
        st.session_state[_growth_initial_key] = st.session_state["sr_growth_rate_pending"]
        del st.session_state["sr_growth_rate_pending"]
    elif _growth_initial_key not in st.session_state:
        st.session_state[_growth_initial_key] = _growth_initial_default

    if use_fade:
        growth_rate_initial = st.slider(
            "초기 성장률", 0.0, _GROWTH_RATE_MAX, step=0.01, format="%.2f", key=_growth_initial_key
        )
        growth_rate_terminal = st.slider("말기 성장률", 0.0, _GROWTH_RATE_MAX, 0.04, 0.01, format="%.2f")
    else:
        growth_rate_initial = st.slider(
            "연간 FCF 성장률", 0.0, _GROWTH_RATE_MAX, step=0.01, format="%.2f", key=_growth_initial_key
        )
        growth_rate_terminal = None

    clamp_notice = st.session_state.get("sr_growth_clamp_notice")
    if clamp_notice:
        st.info(clamp_notice)
        st.session_state.sr_growth_clamp_notice = None
    use_auto_coe = st.checkbox(
        "COE 자동제안 사용 (무위험이자율 + 리스크프리미엄)",
        value=False,
        help="무위험이자율(10년 국채금리)은 FRED에서 실시간 조회하고, 리스크프리미엄은 "
        "신뢰성 있게 자동 추정할 free 데이터가 없어 직접 정하는 가정치입니다.",
    )
    coe_source: dict = {"mode": "manual"}
    if use_auto_coe:
        # 이름검색으로 확정된 코드든 직접 입력한 6자리 코드든, ticker가 이 시점에 이미
        # 6자리 숫자면 한국(KRW) 종목으로 간주한다 (resolve_ticker와 동일한 판별 규칙).
        guessed_currency = "KRW" if ticker.isdigit() and len(ticker) == 6 else "USD"
        risk_premium = st.slider("리스크 프리미엄 (ERP, 가정치)", 0.0, 0.15, 0.05, 0.005, format="%.3f")
        try:
            coe_suggestion = suggest_coe(guessed_currency, risk_premium)
        except CoeSuggestionUnavailableError as e:
            st.warning(f"{e} COE를 직접 입력하세요.")
            use_auto_coe = False
            discount_rate = st.slider("요구수익률 (COE)", 0.03, 0.20, 0.10, 0.01, format="%.2f")
        else:
            discount_rate = coe_suggestion["coe"]
            st.info(
                f"무위험이자율 {coe_suggestion['rf'] * 100:.2f}%"
                f"({coe_suggestion['rf_source_label']}, {coe_suggestion['rf_as_of']:%Y-%m}) "
                # 이 discount_rate는 아래 민감도 표의 COE 축 중심값으로도 그대로 쓰인다 — 표의
                # 열 라벨이 소수점 1자리(.1f)로 표시되는 것과 정밀도를 맞춰, 여기 안내 문구의
                # "제안 COE"도 같은 자리수로 보여준다(실제 계산에 쓰이는 값 자체는 반올림하지
                # 않고 그대로 둔다 — 표시만 맞춘다).
                f"+ 리스크프리미엄 {risk_premium * 100:.1f}% = 제안 COE {discount_rate * 100:.1f}%"
            )
            coe_source = {
                "mode": "auto",
                "coe": discount_rate,
                "rf": coe_suggestion["rf"],
                "risk_premium": risk_premium,
                "rf_source_label": coe_suggestion["rf_source_label"],
                "rf_as_of": coe_suggestion["rf_as_of"],
            }
    else:
        discount_rate = st.slider("요구수익률 (COE)", 0.03, 0.20, 0.10, 0.01, format="%.2f")
    if coe_source["mode"] == "manual":
        coe_source["coe"] = discount_rate
    terminal_growth = st.slider("영구성장률", 0.0, 0.05, 0.025, 0.005, format="%.3f")
    years = st.slider("투영 연수", 3, 10, 5, 1)

    # ⚠️ 기본값은 반드시 "간이"로 둔다 — 기존 사용자 경험 보존(기본이 바뀌면 회귀로 느껴진다).
    # 정식(FCFF→WACC)은 자본구조를 반영한 "다른 관점"을 추가하는 것이지 간이보다 "더 정확한"
    # 게 아니다 — COE(CAPM 입력)가 여전히 베타·ERP 가정에 좌우되는 건 간이와 동일하다.
    valuation_method = st.radio(
        "밸류에이션 방식",
        ["간이 (FCF → COE)", "정식 (FCFF → WACC)"],
        index=0,
        help="간이: OCF 기반 FCF를 COE로 바로 할인해 Equity Value. 정식: FCFF를 WACC로 할인해 "
        "EV를 구하고 순부채를 차감해 Equity Value(자본구조 반영).",
    )
    wacc_beta: float | None = None
    wacc_risk_premium: float | None = None
    if valuation_method == "정식 (FCFF → WACC)":
        # ⚠️ 베타는 yfinance에서 자동으로 가져오지 않는다 — 산출 기준(관측기간·벤치마크 지수)이
        # 소스마다 달라 불투명하므로 사용자가 직접 확인해 입력하게 한다(이번 설계).
        wacc_beta = st.number_input(
            "베타 (β)", min_value=0.0, max_value=3.0, value=1.0, step=0.05,
            help="한국 종목은 Company Guide, 미국 종목은 Investing.com에서 확인할 수 있습니다.",
        )
        # ⚠️ ERP는 위 "COE 자동제안"의 리스크 프리미엄 슬라이더와 별개로 둔다 — 그 슬라이더는
        # use_auto_coe 체크박스 안에 중첩돼 있어, 정식 방식을 쓰려고 굳이 간이 COE 자동제안까지
        # 켜야 하는 건 두 방식을 불필요하게 얽어매는 것이라 판단했다. 두 코드 경로를 완전히
        # 독립적으로 둬 간이 방식에 회귀 위험이 없게 하는 게 우선이다(동일한 ERP를 쓰고 싶으면
        # 사용자가 두 슬라이더에 같은 값을 입력하면 된다).
        wacc_risk_premium = st.slider(
            "리스크 프리미엄 (ERP, WACC용, 가정치)", 0.0, 0.15, 0.05, 0.005, format="%.3f",
            key="sr_wacc_risk_premium",
        )

    # AI 엔진 선택 — AI 해설·8-K 요약 둘 다에 적용되는 페이지 전역 설정이라 사이드바에 둔다
    # (특정 버튼 근처에 두면 "이 버튼에만 적용되는 설정"으로 오해하기 쉽다). ⚠️ 자동 폴백
    # 없음 — 여기서 고른 엔진만 쓰이고, 학교 크레딧 소진 등의 이유로 조용히 바뀌지 않는다.
    st.subheader("AI 엔진")
    engine_options = [ENGINE_SCHOOL, ENGINE_GEMINI]
    if "sr_ai_engine" not in st.session_state:
        st.session_state.sr_ai_engine = ENGINE_SCHOOL
    selected_engine = st.radio(
        "AI 해설·8-K 요약에 사용할 엔진",
        engine_options,
        format_func=lambda e: ENGINE_LABELS[e] + ("" if is_engine_available(e) else " (키 미설정)"),
        key="sr_ai_engine",
        help="10-K처럼 긴 문서를 요약할 때 학교 크레딧을 아끼고 싶다면 Gemini를 선택하세요. "
        "직접 고른 엔진으로만 생성되며, 자동으로 바뀌지 않습니다.",
    )
    if not is_engine_available(selected_engine):
        st.warning(
            f"{ENGINE_LABELS[selected_engine]} API 키가 설정되어 있지 않습니다. "
            f".env에 {'OPENAI_API_KEY' if selected_engine == ENGINE_SCHOOL else 'GEMINI_API_KEY'}를 "
            "추가하기 전까지는 이 엔진으로 생성이 실패합니다."
        )

    run = st.button("조회하기", type="primary")

# ⚠️ st.session_state는 페이지 전용이 아니라 앱 전체(세션) 공용이라, 다른 페이지와 겹치지
# 않도록 이 페이지의 모든 키에 sr_ 접두어를 붙인다(Comparison의 cmp_has_run 충돌 사고 이후
# 전 페이지에 동일하게 적용한 규칙).
if "sr_has_run" not in st.session_state:
    st.session_state.sr_has_run = False
if "sr_confirmed_ticker" not in st.session_state:
    st.session_state.sr_confirmed_ticker = None

# ticker(사이드바의 현재 상태)는 타이핑/selectbox 조작만으로 계속 바뀌지만, 실제 조회
# 파이프라인은 "조회하기"가 눌린 이 rerun에서만 confirmed_ticker를 갱신하고, 그 외의 모든
# rerun(다른 버튼 클릭, selectbox 재선택 등)에서는 마지막으로 확정된 값을 그대로 쓴다.
# 그렇지 않으면 이름검색 selectbox에서 후보를 바꾸기만 해도(조회하기를 다시 안 눌러도)
# 다음 rerun에서 조용히 다른 종목으로 조회가 나가버린다.
if run:
    st.session_state.sr_has_run = True
    st.session_state.sr_confirmed_ticker = ticker

if not st.session_state.sr_has_run:
    st.info("왼쪽에서 티커를 입력하고 '조회하기'를 눌러주세요.")
    st.stop()

ticker = st.session_state.sr_confirmed_ticker

if not ticker:
    st.error("티커를 입력해주세요.")
    st.stop()

# 1단계: 티커 자체가 유효한지(스냅샷 조회)만 확인한다. 여기서 실패하면 기존과 동일하게
# 즉시 정지한다 — 존재하지 않는 티커(예: ZZZZ)는 여기서 걸러지고 DART로는 넘어가지 않는다.
try:
    resolved_ticker = resolve_ticker(ticker)
    snapshot = get_company_snapshot(resolved_ticker)
except TickerNotFoundError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"데이터 조회 중 오류가 발생했습니다: {e}")
    st.stop()
# ⚠️ 파일 분할 라운드(2026-08) — currency는 원래 "1. 회사 기본정보" 블록 맨 앞(옛
# company_overview 섹션)에서 계산됐지만, 아래로 분리된 거의 모든 섹션(주가·재무·공시·수급·
# 리스크·밸류에이션)이 이 값을 인자로 받아 쓴다 — 특정 섹션 전용 값이 아니라 페이지 전역
# 공유값이므로 게이팅 블록으로 끌어올렸다(값 자체는 snapshot["currency"] 그대로, 계산 위치만
# 이동).
currency = snapshot["currency"]

# 2단계: 재무제표(손익계산서) 조회. "yfinance 재무 완전 실패"는 이 함수의
# TickerNotFoundError로 엄격히 정의한다 — 이때만 페이지를 멈추지 않고 DART 폴백으로 넘긴다.
yfinance_financials_failed = False
try:
    financial_history = get_financial_history(resolved_ticker)
except TickerNotFoundError:
    financial_history = None
    yfinance_financials_failed = True

# 3단계: FCF. 재무제표가 아예 없으면(2단계 실패) 같은 소스 기반인 FCF도 없을 가능성이 높아
# 아예 시도하지 않는다 — DCF는 뒤에서 "계산 불가"로 처리한다(DART 폴백은 재무 추이 표시까지만,
# DCF까지 되살리는 건 이번 범위 밖). 재무제표 조회가 성공한 케이스(대다수)는 기존과 완전히
# 동일한 코드 경로를 그대로 탄다.
fcf_excluded_years = 0
latest_fcf = None
if not yfinance_financials_failed:
    try:
        if fcf_basis == "최근 3개년 평균 (양수만)":
            fcf_history = get_free_cash_flow_history(resolved_ticker, years=3)
            positive_fcf = fcf_history[fcf_history > 0]
            fcf_excluded_years = len(fcf_history) - len(positive_fcf)
            if positive_fcf.empty:
                raise ValueError(
                    "최근 3개년 모두 FCF가 0 이하라 평균을 낼 수 없습니다. 'FCF 기준'을 "
                    "'최근 1개년'으로 바꿔보세요 (그래도 음수면 이 모델로 DCF 평가가 어렵습니다)."
                )
            latest_fcf = float(positive_fcf.mean())
        else:
            latest_fcf = get_latest_free_cash_flow(resolved_ticker)
    except TickerNotFoundError as e:
        st.error(str(e))
        st.stop()
    except ValueError as e:
        st.error(str(e))
        st.stop()
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {e}")
        st.stop()

# 1. 회사 기본정보 + 회사 개요
company_overview.render(snapshot, currency, resolved_ticker, selected_engine)

# 2. 주가 추이
price_trend.render(resolved_ticker, currency)

# 3. 재무 추이
financial_trend.render(resolved_ticker, currency, financial_history, yfinance_financials_failed)

# 3-1. 최근 공시 (DART) — yfinance 재무 성공/실패와 무관하게 한국 종목이면 항상 표시하는 상시 섹션.
dart_disclosures.render(resolved_ticker, currency)

# 3-1-2. 최근 공시 (SEC EDGAR) — 위 DART 공시와 대칭 위치, 미국(비한국) 종목 전용.
edgar_disclosures.render(resolved_ticker, currency, snapshot, selected_engine)

# 3-2. 투자자별 수급 (KRX/토스) — 공시 섹션과 마찬가지로 yfinance 성공/실패와 무관하게
# 한국 종목이면 항상 표시하는 상시 섹션이다.
investor_flows.render(resolved_ticker, currency)

# 3-3. 종목 리스크 지표 (공매도·신용·대차) — 토스증권 API 전용, 국내 종목만.
risk_indicators.render(resolved_ticker, currency)

# 4. Peer Group 비교 + PER/PBR 밴드 — target_valuation은 아래 5-1(P/B×ROE)·6(요약 내보내기)에서도
# 재사용되므로 반환받는다. valuation.render() 내부에서 target_valuation 조회 실패 시 st.stop()으로
# 페이지 전체를 멈춘다(원래 코드와 동일한 동작 — 이 값이 하단부에서도 무조건 필요하기 때문).
target_valuation = valuation.render(resolved_ticker)

# 5. DCF 밸류에이션
st.subheader("간단 DCF 밸류에이션")
st.caption(
    "OCF(이자비용 차감 후) 기반의 간이 FCF를 요구수익률(COE)로 할인해 주주가치(Equity Value)를 "
    "직접 산출합니다. 다만 이 FCF는 순차입/상환 효과를 반영하지 않은 근사치라, 앞으로 대규모 "
    "차입이나 상환을 계획한 기업일수록 오차가 커질 수 있습니다."
)
if _is_financial_industry(snapshot.get("industry")):
    # 계산 자체를 막지는 않는다 — FCF 데이터가 있으면 아래 수치는 그대로 보여주되, "계산되니
    # 맞는 값"이라는 착각을 막기 위해 경고만 명확히 띄운다.
    st.warning(
        "이 종목은 금융업(은행·보험 등)입니다. 이 DCF는 제조업 FCF(영업현금흐름-자본적지출) "
        "기반 모델이라 금융업에는 구조적으로 부적합할 수 있습니다 — 금융업은 통상 배당할인모형"
        "(DDM)이나 P/B·ROE 기반으로 평가합니다. 아래 수치는 참고용으로만 보세요."
    )

    st.subheader("P/B × ROE 밸류에이션 (금융업)")
    st.caption(
        "은행 등 금융업의 1차 밸류에이션 지표입니다. 정당(justified) P/B = ROE / COE(단순형) — "
        "자기자본으로 요구수익률(COE)보다 높은 ROE를 벌면 장부가 대비 프리미엄을 받을 자격이 "
        "있다는 논리입니다."
    )
    bank_pbr = target_valuation.get("pbr")
    bank_bvps = target_valuation.get("bvps")
    bank_roe_1y = target_valuation.get("roe")
    try:
        bank_roe_history = get_roe_history(resolved_ticker, years=3)
    except ValuationDataError as e:
        bank_roe_history = None
        st.info(f"3개년 평균 ROE를 계산할 수 없습니다: {e}")
    bank_roe_avg = float(bank_roe_history.mean()) if bank_roe_history is not None and not bank_roe_history.empty else None

    br1, br2, br3, br4 = st.columns(4)
    br1.metric("현재 PBR", format_multiple(bank_pbr))
    br2.metric("ROE (단년)", format_percent(bank_roe_1y))
    br3.metric("ROE (3개년 평균)", format_percent(bank_roe_avg))
    br4.metric("요구수익률 (COE)", f"{discount_rate * 100:.1f}%")

    if bank_roe_history is not None:
        if (bank_roe_history < 0).any():
            st.caption("⚠️ 최근 3개년 중 ROE가 음수인 연도가 있습니다 — 3개년 평균이 그 해 실적의 영향을 그대로 받습니다.")
        if bank_roe_1y is not None and bank_roe_avg is not None and abs(bank_roe_1y - bank_roe_avg) > 0.05:
            st.caption(
                f"단년 ROE({format_percent(bank_roe_1y)})와 3개년 평균({format_percent(bank_roe_avg)})의 "
                "차이가 커, 이 종목 실적이 들쭉날쭉하다는 신호일 수 있습니다."
            )

    # ⚠️ 정당 P/B는 3개년 평균 ROE를 우선 쓴다(일회성 손익으로 튄 단년 ROE 왜곡 방지). 평균을
    # 못 구했을 때만 단년 ROE로 폴백한다 — 그래도 아무 값도 없을 때만 "계산 불가".
    bank_roe_used = bank_roe_avg if bank_roe_avg is not None else bank_roe_1y
    bank_roe_basis_label = "3개년 평균 ROE" if bank_roe_avg is not None else "단년 ROE"

    if bank_roe_used is None:
        st.warning("ROE를 계산할 수 없어 정당 P/B를 산출할 수 없습니다 (순이익 또는 자본총계 데이터 없음).")
    else:
        try:
            bank_justified_pbr = justified_pbr(bank_roe_used, discount_rate)
        except ValueError as e:
            st.warning(str(e))
        else:
            st.write(f"**정당 P/B ({bank_roe_basis_label} 기준)**: {format_multiple(bank_justified_pbr)}")
            if bank_bvps:
                bank_justified_price = bank_justified_pbr * bank_bvps
                bank_price_gap = (bank_justified_price - snapshot["price"]) / snapshot["price"]
                bj1, bj2, bj3 = st.columns(3)
                bj1.metric("정당 주가", format_money(bank_justified_price, currency))
                bj2.metric("현재 주가", format_money(snapshot["price"], currency))
                bj3.metric("P/B×ROE 괴리율", f"{bank_price_gap * 100:+.1f}%")
            elif bank_pbr:
                bank_pbr_gap = (bank_justified_pbr / bank_pbr) - 1
                st.metric("PBR 괴리율 (정당 P/B ÷ 현재 PBR − 1)", f"{bank_pbr_gap * 100:+.1f}%")
            else:
                st.info("BVPS/PBR 정보가 없어 괴리율을 계산할 수 없습니다.")

    st.caption(
        "⚠️ 한계: 정당 P/B = ROE/COE는 현재 ROE가 앞으로도 지속된다는 가정에 기반한 단순형으로, "
        "성장률은 반영하지 않았습니다. 일회성 손익(충당금·자산매각 등)으로 ROE가 변동하면 결과가 "
        "왜곡될 수 있으니, 단년·3개년 평균 ROE를 함께 보고 참고용으로만 활용하세요."
    )

    # 💰 DDM(배당할인모형) — P/B×ROE와 나란히 제공하는 은행·보험 밸류에이션의 또 다른 축.
    # ⚠️ COE는 위 DCF·P/B×ROE와 같은 discount_rate(사이드바 값)를 그대로 재사용한다 — 같은
    # 종목에서 세 밸류에이션의 COE가 다르면 혼란스럽다.
    st.subheader("DDM (배당할인모형)")
    st.caption(
        "은행·보험의 또 다른 축 밸류에이션입니다. 주당가치 = D1 / (COE − g), D1 = D0 × (1 + g) — "
        "배당이 매년 g로 영구히 성장한다는 가정(Gordon Growth)에 기반합니다."
    )
    try:
        bank_dividend_history = get_annual_dividend_history(resolved_ticker, years=5)
    except ValuationDataError as e:
        st.info(f"배당이 없어 DDM을 적용할 수 없습니다: {e}")
    else:
        bank_dividend_growth = dividend_growth_history(bank_dividend_history)
        bank_d0 = float(bank_dividend_history.iloc[-1])

        dd1, dd2, dd3 = st.columns(3)
        dd1.metric("최근 연간 DPS (D0)", format_money(bank_d0, currency))
        dd2.metric(
            f"과거 {len(bank_dividend_history)}개년 배당 CAGR",
            format_percent(bank_dividend_growth["cagr"]) if bank_dividend_growth["cagr"] is not None else "계산 불가",
        )
        dd3.metric("요구수익률 (COE)", f"{discount_rate * 100:.1f}%")

        if bank_dividend_growth["cagr"] is None:
            st.caption(f"⚠️ 배당 CAGR 계산 불가: {bank_dividend_growth['cagr_reason']}")
        if bank_dividend_growth["has_non_positive_year"]:
            st.caption("⚠️ 조회 기간 중 배당이 0 이하(무배당)인 연도가 있습니다 — 아래 표에서 확인하세요.")

        st.caption(f"단위: {currency} (완결된 연도만 — 진행 중인 올해는 제외)")
        dividend_table_df = pd.DataFrame(
            [
                {
                    "연도": item["year"],
                    "DPS": format_money(item["dps"], currency),
                    "전년比 증가율": f"{item['yoy'] * 100:+.1f}%" if item["yoy"] is not None else item["yoy_reason"],
                }
                for item in bank_dividend_growth["yearly"]
            ]
        ).set_index("연도")
        st.dataframe(dividend_table_df, width="stretch")

        # ⚠️ 슬라이더 상한을 COE 미만으로 제한해 발산(g≥COE)을 구조적으로 방지한다(DCF의
        # 영구성장률 슬라이더와 같은 정신) — gordon_ddm 내부 가드는 이중 방어.
        ddm_max_growth = discount_rate - 0.005
        if ddm_max_growth <= 0:
            st.warning("요구수익률(COE)이 너무 낮아 배당성장률을 설정할 수 없습니다. 사이드바에서 COE를 높여주세요.")
        else:
            if "sr_ddm_growth_rate" not in st.session_state:
                cagr_anchor = bank_dividend_growth["cagr"]
                if cagr_anchor is not None and 0 < cagr_anchor < ddm_max_growth:
                    st.session_state.sr_ddm_growth_rate = cagr_anchor
                else:
                    st.session_state.sr_ddm_growth_rate = min(0.03, ddm_max_growth)
            else:
                # COE가 바뀌어 상한이 낮아졌을 수 있다 — Streamlit 슬라이더는 저장된 값이 새
                # min/max 범위를 벗어나면 예외를 던지므로 렌더링 전에 미리 clamp한다.
                st.session_state.sr_ddm_growth_rate = max(
                    0.0, min(st.session_state.sr_ddm_growth_rate, ddm_max_growth)
                )
            ddm_growth_rate = st.slider(
                "배당성장률 (g) — 과거 CAGR을 참고해 조정하세요",
                0.0, ddm_max_growth, step=0.005, format="%.3f", key="sr_ddm_growth_rate",
                help=f"요구수익률(COE) {discount_rate * 100:.1f}%를 넘으면 DDM이 발산해, 슬라이더 상한을 그 아래로 제한했습니다.",
            )
            try:
                ddm_value = gordon_ddm(bank_d0, ddm_growth_rate, discount_rate)
            except ValueError as e:
                st.warning(str(e))
            else:
                ddm_gap = (ddm_value - snapshot["price"]) / snapshot["price"]
                dv1, dv2, dv3 = st.columns(3)
                dv1.metric("DDM 주당가치", format_money(ddm_value, currency))
                dv2.metric("현재 주가", format_money(snapshot["price"], currency))
                dv3.metric("DDM 괴리율", f"{ddm_gap * 100:+.1f}%")

        st.caption(
            "⚠️ 한계: DDM은 배당이 g로 영구히 성장한다는 가정에 기반합니다. 배당 정책이 바뀌거나 "
            "g가 COE에 가까우면 결과가 크게 변동합니다. 위 배당 CAGR·연도별 표를 함께 보고 "
            "참고용으로만 활용하세요."
        )

if fcf_excluded_years:
    st.caption(f"3개년 평균 계산 시 FCF가 음수인 {fcf_excluded_years}개 연도는 제외했습니다.")

# 과거 FCF 증가율 — 감으로 넣는 성장률 가정에 실측 앵커를 제공한다. get_free_cash_flow_history를
# DCF 계산(1개년 또는 3개년 평균)과 별개로 더 긴 기간(4개년)으로 다시 불러와 계산한다.
# ⚠️ 아래 DCF의 FCF≤0 게이팅(st.stop())과 무관하게 항상 렌더링한다 — 전 구간 FCF가 음수인
# 적자 성장주(예: RIVN)일수록 "FCF가 개선 추세인가"가 가장 궁금한 대상인데, 예전엔 DCF 섹션
# 안에 중첩돼 있어 DCF가 st.stop()으로 멈추면 이 표까지 통째로 안 보였다(정리 라운드 과제).
# "세 성장률 대조"(시장 내재 성장률 포함)만 DCF 성공 시에만 의미가 있어 그 부분은 아래 DCF
# 성공 블록에 남겨두고, growth 변수를 여기서 계산해 그대로 재사용한다(중복 조회 없음).
st.subheader("과거 FCF 증가율")
growth = None
try:
    fcf_history_for_growth = get_free_cash_flow_history(resolved_ticker, years=4)
except TickerNotFoundError as e:
    st.info(f"과거 FCF 증가율을 계산할 수 없습니다: {e}")
else:
    growth = fcf_growth_history(fcf_history_for_growth)

    if growth["cagr"] is not None:
        st.metric(f"최근 {len(growth['yearly'])}개년 FCF 연평균 증가율 (CAGR)", f"{growth['cagr'] * 100:.1f}%")
        if growth["cagr"] > 1.0 and not use_fade:
            st.caption(
                "⚠️ 과거 CAGR이 매우 높습니다. 이 성장률을 투영 기간 내내 유지하는 가정은 "
                "비현실적일 수 있습니다 — 아래 표에서 연도별 증가율이 감속 중인지 확인하고, "
                "사이드바의 '성장률 감쇠 사용'을 켜서 초기→말기 성장률을 나눠 설정하는 것을 "
                "권장합니다."
            )
    else:
        st.warning(f"연평균 증가율(CAGR) 계산 불가: {growth['cagr_reason']}")
    if growth["has_non_positive_year"]:
        st.caption("⚠️ 조회 기간 중 FCF가 0 이하인 연도가 있습니다 — 아래 표에서 확인하고, 증가율 해석에 주의하세요.")

    st.caption(f"단위: {currency}")
    growth_df = pd.DataFrame(
        [
            {
                "연도": item["year"],
                "FCF": format_money(item["fcf"], currency),
                "전년比 증가율": f"{item['yoy'] * 100:+.1f}%" if item["yoy"] is not None else item["yoy_reason"],
            }
            for item in growth["yearly"]
        ]
    ).set_index("연도")
    st.dataframe(growth_df, width="stretch")

    # ⚠️ 감쇠 모드에서도 이 버튼을 지원한다 — NVDA처럼 CAGR이 극단적으로 높은 종목일수록
    # 감쇠(초기→말기 선형 감소)가 가장 필요한데, 이전엔 감쇠 모드에서만 버튼을 꺼둬 오히려
    # 거꾸로였다. 감쇠 모드에서는 "초기 성장률"에만 적용하고(말기 성장률은 손대지 않는다),
    # 반영 대상 세션 키는 위 사이드바에서 use_fade에 따라 이미 정해져 있다
    # (sr_growth_rate_initial_fade vs sr_growth_rate_initial).
    _cagr_button_label = "이 값(CAGR)을 초기 성장률로 쓰기" if use_fade else "이 값(CAGR)을 DCF 가정으로 쓰기"
    if growth["cagr"] is None:
        st.button(
            _cagr_button_label, disabled=True,
            help="CAGR을 계산할 수 없어 비활성화되었습니다.",
        )
    elif st.button(_cagr_button_label):
        raw_cagr = growth["cagr"]
        clamped = max(0.0, min(1.0, raw_cagr))
        # sr_growth_rate_initial(_fade) 위젯이 이미 이번 run에서 렌더링된 뒤라 여기서 직접
        # 못 바꾼다 — pending 키에 남겨두면 다음 run의 슬라이더 렌더링 "전"에 반영된다.
        st.session_state.sr_growth_rate_pending = clamped
        st.session_state.sr_growth_clamp_notice = (
            f"과거 CAGR {raw_cagr * 100:.1f}%가 슬라이더 범위(0~100%)를 벗어나 "
            f"{clamped * 100:.1f}%로 설정되었습니다."
            if clamped != raw_cagr
            else None
        )
        st.rerun()

# 5-1. 정식 DCF (FCFF → WACC → EV → Equity) — 사이드바에서 "정식"을 선택했을 때만 렌더링하는
# 순수 추가 섹션. ⚠️ 아래(5-2) 간이 DCF의 else 블록은 latest_fcf<=0이면 st.stop()으로 페이지
# 전체를 멈춘다(예: 은행은 OCF-Capex 기반 FCF가 종종 음수) — 그 지점 "이후"에 이 섹션을 두면
# 금융업·적자 종목에서 정식 DCF 경고조차 안 뜨고 조용히 사라져버린다(실측 확인: JPM으로 "정식"
# 선택 시 아무 것도 안 뜸). 그래서 간이 DCF의 st.stop() 이전인 여기에 배치했다 — 이 섹션은
# fcff_dcf 내부에 자체 FCFF≤0 가드가 있어(ValueError를 st.error로만 잡고 st.stop()은 하지
# 않음) 간이 DCF의 성공 여부와 완전히 독립적으로 항상 도달 가능하다. 대신 "간이 vs 정식 비교"의
# 참고용 간이 결과값(dcf_result)은 이 시점엔 아직 계산 전이라 표시되지 않는다(globals()로 안전
# 확인 후 없으면 조용히 생략 — 그 비교는 과제에서도 "가능하면"으로 명시된 부가 기능이다).
if valuation_method == "정식 (FCFF → WACC)":
    st.divider()
    st.subheader("정식 DCF (FCFF → WACC → EV → Equity)")
    st.caption(
        "자본구조(부채)를 반영해 기업가치(EV)를 먼저 구하고 순부채를 차감해 주주가치를 "
        "산출합니다. ⚠️ 간이 방식보다 '더 정확한 값'이 아니라 '자본구조를 반영한 다른 관점'"
        "입니다 — COE는 여전히 베타·ERP 가정에 좌우되는 추정치이고, 부채비용·실효세율은 최근 "
        "재무제표에서 역산한 근사치라 자본구조가 기중 크게 변한 기업에서는 왜곡될 수 있습니다."
    )

    if _is_financial_industry(snapshot.get("industry")):
        # ⚠️ 은행·보험은 부채가 영업의 원재료라 "자본조달비용"이라는 WACC의 전제 자체가
        # 성립하지 않는다 — 계산을 아예 하지 않고 위 P/B×ROE·DDM으로 안내한다(이미 제공 중).
        st.warning(
            "이 종목은 금융업(은행·보험 등)입니다. 부채가 영업의 원재료인 금융업은 WACC(자본조달"
            "비용) 개념 자체가 성립하지 않아 정식 DCF를 계산하지 않습니다. 위 P/B×ROE·DDM을 "
            "이용하세요."
        )
    elif not snapshot.get("shares_outstanding"):
        st.warning("발행주식수 정보가 없어 정식 DCF를 계산할 수 없습니다.")
    elif latest_fcf is None:
        st.warning(
            "재무 데이터가 없어 정식 DCF 계산을 진행할 수 없습니다."
            + (" yfinance 재무제표 조회 실패로 위에서 DART 대체 재무만 표시했습니다 (정식 DCF는 이 파일럿 범위 밖)." if yfinance_financials_failed else "")
        )
    else:
        try:
            fcff_inputs = get_fcff_inputs(resolved_ticker)
        except TickerNotFoundError as e:
            st.warning(f"정식 DCF에 필요한 재무 데이터를 가져오지 못했습니다: {e}")
        else:
            # ⚠️ 총부채는 있는데 이자비용이 없으면(실측: AAPL 최신 회계연도) 부채비용을 0으로
            # 두고 WACC를 계산하면 "부채가 공짜"라는 조용히 틀린 가정이 된다 — 여기서 막지
            # 않으면 아래 compute_cost_of_debt에 0.0을 넘기게 되고, WACC 자체는 (틀린 값으로)
            # 성공해버려 사용자가 잘못을 알아챌 수 없다. FCFF 전환 단계에서도 결국 막히지만
            # (fcf_to_fcff), 그때는 이미 WACC를 잘못 보여준 뒤라 늦다 — 이 시점에서 먼저 막는다.
            if fcff_inputs["total_debt"] > 0 and fcff_inputs["interest_expense"] is None:
                st.error(
                    "이 종목은 총부채가 있지만 최근 재무제표에 이자비용이 별도로 공시되지 않아"
                    "(예: 일부 기업이 '기타손익'에 통합 공시) 부채비용·WACC·FCFF를 신뢰성 있게 "
                    "계산할 수 없습니다. 간이 방식을 이용해주세요."
                )
            else:
                tax_result = compute_effective_tax_rate(
                    fcff_inputs["tax_provision"], fcff_inputs["pretax_income"], currency
                )
                cost_of_debt_result = compute_cost_of_debt(
                    fcff_inputs["interest_expense"] or 0.0, fcff_inputs["total_debt"]
                )

                try:
                    rf_info = suggest_coe(currency, wacc_risk_premium)
                except CoeSuggestionUnavailableError as e:
                    st.warning(f"{e} 정식 DCF를 계산할 수 없습니다 (CAPM에 무위험이자율이 필요합니다).")
                else:
                    capm_coe_value = capm_coe(rf_info["rf"], wacc_beta, wacc_risk_premium)
                    wacc_result = compute_wacc(
                        market_cap=snapshot.get("market_cap"),
                        total_debt=fcff_inputs["total_debt"],
                        coe=capm_coe_value,
                        cost_of_debt_pretax=cost_of_debt_result["rate"],
                        tax_rate=tax_result["rate"],
                    )

                    st.markdown("**WACC 구성**")
                    wc1, wc2, wc3, wc4, wc5, wc6 = st.columns(6)
                    wc1.metric("자기자본비중(E)", f"{wacc_result['weight_equity'] * 100:.1f}%")
                    wc2.metric("부채비중(D)", f"{wacc_result['weight_debt'] * 100:.1f}%")
                    wc3.metric("COE (CAPM)", f"{capm_coe_value * 100:.2f}%")
                    wc4.metric(
                        "세전 부채비용",
                        f"{cost_of_debt_result['rate'] * 100:.2f}%" if cost_of_debt_result["rate"] is not None else "N/A",
                    )
                    wc5.metric(
                        "실효세율",
                        f"{tax_result['rate'] * 100:.1f}%" + (" (폴백)" if tax_result["source"] == "fallback" else ""),
                    )
                    wc6.metric("WACC", f"{wacc_result['wacc'] * 100:.2f}%")

                    if tax_result["warning"]:
                        st.caption(f"⚠️ 실효세율: {tax_result['warning']}")
                    if cost_of_debt_result["warning"]:
                        st.caption(f"⚠️ 부채비용: {cost_of_debt_result['warning']}")
                    if wacc_result["unlevered"]:
                        st.caption("이 종목은 총부채가 0으로 조회돼 무차입 기업으로 처리했습니다 — WACC = COE.")

                    st.caption(
                        f"CAPM COE = 무위험이자율({rf_info['rf'] * 100:.2f}%, {rf_info['rf_source_label']}, "
                        f"{rf_info['rf_as_of']:%Y-%m}) + 베타({wacc_beta:.2f}) × ERP({wacc_risk_premium * 100:.1f}%) "
                        f"= {capm_coe_value * 100:.2f}%. 기존 COE 자동제안(Rf+ERP)은 이 식에서 베타=1을 넣은 것과 "
                        "동치입니다."
                    )

                    try:
                        latest_fcff = fcf_to_fcff(latest_fcf, fcff_inputs["interest_expense"], tax_result["rate"])
                    except FcffInputsUnavailableError as e:
                        st.error(str(e))
                    else:
                        try:
                            fcff_result = fcff_dcf(
                                latest_fcff=latest_fcff,
                                growth_rate=growth_rate_initial,
                                growth_rate_terminal=growth_rate_terminal,
                                wacc=wacc_result["wacc"],
                                terminal_growth=terminal_growth,
                                years=years,
                                total_debt=fcff_inputs["total_debt"],
                                cash_and_st_investments=fcff_inputs["cash_and_st_investments"],
                                shares_outstanding=snapshot["shares_outstanding"],
                            )
                        except ValueError as e:
                            st.error(str(e))
                        else:
                            money_format = (
                                "₩{:,.0f}" if currency == "KRW" else "${:,.2f}" if currency == "USD" else "{:,.2f}"
                            )

                            st.markdown("**EV → Equity 브릿지**")
                            bridge_df = pd.DataFrame(
                                [
                                    {"항목": "기업가치(EV)", "값": fcff_result["enterprise_value"]},
                                    {"항목": "− 총부채", "값": -fcff_inputs["total_debt"]},
                                    {"항목": "+ 현금성자산", "값": fcff_inputs["cash_and_st_investments"]},
                                    {"항목": "= 자기자본가치(Equity)", "값": fcff_result["equity_value"]},
                                ]
                            ).set_index("항목")
                            st.dataframe(bridge_df.style.format({"값": money_format}), width="stretch")
                            if fcff_result["net_debt"] < 0:
                                st.caption(
                                    f"⚠️ 이 종목은 순현금 기업입니다(순부채 {format_money(fcff_result['net_debt'], currency)}, "
                                    "음수 = 순현금) — 자기자본가치가 기업가치보다 큽니다. 정상입니다."
                                )

                            fcff_intrinsic = fcff_result["intrinsic_value_per_share"]
                            price = snapshot["price"]
                            fcff_upside = (fcff_intrinsic - price) / price

                            fc1, fc2, fc3 = st.columns(3)
                            fc1.metric("주당 내재가치 (정식 DCF)", format_money(fcff_intrinsic, currency))
                            fc2.metric("현재 주가", format_money(price, currency))
                            fc3.metric("정식 DCF 괴리율", f"{fcff_upside * 100:+.1f}%")

                            # ⚠️ 비교 표시(중요) — CAPM COE와 사이드바의 기존 COE를 나란히 보여줘
                            # 결과 차이가 "할인율 차이 때문인지 FCFF 전환 때문인지" 구분할 수 있게 한다.
                            st.markdown("**COE 비교 (간이 방식 대비)**")
                            cmp1, cmp2, cmp3 = st.columns(3)
                            cmp1.metric("COE (사이드바, 간이용)", f"{discount_rate * 100:.2f}%")
                            cmp2.metric("COE (CAPM, 정식용)", f"{capm_coe_value * 100:.2f}%")
                            cmp3.metric("WACC (정식용)", f"{wacc_result['wacc'] * 100:.2f}%")
                            # ⚠️ 간이 방식의 주당 내재가치는 이 섹션이 간이 DCF보다 먼저 렌더링되므로
                            # (위 주석 참고 — 간이의 st.stop()을 피하기 위한 배치) 아직 계산 전이라 이
                            # 자리에서 직접 참조할 수 없다. 대신 아래 '간단 DCF 밸류에이션' 섹션으로
                            # 안내한다 — 화면을 시끄럽게 만드는 억지 재계산보다 안전한 선택이다.
                            st.caption(
                                "간이 방식의 주당 내재가치는 아래 '간단 DCF 밸류에이션' 섹션에서 확인해 "
                                "비교하세요. 두 방식의 차이는 (1) 할인율 차이(COE vs WACC)와 (2) 현금흐름 "
                                "전환(FCF vs FCFF)이 함께 작용한 결과입니다 — 위 COE 비교로 (1)의 크기를 "
                                "가늠할 수 있습니다."
                            )

                            st.caption(
                                "⚠️ 한계: COE는 베타·ERP 가정에 크게 좌우됩니다. 부채비용·실효세율은 최근 "
                                "재무제표에서 역산한 근사치이며, 자본구조가 기중 크게 변한 기업에서는 "
                                "왜곡될 수 있습니다."
                            )

if latest_fcf is None:
    st.warning(
        "재무 데이터가 없어 DCF 계산을 진행할 수 없습니다."
        + (" yfinance 재무제표 조회 실패로 위에서 DART 대체 재무만 표시했습니다 (DCF는 이 파일럿 범위 밖)." if yfinance_financials_failed else "")
    )
elif not snapshot.get("shares_outstanding"):
    st.warning("발행주식수 정보가 없어 DCF 계산을 진행할 수 없습니다.")
else:
    try:
        dcf_result = simplified_dcf(
            latest_fcf=latest_fcf,
            growth_rate=growth_rate_initial,
            growth_rate_terminal=growth_rate_terminal,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            years=years,
            shares_outstanding=snapshot["shares_outstanding"],
        )
    except ValueError as e:
        st.error(str(e))
        st.stop()

    if use_fade:
        st.caption(f"성장률 감쇠: 1년차 {growth_rate_initial * 100:.1f}% → {years}년차 {growth_rate_terminal * 100:.1f}% (선형)")

    st.caption(f"단위: {currency}")
    yearly_df = pd.DataFrame(dcf_result["yearly"]).set_index("year")
    yearly_df.columns = ["투영 FCF", "현재가치", "연간 성장률"]
    money_format = "₩{:,.0f}" if currency == "KRW" else "${:,.2f}" if currency == "USD" else "{:,.2f}"
    st.dataframe(
        yearly_df.style.format({"투영 FCF": money_format, "현재가치": money_format, "연간 성장률": "{:.1%}"}),
        width="stretch",
    )

    intrinsic = dcf_result["intrinsic_value_per_share"]
    price = snapshot["price"]
    upside = (intrinsic - price) / price

    c1, c2, c3 = st.columns(3)
    c1.metric("주당 내재가치 (DCF)", format_money(intrinsic, currency))
    c2.metric("현재 주가", format_money(price, currency))
    c3.metric("DCF 괴리율", f"{upside * 100:+.1f}%")
    st.caption("가정치(성장률/요구수익률/영구성장률)에 따라 결과가 크게 달라질 수 있습니다. 참고용으로만 활용하세요.")

    st.markdown("**현재 주가에 내재된 성장률**")
    implied_g = None
    try:
        implied_g = implied_growth_rate(
            latest_fcf=latest_fcf,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            years=years,
            shares_outstanding=snapshot["shares_outstanding"],
            current_price=price,
        )
    except ImpliedGrowthNotFoundError as e:
        st.info(f"현재가가 너무 높거나 낮아, 상식적 성장률 범위 안에서는 정당화되지 않습니다. ({e})")
    except ValueError as e:
        st.warning(str(e))
    else:
        st.write(f"시장은 이 종목에 향후 {years}년간 연 **{implied_g * 100:.1f}%** FCF 성장을 요구하고 있습니다.")
        your_growth_label = "초기 성장률" if use_fade else "성장률"
        st.caption(f"당신의 가정({your_growth_label}) {growth_rate_initial * 100:.1f}% vs 시장 내재 {implied_g * 100:.1f}%")
        if use_fade:
            st.caption("역산은 항상 단일 성장률 기준입니다 (2-stage는 미지수가 둘이라 유일해가 없음) — 초기 성장률과 비교하고 있습니다.")

    # 세 성장률 대조 — 과거 FCF CAGR(growth)은 위 "📈 과거 FCF 증가율" 섹션에서 이미 계산해뒀다
    # (DCF 게이팅과 무관하게 항상 실행되므로 재조회하지 않고 그대로 재사용). 여기서는 방금 구한
    # 시장 내재 성장률(implied_g)까지 더해 세 가지를 나란히 비교한다 — 이 비교 자체는 DCF가
    # 성공했을 때만 의미가 있어 이 블록 안에 남겨둔다.
    if growth is not None:
        st.markdown("**세 성장률 대조**")
        cagr_display = f"{growth['cagr'] * 100:.1f}%" if growth["cagr"] is not None else "계산 불가"
        implied_g_display = f"{implied_g * 100:.1f}%" if implied_g is not None else "역산 불가"
        ct1, ct2, ct3 = st.columns(3)
        ct1.metric("과거 실제 (CAGR)", cagr_display)
        ct2.metric("내 가정" + (" (초기)" if use_fade else ""), f"{growth_rate_initial * 100:.1f}%")
        ct3.metric("시장 내재", implied_g_display)

    st.subheader("민감도 분석 (성장률 × 요구수익률)")
    grid = sensitivity_grid(
        latest_fcf=latest_fcf,
        growth_rate=growth_rate_initial,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
        years=years,
        shares_outstanding=snapshot["shares_outstanding"],
        current_price=price,
    )
    upside_df = pd.DataFrame(
        grid["upside"],
        index=[f"{g * 100:.1f}%" for g in grid["growth_axis"]],
        columns=[f"{d * 100:.1f}%" for d in grid["discount_axis"]],
    )
    upside_df.index.name = "성장률 \\ 요구수익률(COE)"

    flat_values = [v for row in grid["upside"] for v in row if v is not None]
    max_abs = max((abs(v) for v in flat_values), default=0.01) or 0.01
    center_row, center_col = len(grid["growth_axis"]) // 2, len(grid["discount_axis"]) // 2

    def _highlight_current_assumption(data: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        styles.iloc[center_row, center_col] = "font-weight: bold; border: 2px solid #5B5BD6;"
        return styles

    styled_grid = upside_df.style.format(lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "N/A").background_gradient(
        cmap="RdYlGn", vmin=-max_abs, vmax=max_abs, axis=None
    )
    if not use_fade:
        # fade 모드에서는 중앙 셀(단일 성장률 기준)이 위 DCF 결과(2-stage 감쇠 기준)와 값이
        # 다르기 때문에, "현재 가정과 일치한다"는 오해를 주는 강조 표시를 하지 않는다.
        styled_grid = styled_grid.apply(_highlight_current_assumption, axis=None)
    st.dataframe(styled_grid, width="stretch")

    if use_fade:
        st.caption(
            "민감도 표는 항상 단일 성장률 기준으로 계산합니다 (2-stage 감쇠는 축이 2개 더 "
            "필요해 표로 표현하기 어렵습니다). 위 DCF 결과는 성장률 감쇠가 적용된 값이라, 이 표의 "
            "어떤 셀과도 정확히 일치하지 않을 수 있습니다 — 그래서 특정 셀을 강조하지 않았습니다. "
            "참고용으로 중앙 부근(초기 성장률·현재 요구수익률)을 봐주세요."
        )
    st.caption(
        "셀 값은 해당 (성장률, 요구수익률) 조합에서의 괴리율(현재가 대비)입니다."
        + ("" if use_fade else " 굵은 테두리는 현재 사이드바에 입력한 가정 조합입니다.")
        + " N/A는 요구수익률이 영구성장률 이하가 되어 계산이 성립하지 않는 조합입니다."
    )

    # 6. AI 해설
    st.subheader("AI 해설")
    if financial_history is None:
        # 오늘은 latest_fcf가 None일 때 DCF가 먼저 "계산 불가"로 멈추기 때문에 이 지점에
        # financial_history is None으로 도달할 일이 없지만(둘 다 yfinance_financials_failed에서
        # 비롯됨), 그건 두 게이팅이 우연히 맞물려 있는 것뿐이라 명시적으로 한 번 더 막아둔다.
        # DCF 게이팅 로직이 나중에 바뀌면(예: 다른 FCF 소스 허용) 여기가 소리 없이 깨질 수 있다.
        st.info("yfinance 재무 데이터가 없어 AI 해설을 생성할 수 없습니다.")
    elif st.button("AI 해설 생성하기"):
        with st.spinner(f"AI({ENGINE_LABELS[selected_engine]})가 해설을 작성하고 있습니다..."):
            try:
                commentary = generate_commentary(snapshot, financial_history, dcf_result, engine=selected_engine)
            except RuntimeError as e:
                st.error(str(e))
            else:
                st.markdown(f"**🤖 AI 생성 해설 · {ENGINE_LABELS[selected_engine]}**")
                st.markdown(commentary)

    # 7. 요약 내보내기 (DCF가 정상 계산된 경우에만 이 지점에 도달)
    st.subheader("요약 내보내기")
    summary_md = build_markdown_summary(
        snapshot=snapshot,
        valuation_snapshot=target_valuation,
        dcf_result=dcf_result,
        coe_source=coe_source,
        implied_growth=implied_g,
        as_of=date.today(),
    )
    st.code(summary_md, language="markdown")
    file_ticker = resolved_ticker.split(".")[0]
    st.download_button(
        "마크다운 파일로 다운로드",
        data=summary_md,
        file_name=f"PRISM_{file_ticker}_{date.today():%Y%m%d}.md",
        mime="text/markdown",
    )

# 8. DART 원본 재무제표 (실험) — DCF 성공 여부와 무관하게 항상 도달 가능한 독립 섹션.
st.divider()
st.subheader("DART 원본 재무제표 (실험)")
st.caption(
    "OpenDART 전자공시 원본 재무제표를 yfinance 기반 수치와 대조해보는 파일럿 기능입니다. "
    "위의 다른 분석 결과에는 전혀 반영되지 않으며, 검증 목적으로만 존재합니다."
)

if yfinance_financials_failed:
    # yfinance 재무가 아예 없으면 대조할 게 없다 — 위 "재무 추이" 섹션에서 이미 DART 대체를
    # 보여줬으므로(성공했든 실패했든) 여기서 또 시도하지 않는다(financial_history가 None이라
    # 아래 대조 로직 자체가 성립하지 않기도 한다).
    st.caption(
        "이 종목은 yfinance 재무제표가 없어 위 '재무 추이' 섹션에서 이미 DART로 대체 표시를 "
        "시도했습니다. 대조할 yfinance 값이 없어 이 섹션은 생략합니다."
    )
elif currency != "KRW":
    st.info("DART는 한국 상장기업 재무제표만 제공합니다. 한국 종목(6자리 코드)을 조회했을 때만 표시됩니다.")
else:
    dart_stock_code = resolved_ticker.split(".")[0]
    try:
        dart_corp_code = get_corp_code(dart_stock_code)
    except DartDataError as e:
        st.warning(str(e))
    else:
        try:
            dart_data = get_dart_financial_statements(dart_corp_code)
        except DartDataError as e:
            st.warning(str(e))
        else:
            fs_label = "연결재무제표(CFS)" if dart_data["fs_div_used"] == "CFS" else "별도재무제표(OFS)"
            st.caption(f"기준: {fs_label} · DART corp_code {dart_corp_code}")

            years_sorted = sorted(dart_data["by_year"].keys())
            dart_table = pd.DataFrame({year: dart_data["by_year"][year] for year in years_sorted}).T
            dart_table.index.name = "연도"
            display_dart = dart_table.copy()
            for col in display_dart.columns:
                display_dart[col] = display_dart[col].map(lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A")
            st.dataframe(display_dart, width="stretch")

            matched = dart_data["matched_account_names"]
            matched_desc = ", ".join(f"{label}→'{name}'" for label, name in matched.items() if name)
            if matched_desc:
                st.caption(f"매칭된 DART 계정명: {matched_desc}")
            unmatched = [label for label, name in matched.items() if name is None]
            if unmatched:
                st.caption(f"매칭 실패(N/A 처리): {', '.join(unmatched)}")

            st.markdown("**DART vs yfinance 대조**")
            compare_pairs = [("매출액", "매출"), ("영업이익", "영업이익"), ("당기순이익", "순이익")]
            compare_rows = []
            for year in years_sorted:
                year_str = str(year)
                if year_str not in financial_history.index:
                    continue  # yfinance 쪽에 없는 회계연도는 대조 불가 -> 건너뜀
                for dart_label, yf_label in compare_pairs:
                    dart_val = dart_data["by_year"][year].get(dart_label)
                    yf_raw = financial_history.loc[year_str, yf_label]
                    yf_val = float(yf_raw) if pd.notna(yf_raw) else None
                    diff_pct = (dart_val - yf_val) / yf_val * 100 if (dart_val is not None and yf_val) else None
                    compare_rows.append(
                        {"연도": year_str, "항목": dart_label, "DART": dart_val, "yfinance": yf_val, "차이(%)": diff_pct}
                    )

            if not compare_rows:
                st.info("yfinance 재무 추이와 회계연도가 겹치지 않아 대조할 수 없습니다.")
            else:
                compare_df = pd.DataFrame(compare_rows)
                display_compare = compare_df.copy()
                display_compare["DART"] = display_compare["DART"].map(
                    lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A"
                )
                display_compare["yfinance"] = display_compare["yfinance"].map(
                    lambda v: format_money(v, "KRW") if pd.notna(v) else "N/A"
                )
                display_compare["차이(%)"] = display_compare["차이(%)"].map(
                    lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                )
                st.dataframe(display_compare, width="stretch", hide_index=True)
                st.caption(
                    "차이(%) = (DART − yfinance) / yfinance × 100. 연결/별도 기준 차이, 회계연도 정의 차이 "
                    "(사업연도 vs TTM), 계정 분류 차이 등으로 벌어질 수 있습니다."
                )
