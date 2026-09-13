"""Peer Group 비교 + PER/PBR 밴드 (파일 분할 라운드, 2026-08 — views/stock_research.py에서 이동, 로직 변경 없음).

⚠️ 두 섹션을 한 파일로 묶었다 — PER/PBR 밴드가 Peer Group에서 조회한 target_valuation(현재
PER/PBR)을 그대로 재사용하고(중복 조회 없음), STEP 2 카드 레이아웃에서도 "밸류에이션" 한
카드로 함께 묶일 예정이라 파일 경계와 카드 경계를 일치시켰다.

⚠️ target_valuation은 이 파일 밖(간단 DCF의 P/B×ROE, 요약 내보내기 — 둘 다 이번 라운드
범위 밖이라 아직 stock_research.py에 그대로 있다)에서도 재사용되므로 render()가 반환한다.
target_valuation 조회 자체가 실패하면(TickerNotFoundError) 여기서 st.stop()으로 페이지
전체를 멈춘다 — 이 값이 하단부 여러 섹션에서 무조건 필요하기 때문에(원래 코드도 동일하게
전체 페이지를 멈췄다), 이 섹션만 건너뛰는 방식으로 바꾸지 않았다.
"""

import streamlit as st

from valley.data import TickerNotFoundError
from valley.formatting import format_multiple
from valley.peers import ValuationDataError, build_valuation_table
from views._cache import get_historical_per_pbr, get_peer_tickers, get_valuation_snapshot


def render(resolved_ticker: str) -> dict:
    st.subheader("Peer Group 비교")
    st.caption("Yahoo Finance의 '유사 종목' 데이터 기반이며, 엄밀한 동일산업 분류가 아닌 참고용입니다.")
    try:
        target_valuation = get_valuation_snapshot(resolved_ticker)
    except TickerNotFoundError as e:
        st.error(str(e))
        st.stop()

    # ⚠️ 카드 레이아웃 라운드(2026-08) — Peer 비교 표와 PER/PBR 밴드를 한 카드로 묶는다(둘 다
    # target_valuation을 공유하는 "밸류에이션" 한 덩어리). st.stop() 경로는 카드를 열기 전에
    # 처리해 실패 시 빈 카드가 남지 않게 한다.
    with st.container(border=True):
        peer_tickers = get_peer_tickers(resolved_ticker)
        if peer_tickers is None:
            st.warning("Yahoo Finance 유사 종목 API 호출에 실패했습니다. 잠시 후 다시 시도해주세요.")
        elif not peer_tickers:
            st.info("이 종목에 대한 유사 종목 데이터가 없습니다.")
        else:
            peer_rows = []
            for t in peer_tickers:
                try:
                    peer_rows.append(get_valuation_snapshot(t))
                except TickerNotFoundError:
                    continue
            st.dataframe(build_valuation_table([target_valuation, *peer_rows]), width="stretch", hide_index=True)
        st.caption(
            "PER/PBR은 모든 종목을 최근 확정 회계연도(FY) 실적 기준으로 직접 계산해 통일했습니다. "
            "TTM보다 최대 1년 가까이 지연된 실적이라, 최근 실적이 급성장한 종목일수록 PER이 실제보다 높게 나올 수 있습니다."
        )
        st.caption(
            "Fwd PER은 Yahoo Finance가 제공하는 향후 12개월 컨센서스 기반 PER입니다. "
            "애널리스트 추정치가 충분치 않은 종목(주로 소형주)은 N/A로 표시됩니다."
        )
        if target_valuation.get("currency_mismatch"):
            st.caption(
                f"⚠️ PER/PBR이 N/A인 종목은 주가 통화와 재무제표 통화가 달라(예: 이 종목은 "
                f"{target_valuation.get('financial_currency')}) 계산에서 제외했습니다. Fwd PER·"
                "배당수익률·배당성향·ROE·시가총액은 영향받지 않아 그대로 표시됩니다."
            )

        st.subheader("PER / PBR 밴드 (연도별)")
        st.caption("최근 4~5개년 연간(FY) 데이터 기준이며, 일별 롤링 밴드는 지원하지 않습니다.")
        if target_valuation.get("currency_mismatch"):
            # ⚠️ 통화 불일치 라운드(2026-09) — 밴드도 Peer 표의 PER/PBR과 완전히 같은 계산
            # 방식(raw 재무제표 ÷ 연말 종가)이라 같은 이유로 통째로 막는다. get_historical_per_pbr
            # 자체는 호출하지 않는다(불필요한 조회 방지).
            st.warning(
                f"이 종목은 주가 통화(USD 등)와 재무제표 통화({target_valuation.get('financial_currency')})가 "
                "달라 PER/PBR 밴드를 계산할 수 없습니다. 한국 기업의 ADR인 경우 6자리 원주 코드로 "
                "조회하면 정상 계산됩니다."
            )
        else:
            try:
                band_df = get_historical_per_pbr(resolved_ticker)
            except ValuationDataError as e:
                st.warning(str(e))
            else:
                b1, b2 = st.columns(2)
                for col, metric in [(b1, "PER"), (b2, "PBR")]:
                    series = band_df[metric].dropna()
                    col.markdown(f"**{metric}**")
                    if series.empty:
                        col.caption("계산 가능한 데이터가 없습니다.")
                        continue
                    col.bar_chart(series)
                    current = target_valuation[metric.lower()]
                    if current is not None:
                        col.caption(
                            f"현재 {metric} {format_multiple(current)} · "
                            f"최근 {len(series)}개년 범위 {format_multiple(series.min())}~{format_multiple(series.max())}"
                        )

    return target_valuation
