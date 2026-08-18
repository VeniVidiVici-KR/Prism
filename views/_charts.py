"""views/ 전용 차트 헬퍼 + 네이비 모노크로매틱 팔레트 (2026-08 디자인 라운드).

st.line_chart(내부적으로 Vega-Lite 라인)은 표식(마커) 옵션이 없다. 재무 추이처럼 데이터
포인트가 연 4~5개/분기 4~5개로 적은 시계열은 표식이 있어야 값을 점 단위로 읽기 쉬워서,
Altair(streamlit 번들 의존성이라 별도 설치 불필요)로 직접 그린다.

⚠️ 색상 팔레트는 여기 한 곳에서만 정의하고 모든 차트 헬퍼가 이를 쓴다(디자인 라운드 지시).
네이비 램프 + ink + signal(양/음수 판단용) 뿐이며, 새 색조(4번째 색)는 추가하지 않는다.
"""

import altair as alt
import pandas as pd
import streamlit as st

# ── 팔레트 (디자인 라운드 색상표 그대로) ──────────────────────────────────────────────────
_NAVY_DEEPEST = "#081C3F"  # 핵심 수치, 주요 헤드라인
_NAVY_DEEP = "#0A2E5C"  # 섹션 헤더, 1순위 차트 시리즈
_NAVY_PRIMARY = "#1E4D8B"  # 본문 강조, 2순위 차트 시리즈
_NAVY_MID = "#4A7FBA"  # 3순위 차트 시리즈, 보조 강조
_NAVY_LIGHT = "#8AAED6"  # 4순위 차트 시리즈, 약한 강조
_NAVY_PALE = "#D6E3F2"  # 옅은 채움, 카드 배경, 표 행 교대
_NAVY_TINT = "#F0F5FB"  # 가장 옅은 워시(배경용 — 데이터잉크로는 흰 배경과 거의 안 구분돼 안 씀)

_INK_MUTED = "#6B7280"  # 캡션, 축 라벨, 보조 문구
_HAIRLINE = "#E5E7EB"  # 구분선, 표 테두리

_SIGNAL_POSITIVE = "#2D6A4F"  # 긍정 신호(절제) — 기여도/증감 막대 전용
_SIGNAL_NEGATIVE = "#8B2635"  # 부정 신호(절제) — 기여도/증감 막대 전용

# 팔레트 표가 명시적으로 "1~4순위 차트 시리즈"라 부른 4개 — 대부분의 차트(≤4개 시리즈)는
# 이 순서 그대로 쓴다. navy.deepest는 "핵심 수치/헤드라인" 용도라 차트 데이터잉크로는 쓰지
# 않고, navy.pale/tint는 흰 배경 위에서 선·막대로는 잘 안 보여 기본 4색 램프에서 뺐다.
_CHART_RAMP = [_NAVY_DEEP, _NAVY_PRIMARY, _NAVY_MID, _NAVY_LIGHT]

# ⚠️ 5~6개 시리즈가 필요한 차트(실측: "기저 인플레 비교"가 6개)를 위한 확장 램프 — 새 색조를
# 추가하는 대신 네이비 계열 안에서 명도 단계를 두 칸 더 늘린다(어두운 쪽 navy.deepest, 밝은
# 쪽 navy.pale). 전부 팔레트에 이미 있는 공식 토큰이라 보간에서 오는 탁한 중간색 문제가 없다.
_CHART_RAMP_EXTENDED = [_NAVY_DEEPEST, _NAVY_DEEP, _NAVY_PRIMARY, _NAVY_MID, _NAVY_LIGHT, _NAVY_PALE]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c))) for c in rgb))


