"""
Context about the place itself, as opposed to the restaurants in it.

Three sources, each with a different reason to be careful:
  PLUTO       - authoritative on the building, joined on BBL.
  Pedestrian  - 114 counting sites for the whole city. Almost never outside
                the address you asked about.
  Census      - the supplied ACS file is national-level and has no income at
                all, so tract statistics need a Census API key. Absent one,
                this module reports nothing rather than guessing.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import config
from .geo import haversine_m

#: Beyond this, a pedestrian counter describes a different street entirely.
PEDESTRIAN_USEFUL_M = 400

PLUTO_FIELDS = ["BBL", "address", "bldgclass", "landuse", "lotarea", "bldgarea",
                "comarea", "retailarea", "officearea", "numfloors", "unitsres",
                "unitstotal", "yearbuilt", "assesstot", "latitude", "longitude",
                "zonedist1", "ownername"]

LAND_USE = {
    "01": "One & two family buildings", "02": "Multi-family walk-up",
    "03": "Multi-family elevator", "04": "Mixed residential & commercial",
    "05": "Commercial & office", "06": "Industrial & manufacturing",
    "07": "Transportation & utility", "08": "Public facilities & institutions",
    "09": "Open space & outdoor recreation", "10": "Parking facilities",
    "11": "Vacant land",
}


def load_pluto_lots() -> pd.DataFrame:
    """Building characteristics, one row per tax lot, indexed by BBL."""
    df = pd.read_csv(config.PLUTO, usecols=lambda c: c in PLUTO_FIELDS,
                     dtype={"BBL": str, "bldgclass": str, "landuse": str},
                     low_memory=False)
    df = df.rename(columns={"BBL": "bbl", "latitude": "lat", "longitude": "lon"})
    df["bbl"] = df["bbl"].str.strip().str.replace(r"\.0$", "", regex=True)
    df["landuse_desc"] = df["landuse"].str.zfill(2).map(LAND_USE)
    return df.drop_duplicates("bbl").set_index("bbl")


def lot_context(lots: pd.DataFrame, bbl: str | None) -> dict | None:
    """What PLUTO knows about one tax lot."""
    if not bbl:
        return None
    key = str(bbl).strip().replace(".0", "")
    if key not in lots.index:
        return None
    row = lots.loc[key]
    return {
        "address": row.get("address"),
        "land_use": row.get("landuse_desc"),
        "building_class": row.get("bldgclass"),
        "zoning": row.get("zonedist1"),
        "year_built": _int(row.get("yearbuilt")),
        "num_floors": _num(row.get("numfloors")),
        "lot_area": _int(row.get("lotarea")),
        "building_area": _int(row.get("bldgarea")),
        "commercial_area": _int(row.get("comarea")),
        "retail_area": _int(row.get("retailarea")),
        "residential_units": _int(row.get("unitsres")),
    }


def _int(v):
    try:
        return None if pd.isna(v) else int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return None if pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


_PERIOD = re.compile(r"^(May|Sept|Oct)(\d{2})_(AM|MD|PM)$")
_POINT = re.compile(r"POINT \(([-\d.]+) ([\d.]+)\)")


def load_pedestrian() -> pd.DataFrame:
    """
    The 114 pedestrian counting sites, reduced to their most recent counts.

    The file is wide: one column per season, year and time-of-day going back to
    2007. We keep the latest period that actually has numbers and sum AM+MD+PM
    into a single daily-ish indicator.
    """
    df = pd.read_csv(config.PEDESTRIAN, dtype=str, low_memory=False)
    coords = df["the_geom"].str.extract(_POINT)
    df["lon"] = pd.to_numeric(coords[0], errors="coerce")
    df["lat"] = pd.to_numeric(coords[1], errors="coerce")

    periods: dict[str, list[str]] = {}
    for col in df.columns:
        m = _PERIOD.match(col)
        if m:
            periods.setdefault(f"{m.group(2)}_{m.group(1)}", []).append(col)

    def sort_key(label: str):
        yy, season = label.split("_")
        return (int(yy), {"May": 0, "Sept": 1, "Oct": 1}[season])

    counts = pd.Series(np.nan, index=df.index)
    period_used = pd.Series("", index=df.index)
    for label in sorted(periods, key=sort_key):
        cols = periods[label]
        total = df[cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        fill = counts.isna() & total.notna()
        counts = counts.where(~fill, total)
        period_used = period_used.where(~fill, label)
    # Later periods overwrite earlier ones where present.
    for label in sorted(periods, key=sort_key):
        cols = periods[label]
        total = df[cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        counts = counts.where(total.isna(), total)
        period_used = period_used.where(total.isna(), label)

    out = df[["Loc", "Borough", "Street_Nam", "From_Stree", "To_Street", "lat", "lon"]].copy()
    out["count"] = counts
    out["period"] = period_used
    return out.dropna(subset=["lat", "lon", "count"])


def pretty_period(label: str) -> str:
    """'26_May' -> 'May 2026' — the raw column token means nothing to a user."""
    try:
        yy, season = label.split("_")
        return f"{season} 20{yy}"
    except (ValueError, AttributeError):
        return str(label)


def nearest_pedestrian(ped: pd.DataFrame, lat: float, lon: float) -> dict | None:
    """
    Closest counting site, with its distance stated.

    NYC counts 114 locations. The nearest one is usually not the street the
    user asked about, so the distance is returned alongside the number and the
    caller must show it. Anything past PEDESTRIAN_USEFUL_M is reported as
    context about the district, never as this address's footfall.
    """
    if ped.empty:
        return None
    d = haversine_m(lat, lon, ped["lat"].values, ped["lon"].values)
    i = int(np.argmin(d))
    row = ped.iloc[i]
    return {
        "street": row["Street_Nam"],
        "between": f"{row['From_Stree']} and {row['To_Street']}",
        "borough": row["Borough"],
        "count": int(row["count"]),
        "period": pretty_period(row["period"]),
        "distance_m": float(d[i]),
        "represents_this_block": bool(d[i] <= PEDESTRIAN_USEFUL_M),
    }
