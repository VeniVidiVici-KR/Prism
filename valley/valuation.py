"""밸류에이션 계산 로직. UI(Streamlit)·yfinance 의존성 없이, valley/macro.py의 조회 함수만
재사용하는 순수 로직으로 구성."""

import pandas as pd

from valley.macro import get_indicator_series


class ImpliedGrowthNotFoundError(Exception):
    """탐색 구간(하한~상한) 안에서 현재가를 정당화하는 성장률을 찾지 못했을 때 발생."""


class CoeSuggestionUnavailableError(Exception):
    """통화가 지원 대상이 아니거나 무위험이자율 조회에 실패해 COE 자동제안을 만들 수 없을 때 발생."""


# 통화 -> (무위험이자율로 쓸 INDICATORS 카탈로그 key, 사람이 읽을 출처 라벨)
_COE_RF_INDICATOR_BY_CURRENCY = {
    "USD": ("US_DGS10", "미국 10년 국채금리"),
    "KRW": ("KR_DGS10", "한국 10년 국고채금리"),
}


def suggest_coe(currency: str, risk_premium: float) -> dict:
    """무위험이자율(Rf, 실측) + 리스크 프리미엄(ERP, 사용자 가정)으로 COE를 제안한다.

    Rf는 종목 통화에 맞는 10년 국채금리를 FRED에서 조회한 최신값을 그대로 쓴다. ERP(주식
    리스크 프리미엄)는 무료 데이터로 신뢰성 있게 추정할 방법이 없어(그럴듯하지만 근거가
    약한 값이 나올 위험) 자동 추정을 시도하지 않고 사용자가 직접 정하는 가정치로 둔다 —
    실측(Rf)과 가정(ERP)을 섞지 않고 각각 무엇인지 호출부가 구분해 표시할 수 있도록 dict로
    나눠 반환한다.

    Args:
        currency: 종목 통화 ("USD" 또는 "KRW"만 지원. 그 외는 자동제안 불가).
        risk_premium: 사용자가 정한 리스크 프리미엄 (비율, 예: 0.05 = 5%).

    Returns:
        {"coe": Rf+ERP, "rf": 실측 무위험이자율(비율), "risk_premium": ERP,
         "rf_source_label": 출처 한글 라벨, "rf_source_key": INDICATORS 카탈로그 key,
         "rf_as_of": Rf의 관측 시점(Timestamp)}

    Raises:
        CoeSuggestionUnavailableError: 통화 미지원이거나 Rf 조회 자체가 실패했을 때.
    """
    if currency not in _COE_RF_INDICATOR_BY_CURRENCY:
        raise CoeSuggestionUnavailableError(f"'{currency}' 통화는 COE 자동제안을 지원하지 않습니다 (USD/KRW만 지원).")

    indicator_key, label = _COE_RF_INDICATOR_BY_CURRENCY[currency]
    try:
        rf_series = get_indicator_series(indicator_key)
    except Exception as e:
        # get_indicator_series는 보통 MacroDataError만 던지지만, 이 함수는 "Rf 조회 실패 시
        # 앱이 죽으면 안 된다"는 요구사항이 있어 예상 밖의 예외까지 폭넓게 막는다.
        raise CoeSuggestionUnavailableError(f"무위험이자율({label}) 조회에 실패했습니다: {e}") from e

    rf = float(rf_series.iloc[-1]) / 100  # FRED 국채금리는 퍼센트 값(예: 4.18)이라 비율로 환산
    return {
        "coe": rf + risk_premium,
        "rf": rf,
        "risk_premium": risk_premium,
        "rf_source_label": label,
        "rf_source_key": indicator_key,
        "rf_as_of": rf_series.index[-1],
    }


