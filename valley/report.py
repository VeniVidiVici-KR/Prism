"""종목 분석 결과를 마크다운 요약으로 변환. UI/외부 의존성 없는 순수 함수.

⚠️ 여기서는 아무 숫자도 계산하지 않는다 — 이미 계산·조회된 값을 인자로 받아 문자열로
포맷팅만 한다. 값이 없으면 만들어내지 않고 "N/A" 또는 항목 자체를 생략한다.
"""

from datetime import date

from valley.formatting import format_money, format_multiple, format_percent

DISCLAIMER = (
    "본 요약은 정보 제공 목적이며 투자자문이 아닙니다. "
    "수치는 무료/비공식 데이터(yfinance 등) 기반 참고용입니다."
)


def _format_growth_assumption(assumptions: dict) -> str:
    initial = assumptions["growth_rate_initial"]
    terminal = assumptions["growth_rate_terminal"]
    if initial == terminal:
        return f"{initial * 100:.1f}%"
    return f"{initial * 100:.1f}% → {terminal * 100:.1f}% (초기 → 말기, 선형 감쇠)"


def _format_coe(coe_source: dict) -> str:
    coe_pct = f"{coe_source['coe'] * 100:.2f}%"
    if coe_source.get("mode") == "auto":
        rf_pct = f"{coe_source['rf'] * 100:.2f}%"
        erp_pct = f"{coe_source['risk_premium'] * 100:.1f}%"
        label = coe_source["rf_source_label"]
        return f"{coe_pct} (= 무위험 {rf_pct}[{label}] + 리스크프리미엄 {erp_pct})"
    return f"{coe_pct} (직접 입력)"


def build_markdown_summary(
    snapshot: dict,
    valuation_snapshot: dict | None,
    dcf_result: dict | None,
    coe_source: dict,
    implied_growth: float | None,
    as_of: date,
) -> str:
    """종목 분석 결과를 마크다운 문자열로 요약한다.

    Args:
        snapshot: get_company_snapshot() 결과 (name/ticker/price/currency 등).
        valuation_snapshot: get_valuation_snapshot() 결과 (PER/PBR/배당/ROE). 없으면 해당
            섹션을 생략한다.
        dcf_result: simplified_dcf() 결과. 없으면 DCF 섹션을 생략한다.
        coe_source: {"mode": "manual"|"auto", "coe": float, ...} — STEP 1(COE 자동제안)에서
            기록한 출처 정보. DCF 섹션이 있을 때만 쓰인다.
        implied_growth: implied_growth_rate() 결과 (있으면). 해가 없었으면 None.
        as_of: 조회 시점(오늘 날짜).

    Returns:
        마크다운 문자열.
    """
    currency = snapshot["currency"]
    lines = [
        f"# {snapshot['name']} ({snapshot['ticker']}) 분석 요약",
        "",
        f"- 조회 시점: {as_of:%Y-%m-%d}",
        f"- 현재가: {format_money(snapshot['price'], currency)}",
    ]

    if valuation_snapshot is not None:
        lines += [
            "",
            "## 밸류에이션",
            f"- PER: {format_multiple(valuation_snapshot.get('per'))}",
            f"- Fwd PER: {format_multiple(valuation_snapshot.get('forward_per'))}",
            f"- PBR: {format_multiple(valuation_snapshot.get('pbr'))}",
            f"- 배당수익률: {format_percent(valuation_snapshot.get('dividend_yield'))}",
            f"- 배당성향: {format_percent(valuation_snapshot.get('payout_ratio'))}",
            f"- ROE: {format_percent(valuation_snapshot.get('roe'))}",
        ]

    if dcf_result is not None:
        assumptions = dcf_result["assumptions"]
        intrinsic = dcf_result["intrinsic_value_per_share"]
        price = snapshot["price"]
        upside = (intrinsic - price) / price if price else None
        lines += [
            "",
            "## DCF 요약",
            f"- 주당 내재가치: {format_money(intrinsic, currency)}",
            f"- 현재 주가: {format_money(price, currency)}",
            f"- 괴리율: {upside * 100:+.1f}%" if upside is not None else "- 괴리율: N/A",
            f"- 투영 연수: {assumptions['years']}년",
            f"- 성장률 가정: {_format_growth_assumption(assumptions)}",
            f"- 영구성장률: {assumptions['terminal_growth'] * 100:.2f}%",
            f"- 요구수익률(COE): {_format_coe(coe_source)}",
        ]
        if implied_growth is not None:
            lines.append(f"- 현재 주가에 내재된 성장률: {implied_growth * 100:.1f}%")

    lines += ["", "---", DISCLAIMER]
    return "\n".join(lines)
