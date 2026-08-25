"""
2020 Neighborhood Tabulation Areas: geography and safe demographic rollups.

Two user-supplied DCP files feed this module: the tract->NTA equivalency
(2,327 tracts, verified no duplicates) and the NTA polygon table (262 areas,
WKT geometry). Every tract GEOID stays an 11-digit string throughout.

THE ONE STATISTICAL RULE THIS MODULE EXISTS TO ENFORCE
A mean of tract medians is not an NTA median — tracts differ in size, and a
median is not additive. Counts (population, employment) are summed; medians
are either left at tract level or rolled up as an explicitly labelled
population-weighted *indicator*, never under a "median" name.
`nta_demographics` is the only rollup path, and a regression test pins that
its output contains no NTA-level column with "median" in the name.
"""
from __future__ import annotations

import pandas as pd

from . import config

#: NTAType codes that are not residential neighbourhoods (parks, airports,
#: cemeteries, Rikers...). Kept in the tables, flagged for display layers.
NON_RESIDENTIAL_TYPES = {"5", "6", "7", "8", "9"}


def load_equivalency(path=None) -> pd.DataFrame:
    """tract_geoid -> nta_code / nta_name / borough, all strings."""
    df = pd.read_csv(path or config.TRACT_TO_NTA, dtype=str)
    out = df.rename(columns={
        "GEOID": "tract_geoid", "NTACode": "nta_code", "NTAName": "nta_name",
        "BoroName": "borough", "NTAType": "nta_type",
    })[["tract_geoid", "nta_code", "nta_name", "borough", "nta_type"]]
    bad = out["tract_geoid"].str.len() != 11
    if bad.any():
        raise ValueError(f"{bad.sum()} malformed tract GEOIDs in equivalency")
    dupes = out["tract_geoid"].duplicated().sum()
    if dupes:
        raise ValueError(f"{dupes} duplicate tract mappings in equivalency")
    return out


def load_polygons(path=None) -> pd.DataFrame:
    """The 262 NTA features: code, name, borough, type, WKT geometry."""
    df = pd.read_csv(path or config.NTA_POLYGONS, dtype=str)
    out = df.rename(columns={
        "NTA2020": "nta_code", "NTAName": "nta_name",
        "BoroName": "borough", "NTAType": "nta_type", "the_geom": "geometry_wkt",
    })[["nta_code", "nta_name", "borough", "nta_type", "geometry_wkt"]]
    if out["nta_code"].duplicated().any():
        raise ValueError("duplicate NTA codes in polygon file")
    out["is_residential"] = ~out["nta_type"].isin(NON_RESIDENTIAL_TYPES)
    return out


def nta_demographics(acs_tracts: pd.DataFrame,
                     equivalency: pd.DataFrame) -> pd.DataFrame:
    """
    The safe NTA rollup.

    Sums the additive variables; rolls medians up only as population-weighted
    context indicators with names that say exactly what they are. Tracts with
    a missing value are excluded from that indicator's weights rather than
    counted as zero.
    """
    joined = acs_tracts.merge(equivalency, on="tract_geoid", how="inner",
                              suffixes=("", "_nta"))

    def weighted_indicator(group: pd.DataFrame, col: str) -> float | None:
        valid = group.dropna(subset=[col, "population"])
        valid = valid[valid["population"] > 0]
        if valid.empty:
            return None
        return float((valid[col] * valid["population"]).sum()
                     / valid["population"].sum())

    borough_col = ("borough_nta" if "borough_nta" in joined.columns
                   else "borough")
    rows = []
    # Group by code alone: Marble Hill (BX0802) spans tracts labelled both
    # Bronx and Manhattan, and a borough-keyed group would split its
    # population across two rows. The modal borough labels the row; the sum
    # covers every component tract.
    for code, group in joined.groupby("nta_code"):
        name = group["nta_name"].iloc[0]
        borough = group[borough_col].mode().iloc[0]
        rows.append({
            "nta_code": code, "nta_name": name, "borough": borough,
            "tract_count": len(group),
            # additive: a sum of tract counts IS the NTA count
            "population": float(group["population"].fillna(0).sum()),
            "employed_population": float(
                group["employed_population"].fillna(0).sum()),
            # NOT medians: explicitly-named, population-weighted context
            "income_context": weighted_indicator(
                group, "median_household_income"),
            "age_context": weighted_indicator(group, "median_age"),
        })
    out = pd.DataFrame(rows)
    out["income_context_type"] = "DERIVED_FROM_ACS_TRACTS"
    out["age_context_type"] = "DERIVED_FROM_ACS_TRACTS"
    return out
