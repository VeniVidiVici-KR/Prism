"""Stock Research — 종목 재무분석 + 간단 DCF 밸류에이션 + AI 해설.

⚠️ 파일 분할 라운드(2026-08) — 상단부(시장 스냅샷~PER/PBR 밴드) 섹션 렌더 로직은 views/stock/
아래로 옮겼다. ⚠️ 하단부 분할 라운드(2026-09) — 나머지(간단 DCF~DART 원본 재무제표)도 마저
옮겼다. 이 파일은 이제 사이드바·게이팅(st.stop())·전체 조립만 담당한다(각 섹션 render()는
필요한 값을 인자로 받고, 다른 섹션이 쓰는 값은 반환한다 — 암묵적 전역 의존 없이 인자·반환값
으로만 데이터가 흐른다).
"""

import streamlit as st
from dotenv import load_dotenv

from valley.data import TickerNotFoundError
from valley.krx import search_kr_stock_by_name
from valley.llm import ENGINE_GEMINI, ENGINE_LABELS, ENGINE_SCHOOL, is_engine_available
from valley.valuation import CoeSuggestionUnavailableError, suggest_coe
from views._style import inject_navy_headings
from views._cache import (
    get_company_snapshot,
    get_financial_history,
    get_free_cash_flow_history,
    get_kr_ticker_directory,
    get_latest_free_cash_flow,
    resolve_ticker,
)
from views.stock import (
    ai_commentary_section,
    bank_valuation,
    company_overview,
    dart_disclosures,
    dart_raw_financials,
    edgar_disclosures,
    fcf_growth,
    fcff_dcf_section,
    financial_trend,
    investor_flows,
    market_snapshot,
    price_trend,
    risk_indicators,
    simple_dcf,
    summary_export,
    valuation,
)
from views.stock._shared import is_financial_industry

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


# 업종 분기(P/B×ROE·DDM·정식 DCF의 금융업 경고 여부)는 한 곳에서만 계산한다 — 계산은 여기
# 한 번뿐이고, "그 결과로 무엇을 보여줄지"는 원본에 있던 자리 그대로 각 섹션 안에 남겼다
# (bank_valuation은 최상위 분기, fcff_dcf_section은 valuation_method 분기 안쪽 한 번 더).
is_financial = is_financial_industry(snapshot.get("industry"))

# 5. 간단 DCF 밸류에이션 헤더 — 금융업 경고·P/B×ROE·DDM은 is_financial일 때만 아래에서 그려진다.
st.subheader("간단 DCF 밸류에이션")
st.caption(
    "OCF(이자비용 차감 후) 기반의 간이 FCF를 요구수익률(COE)로 할인해 주주가치(Equity Value)를 "
    "직접 산출합니다. 다만 이 FCF는 순차입/상환 효과를 반영하지 않은 근사치라, 앞으로 대규모 "
    "차입이나 상환을 계획한 기업일수록 오차가 커질 수 있습니다."
)
bank_valuation.render(
    resolved_ticker=resolved_ticker,
    currency=currency,
    snapshot=snapshot,
    target_valuation=target_valuation,
    discount_rate=discount_rate,
    is_financial=is_financial,
)

if fcf_excluded_years:
    st.caption(f"3개년 평균 계산 시 FCF가 음수인 {fcf_excluded_years}개 연도는 제외했습니다.")

# 5-1. 과거 FCF 증가율 — growth는 아래 5-3(간단 DCF의 세 성장률 대조)이 재사용하므로 반환받는다.
growth = fcf_growth.render(resolved_ticker=resolved_ticker, currency=currency, use_fade=use_fade)

# 5-2. 정식 DCF (FCFF → WACC → EV → Equity) — 사이드바에서 "정식"을 선택했을 때만 렌더링하는
# 순수 추가 섹션. ⚠️ 아래(5-3) 간이 DCF의 else 블록은 latest_fcf<=0이면 st.stop()으로 페이지
# 전체를 멈춘다(예: 은행은 OCF-Capex 기반 FCF가 종종 음수) — 그 지점 "이후"에 이 섹션을 두면
# 금융업·적자 종목에서 정식 DCF 경고조차 안 뜨고 조용히 사라져버린다(실측 확인: JPM으로 "정식"
# 선택 시 아무 것도 안 뜸). 그래서 간이 DCF의 st.stop() 이전인 여기에 배치했다.
if valuation_method == "정식 (FCFF → WACC)":
    fcff_dcf_section.render(
        currency=currency,
        snapshot=snapshot,
        resolved_ticker=resolved_ticker,
        latest_fcf=latest_fcf,
        yfinance_financials_failed=yfinance_financials_failed,
        growth_rate_initial=growth_rate_initial,
        growth_rate_terminal=growth_rate_terminal,
        wacc_beta=wacc_beta,
        wacc_risk_premium=wacc_risk_premium,
        terminal_growth=terminal_growth,
        years=years,
        discount_rate=discount_rate,
        is_financial=is_financial,
    )

# 5-3. 간단 DCF 본체 + 현재 주가 내재 성장률 + 세 성장률 대조 + 민감도 분석. dcf_result·
# implied_g는 DCF가 정상 계산된 경우에만 None이 아니다 — 원본에서 AI 해설·요약 내보내기가 이
# 섹션의 else 블록 안에 중첩돼 있던 게이팅을, 여기서는 반환값으로 명시한다(valuation.py가
# target_valuation을 반환하는 것과 같은 패턴).
dcf_result, implied_g = simple_dcf.render(
    latest_fcf=latest_fcf,
    snapshot=snapshot,
    currency=currency,
    growth_rate_initial=growth_rate_initial,
    growth_rate_terminal=growth_rate_terminal,
    discount_rate=discount_rate,
    terminal_growth=terminal_growth,
    years=years,
    use_fade=use_fade,
    yfinance_financials_failed=yfinance_financials_failed,
    growth=growth,
)

if dcf_result is not None:
    # 6. AI 해설 — DCF가 정상 계산된 경우에만 이 지점에 도달(원본과 동일한 게이팅).
    ai_commentary_section.render(
        financial_history=financial_history,
        snapshot=snapshot,
        dcf_result=dcf_result,
        selected_engine=selected_engine,
    )

    # 7. 요약 내보내기 — 마찬가지로 DCF가 정상 계산된 경우에만 도달.
    summary_export.render(
        snapshot=snapshot,
        target_valuation=target_valuation,
        dcf_result=dcf_result,
        coe_source=coe_source,
        implied_g=implied_g,
        resolved_ticker=resolved_ticker,
    )

# 8. DART 원본 재무제표 (실험) — DCF 성공 여부와 무관하게 항상 도달 가능한 독립 섹션.
dart_raw_financials.render(
    yfinance_financials_failed=yfinance_financials_failed,
    currency=currency,
    resolved_ticker=resolved_ticker,
    financial_history=financial_history,
)
