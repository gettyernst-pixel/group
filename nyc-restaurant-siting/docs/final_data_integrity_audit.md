# Final data-integrity audit — 2026-08-26

**Repository audited:** `group-repo/nyc-restaurant-siting` (the deployed copy)
**Method:** every claim below was re-derived by running code against the data,
not by reading the code that produces it. Where a claim could not be tested
from this machine it is marked **NOT VERIFIED — EXTERNAL ACCESS REQUIRED**
rather than assumed.

**Coverage limit, stated up front.** A parallel nine-way automated audit
(DOHMH semantics, geography, ACS, PLUTO/pedestrian, Google tiers, scoring
mathematics, placeholders, reporting, routing) was started alongside this
pass and was stopped after two hours without producing results. **None of
its findings are included here.** Everything in this document comes from the
direct verification described above, so the coverage is what one pass could
establish and check — not the breadth that nine independent auditors would
have given. Areas that pass here have been tested; areas not mentioned have
not been cleared.

---

## 1. Status

### READY WITH LIMITATIONS — one action required before deploy

**`processed/*.parquet` is tracked in git and is how the deployed app gets
its data.** The corrected panel described in §3 exists only in the working
tree; until it is committed and pushed, the live app keeps serving the
fabricated cuisine survival rates. No commit or push was made — that
decision is reserved. This is ISSUE-01b in the register.

The product's analytical guarantees hold: the panel is one row per
establishment, cohort survival is computed on a fixed 2011–17 cohort, small
samples are gated by Wilson intervals, and every unmeasured quantity reports
itself as unmeasured rather than as a low score. The limitations that keep
this from a plain READY are listed in §6 and tracked in
`final_issue_register.md`; the two that matter are that **live-integration
behaviour in the deployed environment cannot be verified from here**, and
that **a map interaction discards the user's own pan and zoom** (ISSUE-02).

One defect found in this pass was severe enough to have blocked release and
has been fixed: the shipped data was built before the cuisine-vocabulary
repair, so eleven cuisines reported a fabricated 0% survival rate (ISSUE-01).

---

## 2. What this pass verified, with evidence

### 2.1 Data grain and provenance

| Property | Verified value |
|---|---|
| Panel rows | 48,101 |
| Unique CAMIS | 48,101 (one row per establishment) |
| Active in 2026 extract | 31,319 |
| Absent since 2017 | 16,782 |
| With usable coordinates | 44,201 (91.9%) |
| Citywide 2011–17 cohort survival | 9,723 / 26,505 = 36.7% |

Sources, all NYC Open Data, all local files — no dataset or API was added:

| Source | File | Size | Role |
|---|---|---|---|
| DOHMH inspections (archive) | `DOHMH_..._Results.csv` | 146.1 MB | 2011-10-07 → 2017-08-26 cohort |
| DOHMH inspections (current) | `DOHMH_..._20260824.csv` | 159.2 MB | 2026 survival observation |
| DOHMH 2021 snapshot | `cleanDb_Aug_31cuisines.csv` | 4.5 MB | intermediate sighting |
| Pre-permit inspections | `Pre-Permit_..._20260824.csv` | 19.2 MB | retained, contributes no earlier date (verified 2026-08-24) |
| PLUTO | `Primary_Land_Use_..._20260824.csv` | 463.7 MB | lot context |
| DOT bi-annual counts | `Bi-Annual_Pedestrian_Counts_*.csv` | — | 114 measured sites |
| ACS 2024 | `acs_2024_nyc_tracts.csv` | 0.3 MB | income/population context |

### 2.2 Missing evidence never reads as a negative result

This is the product's central promise, and it was tested at the boundary
rather than inspected:

| Input | Result | Meaning |
|---|---|---|
| `competitor_saturation(0, None)` | `band=None`, "No comparable-density data." | absent density is not "Low competition" |
| `competitor_saturation(0, NaN)` | `band=None` | NaN is not zero |
| `opportunity_gap(None, None)` | `"Insufficient evidence"` | no gap claim without both components |
| area with `cohort_n = 0` | `persistence_rate = NaN` | never 0.0 |
| location with < 3 recorded tenants | component reported *unavailable* | one closure cannot become "high turnover" |
| cuisine cohort n = 2 | Wilson [0.09, 0.91] → neutral | indistinguishable from the citywide rate |

Counts that genuinely are zero (a neighbourhood with no comparable
restaurants) are filled with 0 and are correct; the *rates* derived from
them are NaN, not 0. That distinction was checked directly.

