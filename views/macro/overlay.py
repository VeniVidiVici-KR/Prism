"""지표 겹쳐보기 (파일 분할 라운드, 2026-08 — views/macro_indicator.py에서 이동, 로직 변경 없음)."""

import pandas as pd
import streamlit as st

from valley.composite import COMPOSITE_INDICATORS
from valley.macro import INDICATORS, MacroDataError, normalize_indexed
from views.macro._shared import load_composite_series, load_series

MAX_OVERLAY_INDICATORS = 3


def render(country_code: str) -> None:
    st.divider()
    st.header("지표 겹쳐보기")
    st.caption(
        "서로 다른 단위의 지표를 비교할 수 있도록, 겹치는 기간의 첫 관측치를 100으로 지수화해 "
        "정규화한 뒤 겹쳐 그립니다. 절대 수준(원계열)이 궁금하면 위 단일/혼합 지표 보기를 이용하세요."
    )

    _overlay_composite_keys = {ind["key"] for ind in COMPOSITE_INDICATORS[country_code]}
    _overlay_options = [
        (ind["key"], ind["label"])
        for indicators in INDICATORS[country_code].values()
        for ind in indicators
    ] + [(ind["key"], ind["label"]) for ind in COMPOSITE_INDICATORS[country_code]]
    _overlay_label_by_key = dict(_overlay_options)

    overlay_selection = st.multiselect(
        "겹쳐볼 지표 선택 (2~3개)",
        options=[key for key, _ in _overlay_options],
        format_func=lambda k: _overlay_label_by_key[k],
        key="macro_overlay_selection",
    )

    if len(overlay_selection) > MAX_OVERLAY_INDICATORS:
        st.warning(f"최대 {MAX_OVERLAY_INDICATORS}개까지 선택할 수 있습니다. 앞의 {MAX_OVERLAY_INDICATORS}개만 사용합니다.")
        overlay_selection = overlay_selection[:MAX_OVERLAY_INDICATORS]

    if len(overlay_selection) < 2:
        st.info("2개 이상 선택하면 정규화해 겹쳐 그린 차트를 보여드립니다.")
    else:
        overlay_series = {}
        overlay_failed = []
        for key in overlay_selection:
            label = _overlay_label_by_key[key]
            try:
                raw = load_composite_series(key) if key in _overlay_composite_keys else load_series(key)
            except MacroDataError as e:
                overlay_failed.append(f"{label}: {e}")
                continue
            overlay_series[label] = raw

        if overlay_failed:
            st.warning("일부 지표 조회에 실패해 제외했습니다: " + " / ".join(overlay_failed))

        if len(overlay_series) < 2:
            st.info("겹쳐 그릴 수 있는 지표가 2개 미만입니다.")
        else:
            # 서로 주기(월별/분기별 등)가 다를 수 있어, 겹치는 구간만 남긴 뒤(join="inner") 그 구간의
            # 첫 관측치를 기준으로 정규화한다 — 이래야 차트에서 두 선이 같은 시작점(=100)에서 출발한다.
            aligned = pd.concat(overlay_series, axis=1, join="inner")
            if aligned.empty:
                st.warning("선택한 지표들 사이에 겹치는 기간이 없습니다.")
            else:
                normalized = aligned.apply(normalize_indexed)
                st.line_chart(normalized)
                st.caption(
                    f"겹치는 구간({aligned.index[0]:%Y-%m} ~ {aligned.index[-1]:%Y-%m})의 첫 관측치 = 100 기준 지수화."
                )
