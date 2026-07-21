"""Screener — 여러 종목을 PER/PBR/배당/ROE/시가총액 조건으로 필터링."""

import streamlit as st
from dotenv import load_dotenv

from valley.data import TickerNotFoundError
from valley.peers import build_valuation_table
from views._cache import get_valuation_snapshot, resolve_ticker

load_dotenv(override=True)

st.title("🔍 Screener")
st.caption("여러 종목의 PER/PBR/배당/ROE/시가총액을 한 번에 훑어봅니다 · 정보 제공 목적이며 투자자문이 아닙니다.")

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "JNJ",
    "WMT", "XOM", "UNH", "PG", "HD",
    "005930", "000660", "373220", "207940", "005380",
    "035420", "035720", "051910", "006400", "105560",
    "055550", "012330", "066570", "003670", "015760",
]

tickers_input = st.text_area(
    "종목 목록 (쉼표 또는 줄바꿈으로 구분, 한국 종목은 6자리 코드)",
    value=", ".join(DEFAULT_UNIVERSE),
    height=100,
)

run = st.button("스크리닝 실행", type="primary")

if run:
    raw_tickers = [t.strip().upper() for t in tickers_input.replace("\n", ",").split(",") if t.strip()]
    unique_tickers = list(dict.fromkeys(raw_tickers))

    rows, failed, failed_detail = [], [], []
    for raw in unique_tickers:
        try:
            resolved = resolve_ticker(raw)
            rows.append(get_valuation_snapshot(resolved))
        except TickerNotFoundError as e:
            failed.append(raw)
            failed_detail.append(f"{raw}: {e}")
        except Exception as e:
            failed.append(raw)
            failed_detail.append(f"{raw}: {type(e).__name__} - {e}")

    st.session_state.screener_rows = rows
    st.session_state.screener_failed = failed
    st.session_state.screener_failed_detail = failed_detail

rows = st.session_state.get("screener_rows")

if rows is None:
    st.info("종목 목록을 확인하고 '스크리닝 실행'을 눌러주세요.")
    st.stop()

failed = st.session_state.get("screener_failed") or []
failed_detail = st.session_state.get("screener_failed_detail") or []
if failed:
    st.warning(f"{len(failed)}개 종목은 데이터를 가져오지 못해 제외되었습니다: {', '.join(failed)}")
    with st.expander("실패 상세"):
        for line in failed_detail:
            st.text(line)

if not rows:
    st.info("표시할 종목이 없습니다.")
    st.stop()

st.subheader("필터")
per_values = [r["per"] for r in rows if r["per"] is not None]
pbr_values = [r["pbr"] for r in rows if r["pbr"] is not None]

f1, f2, f3 = st.columns(3)
per_range = f1.slider(
    "PER 범위 (배)", 0.0, max(per_values, default=100.0) + 1, (0.0, max(per_values, default=100.0) + 1)
) if per_values else None
pbr_range = f2.slider(
    "PBR 범위 (배)", 0.0, max(pbr_values, default=20.0) + 1, (0.0, max(pbr_values, default=20.0) + 1)
) if pbr_values else None
min_cap = f3.number_input("최소 시가총액 (단위: 원래 통화)", min_value=0.0, value=0.0, step=1e9)

st.caption("배당수익률 하한 (%) · 국가별로 다른 기준을 걸 수 있습니다")
d1, d2 = st.columns(2)
kr_div_min = d1.number_input("한국(KRW)", min_value=0.0, value=0.0, step=0.5, key="screener_kr_div_min") / 100
us_div_min = d2.number_input("미국(USD)", min_value=0.0, value=0.0, step=0.5, key="screener_us_div_min") / 100

st.caption("배당성향 하한 (%) · 국가별로 다른 기준을 걸 수 있습니다")
p1, p2 = st.columns(2)
kr_payout_min = p1.number_input("한국(KRW)", min_value=0.0, value=0.0, step=1.0, key="screener_kr_payout_min") / 100
us_payout_min = p2.number_input("미국(USD)", min_value=0.0, value=0.0, step=1.0, key="screener_us_payout_min") / 100

currencies = sorted({r["currency"] for r in rows})
selected_currencies = st.multiselect("통화", currencies, default=currencies)

include_missing = st.checkbox("PER/PBR/배당 등 미산출 지표 포함 (적자 등)", value=True)

sort_by = st.selectbox("정렬 기준", ["PER", "PBR", "시가총액"], index=0)


def _passes(value, in_range) -> bool:
    """값이 없으면 include_missing 여부로, 있으면 in_range(value) 결과로 판정한다.

    지표별로 독립 판정한다 — 한 지표가 없다고 다른 지표의 필터까지 건너뛰면(예: PER
    미산출인데 PBR이 범위 밖인 종목이 통과) 안 되므로, 반드시 지표마다 이 함수를 따로 호출한다.
    """
    if value is None:
        return include_missing
    return in_range(value)


def _country_min(currency: str, kr_value: float, us_value: float) -> float:
    """KRW/USD 통화별 하한을 고른다. 그 외 통화는 하한을 적용하지 않는다(0)."""
    if currency == "KRW":
        return kr_value
    if currency == "USD":
        return us_value
    return 0.0


filtered = []
for r in rows:
    if r["currency"] not in selected_currencies:
        continue
    if not _passes(r["per"], lambda v: not per_range or per_range[0] <= v <= per_range[1]):
        continue
    if not _passes(r["pbr"], lambda v: not pbr_range or pbr_range[0] <= v <= pbr_range[1]):
        continue

    div_min = _country_min(r["currency"], kr_div_min, us_div_min)
    if not _passes(r["dividend_yield"], lambda v, m=div_min: v >= m):
        continue

    payout_min = _country_min(r["currency"], kr_payout_min, us_payout_min)
    if not _passes(r["payout_ratio"], lambda v, m=payout_min: v >= m):
        continue

    if min_cap and (not r["market_cap"] or r["market_cap"] < min_cap):
        continue
    filtered.append(r)

sort_key_map = {"PER": "per", "PBR": "pbr", "시가총액": "market_cap"}
sort_key = sort_key_map[sort_by]
filtered.sort(key=lambda r: (r[sort_key] is None, r[sort_key]))

st.subheader(f"결과 ({len(filtered)}개)")
if not filtered:
    st.info("조건에 맞는 종목이 없습니다.")
else:
    st.dataframe(build_valuation_table(filtered), width="stretch", hide_index=True)
