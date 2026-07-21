"""Comparison — 여러 종목의 밸류에이션과 성장/수익성 추이를 나란히 비교."""

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from valley.data import TickerNotFoundError
from valley.formatting import format_money
from valley.peers import build_valuation_table
from valley.valuation import ImpliedGrowthNotFoundError, implied_growth_rate, simplified_dcf
from views._cache import (
    get_company_snapshot,
    get_financial_history,
    get_latest_free_cash_flow,
    get_valuation_snapshot,
    resolve_ticker,
)

load_dotenv(override=True)

st.title("⚖️ Comparison")
st.caption("최대 4개 종목의 밸류에이션과 성장률/수익성 추이를 비교합니다 · 정보 제공 목적이며 투자자문이 아닙니다.")

MAX_TICKERS = 4

tickers_input = st.text_input("비교할 티커 (쉼표로 구분, 최대 4개, 예: AAPL, MSFT, 005930)", value="AAPL, MSFT, 005930")
run = st.button("비교하기", type="primary")

# DCF 비교 섹션에 슬라이더/버튼이 추가되면서, 그 위젯을 조작하는 것만으로도 스크립트
# 전체가 재실행된다. "비교하기"를 다시 누르지 않은 재실행에서도 이미 확정된 비교 대상을
# 그대로 유지하기 위해(Stock Research의 confirmed_ticker와 동일한 패턴), 세션에 기억해둔다.
# ⚠️ st.session_state는 페이지 전용이 아니라 앱 전체(세션) 공용이라, Stock Research가 쓰는
# "has_run"과 이름이 겹치면 안 된다 — Stock Research에서 조회하기를 먼저 눌러 has_run=True가
# 남은 채로 Comparison에 들어오면, 이 페이지의 confirmed_tickers_input은 여전히 None인데
# 아래 정지 게이트가 (겹친 has_run=True 때문에) 뚫려버려 곧장 크래시했던 회귀였다.
if "cmp_has_run" not in st.session_state:
    st.session_state.cmp_has_run = False
if "cmp_confirmed_tickers_input" not in st.session_state:
    st.session_state.cmp_confirmed_tickers_input = None

if run:
    st.session_state.cmp_has_run = True
    st.session_state.cmp_confirmed_tickers_input = tickers_input

if not st.session_state.cmp_has_run or not st.session_state.cmp_confirmed_tickers_input:
    st.info("티커를 입력하고 '비교하기'를 눌러주세요.")
    st.stop()

raw_tickers = [t.strip().upper() for t in st.session_state.cmp_confirmed_tickers_input.split(",") if t.strip()]
unique_tickers = list(dict.fromkeys(raw_tickers))[:MAX_TICKERS]

if not unique_tickers:
    st.error("티커를 입력해주세요.")
    st.stop()

valuation_rows, growth_series, margin_series, failed, failed_detail = [], {}, {}, [], []
valuation_by_resolved = {}
for raw in unique_tickers:
    try:
        resolved = resolve_ticker(raw)
        valuation_row = get_valuation_snapshot(resolved)
        valuation_rows.append(valuation_row)
        valuation_by_resolved[resolved] = valuation_row
        history = get_financial_history(resolved)
        growth_series[resolved] = history["매출"].pct_change() * 100
        margin_series[resolved] = history["순이익률"] * 100
    except TickerNotFoundError as e:
        failed.append(raw)
        failed_detail.append(f"{raw}: {e}")
    except Exception as e:
        failed.append(raw)
        failed_detail.append(f"{raw}: {type(e).__name__} - {e}")

if failed:
    st.warning(f"{len(failed)}개 종목은 데이터를 가져오지 못해 제외되었습니다: {', '.join(failed)}")
    with st.expander("실패 상세"):
        for line in failed_detail:
            st.text(line)

if not valuation_rows:
    st.stop()

st.subheader("밸류에이션 요약")
st.dataframe(build_valuation_table(valuation_rows), width="stretch", hide_index=True)

st.subheader("매출 성장률 비교 (YoY %)")
st.caption("통화가 다른 종목을 절대금액으로 겹쳐 그리면 스케일이 왜곡되어, 통화에 무관한 성장률(%)로 비교합니다.")
growth_df = pd.DataFrame(growth_series)
if growth_df.dropna(how="all").empty:
    st.info("계산 가능한 성장률 데이터가 없습니다.")
else:
    st.line_chart(growth_df)

st.subheader("순이익률 비교 (%)")
margin_df = pd.DataFrame(margin_series)
if margin_df.dropna(how="all").empty:
    st.info("계산 가능한 수익성 데이터가 없습니다.")
else:
    st.line_chart(margin_df)

# DCF 비교 — 모든 종목에 "동일한" 가정을 적용해야 비교가 의미 있으므로, 종목별이 아니라
# 이 섹션 하나에서 공통 가정을 받는다. 2-stage(fade)·민감도 격자는 Stock Research 전용으로
# 남겨두고, 여기서는 단일 성장률 DCF로만 비교한다.
st.subheader("DCF 비교")
st.caption(
    "모든 종목에 동일한 DCF 가정을 적용해 괴리율(%)과 시장 내재 성장률(%)을 비교합니다. "
    "괴리율·내재 성장률은 통화와 무관해 직접 비교할 수 있지만, 내재가치·현재가 절대금액은 "
    "종목마다 통화가 달라 직접 비교하지 마세요(참고용으로만 각자 통화로 표기)."
)