def simplified_dcf(
    latest_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    years: int,
    shares_outstanding: float,
    growth_rate_terminal: float | None = None,
) -> dict:
    """Simplified DCF: FCF를 N년 투영 후 Gordon Growth로 terminal value를 더해
    주당 내재가치를 계산한다.

    latest_fcf는 영업활동현금흐름(OCF) 기준이라 이자비용이 이미 반영된(levered) 값이다.
    따라서 이 함수는 Equity Value를 직접 산출하며, discount_rate는 WACC가 아니라
    요구수익률(Cost of Equity)로 해석해야 한다. 순부채를 별도로 차감하지 않는다.

    growth_rate_terminal을 지정하면 연간 성장률이 1년차 growth_rate(초기)에서 N년차
    growth_rate_terminal(말기)까지 선형으로 감쇠(linear fade)한다. 지정하지 않으면(기본값
    None) growth_rate_terminal은 growth_rate와 같은 값으로 취급되어, 기존처럼 투영기간
    내내 단일 성장률을 쓰는 것과 수학적으로 완전히 동일하다 (하위호환).

    growth_rate_terminal(투영기간 "말기"의 연간 성장률)과 terminal_growth(투영기간 "이후"의
    영구성장률, Gordon Growth에 쓰임)는 서로 다른 개념이니 혼동하지 말 것.

    Args:
        latest_fcf: 최근 회계연도 잉여현금흐름 (OCF - 자본적지출, levered)
        growth_rate: 1년차(초기) 연간 FCF 성장률 (예: 0.08 = 8%)
        discount_rate: 요구수익률/자기자본비용 (예: 0.10 = 10%)
        terminal_growth: 영구성장률 (예: 0.025 = 2.5%)
        years: 투영 연수
        shares_outstanding: 발행주식수
        growth_rate_terminal: N년차(말기) 연간 FCF 성장률. None이면 growth_rate와 동일
            (단일 성장률 모드).

    Returns:
        연도별 투영 결과, terminal value, 주당 내재가치를 담은 dict
    """
    if discount_rate <= terminal_growth:
        raise ValueError("요구수익률은 영구성장률보다 커야 합니다.")
    if shares_outstanding is None or shares_outstanding <= 0:
        raise ValueError("발행주식수 정보가 유효하지 않습니다.")
    if latest_fcf <= 0:
        raise ValueError(
            "최근 잉여현금흐름(FCF)이 0 이하라 DCF 계산의 의미가 없습니다. "
            "대규모 투자·적자 등으로 일시적으로 마이너스일 수 있어 이 모델로는 평가하기 어렵습니다."
        )

    growth_rate_initial = growth_rate
    growth_rate_terminal = growth_rate_terminal if growth_rate_terminal is not None else growth_rate_initial

    yearly = []
    fcf = latest_fcf
    pv_sum = 0.0
    for year in range(1, years + 1):
        fraction = (year - 1) / (years - 1) if years > 1 else 0.0
        year_growth = growth_rate_initial + (growth_rate_terminal - growth_rate_initial) * fraction
        fcf = fcf * (1 + year_growth)
        present_value = fcf / ((1 + discount_rate) ** year)
        pv_sum += present_value
        yearly.append(
            {"year": year, "projected_fcf": fcf, "present_value": present_value, "growth_rate": year_growth}
        )

    terminal_value = (fcf * (1 + terminal_growth)) / (discount_rate - terminal_growth)
    pv_terminal_value = terminal_value / ((1 + discount_rate) ** years)

    equity_value = pv_sum + pv_terminal_value
    intrinsic_value_per_share = equity_value / shares_outstanding

    return {
        "yearly": yearly,
        "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal_value,
        "equity_value": equity_value,
        "intrinsic_value_per_share": intrinsic_value_per_share,
        "assumptions": {
            "growth_rate": growth_rate_initial,
            "growth_rate_initial": growth_rate_initial,
            "growth_rate_terminal": growth_rate_terminal,
            "discount_rate": discount_rate,
            "terminal_growth": terminal_growth,
            "years": years,
        },
    }


