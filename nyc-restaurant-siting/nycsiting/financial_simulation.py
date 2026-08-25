"""
Scenario-based financial simulation for a proposed restaurant.

WHAT THIS IS AND IS NOT
Public data contains no future revenue. Everything here is arithmetic over
assumptions the user can see and change — a way to ask "IF customers, spend
and costs looked like this, what would the cash position do?" — never a
forecast. The UI wording is required to carry that framing, and the language
tests hold it there.

THE MODEL, IN FULL (there is deliberately nothing more to it):

    daily_customers   = seats * table_turns_per_day * utilization
    monthly_customers = daily_customers * operating_days_per_week * 52/12
    monthly_revenue   = monthly_customers * average_customer_spend
    variable costs    = revenue * (food% + labor% + other%)
    fixed costs       = rent + other_fixed + marketing
    operating_profit  = revenue - variable - fixed
    cumulative_return = -initial_investment + Σ operating_profit
    break-even        = first month cumulative_return >= 0
    ROI (5y)          = Σ operating_profit / initial_investment

Growth is applied transparently: average spend compounds at
annual_revenue_growth, fixed costs at annual_cost_growth. Variable-cost
percentages stay constant (they are shares of revenue, so they already scale).

Location intelligence is shown BESIDE these numbers as context, and is not
multiplied into them: we have no calibrated relationship between our location
signals and restaurant revenue, and inventing one would smuggle a black box
into an otherwise fully inspectable model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

import numpy as np
import pandas as pd

WEEKS_PER_MONTH = 52 / 12
HORIZON_CHOICES = (12, 36, 60)
DEFAULT_HORIZON = 60

#: Source classification for every input the simulation consumes. "USER" inputs
#: are asked for up front; "MODEL" assumptions ship with editable defaults and
#: must never be presented as observed NYC facts.
INPUT_SPECS: dict[str, dict] = {
    "average_customer_spend": dict(source="USER", default=45.0, min=1.0, max=500.0,
                                   label="Average customer spend ($)"),
    "seats": dict(source="USER", default=50, min=1, max=500, label="Seats"),
    "monthly_rent": dict(source="USER", default=12_000.0, min=0.0, max=500_000.0,
                         label="Monthly rent ($)"),
    "initial_investment": dict(source="USER", default=350_000.0, min=0.0,
                               max=20_000_000.0, label="Initial investment ($)"),
    "operating_days_per_week": dict(source="USER", default=6, min=1, max=7,
                                    label="Operating days per week"),
    "food_cost_pct": dict(source="MODEL", default=0.30, min=0.0, max=0.9,
                          label="Food cost (% of revenue)"),
    "labor_cost_pct": dict(source="MODEL", default=0.32, min=0.0, max=0.9,
                           label="Labour cost (% of revenue)"),
    "other_variable_cost_pct": dict(source="MODEL", default=0.08, min=0.0, max=0.9,
                                    label="Other variable costs (% of revenue)"),
    "other_fixed_monthly_cost": dict(source="MODEL", default=6_000.0, min=0.0,
                                     max=1_000_000.0,
                                     label="Other fixed costs ($/month)"),
    "marketing_monthly": dict(source="MODEL", default=2_000.0, min=0.0,
                              max=1_000_000.0, label="Marketing ($/month)"),
    "table_turns_per_day": dict(source="MODEL", default=1.8, min=0.1, max=10.0,
                                label="Table turns per operating day"),
    "base_utilization": dict(source="MODEL", default=0.65, min=0.0, max=1.0,
                             label="Seat utilisation"),
    "annual_revenue_growth": dict(source="MODEL", default=0.03, min=-0.5, max=0.5,
                                  label="Annual revenue growth"),
    "annual_cost_growth": dict(source="MODEL", default=0.03, min=-0.5, max=0.5,
                               label="Annual fixed-cost growth"),
    "maximum_utilization": dict(source="MODEL", default=0.90, min=0.0, max=1.0,
                                label="Maximum utilisation"),
}

#: Scenario deltas, defined in one place so the UI can print them verbatim.
#: These are judgement calls about how wrong the base assumptions might be,
#: not calibrated quantities — the labels in the UI say so.
SCENARIO_CONFIG: dict[str, dict[str, float]] = {
    "conservative": dict(utilization_multiplier=0.80, table_turns_multiplier=0.90,
                         spend_multiplier=0.95, fixed_cost_multiplier=1.05),
    "expected": dict(utilization_multiplier=1.00, table_turns_multiplier=1.00,
                     spend_multiplier=1.00, fixed_cost_multiplier=1.00),
    "optimistic": dict(utilization_multiplier=1.15, table_turns_multiplier=1.10,
                       spend_multiplier=1.05, fixed_cost_multiplier=1.00),
}

#: Sensitivity: the levers a prospective owner actually negotiates or controls.
SENSITIVITY_VARIABLES = ("average_customer_spend", "monthly_rent",
                         "base_utilization", "food_cost_pct", "labor_cost_pct")


@dataclass(frozen=True)
class SimulationInputs:
    average_customer_spend: float = INPUT_SPECS["average_customer_spend"]["default"]
    seats: int = INPUT_SPECS["seats"]["default"]
    monthly_rent: float = INPUT_SPECS["monthly_rent"]["default"]
    initial_investment: float = INPUT_SPECS["initial_investment"]["default"]
    operating_days_per_week: int = INPUT_SPECS["operating_days_per_week"]["default"]
    food_cost_pct: float = INPUT_SPECS["food_cost_pct"]["default"]
    labor_cost_pct: float = INPUT_SPECS["labor_cost_pct"]["default"]
    other_variable_cost_pct: float = INPUT_SPECS["other_variable_cost_pct"]["default"]
    other_fixed_monthly_cost: float = INPUT_SPECS["other_fixed_monthly_cost"]["default"]
    marketing_monthly: float = INPUT_SPECS["marketing_monthly"]["default"]
    table_turns_per_day: float = INPUT_SPECS["table_turns_per_day"]["default"]
    base_utilization: float = INPUT_SPECS["base_utilization"]["default"]
    annual_revenue_growth: float = INPUT_SPECS["annual_revenue_growth"]["default"]
    annual_cost_growth: float = INPUT_SPECS["annual_cost_growth"]["default"]
    maximum_utilization: float = INPUT_SPECS["maximum_utilization"]["default"]


def validate_simulation_inputs(inputs: SimulationInputs) -> tuple[list[str], list[str]]:
    """
    (errors, warnings). Errors block the run; warnings run but are shown.

    NaN and infinity are errors, not silently coerced — a NaN spend flowing
    through would poison every downstream number while looking like a result.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for f in fields(inputs):
        value = getattr(inputs, f.name)
        spec = INPUT_SPECS[f.name]
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            errors.append(f"{spec['label']}: not a valid number.")
            continue
        if not (spec["min"] <= value <= spec["max"]):
            errors.append(
                f"{spec['label']}: {value} is outside the allowed range "
                f"[{spec['min']}, {spec['max']}].")

    if errors:
        return errors, warnings

    variable_share = (inputs.food_cost_pct + inputs.labor_cost_pct
                      + inputs.other_variable_cost_pct)
    if variable_share >= 1.0:
        warnings.append(
            f"Variable costs sum to {variable_share:.0%} of revenue — every "
            f"dollar of sales loses money before rent. Check the cost "
            f"percentages.")
    elif variable_share >= 0.85:
        warnings.append(
            f"Variable costs sum to {variable_share:.0%} of revenue, leaving "
            f"under 15% to cover rent and fixed costs — unusually thin.")

    if inputs.base_utilization > inputs.maximum_utilization:
        warnings.append(
            "Base utilisation exceeds the maximum; it will be clamped to "
            f"{inputs.maximum_utilization:.0%}.")

    daily = calculate_daily_capacity(inputs)
    if daily > 2_000:
        warnings.append(
            f"These assumptions imply {daily:,.0f} covers per day — check "
            f"seats and table turns.")

    if inputs.initial_investment == 0:
        warnings.append(
            "Initial investment is $0, so ROI and payback cannot be computed.")

    ops = calculate_monthly_operations(inputs)
    if ops["revenue"] <= ops["fixed_cost"]:
        warnings.append(
            f"Expected-scenario monthly revenue (${ops['revenue']:,.0f}) does "
            f"not cover fixed costs alone (${ops['fixed_cost']:,.0f}) — this "
            f"plan loses money in every month it operates.")
    elif ops["operating_profit"] < 0:
        warnings.append(
            f"Expected-scenario operating profit is negative "
            f"(${ops['operating_profit']:,.0f}/month) under these assumptions.")
    return errors, warnings


