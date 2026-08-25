"""
NYC DOT measured pedestrian counts, for the simulator's demand context.

WHAT THE LIVE API ACTUALLY LOOKS LIKE (discovered 2026-08-24, not assumed)
- Sensor metadata (6up2-gnw8): travelmodes is a comma list; the pedestrian
  value is lowercase "pedestrian"; ids repeat, so metadata is deduped by id.
  Citywide there are only FIVE distinct pedestrian sensor locations, so for
  most restaurant addresses the honest quality verdict is TOO_REMOTE.
- Counts (ct66-47at, ~21M rows): one row per (timestamp, flow). A pedestrian
  sensor carries two directional flows with distinct flowids; the interval
  total is their SUM (verified: in=4 + out=5 at one timestamp = 9 people).
  There is no additional "total" flow to double-count. flowname is null on
  older rows, so aggregation keys on timestamp only, never flowname.
  Pedestrian rows carry status "raw" (bike also has modified/deleted);
  "deleted" rows are excluded.
- Timestamps are floating strings in DOT's source-local clock. They are
  parsed naive and never timezone-shifted, so an 11:00-15:00 lunch filter
  means 11:00-15:00 on the sensor's own clock.

The full dataset is NEVER downloaded: sensors are fetched once (a handful of
rows), then counts are requested per sensor and date window only.

Quality thresholds and the coverage rule are EDITORIAL, documented constants —
not fitted science. Only DIRECT_NEARBY data may influence covers, and only
when the user explicitly switches the demand method; NEARBY_REFERENCE is
context beside the numbers, never inside them.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .geo import haversine_m

V3_URL = "https://data.cityofnewyork.us/api/v3/views/{dataset}/query.json"
SENSOR_DATASET = "6up2-gnw8"
COUNT_DATASET = "ct66-47at"

#: Exact values observed on the live API — see module docstring.
PEDESTRIAN_MODE = "pedestrian"
EXCLUDED_STATUSES = {"deleted"}

#: Editorial distance rules. A sensor 150m away is treated as measuring this
#: block; up to 500m it is reference context; beyond that it says nothing
#: about the address.
DIRECT_NEARBY_M = 150.0
NEARBY_REFERENCE_M = 500.0

#: Editorial completeness rule: a day missing more than 10% of its expected
#: 15-minute intervals is excluded rather than silently undercounted.
MIN_DAILY_COVERAGE = 0.90
INTERVALS_PER_DAY = 96          # PT15M granularity

#: Latest complete weeks used for measurement.
LOOKBACK_WEEKS = 12

#: Product-defined service windows, on the sensor's source-local clock.
SERVICE_PERIODS = {"lunch": (11, 15), "dinner": (17, 22)}

QUALITY_DIRECT = "DIRECT_NEARBY"
QUALITY_REFERENCE = "NEARBY_REFERENCE"
QUALITY_REMOTE = "TOO_REMOTE"
QUALITY_UNAVAILABLE = "UNAVAILABLE"
QUALITY_LOW_DATA = "LOW_DATA_QUALITY"


@dataclass
class PedestrianMeasurement:
    """Everything the UI needs, failure states included. Never raises."""
    quality: str
    sensor_id: str | None = None
    sensor_name: str | None = None
    distance_m: float | None = None
    period_start: str | None = None
    period_end: str | None = None
    raw_days: int = 0
    valid_days: int = 0
    coverage: float = 0.0
    daily: dict = field(default_factory=dict)
    periods: dict = field(default_factory=dict)   # {"lunch": {...}, "dinner": {...}}
    message: str | None = None


# ---------------------------------------------------------------- transport
def _post(dataset: str, soql: str, token: str | None = None,
          opener=None, timeout: float = 45.0) -> list[dict]:
    """One SODA3 query. The token, when present, travels in X-App-Token."""
    request = urllib.request.Request(
        V3_URL.format(dataset=dataset),
        data=json.dumps({"query": soql}).encode(),
        headers={"Content-Type": "application/json",
                 **({"X-App-Token": token} if token else {})},
        method="POST")
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------- sensors
def fetch_pedestrian_sensors(token: str | None = None, opener=None) -> pd.DataFrame:
    rows = _post(SENSOR_DATASET,
                 "SELECT id, name, lat, lon, firstdata, lastdata, granularity, "
                 "travelmodes, directional "
                 f"WHERE travelmodes LIKE '%{PEDESTRIAN_MODE}%'",
                 token, opener)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    df = df[df["lat"].between(40.0, 41.5) & df["lon"].between(-75.0, -73.0)]
    # The SoQL already filters server-side; re-check client-side so a relaxed
    # or failed server filter can never smuggle a bike-only sensor through.
    df = df[df["travelmodes"].astype(str).str.contains(PEDESTRIAN_MODE)]
    # ids repeat in the metadata; keep the row with the latest lastdata.
    df = (df.sort_values("lastdata").drop_duplicates("id", keep="last")
          .reset_index(drop=True))
    return df


def classify_distance(distance_m: float) -> str:
    if distance_m <= DIRECT_NEARBY_M:
        return QUALITY_DIRECT
    if distance_m <= NEARBY_REFERENCE_M:
        return QUALITY_REFERENCE
    return QUALITY_REMOTE


def nearest_pedestrian_sensor(sensors: pd.DataFrame, lat: float,
                              lon: float) -> tuple[pd.Series | None, float | None]:
    if sensors is None or sensors.empty:
        return None, None
    d = haversine_m(lat, lon, sensors["lat"].values, sensors["lon"].values)
    i = int(np.argmin(d))
    return sensors.iloc[i], float(d[i])


# ---------------------------------------------------------------- counts
def fetch_counts(sensor_id: str, start: date, end: date,
                 token: str | None = None, opener=None) -> pd.DataFrame:
    """
    Interval totals for one sensor and window: directional flow rows summed
    per timestamp, deleted rows excluded, pedestrian mode only.
    """
    soql = (
        "SELECT timestamp, direction, flowid, counts, status "
        f"WHERE sensor_id = '{sensor_id}' "
        f"AND travelmode = '{PEDESTRIAN_MODE}' "
        f"AND timestamp >= '{start.isoformat()}T00:00:00' "
        f"AND timestamp < '{(end + timedelta(days=1)).isoformat()}T00:00:00' "
        "LIMIT 100000")
    rows = _post(COUNT_DATASET, soql, token, opener)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "counts"])
    df = df[~df.get("status", pd.Series(dtype=str)).isin(EXCLUDED_STATUSES)]
    df["counts"] = pd.to_numeric(df["counts"], errors="coerce").fillna(0)
    # Source-local clock: parse naive, never tz-convert.
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    # THE aggregation rule: directional flows sum per interval.
    return (df.groupby("timestamp", as_index=False)["counts"].sum()
            .sort_values("timestamp").reset_index(drop=True))


# ---------------------------------------------------------------- aggregation
def daily_series(intervals: pd.DataFrame) -> pd.DataFrame:
    """Per-day totals with completeness; days under the coverage rule are
    marked invalid rather than silently undercounted."""
    if intervals.empty:
        return pd.DataFrame(columns=["day", "total", "observed", "coverage", "valid"])
    g = intervals.groupby(intervals["timestamp"].dt.date)
    out = pd.DataFrame({
        "total": g["counts"].sum(),
        "observed": g["timestamp"].nunique(),
    }).reset_index()
    out.columns = ["day", "total", "observed"]
    out["coverage"] = out["observed"] / INTERVALS_PER_DAY
    out["valid"] = out["coverage"] >= MIN_DAILY_COVERAGE
    return out


def service_period_series(intervals: pd.DataFrame,
                          window: tuple[int, int]) -> pd.DataFrame:
    """Per-day totals inside a service window (source-local hours)."""
    if intervals.empty:
        return pd.DataFrame(columns=["day", "total"])
    lo, hi = window
    hours = intervals["timestamp"].dt.hour
    sub = intervals[(hours >= lo) & (hours < hi)]
    out = sub.groupby(sub["timestamp"].dt.date)["counts"].sum().reset_index()
    out.columns = ["day", "total"]
    return out


def _quantiles(values: pd.Series) -> dict:
    if len(values) == 0:
        return {}
    return dict(mean=float(values.mean()), median=float(values.median()),
                p25=float(values.quantile(0.25)),
                p75=float(values.quantile(0.75)), n_days=int(len(values)))


def footfall_metrics(intervals: pd.DataFrame) -> dict:
    """Daily, weekday-profile and service-period statistics from valid days."""
    days = daily_series(intervals)
    valid = days[days["valid"]]
    valid_dates = set(valid["day"])
    out = dict(
        raw_days=int(len(days)), valid_days=int(len(valid)),
        coverage=float(valid["coverage"].mean()) if len(valid) else 0.0,
        daily=_quantiles(valid["total"]),
        by_weekday={}, periods={},
    )
    if len(valid):
        dow = pd.to_datetime(valid["day"]).dt.dayofweek
        for name, code in [("Monday", 0), ("Tuesday", 1), ("Wednesday", 2),
                           ("Thursday", 3), ("Friday", 4), ("Saturday", 5),
                           ("Sunday", 6)]:
            sub = valid[dow.values == code]["total"]
            if len(sub):
                out["by_weekday"][name] = float(sub.median())
        out["weekday_median"] = float(valid[dow.values < 5]["total"].median()) \
            if (dow < 5).any() else None
        out["weekend_median"] = float(valid[dow.values >= 5]["total"].median()) \
            if (dow >= 5).any() else None
    for period, window in SERVICE_PERIODS.items():
        series = service_period_series(intervals, window)
        series = series[series["day"].isin(valid_dates)]
        stats = _quantiles(series["total"])
        if stats:
            dow = pd.to_datetime(series["day"]).dt.dayofweek
            for name, code in [("Friday", 4), ("Saturday", 5), ("Sunday", 6)]:
                sub = series[dow.values == code]["total"]
                if len(sub):
                    stats[f"{name.lower()}_median"] = float(sub.median())
        out["periods"][period] = stats
    return out


# ---------------------------------------------------------------- top level
def measurement_window(sensor_lastdata: str,
                       today: date | None = None) -> tuple[date, date]:
    """Latest LOOKBACK_WEEKS complete weeks (Mon-Sun), never touching the
    current incomplete day or week."""
    limit = min(pd.Timestamp(sensor_lastdata).date(),
                (today or date.today()) - timedelta(days=1))
    # last complete Sunday on or before the limit
    end = limit - timedelta(days=(limit.weekday() + 1) % 7)
    start = end - timedelta(days=7 * LOOKBACK_WEEKS - 1)
    return start, end


def measure_location(lat: float, lon: float, token: str | None = None,
                     opener=None, today: date | None = None) -> PedestrianMeasurement:
    """The one entry point the UI calls. Returns a state, never raises."""
    try:
        sensors = fetch_pedestrian_sensors(token, opener)
    except Exception:
        return PedestrianMeasurement(
            quality=QUALITY_UNAVAILABLE,
            message="NYC DOT sensor data could not be reached.")
    sensor, distance = nearest_pedestrian_sensor(sensors, lat, lon)
    if sensor is None:
        return PedestrianMeasurement(
            quality=QUALITY_UNAVAILABLE,
            message="No pedestrian count sensors are published.")
    quality = classify_distance(distance)
    base = PedestrianMeasurement(
        quality=quality, sensor_id=sensor["id"], sensor_name=sensor["name"],
        distance_m=round(distance))
    if quality == QUALITY_REMOTE:
        base.message = (f"The nearest DOT pedestrian sensor "
                        f"({sensor['name']}) is {distance/1000:.1f}km away — "
                        f"too remote to describe this address.")
        return base

    start, end = measurement_window(sensor["lastdata"], today)
    try:
        intervals = fetch_counts(sensor["id"], start, end, token, opener)
    except Exception:
        base.quality = QUALITY_UNAVAILABLE
        base.message = "NYC DOT count data could not be reached."
        return base
    metrics = footfall_metrics(intervals)
    base.period_start, base.period_end = start.isoformat(), end.isoformat()
    base.raw_days = metrics["raw_days"]
    base.valid_days = metrics["valid_days"]
    base.coverage = metrics["coverage"]
    base.daily = metrics["daily"]
    base.periods = metrics["periods"]
    if base.valid_days < 14:
        base.quality = QUALITY_LOW_DATA
        base.message = (f"Only {base.valid_days} sufficiently complete days "
                        f"in the measurement window — too few to rely on.")
    return base


# ---------------------------------------------------------------- demand math
def required_capture_rate(daily_covers: float,
                          service_footfall: float) -> float | None:
    """What share of measured passers-by the capacity assumptions imply."""
    if not service_footfall or service_footfall <= 0:
        return None
    return daily_covers / service_footfall


def footfall_covers(service_footfall: float, capture_rate: float,
                    capacity_covers: float) -> float:
    """Walk-in demand from measured footfall × an ASSUMED capture rate,
    never exceeding seated capacity."""
    return float(min(max(service_footfall, 0.0) * max(capture_rate, 0.0),
                     capacity_covers))
