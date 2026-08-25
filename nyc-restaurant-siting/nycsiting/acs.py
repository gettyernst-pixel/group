"""
2024 ACS 5-Year census-tract demographics for NYC.

Replaces the legacy national-level DP05 extract, which covered the United
States as a single geography and carried no income column at all — it could
never describe a neighbourhood, and the audit marked it unusable. This module
speaks to the official Census API only, at tract geography, for exactly the
five NYC counties.

ARCHITECTURE: the API is called only by scripts/fetch_acs_nyc.py (five county
requests), which writes data/acs_2024_nyc_tracts.csv. The app reads that local
file and never touches the network on a rerun — a Census outage can slow a
refresh, never a user.

Verified against live variable metadata on 2026-08-25:
    B01003_001E  int    Total population
    B19013_001E  int    Median household income (2024 inflation-adjusted $)
    B01002_001E  float  Median age
    B23025_004E  int    Civilian employed population, 16+
    B25064_001E  int    Median gross rent
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config
from .geo import haversine_m

ACS_BASE = "https://api.census.gov/data/2024/acs/acs5"
ACS_YEAR = 2024
ACS_PRODUCT = "ACS 5-Year"

#: variable id -> canonical column name. Order matters: it is the query order.
VARIABLES = {
    "B01003_001E": "population",
    "B19013_001E": "median_household_income",
    "B01002_001E": "median_age",
    "B23025_004E": "employed_population",
    "B25064_001E": "median_gross_rent",
}

STATE_FIPS = "36"
#: county FIPS -> borough. Only these five are ever queried.
NYC_COUNTIES = {
    "005": "Bronx", "047": "Brooklyn", "061": "Manhattan",
    "081": "Queens", "085": "Staten Island",
}
#: DOHMH borough spelling -> county FIPS, for building GEOIDs from panel rows.
BORO_TO_COUNTY = {
    "Bronx": "005", "Brooklyn": "047", "Manhattan": "061",
    "Queens": "081", "Staten Island": "085",
}

#: Census publishes sentinel negatives (-666666666 and kin) for suppressed or
#: unavailable estimates. Any negative in these columns is a sentinel, never a
#: real value — a median income cannot be negative.
NEVER_NEGATIVE = ["population", "median_household_income", "median_age",
                  "employed_population", "median_gross_rent"]
#: Medians of zero are also non-values (an income of $0 is suppression noise).
POSITIVE_ONLY = ["median_household_income", "median_age", "median_gross_rent"]

#: How far a site may be from the nearest tract-bearing DOHMH restaurant
#: before we decline to borrow its tract. Same editorial spirit as the
#: pedestrian DIRECT_NEARBY rule: borrowing geography from across the street
#: is fine, from three blocks away is invention.
TRACT_BORROW_MAX_M = 150.0

PROVENANCE = {
    name: {
        "source": "US Census Bureau", "dataset": f"{ACS_YEAR} {ACS_PRODUCT}",
        "variable": var, "geography": "Census tract", "type": "ACS_ESTIMATE",
    } for var, name in VARIABLES.items()
}


class CensusKeyMissing(Exception):
    """Raised by the fetch path when no CENSUS_API_KEY is configured."""

    def __init__(self):
        super().__init__(
            "No CENSUS_API_KEY found. The Census API requires a key for data "
            "queries. Get a free key at https://api.census.gov/data/key_signup.html "
            "and put it in .streamlit/secrets.toml as\n\n"
            '    CENSUS_API_KEY = "your-key"\n\n'
            "or export CENSUS_API_KEY in the environment, then re-run "
            "scripts/fetch_acs_nyc.py. The app keeps working without it — the "
            "Local Market section shows an honest 'not fetched' state.")


# ------------------------------------------------------------------ fetch
def fetch_county(county_fips: str, api_key: str,
                 session=None) -> list[list[str]]:
    """One county's tracts, as the raw Census row-of-rows payload."""
    params = {
        "get": "NAME," + ",".join(VARIABLES),
        "for": "tract:*",
        "in": f"state:{STATE_FIPS} county:{county_fips}",
        "key": api_key,
    }
    getter = (session or requests).get
    response = getter(ACS_BASE, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError(f"Census returned an unexpected payload for county "
                         f"{county_fips}")
    return payload


def parse_payload(payload: list[list[str]]) -> pd.DataFrame:
    """
    Census rows -> clean tract frame with an 11-digit string GEOID.

    All geography columns stay strings throughout: the Bronx county code is
    '005', and an integer would eat the leading zeros that make a GEOID a
    GEOID.
    """
    df = pd.DataFrame(payload[1:], columns=payload[0])
    df = df.rename(columns={**VARIABLES, "NAME": "census_name"})

    for col in ("state", "county", "tract"):
        df[col] = df[col].astype(str)
    df["tract_geoid"] = df["state"] + df["county"] + df["tract"]
    bad = df["tract_geoid"].str.len() != 11
    if bad.any():
        raise ValueError(f"{bad.sum()} malformed GEOIDs, e.g. "
                         f"{df.loc[bad, 'tract_geoid'].iloc[0]!r}")

    for col in VARIABLES.values():
        df[col] = pd.to_numeric(df[col], errors="coerce")
        # Sentinels: negative anywhere is a non-value, never a real estimate.
        df.loc[df[col] < 0, col] = np.nan
    for col in POSITIVE_ONLY:
        df.loc[df[col] == 0, col] = np.nan

    df["borough"] = df["county"].map(NYC_COUNTIES)
    df = df.rename(columns={"state": "state_fips", "county": "county_fips"})
    df["acs_year"] = ACS_YEAR
    df["acs_product"] = ACS_PRODUCT
    return df[["tract_geoid", "state_fips", "county_fips", "tract", "borough",
               "census_name", *VARIABLES.values(), "acs_year", "acs_product"]]


def fetch_all_nyc(api_key: str, session=None) -> pd.DataFrame:
    """All five NYC counties — exactly five requests, never one per tract."""
    frames = [parse_payload(fetch_county(fips, api_key, session))
              for fips in NYC_COUNTIES]
    out = pd.concat(frames, ignore_index=True)
    dupes = out["tract_geoid"].duplicated().sum()
    if dupes:
        raise ValueError(f"{dupes} duplicate tract GEOIDs across counties")
    return out


# ------------------------------------------------------------------ cache
def save_cache(df: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or config.ACS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def load_cache(path: Path | None = None) -> pd.DataFrame | None:
    """The local tract table, or None when the fetch has not been run yet."""
    path = path or config.ACS_CSV
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"tract_geoid": str, "state_fips": str,
                                  "county_fips": str, "tract": str})
    return df


