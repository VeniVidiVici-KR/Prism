"""증시 주변자금 / 신용공여(KOFIA) — 한국 전용 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import os

import pandas as pd
import streamlit as st

from valley.kofia import KofiaDataError
from views._cache import get_credit_balance_history, get_securities_market_capital_history


def render(country_code: str) -> None:
    if country_code != "KR":
        return

    st.divider()
    st.header("증시 주변자금 / 신용공여")
    st.caption(
        "출처: 금융투자협회(KOFIA), 공공데이터포털(data.go.kr) \"금융투자협회종합통계정보\" "
        "경유 · 일별 · 코스피(유가증권)/코스닥 구분. ⚠️ 위 '신용융자' 카테고리의 월별 ECOS "
        "계열과는 절대 같은 차트에 섞지 않습니다 — 주기가 달라(월별 vs 일별) 오해를 부릅니다. "
        "실측 대조 결과 이 API의 최신 관측치가 ECOS 월말값과 정의상 동일함을 확인했습니다"
        "(2026-07-31 기준 원 단위까지 일치)."
    )
    st.caption(
        "⚠️ 데이터 지연: 이 API는 갱신이 며칠 늦을 수 있어 KOFIA 공식 웹 화면보다도 최신 "
        "관측치가 오래됐을 수 있습니다 — 아래 '기준일'이 오늘이 아닐 수 있으니 반드시 확인하세요. "
        "⚠️ 이 데이터는 공시 원인의 '결과'만 보여줍니다 — 청약 수요·반대매매 사유 같은 맥락은 "
        "담기지 않습니다."
    )

    if not os.environ.get("KOFIA_API_KEY"):
        st.info(
            "KOFIA_API_KEY가 설정되어 있지 않아 신용공여·증시자금 데이터를 표시할 수 없습니다 — "
            "https://www.data.go.kr 에서 \"금융위원회_금융투자협회종합통계정보\" API 활용신청 후 "
            "발급받은 서비스키를 .env에 KOFIA_API_KEY로 추가하세요."
        )
    elif st.button("증시 주변자금 / 신용공여 불러오기", key="btn_kofia"):
        st.session_state.macro_kofia_loaded = True

    if st.session_state.get("macro_kofia_loaded") and os.environ.get("KOFIA_API_KEY"):
        try:
            credit_rows = get_credit_balance_history()
        except KofiaDataError as e:
            st.warning(f"신용공여 잔고 조회에 실패했습니다: {e}")
            credit_rows = None

        try:
            capital_rows = get_securities_market_capital_history()
        except KofiaDataError as e:
            st.warning(f"증시자금 조회에 실패했습니다: {e}")
            capital_rows = None

        if credit_rows:
            with st.container(border=True):
                credit_df = pd.DataFrame(credit_rows)
                credit_df["기준일"] = pd.to_datetime(credit_df["basDt"], format="%Y%m%d")
                credit_df = credit_df.sort_values("기준일")

                st.subheader("신용거래융자 추이 (전체·유가증권·코스닥)")
                chart_df = credit_df.set_index("기준일")[["crdTrFingWhl", "crdTrFingScrs", "crdTrFingKosdaq"]].rename(
                    columns={"crdTrFingWhl": "전체", "crdTrFingScrs": "유가증권", "crdTrFingKosdaq": "코스닥"}
                )
                st.line_chart(chart_df)

                latest = credit_df.iloc[-1]
                previous = credit_df.iloc[-2] if len(credit_df) >= 2 else None
                _JO = 1e12  # 조 원 — 국내 신용융자 잔고는 통상 수십조원 규모라 원 단위 그대로면 표가 안 읽힌다.
                credit_summary_rows = []
                for label, field in [
                    ("신용거래융자 전체", "crdTrFingWhl"),
                    ("신용거래융자 유가증권", "crdTrFingScrs"),
                    ("신용거래융자 코스닥", "crdTrFingKosdaq"),
                    ("신용거래대주 전체", "crdTrLndrWhl"),
                ]:
                    latest_val = latest[field]
                    prev_val = previous[field] if previous is not None else None
                    credit_summary_rows.append(
                        {
                            "항목": label,
                            "최근 값(조원)": latest_val / _JO,
                            "전일비 증감(조원)": (latest_val - prev_val) / _JO if prev_val is not None else None,
                        }
                    )
                st.dataframe(
                    pd.DataFrame(credit_summary_rows).set_index("항목").style.format(
                        {"최근 값(조원)": "{:,.2f}", "전일비 증감(조원)": "{:+,.2f}"}
                    ),
                    width="stretch",
                )
                st.caption(
                    f"기준일: {latest['기준일']:%Y-%m-%d} · 원본 단위는 원(KRW), 표에서는 조 원으로 "
                    "환산해 표시합니다(전일비는 직전 영업일 대비)."
                )

        if capital_rows:
            with st.container(border=True):
                capital_df = pd.DataFrame(capital_rows)
                capital_df["기준일"] = pd.to_datetime(capital_df["basDt"], format="%Y%m%d")
                capital_df = capital_df.sort_values("기준일")

                st.subheader("증시자금추이 (투자자예탁금 등)")
                latest_c = capital_df.iloc[-1]
                previous_c = capital_df.iloc[-2] if len(capital_df) >= 2 else None
                _JO = 1e12
                capital_summary_rows = []
                for label, field in [
                    ("투자자예탁금", "invrDpsgAmt"),
                    ("위탁매매 미수금", "brkTrdUcolMny"),
                ]:
                    latest_val = latest_c[field]
                    prev_val = previous_c[field] if previous_c is not None else None
                    capital_summary_rows.append(
                        {
                            "항목": label,
                            "최근 값(조원)": latest_val / _JO,
                            "전일비 증감(조원)": (latest_val - prev_val) / _JO if prev_val is not None else None,
                        }
                    )
                st.dataframe(
                    pd.DataFrame(capital_summary_rows).set_index("항목").style.format(
                        {"최근 값(조원)": "{:,.2f}", "전일비 증감(조원)": "{:+,.2f}"}
                    ),
                    width="stretch",
                )
                st.caption(f"기준일: {latest_c['기준일']:%Y-%m-%d} · 원본 단위는 원(KRW), 표에서는 조 원으로 환산해 표시합니다.")
