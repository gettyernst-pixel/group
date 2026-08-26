"""
V6 performance diagnostic — measures the operations behind the workspace's
interaction latency. Development tool, not CI: run manually, compare runs.

    python scripts/benchmark_v6.py

Network-dependent paths (Google, geocoding, Claude) are deliberately absent —
they are cached per session and measured live in the app's dev trace.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from nycsiting import areas, config, cuisines, geometry, workspace_map  # noqa: E402


def timed(label: str, fn, n: int = 1):
    fn()  # warm module-level lazies out of the first measurement
    t0 = time.perf_counter()
    for _ in range(n):
        out = fn()
    ms = (time.perf_counter() - t0) * 1000 / n
    print(f"{label:<58s} {ms:9.1f} ms")
    return out


def main() -> None:
    panel = pd.read_parquet(config.RESTAURANTS_PQ)
    index = geometry.NTAIndex()
    assignment = geometry.assign_restaurants(panel, index)
    geojson = index.to_geojson()
    names = {c: f["name"] for c, f in index.features.items()}
    hover = pd.DataFrame({"name": pd.Series(names)})
    cuisine = "Japanese"

    print(f"panel={len(panel)} rows · {len(index.features)} NTAs\n")

    # -- map build ----------------------------------------------------------
    fit = areas.area_concept_fit(panel, assignment, cuisine)
    timed("map: band_choropleth (concept fit, display geometry)",
          lambda: workspace_map.band_choropleth(
              geojson, fit["band"], "concept_fit", hover), n=3)

    # -- area click: restaurant subset -------------------------------------
    def subset_via_merge(code="MN0603"):
        merged = panel.merge(assignment.rename("nta_2020"), left_on="camis",
                             right_index=True)
        inside = merged[(merged["nta_2020"] == code) & merged["seen_2026"]
                        & merged["lat"].notna()]
        compset = cuisines.competitive_set(cuisine)
        return (inside[inside["cuisine"].isin(compset)],
                inside[~inside["cuisine"].isin(compset)])

    similar, other = timed("area click: restaurant subset (merge per call)",
                           subset_via_merge, n=5)

    merged_once = panel.merge(assignment.rename("nta_2020"), left_on="camis",
                              right_index=True)

    def subset_pre_merged(code="MN0603"):
        inside = merged_once[(merged_once["nta_2020"] == code)
                             & merged_once["seen_2026"]
                             & merged_once["lat"].notna()]
        compset = cuisines.competitive_set(cuisine)
        return (inside[inside["cuisine"].isin(compset)],
                inside[~inside["cuisine"].isin(compset)])

    timed("area click: restaurant subset (pre-merged frame)",
          subset_pre_merged, n=5)

    # -- concept ranking ----------------------------------------------------
    t0 = time.perf_counter()
    areas.rank_concepts_for_area(panel, assignment, "MN0603")
    print(f"{'concept ranking: rank_concepts_for_area (one area, cold)':<58s} "
          f"{(time.perf_counter() - t0) * 1000:9.1f} ms")

    # per-cuisine tables cached across areas (the app's cached shape)
    active = panel[panel["seen_2026"] & (panel["cuisine"] != "")]
    candidates = [c for c, n in active["cuisine"].value_counts().items()
                  if n >= areas.MIN_CITYWIDE_CUISINE]
    tables = {}
    t0 = time.perf_counter()
    for c in candidates:
        tables[c] = areas.area_concept_fit(panel, assignment, c)
    warm_build = (time.perf_counter() - t0) * 1000
    print(f"{'concept ranking: all per-cuisine fit tables (once/session)':<58s} "
          f"{warm_build:9.1f} ms   ({len(candidates)} cuisines)")

    def rank_from_tables(code="BK0101"):
        rows = []
        for c, table in tables.items():
            if code not in table.index:
                continue
            row = table.loc[code]
            if row["band"] == "Limited evidence" or row["fit_index"] is None:
                continue
            rows.append(dict(cuisine=c, fit_index=float(row["fit_index"]),
                             band=row["band"]))
        rows.sort(key=lambda r: -r["fit_index"])
        return rows[:5]

    timed("concept ranking: next area from cached tables", rank_from_tables,
          n=5)

    # -- marker construction ------------------------------------------------
    def build_markers():
        fig = workspace_map.band_choropleth(geojson, fit["band"],
                                            "concept_fit", hover)
        if hasattr(workspace_map, "add_restaurant_markers"):
            return workspace_map.add_restaurant_markers(fig, similar, other)
        import plotly.graph_objects as go
        fig.add_trace(go.Scattermapbox(
            lat=other["lat"], lon=other["lon"], mode="markers",
            marker=dict(size=6, opacity=0.45),
            customdata=[[f"camis:{c}"] for c in other["camis"]],
            text=other["name"].str.title() + " · "
                 + other["cuisine"].replace("", "unspecified"),
        ))
        fig.add_trace(go.Scattermapbox(
            lat=similar["lat"], lon=similar["lon"], mode="markers",
            marker=dict(size=10),
            customdata=[[f"camis:{c}"] for c in similar["camis"]],
            text=similar["name"].str.title() + " · " + similar["cuisine"],
        ))
        return fig

    timed(f"markers: figure + {len(similar)}+{len(other)} restaurant points",
          build_markers, n=3)


if __name__ == "__main__":
    main()