# ------------------------------------------------------------------ tracts
def normalize_tract_code(value: object) -> str | None:
    """
    DOHMH/PLUTO tract spellings -> the 6-digit Census tract code.

    The panel holds '025200' (already right), '348.0' (float-string: tract
    348 -> '034800'), and short forms like '112' -> '011200'. Tract codes are
    four integer digits plus two implied decimals, so the integer part is
    left-padded to 4 and the decimal part right-padded to 2.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if "." in text:
        whole, _, frac = text.partition(".")
        frac = (frac or "0")[:2].ljust(2, "0")
        whole = "".join(ch for ch in whole if ch.isdigit())
        if not whole:
            return None
        return whole.zfill(4) + frac
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    if len(digits) >= 6:
        return digits[-6:]
    if len(digits) <= 4:
        return digits.zfill(4) + "00"
    return digits.zfill(6)


def tract_geoid_for(boro: object, tract_code: object) -> str | None:
    county = BORO_TO_COUNTY.get(str(boro).strip().title() if boro else "")
    code = normalize_tract_code(tract_code)
    if not county or not code:
        return None
    return STATE_FIPS + county + code


GEOCODER_URL = ("https://geocoding.geo.census.gov/geocoder/geographies/"
                "coordinates")


def tract_geoid_from_point(lat: float, lon: float,
                           session=None) -> str | None:
    """
    2020 tract GEOID for a coordinate, from the official Census geocoder.

    Keyless and authoritative — this is the primary path. Returns None on any
    failure; the caller falls back to borrowing a neighbour's tract.
    """
    getter = (session or requests).get
    try:
        response = getter(GEOCODER_URL, params={
            "x": lon, "y": lat, "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "format": "json"}, timeout=15)
        response.raise_for_status()
        tracts = (response.json().get("result", {})
                  .get("geographies", {}).get("Census Tracts", []))
        geoid = tracts[0].get("GEOID") if tracts else None
        return geoid if geoid and len(str(geoid)) == 11 else None
    except Exception:
        return None


def site_tract_geoid(panel: pd.DataFrame, lat: float, lon: float,
                     valid_geoids: set[str] | None = None,
                     max_m: float = TRACT_BORROW_MAX_M,
                     session=None) -> tuple[str | None, str]:
    """
    The Census tract containing an address: (geoid, source).

    Primary: the official Census coordinate geocoder (2020 vintage, keyless).
    Fallback: borrow the tract of the nearest DOHMH-listed restaurant — but
    the DOHMH field is 2010-VINTAGE (verified empirically: every join miss is
    a tract split in 2020), so a borrowed code is only trusted when it exists
    in the 2020 equivalency, which is precisely the unsplit-and-identical
    case. If neither path answers, the honest result is (None, ...): we never
    invent geography.
    """
    geoid = tract_geoid_from_point(lat, lon, session=session)
    if geoid and (valid_geoids is None or geoid in valid_geoids):
        return geoid, "census_geocoder"

    carriers = panel[
        panel["seen_2026"] & panel["lat"].notna()
        & panel["census_tract"].notna() & (panel["geo_source"] == "self")]
    if carriers.empty:
        return None, "unavailable"
    distances = haversine_m(lat, lon, carriers["lat"].values,
                            carriers["lon"].values)
    idx = int(np.argmin(distances))
    if distances[idx] > max_m:
        return None, "unavailable"
    row = carriers.iloc[idx]
    borrowed = tract_geoid_for(row["boro"], row["census_tract"])
    if borrowed and valid_geoids is not None and borrowed not in valid_geoids:
        # 2010 code split in 2020 — using it would fetch the wrong tract.
        return None, "unavailable"
    return borrowed, "dohmh_neighbour"


# ------------------------------------------------------------------ benchmarks
def tract_percentiles(acs: pd.DataFrame, geoid: str) -> dict:
    """
    Where this tract sits among all NYC tracts, per metric — computed over
    valid values only, and returned alongside the values themselves.
    """
    row = acs[acs["tract_geoid"] == geoid]
    if row.empty:
        return {}
    row = row.iloc[0]
    out = {}
    for col in VARIABLES.values():
        value = row[col]
        if pd.isna(value):
            out[col] = {"value": None, "percentile": None}
            continue
        valid = acs[col].dropna()
        pct = float((valid < value).mean() * 100)
        out[col] = {"value": float(value), "percentile": pct}
    out["borough"] = row["borough"]
    out["census_name"] = row["census_name"]
    return out