def implied_growth_rate(
    latest_fcf: float,
    discount_rate: float,
    terminal_growth: float,
    years: int,
    shares_outstanding: float,
    current_price: float,
    lower_bound: float = -0.5,
    upper_bound_margin: float = 0.005,
    max_iterations: int = 100,
    tolerance: float = 1e-4,
) -> float:
    """현재 주가를 정당화하는 (단일) FCF 성장률을 simplified_dcf 반복 호출 + 이분법으로 역산한다.

    2-stage(초기/말기) 역산은 미지수가 둘이라 유일해가 없으므로 하지 않는다 — 항상 단일
    성장률 기준(simplified_dcf의 growth_rate_terminal 없이 호출)으로 역산한다.

    탐색 구간은 [lower_bound, discount_rate - upper_bound_margin]이다. 상한을 discount_rate
    바로 아래로 제한하는 이유: growth_rate가 discount_rate에 가까워질수록 매년 FCF의 현재가치가
    할인 효과를 거의 상쇄해 equity_value가 비정상적으로 커지므로, 상식적인 탐색 범위를 벗어난다.

    intrinsic_value_per_share는 growth_rate에 대해 단조증가이므로(성장률이 높을수록 미래
    현금흐름이 커져 내재가치도 커진다) 구간 양 끝에서 (내재가치 - current_price)의 부호가
    다르면 그 사이에 유일해가 존재한다. 부호가 같으면(현재가가 이 구간 안에서 전혀 정당화되지
    않으면) ImpliedGrowthNotFoundError를 발생시킨다 — 무한루프 대신 명시적으로 실패를 알린다.

    Raises:
        ValueError: discount_rate <= terminal_growth 등 simplified_dcf 자체의 입력 오류.
        ImpliedGrowthNotFoundError: 탐색 구간 안에 해가 없을 때.
    """
    upper_bound = discount_rate - upper_bound_margin
    if upper_bound <= lower_bound:
        raise ImpliedGrowthNotFoundError(
            "요구수익률이 너무 낮아(또는 하한이 너무 높아) 탐색 구간을 만들 수 없습니다."
        )

    def value_at(g: float) -> float:
        return simplified_dcf(
            latest_fcf=latest_fcf,
            growth_rate=g,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            years=years,
            shares_outstanding=shares_outstanding,
        )["intrinsic_value_per_share"]

    lo, hi = lower_bound, upper_bound
    f_lo = value_at(lo) - current_price
    f_hi = value_at(hi) - current_price

    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:
        raise ImpliedGrowthNotFoundError(
            f"현재가가 상식적 성장률 범위({lower_bound * 100:.0f}%~{upper_bound * 100:.1f}%) "
            "안에서 정당화되지 않습니다."
        )

    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        f_mid = value_at(mid) - current_price
        if abs(f_mid) < tolerance * current_price:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid

    return (lo + hi) / 2


def sensitivity_grid(
    latest_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    years: int,
    shares_outstanding: float,
    current_price: float,
    growth_steps: int = 5,
    growth_step_size: float = 0.005,
    discount_steps: int = 5,
    discount_step_size: float = 0.005,
) -> dict:
    """성장률(행) × COE(열) 격자에서 각 조합의 주당 내재가치·괴리율을 계산한다.

    항상 단일 성장률(growth_rate_terminal 미지정) 기준으로 계산한다 — 2-stage 모드에서도
    민감도 표는 단순하게 유지하기 위해 단일 성장률로 고정한다 (호출부가 UI에 명시해야 한다).

    각 축은 중심값(growth_rate/discount_rate) 기준 ±step_size*(steps//2) 범위를 step_size
    간격으로 펼친다 (기본값: 0.5%p 간격 5개 = 중심 ±1%p).

    격자 셀마다 simplified_dcf를 독립 호출하므로, discount_rate <= terminal_growth가 되는
    조합이 섞여 있어도 그 셀만 None으로 두고 나머지는 계속 계산한다 (전체가 죽지 않게).

    Returns:
        {
            "growth_axis": [...],        성장률 값 리스트 (행, 오름차순)
            "discount_axis": [...],      COE 값 리스트 (열, 오름차순)
            "intrinsic_value": [[...]],  growth_axis x discount_axis 2차원 리스트. 실패 셀은 None.
            "upside": [[...]],           같은 형태의 괴리율((내재가치-현재가)/현재가). 실패 셀은 None.
        }
    """

    def _axis(center: float, steps: int, step_size: float) -> list[float]:
        half = steps // 2
        return [center + (i - half) * step_size for i in range(steps)]

    growth_axis = _axis(growth_rate, growth_steps, growth_step_size)
    discount_axis = _axis(discount_rate, discount_steps, discount_step_size)

    intrinsic_grid: list[list[float | None]] = []
    upside_grid: list[list[float | None]] = []
    for g in growth_axis:
        intrinsic_row: list[float | None] = []
        upside_row: list[float | None] = []
        for d in discount_axis:
            try:
                iv = simplified_dcf(
                    latest_fcf=latest_fcf,
                    growth_rate=g,
                    discount_rate=d,
                    terminal_growth=terminal_growth,
                    years=years,
                    shares_outstanding=shares_outstanding,
                )["intrinsic_value_per_share"]
            except ValueError:
                intrinsic_row.append(None)
                upside_row.append(None)
            else:
                intrinsic_row.append(iv)
                upside_row.append((iv - current_price) / current_price if current_price else None)
        intrinsic_grid.append(intrinsic_row)
        upside_grid.append(upside_row)

    return {
        "growth_axis": growth_axis,
        "discount_axis": discount_axis,
        "intrinsic_value": intrinsic_grid,
        "upside": upside_grid,
    }


