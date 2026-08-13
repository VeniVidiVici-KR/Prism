"""valley/valuation.py 순수 함수 단위테스트.

⚠️ 네트워크를 타지 않는 함수만 대상으로 한다(suggest_coe는 FRED/토스 조회가 필요해 원래
제외 — AppTest 회귀에서 실제 페이지 흐름의 일부로 간접 검증된다). 각 함수의 정상 케이스뿐
아니라 과거 라운드에서 실측으로 발견한 엣지 케이스를 그대로 고정한다.

⚠️ 예외 — suggest_coe의 "토스 실패 시 FRED로 폴백" 분기(2026-08 국채 라운드 추가)는
get_indicator_series를 mock해서 실제 네트워크 없이 검증한다. 이 분기는 실제 시세 조회가
아니라 "어느 소스를 선택하는가"라는 순수 분기 로직이라, 위 방침이 막으려는 "실제 조회
테스트"에 해당하지 않는다고 판단했다 — 회귀 위험이 큰 로직(COE가 DCF 결과를 직접 바꿈)이라
mock으로라도 고정해두는 게 낫다.
"""

import math
from unittest.mock import patch

import pandas as pd
import pytest

from valley.valuation import (
    DEFAULT_STATUTORY_TAX_RATE,
    CoeSuggestionUnavailableError,
    FcffInputsUnavailableError,
    ImpliedGrowthNotFoundError,
    capm_coe,
    compute_cost_of_debt,
    compute_effective_tax_rate,
    compute_wacc,
    dividend_growth_history,
    fcf_growth_history,
    fcf_to_fcff,
    fcff_dcf,
    gordon_ddm,
    implied_growth_rate,
    justified_pbr,
    sensitivity_grid,
    simplified_dcf,
    suggest_coe,
)


# ── simplified_dcf ──────────────────────────────────────────────────────────


def test_simplified_dcf_single_growth_rate():
    result = simplified_dcf(
        latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.10,
        terminal_growth=0.025, years=5, shares_outstanding=100.0,
    )
    assert len(result["yearly"]) == 5
    # 단일 성장률이면 매 연도 growth_rate가 동일해야 한다.
    assert all(y["growth_rate"] == pytest.approx(0.08) for y in result["yearly"])
    assert result["intrinsic_value_per_share"] > 0


def test_simplified_dcf_growth_rate_terminal_none_matches_single_rate():
    """⚠️ 하위호환 보장: growth_rate_terminal을 생략하면 growth_rate와 같은 값을 명시한
    것과 수학적으로 완전히 동일해야 한다(2-stage 감쇠 도입 후에도 기존 호출부가 안 깨지는
    이유)."""
    kwargs = dict(
        latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.10,
        terminal_growth=0.025, years=5, shares_outstanding=100.0,
    )
    implicit = simplified_dcf(**kwargs)
    explicit = simplified_dcf(**kwargs, growth_rate_terminal=0.08)
    assert implicit["intrinsic_value_per_share"] == pytest.approx(explicit["intrinsic_value_per_share"])
    assert implicit["equity_value"] == pytest.approx(explicit["equity_value"])


def test_simplified_dcf_two_stage_fade():
    result = simplified_dcf(
        latest_fcf=1000.0, growth_rate=0.12, growth_rate_terminal=0.04,
        discount_rate=0.10, terminal_growth=0.025, years=5, shares_outstanding=100.0,
    )
    growth_rates = [y["growth_rate"] for y in result["yearly"]]
    assert growth_rates[0] == pytest.approx(0.12)
    assert growth_rates[-1] == pytest.approx(0.04)
    # 선형 감쇠이므로 단조 감소해야 한다.
    assert all(a >= b for a, b in zip(growth_rates, growth_rates[1:]))


def test_simplified_dcf_discount_rate_must_exceed_terminal_growth():
    with pytest.raises(ValueError):
        simplified_dcf(
            latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.02,
            terminal_growth=0.025, years=5, shares_outstanding=100.0,
        )


