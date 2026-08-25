"""
Building one restaurant-level table out of four differently-shaped files.

THE GRAIN PROBLEM
The DOHMH files are one row per VIOLATION, not per restaurant: 400k rows cover
26.5k restaurants. Everything here aggregates by CAMIS first.

THE CENSORING PROBLEM
This is the subtle one, and getting it wrong would poison every comparison the
app makes. Our two big files observe two disjoint stretches of time:

    2011-10 ------- 2017-08        (the archive)
                                        2023-01 ---- 2026-08   (the extract)
              2021 partial snapshot ^

A restaurant that closed in 2019 is last observed in 2017, so its "observed
duration" is capped at ~6 years by the archive's cutoff. A survivor is observed
into 2026 and can show 13. Ranking those durations against each other would
conclude that closing early causes short durations — which is circular.

So duration is NOT the primary measure. The primary measure is a clean binary:
of the restaurants observed in 2011-2017, which ones were still in DOHMH's
system in 2026? That question is not distorted by where the windows fall.
Durations are still computed, but carry explicit censoring flags.
"""
from __future__ import annotations

import pandas as pd

from . import config, cuisines
from .normalize import (
    location_key, location_key_variants, normalize_borough,
    normalize_building, normalize_street, pretty_address,
)

# Column names differ between files; this is the one place that knows about it.
HIST_COLS = ["CAMIS", "DBA", "BORO", "BUILDING", "STREET", "ZIPCODE",
             "CUISINE DESCRIPTION", "INSPECTION DATE", "CRITICAL FLAG"]
CUR_COLS = HIST_COLS + ["Latitude", "Longitude", "BBL", "BIN", "NTA",
                        "Census Tract", "Community Board"]
PRE_COLS = ["CAMIS", "DBA", "BORO", "BUILDING", "STREET",
            "CUISINE DESCRIPTION", "INSPECTION DATE"]

RENAME = {
    "CAMIS": "camis", "DBA": "name", "RESTAURANT": "name", "BORO": "boro",
    "BUILDING": "building", "STREET": "street", "ZIPCODE": "zipcode",
    "CUISINE DESCRIPTION": "cuisine", "CUISINE_DESCRIPTION": "cuisine",
    "INSPECTION DATE": "inspection_date", "INSPECTION_DATE": "inspection_date",
    "CRITICAL FLAG": "critical_flag", "CRITICALFLAG": "critical_flag",
    "Latitude": "lat", "Longitude": "lon", "BBL": "bbl", "BIN": "bin",
    "NTA": "nta", "Census Tract": "census_tract",
    "Community Board": "community_board",
}


def _parse_dates(series: pd.Series) -> pd.Series:
    """
    DOHMH writes 01/01/1900 for 'never inspected'. Treated as a real date it
    would hand every such restaurant a 120-year lifespan, so it becomes NaT.
    """
    parsed = pd.to_datetime(series, format="%m/%d/%Y", errors="coerce")
    return parsed.mask(parsed.dt.year <= 1900)


def _read(path, cols) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=lambda c: c in cols, dtype=str, low_memory=False)
    df = df.rename(columns=RENAME)
    df["inspection_date"] = _parse_dates(df["inspection_date"])
    df["camis"] = df["camis"].astype(str).str.strip()
    return df


