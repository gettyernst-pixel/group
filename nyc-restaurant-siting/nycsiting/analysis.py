"""
The question the app exists to answer, expressed as a query over the panel.

Everything returned here is evidence, deliberately structured so the UI can
show the numbers AND what they rest on. Nothing is rounded into a verdict until
scoring.py, and even there the components stay visible.
"""
from __future__ import annotations

import pandas as pd

from . import config, cuisines
from .geo import within_radius
from .stats import wilson_interval, rate_differs


def cohort_survival(df: pd.DataFrame) -> tuple[int, int]:
    """
    (survived, total) for the 2011-2017 cohort inside `df`.

    The cohort is restaurants the 2017 archive recorded; the outcome is whether
    DOHMH still listed them in 2026. This is the app's only sound survival
    measure — see locations.py for why observed durations cannot be compared.
    """
    cohort = df[df["seen_2017"]]
    return int(cohort["seen_2026"].sum()), int(len(cohort))


def _rate(survived: int, total: int) -> float | None:
    return survived / total if total else None


def site_report(
    panel: pd.DataFrame,
    locations: pd.DataFrame,
    lat: float,
    lon: float,
    cuisine: str,
    radius_m: float = config.DEFAULT_RADIUS_M,
    location_key: str | None = None,
) -> dict:
    """Assemble every piece of evidence for one address / cuisine pair."""
    compset = cuisines.competitive_set(cuisine)

    # --- the surrounding market -------------------------------------------
    placed = panel[panel["lat"].notna()]
    area = within_radius(placed, lat, lon, radius_m)
    area_same = area[area["cuisine"] == cuisine]
    area_comp = area[area["cuisine"].isin(compset)]

    city_surv, city_tot = cohort_survival(panel)
    area_surv, area_tot = cohort_survival(area)
    same_surv, same_tot = cohort_survival(area_same)

    # Same-cuisine survival citywide, so a neighbourhood figure can be read
    # against how the concept does everywhere, not only against other cuisines.
    city_same = panel[panel["cuisine"] == cuisine]
    city_same_surv, city_same_tot = cohort_survival(city_same)

    # --- the exact storefront ---------------------------------------------
    at_loc = pd.DataFrame()
    loc_row = None
    if location_key:
        at_loc = panel[panel["location_key"] == location_key].copy()
        match = locations[locations["location_key"] == location_key]
        if len(match):
            loc_row = match.iloc[0].to_dict()

    loc_surv, loc_tot = cohort_survival(at_loc) if len(at_loc) else (0, 0)
    at_loc_same = at_loc[at_loc["cuisine"] == cuisine] if len(at_loc) else pd.DataFrame()

    # --- active competition -----------------------------------------------
    active_comp = area_comp[area_comp["seen_2026"]]
    active_same = area_same[area_same["seen_2026"]]
    active_all = area[area["seen_2026"]]

    return {
        "query": {"lat": lat, "lon": lon, "cuisine": cuisine,
                  "radius_m": radius_m, "location_key": location_key},
        "location": {
            "row": loc_row,
            "occupancy": at_loc,
            "restaurants_ever": int(at_loc["camis"].nunique()) if len(at_loc) else 0,
            "closed_here": int((at_loc["status"] == "closed").sum()) if len(at_loc) else 0,
            "cuisines_here": sorted(set(at_loc["cuisine"]) - {""}) if len(at_loc) else [],
            "same_cuisine_here": at_loc_same,
            "cohort": {"survived": loc_surv, "total": loc_tot,
                       "rate": _rate(loc_surv, loc_tot)},
            "is_multi_vendor": bool(loc_row["is_multi_vendor"]) if loc_row else False,
        },
        "area": {
            "all": area,
            "competitors": area_comp,
            "same_cuisine": area_same,
            "active_all": int(len(active_all)),
            "active_competitors": int(len(active_comp)),
            "active_same_cuisine": int(len(active_same)),
            "competitive_set": sorted(compset),
            "cohort": {"survived": area_surv, "total": area_tot,
                       "rate": _rate(area_surv, area_tot),
                       "ci": wilson_interval(area_surv, area_tot)},
            "cohort_same_cuisine": {
                "survived": same_surv, "total": same_tot,
                "rate": _rate(same_surv, same_tot),
                "ci": wilson_interval(same_surv, same_tot)},
        },
        "city": {
            "cohort": {"survived": city_surv, "total": city_tot,
                       "rate": _rate(city_surv, city_tot)},
            "cohort_same_cuisine": {
                "survived": city_same_surv, "total": city_same_tot,
                "rate": _rate(city_same_surv, city_same_tot)},
        },
        "comparisons": _comparisons(
            loc_surv, loc_tot, area_surv, area_tot,
            same_surv, same_tot, city_same_surv, city_same_tot, city_surv, city_tot),
    }


def _comparisons(loc_s, loc_t, area_s, area_t, same_s, same_t,
                 citysame_s, citysame_t, city_s, city_t) -> list[dict]:
    """
    The three comparisons that actually answer the user's question.

    Each carries its own sample size and a verdict that refuses to call a
    difference when the counts cannot support one. A single address typically
    holds one to five restaurants ever, which is almost never enough — saying
    so is more useful than quoting a percentage computed from three data points.
    """
    out = []
    area_rate = _rate(area_s, area_t)
    city_same_rate = _rate(citysame_s, citysame_t)
    city_rate = _rate(city_s, city_t)

    if loc_t and area_rate is not None:
        out.append({
            "key": "location_vs_area",
            "question": "Do restaurants at this exact address survive as often as those nearby?",
            "subject_rate": _rate(loc_s, loc_t), "subject_n": loc_t,
            "baseline_rate": area_rate, "baseline_n": area_t,
            "verdict": rate_differs(loc_s, loc_t, area_rate),
        })
    if same_t and area_rate is not None:
        out.append({
            "key": "cuisine_vs_area",
            "question": "Does this cuisine survive as often here as other cuisines nearby?",
            "subject_rate": _rate(same_s, same_t), "subject_n": same_t,
            "baseline_rate": area_rate, "baseline_n": area_t,
            "verdict": rate_differs(same_s, same_t, area_rate),
        })
    if same_t and city_same_rate is not None:
        out.append({
            "key": "cuisine_vs_city",
            "question": "Does this cuisine survive as often here as it does citywide?",
            "subject_rate": _rate(same_s, same_t), "subject_n": same_t,
            "baseline_rate": city_same_rate, "baseline_n": citysame_t,
            "verdict": rate_differs(same_s, same_t, city_same_rate),
        })
    if area_t and city_rate is not None:
        out.append({
            "key": "area_vs_city",
            "question": "Does this neighbourhood retain restaurants as well as the city does?",
            "subject_rate": area_rate, "subject_n": area_t,
            "baseline_rate": city_rate, "baseline_n": city_t,
            "verdict": rate_differs(area_s, area_t, city_rate),
        })
    return out