def test_simplified_dcf_rejects_non_positive_fcf():
    with pytest.raises(ValueError):
        simplified_dcf(
            latest_fcf=-10.0, growth_rate=0.08, discount_rate=0.10,
            terminal_growth=0.025, years=5, shares_outstanding=100.0,
        )


def test_simplified_dcf_rejects_invalid_shares_outstanding():
    with pytest.raises(ValueError):
        simplified_dcf(
            latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.10,
            terminal_growth=0.025, years=5, shares_outstanding=0,
        )


# ── implied_growth_rate ─────────────────────────────────────────────────────


def test_implied_growth_rate_recovers_known_growth_rate():
    """알려진 growth_rate로 simplified_dcf를 돌려 현재가를 만든 뒤, 그 현재가로 역산하면
    같은 growth_rate가 나와야 한다(왕복 검증)."""
    known_growth = 0.08
    forward = simplified_dcf(
        latest_fcf=1000.0, growth_rate=known_growth, discount_rate=0.10,
        terminal_growth=0.025, years=5, shares_outstanding=100.0,
    )
    price = forward["intrinsic_value_per_share"]

    implied = implied_growth_rate(
        latest_fcf=1000.0, discount_rate=0.10, terminal_growth=0.025,
        years=5, shares_outstanding=100.0, current_price=price,
    )
    assert implied == pytest.approx(known_growth, abs=1e-3)


def test_implied_growth_rate_not_found_when_price_too_high():
    with pytest.raises(ImpliedGrowthNotFoundError):
        implied_growth_rate(
            latest_fcf=1000.0, discount_rate=0.10, terminal_growth=0.025,
            years=5, shares_outstanding=100.0, current_price=1e12,
        )


# ── sensitivity_grid ─────────────────────────────────────────────────────────


def test_sensitivity_grid_shape():
    grid = sensitivity_grid(
        latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.10,
        terminal_growth=0.025, years=5, shares_outstanding=100.0, current_price=50.0,
    )
    assert len(grid["growth_axis"]) == 5
    assert len(grid["discount_axis"]) == 5
    assert len(grid["upside"]) == 5
    assert all(len(row) == 5 for row in grid["upside"])
    # 중심 셀은 기준 가정과 정확히 같아야 한다.
    center = 5 // 2
    assert grid["discount_axis"][center] == pytest.approx(0.10)
    assert grid["growth_axis"][center] == pytest.approx(0.08)


def test_sensitivity_grid_isolates_failing_cells():
    """discount_rate <= terminal_growth가 되는 조합이 격자 안에 섞여도(discount_step을 작게,
    terminal_growth를 discount_rate 중심 가까이 둠) 그 셀만 None이고 전체가 죽지 않아야 한다."""
    grid = sensitivity_grid(
        latest_fcf=1000.0, growth_rate=0.08, discount_rate=0.03,
        terminal_growth=0.025, years=5, shares_outstanding=100.0, current_price=50.0,
        discount_steps=5, discount_step_size=0.005,
    )
    flat = [v for row in grid["upside"] for v in row]
    assert any(v is None for v in flat), "discount_rate<=terminal_growth 조합이 있어야 이 테스트가 의미 있음"
    assert any(v is not None for v in flat)


# ── justified_pbr ─────────────────────────────────────────────────────────────


def test_justified_pbr_normal():
    assert justified_pbr(roe=0.12, coe=0.10) == pytest.approx(1.2)


def test_justified_pbr_rejects_non_positive_roe():
    with pytest.raises(ValueError):
        justified_pbr(roe=-0.05, coe=0.10)
    with pytest.raises(ValueError):
        justified_pbr(roe=0.0, coe=0.10)


def test_justified_pbr_rejects_non_positive_coe():
    with pytest.raises(ValueError):
        justified_pbr(roe=0.12, coe=0.0)
    with pytest.raises(ValueError):
        justified_pbr(roe=0.12, coe=-0.01)


# ── gordon_ddm ─────────────────────────────────────────────────────────────


