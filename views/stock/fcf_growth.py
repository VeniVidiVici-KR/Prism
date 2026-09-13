"""과거 FCF 증가율 (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동).

감으로 넣는 성장률 가정에 실측 앵커를 제공한다. get_free_cash_flow_history를 DCF 계산(1개년
또는 3개년 평균)과 별개로 더 긴 기간(4개년)으로 다시 불러와 계산한다.

⚠️ 아래 DCF의 FCF≤0 게이팅(st.stop())과 무관하게 항상 렌더링한다 — 전 구간 FCF가 음수인
적자 성장주(예: RIVN)일수록 "FCF가 개선 추세인가"가 가장 궁금한 대상인데, 예전엔 DCF 섹션
안에 중첩돼 있어 DCF가 st.stop()으로 멈추면 이 표까지 통째로 안 보였다(정리 라운드 과제).
"세 성장률 대조"(시장 내재 성장률 포함)만 DCF 성공 시에만 의미가 있어 그 부분은 간단 DCF
섹션(views/stock/simple_dcf.py)에 남아있다 — 여기서 계산한 growth를 그 섹션에 반환해 재사용
하므로(중복 조회 없음), 조립부가 이 render()의 반환값을 simple_dcf.render()에 그대로 넘겨야
한다.

⚠️ pending 스테이징(sr_growth_rate_pending/sr_growth_clamp_notice) — "이 값을 가정으로
쓰기" 버튼은 여기서 세션 키를 "쓰기"만 한다. 실제 슬라이더 값 반영("읽기+소비")은
stock_research.py 사이드바 블록(슬라이더 렌더링 직전)에서 일어난다 — 이 파일을 옮겨도 그
소비 순서는 그대로다(사이드바 코드 자체는 이번 라운드에서 옮기지 않았고, 매 rerun마다 이
섹션보다 먼저 실행되기 때문).

⚠️ 통화 불일치 라운드(2026-09) — 증가율(%)은 KRW/KRW 같은 비율이라 currency_mismatch(ADR)
종목에서도 안전하다(실측: KT FCF YoY 계산 정상). 다만 표에 함께 찍는 **절대 FCF 금액**은
get_free_cash_flow_history가 raw 재무제표(공시통화)를 그대로 주므로, 거래통화(currency)로
라벨링하면 거짓말이 된다(예: ₩900,771,000,000를 "$900,771,000,000.00"로 표시) — 계산이
멀쩡해서 오히려 더 위험한 "조용히 틀린" 케이스다. currency_mismatch면 절대금액 표시만
financial_currency로 바꾸고 그 이유를 캡션으로 밝힌다(막지는 않는다 — 증가율 자체는 유효한
정보이므로).
"""

import pandas as pd
import streamlit as st

from valley.data import TickerNotFoundError
from valley.formatting import format_money
from valley.valuation import fcf_growth_history
from views._cache import get_free_cash_flow_history


def render(
    *,
    resolved_ticker: str,
    currency: str,
    use_fade: bool,
    currency_mismatch: bool = False,
    financial_currency: str | None = None,
) -> dict | None:
    st.subheader("과거 FCF 증가율")
    try:
        fcf_history_for_growth = get_free_cash_flow_history(resolved_ticker, years=4)
    except TickerNotFoundError as e:
        st.info(f"과거 FCF 증가율을 계산할 수 없습니다: {e}")
        return None

    growth = fcf_growth_history(fcf_history_for_growth)

    # ⚠️ 카드 레이아웃 라운드(2026-09) — CAGR·연도별 표·버튼은 "과거 FCF가 실제로 어떻게
    # 늘었나"라는 한 질문에 답하는 한 덩어리라 카드로 묶는다.
    with st.container(border=True):
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

        # ⚠️ 통화 불일치면 절대 FCF 금액은 거래통화(currency)가 아니라 원본 재무제표 통화
        # (financial_currency)로 라벨링한다 — 값 자체는 원래도 그 통화였다(get_free_cash_flow_history가
        # raw 재무제표를 그대로 주므로), 라벨만 정직하게 고친다.
        amount_currency = financial_currency if currency_mismatch and financial_currency else currency
        st.caption(f"단위: {amount_currency}")
        if currency_mismatch:
            st.caption(
                f"⚠️ 이 종목은 주가 통화({currency})와 재무제표 통화({financial_currency})가 달라, "
                f"위 금액은 재무제표 원본 통화({financial_currency}) 기준입니다(환산되지 않음) — "
                "증가율(%)은 통화와 무관해 그대로 신뢰할 수 있습니다."
            )
        growth_df = pd.DataFrame(
            [
                {
                    "연도": item["year"],
                    "FCF": format_money(item["fcf"], amount_currency),
                    "전년比 증가율": f"{item['yoy'] * 100:+.1f}%" if item["yoy"] is not None else item["yoy_reason"],
                }
                for item in growth["yearly"]
            ]
        ).set_index("연도")
        st.dataframe(growth_df, width="stretch")

        # ⚠️ 감쇠 모드에서도 이 버튼을 지원한다 — NVDA처럼 CAGR이 극단적으로 높은 종목일수록
        # 감쇠(초기→말기 선형 감소)가 가장 필요한데, 이전엔 감쇠 모드에서만 버튼을 꺼둬 오히려
        # 거꾸로였다. 감쇠 모드에서는 "초기 성장률"에만 적용하고(말기 성장률은 손대지 않는다),
        # 반영 대상 세션 키는 사이드바에서 use_fade에 따라 이미 정해져 있다
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

    return growth
