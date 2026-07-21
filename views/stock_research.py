"""Stock Research — 종목 재무분석 + 간단 DCF 밸류에이션 + AI 해설."""

from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from valley.ai_commentary import generate_commentary
from valley.dart import DartDataError
from valley.data import TickerNotFoundError
from valley.formatting import format_money, format_multiple
from valley.krx import search_kr_stock_by_name
from valley.peers import ValuationDataError, build_valuation_table
from valley.report import build_markdown_summary
from valley.supply import SupplyDataError, summarize_net_trading
from valley.valuation import (
    CoeSuggestionUnavailableError,
    ImpliedGrowthNotFoundError,
    fcf_growth_history,
    implied_growth_rate,
    sensitivity_grid,
    simplified_dcf,
    suggest_coe,
)
from views._charts import line_chart_with_markers
from views._cache import (
    get_company_snapshot,
    get_corp_code,
    get_dart_disclosure_list,
    get_dart_financial_statements,
    get_financial_history,
    get_free_cash_flow_history,
    get_historical_per_pbr,
    get_investor_net_trading,
    get_kr_ticker_directory,
    get_latest_free_cash_flow,
    get_peer_tickers,
    get_price_history,
    get_quarterly_financial_history,
    get_valuation_snapshot,
    resolve_ticker,
)

PRICE_PERIOD_LABELS = {
    "1mo": "1개월", "3mo": "3개월", "6mo": "6개월",
    "1y": "1년", "3y": "3년", "5y": "5년", "max": "전체",
}

load_dotenv(override=True)

