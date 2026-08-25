#!/usr/bin/env python3
"""
Manual reconciliation for the ACS pipeline (docs/census_validation.md).

Run AFTER scripts/fetch_acs_nyc.py has produced the local CSV:

    python scripts/validate_census.py

Part 1 — three tracts (Manhattan, Brooklyn, Queens): fetches each tract's
population, median household income and median age STRAIGHT from the Census
API (independent single-tract queries, not the county batch) and compares
with the saved CSV. Required difference after numeric parsing: 0.

Part 2 — three NTAs: recomputes NTA population as the sum of component-tract
populations from the raw CSV and compares with nta_demographics(). Required
difference: 0. Also asserts no NTA-level column carries a "median" name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from nycsiting import acs, nta  # noqa: E402
from scripts.fetch_acs_nyc import find_key  # noqa: E402

CHECK_TRACTS = {
    "36061001800": "Manhattan (Census Tract 18, the Bowery)",
    "36047052900": "Brooklyn",
    "36081071600": "Queens",
}
CHECK_VARS = ["population", "median_household_income", "median_age"]
CHECK_NTAS = ["MN0302", "BK0101", "QN0201"]


def main() -> int:
    table = acs.load_cache()
    if table is None:
        print("No local ACS CSV yet — run scripts/fetch_acs_nyc.py first.",
              file=sys.stderr)
        return 1
    key = find_key()
    if not key:
        print(acs.CensusKeyMissing(), file=sys.stderr)
        return 1

    failures = 0
    print("Part 1 — tract values vs a fresh single-tract API call")
    for geoid, label in CHECK_TRACTS.items():
        state, county, tract = geoid[:2], geoid[2:5], geoid[5:]
        live = requests.get(acs.ACS_BASE, params={
            "get": ",".join(acs.VARIABLES), "for": f"tract:{tract}",
            "in": f"state:{state} county:{county}", "key": key,
        }, timeout=60).json()
        live_row = dict(zip(live[0], live[1]))
        saved = table[table["tract_geoid"] == geoid].iloc[0]
        print(f"  {geoid}  ({label})")
        for var, name in acs.VARIABLES.items():
            if name not in CHECK_VARS:
                continue
            api_val = float(live_row[var])
            csv_val = float(saved[name])
            ok = api_val == csv_val or (api_val < 0 and saved.isna()[name])
            failures += not ok
            print(f"    {name:28s} api={api_val:>12,.1f} "
                  f"csv={csv_val:>12,.1f}  {'OK' if ok else 'MISMATCH'}")

    print("\nPart 2 — NTA sums vs component tracts")
    eq = nta.load_equivalency()
    rollup = nta.nta_demographics(table, eq).set_index("nta_code")
    assert not any("median" in c for c in rollup.columns), \
        "an NTA column claims to be a median"
    for code in CHECK_NTAS:
        members = eq[eq["nta_code"] == code]["tract_geoid"]
        by_hand = table[table["tract_geoid"].isin(members)]["population"] \
            .fillna(0).sum()
        via_module = rollup.loc[code, "population"]
        ok = by_hand == via_module
        failures += not ok
        print(f"  {code}: tracts={len(members)}  sum_by_hand={by_hand:,.0f}  "
              f"module={via_module:,.0f}  {'OK' if ok else 'MISMATCH'}")

    print(f"\n{'ALL RECONCILED' if failures == 0 else f'{failures} FAILURES'}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
