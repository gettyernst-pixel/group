# Census validation — completed 2026-08-25

## Status: ALL RECONCILED (live)

`scripts/fetch_acs_nyc.py` ran with the user's key at 10:56 (5 county
requests → 2,327 tracts, matching the 2020 equivalency exactly).
`scripts/validate_census.py` then reconciled live:

## Part 1 — three tracts, fresh single-tract API calls vs saved CSV

| GEOID | Borough | population | median HH income | median age | diff |
|---|---|---|---|---|---|
| 36061001800 | Manhattan (Tract 18, Bowery) | 6,706 | $76,726 | 35.2 | **0** |
| 36047052900 | Brooklyn | 3,723 | $39,549 | 29.0 | **0** |
| 36081071600 | Queens | 0 | sentinel −666666666 → NaN | sentinel → NaN | **0** |

The Queens tract turned out to be a zero-population tract, which validated
the sentinel path live: the API's −666,666,666 is stored as missing, never
as a number.

## Part 2 — three NTAs, population as sum of component tracts

| NTA | tracts | sum by hand | module | diff |
|---|---|---|---|---|
| MN0302 | 10 | 47,185 | 47,185 | **0** |
| BK0101 (Greenpoint) | 15 | 39,784 | 39,784 | **0** |
| QN0201 | 9 | 30,464 | 30,464 | **0** |

No NTA-level column carries a "median" name (asserted by the script and by
`test_no_naive_average_of_income_medians`); income/age context indicators
are population-weighted and labelled `DERIVED_FROM_ACS_TRACTS`.

## Verified live earlier (keyless endpoints)

- All five ACS variable IDs against official metadata.
- Census coordinate geocoder returns 2020 tract GEOIDs (195 Bowery →
  36061001800).
- DOHMH's tract field is 2010-vintage: every 2020-equivalency miss checked
  was a tract split in 2020, so borrowed codes are used only when unchanged.
