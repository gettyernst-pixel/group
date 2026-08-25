"""
V2 financial taxonomy — reference values from restaurant-industry definitions
(RestaurantOwner ROI and sales-to-investment; Toast break-even and prime cost).
"""
import pandas as pd
import pytest

from nycsiting import financial_simulation as fs
from nycsiting.financial_simulation import FinancingInputs, SimulationInputs
from tests.test_financial_simulation import make


def frame(inputs, financing=None, months=60, scenario="expected"):
    return fs.extend_with_financing(
        fs.build_simulation_dataframe(inputs, scenario, months),
        inputs, financing)


class TestRestaurantOwnerROI:
    """The denominator is the owner's investment; the numerator is the year's
    distributable cash. Straight from the sample investment analysis."""

    def test_year1_roi(self):
        assert 90_819 / 150_000 * 100 == pytest.approx(60.546)

    def test_year2_roi(self):
        assert 110_228 / 150_000 * 100 == pytest.approx(73.485, abs=0.001)

    def test_average_annual_roi(self):
        values = [60.5, 73.5, 88.5, 88.6, 94.8]
        assert sum(values) / len(values) == pytest.approx(81.18)

    def test_engine_uses_the_same_denominator_logic(self):
        # An engine year of exactly $90,819 owner cash on $150,000 equity
        # must report 60.5%, not anything net-of-investment.
        inputs = make(initial_investment=150_000.0)
        df = frame(inputs)
        df = df.copy()
        # Force a clean known year-1 owner cash flow.
        df.loc[df["year"] == 1, "owner_cash_flow"] = 90_819 / 12
        df["cumulative_owner_cash_flow"] = df["owner_cash_flow"].cumsum()
        summary = fs.roi_summary(df, inputs)
        assert summary["owner_roi_year1_pct"] == pytest.approx(60.546, abs=0.01)


class TestNoDoubleSubtraction:
    def test_504k_on_350k_is_144pct_not_44(self):
        inputs = make(initial_investment=350_000.0)
        df = frame(inputs, months=60)
        df["owner_cash_flow"] = 504_000 / 60
        df["cumulative_owner_cash_flow"] = df["owner_cash_flow"].cumsum()
        s = fs.roi_summary(df, inputs)
        assert s["cumulative_owner_roi_pct"] == pytest.approx(144.0)
        assert s["cash_return_multiple"] == pytest.approx(1.44)
        # The wrong (double-subtracted) answer must not appear anywhere.
        assert s["cumulative_owner_roi_pct"] != pytest.approx(44.0)


class TestOperatingBreakEven:
    def test_toast_contribution_margin_example(self):
        # sales 10,000 · variable 3,000 · fixed 4,000 -> CM ratio 0.7 ->
        # break-even sales 5,714.2857
        inputs = make(food_cost_pct=0.30, labor_cost_pct=0.0,
                      other_variable_cost_pct=0.0, monthly_rent=4_000.0,
                      other_fixed_monthly_cost=0.0, marketing_monthly=0.0)
        be = fs.operating_break_even(inputs)
        assert be["contribution_margin_ratio"] == pytest.approx(0.7)
        assert be["break_even_sales"] == pytest.approx(5714.2857, abs=0.001)

    def test_break_even_covers_use_average_net_check(self):
        inputs = make(food_cost_pct=0.30, labor_cost_pct=0.0,
                      other_variable_cost_pct=0.0, monthly_rent=4_000.0,
                      other_fixed_monthly_cost=0.0, marketing_monthly=0.0,
                      average_customer_spend=45.0)
        be = fs.operating_break_even(inputs)
        assert be["break_even_covers"] == pytest.approx(5714.2857 / 45, abs=0.01)

    def test_variable_share_of_one_is_unreachable_not_a_crash(self):
        inputs = make(food_cost_pct=0.5, labor_cost_pct=0.5,
                      other_variable_cost_pct=0.0)
        be = fs.operating_break_even(inputs)
        assert be["reachable"] is False and be["break_even_sales"] is None

    def test_break_even_is_not_payback(self):
        # Different questions, different numbers.
        inputs = make()
        df = frame(inputs)
        be = fs.operating_break_even(inputs)
        payback = fs.calculate_payback_month(df, inputs.initial_investment)
        assert be["break_even_sales"] is not None
        assert payback != be["break_even_sales"]


