"""Distance helpers. Flat-earth is fine over a few hundred metres in one city."""
from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in metres."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_M * np.arcsin(np.sqrt(a))


def within_radius(df: pd.DataFrame, lat: float, lon: float,
                  radius_m: float) -> pd.DataFrame:
    """
    Rows within `radius_m`, with a `distance_m` column, nearest first.

    A bounding-box prefilter runs first. With ~48k restaurants and a Streamlit
    app recomputing on every widget change, trigonometry over the whole frame
    on each interaction is a visible pause; the box makes it imperceptible.
    """
    if df.empty:
        return df.assign(distance_m=pd.Series(dtype=float))
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(np.cos(np.radians(lat)), 1e-6))
    box = df[
        df["lat"].between(lat - dlat, lat + dlat)
        & df["lon"].between(lon - dlon, lon + dlon)
    ].copy()
    if box.empty:
        return box.assign(distance_m=pd.Series(dtype=float))
    box["distance_m"] = haversine_m(lat, lon, box["lat"].values, box["lon"].values)
    return box[box["distance_m"] <= radius_m].sort_values("distance_m")