st.title("📈 PRISM")
st.caption("종목 재무분석 + 간단 DCF 밸류에이션 + AI 해설 · 정보 제공 목적이며 투자자문이 아닙니다.")

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
    if use_fade:
        growth_rate_initial = st.slider("초기 성장률", 0.0, 0.30, 0.12, 0.01, format="%.2f")
        growth_rate_terminal = st.slider("말기 성장률", 0.0, 0.30, 0.04, 0.01, format="%.2f")
    else:
        # ⚠️ 세션 키로 관리한다 — 아래 DCF 섹션의 "과거 FCF 증가율(CAGR)을 가정으로 쓰기"
        # 버튼이 이 값을 반영하기 위해서다. 페이지 접두어 규칙(sr_)을 지켜 다른 페이지의
        # 세션 키와 겹치지 않게 한다.
        # ⚠️ Streamlit은 key가 있는 위젯이 "이미 렌더링된 뒤"에는 같은 run 안에서 그
        # session_state를 직접 못 바꾸게 막는다(StreamlitAPIException). 그래서 버튼은
        # sr_growth_rate_initial을 직접 건드리지 않고, 이 슬라이더가 렌더링되기 "전"인
        # 여기서만 sr_growth_rate_pending(있다면)을 실제 값에 반영한 뒤 지운다.
        if "sr_growth_rate_pending" in st.session_state:
            st.session_state.sr_growth_rate_initial = st.session_state["sr_growth_rate_pending"]
            del st.session_state["sr_growth_rate_pending"]
        elif "sr_growth_rate_initial" not in st.session_state:
            st.session_state.sr_growth_rate_initial = 0.08  # 기존 기본값 그대로
        growth_rate_initial = st.slider(
            "연간 FCF 성장률", 0.0, 0.30, step=0.01, format="%.2f", key="sr_growth_rate_initial"
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

# 1. 회사 기본정보
st.subheader(f"{snapshot['name']} ({snapshot['ticker']})")
col1, col2, col3, col4 = st.columns(4)
currency = snapshot["currency"]
col1.metric("현재가", format_money(snapshot["price"], currency))
col2.metric("섹터", snapshot["sector"])
col3.metric("산업", snapshot["industry"])
market_cap = snapshot.get("market_cap")
col4.metric("시가총액", format_money(market_cap, currency) if market_cap else "N/A")

# 2. 주가 추이
st.subheader("주가 추이")
price_period = st.radio(
    "기간", list(PRICE_PERIOD_LABELS.values()), index=3, horizontal=True, label_visibility="collapsed"
)
selected_period = next(k for k, v in PRICE_PERIOD_LABELS.items() if v == price_period)
try:
    price_history = get_price_history(resolved_ticker, selected_period)
except (TickerNotFoundError, ValueError) as e:
    st.warning(str(e))
else:
    st.caption(f"단위: {currency} · 배당/분할 조정 종가")
    st.line_chart(price_history)

# 3. 재무 추이
st.subheader("재무 추이")

dart_fallback_used = False  # 아래 파일럿 섹션에서 중복 표시를 피하기 위해 기억해둔다.

if yfinance_financials_failed:
    st.warning("yfinance에서 이 종목의 재무제표를 가져오지 못했습니다.")
    if currency != "KRW":
        st.info("DART는 한국 상장기업 재무제표만 제공해 이 종목(한국 외)은 대체 표시가 불가합니다.")
    else:
        dart_stock_code = resolved_ticker.split(".")[0]
        try:
            dart_corp_code = get_corp_code(dart_stock_code)
            dart_fallback_data = get_dart_financial_statements(dart_corp_code)
        except DartDataError as e:
            st.error(f"DART 대체 조회도 실패했습니다: {e}")
        else:
            dart_fallback_used = True
            fs_label = "연결재무제표(CFS)" if dart_fallback_data["fs_div_used"] == "CFS" else "별도재무제표(OFS)"
            st.info(f"📄 yfinance 데이터 없음 → DART 원본 재무제표({fs_label})로 대체 표시합니다.")

            years_sorted = sorted(dart_fallback_data["by_year"].keys())
            fallback_rows = {}
            for y in years_sorted:
                acc = dart_fallback_data["by_year"][y]
                fallback_rows[str(y)] = {"매출": acc.get("매출액"), "영업이익": acc.get("영업이익"), "순이익": acc.get("당기순이익")}
            fallback_df = pd.DataFrame(fallback_rows).T
            fallback_df["영업이익률"] = fallback_df["영업이익"] / fallback_df["매출"]
            fallback_df["순이익률"] = fallback_df["순이익"] / fallback_df["매출"]

            st.caption(f"단위: {currency} (DART 원본, 연간)")
            display_fallback = fallback_df.copy()
            for col in ["매출", "영업이익", "순이익"]:
                display_fallback[col] = display_fallback[col].map(lambda v: format_money(v, currency) if pd.notna(v) else "N/A")
            for col in ["영업이익률", "순이익률"]:
                display_fallback[col] = display_fallback[col].map(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "N/A")
            st.dataframe(display_fallback, width="stretch")
            line_chart_with_markers(fallback_df[["매출", "순이익"]])

            matched = dart_fallback_data["matched_account_names"]
            matched_desc = ", ".join(f"{label}→'{name}'" for label, name in matched.items() if name)
            if matched_desc:
                st.caption(f"매칭된 DART 계정명: {matched_desc}")
else:
    period_view = st.radio("기간", ["연간", "분기별"], horizontal=True, label_visibility="collapsed")

    if period_view == "분기별":
        try:
            history_to_show = get_quarterly_financial_history(resolved_ticker)
        except TickerNotFoundError as e:
            st.warning(str(e))
            history_to_show = None
        else:
            st.caption("최근 4~5개 분기 (yfinance 무료 API 제공 범위 한계)")
    else:
        history_to_show = financial_history

    if history_to_show is not None:
        st.caption(f"단위: {currency}")
        display_history = history_to_show.copy()
        for col in ["매출", "영업이익", "순이익"]:
            display_history[col] = display_history[col].map(lambda v: format_money(v, currency) if pd.notna(v) else "N/A")
        for col in ["영업이익률", "순이익률"]:
            display_history[col] = display_history[col].map(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "N/A")
        st.dataframe(display_history, width="stretch")
        line_chart_with_markers(history_to_show[["매출", "순이익"]])

# 3-1. 공시 목록 (DART) — yfinance 재무 성공/실패와 무관하게 한국 종목이면 항상 표시하는
# 상시 섹션이다 (위 재무 추이의 DART 폴백과는 성격이 다름 — 폴백이 아니라 항상 노출).
st.subheader("📢 최근 공시 (DART)")
if currency != "KRW":
    st.info("현재는 한국 상장기업 공시만 지원합니다. 해외 종목 공시(SEC EDGAR 등)는 추후 지원 예정입니다.")
else:
    disclosure_period_labels = {"1개월": 1, "3개월": 3, "6개월": 6, "1년": 12}
    disclosure_period = st.radio(
        "조회 기간", list(disclosure_period_labels.keys()), index=2, horizontal=True, key="sr_disclosure_period"
    )
    keyword_filter = st.text_input(
        "보고서명 키워드 필터 (선택)", value="", key="sr_disclosure_keyword"
    ).strip()

    disclosure_stock_code = resolved_ticker.split(".")[0]
    try:
        disclosure_corp_code = get_corp_code(disclosure_stock_code)
        disclosures = get_dart_disclosure_list(
            disclosure_corp_code, months=disclosure_period_labels[disclosure_period]
        )
    except DartDataError as e:
        st.warning(f"공시 목록을 가져오지 못했습니다: {e}")
    else:
        if keyword_filter:
            disclosures = [d for d in disclosures if keyword_filter in d["report_nm"]]

        if not disclosures:
            no_result_msg = f"최근 {disclosure_period}간 공시가 없습니다."
            if keyword_filter:
                no_result_msg += f" (키워드 '{keyword_filter}' 필터 적용 중)"
            st.info(no_result_msg)
        else:
            disclosure_df = pd.DataFrame(
                [
                    {
                        "접수일": f"{d['rcept_dt'][:4]}-{d['rcept_dt'][4:6]}-{d['rcept_dt'][6:]}"
                        if d["rcept_dt"]
                        else "",
                        "보고서명": d["report_nm"],
                        "제출인": d["flr_nm"],
                        "원문": d["url"],
                    }
                    for d in disclosures
                ]
            )
            st.dataframe(
                disclosure_df,
                width="stretch",
                hide_index=True,
                column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")},
            )
            st.caption(f"최근 {disclosure_period} 이내 공시 {len(disclosures)}건 (최신순, 최대 30건 조회)")

# 3-2. 투자자별 수급 (KRX/pykrx) — 공시 섹션과 마찬가지로 yfinance 성공/실패와 무관하게
# 한국 종목이면 항상 표시하는 상시 섹션이다. 개별종목 투자자별 매매동향은 KRX 로그인
# (KRX_ID/KRX_PW)이 있어야 조회되므로, 로그인 정보가 없거나 KRX가 막으면 이 섹션만
# 경고로 안내하고 나머지 섹션(재무/DCF/Peer/공시)은 정상 동작한다.
st.subheader("📊 투자자별 수급 (외국인·기관·개인)")
if currency != "KRW":
    st.info("현재는 한국 상장기업 수급만 제공합니다. 해외 종목은 투자자별 매매동향 공개 데이터가 없습니다.")
else:
    supply_stock_code = resolved_ticker.split(".")[0]
    try:
        daily_supply = get_investor_net_trading(supply_stock_code, days=20)
    except SupplyDataError as e:
        st.warning(f"투자자별 수급 조회에 실패했습니다: {e}")
    else:
        supply_summary = summarize_net_trading(daily_supply)
        st.caption(f"최근 {len(daily_supply)}영업일 누적 순매수 (단위: 억원 · +매수우위 / -매도우위)")
        s1, s2, s3 = st.columns(3)
        s1.metric("외국인", f"{supply_summary['외국인'] / 1e8:+,.0f}억원")
        s2.metric("기관", f"{supply_summary['기관'] / 1e8:+,.0f}억원")
        s3.metric("개인", f"{supply_summary['개인'] / 1e8:+,.0f}억원")

        st.caption(f"일별 순매수 추이 (최근 {len(daily_supply)}영업일, 단위: 억원)")
        line_chart_with_markers((daily_supply / 1e8).rename(index=lambda d: d.strftime("%m-%d")))

# 4. Peer Group & PER/PBR 밴드
st.subheader("Peer Group 비교")
st.caption("Yahoo Finance의 '유사 종목' 데이터 기반이며, 엄밀한 동일산업 분류가 아닌 참고용입니다.")
try:
    target_valuation = get_valuation_snapshot(resolved_ticker)
except TickerNotFoundError as e:
    st.error(str(e))
    st.stop()
peer_tickers = get_peer_tickers(resolved_ticker)
if peer_tickers is None:
    st.warning("Yahoo Finance 유사 종목 API 호출에 실패했습니다. 잠시 후 다시 시도해주세요.")
elif not peer_tickers:
    st.info("이 종목에 대한 유사 종목 데이터가 없습니다.")
else:
    peer_rows = []
    for t in peer_tickers:
        try:
            peer_rows.append(get_valuation_snapshot(t))
        except TickerNotFoundError:
            continue
    st.dataframe(build_valuation_table([target_valuation, *peer_rows]), width="stretch", hide_index=True)
st.caption(
    "PER/PBR은 모든 종목을 최근 확정 회계연도(FY) 실적 기준으로 직접 계산해 통일했습니다. "
    "TTM보다 최대 1년 가까이 지연된 실적이라, 최근 실적이 급성장한 종목일수록 PER이 실제보다 높게 나올 수 있습니다."
)
st.caption(
    "Fwd PER은 Yahoo Finance가 제공하는 향후 12개월 컨센서스 기반 PER입니다. "
    "애널리스트 추정치가 충분치 않은 종목(주로 소형주)은 N/A로 표시됩니다."
)

st.subheader("PER / PBR 밴드 (연도별)")
st.caption("최근 4~5개년 연간(FY) 데이터 기준이며, 일별 롤링 밴드는 지원하지 않습니다.")
try:
    band_df = get_historical_per_pbr(resolved_ticker)
except ValuationDataError as e:
    st.warning(str(e))
else:
    b1, b2 = st.columns(2)
    for col, metric in [(b1, "PER"), (b2, "PBR")]:
        series = band_df[metric].dropna()
        col.markdown(f"**{metric}**")
        if series.empty:
            col.caption("계산 가능한 데이터가 없습니다.")
            continue
        col.bar_chart(series)
        current = target_valuation[metric.lower()]
        if current is not None:
            col.caption(
                f"현재 {metric} {format_multiple(current)} · "
                f"최근 {len(series)}개년 범위 {format_multiple(series.min())}~{format_multiple(series.max())}"
            )

# 5. DCF 밸류에이션
st.subheader("간단 DCF 밸류에이션")
st.caption(
    "OCF(이자비용 차감 후) 기반의 간이 FCF를 요구수익률(COE)로 할인해 주주가치(Equity Value)를 "
    "직접 산출합니다. 다만 이 FCF는 순차입/상환 효과를 반영하지 않은 근사치라, 앞으로 대규모 "
    "차입이나 상환을 계획한 기업일수록 오차가 커질 수 있습니다."
)
if fcf_excluded_years:
    st.caption(f"3개년 평균 계산 시 FCF가 음수인 {fcf_excluded_years}개 연도는 제외했습니다.")
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
    c3.metric("괴리율", f"{upside * 100:+.1f}%")
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

    # 과거 FCF 증가율 — 감으로 넣는 성장률 가정에 실측 앵커를 제공한다. get_free_cash_flow_history를
    # DCF 계산(1개년 또는 3개년 평균)과 별개로 더 긴 기간(4개년)으로 다시 불러와 계산한다.
    st.subheader("📈 과거 FCF 증가율")
    try:
        fcf_history_for_growth = get_free_cash_flow_history(resolved_ticker, years=4)
    except TickerNotFoundError as e:
        st.info(f"과거 FCF 증가율을 계산할 수 없습니다: {e}")
    else:
        growth = fcf_growth_history(fcf_history_for_growth)

        if growth["cagr"] is not None:
            st.metric(f"최근 {len(growth['yearly'])}개년 FCF 연평균 증가율 (CAGR)", f"{growth['cagr'] * 100:.1f}%")
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

        st.markdown("**세 성장률 대조**")
        cagr_display = f"{growth['cagr'] * 100:.1f}%" if growth["cagr"] is not None else "계산 불가"
        implied_g_display = f"{implied_g * 100:.1f}%" if implied_g is not None else "역산 불가"
        ct1, ct2, ct3 = st.columns(3)
        ct1.metric("과거 실제 (CAGR)", cagr_display)
        ct2.metric("내 가정" + (" (초기)" if use_fade else ""), f"{growth_rate_initial * 100:.1f}%")
        ct3.metric("시장 내재", implied_g_display)

        if use_fade:
            st.caption(
                "2-stage(성장률 감쇠) 모드에서는 이 버튼을 지원하지 않습니다 — 감쇠를 끄면 "
                "과거 CAGR을 성장률 가정으로 바로 반영할 수 있습니다."
            )
        elif growth["cagr"] is None:
            st.button(
                "이 값(CAGR)을 DCF 가정으로 쓰기", disabled=True,
                help="CAGR을 계산할 수 없어 비활성화되었습니다.",
            )
        elif st.button("이 값(CAGR)을 DCF 가정으로 쓰기"):
            raw_cagr = growth["cagr"]
            clamped = max(0.0, min(0.30, raw_cagr))
            # sr_growth_rate_initial 위젯이 이미 이번 run에서 렌더링된 뒤라 여기서 직접
            # 못 바꾼다 — pending 키에 남겨두면 다음 run의 슬라이더 렌더링 "전"에 반영된다.
            st.session_state.sr_growth_rate_pending = clamped
            st.session_state.sr_growth_clamp_notice = (
                f"과거 CAGR {raw_cagr * 100:.1f}%가 슬라이더 범위(0~30%)를 벗어나 "
                f"{clamped * 100:.1f}%로 설정되었습니다."
                if clamped != raw_cagr
                else None
            )
            st.rerun()

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
        with st.spinner("AI가 해설을 작성하고 있습니다..."):
            try:
                commentary = generate_commentary(snapshot, financial_history, dcf_result)
                st.markdown(commentary)
            except RuntimeError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"AI 해설 생성 중 오류가 발생했습니다: {e}")

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
st.subheader("🔬 DART 원본 재무제표 (실험)")
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
