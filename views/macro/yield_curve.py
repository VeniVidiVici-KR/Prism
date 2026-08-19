"""한국 국채 수익률곡선 — 한국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import os

import pandas as pd
import streamlit as st

from valley.toss import TossDataError
from views._cache import get_bond_yield_snapshot
from views._charts import line_chart_with_markers


def render(country_code: str) -> None:
    if country_code != "KR":
        return

    # ⚠️ 국채 라운드(2026-08) — 수익률곡선은 시계열이 아니라 "한 시점의 단면"이라 위 개별
    # 지표 버튼(시계열 line_chart)과는 다른 뷰가 필요하다. line_chart_with_markers(재무추이
    # 라운드에서 만든 헬퍼)가 인덱스를 명목형(N) 축으로 그대로 그려주므로(정렬 순서 보존)
    # 새 차트 헬퍼 없이 재사용한다 — "과하면 만들지 마라"는 지시에 맞춰 표+차트 정도로만
    # 유지했다.
    st.divider()
    st.header("한국 국채 수익률곡선")
    st.caption(
        "국고채 2Y·3Y·5Y·10Y·20Y·30Y의 현재 수익률을 만기 순으로 이어 곡선 모양을 봅니다 — "
        "시계열이 아니라 한 시점의 단면(스냅샷)입니다. 출처: 토스증권 Open API."
    )
    st.caption(
        "⚠️ 기준 시각: 응답에 정확한 시각 정보가 없습니다(실측 확인, timestamp 필드가 null) — "
        "일봉(candles) 최신 종가와 정확히 일치해 '최근 거래일 종가' 기준으로 추정됩니다."
    )

    _toss_key_present = bool(os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET"))
    if not _toss_key_present:
        st.info(
            "TOSS_CLIENT_ID/TOSS_CLIENT_SECRET가 설정되어 있지 않아 수익률곡선을 표시할 수 "
            "없습니다 — 토스증권 오픈 API 개발자센터에서 발급받은 키를 .env에 추가하세요."
        )
    elif st.button("수익률곡선 불러오기", key="btn_kr_yield_curve"):
        st.session_state.macro_yield_curve_loaded = True

    if st.session_state.get("macro_yield_curve_loaded") and _toss_key_present:
        try:
            snapshot = get_bond_yield_snapshot()
        except TossDataError as e:
            st.warning(f"수익률곡선 조회에 실패했습니다: {e}")
        else:
            _maturity_labels = {
                "KR_BOND_2Y": "2Y", "KR_BOND_3Y": "3Y", "KR_BOND_5Y": "5Y",
                "KR_BOND_10Y": "10Y", "KR_BOND_20Y": "20Y", "KR_BOND_30Y": "30Y",
            }
            _curve_order = ["KR_BOND_2Y", "KR_BOND_3Y", "KR_BOND_5Y", "KR_BOND_10Y", "KR_BOND_20Y", "KR_BOND_30Y"]
            curve_keys = [k for k in _curve_order if k in snapshot]

            if len(curve_keys) < 2:
                st.info("표시할 수 있는 만기 구간이 부족합니다.")
            else:
                with st.container(border=True):
                    # ⚠️ 카드 레이아웃 라운드(2026-08) — 10Y-2Y 곡선 형태는 원래 맨 아래
                    # 캡션이었다. CPI 라운드에서 헤드라인 변화율을 표 위로 올린 것과 같은
                    # 원칙으로, 곡선을 한눈에 읽을 때 가장 먼저 궁금한 값(우상향/역전)을
                    # 카드 상단 st.metric으로 올린다 — 계산 자체(10Y-2Y 부호 판정, 아래 주석
                    # 참고)는 그대로다. ⚠️ 10Y-2Y 부호로 형태를 판정한다 — 위 혼합지표의
                    # KR_YIELD_SPREAD_10Y2Y와 같은 정의라 두 뷰가 서로 모순되지 않는다(시장
                    # 표준 관행: 10Y-2Y가 장단기 금리 역전의 대표 지표).
                    if "KR_BOND_2Y" in snapshot and "KR_BOND_10Y" in snapshot:
                        spread_10y2y = snapshot["KR_BOND_10Y"] - snapshot["KR_BOND_2Y"]
                        shape = "우상향(정상)" if spread_10y2y > 0 else ("역전" if spread_10y2y < 0 else "평탄")
                        st.metric("곡선 형태 (10Y-2Y)", f"{shape} ({spread_10y2y:+.3f}%p)")

                    curve_df = pd.DataFrame(
                        {"수익률(%)": [snapshot[k] for k in curve_keys]},
                        index=[_maturity_labels[k] for k in curve_keys],
                    )
                    line_chart_with_markers(curve_df)
                    st.dataframe(curve_df.style.format({"수익률(%)": "{:.3f}%"}), width="stretch")
                    st.caption("만기 순서대로 정렬.")
