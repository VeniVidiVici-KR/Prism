"""간단 DCF 밸류에이션 + 현재 주가 내재 성장률 + 세 성장률 대조 + 민감도 분석
(하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 원본에서 AI 해설·요약 내보내기(views/stock/ai_commentary_section.py,
views/stock/summary_export.py)가 이 섹션의 `else:` 블록 안에 중첩돼 있었다 — 즉 DCF가 여기서
계산되지 못하면(latest_fcf 없음, 발행주식수 없음) 그 두 섹션은 경고조차 없이 통째로
렌더링되지 않았다. 이 함수는 그 게이팅을 조립부에 명시적으로 넘기기 위해
`(dcf_result, implied_g)`를 반환한다 — 둘 다 None이면 조립부가 AI 해설·요약 내보내기를
호출하지 않아야 원본과 동일하다(밸류에이션.py가 target_valuation을 반환해 하위 섹션이 쓰는
것과 같은 패턴).

⚠️ st.stop() 유지 — simplified_dcf()가 ValueError를 던지면(예: 퇴화 입력) 원본처럼 페이지
전체를 멈춘다. 이 경우 함수가 반환되지 않으므로(스크립트 자체가 여기서 끝난다) 조립부의
None 체크는 아예 실행되지 않는다 — 원본도 동일(전체 halt).
"""

import pandas as pd
import streamlit as st

from valley.formatting import format_money
from valley.valuation import ImpliedGrowthNotFoundError, implied_growth_rate, sensitivity_grid, simplified_dcf


def render(
    *,
    latest_fcf: float | None,
    snapshot: dict,
    currency: str,
    growth_rate_initial: float,
    growth_rate_terminal: float | None,
    discount_rate: float,
    terminal_growth: float,
    years: int,
    use_fade: bool,
    yfinance_financials_failed: bool,
    growth: dict | None,
) -> tuple[dict | None, float | None]:
    if latest_fcf is None:
        st.warning(
            "재무 데이터가 없어 DCF 계산을 진행할 수 없습니다."
            + (" yfinance 재무제표 조회 실패로 위에서 DART 대체 재무만 표시했습니다 (DCF는 이 파일럿 범위 밖)." if yfinance_financials_failed else "")
        )
        return None, None
    if not snapshot.get("shares_outstanding"):
        st.warning("발행주식수 정보가 없어 DCF 계산을 진행할 수 없습니다.")
        return None, None

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

    # ⚠️ 카드 레이아웃 라운드(2026-09) — 라운드가 명시한 대로 "내재가치·괴리율·역산 성장률이
    # 한 카드"로 묶는다. 연도별 투영 표는 그 내재가치의 직접 근거라 같은 카드에 포함했고,
    # "세 성장률 대조"는 역산 성장률(시장 내재)의 연장선(과거·가정·시장 세 값을 나란히
    # 비교)이라 같은 흐름으로 붙였다 — fcf_growth.py의 카드(과거 CAGR 자체)와는 별개다
    # (파일 경계상 growth는 인자로 넘겨받은 값을 재사용할 뿐, 그 카드를 다시 열지 않는다).
    # 민감도 표는 열이 많고(성장률×요구수익률 그리드) 독립적으로 읽을 수 있는 내용이라 별도
    # 카드로 뺐다(라운드가 제시한 두 옵션 중 "별도 카드").
    with st.container(border=True):
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

        # 세 성장률 대조 — 과거 FCF CAGR(growth)은 "📈 과거 FCF 증가율" 섹션(views/stock/fcf_growth.py)에서
        # 이미 계산해뒀다(DCF 게이팅과 무관하게 항상 실행되므로 재조회하지 않고 그대로 재사용). 여기서는
        # 방금 구한 시장 내재 성장률(implied_g)까지 더해 세 가지를 나란히 비교한다 — 이 비교 자체는 DCF가
        # 성공했을 때만 의미가 있어 이 함수 안에 남겨둔다.
        if growth is not None:
            st.markdown("**세 성장률 대조**")
            cagr_display = f"{growth['cagr'] * 100:.1f}%" if growth["cagr"] is not None else "계산 불가"
            implied_g_display = f"{implied_g * 100:.1f}%" if implied_g is not None else "역산 불가"
            ct1, ct2, ct3 = st.columns(3)
            ct1.metric("과거 실제 (CAGR)", cagr_display)
            ct2.metric("내 가정" + (" (초기)" if use_fade else ""), f"{growth_rate_initial * 100:.1f}%")
            ct3.metric("시장 내재", implied_g_display)

    st.subheader("민감도 분석 (성장률 × 요구수익률)")
    with st.container(border=True):
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

    return dcf_result, implied_g
