"""시장 스냅샷 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음)."""

import streamlit as st

from views._cache import get_market_snapshot, get_us_market_state


def render() -> None:
    """버튼 클릭 없이 페이지 진입만으로 자동 조회되는 유일한 섹션이다(get_market_snapshot의
    ttl=300초 캐싱으로 호출 부담을 억제). "열자마자 시장 상황 파악"이 목적이라 종목을 조회하기
    전/후 둘 다 항상 보이도록, 티커 확정 여부로 갈리는 st.stop() 게이트보다 앞에서 호출된다
    (조립부 stock_research.py가 사이드바보다 먼저 부른다).
    """
    with st.expander("📊 시장 스냅샷 (KOSPI·S&P500·VIX·환율 등)", expanded=True):
        try:
            snapshot_rows = get_market_snapshot()
        except Exception:
            snapshot_rows = []

        if not snapshot_rows:
            st.caption("시장 스냅샷을 불러오지 못했습니다 (일시적 네트워크 문제일 수 있습니다).")
        else:
            _SNAPSHOT_COLS_PER_ROW = 5
            for i in range(0, len(snapshot_rows), _SNAPSHOT_COLS_PER_ROW):
                chunk = snapshot_rows[i : i + _SNAPSHOT_COLS_PER_ROW]
                cols = st.columns(_SNAPSHOT_COLS_PER_ROW)
                for col, row in zip(cols, chunk):
                    if row["mode"] == "bp":
                        value_text = f"{row['value']:.2f}%"
                        delta_text = f"{row['change']:+.1f}bp"
                    else:
                        value_text = f"{row['value']:,.2f}"
                        delta_text = f"{row['change']:+.2f}%"
                    # ⚠️ 한국 사용자 관례(상승 빨강/하락 파랑)와 Streamlit 기본값(상승 초록/하락
                    # 빨강)이 반대다. st.metric의 delta_color는 "normal"(초록/빨강)과
                    # "inverse"(빨강/초록) 둘 중에서만 고를 수 있어(파랑 자체는 지원하지 않음) 완전한
                    # 한국식 재현은 불가능하지만, 상승=빨강이 더 중요한 관례라 판단해 inverse를
                    # 택했다(하락이 초록으로 보이는 절충은 감수).
                    col.metric(row["name"], value_text, delta_text, delta_color="inverse")

            market_state = get_us_market_state()
            _MARKET_STATE_LABELS = {
                "REGULAR": "개장 중", "PRE": "장 시작 전", "POST": "장 마감 후 시간외", "CLOSED": "마감",
            }
            state_text = _MARKET_STATE_LABELS.get(market_state, "확인 불가")
            st.caption(
                f"⚠️ 지연 시세(약 15~20분) · 실시간이 아닙니다 · 미국장 상태: {state_text} "
                "(S&P500 기준 참고치, 한국장 상태는 별도 확인 필요)"
            )
