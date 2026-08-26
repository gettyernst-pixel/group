"""
NTA geometry without a GIS stack: WKT parsing, point-in-polygon, and the
restaurant->NTA assignment.

Why this exists at all: the panel's `nta` column is 2010-vintage ('MN15'),
the polygon file is 2020 ('MN0302'), and shapely is not installed in this
environment. Both problems dissolve with one precomputed spatial join —
every restaurant's coordinates tested against the 262 polygons — which is
also more trustworthy than any code crosswalk. The result is cached to disk;
the join runs once, not per session.

The point-in-polygon test is standard ray casting with a bounding-box
prefilter. Holes (interior rings) are honoured: a point inside a hole is
outside the polygon.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, nta

ASSIGNMENT_CACHE = config.APP_DIR / "data" / "restaurant_nta_2020.parquet"


# ------------------------------------------------------------------ WKT
def parse_wkt_multipolygon(wkt: str) -> list[list[list[tuple[float, float]]]]:
    """
    'MULTIPOLYGON (((x y, ...)), ...)' -> [polygon][ring][(lon, lat)].

    Ring 0 of each polygon is the shell; later rings are holes. POLYGON input
    is accepted and treated as a one-polygon multipolygon.
    """
    text = wkt.strip()
    if text.upper().startswith("POLYGON"):
        text = "MULTIPOLYGON (" + text[text.index("("):] + ")"
    body = text[text.index("(") + 1:text.rindex(")")]

    polygons: list[list[list[tuple[float, float]]]] = []
    for poly_text in _split_level(body):
        rings = []
        inner = poly_text.strip()[1:-1]          # strip the polygon parens
        for ring_text in _split_level(inner):
            coords = []
            for pair in ring_text.strip().strip("()").split(","):
                x, y = pair.split()[:2]
                coords.append((float(x), float(y)))
            rings.append(coords)
        polygons.append(rings)
    return polygons


def _split_level(text: str) -> list[str]:
    """Split on commas at parenthesis depth zero."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p for p in parts if p.strip()]


# ------------------------------------------------------------------ PIP
def point_in_ring(lon: float, lat: float,
                  ring: list[tuple[float, float]]) -> bool:
    """Ray casting; boundary points may fall either way, fine at NYC scale."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_multipolygon(lon: float, lat: float, polygons) -> bool:
    for rings in polygons:
        if point_in_ring(lon, lat, rings[0]):
            if any(point_in_ring(lon, lat, hole) for hole in rings[1:]):
                continue
            return True
    return False


def simplify_ring(ring: list[tuple[float, float]],
                  tolerance: float = 0.00025) -> list[tuple[float, float]]:
    """
    Display-only decimation: drop vertices closer than ~tolerance degrees
    (~25m) to the last kept vertex. Analytical point-in-polygon always uses
    the ORIGINAL rings — this exists purely so the map does not ship an
    18MB figure to the browser on every rerun.
    """
    if len(ring) <= 8:
        return ring
    kept = [ring[0]]
    for pt in ring[1:-1]:
        last = kept[-1]
        if abs(pt[0] - last[0]) + abs(pt[1] - last[1]) >= tolerance:
            kept.append(pt)
    kept.append(ring[-1])
    return kept if len(kept) >= 4 else ring


# ------------------------------------------------------------------ features
class NTAIndex:
    """Parsed polygons + bounding boxes, built once from the polygon file."""

    def __init__(self, polygons_df: pd.DataFrame | None = None):
        df = polygons_df if polygons_df is not None else nta.load_polygons()
        self.features: dict[str, dict] = {}
        for row in df.itertuples():
            polys = parse_wkt_multipolygon(row.geometry_wkt)
            xs = [x for p in polys for x, _ in p[0]]
            ys = [y for p in polys for _, y in p[0]]
            self.features[row.nta_code] = dict(
                name=row.nta_name, borough=row.borough,
                residential=bool(row.is_residential), polygons=polys,
                bbox=(min(xs), min(ys), max(xs), max(ys)))

    def locate(self, lat: float, lon: float) -> str | None:
        for code, f in self.features.items():
            x0, y0, x1, y1 = f["bbox"]
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if point_in_multipolygon(lon, lat, f["polygons"]):
                return code
        return None

    def to_geojson(self, simplified: bool = True) -> dict:
        """
        The 262 areas as a FeatureCollection for choropleth rendering.
        Simplified geometry by default — display only; spatial membership
        always runs on the original rings.
        """
        features = []
        for code, f in self.features.items():
            polys = ([[simplify_ring(ring) for ring in poly]
                      for poly in f["polygons"]] if simplified
                     else f["polygons"])
            features.append({
                "type": "Feature", "id": code,
                "properties": {"nta_code": code, "name": f["name"],
                               "borough": f["borough"]},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[list(pt) for pt in ring]
                                     for ring in poly]
                                    for poly in polys]}})
        return {"type": "FeatureCollection", "features": features}


def assign_restaurants(panel: pd.DataFrame,
                       index: NTAIndex | None = None,
                       cache: Path = ASSIGNMENT_CACHE,
                       force: bool = False) -> pd.Series:
    """
    camis -> 2020 NTA code, from coordinates. Disk-cached: the join is
    deterministic, so it only ever needs to run when the panel changes.
    """
    placed = panel[panel["lat"].notna()]
    if cache.exists() and not force:
        stored = pd.read_parquet(cache)
        if len(stored) == len(placed):
            return stored.set_index("camis")["nta_2020"]

    index = index or NTAIndex()
    codes = [index.locate(row.lat, row.lon) for row in placed.itertuples()]
    out = pd.DataFrame({"camis": placed["camis"].values, "nta_2020": codes})
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache, index=False)
    return out.set_index("camis")["nta_2020"]
