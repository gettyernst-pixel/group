"""
The location as the unit of analysis.

CAMIS identifies a BUSINESS; location_key identifies a STOREFRONT. When four
different CAMIS values share one location_key, four restaurants have occupied
that address over the years — and that sequence is what the app exists to show.

The survival measure here is deliberately binary. Both DOHMH extracts window
each restaurant to three years (see panel.py), so a closed restaurant's
observable duration is capped at 3 while a survivor's, spanning both files,
reaches 13. Comparing those durations would find that closing early causes
short durations. Asking instead 'of the restaurants trading in 2011-2017, which
were still on the books in 2026' is not distorted by where the windows fall.
"""
from __future__ import annotations

import pandas as pd

#: Above this many restaurants trading simultaneously, an address is a food
#: hall, stadium or airport terminal rather than a storefront.
MULTI_VENDOR_THRESHOLD = 3


def _max_concurrent(first: pd.Series, last: pd.Series) -> int:
    """
    Peak number of restaurants trading at one address at the same moment.

    This is what separates a storefront that has churned through four tenants
    from Bryant Park's Winter Village, where eighty food stalls share a single
    street address. Both look identical if you only count distinct CAMIS
    values, and the second kind would otherwise dominate every "high turnover"
    ranking the app produces. Measured on the real data, of the addresses with
    four or more restaurants on record, 249 are multi-vendor venues and only
    225 are genuinely sequential — so this is the majority case, not an edge
    case.

    Implemented as a sweep over interval endpoints. Ties open before they
    close, so two tenants that touch on the same day count as concurrent.
    """
    events: list[tuple] = []
    for start, end in zip(first.values, last.values):
        if pd.isna(start) or pd.isna(end):
            continue
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return 0
    events.sort(key=lambda e: (e[0], -e[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def build_locations(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per storefront."""
    placed = panel[panel["location_key"] != ""].copy()
    cohort = placed["seen_2017"]

    g = placed.groupby("location_key")
    out = pd.DataFrame({
        "address": g["address"].last(),
        "lat": g["lat"].median(),
        "lon": g["lon"].median(),
        "bbl": g["bbl"].last(),
        "nta": g["nta"].last(),
        "census_tract": g["census_tract"].last(),
        "restaurants_ever": g["camis"].nunique(),
        "restaurants_active": g["seen_2026"].sum(),
        "restaurants_closed": (placed["status"] == "closed").groupby(
            placed["location_key"]).sum(),
        "cohort_n": cohort.groupby(placed["location_key"]).sum(),
        "cohort_survived": (cohort & placed["seen_2026"]).groupby(
            placed["location_key"]).sum(),
        "first_observed": g["first_observed"].min(),
        "last_observed": g["last_observed"].max(),
    })
    out["cuisines_ever"] = (
        placed[placed["cuisine"] != ""]
        .groupby("location_key")["cuisine"].apply(lambda s: sorted(set(s)))
    )
    out["cuisines_ever"] = out["cuisines_ever"].apply(
        lambda v: v if isinstance(v, list) else [])
    out["cohort_survival_rate"] = (
        out["cohort_survived"] / out["cohort_n"].replace(0, pd.NA))

    concurrent = placed.groupby("location_key").apply(
        lambda g: _max_concurrent(g["first_observed"], g["last_observed"]))
    out["max_concurrent"] = concurrent.reindex(out.index).fillna(0).astype(int)

    # A peak of 0 means every restaurant here lacked usable dates, so
    # concurrency is UNKNOWN rather than low. Treating unknown as sequential
    # would let Macy's (39 food businesses at 1 Herald Square, none datable)
    # pass as a churning storefront. When we cannot measure it, we decline to
    # claim turnover.
    out["concurrency_known"] = out["max_concurrent"] > 0
    out["is_multi_vendor"] = (
        (out["max_concurrent"] > MULTI_VENDOR_THRESHOLD)
        | (~out["concurrency_known"] & (out["restaurants_ever"] > MULTI_VENDOR_THRESHOLD))
    )

    # Turnover only means anything where tenants followed one another. At a
    # food hall a high count means the hall is busy, not that the site is hard.
    out["sequential_tenants"] = out["restaurants_ever"].where(
        ~out["is_multi_vendor"])
    return out.reset_index()


def occupancy_history(panel: pd.DataFrame, location_key: str) -> pd.DataFrame:
    """
    Every restaurant known to have occupied one storefront, oldest first.

    This is the 'what was here before' table — the concrete evidence behind any
    claim the app makes about a specific address.
    """
    at = panel[panel["location_key"] == location_key].copy()
    at = at.sort_values("first_observed", na_position="last")
    cols = ["camis", "name", "cuisine", "first_observed", "last_observed",
            "observed_years", "status", "seen_2017", "seen_2021", "seen_2026",
            "left_censored", "right_censored", "closed_after", "closed_before"]
    return at[[c for c in cols if c in at.columns]]