### 2.3 Scores are relative indices

The 0–100 figure is a relative index, never a probability of success. Its
weights (25/25/20/15/10/5) are editorial judgements, stated as such in the
Method page and in code. Bands carry the information; the number exists for
comparability between candidates.

### 2.4 Claude's boundary

Claude is used in exactly two places, both enforced by tests:
`plan_parser.py` (natural-language → structured plan fields) and
`report_writer.py` (prose editing of an already-computed payload). The
report writer strips any sentence containing a numeral and whitelists area
codes, so it cannot introduce or alter a quantity. With no key configured,
the app says so plainly — the confirm page reads "Parsed with the local
pattern parser (language parsing not configured)".

### 2.5 Map geometry joins the data correctly

- 262 NTA geometries, 262 unique ids, 0 duplicates.
- 0 thematic values without a matching geometry.
- Every layer renders **one** choropleth trace covering **all 262** areas —
  201 with a value, 61 in an explicit "Not evaluated" band with a dim fill,
  a full-strength border, and a hover that says "not evaluated for this
  layer".
- The concept-fit legend reconciles exactly: 9 Strong + 50 Promising +
  35 Mixed + 107 Limited evidence + 61 Not evaluated = 262.
- "Limited evidence" (measured, sample too small) and "Not evaluated"
  (not measured on this layer) are distinct bands with distinct wording, so
  neither can read as a poor result.

Area auto-fit was measured rather than eyeballed: for eight districts across
four boroughs, in both the Explore (920×640) and Assess (615×640) panes, the
whole district is contained and the binding axis lands at 71% of the pane —
inside the intended 65–80% band.

### 2.6 Display geometry is display-only

Coordinates in the published FeatureCollection are rounded to 5 decimals
(~1.1 m, finer than one screen pixel at any zoom the workspace allows).
Point-in-polygon and every analytic result run on the original full-precision
rings; this was verified by comparing source vertices against published ones
(max deviation ≤ 1e-5) and by a test that pins the two apart.

---

## 3. Defect found and fixed in this pass

**The shipped data predated the cuisine-vocabulary repair.** DOHMH renamed
eleven cuisine categories between the 2017 archive and the 2026 extract.
`cuisines.DOHMH_2017_TO_2026` maps each to its successor inside
`clean_label`, and the 2026-08-25 audit verified the repair — but
`processed/restaurants.parquet` in this repository was built on 2026-08-25
at 13:58, *before* that code, so the panel still carried the retired labels.

Observed in the shipped data:

| Retired label | cohort | Successor | cohort |
|---|---|---|---|
| Asian | 0/280 = **0%** | Asian/Asian Fusion | 84/84 = 100% |
| Bakery | 0/401 = **0%** | Bakery Products/Desserts | 333/333 = 100% |
| CafÃ©/Coffee/Tea | 0/1016 = **0%** | Coffee/Tea | 531/531 = 100% |
| Latin (Cuban, Dominican…) | 0/525 = **0%** | Latin American | 420/420 = 100% |
| …7 more | | | |

Both halves were artifacts. The 100% half was already neutralised at
presentation by `app._label_artifact`. **The 0% half was not**, and those
labels — including the mojibake `CafÃ©/Coffee/Tea` — were selectable
concepts. A user choosing "Bakery" would have been shown a real-looking
citywide track record of zero survivors out of 401.

**How much it moved the answers.** Concept-fit bands were recomputed for
every neighbourhood on the old and new panels:

| Concept | Neighbourhoods that changed band | Before → after |
|---|---|---|
| Bakery Products/Desserts | 57 of 195 | 0 Strong, 0 Promising, 37 Mixed → 8 Strong, 33 Promising, 22 Mixed |
| Coffee/Tea | 67 of 208 | 5 Strong, 52 Promising → 17 Strong, 54 Promising |
| Italian | 18 of 202 | 9 Strong, 50 Promising → 8 Strong, 55 Promising |

Bakery is the clearest demonstration of harm: on the shipped data **no
neighbourhood in New York could be Promising or Strong for a bakery**,
because half the bakery cohort was hidden under a retired label reading 0%
survival. Italian was affected without being renamed itself — its
competitive set includes Pizza, and 296 establishments labelled
`Pizza/Italian` were absent from it. Murray Hill–Kips Bay for an Italian
concept moved from 46 (Mixed) to 52 (Promising).

