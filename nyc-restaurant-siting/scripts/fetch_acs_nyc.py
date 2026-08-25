#!/usr/bin/env python3
"""
Refresh data/acs_2024_nyc_tracts.csv from the official Census API.

Exactly five requests (one per NYC county), tract geography, 2024 ACS 5-Year.
The app never calls the Census API itself — it reads the CSV this writes.

    python scripts/fetch_acs_nyc.py

The key is looked up in the CENSUS_API_KEY environment variable, then in
.streamlit/secrets.toml. It is never printed and never written anywhere.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nycsiting import acs, config  # noqa: E402


def find_key() -> str | None:
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key
    secrets = Path(".streamlit/secrets.toml")
    if secrets.exists():
        with open(secrets, "rb") as f:
            return tomllib.load(f).get("CENSUS_API_KEY")
    return None


def main() -> int:
    key = find_key()
    if not key:
        print(acs.CensusKeyMissing(), file=sys.stderr)
        return 1

    print("Fetching 2024 ACS 5-Year tracts for the five NYC counties "
          "(5 requests)...")
    frame = acs.fetch_all_nyc(key)
    path = acs.save_cache(frame)

    print(f"\nWrote {path}")
    print(f"  tracts:            {len(frame):,}")
    for county, borough in acs.NYC_COUNTIES.items():
        n = (frame["county_fips"] == county).sum()
        print(f"  {borough:<15s} {n:>5,}")
    for col in acs.VARIABLES.values():
        missing = frame[col].isna().sum()
        print(f"  {col:<28s} missing: {missing:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
