"""요약 내보내기 (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 원본에서 이 섹션은 간단 DCF(views/stock/simple_dcf.py)의 `else:` 블록 안에 중첩돼 있었다 —
DCF가 정상 계산된 경우에만 이 지점에 도달했다("DCF가 정상 계산된 경우에만"이라고 원본
주석에도 명시돼 있음). 조립부가 simple_dcf.render()의 반환값(dcf_result)이 None이 아닐 때만
이 render()를 호출해 그 게이팅을 그대로 재현한다.
"""

from datetime import date

import streamlit as st

from valley.report import build_markdown_summary
from views.stock._shared import kr_stock_code


def render(
    *,
    snapshot: dict,
    target_valuation: dict,
    dcf_result: dict,
    coe_source: dict,
    implied_g: float | None,
    resolved_ticker: str,
) -> None:
    st.subheader("요약 내보내기")
    # ⚠️ 카드 레이아웃 라운드(2026-09) — 마크다운 미리보기와 다운로드 버튼은 "이 결과물을
    # 내보낸다"는 한 동작이라 카드로 묶는다.
    with st.container(border=True):
        summary_md = build_markdown_summary(
            snapshot=snapshot,
            valuation_snapshot=target_valuation,
            dcf_result=dcf_result,
            coe_source=coe_source,
            implied_growth=implied_g,
            as_of=date.today(),
        )
        st.code(summary_md, language="markdown")
        file_ticker = kr_stock_code(resolved_ticker)
        st.download_button(
            "마크다운 파일로 다운로드",
            data=summary_md,
            file_name=f"PRISM_{file_ticker}_{date.today():%Y%m%d}.md",
            mime="text/markdown",
        )