def calculate_daily_capacity(inputs: SimulationInputs,
                             utilization: float | None = None) -> float:
    """seats × turns × utilisation, with utilisation clamped to [0, max]."""
    u = inputs.base_utilization if utilization is None else utilization
    u = float(np.clip(u, 0.0, inputs.maximum_utilization))
    return inputs.seats * inputs.table_turns_per_day * u


def calculate_monthly_operations(inputs: SimulationInputs,
                                 utilization: float | None = None,
                                 spend: float | None = None,
                                 fixed_multiplier: float = 1.0) -> dict:
    """One month of the P&L, as plain numbers."""
    daily = calculate_daily_capacity(inputs, utilization)
    customers = daily * inputs.operating_days_per_week * WEEKS_PER_MONTH
    revenue = customers * (inputs.average_customer_spend if spend is None else spend)

    food = revenue * inputs.food_cost_pct
    labor = revenue * inputs.labor_cost_pct
    other_var = revenue * inputs.other_variable_cost_pct
    rent = inputs.monthly_rent * fixed_multiplier
    other_fixed = inputs.other_fixed_monthly_cost * fixed_multiplier
    marketing = inputs.marketing_monthly * fixed_multiplier
    fixed = rent + other_fixed + marketing
    total = food + labor + other_var + fixed
    profit = revenue - total
    return dict(customers=customers, revenue=revenue, food_cost=food,
                labor_cost=labor, other_variable_cost=other_var, rent=rent,
                other_fixed_cost=other_fixed, marketing=marketing,
                fixed_cost=fixed, total_cost=total, operating_profit=profit,
                operating_margin=(profit / revenue) if revenue > 0 else 0.0)


