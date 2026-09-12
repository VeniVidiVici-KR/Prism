"""AI 해설 (하단부 분할 라운드, 2026-09 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 원본에서 이 섹션은 간단 DCF(views/stock/simple_dcf.py)의 `else:` 블록 안에 중첩돼 있었다 —
DCF가 계산되지 못하면(latest_fcf 없음 등) 이 섹션 자체가 렌더링되지 않았다. 조립부가
simple_dcf.render()의 반환값(dcf_result)이 None이 아닐 때만 이 render()를 호출해 그 게이팅을
그대로 재현한다.
"""

import streamlit as st

from valley.ai_commentary import generate_commentary
from valley.llm import ENGINE_LABELS


def render(*, financial_history, snapshot: dict, dcf_result: dict, selected_engine: str) -> None:
    st.subheader("AI 해설")
    # ⚠️ 카드 레이아웃 라운드(2026-09) — 버튼과 그 결과(생성된 해설)가 "하나의 상호작용"이라
    # 카드로 묶는다. 데이터가 아예 없어 안내만 뜨는 경우(financial_history is None)는 묶을
    # 내용이 없어 카드를 열지 않는다(다른 섹션들의 "실패 시 카드 생략" 관례와 동일).
    if financial_history is None:
        # 오늘은 latest_fcf가 None일 때 DCF가 먼저 "계산 불가"로 멈추기 때문에 이 지점에
        # financial_history is None으로 도달할 일이 없지만(둘 다 yfinance_financials_failed에서
        # 비롯됨), 그건 두 게이팅이 우연히 맞물려 있는 것뿐이라 명시적으로 한 번 더 막아둔다.
        # DCF 게이팅 로직이 나중에 바뀌면(예: 다른 FCF 소스 허용) 여기가 소리 없이 깨질 수 있다.
        st.info("yfinance 재무 데이터가 없어 AI 해설을 생성할 수 없습니다.")
        return

    with st.container(border=True):
        if st.button("AI 해설 생성하기"):
            with st.spinner(f"AI({ENGINE_LABELS[selected_engine]})가 해설을 작성하고 있습니다..."):
                try:
                    commentary = generate_commentary(snapshot, financial_history, dcf_result, engine=selected_engine)
                except RuntimeError as e:
                    st.error(str(e))
                else:
                    st.markdown(f"**🤖 AI 생성 해설 · {ENGINE_LABELS[selected_engine]}**")
                    st.markdown(commentary)