def test_gordon_ddm_normal():
    # D1 = 1000*1.03 = 1030, / (0.08-0.03) = 20600
    assert gordon_ddm(dps=1000.0, growth_rate=0.03, coe=0.08) == pytest.approx(20600.0)


def test_gordon_ddm_diverges_when_growth_at_or_above_coe():
    with pytest.raises(ValueError):
        gordon_ddm(dps=1000.0, growth_rate=0.08, coe=0.08)
    with pytest.raises(ValueError):
        gordon_ddm(dps=1000.0, growth_rate=0.10, coe=0.08)


def test_gordon_ddm_rejects_non_positive_dps():
    with pytest.raises(ValueError):
        gordon_ddm(dps=0.0, growth_rate=0.03, coe=0.08)
    with pytest.raises(ValueError):
        gordon_ddm(dps=-5.0, growth_rate=0.03, coe=0.08)
    with pytest.raises(ValueError):
        gordon_ddm(dps=None, growth_rate=0.03, coe=0.08)


def test_gordon_ddm_rejects_non_positive_coe():
    with pytest.raises(ValueError):
        gordon_ddm(dps=1000.0, growth_rate=0.03, coe=0.0)


# ── fcf_growth_history / dividend_growth_history ────────────────────────────


@pytest.mark.parametrize("history_fn", [fcf_growth_history, dividend_growth_history])
def test_growth_history_normal_cagr(history_fn):
    series = pd.Series([100.0, 110.0, 121.0], index=["2022", "2023", "2024"])
    result = history_fn(series)
    assert result["cagr"] == pytest.approx(0.10, abs=1e-9)
    assert result["cagr_reason"] is None
    assert result["has_non_positive_year"] is False
    assert result["yearly"][0]["yoy"] is None  # 첫 연도는 비교 대상 없음
    assert result["yearly"][1]["yoy"] == pytest.approx(0.10)


@pytest.mark.parametrize("history_fn", [fcf_growth_history, dividend_growth_history])
def test_growth_history_yoy_unavailable_when_prev_non_positive(history_fn):
    """⚠️ 전년 값이 0 이하면 그 해의 YoY는 계산하지 않고 사유를 남긴다(부호가 뒤집혀 무의미)."""
    series = pd.Series([-50.0, 80.0], index=["2022", "2023"])
    result = history_fn(series)
    assert result["yearly"][1]["yoy"] is None
    assert result["yearly"][1]["yoy_reason"] == "계산 불가(전년 배당≤0)" if history_fn is dividend_growth_history else "계산 불가(전년 FCF≤0)"
    assert result["has_non_positive_year"] is True


@pytest.mark.parametrize("history_fn", [fcf_growth_history, dividend_growth_history])
def test_growth_history_cagr_unavailable_when_start_or_end_negative(history_fn):
    """⚠️ 시작 또는 종료 연도가 음수면 CAGR을 계산하지 않는다(기하평균이 정의되지 않음)."""
    series = pd.Series([-50.0, 30.0, 80.0], index=["2022", "2023", "2024"])
    result = history_fn(series)
    assert result["cagr"] is None
    assert "계산 불가" in result["cagr_reason"]


@pytest.mark.parametrize("history_fn", [fcf_growth_history, dividend_growth_history])
def test_growth_history_cagr_unavailable_when_fewer_than_two_years(history_fn):
    series = pd.Series([100.0], index=["2024"])
    result = history_fn(series)
    assert result["cagr"] is None
    assert "2개년 미만" in result["cagr_reason"]
    assert result["yearly"][0]["yoy"] is None


# ── WACC 계열: 실효세율 ──────────────────────────────────────────────────────


def test_effective_tax_rate_normal():
    result = compute_effective_tax_rate(tax_provision=20719e6, pretax_income=132729e6, currency="USD")
    assert result["source"] == "effective"
    assert result["rate"] == pytest.approx(20719 / 132729, abs=1e-6)
    assert result["warning"] is None