def build_simulation_dataframe(inputs: SimulationInputs,
                               scenario: str = "expected",
                               months: int = DEFAULT_HORIZON,
                               daily_covers_override: float | None = None,
                               ramp_months: int = 0,
                               ramp_start_factor: float = 1.0) -> pd.DataFrame:
    """
    The monthly ledger for one scenario. Every displayed number reads off this
    frame — the animation included, so the picture can never disagree with the
    arithmetic.
    """
    cfg = SCENARIO_CONFIG[scenario]
    utilization = float(np.clip(
        inputs.base_utilization * cfg["utilization_multiplier"],
        0.0, inputs.maximum_utilization))
    if daily_covers_override is not None:
        # Footfall-anchored demand: covers come from measured footfall x an
        # assumed capture rate, expressed as the utilisation that produces
        # exactly those covers — still capped by the capacity ceiling, and
        # with the editorial utilisation multiplier deliberately NOT applied
        # (scenario variation comes from observed footfall quantiles instead).
        turns = inputs.table_turns_per_day * cfg["table_turns_multiplier"]
        capacity_daily = inputs.seats * turns
        utilization = float(np.clip(
            daily_covers_override / capacity_daily if capacity_daily > 0 else 0.0,
            0.0, inputs.maximum_utilization))
    scenario_inputs = replace(
        inputs,
        table_turns_per_day=inputs.table_turns_per_day * cfg["table_turns_multiplier"])
    base_spend = inputs.average_customer_spend * cfg["spend_multiplier"]

    rows = []
    cumulative_profit = 0.0
    for month in range(1, months + 1):
        years_elapsed = (month - 1) / 12
        spend = base_spend * (1 + inputs.annual_revenue_growth) ** years_elapsed
        fixed_mult = (cfg["fixed_cost_multiplier"]
                      * (1 + inputs.annual_cost_growth) ** years_elapsed)
        # Optional opening ramp (MODEL ASSUMPTION, off by default): month-1
        # utilisation starts at ramp_start_factor and climbs linearly to full
        # over ramp_months. Nothing is ramped unless the user turns it on.
        month_utilization = utilization
        if ramp_months > 1 and month <= ramp_months:
            factor = (ramp_start_factor
                      + (1.0 - ramp_start_factor) * (month - 1) / (ramp_months - 1))
            month_utilization = utilization * factor
        elif ramp_months == 1 and month == 1:
            month_utilization = utilization * ramp_start_factor
        ops = calculate_monthly_operations(
            scenario_inputs, utilization=month_utilization, spend=spend,
            fixed_multiplier=fixed_mult)
        cumulative_profit += ops["operating_profit"]
        rows.append(dict(
            month=month, year=(month - 1) // 12 + 1,
            month_of_year=(month - 1) % 12 + 1,
            utilization=month_utilization, average_customer_spend=spend,
            **ops,
            cumulative_operating_profit=cumulative_profit,
            cumulative_return_after_investment=(
                cumulative_profit - inputs.initial_investment),
        ))
    df = pd.DataFrame(rows)
    if inputs.initial_investment > 0:
        df["investment_recovery_pct"] = (
            df["cumulative_operating_profit"] / inputs.initial_investment
        ).clip(lower=0.0, upper=1.0)
        df["roi"] = df["cumulative_operating_profit"] / inputs.initial_investment
    else:
        df["investment_recovery_pct"] = float("nan")
        df["roi"] = float("nan")
    return df


