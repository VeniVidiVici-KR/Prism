"""Data Health — 매크로 카탈로그 전 시리즈의 신선도·소스를 한 화면에서 점검한다
(건강검진 라운드, 2026-10).

⚠️ 배경: 2026-09~10에 고친 버그가 전부 "데이터가 조용히 틀려 있었다"는 같은 패턴이었다
(한국 CPI 미러 2023-11 정지, M2 미러 2017-05 정지, 기준금리가 실은 콜금리, ADR 통화 불일치).
전부 사람이 수동으로 캐물어 발견한 것들이라, 그 점검을 상시화하는 게 이 페이지의 목적이다.

⚠️ 버튼 클릭 시에만 조회한다 — 105개 시리즈를 전수 조회하므로(실측 1분 내외) 페이지 진입만
으로 자동 실행되면 안 된다. 이 프로젝트의 다른 무거운 섹션들과 같은 원칙이다.
"""

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from valley.health import classify_freshness, describe_source, missing_credentials, used_fred_fallback
from valley.macro import INDICATORS, MacroDataError, get_indicator_series
from views._style import inject_navy_headings

load_dotenv(override=True)
inject_navy_headings()

st.title("Data Health")
st.caption(
    "매크로 카탈로그의 모든 시리즈를 실제로 조회해 최신 관측일·발표 주기·소스를 점검합니다 · "
    "데이터가 조용히 멈춰 있거나 폴백으로 떨어진 항목을 먼저 띄웁니다."
)

# ⚠️ 상태 문자열은 한 곳에서만 정의한다 — 아래 요약 지표·필터·정렬이 전부 이 값을 쓴다.
STATUS_OK = "정상"
STATUS_STALE = "지연"
STATUS_FALLBACK = "폴백"
STATUS_NO_KEY = "키 없음"
STATUS_FAILED = "조회 실패"

# 심각한 것부터 위로 — 조회가 아예 안 되는 것 > 멈춘 것 > 폴백으로 버티는 것 > 정상.
_STATUS_ORDER = {STATUS_FAILED: 0, STATUS_STALE: 1, STATUS_NO_KEY: 2, STATUS_FALLBACK: 3, STATUS_OK: 4}


def _catalog_entries() -> list[tuple[str, str, dict]]:
    """(국가, 카테고리, 항목) 평면 목록. 카탈로그 정의 순서를 그대로 보존한다."""
    return [
        (country, category, indicator)
        for country, categories in INDICATORS.items()
        for category, indicators in categories.items()
        for indicator in indicators
    ]


def _scan_one(country: str, category: str, indicator: dict) -> dict:
    """한 시리즈를 실제로 조회해 점검 결과 한 행을 만든다.

    ⚠️ 개별 항목 실패가 전체 스캔을 멈추면 안 된다 — 건강검진은 "뭐가 깨졌는지" 보는 게
    목적이라 깨진 항목이야말로 결과에 남아야 한다(CPI 중분류 라운드의 개별 항목 격리와 동일).
    """
    base = {
        "국가": country,
        "카테고리": category,
        "지표": indicator["label"],
        "key": indicator["key"],
        "소스": describe_source(indicator),
    }
    # 키 부재는 "데이터가 깨진 것"이 아니라 "이 환경에서 조회 불가"라 따로 구분한다 — 섞어
    # 버리면 키 없는 환경에서 전부 빨간불이 되어 진짜 문제가 묻힌다. 에러 문구가 아니라
    # 환경변수를 직접 보고 판정한다(valley.health.missing_credentials 주석 참고).
    missing = missing_credentials(indicator)

    try:
        series = get_indicator_series(indicator["key"])
    except MacroDataError as e:
        return {
            **base, "상태": STATUS_NO_KEY if missing else STATUS_FAILED,
            "최신 관측": "—", "주기": "—", "경과(일)": None, "관측치": None, "비고": str(e),
        }
    except Exception as e:  # 소스 라이브러리가 MacroDataError로 감싸지 않은 예외도 흘리지 않는다
        return {
            **base, "상태": STATUS_NO_KEY if missing else STATUS_FAILED,
            "최신 관측": "—", "주기": "—", "경과(일)": None, "관측치": None,
            "비고": f"{type(e).__name__}: {e}",
        }

    freshness = classify_freshness(series)
    fallback = used_fred_fallback(series, indicator)

    if freshness["stale"]:
        status = STATUS_STALE
    elif fallback:
        status = STATUS_FALLBACK
    else:
        status = STATUS_OK

    # ⚠️ 비고는 "왜 걸렸는지"를 담는 가장 중요한 열인데 실측 결과 1280px에서도 잘렸다 —
    # 주기·경과는 이미 옆 열에 있으니 중복을 빼고 핵심만 남긴다("연간 지표인데 허용 1095일을
    # 넘김" → "허용 1095일 초과").
    notes = []
    if freshness["stale"] and freshness["allowed_days"]:
        notes.append(f"허용 {int(freshness['allowed_days'])}일 초과")
    if fallback:
        notes.append("ECOS 실패 → FRED 폴백")

    return {
        **base,
        "상태": status,
        "최신 관측": str(freshness["latest"]) if freshness["latest"] else "—",
        "주기": freshness["frequency"],
        "경과(일)": freshness["age_days"],
        "관측치": freshness["observations"],
        "비고": " · ".join(notes),
    }


