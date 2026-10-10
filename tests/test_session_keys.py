"""4개 페이지(views/*.py)의 st.session_state 키가 서로 겹치지 않는지 정적으로 검사한다.

⚠️ 과거 Comparison 페이지 크래시의 근본 원인이 정확히 이거였다 — 페이지마다 접두어 없는
세션 키(예: has_run)를 썼다가, Streamlit의 session_state가 앱 전체(멀티페이지) 공용이라
다른 페이지의 값이 그대로 남아 충돌했다. 그 사고 이후 sr_/cmp_/screener_/macro_ 접두어
규칙이 생겼는데, 이 규칙이 실제로 지켜지고 있는지 그 무엇도 자동으로 검증하지 않고 있었다.

이 테스트는 소스를 import/실행하지 않고 정규식으로 텍스트만 훑는다(순수 정적 분석) —
네트워크·Streamlit 런타임이 전혀 필요 없어 항상 빠르고 안정적으로 돈다.
"""

import re
from pathlib import Path

VIEWS_DIR = Path(__file__).resolve().parent.parent / "views"

# ⚠️ 파일 분할 라운드(2026-08) — macro_indicator·stock_research 둘 다 더 이상 단일 파일이
# 아니라 조립 파일 + 섹션 모듈 패키지(views/macro/, views/stock/)로 나뉜다. 조립 파일 하나만
# 스캔하면 섹션 모듈에만 있는 키(예: macro_bond_ai_engine, sr_edgar_form_categories 등)를 놓쳐
# 이 테스트의 원래 목적(페이지 간 세션 키 충돌 검사)이 조용히 약해진다 — 그래서 이 두 페이지는
# 파일 목록(list[Path])으로 받아 폴더 전체를 훑는다. 다른 페이지는 여전히 단일 파일이라 Path
# 그대로 둔다.
PAGE_FILES = {
    "stock_research": [VIEWS_DIR / "stock_research.py", *sorted((VIEWS_DIR / "stock").glob("*.py"))],
    "screener": VIEWS_DIR / "screener.py",
    "comparison": VIEWS_DIR / "comparison.py",
    "macro_indicator": [VIEWS_DIR / "macro_indicator.py", *sorted((VIEWS_DIR / "macro").glob("*.py"))],
    # 건강검진 라운드(2026-10) — 새 페이지도 키 충돌 검사 대상에 포함한다(빠뜨리면 이 테스트가
    # 조용히 약해진다 — 파일 분할 라운드에서 섹션 모듈을 빠뜨렸던 것과 같은 함정).
    "data_health": VIEWS_DIR / "data_health.py",
}

# st.session_state의 dict-like 메서드 — 키 이름이 아니라 메서드 호출이므로 제외한다.
_DICT_METHODS = {"get", "pop", "setdefault", "update", "keys", "values", "items", "clear"}

_ATTR_PATTERN = re.compile(r"st\.session_state\.([a-zA-Z_][a-zA-Z0-9_]*)")
_BRACKET_PATTERN = re.compile(r"""st\.session_state\[["']([a-zA-Z_][a-zA-Z0-9_]*)["']\]""")
# key="literal" 또는 key=f"literal{...}..." — f-string은 첫 '{' 앞까지만(정적 접두어)를 취한다.
_KEY_PARAM_PATTERN = re.compile(r'key=f?"([^"{]*)')


def _extract_session_keys(path_or_paths: Path | list[Path]) -> set[str]:
    paths = [path_or_paths] if isinstance(path_or_paths, Path) else path_or_paths
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    keys: set[str] = set()

    for m in _ATTR_PATTERN.finditer(text):
        name = m.group(1)
        if name not in _DICT_METHODS:
            keys.add(name)

    for m in _BRACKET_PATTERN.finditer(text):
        keys.add(m.group(1))

    for m in _KEY_PARAM_PATTERN.finditer(text):
        prefix = m.group(1)
        if prefix:  # 완전히 동적이라 리터럴 접두어가 없는 키(예: key=f"{var}")는 건너뜀
            keys.add(prefix)

    return keys


def test_all_page_files_exist():
    """이 테스트가 조용히 0개 파일을 검사하고 통과하는 사고를 막는다."""
    for name, path_or_paths in PAGE_FILES.items():
        paths = [path_or_paths] if isinstance(path_or_paths, Path) else path_or_paths
        assert paths, f"{name}에 스캔할 파일이 하나도 없습니다(glob이 빈 목록을 반환했을 수 있음)."
        for path in paths:
            assert path.exists(), f"{name} 페이지 파일을 찾을 수 없습니다: {path}"


def test_each_page_extracts_at_least_one_session_key():
    """정규식이 실제로 뭔가 잡아내고 있는지 확인 — 전부 0개면 파서가 조용히 고장난 것."""
    for name, path in PAGE_FILES.items():
        keys = _extract_session_keys(path)
        assert keys, f"{name}에서 세션 키를 하나도 못 찾음 — 정규식이 깨졌을 수 있습니다."


def test_session_keys_do_not_collide_across_pages():
    keys_by_page = {name: _extract_session_keys(path) for name, path in PAGE_FILES.items()}

    pages = list(keys_by_page)
    collisions = []
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            a, b = pages[i], pages[j]
            overlap = keys_by_page[a] & keys_by_page[b]
            if overlap:
                collisions.append((a, b, overlap))

    assert not collisions, "페이지 간 세션 키 충돌 발견:\n" + "\n".join(
        f"  {a} <-> {b}: {sorted(overlap)}" for a, b, overlap in collisions
    )


def test_known_page_prefixes_cover_most_persistent_keys():
    """⚠️ 이건 엄격한 규칙 강제가 아니라 현재 상태를 문서화하는 관찰용 테스트다 — 접두어를
    안 지키는 키가 있어도 실패시키지 않는다(예: macro_indicator.py의 버튼 key는 "btn_"로
    시작해 "macro_" 접두어를 안 따른다 — 페이지 간 충돌은 없어 안전하지만 관례는 아니다).
    접두어 커버리지가 급격히 떨어지면(리팩터링 중 실수로 접두어를 지우는 등) 눈에 띄도록
    최소 비율만 확인한다."""
    prefixes = {
        "stock_research": "sr_",
        "screener": "screener_",
        "comparison": "cmp_",
        "macro_indicator": "macro_",
        "data_health": "health_",
    }
    for name, path in PAGE_FILES.items():
        keys = _extract_session_keys(path)
        prefixed = {k for k in keys if k.startswith(prefixes[name])}
        coverage = len(prefixed) / len(keys)
        assert coverage > 0.3, (
            f"{name}: 접두어({prefixes[name]}) 커버리지가 {coverage:.0%}로 너무 낮습니다 — "
            "세션 키 네이밍 관례가 크게 깨졌을 수 있습니다."
        )