dcol1, dcol2, dcol3, dcol4 = st.columns(4)
dcf_growth_rate = dcol1.slider("연간 FCF 성장률", 0.0, 0.30, 0.08, 0.01, format="%.2f", key="cmp_growth_rate")
dcf_discount_rate = dcol2.slider("요구수익률 (COE)", 0.03, 0.20, 0.10, 0.01, format="%.2f", key="cmp_discount_rate")
dcf_terminal_growth = dcol3.slider("영구성장률", 0.0, 0.05, 0.025, 0.005, format="%.3f", key="cmp_terminal_growth")
dcf_years = dcol4.slider("투영 연수", 3, 10, 5, 1, key="cmp_years")

if not valuation_by_resolved:
    st.info("DCF를 비교할 종목이 없습니다.")
elif st.button("DCF 비교 계산"):
    dcf_rows = []
    for resolved, valuation_row in valuation_by_resolved.items():
        currency = valuation_row["currency"]
        price = valuation_row["price"]
        base_row = {
            "티커": valuation_row["ticker"],
            "종목명": valuation_row["name"],
            "현재가": format_money(price, currency),
        }

        # 종목별 실패 격리: 어느 한 종목에서 FCF/발행주식수/역산 성장률 중 무엇이 없거나
        # 계산이 안 되더라도 그 칸만 N/A로 두고, 나머지 종목은 정상적으로 계산을 이어간다.
        try:
            snapshot = get_company_snapshot(resolved)
            shares_outstanding = snapshot.get("shares_outstanding")
            if not shares_outstanding:
                dcf_rows.append({**base_row, "내재가치": "N/A", "괴리율": "N/A", "시장 내재 성장률": "N/A", "비고": "발행주식수 없음"})
                continue

            latest_fcf = get_latest_free_cash_flow(resolved)
        except TickerNotFoundError:
            dcf_rows.append({**base_row, "내재가치": "N/A", "괴리율": "N/A", "시장 내재 성장률": "N/A", "비고": "FCF 없음"})
            continue

        if latest_fcf is None or latest_fcf <= 0:
            dcf_rows.append(
                {**base_row, "내재가치": "N/A", "괴리율": "N/A", "시장 내재 성장률": "N/A", "비고": "FCF 없음 또는 0 이하"}
            )
            continue

        try:
            dcf_result = simplified_dcf(
                latest_fcf=latest_fcf,
                growth_rate=dcf_growth_rate,
                discount_rate=dcf_discount_rate,
                terminal_growth=dcf_terminal_growth,
                years=dcf_years,
                shares_outstanding=shares_outstanding,
            )
        except ValueError as e:
            dcf_rows.append({**base_row, "내재가치": "N/A", "괴리율": "N/A", "시장 내재 성장률": "N/A", "비고": str(e)})
            continue

        intrinsic = dcf_result["intrinsic_value_per_share"]
        upside = (intrinsic - price) / price

        try:
            implied_g = implied_growth_rate(
                latest_fcf=latest_fcf,
                discount_rate=dcf_discount_rate,
                terminal_growth=dcf_terminal_growth,
                years=dcf_years,
                shares_outstanding=shares_outstanding,
                current_price=price,
            )
            implied_g_display = f"{implied_g * 100:.1f}%"
            note = ""
        except ImpliedGrowthNotFoundError:
            # 에러가 아니라 정보성 결과다 — 현재가가 이 가정(COE 등) 아래 상식적 성장률
            # 범위를 벗어났다는 뜻으로, 대개 시장이 이 종목에 그만큼 높은 성장을 기대하고
            # 있다는(=고평가일 수 있다는) 신호로 읽을 수 있다.
            implied_g_display = "N/A"
            note = "역산 불가 (현재가가 상식적 성장률 범위 밖 — 고평가 가능성)"
        except ValueError as e:
            implied_g_display = "N/A"
            note = str(e)

        dcf_rows.append(
            {
                **base_row,
                "내재가치": format_money(intrinsic, currency),
                "괴리율": f"{upside * 100:+.1f}%",
                "시장 내재 성장률": implied_g_display,
                "비고": note,
            }
        )

    dcf_df = pd.DataFrame(dcf_rows)[["티커", "종목명", "현재가", "내재가치", "괴리율", "시장 내재 성장률", "비고"]]
    st.dataframe(dcf_df, width="stretch", hide_index=True)
    st.caption(f"DCF 가정: 성장률 {dcf_growth_rate * 100:.1f}% · COE {dcf_discount_rate * 100:.1f}% · 영구성장률 {dcf_terminal_growth * 100:.1f}% · 투영 {dcf_years}년 (모든 종목 동일 적용)")