**Fix:** rebuilt the derived data with `python build_data.py` (21 s). After
the rebuild: zero retired labels remain in the panel, all eleven merged
cohort rates match the values documented on 2026-08-25 exactly (e.g.
Asian/Asian Fusion 84/364, Coffee/Tea 531/1547), panel row count is
unchanged at 48,101, and citywide cohort survival is unchanged at
9,723/26,505 = 36.7% — confirming the repair renames labels without adding,
dropping or reclassifying any establishment.

**Why nothing caught it:** all 596 tests passed against the corrupted panel,
because every test exercised the *code* and none checked the *artifact the
code produces*. `tests/test_data_integrity.py` now reads the built parquet
and fails if a retired label survives, if a cohort of n ≥ 30 has no
survivors, if a fully-surviving cohort is not flagged as a label artifact,
if the concept picker offers a retired or encoding-damaged label, or if the
parquet is older than the modules that define how it is built.

---

## 4. Previously-closed findings, re-verified

Each check below is the one that originally failed, re-run against today's
data. All eleven pass.

| ID | Original finding | Re-verified result |
|---|---|---|
| H1 | 475 restaurants at (0,0) passed as placed | 0 at (0,0); 0 outside NYC; coverage 91.9% |
| H2 | one closure ⇒ "High turnover" | `MIN_LOT_HISTORY = 3`; below it the component is unavailable |
| H3 | n=2 cuisine verdict at full confidence | Wilson(1/2) = [0.09, 0.91] → neutral |
| M1 | pre-permit claimed as a date improver | claim removed; dataset retained and labelled redundant |
| M2 | "Strong" competitor from 8 reviews | `MIN_REVIEWS_FOR_STRONG = 20` |
| M3 | fresh clone could not build | README lists required files and sources |
| M4 | no evidence-quality signal | evidence band rendered beside the headline |
| M5 | no traceability | developer trace available in Method |
| Vocab | renamed cuisines split their cohorts | 0 archive-only labels survive `clean_label` |

The eight remaining 2026-only labels (Basque, Chimichurri, Fusion, Haute
Cuisine, Lebanese, New American, New French, Vegan) are genuinely new
categories with no 2011–17 counterpart. They are correctly *not* remapped,
and `_label_artifact` neutralises their fit rather than presenting a 100%
survival rate as a track record. `Chinese/Japanese` (6/52 = 11.5%) was
checked and appears in **both** extracts, so its low rate is a real
measurement, not a taxonomy artifact.

---

## 5. Assumption register

Every assumption the analysis rests on, stated so a reader can disagree with
it:

1. **Absence from the 2026 extract means closure.** DOHMH publishes no
   closing dates. A restaurant that stopped being inspected but still trades
   is counted as closed. Stated in the UI wherever turnover is shown.
2. **The most recent cuisine label describes the establishment.** A venue
   that changed cuisine is counted under its latest label for its whole
   history.
3. **Establishment ≠ location.** Address normalisation merges variants of
   the same street; an empirical scan of 11,109 multi-tenant location keys
   found none with a coordinate spread beyond ~550 m.
4. **The 2011–17 and 2023–26 windows are disjoint**, so survival is a binary
   cohort question, never a duration comparison.
5. **Weights are editorial.** 25/25/20/15/10/5 are judgements, not fitted
   parameters. No outcome data exists to fit them against.
6. **Neighbourhood boundaries are 2020 NTAs.** Restaurants are assigned by
   point-in-polygon on original geometry, cached per CAMIS.
7. **ACS 2024 1-year estimates** carry sampling error that the app does not
   propagate into the score; income and population are context layers, not
   scored components.
8. **114 DOT pedestrian sites** cover a small fraction of the city. Areas
   without a site report pedestrian context as unmeasured.
9. **Google ratings are a live, third-party opinion**, not a measurement of
   trade. They sharpen the competition read; they never enter concept fit.
10. **A score is an index, not a probability.** No claim is made that a
    higher score raises the chance a restaurant succeeds.

---

## 6. NOT VERIFIED — EXTERNAL ACCESS REQUIRED

These could not be tested from this machine and must not be reported as
working:

1. **Live Google Places behaviour in the deployed app.** No
   `GOOGLE_MAPS_API_KEY` is configured in this working copy (no
   `secrets.toml`, no environment variable), so only the no-key path was
   exercised. That path was confirmed honest: it names the missing secret,
   states that the public-record analysis is unaffected, and falls back to
   measured inventory rather than inventing competitors.