def navy_shades(n: int) -> list[str]:
    """n개의 구분 가능한 네이비 색조를 만든다.

    ⚠️ 이 모듈의 다른 함수와 달리 공개(비-언더스코어) 함수다 — pie_chart/
    line_chart_with_markers 내부에서도 쓰지만, 플레인 st.line_chart(color= 인자, Streamlit
    1.59+ 지원)처럼 차트 헬퍼를 거치지 않고 색만 명시적으로 넘겨야 하는 호출부(예:
    macro_indicator.py의 6개 시리즈짜리 "기저 인플레 비교" — 데이터가 40년치 월별이라
    line_chart_with_markers의 점 표식을 쓰면 무거워져 차트 종류를 안 바꾸고 색만 지정)를
    위해 노출한다.

    ⚠️ "네 번째 색조를 도입하지 마라"는 원칙에 따라 새 색상 계열을 추가하는 대신, 이미 있는
    네이비 계열 안에서 명도 단계를 늘리는 방식으로만 시리즈 수를 늘린다(디자인 라운드 지시:
    "램프가 부족하면... 명도 단계를 더 만들거나(navy 계열 내에서)").
    - n<=4: 공식 "1~4순위 차트 시리즈" 4색을 그대로 쓴다.
    - 4<n<=6: navy.deepest/navy.pale까지 넓힌 6개 공식 토큰을 그대로 쓴다(보간 없음 — 전부
      팔레트에 이미 있는 색이라 탁해질 위험이 없다).
    - n>6(실측: 한국 CPI 12개 대분류 파이차트가 유일한 사례): 6개 공식 토큰을 순서대로 잇는
      경로를 따라 균등한 간격으로 n개 색을 뽑는다 — 각 구간이 이미 서로 가까운 공식 색
      사이의 짧은 보간이라(전체 범위를 한 번에 보간할 때와 달리) 중간색이 탁한 회색으로
      보이지 않는다.
    """
    if n <= 0:
        return []
    if n <= len(_CHART_RAMP):
        return _CHART_RAMP[:n]
    if n <= len(_CHART_RAMP_EXTENDED):
        return _CHART_RAMP_EXTENDED[:n]

    anchors = [_hex_to_rgb(c) for c in _CHART_RAMP_EXTENDED]
    segments = len(anchors) - 1
    colors = []
    for i in range(n):
        t = i / (n - 1) * segments  # 0..segments 범위의 위치
        seg = min(int(t), segments - 1)
        local_t = t - seg
        start, end = anchors[seg], anchors[seg + 1]
        rgb = tuple(s + (e - s) * local_t for s, e in zip(start, end))
        colors.append(_rgb_to_hex(rgb))
    return colors


# ── 전역 Altair 테마 — 모든 alt.Chart에 자동 적용(축/격자/배경). 개별 차트 함수마다 반복해서
# 지정하지 않아도 되게 한 곳에 모았다. Altair 5.5+ 신규 API(alt.theme.register)를 쓴다 —
# 구 API(alt.themes.register)는 이 버전(6.2.2)에서 이미 폐기예정(deprecated) 경고가 뜬다.
@alt.theme.register("prism_navy", enable=True)
def _prism_navy_theme() -> alt.theme.ThemeConfig:
    return alt.theme.ThemeConfig(
        background="transparent",  # canvas — 페이지 배경이 그대로 비친다(투명)
        config={
            "axis": {
                "labelColor": _INK_MUTED,
                "titleColor": _INK_MUTED,
                "gridColor": _HAIRLINE,
                "domainColor": _HAIRLINE,
                "tickColor": _HAIRLINE,
            },
            "legend": {"labelColor": _INK_MUTED, "titleColor": _INK_MUTED},
            "view": {"stroke": _HAIRLINE},
            "range": {"category": _CHART_RAMP},
        },
    )


