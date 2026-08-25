#!/usr/bin/env python3
"""
One-shot ETL: raw CSVs -> processed/*.parquet.

Run once (about a minute, mostly PLUTO). The Streamlit app reads only the
parquet files, so it starts instantly and never touches the 442MB PLUTO CSV.

    python build_data.py
"""
from __future__ import annotations

import sys
import time

import pandas as pd

from nycsiting import config, context, panel, locations


def main() -> int:
    missing = [p.name for p in (config.HIST_2017, config.CUR_2026,
                                config.SNAP_2021, config.PREPERMIT, config.PLUTO)
               if not p.exists()]
    if missing:
        print("Missing required file(s):", ", ".join(missing), file=sys.stderr)
        return 1

    t0 = time.time()
    print("1/5  restaurant panel ...", flush=True)
    rest = panel.build_restaurants()

    print("2/5  address index (DOHMH + PLUTO) ...", flush=True)
    index = panel.build_location_index()
    rest = panel.attach_coordinates(rest, index)

    print("3/5  location table ...", flush=True)
    locs = locations.build_locations(rest)

    print("4/5  PLUTO lots ...", flush=True)
    lots = context.load_pluto_lots()

    print("5/5  pedestrian counts ...", flush=True)
    ped = context.load_pedestrian()

    # Parquet needs one type per column. Census tracts arrive as '005602'
    # strings from DOHMH and as floats from PLUTO's tract2010, so identifier
    # columns are pinned to string everywhere rather than left to inference.
    id_cols = ("bbl", "nta", "census_tract", "bin", "community_board")
    for frame in (rest, locs):
        for col in id_cols:
            if col in frame.columns:
                frame[col] = frame[col].astype("string")
    locs["cuisines_ever"] = locs["cuisines_ever"].apply(list)

    rest.to_parquet(config.RESTAURANTS_PQ, index=False)
    locs.to_parquet(config.LOCATIONS_PQ, index=False)
    lots.reset_index().to_parquet(config.LOTS_PQ, index=False)
    ped.to_parquet(config.PEDESTRIAN_PQ, index=False)

    placed = rest["lat"].notna()
    closed = rest["status"] == "closed"
    print(f"\nDone in {time.time() - t0:.0f}s")
    print(f"  restaurants          {len(rest):>8,}")
    print(f"    active (2026)      {rest['seen_2026'].sum():>8,}")
    print(f"    closed since 2017  {closed.sum():>8,}")
    print(f"    with coordinates   {placed.sum():>8,}  ({100*placed.mean():.1f}%)")
    print(f"    closed w/ coords   {(placed & closed).sum():>8,}  "
          f"({100*(placed & closed).sum()/closed.sum():.1f}%)")
    print(f"  storefronts          {len(locs):>8,}")
    print(f"    multi-vendor       {locs['is_multi_vendor'].sum():>8,}")
    print(f"  PLUTO lots           {len(lots):>8,}")
    print(f"  pedestrian sites     {len(ped):>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