2. **Live Claude parsing in the deployed app.** Same reason; the local
   pattern parser ran instead and labelled itself as such in the UI.
3. **Streamlit Community Cloud static file serving.** The map geometry is
   published to `static/` and referenced as `app/static/…`; this was
   verified end-to-end against a local Streamlit server (HTTP 200, polygons
   rendered, hover resolved the correct area) but not on Cloud. If static
   serving were unavailable there, `nta_display_geometry()` still returns a
   URL it has just written, so the failure mode would be an empty
   choropleth. Confirm the first deployed load before announcing the change.
4. **Cross-browser rendering.** Verified in one Chromium-based browser only.
5. **Concurrent multi-user behaviour.** The reference frames are now shared
   between sessions via `st.cache_resource`. Nothing in the request path
   writes to them — pinned by a test that fingerprints all four frames
   across a full interaction sequence — but this was tested single-process,
   not under real concurrency.

---

## 7. Performance, measured

Server-side script duration per interaction, median of repeated runs on
warm caches, same machine before and after:

| Interaction | Before | After | Change |
|---|---:|---:|---:|
| Cold start → workspace | 7,121 ms | 2,820 ms | −60% |
| Area click | 1,759 ms | 155 ms | −91% |
| Layer switch | 1,128 ms | 150 ms | −87% |
| Restaurant filter switch | 1,074 ms | 114 ms | −89% |
| Concept change | 1,361 ms | 199 ms | −85% |
| Explore ↔ Assess | 1,369 ms | 160 ms | −88% |

Component measurements behind those figures:

| Cost | Before | After |
|---|---:|---:|
| `load_lots()` per rerun (cache **hit**) | 355–466 ms | ~0 ms |
| `load_locations()` per rerun | 75–190 ms | ~0 ms |
| `nta_geojson()` per rerun | 57–64 ms | ~0 ms |
| `_layer_figure()` | 222 ms | 5.8 ms |
| Figure serialisation | 116 ms | 0.5 ms |
| Figure payload per interaction | 1.81 MB | 34.3 KB |

Three changes account for all of it, and each is pinned by a test in
`tests/test_performance.py`:

1. **`st.cache_data` → `st.cache_resource`** for the read-only reference
   frames. `cache_data` deep-copies its return value on every hit, so simply
   *asking* for the PLUTO lots frame cost up to 466 ms per rerun. Safe only
   while nothing mutates them, which is now a tested invariant.
2. **Geometry attached after the trace is on the figure.** Plotly deep-copies
   trace properties on attach; handing ~1 MB of nested lists to the
   constructor cost 155 ms per figure versus 0.9 ms for an identical result.
3. **Geometry published once and referenced by URL.** The shapes never
   change, but Streamlit re-sent them with every figure. They are now written
   to `static/nta_display.<hash>.geojson` and fetched once per browser. The
   filename carries a content hash so a cached copy can never go stale, and
   a failed write falls back to embedding rather than to an unfetchable URL.

Lots, locations and pedestrian counts are also loaded lazily now — an
area-only session never reads them at all.

---

## 8. Loading behaviour

The app previously showed **two** loading affordances: the branded chair for
four explicit call sites, and Streamlit's default grey spinner for
`st.spinner` and the `show_spinner=` text on ten cached functions. Which one
appeared depended on which code path happened to be slow.

Streamlit's spinner is now restyled to the chair mark app-wide, which keeps
every existing contextual message ("Analyzing concept fit…", "Looking up
nearby competitors…") and preserves Streamlit's built-in 0.5 s delay — the
mechanism that stops fast work from flashing a loader. Styles are injected
before the first data load, so the longest wait in the product shows the
branded loader too.

The map's "pulsing" was diagnosed rather than guessed at. `st.plotly_chart`
computes its element identity from the figure spec (`key_as_main_identity`
is `False`), so any real change mints a new element id. Measured in the
browser: on a data change the plot div **and** the mapbox instance are both
replaced; on a rerun that leaves the figure unchanged, both survive. The
rebuild cannot be avoided through the public API, so it was made cheap
(34 KB instead of 1.81 MB, geometry served from browser cache) and given a
branded ground — the map container paints its own dark ground and the chair
mark behind the chart, so the rebuild window reads as a deliberate loading
panel at exactly the map's size, with the column height held so nothing
jumps. Measured end-to-end in the browser, a filter switch now completes in
265 ms with no empty frame.