def line_chart_with_markers(data: pd.DataFrame, height: int = 320) -> None:
    """와이드 포맷 DataFrame(인덱스=기간, 컬럼=시리즈)을 표식이 있는 꺾은선 차트로 그린다."""
    long_df = data.reset_index(names="기간").melt("기간", var_name="지표", value_name="값")
    series_names = list(data.columns)
    chart = (
        alt.Chart(long_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("기간:N", sort=list(data.index), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("값:Q", title=None),
            color=alt.Color(
                "지표:N",
                legend=alt.Legend(title=None),
                scale=alt.Scale(domain=series_names, range=navy_shades(len(series_names))),
            ),
            tooltip=["기간", "지표", "값"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def actual_projection_line_chart(series_pairs: dict[str, tuple[pd.Series, pd.Series | None]], height: int = 320) -> None:
    """{계열이름: (실적, 전망 또는 None)}을 실적(실선)+전망(점선)으로 이어 그린다.

    전망 반영 라운드(2026-08)에서 추가 — 국가부채/GDP처럼 실적과 IMF 전망이 이어지는
    시계열을 "어디부터 전망인지" 헷갈리지 않게 보여줘야 하는 용도다. ⚠️ 새 색조를 추가하는
    대신, 계열 구분은 기존 navy_shades()로(1개 계열이면 단색), 실적/전망 구분은 strokeDash로
    한다 — 팔레트 자체는 그대로 두고 인코딩 채널만 하나 늘렸다.

    ⚠️ 실적의 마지막 관측치를 전망 구간의 시작점으로도 복제해 넣는다(값 자체는 그대로, 차트
    표시 전용 보정) — 그렇지 않으면 Altair가 실적/전망을 완전히 분리된 두 선으로 그려 둘
    사이에 시각적 공백(gap)이 생긴다. 실적 데이터 자체를 바꾸는 게 아니다.
    """
    frames = []
    for label, (actual, projection) in series_pairs.items():
        # ⚠️ Series.reset_index()는 (DataFrame.reset_index와 달리) names= 인자를 지원하지
        # 않는다(pandas 2.3) — rename_axis로 인덱스 이름을 먼저 바꾼 뒤 reset_index()한다.
        actual_df = actual.rename("값").rename_axis("기간").reset_index()
        actual_df["계열"] = label
        actual_df["구분"] = "실적"
        frames.append(actual_df)
        if projection is not None and not projection.empty and not actual.empty:
            bridge = pd.DataFrame(
                {"기간": [actual.index[-1]], "값": [float(actual.iloc[-1])], "계열": [label], "구분": ["전망"]}
            )
            projection_df = projection.rename("값").rename_axis("기간").reset_index()
            projection_df["계열"] = label
            projection_df["구분"] = "전망"
            frames.append(pd.concat([bridge, projection_df], ignore_index=True))

    combined = pd.concat(frames, ignore_index=True)
    combined["기간"] = combined["기간"].apply(lambda d: d.strftime("%Y") if hasattr(d, "strftime") else str(d))
    year_order = sorted(combined["기간"].unique())
    series_names = list(series_pairs.keys())

    chart = (
        alt.Chart(combined)
        .mark_line(point=True)
        .encode(
            x=alt.X("기간:N", sort=year_order, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("값:Q", title=None),
            color=alt.Color(
                "계열:N",
                legend=alt.Legend(title=None) if len(series_names) > 1 else None,
                scale=alt.Scale(domain=series_names, range=navy_shades(len(series_names))),
            ),
            strokeDash=alt.StrokeDash(
                "구분:N", legend=alt.Legend(title=None), scale=alt.Scale(domain=["실적", "전망"], range=[[1, 0], [6, 4]])
            ),
            tooltip=["기간", "계열", "구분", alt.Tooltip("값:Q", format=".1f")],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def pie_chart(data: pd.DataFrame, category_col: str, value_col: str, height: int = 320) -> None:
    """항목별 비중(%)을 원 그래프로 그린다 — 항목명과 비중%를 툴팁/각도로 표시한다."""
    categories = list(data[category_col])
    chart = (
        alt.Chart(data)
        .mark_arc()
        .encode(
            theta=alt.Theta(f"{value_col}:Q"),
            color=alt.Color(
                f"{category_col}:N",
                legend=alt.Legend(title=None),
                scale=alt.Scale(domain=categories, range=navy_shades(len(categories))),
            ),
            tooltip=[category_col, alt.Tooltip(f"{value_col}:Q", format=".1f")],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def contribution_bar_chart(data: pd.DataFrame, category_col: str, value_col: str, height: int = 320) -> None:
    """기여도처럼 음수가 나올 수 있는 값을 막대그래프로 그린다.

    ⚠️ 네이비 램프의 예외다 — 여기선 양수/음수 구분 자체가 정보의 핵심이라 디자인 라운드
    지시대로 signal.positive/negative를 쓴다(카테고리 램프가 아님). 큰 값(가장 밀어올린
    항목)이 위로 오도록 정렬하고, 0선은 ink.muted로 그려 양/음수 경계가 바로 보이게 한다.
    """
    sorted_data = data.sort_values(value_col, ascending=False)
    bars = (
        alt.Chart(sorted_data)
        .mark_bar()
        .encode(
            x=alt.X(f"{value_col}:Q", title=None),
            y=alt.Y(f"{category_col}:N", sort=list(sorted_data[category_col]), title=None),
            color=alt.condition(
                f"datum['{value_col}'] >= 0", alt.value(_SIGNAL_POSITIVE), alt.value(_SIGNAL_NEGATIVE)
            ),
            tooltip=[category_col, alt.Tooltip(f"{value_col}:Q", format="+.2f")],
        )
    )
    zero_rule = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=_INK_MUTED).encode(x="x:Q")
    st.altair_chart((bars + zero_rule).properties(height=height), width="stretch")