def calculate_break_even(df: pd.DataFrame) -> int | None:
    """First month the cumulative position (after investment) reaches zero."""
    hit = df[df["cumulative_return_after_investment"] >= 0]
    return int(hit.iloc[0]["month"]) if len(hit) else None


def calculate_roi(df: pd.DataFrame, inputs: SimulationInputs) -> float | None:
    """Cumulative operating profit over initial investment. None when $0 in."""
    if inputs.initial_investment <= 0:
        return None
    return float(df["cumulative_operating_profit"].iloc[-1]
                 / inputs.initial_investment)


def summarise_scenario(df: pd.DataFrame, inputs: SimulationInputs) -> dict:
    year1 = df[df["year"] == 1]
    total_profit = float(df["cumulative_operating_profit"].iloc[-1])
    return dict(
        year1_revenue=float(year1["revenue"].sum()),
        year1_operating_profit=float(year1["operating_profit"].sum()),
        year1_operating_margin=(
            float(year1["operating_profit"].sum() / year1["revenue"].sum())
            if year1["revenue"].sum() > 0 else 0.0),
        break_even_month=calculate_break_even(df),
        cumulative_operating_profit=total_profit,
        roi=calculate_roi(df, inputs),
        net_return_after_investment=total_profit - inputs.initial_investment,
    )


def calculate_all_scenarios(inputs: SimulationInputs,
                            months: int = DEFAULT_HORIZON) -> dict[str, dict]:
    """{scenario: {'frame': DataFrame, 'summary': dict}} for all three."""
    out = {}
    for name in SCENARIO_CONFIG:
        frame = build_simulation_dataframe(inputs, name, months)
        out[name] = {"frame": frame, "summary": summarise_scenario(frame, inputs)}
    return out


def calculate_sensitivity(inputs: SimulationInputs,
                          months: int = DEFAULT_HORIZON,
                          shock: float = 0.10) -> list[dict]:
    """
    Expected scenario re-run at ±shock on each lever, one at a time.

    Reported as deltas against the unshocked expected case so the user sees
    which assumption their outcome actually hinges on. Computed, never
    hardcoded — an interpretation string with no calculation behind it is
    exactly the kind of decoration this app must not ship.
    """
    base = build_simulation_dataframe(inputs, "expected", months)
    base_summary = summarise_scenario(base, inputs)
    out = []
    for name in SENSITIVITY_VARIABLES:
        row = {"variable": name, "label": INPUT_SPECS[name]["label"]}
        for direction, mult in (("up", 1 + shock), ("down", 1 - shock)):
            spec = INPUT_SPECS[name]
            shocked_value = float(np.clip(
                getattr(inputs, name) * mult, spec["min"], spec["max"]))
            shocked = replace(inputs, **{name: shocked_value})
            summary = summarise_scenario(
                build_simulation_dataframe(shocked, "expected", months), shocked)
            roi_delta = (None if summary["roi"] is None or base_summary["roi"] is None
                         else summary["roi"] - base_summary["roi"])
            be_base, be_new = base_summary["break_even_month"], summary["break_even_month"]
            be_delta = (be_new - be_base) if (be_base is not None and be_new is not None) else None
            row[direction] = dict(
                value=shocked_value, roi=summary["roi"], roi_delta=roi_delta,
                break_even_month=be_new, break_even_delta_months=be_delta,
                year1_operating_profit=summary["year1_operating_profit"])
        out.append(row)
    return out