def justified_pbr(roe: float, coe: float) -> float:
    """정당(justified) P/B = ROE / COE (단순형, 성장률 미반영).

    은행 등 금융업의 1차 밸류에이션 지표: 자기자본으로 요구수익률(COE)보다 높은
    ROE를 벌면 장부가 대비 프리미엄(P/B>1)을, 못 벌면 할인(P/B<1)을 받는 게
    정당하다는 논리. 성장 포함형 (ROE-g)/(COE-g)는 g가 COE에 가까워지면 발산해
    (simplified_dcf의 영구성장률과 같은 문제) 이번엔 쓰지 않고 단순형으로 시작한다.

    Raises:
        ValueError: COE가 0 이하이거나(계산 불가), ROE가 0 이하(적자)라 정당 P/B가
            음수가 되어 무의미할 때.
    """
    if coe <= 0:
        raise ValueError("요구수익률(COE)이 0 이하라 정당 P/B를 계산할 수 없습니다.")
    if roe <= 0:
        raise ValueError("ROE가 0 이하(적자)라 정당 P/B 계산이 무의미합니다. 이 모델로는 평가하기 어렵습니다.")
    return roe / coe


def gordon_ddm(dps: float, growth_rate: float, coe: float) -> float:
    """Gordon Growth 배당할인모형으로 주당가치를 계산한다.

    주당가치 = D1 / (COE − g), D1 = D0 × (1 + g). 은행·보험의 또 다른 축 밸류에이션(P/B×ROE와
    나란히 제공) — 배당이 매년 g로 영구히 성장한다는 가정에 기반한다.

    ⚠️ 발산 가드: g가 COE 이상이면 분모(COE−g)가 0 이하가 되어 가치가 무한대/음수로 튄다 —
    simplified_dcf의 영구성장률(terminal_growth)과 정확히 같은 문제라 동일하게 명확히
    실패시킨다(억지로 숫자를 만들지 않는다).

    Raises:
        ValueError: COE가 0 이하, DPS가 0 이하(무배당), 또는 g가 COE 이상일 때.
    """
    if coe <= 0:
        raise ValueError("요구수익률(COE)이 0 이하라 DDM을 계산할 수 없습니다.")
    if dps is None or dps <= 0:
        raise ValueError("배당이 없어(DPS 0 이하) DDM을 적용할 수 없습니다.")
    if growth_rate >= coe:
        raise ValueError("배당성장률이 요구수익률(COE) 이상이라 DDM 계산이 발산합니다. 성장률을 낮춰주세요.")
    d1 = dps * (1 + growth_rate)
    return d1 / (coe - growth_rate)


