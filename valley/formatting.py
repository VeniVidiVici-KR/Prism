"""통화별 금액 및 배수(PER/PBR 등) 표시 포맷팅."""

import math


def format_money(value: float, currency: str) -> str:
    """통화에 맞는 기호/소수점 자리수로 금액 문자열을 만든다."""
    if currency == "KRW":
        return f"₩{value:,.0f}"
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


def format_multiple(value: float | None) -> str:
    """PER/PBR 같은 배수를 'N.N배'로 표시한다. 값이 없으면 'N/A'.

    None과 NaN을 모두 처리한다: 여러 종목을 DataFrame으로 묶을 때 pandas가
    같은 컬럼 안의 None을 NaN으로 자동 변환하는 경우가 있기 때문이다.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:.1f}배"


def format_percent(value: float | None) -> str:
    """배당수익률/배당성향/ROE 같은 비율을 'N.N%'로 표시한다. 값이 없으면 'N/A'.

    내부 저장값은 비율(0.024 = 2.4%) 기준이라 ×100 해서 표시한다.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value * 100:.1f}%"
