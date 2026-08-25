"""
The financial engine. Every number the simulator shows traces to arithmetic in
financial_simulation.py, and these tests reconcile that arithmetic by hand —
the spec's ten required cases plus the guards around them.
"""
import math

import numpy as np
import pandas as pd
import pytest

from nycsiting import financial_simulation as fs
from nycsiting.financial_simulation import SimulationInputs


def make(**over) -> SimulationInputs:
    """Reference restaurant used throughout: the spec's worked example."""
    base = dict(average_customer_spend=45.0, seats=55, monthly_rent=12_000.0,
                initial_investment=350_000.0, operating_days_per_week=7,
                food_cost_pct=0.30, labor_cost_pct=0.32,
                other_variable_cost_pct=0.08, other_fixed_monthly_cost=6_000.0,
                marketing_monthly=2_000.0, table_turns_per_day=1.8,
                base_utilization=0.70, annual_revenue_growth=0.0,
                annual_cost_growth=0.0, maximum_utilization=0.90)
    base.update(over)
    return SimulationInputs(**base)


class TestCoreArithmetic:
    def test_1_daily_customers_reconcile_by_hand(self):
        # 55 seats * 1.8 turns * 0.70 utilisation = 69.3 covers/day
        assert fs.calculate_daily_capacity(make()) == pytest.approx(69.3)

    def test_2_revenue_reconciles_by_hand(self):
        # 69.3/day * $45 = $3,118.50/day; monthly uses 7 days * 52/12 weeks:
        # 69.3 * 7 * 52/12 = 2,102.1 customers -> * 45 = $94,594.50
        ops = fs.calculate_monthly_operations(make())
        assert ops["customers"] == pytest.approx(69.3 * 7 * 52 / 12)
        assert ops["revenue"] == pytest.approx(69.3 * 7 * 52 / 12 * 45)
        assert ops["revenue"] == pytest.approx(94_594.50)

    def test_3_operating_profit_reconciles_by_hand(self):
        # revenue 94,594.50; variable = 70% of revenue = 66,216.15;
        # fixed = 12,000 + 6,000 + 2,000 = 20,000
        # profit = 94,594.50 - 66,216.15 - 20,000 = 8,378.35
        ops = fs.calculate_monthly_operations(make())
        assert ops["food_cost"] == pytest.approx(94_594.50 * 0.30)
        assert ops["fixed_cost"] == pytest.approx(20_000.0)
        assert ops["operating_profit"] == pytest.approx(8_378.35)
        assert ops["operating_margin"] == pytest.approx(8_378.35 / 94_594.50)

    def test_4_break_even_month_is_where_cumulative_crosses_zero(self):
        # profit 8,378.35/month against 350,000 in:
        # 350,000 / 8,378.35 = 41.77 -> month 42
        frames = fs.build_simulation_dataframe(make(), "expected", 60)
        assert fs.calculate_break_even(frames) == 42
        # and the frame agrees with itself at that month
        at = frames[frames.month == 42].iloc[0]
        assert at["cumulative_return_after_investment"] >= 0
        before = frames[frames.month == 41].iloc[0]
        assert before["cumulative_return_after_investment"] < 0

    def test_5_never_breaking_even_returns_none(self):
        df = fs.build_simulation_dataframe(
            make(base_utilization=0.10, initial_investment=1_000_000.0),
            "expected", 60)
        assert fs.calculate_break_even(df) is None

    def test_6_zero_investment_yields_no_roi_and_no_crash(self):
        inputs = make(initial_investment=0.0)
        df = fs.build_simulation_dataframe(inputs, "expected", 12)
        assert fs.calculate_roi(df, inputs) is None
        summary = fs.summarise_scenario(df, inputs)
        assert summary["roi"] is None
        assert summary["break_even_month"] == 1   # nothing to recover
        assert df["roi"].isna().all()

    def test_7_negative_profit_scenario_stays_coherent(self):
        inputs = make(base_utilization=0.10)   # thin trade, heavy fixed costs
        df = fs.build_simulation_dataframe(inputs, "expected", 60)
        assert (df["operating_profit"] < 0).all()
        assert df["cumulative_return_after_investment"].is_monotonic_decreasing
        assert (df["investment_recovery_pct"] == 0).all()

    def test_8_scenarios_order_logically(self):
        results = fs.calculate_all_scenarios(make(), 60)
        c = results["conservative"]["summary"]
        e = results["expected"]["summary"]
        o = results["optimistic"]["summary"]
        assert c["year1_revenue"] <= e["year1_revenue"] <= o["year1_revenue"]
        assert (c["cumulative_operating_profit"]
                <= e["cumulative_operating_profit"]
                <= o["cumulative_operating_profit"])

    def test_9_utilization_is_clamped_both_ends(self):
        assert fs.calculate_daily_capacity(make(), utilization=-0.5) == 0
        capped = fs.calculate_daily_capacity(make(), utilization=5.0)
        assert capped == pytest.approx(55 * 1.8 * 0.90)   # max, not 5.0
        # optimistic multiplier cannot push past the cap either:
        df = fs.build_simulation_dataframe(
            make(base_utilization=0.85), "optimistic", 12)
        assert (df["utilization"] <= 0.90).all()

    def test_10_nan_and_infinite_inputs_are_rejected_not_propagated(self):
        for bad in (float("nan"), float("inf"), -float("inf")):
            errors, _ = fs.validate_simulation_inputs(
                make(average_customer_spend=bad))
            assert errors, f"{bad} accepted"
        errors, _ = fs.validate_simulation_inputs(make(seats=-5))
        assert any("Seats" in e for e in errors)
        errors, _ = fs.validate_simulation_inputs(make(monthly_rent=-1))
        assert errors