def _aggregate(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Collapse inspection rows to one row per restaurant."""
    g = df.groupby("camis")
    out = pd.DataFrame({
        f"{prefix}_first": g["inspection_date"].min(),
        f"{prefix}_last": g["inspection_date"].max(),
        f"{prefix}_rows": g.size(),
    })
    if "critical_flag" in df.columns:
        crit = df["critical_flag"].fillna("").str.strip().str.lower().eq("critical")
        out[f"{prefix}_critical"] = crit.groupby(df["camis"]).sum()
    return out


def _identity(df: pd.DataFrame) -> pd.DataFrame:
    """Most recent name/cuisine/address per restaurant."""
    ordered = df.sort_values("inspection_date")
    keep = [c for c in ["name", "cuisine", "boro", "building", "street", "zipcode",
                        "lat", "lon", "bbl", "bin", "nta", "census_tract",
                        "community_board"] if c in ordered.columns]
    return ordered.groupby("camis")[keep].last()


def build_restaurants() -> pd.DataFrame:
    """One row per CAMIS, spanning every source that mentions it."""
    hist = _read(config.HIST_2017, HIST_COLS)
    cur = _read(config.CUR_2026, CUR_COLS)
    pre = _read(config.PREPERMIT, PRE_COLS)
    snap = pd.read_csv(config.SNAP_2021, dtype=str, low_memory=False).rename(columns=RENAME)
    snap["camis"] = snap["camis"].astype(str).str.strip()

    frames = [
        _aggregate(hist, "hist"), _aggregate(cur, "cur"), _aggregate(pre, "pre"),
    ]
    panel = pd.concat(frames, axis=1)

    # Identity: prefer the current extract (richer, geocoded), fall back to the
    # archive for restaurants that no longer exist.
    ident = _identity(hist).combine_first(_identity(cur))
    ident.update(_identity(cur))
    panel = panel.join(ident, how="left")
    panel.index.name = "camis"
    panel = panel.reset_index()

    panel["seen_2017"] = panel["camis"].isin(set(hist["camis"]))
    panel["seen_2021"] = panel["camis"].isin(set(snap["camis"]))
    panel["seen_2026"] = panel["camis"].isin(set(cur["camis"]))

    date_cols = ["hist_first", "hist_last", "cur_first", "cur_last",
                 "pre_first", "pre_last"]
    panel["first_observed"] = panel[date_cols].min(axis=1)
    panel["last_observed"] = panel[date_cols].max(axis=1)
    panel["first_prepermit"] = panel["pre_first"]

    for col in ["hist_rows", "cur_rows", "pre_rows"]:
        panel[col] = panel[col].fillna(0)
    panel["n_inspection_rows"] = panel[["hist_rows", "cur_rows", "pre_rows"]].sum(axis=1)
    panel["critical_violations"] = (
        panel.get("hist_critical", 0).fillna(0) + panel.get("cur_critical", 0).fillna(0)
    )

    panel["cuisine"] = panel["cuisine"].map(cuisines.clean_label)

    # --- status ------------------------------------------------------------
    # Presence in the 2026 extract means DOHMH still has the establishment on
    # its books. Absence, for something the 2017 archive recorded, is the
    # closure signal — the whole reason the archive is worth having.
    panel["status"] = "unknown"
    panel.loc[panel["seen_2026"], "status"] = "active"
    panel.loc[~panel["seen_2026"] & panel["seen_2017"], "status"] = "closed"

    # --- censoring ---------------------------------------------------------
    hist_start = pd.Timestamp(config.HIST_WINDOW[0])
    snapshot = pd.Timestamp(config.SNAPSHOT_DATE)

    # Already trading when observation began: true age is unknown and larger.
    panel["left_censored"] = panel["first_observed"] <= hist_start + pd.Timedelta(days=120)
    # Still trading: the duration we can see is a floor, not a total.
    panel["right_censored"] = panel["status"] == "active"

    panel["observed_years"] = (
        (panel["last_observed"] - panel["first_observed"]).dt.days / 365.25
    )

    # For closures the exact date is unknown; we know only that it happened
    # between the last sighting and the 2026 extract. Report the interval.
    panel["closed_after"] = panel["last_observed"].where(panel["status"] == "closed")
    panel["closed_before"] = pd.Series(
        [snapshot] * len(panel), index=panel.index
    ).where(panel["status"] == "closed")
    panel["closure_uncertainty_years"] = (
        (panel["closed_before"] - panel["closed_after"]).dt.days / 365.25
    )

    # --- location identity --------------------------------------------------
    panel["location_key"] = [
        location_key(b, n, s)
        for b, n, s in zip(panel["boro"], panel["building"], panel["street"])
    ]
    panel["address"] = [
        pretty_address(b, n, s)
        for b, n, s in zip(panel["boro"], panel["building"], panel["street"])
    ]
    for col in ("lat", "lon"):
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    # DOHMH writes 0,0 for establishments it could not geocode. Kept as real
    # coordinates they sit in the Gulf of Guinea: silently absent from every
    # radius query, yet counted as "placed" — and, worse, a non-null latitude
    # stops attach_coordinates from recovering them by address. 475 restaurants
    # were in that state when this was audited.
    ungeocoded = (panel["lat"] == 0) | (panel["lon"] == 0)
    panel.loc[ungeocoded, ["lat", "lon"]] = float("nan")

    return panel


def build_location_index() -> pd.DataFrame:
    """
    location_key -> coordinates, for addresses the archive cannot place itself.

    The 2017 archive has no latitude or longitude, so a restaurant that closed
    before 2026 has no coordinates of its own. We recover them from two places
    that do: the current extract (where a surviving restaurant shares the
    address) and PLUTO (which knows every tax lot in the city). Measured
    coverage of closed restaurants: 50.6% from the extract alone, 81.5% once
    PLUTO and the house-number variants are added.
    """
    cur = pd.read_csv(
        config.CUR_2026,
        usecols=["BORO", "BUILDING", "STREET", "BBL", "Latitude", "Longitude",
                 "NTA", "Census Tract"],
        dtype=str, low_memory=False,
    ).rename(columns=RENAME)
    cur = cur.dropna(subset=["lat", "lon"])
    # The same 0,0 sentinel appears here; an index entry carrying it would
    # hand the bogus coordinates straight back to any restaurant that resolves
    # through this address.
    for col in ("lat", "lon"):
        cur[col] = pd.to_numeric(cur[col], errors="coerce")
    cur = cur[(cur["lat"] != 0) & (cur["lon"] != 0)]
    cur["location_key"] = [
        location_key(b, n, s)
        for b, n, s in zip(cur["boro"], cur["building"], cur["street"])
    ]
    cur = cur[cur["location_key"] != ""].drop_duplicates("location_key")
    cur["source"] = "dohmh"

    pluto = pd.read_csv(
        config.PLUTO,
        usecols=["borough", "address", "BBL", "latitude", "longitude", "tract2010"],
        dtype={"BBL": str, "address": str, "borough": str}, low_memory=False,
    ).dropna(subset=["address", "latitude", "longitude"])
    split = pluto["address"].str.strip().str.split(n=1)
    pluto["location_key"] = (
        pluto["borough"].str.upper()
        + "|" + split.str[0].map(normalize_building)
        + "|" + split.str[1].fillna("").map(normalize_street)
    )
    pluto = pluto.rename(columns={"latitude": "lat", "longitude": "lon",
                                  "BBL": "bbl", "tract2010": "census_tract"})
    pluto = pluto.drop_duplicates("location_key")
    pluto["source"] = "pluto"
    pluto["nta"] = None

    keep = ["location_key", "lat", "lon", "bbl", "nta", "census_tract", "source"]
    index = pd.concat([cur[keep], pluto[keep]], ignore_index=True)
    # DOHMH rows come first, so keeping the first occurrence prefers them.
    return index.drop_duplicates("location_key", keep="first").set_index("location_key")


def attach_coordinates(panel: pd.DataFrame, index: pd.DataFrame) -> pd.DataFrame:
    """Fill missing coordinates by looking the address up, trying each variant."""
    lookup = index.to_dict("index")
    need = panel["lat"].isna()

    resolved_key, lat, lon, bbl, tract, src = [], [], [], [], [], []
    for row in panel.itertuples():
        if not need[row.Index]:
            resolved_key.append(row.location_key)
            lat.append(row.lat); lon.append(row.lon); bbl.append(row.bbl)
            tract.append(getattr(row, "census_tract", None)); src.append("self")
            continue
        hit = None
        for variant in location_key_variants(row.boro, row.building, row.street):
            if variant in lookup:
                hit = (variant, lookup[variant]); break
        if hit is None:
            resolved_key.append(row.location_key)
            lat.append(None); lon.append(None); bbl.append(row.bbl)
            tract.append(None); src.append("unresolved")
        else:
            key, rec = hit
            resolved_key.append(key)
            lat.append(rec["lat"]); lon.append(rec["lon"]); bbl.append(rec["bbl"])
            tract.append(rec["census_tract"]); src.append(rec["source"])

    out = panel.copy()
    out["location_key"] = resolved_key
    out["lat"] = pd.to_numeric(pd.Series(lat, index=out.index), errors="coerce")
    out["lon"] = pd.to_numeric(pd.Series(lon, index=out.index), errors="coerce")
    out["bbl"] = pd.Series(bbl, index=out.index)
    out["census_tract"] = pd.Series(tract, index=out.index).fillna(
        out.get("census_tract"))
    out["geo_source"] = src
    return out