# ============================================================================
# V2: restaurant-industry financial taxonomy
#
# The v1 columns and functions above are unchanged — they are correct as
# pre-financing project cash economics and the validated tests pin them. V2
# adds the industry vocabulary on top: distinct ROI metrics that must never
# collapse into one number, an amortizing-debt layer, and the startup-cost
# breakdown. Definitions follow RestaurantOwner (ROI on owner investment,
# sales-to-investment, startup uses) and Toast (contribution-margin break-even,
# prime cost); docs/financial_methodology_sources.md summarises the sources.
#
# One correction this version makes deliberate: v1's single "roi" number
# (cumulative operating profit / investment, e.g. 1.44) was a CASH RETURN
# MULTIPLE, and v1's "break-even" (cumulative >= investment) was INVESTMENT
# PAYBACK. Both calculations were right; both names were wrong. V2 names them
# properly and adds the genuinely different metrics (operating break-even from
# contribution margin, annual project/owner ROI) that the old names implied.
# ============================================================================

#: Startup-investment uses (RestaurantOwner's plan structure). The simple UI
#: still asks for one total; Advanced can itemise. No hidden costs: the total
#: is exactly the sum of what the user enters.
STARTUP_COMPONENTS = [
    "leasehold_improvements", "kitchen_equipment", "furniture_fixtures",
    "deposits", "design_fees", "professional_fees", "permits_licensing",
    "preopening_payroll", "opening_inventory", "preopening_marketing",
    "contingency", "working_capital", "other_startup",
]


@dataclass(frozen=True)
class FinancingInputs:
    """Optional debt. Principal is a financing flow, never an operating cost."""
    enabled: bool = False
    loan_principal: float = 0.0
    annual_interest_rate: float = 0.08
    loan_term_months: int = 84


def total_startup_investment(breakdown: dict[str, float] | None,
                             simple_total: float) -> float:
    """The denominator for project ROI and sales-to-investment."""
    if breakdown:
        return float(sum(breakdown.get(k, 0.0) for k in STARTUP_COMPONENTS))
    return float(simple_total)


def owner_equity(investment: float, financing: FinancingInputs | None) -> float:
    """Sources = uses: whatever debt does not fund, the owner does."""
    if financing is None or not financing.enabled:
        return float(investment)
    return float(investment - financing.loan_principal)


def validate_financing(investment: float,
                       financing: FinancingInputs | None) -> list[str]:
    """Errors that block owner-ROI math (never a crash, never silent)."""
    if financing is None or not financing.enabled:
        return []
    problems = []
    if financing.loan_principal < 0:
        problems.append("Loan principal cannot be negative.")
    if not (0 <= financing.annual_interest_rate <= 1):
        problems.append("Annual interest rate must be between 0% and 100%.")
    if financing.loan_term_months < 1:
        problems.append("Loan term must be at least one month.")
    if financing.loan_principal >= investment:
        problems.append(
            f"Debt (${financing.loan_principal:,.0f}) equals or exceeds the "
            f"total startup investment (${investment:,.0f}), leaving no owner "
            f"equity — sources and uses do not reconcile.")
    return problems


def amortization_schedule(principal: float, annual_rate: float,
                          term_months: int) -> pd.DataFrame:
    """
    Standard fixed-payment amortizing loan, exact per month.

    payment = P·r·(1+r)^n / ((1+r)^n − 1), or P/n at 0%. Interest accrues on
    the opening balance; principal is the remainder of the payment; the final
    balance lands at zero within floating tolerance.
    """
    r = annual_rate / 12.0
    n = int(term_months)
    if principal <= 0 or n <= 0:
        return pd.DataFrame(columns=["month", "payment", "interest",
                                     "principal", "balance"])
    payment = (principal / n if r == 0
               else principal * r * (1 + r) ** n / ((1 + r) ** n - 1))
    rows, balance = [], float(principal)
    for month in range(1, n + 1):
        interest = balance * r
        principal_paid = payment - interest
        balance -= principal_paid
        rows.append(dict(month=month, payment=payment, interest=interest,
                         principal=principal_paid, balance=max(balance, 0.0)))
    return pd.DataFrame(rows)


