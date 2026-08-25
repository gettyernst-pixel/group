# Pedestrian data validation — manual reconciliation

**Sensor:** 300038509 "Emmons Ave" (Sheepshead Bay), pedestrian travel mode
**Date:** Sunday 2026-06-14 (one complete day: 96 × PT15M intervals present)
**Method:** raw flow rows fetched directly from the SODA3 endpoint with
`urllib` (no module code), hand-summed, compared against
`pedestrian_dot.fetch_counts` + `service_period_series`.

| Quantity | Manual (raw rows) | Module | Difference |
|---|---|---|---|
| Flow rows returned | 192 (96 intervals × 2 directional flows) | — | — |
| Daily total | **381** | **381** | **0** |
| Dinner window 17:00–22:00 | **114** | **114** | **0** |

## Count semantics confirmed on the live API (2026-08-24)

- Each 15-minute interval carries **two rows** — one per directional flow
  (`in` / `out`, distinct `flowid`s). The interval total is their **sum**
  (verified at 2026-06-14 18:00: in=4, out=5, total=9). No "total" flow row
  exists, so summing cannot double-count.
- `flowname` is null on older rows and populated on newer ones for the same
  flowid; aggregation therefore keys on `timestamp`, never `flowname`.
- Pedestrian rows carry `travelmode = "pedestrian"` (lowercase) and
  `status = "raw"`; bike data also shows `modified` and `deleted`. The module
  filters to the pedestrian mode and drops `deleted`.
- Timestamps are floating strings in DOT source-local time. The module parses
  them naive and never timezone-shifts, so service-window filters run on the
  sensor's own clock.

## Coverage of the network

Only **five distinct pedestrian sensor locations** exist citywide (Willis Ave,
High Bridge, Emmons Ave, Concrete Plant Park, plus paired ids at the same
coordinates). For most restaurant addresses the honest quality verdict is
`TOO_REMOTE`, and the module returns exactly that rather than stretching a
distant sensor over an unrelated block.
