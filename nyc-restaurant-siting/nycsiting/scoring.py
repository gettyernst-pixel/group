"""
Turning evidence into one number, without hiding how.

Design rules, in order of importance:

1. Every component is computed from something the user can see elsewhere in the
   report. No component depends on a number that appears nowhere else.
2. A component that cannot be computed is DROPPED and named, not defaulted to
   a middle value. Silently scoring an unmeasured thing as 50 would let missing
   data masquerade as a finding.
3. Weights are declared here, in the open, and shown in the UI next to each
   component's contribution.
4. The result is called a screening score. It ranks locations by how much of
   the observable evidence points at risk. It is not a probability of failure,
   and nothing in the data would support calling it one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .geo import within_radius
from .stats import rate_differs

#: component -> weight. Relative, renormalised over whatever is available.
WEIGHTS = {
    "location_history": 25,
    "cuisine_track_record": 25,
    "competition": 20,
    "area_retention": 15,
    "foot_traffic": 10,
    "property_fit": 5,
}

BANDS = [(0, 35, "Lower risk"), (35, 65, "Moderate risk"), (65, 101, "Higher risk")]

#: Fewer restaurants than this ever recorded at an address and turnover there
#: is an anecdote, not a pattern.
MIN_LOT_HISTORY = 3


class Component(dict):
    """One scored line of evidence. A dict so Streamlit can render it directly."""

    def __init__(self, key, label, score, weight, evidence, detail="", available=True):
        super().__init__(key=key, label=label, score=score, weight=weight,
                         evidence=evidence, detail=detail, available=available)


def _unavailable(key, label, reason):
    return Component(key, label, None, WEIGHTS[key], reason, available=False)


#: Memo for competitor_reference. Bounded, and each entry remembers the exact
#: panel object it was built from.
_REFERENCE_CACHE: dict = {}
_REFERENCE_CACHE_MAX = 64


def competitor_reference(panel: pd.DataFrame, cuisine_set: set[str],
                         radius_m: float, sample: int = 1200,
                         seed: int = 0) -> np.ndarray:
    """
    How many competitors a typical member of this concept already sits among.

    Ranking a location against ALL of NYC would be meaningless — most of the
    city is residential and every commercial strip would look saturated. The
    reference is instead the places where this concept actually trades. Sampled
    rather than exhaustive because the full comparison is quadratic and this
    runs inside a web app; 1200 points is ample for a percentile.

    MEMOISED, because this distribution describes the CONCEPT, not the site
    being scored: it depends only on the panel, the competitive set and the
    radius, and with a fixed seed it is deterministic. Measured before the
    memo, it ran 1200 radius queries taking ~1.1s on EVERY score — so a
    radius change cost 1.4s, and comparing three addresses of the same
    cuisine paid for the identical array three times.

    The entry stores the panel it was built from and is only reused when that
    is the very same object (`is`), so a rebuilt or reloaded panel can never
    be served a reference distribution computed from the previous one. The
    numbers returned are unchanged — this is the same computation, done once.
    """
    key = (frozenset(cuisine_set), float(radius_m), int(sample), int(seed))
    cached = _REFERENCE_CACHE.get(key)
    if cached is not None and cached[0] is panel:
        return cached[1]

    pool = panel[panel["seen_2026"] & panel["cuisine"].isin(cuisine_set)
                 & panel["lat"].notna()]
    if pool.empty:
        result = np.array([])
    else:
        ref = pool.sample(min(sample, len(pool)), random_state=seed)
        result = np.array([
            len(within_radius(pool, r.lat, r.lon, radius_m)) - 1
            for r in ref.itertuples()
        ])

    if len(_REFERENCE_CACHE) >= _REFERENCE_CACHE_MAX:
        # plain FIFO: every entry costs the same to rebuild, so there is no
        # value in tracking recency, and an unbounded dict would grow with
        # every cuisine x radius the session touches.
        _REFERENCE_CACHE.pop(next(iter(_REFERENCE_CACHE)))
    _REFERENCE_CACHE[key] = (panel, result)
    return result


def score_site(report: dict, panel: pd.DataFrame, lot: dict | None,
               pedestrian: dict | None, radius_m: float) -> dict:
    """Build every component, then combine what is available."""
    comps: list[Component] = []
    loc = report["location"]
    area = report["area"]
    city = report["city"]

    # --- 1. turnover at the exact storefront -------------------------------
    if loc["is_multi_vendor"]:
        comps.append(_unavailable(
            "location_history", "History at this address",
            "This address is a food hall, market or terminal with many "
            "restaurants trading at once, so tenant turnover here does not "
            "mean what it means at a storefront."))
    elif loc["restaurants_ever"] == 0:
        comps.append(_unavailable(
            "location_history", "History at this address",
            "No food business is on record at this exact address in either "
            "era of the public inspection record."))
    elif loc["restaurants_ever"] < MIN_LOT_HISTORY:
        # One or two data points cannot establish a turnover pattern. Scoring
        # them anyway is how "the one restaurant here closed" becomes "this
        # address kills restaurants" — the exact overreach this tool must not
        # commit. Say what was seen; decline to score it.
        ever, closed = loc["restaurants_ever"], loc["closed_here"]
        comps.append(_unavailable(
            "location_history", "History at this address",
            f"Only {ever} restaurant{'s' if ever != 1 else ''} on record at "
            f"this exact address ({closed} no longer listed) — too little "
            f"history to read a turnover pattern from."))
    else:
        ever, closed = loc["restaurants_ever"], loc["closed_here"]
        # One tenant that is still trading is the strongest signal available;
        # each additional departed tenant raises risk, saturating at four.
        risk = min(100, 100 * closed / max(ever, 1)) if ever else 50
        if ever >= 3 and closed >= 2:
            risk = min(100, risk + 15)
        comps.append(Component(
            "location_history", "History at this address", risk,
            WEIGHTS["location_history"],
            f"{ever} food business{'es' if ever != 1 else ''} on record here; "
            f"{closed} no longer appear in the 2026 data.",
            detail="Absence from the 2026 extract is inferred closure — DOHMH "
                   "publishes no closing dates."))

    # --- 2. how the concept has fared in this market -----------------------
    same = area["cohort_same_cuisine"]
    city_same = city["cohort_same_cuisine"]
    if not same["total"] or city_same["rate"] is None:
        # The plan may name no cuisine at all; the component is unavailable
        # either way, but the sentence has to read correctly.
        named = report["query"]["cuisine"]
        subject = f"No {named} restaurant" if named else "No comparable "\
                                                         "restaurant"
        comps.append(_unavailable(
            "cuisine_track_record", "Track record for this cuisine nearby",
            f"{subject} near here appears in the 2011-2017 archive, so "
            f"there is no local cohort to follow."))
    else:
        # Ask the same question the comparison cards ask, with the same test:
        # is the local rate DISTINGUISHABLE from the citywide rate at this
        # sample size? Scoring the raw gap regardless let two restaurants
        # produce a full-confidence "Strong" while the card next to it said
        # "not distinguishable" — the two layers of one report disagreeing.
        verdict = rate_differs(same["survived"], same["total"], city_same["rate"])
        base = (f"{same['survived']} of {same['total']} "
                f"{report['query']['cuisine']} restaurants trading near here "
                f"in 2011-2017 were still listed in 2026 "
                f"({100*same['rate']:.0f}%), against "
                f"{100*city_same['rate']:.0f}% for the same cuisine citywide.")
        if verdict == "inconclusive":
            comps.append(Component(
                "cuisine_track_record", "Track record for this cuisine nearby",
                50.0, WEIGHTS["cuisine_track_record"],
                base + " The gap is within sampling noise for "
                       f"{same['total']} restaurants, so this component is "
                       "scored as neutral.",
                detail="A difference is only scored when it exceeds the "
                       "margin of error for the local sample."))
        else:
            gap = same["rate"] - city_same["rate"]
            # +-20 points of survival difference spans the plausible range.
            risk = float(np.clip(50 - gap * 250, 0, 100))
            comps.append(Component(
                "cuisine_track_record", "Track record for this cuisine nearby",
                risk, WEIGHTS["cuisine_track_record"],
                base + f" The gap exceeds the margin of error (n={same['total']}).",
                detail="Scored because the local rate is statistically "
                       "distinguishable from the citywide rate."))

    # --- 3. current competition --------------------------------------------
    ref = competitor_reference(panel, set(area["competitive_set"]), radius_m)
    if ref.size == 0:
        comps.append(_unavailable("competition", "Competition nearby",
                                  "No comparable restaurants citywide to rank against."))
    else:
        here = area["active_competitors"]
        pct = float((ref < here).mean() * 100)
        comps.append(Component(
            "competition", "Competition nearby", pct, WEIGHTS["competition"],
            f"{here} competing restaurants trading within {radius_m:.0f}m — more "
            f"than {pct:.0f}% of the places where this concept operates.",
            detail="Clustering cuts both ways: a dense restaurant row can mean "
                   "the market is full, or that it is a proven destination. "
                   "This number cannot tell the two apart."))

    # --- 4. does the neighbourhood keep its restaurants? -------------------
    if not area["cohort"]["total"]:
        comps.append(_unavailable("area_retention", "Neighbourhood retention",
                                  "No 2011-2017 restaurants near here to follow."))
    else:
        gap = area["cohort"]["rate"] - city["cohort"]["rate"]
        risk = float(np.clip(50 - gap * 250, 0, 100))
        comps.append(Component(
            "area_retention", "Neighbourhood retention", risk,
            WEIGHTS["area_retention"],
            f"{100*area['cohort']['rate']:.0f}% of the {area['cohort']['total']} "
            f"restaurants trading near here in 2011-2017 were still listed in "
            f"2026, against {100*city['cohort']['rate']:.0f}% citywide.",
            detail="Covers all cuisines, so it describes the block rather than "
                   "the concept."))

    # --- 5. foot traffic ----------------------------------------------------
    if not pedestrian:
        comps.append(_unavailable("foot_traffic", "Pedestrian activity",
                                  "No pedestrian counting site could be matched."))
    elif not pedestrian["represents_this_block"]:
        comps.append(_unavailable(
            "foot_traffic", "Pedestrian activity",
            f"The nearest of NYC's 114 counting sites is "
            f"{pedestrian['distance_m']:.0f}m away on {pedestrian['street']} — "
            f"too far to describe this address."))
    else:
        # Counts run from a few hundred to ~90k; log scale reflects how they
        # are actually distributed.
        c = max(pedestrian["count"], 1)
        risk = float(np.clip(100 - (np.log10(c) - 2.5) / 1.7 * 100, 0, 100))
        comps.append(Component(
            "foot_traffic", "Pedestrian activity", risk, WEIGHTS["foot_traffic"],
            f"{c:,} pedestrians counted on {pedestrian['street']} "
            f"({pedestrian['distance_m']:.0f}m away), {pedestrian['period']}.",
            detail="NYC counts only 114 sites citywide; this is the nearest "
                   "available indicator, not this doorway's footfall."))

    # --- 6. the building ----------------------------------------------------
    if not lot or not lot.get("land_use"):
        comps.append(_unavailable("property_fit", "Property characteristics",
                                  "No PLUTO record matched this tax lot."))
    else:
        good = {"Commercial & office", "Mixed residential & commercial",
                "Multi-family elevator", "Multi-family walk-up"}
        risk = 30.0 if lot["land_use"] in good else 65.0
        comps.append(Component(
            "property_fit", "Property characteristics", risk,
            WEIGHTS["property_fit"],
            f"{lot['land_use']}"
            + (f", built {lot['year_built']}" if lot.get("year_built") else "")
            + (f", {lot['num_floors']:.0f} floors" if lot.get("num_floors") else "")
            + (f", zoning {lot['zoning']}" if lot.get("zoning") else ""),
            detail="Land use indicates whether the surrounding building stock "
                   "supports ground-floor food service."))

    return combine(comps)


def combine(components: list[Component]) -> dict:
    """
    Weighted mean over available components, with the missing ones named.

    Weights are renormalised across what could be measured, so a location
    missing pedestrian data is not penalised for it — but the report says which
    components were dropped, because a score resting on three of six signals
    deserves less confidence than one resting on all six.
    """
    usable = [c for c in components if c["available"] and c["score"] is not None]
    dropped = [c for c in components if not c["available"]]

    if not usable:
        return {"score": None, "band": "Not enough data", "components": components,
                "dropped": dropped, "coverage": 0.0,
                "headline": "Not enough observable evidence to screen this location."}

    total_w = sum(c["weight"] for c in usable)
    score = sum(c["score"] * c["weight"] for c in usable) / total_w
    coverage = total_w / sum(WEIGHTS.values())
    band = next(name for lo, hi, name in BANDS if lo <= score < hi)

    for c in usable:
        c["contribution"] = c["score"] * c["weight"] / total_w

    return {
        "score": round(score),
        "band": band,
        "components": components,
        "dropped": dropped,
        "coverage": coverage,
        "headline": _headline(score, band, usable, dropped),
    }


def _headline(score: float, band: str, usable: list, dropped: list) -> str:
    worst = max(usable, key=lambda c: c["score"])
    best = min(usable, key=lambda c: c["score"])
    text = (f"Screening score {score:.0f}/100 — {band.lower()}. "
            f"The strongest signal against this site is "
            f"{worst['label'].lower()}; the strongest in its favour is "
            f"{best['label'].lower()}.")
    if dropped:
        text += (f" {len(dropped)} of {len(usable) + len(dropped)} components "
                 f"could not be measured here and were excluded.")
    return text