def extend_with_financing(df: pd.DataFrame, inputs: SimulationInputs,
                          financing: FinancingInputs | None) -> pd.DataFrame:
    """
    Add the v2 vocabulary columns to a v1 monthly frame.

    v1's operating_profit IS pre-financing operating cash flow (the model is
    cash-based and pre-tax throughout), so v2 aliases rather than recomputes —
    one source of truth, two vocabularies.
    """
    out = df.copy()
    out["net_sales"] = out["revenue"]
    variable = out["food_cost"] + out["labor_cost"] + out["other_variable_cost"]
    out["contribution_margin"] = out["net_sales"] - variable
    out["prime_cost"] = out["food_cost"] + out["labor_cost"]
    out["prime_cost_pct"] = (out["prime_cost"] / out["net_sales"]).where(
        out["net_sales"] > 0, 0.0)
    out["operating_cash_flow"] = out["operating_profit"]

    if financing is not None and financing.enabled and financing.loan_principal > 0:
        sched = amortization_schedule(
            financing.loan_principal, financing.annual_interest_rate,
            financing.loan_term_months).set_index("month")
        out["loan_interest"] = out["month"].map(sched["interest"]).fillna(0.0)
        out["loan_principal_paid"] = out["month"].map(sched["principal"]).fillna(0.0)
    else:
        out["loan_interest"] = 0.0
        out["loan_principal_paid"] = 0.0
    out["debt_service"] = out["loan_interest"] + out["loan_principal_paid"]
    out["owner_cash_flow"] = out["operating_cash_flow"] - out["debt_service"]
    out["cumulative_owner_cash_flow"] = out["owner_cash_flow"].cumsum()

    equity = owner_equity(inputs.initial_investment, financing)
    out["investment_remaining"] = (equity - out["cumulative_owner_cash_flow"]).clip(lower=0.0)
    return out


def calculate_payback_month(df: pd.DataFrame, equity: float) -> float | None:
    """
    INVESTMENT PAYBACK: when cumulative owner cash flow has recovered the
    owner's investment — interpolated inside the crossing month. Distinct
    from operating break-even, which asks about a single month's P&L.
    """
    if equity <= 0:
        return None
    cum = df["cumulative_owner_cash_flow"]
    crossed = df[cum >= equity]
    if crossed.empty:
        return None
    month = int(crossed.iloc[0]["month"])
    if month == 1:
        prior = 0.0
    else:
        prior = float(cum[df["month"] == month - 1].iloc[0])
    flow = float(df[df["month"] == month]["owner_cash_flow"].iloc[0])
    if flow <= 0:
        return float(month)
    return round(month - 1 + (equity - prior) / flow, 1)


def operating_break_even(inputs: SimulationInputs) -> dict:
    """
    OPERATING BREAK-EVEN (Toast): monthly sales where contribution covers
    fixed cash costs. contribution_margin_ratio = 1 − variable share;
    break_even_sales = fixed / ratio; covers = sales / average net check.
    """
    variable_share = (inputs.food_cost_pct + inputs.labor_cost_pct
                      + inputs.other_variable_cost_pct)
    ratio = 1.0 - variable_share
    fixed = (inputs.monthly_rent + inputs.other_fixed_monthly_cost
             + inputs.marketing_monthly)
    if ratio <= 0:
        return dict(contribution_margin_ratio=ratio, break_even_sales=None,
                    break_even_covers=None, fixed_costs=fixed,
                    reachable=False)
    sales = fixed / ratio
    return dict(
        contribution_margin_ratio=ratio, fixed_costs=fixed,
        break_even_sales=sales,
        break_even_covers=(sales / inputs.average_customer_spend
                           if inputs.average_customer_spend > 0 else None),
        reachable=True)


