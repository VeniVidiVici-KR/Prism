"""최근 공시 (DART) (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ yfinance 재무 성공/실패와 무관하게 한국 종목이면 항상 표시하는 상시 섹션이다(재무 추이의
DART 폴백과는 성격이 다름 — 폴백이 아니라 항상 노출).
"""

import pandas as pd
import streamlit as st

from valley.dart import DartDataError
from views._cache import get_corp_code, get_dart_disclosure_list
from views.stock._shared import kr_stock_code


def render(resolved_ticker: str, currency: str) -> None:
    st.subheader("최근 공시 (DART)")
    if currency != "KRW":
        # ⚠️ 기존 DART 섹션 로직(else 분기)은 그대로 두고, 이 안내 문구만 최소로 손본다 — 바로
        # 아래에 EDGAR 섹션이 실제로 추가됐는데 "추후 지원 예정"이라는 예전 문구를 그대로 두면
        # 방금 만든 기능과 바로 모순되는 문구가 남는다(정리 라운드 아님, 신규 기능 추가에 딸린
        # 최소 문구 수정으로 한정).
        st.info("현재 DART는 한국 상장기업 공시만 지원합니다. 미국 종목은 아래 'SEC EDGAR' 섹션을 참고하세요.")
    else:
        disclosure_period_labels = {"1개월": 1, "3개월": 3, "6개월": 6, "1년": 12}
        disclosure_period = st.radio(
            "조회 기간", list(disclosure_period_labels.keys()), index=2, horizontal=True, key="sr_disclosure_period"
        )
        keyword_filter = st.text_input(
            "보고서명 키워드 필터 (선택)", value="", key="sr_disclosure_keyword"
        ).strip()

        disclosure_stock_code = kr_stock_code(resolved_ticker)
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