@pytest.mark.parametrize("currency", ["USD", "KRW"])
def test_effective_tax_rate_falls_back_when_pretax_income_non_positive(currency):
    result = compute_effective_tax_rate(tax_provision=-100.0, pretax_income=-50.0, currency=currency)
    assert result["source"] == "fallback"
    assert result["rate"] == DEFAULT_STATUTORY_TAX_RATE[currency]
    assert result["warning"] is not None


def test_effective_tax_rate_falls_back_when_data_missing():
    result = compute_effective_tax_rate(tax_provision=None, pretax_income=None, currency="USD")
    assert result["source"] == "fallback"
    assert result["rate"] == DEFAULT_STATUTORY_TAX_RATE["USD"]


def test_effective_tax_rate_negative_when_tax_refunded():
    """법인세 환급(음수)은 폴백하지 않고 음수 세율을 그대로 반환하되 경고를 세운다."""
    result = compute_effective_tax_rate(tax_provision=-10.0, pretax_income=100.0, currency="USD")
    assert result["source"] == "effective"
    assert result["rate"] == pytest.approx(-0.1)
    assert result["warning"] is not None


def test_effective_tax_rate_warns_when_outside_common_range():
    result = compute_effective_tax_rate(tax_provision=60.0, pretax_income=100.0, currency="USD")
    assert result["source"] == "effective"
    assert result["rate"] == pytest.approx(0.6)
    assert result["warning"] is not None


# ── WACC 계열: 부채비용 ──────────────────────────────────────────────────────


def test_cost_of_debt_normal():
    result = compute_cost_of_debt(interest_expense=3051e6, total_debt=56826e6)
    assert result["rate"] == pytest.approx(3051 / 56826, abs=1e-6)
    assert result["warning"] is None


def test_cost_of_debt_unlevered_when_total_debt_zero_or_negative():
    """⚠️ 무차입(총부채 0) 기업은 0으로 나누지 않고 rate=None을 반환해야 한다."""
    assert compute_cost_of_debt(interest_expense=1000.0, total_debt=0.0) == {"rate": None, "warning": None}
    assert compute_cost_of_debt(interest_expense=1000.0, total_debt=-10.0) == {"rate": None, "warning": None}


def test_cost_of_debt_warns_when_outside_common_range():
    result = compute_cost_of_debt(interest_expense=400.0, total_debt=1000.0)
    assert result["rate"] == pytest.approx(0.4)
    assert result["warning"] is not None


# ── WACC 계열: CAPM / WACC ───────────────────────────────────────────────────


def test_capm_coe():
    assert capm_coe(risk_free_rate=0.04, beta=1.2, risk_premium=0.05) == pytest.approx(0.10)


def test_capm_coe_beta_one_matches_simple_rf_plus_erp():
    """⚠️ 기존 COE 자동제안(Rf+ERP)은 CAPM에서 베타=1을 대입한 것과 동치여야 한다."""
    rf, erp = 0.04, 0.05
    assert capm_coe(rf, 1.0, erp) == pytest.approx(rf + erp)


def test_compute_wacc_lower_than_coe_when_levered():
    """세후 부채비용이 COE보다 싸면 WACC는 COE보다 낮아야 한다(통상적인 경우)."""
    result = compute_wacc(
        market_cap=3.0e12, total_debt=56.826e9, coe=0.10, cost_of_debt_pretax=0.0537, tax_rate=0.194
    )
    assert result["unlevered"] is False
    assert result["wacc"] < 0.10
    assert result["weight_equity"] + result["weight_debt"] == pytest.approx(1.0)


def test_compute_wacc_unlevered_equals_coe():
    """⚠️ 무차입(cost_of_debt_pretax=None)이면 WACC=COE로 처리하고 unlevered=True를 표시해야
    한다."""
    result = compute_wacc(market_cap=1000.0, total_debt=0.0, coe=0.09, cost_of_debt_pretax=None, tax_rate=0.21)
    assert result["wacc"] == pytest.approx(0.09)
    assert result["unlevered"] is True
    assert result["weight_equity"] == 1.0
    assert result["weight_debt"] == 0.0