class TestPrimeCost:
    def test_industry_example(self):
        # COGS 39,000 + labor 12,000 = 51,000 on 80,000 sales = 63.75%
        assert (39_000 + 12_000) / 80_000 * 100 == pytest.approx(63.75)

    def test_engine_prime_cost_is_cogs_plus_labor(self):
        df = frame(make())
        row = df.iloc[0]
        assert row["prime_cost"] == pytest.approx(
            row["food_cost"] + row["labor_cost"])
        assert row["prime_cost_pct"] == pytest.approx(
            row["prime_cost"] / row["net_sales"])


class TestLoan:
    def test_deterministic_amortization(self):
        sched = fs.amortization_schedule(100_000, 0.06, 12)
        # payment = P r (1+r)^n / ((1+r)^n - 1), r = 0.005, n = 12
        r = 0.06 / 12
        expected_payment = 100_000 * r * (1 + r) ** 12 / ((1 + r) ** 12 - 1)
        assert sched["payment"].iloc[0] == pytest.approx(expected_payment)
        # month 1: interest = 500; principal = payment - 500
        assert sched["interest"].iloc[0] == pytest.approx(500.0)
        assert sched["principal"].iloc[0] == pytest.approx(expected_payment - 500)
        # exact amortization: final balance ~ 0
        assert sched["balance"].iloc[-1] == pytest.approx(0.0, abs=1e-6)
        # principal repaid sums to the loan
        assert sched["principal"].sum() == pytest.approx(100_000, abs=1e-6)

    def test_zero_rate_loan(self):
        sched = fs.amortization_schedule(12_000, 0.0, 12)
        assert sched["payment"].tolist() == pytest.approx([1_000.0] * 12)
        assert sched["interest"].sum() == 0.0

    def test_principal_is_financing_not_operating_cost(self):
        fin = FinancingInputs(enabled=True, loan_principal=200_000,
                              annual_interest_rate=0.08, loan_term_months=84)
        df = frame(make(), fin)
        # Operating cash flow ignores the loan entirely...
        assert (df["operating_cash_flow"] == df["operating_profit"]).all()
        # ...owner cash flow bears the full debt service.
        assert df["owner_cash_flow"].iloc[0] == pytest.approx(
            df["operating_cash_flow"].iloc[0] - df["debt_service"].iloc[0])


class TestProjectVsOwnerROI:
    def test_they_differ_when_debt_exists(self):
        inputs = make(initial_investment=350_000.0)
        fin = FinancingInputs(enabled=True, loan_principal=200_000,
                              annual_interest_rate=0.08, loan_term_months=84)
        df = frame(inputs, fin)
        s = fs.roi_summary(df, inputs, fin)
        # project ROI: pre-financing cash over TOTAL investment
        y1 = df[df.year == 1]
        assert s["project_roi_year1_pct"] == pytest.approx(
            100 * y1["operating_cash_flow"].sum() / 350_000)
        # owner ROI: after debt service, over EQUITY (150k)
        assert s["owner_equity"] == pytest.approx(150_000)
        assert s["owner_roi_year1_pct"] == pytest.approx(
            100 * y1["owner_cash_flow"].sum() / 150_000)
        assert s["project_roi_year1_pct"] != s["owner_roi_year1_pct"]

    def test_without_financing_owner_equals_project(self):
        inputs = make()
        s = fs.roi_summary(frame(inputs), inputs, None)
        assert s["owner_roi_year1_pct"] == pytest.approx(s["project_roi_year1_pct"])

    def test_debt_exceeding_investment_blocks_owner_math(self):
        problems = fs.validate_financing(
            300_000, FinancingInputs(enabled=True, loan_principal=300_000))
        assert any("sources and uses" in p for p in problems)
        s = fs.roi_summary(
            frame(make(initial_investment=300_000.0)),
            make(initial_investment=300_000.0),
            FinancingInputs(enabled=True, loan_principal=300_000))
        assert s["owner_roi_year1_pct"] is None
        assert s["cash_return_multiple"] is None