def dividend_growth_history(dividend_series: pd.Series) -> dict:
    """연도별 배당(DPS) 시계열(get_annual_dividend_history 결과, 연도 오름차순)로부터 연도별
    전년대비 증가율(YoY)과 연평균 증가율(CAGR)을 계산한다.

    ⚠️ fcf_growth_history와 같은 원칙을 배당에 적용한다 — 배당 삭감·중단(0으로 떨어짐)이나
    특별배당(일회성 급증)이 섞이면 증가율이 왜곡되거나 무의미해진다:
    - YoY는 "전년 배당이 0 이하"면 계산하지 않고 사유를 남긴다.
    - CAGR은 시작·종료 연도 배당이 "둘 다 양수"여야 계산하고, 아니면 사유를 남긴다(단순
      연평균은 튀는 값에 취약해 쓰지 않는다 — CAGR만).
    - 시계열 안에 0 이하인 연도가 하나라도 있으면 has_non_positive_year로 알린다 — 호출부가
      연도별 배당 표를 함께 보여줘 사용자가 이상치(삭감·특별배당)를 눈으로 확인하게 한다.

    Returns:
        {
            "yearly": [{"year": str, "dps": float, "yoy": float | None, "yoy_reason": str | None}, ...],
            "cagr": float | None,
            "cagr_reason": str | None,
            "has_non_positive_year": bool,
        }
    """
    years_list = list(dividend_series.index)
    values = [float(v) for v in dividend_series.values]

    yearly = []
    for i, (year, dps) in enumerate(zip(years_list, values)):
        if i == 0:
            yoy, yoy_reason = None, "첫 연도(비교 대상 없음)"
        else:
            prev = values[i - 1]
            if prev <= 0:
                yoy, yoy_reason = None, "계산 불가(전년 배당≤0)"
            else:
                yoy, yoy_reason = (dps - prev) / prev, None
        yearly.append({"year": year, "dps": dps, "yoy": yoy, "yoy_reason": yoy_reason})

    has_non_positive_year = any(v <= 0 for v in values)

    if len(values) < 2:
        cagr, cagr_reason = None, "데이터가 2개년 미만이라 계산 불가"
    else:
        start, end = values[0], values[-1]
        if start <= 0 or end <= 0:
            cagr, cagr_reason = None, "계산 불가(시작/종료 배당이 0 이하)"
        else:
            periods = len(values) - 1
            cagr, cagr_reason = (end / start) ** (1 / periods) - 1, None

    return {
        "yearly": yearly,
        "cagr": cagr,
        "cagr_reason": cagr_reason,
        "has_non_positive_year": has_non_positive_year,
    }


def fcf_growth_history(fcf_series: pd.Series) -> dict:
    """연도별 FCF 시계열(get_free_cash_flow_history 결과, 연도 오름차순)로부터 연도별
    전년대비 증가율(YoY)과 연평균 증가율(CAGR)을 계산한다.

    ⚠️ FCF는 매출/순이익과 달리 변동이 극심해(대규모 capex 등으로 음수가 됐다 회복하면
    증가율이 수백 %로 튐), 아래 규칙을 엄격히 지킨다:
    - YoY는 "전년 FCF가 0 이하"면 부호까지 뒤집혀 무의미하므로 계산하지 않고 사유를 남긴다.
    - CAGR은 시작·종료 연도 FCF가 "둘 다 양수"여야 기하평균이 의미를 가지므로, 그 조건이
      깨지면 억지로 숫자를 만들지 않고 사유를 남긴다. 단순 연평균(YoY들의 평균)은 튀는 값에
      취약해 기본으로 쓰지 않는다 — CAGR(시작→끝, 기간 기하평균) 방식만 쓴다.
    - 시계열 안에 0 이하인 연도가 하나라도 있으면 has_non_positive_year로 알린다.

    Returns:
        {
            "yearly": [{"year": str, "fcf": float, "yoy": float | None, "yoy_reason": str | None}, ...],
            "cagr": float | None,
            "cagr_reason": str | None,
            "has_non_positive_year": bool,
        }
    """
    years_list = list(fcf_series.index)
    values = [float(v) for v in fcf_series.values]

    yearly = []
    for i, (year, fcf) in enumerate(zip(years_list, values)):
        if i == 0:
            yoy, yoy_reason = None, "첫 연도(비교 대상 없음)"
        else:
            prev = values[i - 1]
            if prev <= 0:
                yoy, yoy_reason = None, "계산 불가(전년 FCF≤0)"
            else:
                yoy, yoy_reason = (fcf - prev) / prev, None
        yearly.append({"year": year, "fcf": fcf, "yoy": yoy, "yoy_reason": yoy_reason})

    has_non_positive_year = any(v <= 0 for v in values)

    if len(values) < 2:
        cagr, cagr_reason = None, "데이터가 2개년 미만이라 계산 불가"
    else:
        start, end = values[0], values[-1]
        if start <= 0 or end <= 0:
            cagr, cagr_reason = None, "계산 불가(시작/종료 FCF가 음수)"
        else:
            periods = len(values) - 1
            cagr, cagr_reason = (end / start) ** (1 / periods) - 1, None

    return {
        "yearly": yearly,
        "cagr": cagr,
        "cagr_reason": cagr_reason,
        "has_non_positive_year": has_non_positive_year,
    }