def test_compute_wacc_rejects_invalid_market_cap():
    with pytest.raises(ValueError):
        compute_wacc(market_cap=0.0, total_debt=100.0, coe=0.09, cost_of_debt_pretax=0.05, tax_rate=0.21)
    with pytest.raises(ValueError):
        compute_wacc(market_cap=None, total_debt=100.0, coe=0.09, cost_of_debt_pretax=0.05, tax_rate=0.21)


# ── WACC 계열: FCFF 전환 ─────────────────────────────────────────────────────


def test_fcf_to_fcff_normal():
    # FCFF = 1000 + 300*(1-0.21) = 1237
    assert fcf_to_fcff(fcf=1000.0, interest_expense=300.0, tax_rate=0.21) == pytest.approx(1237.0)


def test_fcf_to_fcff_raises_when_interest_expense_missing():
    """⚠️ AAPL 케이스: 최근 회계연도에 이자비용이 아예 공시되지 않는 종목이 실제로 있다 —
    0으로 조용히 대체하지 않고 명시적으로 실패해야 한다."""
    with pytest.raises(FcffInputsUnavailableError):
        fcf_to_fcff(fcf=1000.0, interest_expense=None, tax_rate=0.21)


# ── WACC 계열: fcff_dcf (정식 DCF 본체) ──────────────────────────────────────


def test_fcff_dcf_net_cash_company_equity_exceeds_ev():
    """⚠️ 삼성전자 케이스: 순현금(현금 > 총부채) 기업은 Equity Value가 EV보다 커야 하고,
    0으로 클램프하면 안 된다."""
    result = fcff_dcf(
        latest_fcff=50_000_000_000_000, growth_rate=0.05, wacc=0.09, terminal_growth=0.025,
        years=5, total_debt=25_239_139_000_000, cash_and_st_investments=125_847_114_000_000,
        shares_outstanding=5_969_782_550,
    )
    assert result["net_debt"] < 0
    assert result["equity_value"] > result["enterprise_value"]
    assert result["intrinsic_value_per_share"] > 0


def test_fcff_dcf_leveraged_company_equity_below_ev():
    result = fcff_dcf(
        latest_fcff=1000.0, growth_rate=0.05, wacc=0.09, terminal_growth=0.025,
        years=5, total_debt=500.0, cash_and_st_investments=100.0, shares_outstanding=100.0,
    )
    assert result["net_debt"] == pytest.approx(400.0)
    assert result["equity_value"] == pytest.approx(result["enterprise_value"] - 400.0)


def test_fcff_dcf_rejects_wacc_at_or_below_terminal_growth():
    with pytest.raises(ValueError):
        fcff_dcf(
            latest_fcff=1000.0, growth_rate=0.05, wacc=0.02, terminal_growth=0.025,
            years=5, total_debt=0.0, cash_and_st_investments=0.0, shares_outstanding=100.0,
        )


def test_fcff_dcf_rejects_non_positive_fcff():
    with pytest.raises(ValueError):
        fcff_dcf(
            latest_fcff=-10.0, growth_rate=0.05, wacc=0.09, terminal_growth=0.025,
            years=5, total_debt=0.0, cash_and_st_investments=0.0, shares_outstanding=100.0,
        )


def test_fcff_dcf_matches_simplified_dcf_when_unlevered_and_no_interest():
    """무차입·이자비용 0(FCFF=FCF)인 특수 케이스에서는 정식 DCF의 EV가 간이 DCF의 Equity와
    같아야 한다(둘 다 순부채=0이므로 EV=Equity, WACC=COE)."""
    common = dict(
        growth_rate=0.08, terminal_growth=0.025, years=5, shares_outstanding=100.0,
    )
    simplified = simplified_dcf(latest_fcf=1000.0, discount_rate=0.10, **common)
    fcff = fcf_to_fcff(fcf=1000.0, interest_expense=0.0, tax_rate=0.21)
    fcff_result = fcff_dcf(
        latest_fcff=fcff, wacc=0.10, total_debt=0.0, cash_and_st_investments=0.0, **common
    )
    assert fcff_result["enterprise_value"] == pytest.approx(simplified["equity_value"])
    assert fcff_result["equity_value"] == pytest.approx(simplified["equity_value"])
    assert fcff_result["intrinsic_value_per_share"] == pytest.approx(simplified["intrinsic_value_per_share"])