class TestPayback:
    def test_interpolated_within_the_month(self):
        # prior cumulative 330,000; month flow 30,000; equity 350,000
        # -> fraction 20,000/30,000 -> month 11.667 ≈ 11.7
        df = pd.DataFrame({
            "month": range(1, 13),
            "owner_cash_flow": [30_000.0] * 12,
        })
        df["cumulative_owner_cash_flow"] = df["owner_cash_flow"].cumsum()
        assert fs.calculate_payback_month(df, 350_000) == pytest.approx(11.7)

    def test_not_reached_returns_none(self):
        df = pd.DataFrame({"month": [1, 2], "owner_cash_flow": [10.0, 10.0]})
        df["cumulative_owner_cash_flow"] = df["owner_cash_flow"].cumsum()
        assert fs.calculate_payback_month(df, 1_000_000) is None

    def test_zero_equity_yields_none(self):
        df = pd.DataFrame({"month": [1], "owner_cash_flow": [10.0],
                           "cumulative_owner_cash_flow": [10.0]})
        assert fs.calculate_payback_month(df, 0) is None


class TestSalesToInvestment:
    def test_ratio_is_sales_over_startup_investment(self):
        inputs = make(initial_investment=350_000.0)
        s = fs.roi_summary(frame(inputs), inputs)
        y1 = fs.build_simulation_dataframe(inputs, "expected", 60)
        y1_sales = y1[y1.year == 1]["revenue"].sum()
        assert s["sales_to_investment"] == pytest.approx(y1_sales / 350_000)


class TestStartupBreakdown:
    def test_total_is_the_sum_of_uses(self):
        breakdown = dict(leasehold_improvements=120_000, kitchen_equipment=90_000,
                         opening_inventory=15_000, working_capital=50_000)
        assert fs.total_startup_investment(breakdown, 0) == 275_000

    def test_simple_total_used_when_no_breakdown(self):
        assert fs.total_startup_investment(None, 350_000) == 350_000


class TestOpeningRamp:
    def test_off_by_default_full_utilization_from_month_one(self):
        df = fs.build_simulation_dataframe(make(), "expected", 12)
        assert df["utilization"].nunique() == 1

    def test_linear_ramp_reaches_full_at_ramp_end(self):
        df = fs.build_simulation_dataframe(make(), "expected", 12,
                                           ramp_months=6, ramp_start_factor=0.5)
        u = df["utilization"]
        assert u.iloc[0] == pytest.approx(0.70 * 0.5)
        assert u.iloc[5] == pytest.approx(0.70)          # month 6: full
        assert u.iloc[6] == pytest.approx(0.70)          # stays full
        # linear in between
        assert u.iloc[2] == pytest.approx(0.70 * (0.5 + 0.5 * 2 / 5))


class TestFootfallScenarios:
    def test_scenarios_use_observed_quantiles_not_multipliers(self):
        stats = {"p25": 80.0, "median": 113.0, "p75": 140.0}
        cov = fs.footfall_scenario_covers(make(seats=50), stats, 0.5)
        assert cov["conservative"]["footfall"] == 80.0
        assert cov["expected"]["footfall"] == 113.0
        assert cov["optimistic"]["footfall"] == 140.0
        assert all(v["capture_rate"] == 0.5 for v in cov.values())

    def test_capacity_ceiling_binds(self):
        stats = {"p25": 10_000.0, "median": 10_000.0, "p75": 10_000.0}
        inputs = make(seats=50)
        cov = fs.footfall_scenario_covers(inputs, stats, 0.5)
        cap = 50 * 1.8 * inputs.maximum_utilization
        assert all(v["covers_daily"] == pytest.approx(cap) for v in cov.values())

    def test_v2_runner_orders_by_footfall(self):
        stats = {"p25": 60.0, "median": 90.0, "p75": 120.0}
        cov = fs.footfall_scenario_covers(make(), stats, 0.5)
        res = fs.calculate_all_scenarios_v2(make(), 12,
                                            footfall_covers_by_scenario=cov)
        sales = [res[k]["v2"]["year1_net_sales"]
                 for k in ("conservative", "expected", "optimistic")]
        assert sales == sorted(sales)
        assert res["expected"]["footfall"]["quantile"] == "median"
