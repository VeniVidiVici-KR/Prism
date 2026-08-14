"""단 하나의 CSS 주입 지점 (2026-08 디자인 라운드).

⚠️ Streamlit은 개별 위젯 스타일링이 제한적이고, 버전이 바뀌면 CSS 선택자가 깨지기 쉽다 —
그래서 무리하게 주입하지 않고 딱 하나만 한다: 제목(h1/h2/h3 — st.title/header/subheader가
렌더링하는 표준 HTML 태그)의 글자색을 팔레트의 navy.deepest/navy.deep으로 바꾸는 것.
나머지(배경·본문색·표·차트·버튼 강조색 등)는 전부 .streamlit/config.toml의 [theme]과
views/_charts.py의 Altair 팔레트로 처리하며, 여기서는 손대지 않는다.

⚠️ h1/h2/h3라는 "표준 HTML 태그" 선택자를 쓴 이유: Streamlit 내부 클래스명·data-testid는
버전마다 바뀌지만, 마크다운 렌더러가 제목에 실제 h1~h3 태그를 쓰는 것은 접근성 때문에 매우
안정적이다(잘 안 바뀐다). !important는 Streamlit 자체 스타일의 특이도를 확실히 이기기
위해서만 이 한 규칙에 붙였다.

st.metric 값·st.caption은 의도적으로 건드리지 않는다(각각 "기본 스타일 유지", "이미
ink.muted에 가까워 전역 테마로 충분" — 디자인 라운드 지시).
"""

import streamlit as st

_NAVY_DEEPEST = "#081C3F"  # 핵심 수치, 주요 헤드라인 — st.title(h1)
_NAVY_DEEP = "#0A2E5C"  # 섹션 헤더 — st.header/st.subheader(h2/h3)

_CSS = f"""
<style>
h1 {{ color: {_NAVY_DEEPEST} !important; }}
h2, h3 {{ color: {_NAVY_DEEP} !important; }}
</style>
"""


def inject_navy_headings() -> None:
    """페이지마다 한 번만 호출한다(각 views/*.py 최상단)."""
    st.markdown(_CSS, unsafe_allow_html=True)
