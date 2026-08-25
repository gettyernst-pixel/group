"""
Area-level intelligence: the approved derived functions, from existing data only.

Every number here traces to the panel (DOHMH, CAMIS-deduplicated), the 2020
NTA spatial join, the ACS tract table, and — for site-level saturation — the
already-fetched Google landscape. Nothing external, nothing modelled from
outside benchmarks, and none of it is a probability of success.

THE MEASUREMENT SPINE
Both DOHMH extracts window each restaurant to three years, so observed
durations are not comparable between closed and surviving restaurants (the
audit's central finding). Every area metric therefore stands on the one
sound longitudinal measure: of restaurants trading in 2011-2017, which were
still listed in 2026. "Persistence" here means that survival share — never a
median lifespan.

Bands are Wilson-gated: an area is only called above or below the citywide
baseline when its sample can support the claim; small samples say LIMITED
EVIDENCE, which is information about the evidence, never about the location.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import cuisines
from .stats import rate_differs, wilson_interval

#: Below this many cohort restaurants, an area's rate is an anecdote.
MIN_AREA_SAMPLE = 20
#: Below this, a cuisine-within-area rate is an anecdote.
MIN_CUISINE_SAMPLE = 8
#: Concept ranking only considers cuisines with at least this many active
#: NYC restaurants — rarities produce noise, not fit.
MIN_CITYWIDE_CUISINE = 150

FIT_BANDS = ("Strong", "Promising", "Mixed", "Limited evidence")
SATURATION_BANDS = ("Low", "Moderate", "High")
GAP_BANDS = ("High", "Moderate", "Low", "Insufficient evidence")
TURNOVER_BANDS = ("Lower observed turnover", "Typical",
                  "Higher observed turnover", "Limited evidence")
EVIDENCE_BANDS = ("High", "Moderate", "Limited")


# ------------------------------------------------------------------ features
def area_features(panel: pd.DataFrame, assignment: pd.Series) -> pd.DataFrame:
    """
    One row per 2020 NTA: active inventory, the 2011-17 cohort and its
    survivors, and gone-since counts. CAMIS-deduplicated by construction —
    the panel is one row per establishment.
    """
    df = panel.merge(assignment.rename("nta_2020"), left_on="camis",
                     right_index=True, how="inner")
    df = df[df["nta_2020"].notna()]
    grouped = df.groupby("nta_2020")
    out = pd.DataFrame({
        "restaurants_active": grouped["seen_2026"].sum(),
        "restaurants_ever": grouped.size(),
        "cohort_n": grouped["seen_2017"].sum(),
        "cohort_survived": df[df["seen_2017"] & df["seen_2026"]]
            .groupby("nta_2020").size(),
        "gone": df[df["status"] == "closed"].groupby("nta_2020").size(),
    }).fillna(0).astype(int)
    out["persistence_rate"] = np.where(
        out["cohort_n"] > 0, out["cohort_survived"] / out["cohort_n"].replace(0, 1),
        np.nan)
    return out


def cuisine_area_table(panel: pd.DataFrame, assignment: pd.Series,
                       cuisine_set: set[str]) -> pd.DataFrame:
    """Per-NTA counts and cohort survival for one competitive set."""
    df = panel.merge(assignment.rename("nta_2020"), left_on="camis",
                     right_index=True, how="inner")
    df = df[df["nta_2020"].notna() & df["cuisine"].isin(cuisine_set)]
    grouped = df.groupby("nta_2020")
    out = pd.DataFrame({
        "active": grouped["seen_2026"].sum(),
        "cohort_n": grouped["seen_2017"].sum(),
        "cohort_survived": df[df["seen_2017"] & df["seen_2026"]]
            .groupby("nta_2020").size(),
    }).fillna(0).astype(int)
    return out


def _city_baseline(panel: pd.DataFrame, cuisine_set: set[str]) -> tuple[float, int]:
    sub = panel[panel["cuisine"].isin(cuisine_set)]
    cohort = sub[sub["seen_2017"]]
    if not len(cohort):
        return float("nan"), 0
    return float(cohort["seen_2026"].mean()), int(len(cohort))


# ------------------------------------------------------------------ fit
def area_concept_fit(panel: pd.DataFrame, assignment: pd.Series,
                     cuisine: str) -> pd.DataFrame:
    """
    Per-NTA concept fit: the competitive set's 2011-17 -> 2026 survival,
    compared with the same set citywide, Wilson-gated.

    Bands: Strong (distinguishably above baseline), Promising (above, inside
    the margin, adequate sample), Mixed (at/below), Limited evidence (sample
    below MIN_CUISINE_SAMPLE). The fit index maps the survival gap onto the
    same 50-neutral scale scoring.py already uses (50 + gap*250, clipped),
    so a site's cuisine component and an area's fit never disagree in
    direction. It is a relative comparison figure, not a probability.
    """
    compset = cuisines.competitive_set(cuisine)
    table = cuisine_area_table(panel, assignment, compset)
    baseline, baseline_n = _city_baseline(panel, compset)

    rows = []
    for code, row in table.iterrows():
        n, survived = int(row["cohort_n"]), int(row["cohort_survived"])
        if n < MIN_CUISINE_SAMPLE or not np.isfinite(baseline):
            band, index = "Limited evidence", None
        else:
            rate = survived / n
            verdict = rate_differs(survived, n, baseline)
            gap = rate - baseline
            index = float(np.clip(50 + gap * 250, 0, 100))
            if verdict == "above":
                band = "Strong"
            elif gap > 0:
                band = "Promising"
            else:
                band = "Mixed"
        rows.append(dict(nta_code=code, band=band, fit_index=index,
                         cohort_n=n, cohort_survived=survived,
                         active_same=int(row["active"]),
                         baseline_rate=baseline, baseline_n=baseline_n))
    return pd.DataFrame(rows).set_index("nta_code")


# ------------------------------------------------------------------ density
def restaurant_density_by_cuisine(panel: pd.DataFrame, assignment: pd.Series,
                                  cuisine: str) -> pd.DataFrame:
    """
    Active same-set establishments per NTA, with the NYC percentile among
    NTAs that have any. Establishments, never inspection rows.
    """
    compset = cuisines.competitive_set(cuisine)
    table = cuisine_area_table(panel, assignment, compset)
    counts = table["active"]
    nonzero = counts[counts > 0]
    out = pd.DataFrame({"active_same": counts})
    out["density_percentile"] = counts.map(
        lambda v: float((nonzero < v).mean() * 100) if v > 0 else 0.0)
    return out


def competitor_saturation(active_same: int, density_percentile: float | None,
                          strong_nearby: int | None = None) -> dict:
    """
    Relative saturation with comparable restaurants.

    Area form: the density percentile alone. Site form: the Google strong-
    competitor count sharpens the call when the landscape is available.
    Missing data yields None — absence of competition data must never read
    as low competition (tested).
    """
    if density_percentile is None or not np.isfinite(density_percentile):
        return dict(band=None, detail="No comparable-density data.")
    if density_percentile >= 75 or (strong_nearby or 0) >= 3:
        band = "High"
    elif density_percentile >= 40 or (strong_nearby or 0) >= 1:
        band = "Moderate"
    else:
        band = "Low"
    detail = (f"{active_same} comparable establishments · "
              f"{density_percentile:.0f}th percentile among NYC areas")
    if strong_nearby is not None:
        detail += f" · {strong_nearby} strong nearby"
    return dict(band=band, detail=detail,
                density_percentile=float(density_percentile))


# ------------------------------------------------------------------ gap
def opportunity_gap(fit_band: str | None,
                    saturation_band: str | None) -> dict:
    """
    Where the concept fits AND comparable competition is relatively lower.

    A lookup, not a model: every state traces to its two component bands.
    Anything unmeasured is Insufficient evidence — a gap claim without a
    competition reading would be an invented "unmet demand".
    """
    if fit_band in (None, "Limited evidence") or saturation_band is None:
        return dict(band="Insufficient evidence",
                    reason="fit or competition unmeasured")
    matrix = {
        ("Strong", "Low"): "High", ("Strong", "Moderate"): "High",
        ("Strong", "High"): "Moderate",
        ("Promising", "Low"): "High", ("Promising", "Moderate"): "Moderate",
        ("Promising", "High"): "Low",
        ("Mixed", "Low"): "Moderate", ("Mixed", "Moderate"): "Low",
        ("Mixed", "High"): "Low",
    }
    band = matrix[(fit_band, saturation_band)]
    return dict(band=band,
                reason=f"concept fit {fit_band.lower()}, comparable "
                       f"competition {saturation_band.lower()}")


# ------------------------------------------------------------------ turnover
def area_turnover_context(features: pd.DataFrame,
                          panel: pd.DataFrame) -> pd.DataFrame:
    """
    Observed turnover per NTA: share of ever-listed restaurants gone since
    2017, against the citywide share, Wilson-gated. Unknown stays LIMITED
    EVIDENCE — never a risk signal.
    """
    city_gone = float((panel["status"] == "closed").mean())
    rows = []
    for code, row in features.iterrows():
        ever, gone = int(row["restaurants_ever"]), int(row["gone"])
        if ever < MIN_AREA_SAMPLE:
            band, rate = "Limited evidence", None
        else:
            rate = gone / ever
            verdict = rate_differs(gone, ever, city_gone)
            band = {"above": "Higher observed turnover",
                    "below": "Lower observed turnover",
                    "inconclusive": "Typical"}[verdict]
        rows.append(dict(nta_code=code, band=band, gone=gone, ever=ever,
                         rate=rate, city_rate=city_gone))
    return pd.DataFrame(rows).set_index("nta_code")


# ------------------------------------------------------------------ evidence
def evidence_quality_by_area(features: pd.DataFrame,
                             acs_by_nta: pd.Series | None) -> pd.DataFrame:
    """
    HIGH / MODERATE / LIMITED per NTA, from three stated checks: cohort
    depth, active inventory, ACS availability. Evidence describes the data,
    never the location — thin evidence must not read as risk.
    """
    rows = []
    for code, row in features.iterrows():
        points = 0
        points += int(row["cohort_n"]) >= MIN_AREA_SAMPLE
        points += int(row["restaurants_active"]) >= MIN_AREA_SAMPLE
        points += bool(acs_by_nta is not None and acs_by_nta.get(code, False))
        band = ["Limited", "Limited", "Moderate", "High"][points]
        rows.append(dict(nta_code=code, band=band,
                         cohort_n=int(row["cohort_n"]),
                         active_n=int(row["restaurants_active"]),
                         acs=bool(acs_by_nta is not None
                                  and acs_by_nta.get(code, False))))
    return pd.DataFrame(rows).set_index("nta_code")


# ------------------------------------------------------------------ concepts
def rank_concepts_for_area(panel: pd.DataFrame, assignment: pd.Series,
                           nta_code: str, top: int = 5) -> list[dict]:
    """
    "What concept fits here?" — every canonical cuisine with a citywide
    presence of at least MIN_CITYWIDE_CUISINE active restaurants, evaluated
    with the SAME fit formula, ranked by fit index. Cuisines whose local
    sample misses the threshold are excluded rather than guessed at.
    """
    active = panel[panel["seen_2026"] & (panel["cuisine"] != "")]
    candidates = [c for c, n in active["cuisine"].value_counts().items()
                  if n >= MIN_CITYWIDE_CUISINE]
    rows = []
    for cuisine in candidates:
        fit = area_concept_fit(panel, assignment, cuisine)
        if nta_code not in fit.index:
            continue
        row = fit.loc[nta_code]
        if row["band"] == "Limited evidence" or row["fit_index"] is None:
            continue
        rows.append(dict(cuisine=cuisine, fit_index=float(row["fit_index"]),
                         band=row["band"], cohort_n=int(row["cohort_n"]),
                         active_same=int(row["active_same"])))
    rows.sort(key=lambda r: -r["fit_index"])
    return rows[:top]


def compare_concepts(panel: pd.DataFrame, assignment: pd.Series,
                     nta_code: str, concepts: list[str]) -> pd.DataFrame:
    """Up to three concepts at one area, same formulas throughout."""
    rows = {}
    density_cache = {}
    for cuisine in concepts[:3]:
        fit = area_concept_fit(panel, assignment, cuisine)
        density = restaurant_density_by_cuisine(panel, assignment, cuisine)
        density_cache[cuisine] = density
        fit_row = fit.loc[nta_code] if nta_code in fit.index else None
        dens_row = density.loc[nta_code] if nta_code in density.index else None
        saturation = competitor_saturation(
            int(dens_row["active_same"]) if dens_row is not None else 0,
            float(dens_row["density_percentile"]) if dens_row is not None else None)
        gap = opportunity_gap(
            fit_row["band"] if fit_row is not None else None,
            saturation["band"])
        rows[cuisine] = {
            "Fit": ("—" if fit_row is None or fit_row["fit_index"] is None
                    else f"{fit_row['fit_index']:.0f}"),
            "Fit band": fit_row["band"] if fit_row is not None else "Limited evidence",
            "Competition": saturation["band"] or "—",
            "Opportunity gap": gap["band"],
            "Local sample": int(fit_row["cohort_n"]) if fit_row is not None else 0,
        }
    return pd.DataFrame(rows)


def compare_locations(rows: list[dict]) -> pd.DataFrame:
    """2-3 saved area/site snapshots, columns side by side, gaps as dashes."""
    frame = pd.DataFrame(rows[:3]).set_index("label").T
    return frame.fillna("—")
