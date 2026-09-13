"""P/B×ROE + DDM (금융업 전용) (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동).

⚠️ 원본에서 두 섹션 다 `if _is_financial_industry(...):` 최상위 블록 하나에 함께 들어있었다
(P/B×ROE 다음에 바로 DDM). is_financial 판정 자체는 views.stock._shared.is_financial_industry로
옮겼고(조립부에서 한 번만 계산), 이 모듈은 그 결과(bool)만 받아 원본에 있던 자리 그대로
("금융업이 아니면 이 섹션 전체를 그리지 않는다") 맨 앞에서 분기한다.

⚠️ 카드 레이아웃 라운드(2026-09) — 이 라운드 지시대로 P/B×ROE·DDM을 각각 별도 카드로 나눴다
(둘 다 "정당가치 계산"이라는 답을 향해 가지만, COE 계산방식(단순형 ROE/COE vs Gordon
Growth)이 서로 다른 별개의 밸류에이션이라 나란히 두면 오히려 헷갈린다). 금융업 경고는 두
카드 다 적용되는 공통 전제라 카드 밖(맨 위)에 한 번만 둔다.

⚠️ 통화 불일치 라운드(2026-09) — P/B×ROE는 BVPS(raw 재무제표, 공시통화)와 주가(거래통화)를
섞어 currency_mismatch 종목(ADR)에서 결과가 터진다(실측: 신한 ADR "SHG" 괴리율 +118,622%).
이 카드는 snapshot["currency_mismatch"]가 True면 통째로 막고 원주 안내로 유도한다. DDM은
D0가 t.dividends(ADR 실제 지급액, 이미 거래통화로 옴 — KT/SHG 실측 확인)에서 오므로 안전해
막지 않되, 옆 카드가 막힌 화면에서 이것만 멀쩡하면 혼란스러우니 안전한 이유를 캡션으로
짧게 밝힌다.
"""

import pandas as pd
import streamlit as st

from valley.formatting import format_money, format_multiple, format_percent
from valley.peers import ValuationDataError
from valley.valuation import dividend_growth_history, gordon_ddm, justified_pbr
from views._cache import get_annual_dividend_history, get_roe_history


def render(
    *,
    resolved_ticker: str,
    currency: str,
    snapshot: dict,
    target_valuation: dict,
    discount_rate: float,
    is_financial: bool,
) -> None:
    if not is_financial:
        return

    # 계산 자체를 막지는 않는다 — FCF 데이터가 있으면 아래 수치는 그대로 보여주되, "계산되니
    # 맞는 값"이라는 착각을 막기 위해 경고만 명확히 띄운다.
    st.warning(
        "이 종목은 금융업(은행·보험 등)입니다. 이 DCF는 제조업 FCF(영업현금흐름-자본적지출) "
        "기반 모델이라 금융업에는 구조적으로 부적합할 수 있습니다 — 금융업은 통상 배당할인모형"
        "(DDM)이나 P/B·ROE 기반으로 평가합니다. 아래 수치는 참고용으로만 보세요."
    )

    currency_mismatch = snapshot.get("currency_mismatch", False)

    st.subheader("P/B × ROE 밸류에이션 (금융업)")
    with st.container(border=True):
        if currency_mismatch:
            st.warning(
                f"이 종목은 주가 통화(USD 등)와 재무제표 통화({snapshot.get('financial_currency')})가 "
                "달라 P/B×ROE를 계산할 수 없습니다. 한국 기업의 ADR인 경우 6자리 원주 코드로 "
                "조회하면 정상 계산됩니다."
            )
        else:
            _render_pb_roe(
                resolved_ticker=resolved_ticker, currency=currency, snapshot=snapshot,
                target_valuation=target_valuation, discount_rate=discount_rate,
            )

    # 💰 DDM(배당할인모형) — P/B×ROE와 나란히 제공하는 은행·보험 밸류에이션의 또 다른 축.
    # ⚠️ COE는 위 DCF·P/B×ROE와 같은 discount_rate(사이드바 값)를 그대로 재사용한다 — 같은
    # 종목에서 세 밸류에이션의 COE가 다르면 혼란스럽다.
    st.subheader("DDM (배당할인모형)")
    with st.container(border=True):
        if currency_mismatch:
            st.caption(
                "✅ 이 지표는 실제 지급된 배당(이미 주가와 같은 거래통화 기준)을 쓰므로 "
                "통화 불일치의 영향을 받지 않습니다."
            )
        st.caption(
            "은행·보험의 또 다른 축 밸류에이션입니다. 주당가치 = D1 / (COE − g), D1 = D0 × (1 + g) — "
            "배당이 매년 g로 영구히 성장한다는 가정(Gordon Growth)에 기반합니다."
        )
        _render_ddm(resolved_ticker=resolved_ticker, currency=currency, snapshot=snapshot, discount_rate=discount_rate)


def _render_pb_roe(
    *, resolved_ticker: str, currency: str, snapshot: dict, target_valuation: dict, discount_rate: float
) -> None:
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
                # ⚠️ 카드 레이아웃 라운드(2026-09) — 원래 라벨("PBR 괴리율 (정당 P/B ÷ 현재
                # PBR − 1)")이 st.metric 말줄임 기준보다 길어 좁은 화면에서 잘렸다(실측
                # 확인). 계산식은 help 툴팁으로 옮기고 라벨은 숫자 위주로 줄였다 — 표시되는
                # 값 자체는 그대로다.
                st.metric(
                    "PBR 괴리율", f"{bank_pbr_gap * 100:+.1f}%",
                    help="정당 P/B ÷ 현재 PBR − 1",
                )
            else:
                st.info("BVPS/PBR 정보가 없어 괴리율을 계산할 수 없습니다.")

    st.caption(
        "⚠️ 한계: 정당 P/B = ROE/COE는 현재 ROE가 앞으로도 지속된다는 가정에 기반한 단순형으로, "
        "성장률은 반영하지 않았습니다. 일회성 손익(충당금·자산매각 등)으로 ROE가 변동하면 결과가 "
        "왜곡될 수 있으니, 단년·3개년 평균 ROE를 함께 보고 참고용으로만 활용하세요."
    )


def _render_ddm(*, resolved_ticker: str, currency: str, snapshot: dict, discount_rate: float) -> None:
    try:
        bank_dividend_history = get_annual_dividend_history(resolved_ticker, years=5)
    except ValuationDataError as e:
        st.info(f"배당이 없어 DDM을 적용할 수 없습니다: {e}")
        return

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