class TestValidationWarnings:
    def test_variable_costs_over_100pct_warns(self):
        _, warnings = fs.validate_simulation_inputs(
            make(food_cost_pct=0.5, labor_cost_pct=0.5,
                 other_variable_cost_pct=0.1))
        assert any("loses money" in w for w in warnings)

    def test_revenue_below_fixed_costs_warns(self):
        _, warnings = fs.validate_simulation_inputs(make(base_utilization=0.05))
        assert any("does not cover fixed costs" in w for w in warnings)

    def test_absurd_capacity_warns(self):
        _, warnings = fs.validate_simulation_inputs(
            make(seats=500, table_turns_per_day=10.0, base_utilization=0.9,
                 maximum_utilization=1.0))
        assert any("covers per day" in w for w in warnings)

    def test_clean_inputs_produce_no_errors(self):
        errors, _ = fs.validate_simulation_inputs(make())
        assert errors == []


class TestFrameIntegrity:
    def test_schema_is_complete(self):
        df = fs.build_simulation_dataframe(make(), "expected", 12)
        for col in ("month", "year", "month_of_year", "utilization",
                    "customers", "average_customer_spend", "revenue",
                    "food_cost", "labor_cost", "other_variable_cost", "rent",
                    "other_fixed_cost", "marketing", "total_cost",
                    "operating_profit", "operating_margin",
                    "cumulative_operating_profit",
                    "cumulative_return_after_investment",
                    "investment_recovery_pct", "roi"):
            assert col in df.columns, col
        assert len(df) == 12
        assert not df.drop(columns=["investment_recovery_pct", "roi"]).isna().any().any()

    def test_cumulative_column_is_the_running_sum(self):
        df = fs.build_simulation_dataframe(make(), "expected", 24)
        assert df["cumulative_operating_profit"].iloc[-1] == pytest.approx(
            df["operating_profit"].sum())

    def test_growth_compounds_annually(self):
        df = fs.build_simulation_dataframe(
            make(annual_revenue_growth=0.10), "expected", 24)
        m1, m13 = df.iloc[0], df.iloc[12]
        assert m13["average_customer_spend"] == pytest.approx(
            m1["average_customer_spend"] * 1.10)

    def test_recovery_pct_saturates_at_one(self):
        df = fs.build_simulation_dataframe(
            make(initial_investment=10_000.0), "expected", 60)
        assert df["investment_recovery_pct"].max() == 1.0


class TestSensitivity:
    def test_deltas_are_computed_not_decorative(self):
        rows = fs.calculate_sensitivity(make(), 60)
        assert {r["variable"] for r in rows} == set(fs.SENSITIVITY_VARIABLES)
        spend = next(r for r in rows if r["variable"] == "average_customer_spend")
        assert spend["up"]["roi_delta"] > 0        # more spend, better ROI
        assert spend["down"]["roi_delta"] < 0
        rent = next(r for r in rows if r["variable"] == "monthly_rent")
        assert rent["up"]["roi_delta"] < 0          # more rent, worse ROI
        assert rent["up"]["break_even_delta_months"] >= 0

    def test_sensitivity_respects_input_bounds(self):
        rows = fs.calculate_sensitivity(make(base_utilization=0.95,
                                             maximum_utilization=1.0), 12)
        util = next(r for r in rows if r["variable"] == "base_utilization")
        assert util["up"]["value"] <= 1.0