if st.button("전체 점검 실행", type="primary"):
    entries = _catalog_entries()
    results = []
    progress = st.progress(0.0, text="점검 준비 중...")
    for i, (country, category, indicator) in enumerate(entries, start=1):
        progress.progress(i / len(entries), text=f"[{i}/{len(entries)}] {indicator['label']} 조회 중...")
        results.append(_scan_one(country, category, indicator))
    progress.empty()
    st.session_state.health_results = results
    st.session_state.health_scanned_at = pd.Timestamp.now()

results = st.session_state.get("health_results")

if results is None:
    st.info(
        f"'전체 점검 실행'을 눌러주세요 — 카탈로그 {len(_catalog_entries())}개 시리즈를 실제로 "
        "조회합니다(실측 1분 내외)."
    )
    st.stop()

df = pd.DataFrame(results)
scanned_at = st.session_state.get("health_scanned_at")

counts = df["상태"].value_counts()
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("정상", int(counts.get(STATUS_OK, 0)))
m2.metric("지연", int(counts.get(STATUS_STALE, 0)))
m3.metric("폴백", int(counts.get(STATUS_FALLBACK, 0)))
m4.metric("키 없음", int(counts.get(STATUS_NO_KEY, 0)))
m5.metric("조회 실패", int(counts.get(STATUS_FAILED, 0)))
if scanned_at is not None:
    st.caption(f"점검 시각: {scanned_at:%Y-%m-%d %H:%M} · 전체 {len(df)}개 시리즈")

problem_count = int(len(df) - counts.get(STATUS_OK, 0))
if problem_count == 0:
    st.success("점검한 모든 시리즈가 정상입니다.")
else:
    st.warning(
        f"{problem_count}개 시리즈에 확인이 필요합니다 — 아래 표 위쪽부터 보세요"
        "(조회 실패 → 지연 → 키 없음 → 폴백 순)."
    )

with st.container(border=True):
    only_problems = st.checkbox("문제 항목만 보기", value=problem_count > 0)
    view = df[df["상태"] != STATUS_OK] if only_problems else df
    # 심각도 우선, 같은 심각도 안에서는 오래된 것부터.
    view = view.assign(_order=view["상태"].map(_STATUS_ORDER)).sort_values(
        ["_order", "경과(일)"], ascending=[True, False]
    ).drop(columns="_order")

    if view.empty:
        st.info("표시할 항목이 없습니다.")
    else:
        # ⚠️ 열이 많으면 좁은 폭에서 오른쪽 열(하필 가장 중요한 '비고')부터 잘린다 — 실측으로
        # 1280px에서도 잘려 '관측치'(진단용 부가 정보)를 표에서 뺐다. 값 자체는 결과 dict에
        # 남아 있어 나중에 필요하면 되살릴 수 있다.
        st.dataframe(
            view[["상태", "국가", "카테고리", "지표", "최신 관측", "주기", "경과(일)", "소스", "비고"]],
            width="stretch",
            hide_index=True,
        )

    st.caption(
        "⚠️ '지연'은 발표 주기(관측 간격에서 자동 추론) 대비 3배 이상 갱신이 없는 경우입니다 — "
        "연간 지표가 1년 넘게 비는 것처럼 정상적인 지연은 걸리지 않습니다. '폴백'은 ECOS 조회가 "
        "실패해 FRED 미러로 대체된 항목으로, 값은 나오지만 미러가 멈춰 있을 수 있어 함께 표시합니다."
    )
