"""정식 DCF (FCFF → WACC → EV → Equity) (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 사이드바에서 "정식"을 선택했을 때만 렌더링하는 순수 추가 섹션이다 — 이 호출 여부(밸류에이션
방식 라디오 분기) 자체는 조립부가 맡는다(이 함수는 호출됐다는 전제하에 무조건 subheader부터
그린다). 아래(간단 DCF) 섹션의 else 블록은 latest_fcf<=0이면 st.stop()으로 페이지 전체를
멈춘다(예: 은행은 OCF-Capex 기반 FCF가 종종 음수) — 그 지점 "이후"에 이 섹션을 두면 금융업·
적자 종목에서 정식 DCF 경고조차 안 뜨고 조용히 사라져버린다(실측 확인: JPM으로 "정식" 선택 시
아무 것도 안 뜸). 그래서 원본과 마찬가지로 조립부에서 이 섹션을 간단 DCF보다 먼저 호출한다 —
이 섹션은 fcff_dcf 내부에 자체 FCFF≤0 가드가 있어(ValueError를 st.error로만 잡고 st.stop()은
하지 않음) 간이 DCF의 성공 여부와 완전히 독립적으로 항상 도달 가능하다.

⚠️ is_financial 분기는 원본에서 이 섹션(valuation_method=="정식") 안쪽에 한 번 더 중첩돼
있었다 — 은행·보험은 부채가 영업의 원재료라 WACC 전제 자체가 성립하지 않아 계산을 아예
하지 않고 경고만 낸다(위쪽 P/B×ROE·DDM 안내로 유도). 그 중첩 그대로 이 함수 안에 남긴다
(views.stock.bank_valuation과 달리 여기는 "정식 DCF를 볼지" 자체가 이미 상위 조건이라, 그
안에서 다시 "그런데 금융업이면" 분기하는 구조가 자연스럽다).
"""

import pandas as pd
import streamlit as st

from valley.formatting import format_money
from valley.valuation import (
    CoeSuggestionUnavailableError,
    FcffInputsUnavailableError,
    capm_coe,
    compute_cost_of_debt,
    compute_effective_tax_rate,
    compute_wacc,
    fcf_to_fcff,
    fcff_dcf,
    suggest_coe,
)
from valley.data import TickerNotFoundError
from views._cache import get_fcff_inputs


def render(
    *,
    currency: str,
    snapshot: dict,
    resolved_ticker: str,
    latest_fcf: float | None,
    yfinance_financials_failed: bool,
    growth_rate_initial: float,
    growth_rate_terminal: float | None,
    wacc_beta: float,
    wacc_risk_premium: float,
    terminal_growth: float,
    years: int,
    discount_rate: float,
    is_financial: bool,
) -> None:
    st.divider()
    st.subheader("정식 DCF (FCFF → WACC → EV → Equity)")
    st.caption(
        "자본구조(부채)를 반영해 기업가치(EV)를 먼저 구하고 순부채를 차감해 주주가치를 "
        "산출합니다. ⚠️ 간이 방식보다 '더 정확한 값'이 아니라 '자본구조를 반영한 다른 관점'"
        "입니다 — COE는 여전히 베타·ERP 가정에 좌우되는 추정치이고, 부채비용·실효세율은 최근 "
        "재무제표에서 역산한 근사치라 자본구조가 기중 크게 변한 기업에서는 왜곡될 수 있습니다."
    )

    if is_financial:
        # ⚠️ 은행·보험은 부채가 영업의 원재료라 "자본조달비용"이라는 WACC의 전제 자체가
        # 성립하지 않는다 — 계산을 아예 하지 않고 위 P/B×ROE·DDM으로 안내한다(이미 제공 중).
        st.warning(
            "이 종목은 금융업(은행·보험 등)입니다. 부채가 영업의 원재료인 금융업은 WACC(자본조달"
            "비용) 개념 자체가 성립하지 않아 정식 DCF를 계산하지 않습니다. 위 P/B×ROE·DDM을 "
            "이용하세요."
        )
        return
    if not snapshot.get("shares_outstanding"):
        st.warning("발행주식수 정보가 없어 정식 DCF를 계산할 수 없습니다.")
        return
    if latest_fcf is None:
        st.warning(
            "재무 데이터가 없어 정식 DCF 계산을 진행할 수 없습니다."
            + (" yfinance 재무제표 조회 실패로 위에서 DART 대체 재무만 표시했습니다 (정식 DCF는 이 파일럿 범위 밖)." if yfinance_financials_failed else "")
        )
        return

    try:
        fcff_inputs = get_fcff_inputs(resolved_ticker)
    except TickerNotFoundError as e:
        st.warning(f"정식 DCF에 필요한 재무 데이터를 가져오지 못했습니다: {e}")
        return

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
        return

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
        return

    capm_coe_value = capm_coe(rf_info["rf"], wacc_beta, wacc_risk_premium)
    wacc_result = compute_wacc(
        market_cap=snapshot.get("market_cap"),
        total_debt=fcff_inputs["total_debt"],
        coe=capm_coe_value,
        cost_of_debt_pretax=cost_of_debt_result["rate"],
        tax_rate=tax_result["rate"],
    )

    # ⚠️ 카드 레이아웃 라운드(2026-09) — WACC 구성부터 EV→Equity 브릿지·결과·COE 비교까지
    # 전부 "정식 DCF가 실제로 계산해낸 것"이라는 한 흐름이라 카드 하나로 묶는다(라운드가
    # 명시한 "WACC 구성 + EV→Equity 브릿지" 권장에 결과 지표·비교까지 자연스럽게 이어 붙였다
    # — 별도 카드로 쪼개면 "WACC가 이 결과로 이어진다"는 흐름이 끊긴다).
    with st.container(border=True):
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
            return

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
            return

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