# ── suggest_coe (국채 라운드 2026-08 — 토스 우선/FRED 폴백, get_indicator_series mock) ──────


def _series(value, date="2026-08-12"):
    return pd.Series([value], index=pd.to_datetime([date]))


class MacroDataErrorForTest(Exception):
    """valley.macro.MacroDataError를 흉내낸 더미 예외 — suggest_coe는 Exception 전체를
    잡으므로(주석에 명시된 대로 "예상 밖의 예외까지 폭넓게 막는다") 실제 MacroDataError를
    임포트하지 않고도 분기 검증이 가능하다."""


def test_suggest_coe_krw_uses_toss_bond_when_available():
    """토스가 정상 응답하면 KR_BOND_10Y(토스 일별)를 쓰고, 출처 라벨에 "토스 일별"이 명시돼야
    한다."""
    with patch("valley.valuation.get_indicator_series", return_value=_series(4.295)) as mock_fn:
        result = suggest_coe("KRW", 0.05)
    mock_fn.assert_called_once_with("KR_BOND_10Y")
    assert result["rf"] == pytest.approx(0.04295)
    assert result["coe"] == pytest.approx(0.04295 + 0.05)
    assert "토스 일별" in result["rf_source_label"]
    assert result["rf_source_key"] == "KR_BOND_10Y"


def test_suggest_coe_krw_falls_back_to_fred_when_toss_fails():
    """⚠️ 핵심 회귀 — 토스 조회가 실패하면(키 없음/네트워크 등 무엇이든) 예외 없이 기존
    KR_DGS10(FRED 월별)으로 폴백해야 한다. 폴백 시에도 COE 계산 자체는 정상 완료돼야
    하고, 출처 라벨에 "FRED 월별 폴백"이 명시돼야 한다(어느 소스를 썼는지 화면에 표시하기
    위함)."""
    call_log = []

    def fake_get_indicator_series(key):
        call_log.append(key)
        if key == "KR_BOND_10Y":
            raise MacroDataErrorForTest("TOSS_CLIENT_ID가 설정되어 있지 않습니다.")
        assert key == "KR_DGS10"
        return _series(4.181, date="2026-06-01")

    with patch("valley.valuation.get_indicator_series", side_effect=fake_get_indicator_series):
        result = suggest_coe("KRW", 0.05)

    assert call_log == ["KR_BOND_10Y", "KR_DGS10"]  # 토스 먼저 시도 -> 실패 -> FRED 폴백
    assert result["rf"] == pytest.approx(0.04181)
    assert result["coe"] == pytest.approx(0.04181 + 0.05)
    assert "FRED 월별 폴백" in result["rf_source_label"]
    assert result["rf_source_key"] == "KR_DGS10"


def test_suggest_coe_krw_raises_when_both_toss_and_fred_fail():
    """토스·FRED 둘 다 실패하는 극단적 상황에서도 앱이 안 죽고 명확한 예외로 처리돼야 한다."""
    with patch("valley.valuation.get_indicator_series", side_effect=MacroDataErrorForTest("조회 실패")):
        with pytest.raises(CoeSuggestionUnavailableError):
            suggest_coe("KRW", 0.05)


def test_suggest_coe_usd_path_unchanged_no_fallback_branch():
    """⚠️ 회귀 확인 — USD는 이번 라운드에서 건드리지 않았다. 실패해도 KRW식 폴백을 시도하지
    않고 바로 CoeSuggestionUnavailableError를 던져야 한다(US_DGS10 한 번만 호출)."""
    with patch("valley.valuation.get_indicator_series", side_effect=MacroDataErrorForTest("실패")) as mock_fn:
        with pytest.raises(CoeSuggestionUnavailableError):
            suggest_coe("USD", 0.05)
    mock_fn.assert_called_once_with("US_DGS10")