def roi_summary(df: pd.DataFrame, inputs: SimulationInputs,
                financing: FinancingInputs | None = None) -> dict:
    """
    The full v2 metric taxonomy for one scenario frame. Nothing here is ever
    collapsed into a single "ROI" — each metric keeps its own name, and the
    denominators differ on purpose (project ROI over total investment,
    owner metrics over owner equity).
    """
    investment = float(inputs.initial_investment)
    equity = owner_equity(investment, financing)
    year1 = df[df["year"] == 1]

    y1_sales = float(year1["net_sales"].sum())
    y1_ocf = float(year1["operating_cash_flow"].sum())
    y1_owner = float(year1["owner_cash_flow"].sum())
    cum_owner = float(df["cumulative_owner_cash_flow"].iloc[-1])

    annual = []
    for year, grp in df.groupby("year"):
        annual.append(dict(
            year=int(year),
            project_roi_pct=(100 * grp["operating_cash_flow"].sum() / investment
                             if investment > 0 else None),
            owner_roi_pct=(100 * grp["owner_cash_flow"].sum() / equity
                           if equity > 0 else None),
            cumulative_owner_roi_pct=(
                100 * df[df["year"] <= year]["owner_cash_flow"].sum() / equity
                if equity > 0 else None),
            cash_return_multiple=(
                df[df["year"] <= year]["owner_cash_flow"].sum() / equity
                if equity > 0 else None),
        ))

    return dict(
        year1_net_sales=y1_sales,
        year1_operating_cash_flow=y1_ocf,
        operating_cash_margin=(y1_ocf / y1_sales) if y1_sales > 0 else 0.0,
        prime_cost_pct=(float(year1["prime_cost"].sum()) / y1_sales
                        if y1_sales > 0 else 0.0),
        project_roi_year1_pct=(100 * y1_ocf / investment
                               if investment > 0 else None),
        owner_roi_year1_pct=(100 * y1_owner / equity if equity > 0 else None),
        cumulative_owner_roi_pct=(100 * cum_owner / equity
                                  if equity > 0 else None),
        cash_return_multiple=(cum_owner / equity if equity > 0 else None),
        payback_month=calculate_payback_month(df, equity),
        sales_to_investment=(y1_sales / investment if investment > 0 else None),
        owner_equity=equity,
        annual=annual,
        break_even=operating_break_even(inputs),
    )


#: Footfall-anchored scenarios draw traffic from OBSERVED quantiles of the
#: measured service period — never from editorial traffic multipliers.
FOOTFALL_QUANTILES = {"conservative": "p25", "expected": "median",
                      "optimistic": "p75"}


def footfall_scenario_covers(inputs: SimulationInputs, service_stats: dict,
                             capture_rate: float) -> dict[str, dict]:
    """
    Per-scenario daily covers for footfall-anchored mode.

    covers = measured service-period footfall (scenario quantile) x the
    ASSUMED capture rate, capped at capacity (seats x turns x max
    utilisation). The capture rate is one explicit assumption applied to all
    scenarios; what varies between scenarios is only the observed footfall.
    """
    capacity = inputs.seats * inputs.table_turns_per_day * inputs.maximum_utilization
    out = {}
    for scenario, quantile in FOOTFALL_QUANTILES.items():
        footfall = float(service_stats.get(quantile) or 0.0)
        covers = min(footfall * capture_rate, capacity)
        out[scenario] = dict(quantile=quantile, footfall=footfall,
                             capture_rate=capture_rate,
                             capacity_daily=capacity, covers_daily=covers)
    return out


def calculate_all_scenarios_v2(inputs: SimulationInputs,
                               months: int = DEFAULT_HORIZON,
                               financing: FinancingInputs | None = None,
                               footfall_covers_by_scenario: dict | None = None,
                               ramp_months: int = 0,
                               ramp_start_factor: float = 1.0
                               ) -> dict[str, dict]:
    """V1 scenarios plus the v2 vocabulary columns and metric taxonomy."""
    out = {}
    for name in SCENARIO_CONFIG:
        override = None
        if footfall_covers_by_scenario is not None:
            override = footfall_covers_by_scenario[name]["covers_daily"]
        frame = extend_with_financing(
            build_simulation_dataframe(inputs, name, months,
                                       daily_covers_override=override,
                                       ramp_months=ramp_months,
                                       ramp_start_factor=ramp_start_factor),
            inputs, financing)
        out[name] = {"frame": frame,
                     "summary": summarise_scenario(frame, inputs),
                     "v2": roi_summary(frame, inputs, financing)}
        if footfall_covers_by_scenario is not None:
            out[name]["footfall"] = footfall_covers_by_scenario[name]
    return out
