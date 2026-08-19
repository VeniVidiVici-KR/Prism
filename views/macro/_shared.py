"""Macro Indicator 섹션 여러 곳이 공유하는 캐시 로더·레이아웃 헬퍼·상수 (파일 분할 라운드, 2026-08).

⚠️ 파일 분할 이전에는 views/macro_indicator.py 한 파일 안에서 이미 이 이름들을 여러 섹션이
공유했다(_load_series는 카탈로그·혼합지표 이외 거의 모든 섹션이, _load_composite_series는
혼합지표·겹쳐보기가, _balanced_row_sizes는 카탈로그·혼합지표 버튼 렌더가 함께 썼다) — 분할
후에도 각자 새로 정의하지 않고 여기 하나로 모아 재사용한다(모듈 임포트는 1회만 일어나므로
st.cache_data 캐시 identity도 그대로 유지된다).
"""

import streamlit as st

from valley.composite import get_composite_series
from valley.macro import MacroDataError, get_indicator_series

MAX_BUTTONS_PER_ROW = 5

# "국가부채/GDP" 실적+전망 결합 뷰 전용(전망 반영 라운드, 2026-08) — 실적 key -> 전망 key
# 매핑과 그 역방향. 실적/전망 버튼 중 어느 쪽을 눌러도 항상 같은 결합 뷰(실적 실선 + IMF 전망
# 점선)를 보여주기 위해 양방향으로 찾는다. valley/macro.py "재정" 카테고리 주석 참고.
# ⚠️ views/macro/catalog.py(단일 지표 뷰)와 views/macro/debt_gdp.py(한미 비교) 양쪽이 쓴다.
DEBT_GDP_PROJECTION_OF = {"US_DEBT_GDP": "US_DEBT_GDP_PROJECTION", "KR_DEBT_GDP": "KR_DEBT_GDP_PROJECTION"}
DEBT_GDP_ACTUAL_OF = {v: k for k, v in DEBT_GDP_PROJECTION_OF.items()}
DEBT_GDP_COUNTRY_LABEL = {"US_DEBT_GDP": "미국", "KR_DEBT_GDP": "한국"}


@st.cache_data(ttl=3600, show_spinner=False)
def load_series(key: str):
    return get_indicator_series(key)


@st.cache_data(ttl=3600, show_spinner=False)
def load_composite_series(key: str):
    return get_composite_series(key)


def balanced_row_sizes(n: int, max_cols: int = MAX_BUTTONS_PER_ROW) -> list[int]:
    """n개 항목을 max_cols 이하 열의 여러 줄에 최대한 고르게 나눈다.

    예전엔 max_cols개씩 고정으로 잘라(예: 10개를 4개씩) 마지막 줄만 휑하게 남았다
    (4+4+2). 대신 줄 수를 먼저 정하고(ceil(n/max_cols)) 그 줄 수로 최대한 균등하게
    나눠(10개, 최대 5열 → 5+5) 마지막 줄이 비어 보이지 않게 한다. 반환값은 각 줄에 넣을
    항목 수 리스트(예: [5, 5] 또는 [3, 3]).
    """
    if n <= 0:
        return []
    if n <= max_cols:
        return [n]
    rows = -(-n // max_cols)  # ceil division
    base, extra = divmod(n, rows)
    return [base + 1 if i < extra else base for i in range(rows)]


__all__ = [
    "MacroDataError",
    "MAX_BUTTONS_PER_ROW",
    "DEBT_GDP_PROJECTION_OF",
    "DEBT_GDP_ACTUAL_OF",
    "DEBT_GDP_COUNTRY_LABEL",
    "load_series",
    "load_composite_series",
    "balanced_row_sizes",
]
