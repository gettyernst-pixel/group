# Final issue register — 2026-08-26

Every issue found in the final audit pass, with the evidence that
established it and its status. Nothing here is closed on the strength of a
code change alone: an issue is CLOSED only when the check that originally
failed was re-run and passed.

Severity: **CRITICAL** would mislead a user about the data ·
**HIGH** materially degrades the product · **MEDIUM** visible but not
misleading · **LOW** cosmetic or deferred.

---

## ISSUE-01 · CRITICAL · CLOSED
### Shipped data predated the cuisine-vocabulary repair

**What was wrong.** `processed/restaurants.parquet` was built 2026-08-25
13:58, before `cuisines.DOHMH_2017_TO_2026` existed. Eleven cuisine labels
DOHMH retired between the 2017 archive and the 2026 extract therefore
survived in the panel, splitting each affected cuisine's cohort in two: the
retired label kept every closure and its successor kept every survivor.

**Evidence.** In the shipped panel, `Asian` showed 0 survivors out of 280,
`Bakery` 0/401, `CafÃ©/Coffee/Tea` 0/1016, `Latin (Cuban, Dominican, Puerto
Rican, South & Central American)` 0/525 — eleven labels at exactly 0%, each
paired with a successor at or near 100%.

**Why it mattered.** The 100% side was already neutralised at presentation
by `app._label_artifact` (which fires at a baseline ≥ 0.999). The 0% side
was not: a baseline of 0.0 is indistinguishable from an ordinary
catastrophic result, and every one of those labels — including the
encoding-damaged `CafÃ©/Coffee/Tea` — appeared in the concept picker. A user
selecting "Bakery" would have been shown a fabricated citywide track record
of zero survivors from 401 restaurants. This is precisely the failure the
product exists to avoid: an artifact of record-keeping presented as evidence
about a cuisine.

**How much it moved the answers.** Recomputing concept-fit bands on the old
and new panels: 57 of 195 neighbourhoods changed band for Bakery Products/
Desserts, 67 of 208 for Coffee/Tea, 18 of 202 for Italian. On the shipped
data no neighbourhood in New York could read Promising or Strong for a
bakery at all (0 Strong, 0 Promising, 37 Mixed → 8 Strong, 33 Promising,
22 Mixed). Italian was affected without being renamed itself: its
competitive set includes Pizza, and 296 `Pizza/Italian` establishments were
missing from it — Murray Hill–Kips Bay moved from 46 (Mixed) to 52
(Promising).

**Fix.** Rebuilt derived data with `python build_data.py` (21 s). The repair
was already correct in code; only the artifact was stale.

**Verified closed.** Zero retired labels remain in the panel. All eleven
merged cohort rates match the values documented on 2026-08-25 exactly
(Asian/Asian Fusion 84/364, Bakery Products/Desserts 333/734, Coffee/Tea
531/1547, Latin American 420/945, Pizza 574/1655, Sandwiches 223/748, Frozen
Desserts 89/359, Bottled Beverages 49/102, Soups/Salads/Sandwiches 10/52,
Southeast Asian 38/93, Steakhouse 56/93). Panel row count unchanged at
48,101; citywide cohort survival unchanged at 9,723/26,505 = 36.7%, which
proves the rename moved no establishment between categories.

**Regression guard.** `tests/test_data_integrity.py` reads the built parquet
rather than the code, and fails if a retired label survives, if a cohort of
n ≥ 30 has no survivors at all, if a fully-surviving cohort is not flagged
as a label artifact, if the concept picker offers a retired or mojibake
label, or if the parquet is older than the modules that define how it is
built. That last check is the one that would have caught this.

---

## ISSUE-01b · HIGH · CLOSED (2026-08-27)
### The corrected data reaches production only when committed

`processed/*.parquet` is **tracked in git**, which is how the deployed app
obtains its data. When this issue was written the ISSUE-01 rebuild existed
only in the working tree:

```
 M processed/restaurants.parquet
 M processed/locations.parquet
```

so the deployed app would have kept serving the panel with the fabricated
0%-survival cuisines until someone committed it.

**Verified closed by inspection, not by assumption.** This audit re-checked
the claim rather than repeating it — and it was already stale. The rebuilt
data was committed as `2fdcd41` ("Final data integrity and performance
fixes", 2026-08-26 15:56), after the parquet files were written at 14:42.
`git status processed/` is now empty: the working tree and the commit agree.

The committed panel was then read directly and checked against the figures
this register pins:

| Check | Committed data | Register |
|---|---|---|
| Panel rows | 48,101 | 48,101 |
| Cohort survival | 9,723 / 26,505 = 36.7% | 9,723 / 26,505 = 36.7% |
| Bakery Products/Desserts | 333 / 734 | 333 / 734 |
| Coffee/Tea | 531 / 1,547 | 531 / 1,547 |
| Pizza | 574 / 1,655 | 574 / 1,655 |
| Asian/Asian Fusion | 84 / 364 | 84 / 364 |
| Retired DOHMH labels present | **none** | none expected |

**Nothing blocks release on this item.** No commit or push was made by this
audit; the commit that closed it predates this pass.

**Contributing cause, still worth fixing separately.** `.gitignore` lists
`processed/`, but an ignore rule has no effect on files already tracked. The
repository therefore looks as though its data is not versioned while in fact
it is, which is very likely why a stale build survived a code fix: someone
correcting `cuisines.py` had no reason to think a data artifact also needed
committing. Either untrack the directory and build data at deploy time, or
drop the misleading ignore rule and treat the parquet as a versioned
artifact with its build recorded.

---

## ISSUE-02 · HIGH · OPEN (not fixed — see rationale)
### A map interaction discards the user's own pan and zoom

**What is wrong.** After the user pans or zooms the map themselves, changing
the restaurant filter, the layer, or the concept returns the map to the last
server-computed view. The user's exploration is lost.

**Root cause, measured.** `st.plotly_chart` computes its element identity
from the figure spec — Streamlit calls
`compute_and_register_element_id(..., key_as_main_identity=False,
plotly_spec=…)`, so a `key` does not stabilise it. Any real change to the
figure produces a new element id, and Streamlit unmounts the chart and
mounts a new one. Confirmed directly in the browser by tagging the DOM node
and the mapbox instance: after a data change both are replaced; after a
rerun that leaves the figure unchanged both survive. A fresh mapbox instance
starts at the spec's `center`/`zoom`, and `uirevision` — which does preserve
the camera across in-place updates — has no effect across a remount. Test:
panned to zoom 12.5 at (-73.85, 40.68), changed the data, map returned to
zoom 9.4 at (-73.97, 40.72).

**Why it is not fixed.** There is no supported way to keep the element
identity stable, so the remount cannot be prevented. The only workaround is
a client-side shim that saves the camera on `moveend` and restores it after
a remount, guarded by a fit token so a deliberate refit still wins. That was
prototyped and it works, but it depends on polling Streamlit's internal DOM
and on a race between the shim attaching and the map initialising — the
prototype showed exactly that race. Shipping DOM-manipulating JavaScript
that can fight the area auto-fit is a worse risk than the defect it cures,
in a release whose purpose is integrity.

**Mitigations shipped.** The rebuild is now cheap and no longer looks like a
glitch: the figure payload dropped from 1.81 MB to 34.3 KB, geometry is
served from browser cache, and the map container paints a branded ground so
the rebuild window reads as a loading state. A filter switch completes in
265 ms end-to-end with no empty frame.

**Recommended fix.** A custom Streamlit component that owns the mapbox
instance across reruns. That is the only way to update the map in place, and
it also removes the pulse entirely.

---

## ISSUE-03 · MEDIUM · CLOSED
### Two different loading indicators, chosen by which code path was slow

**What was wrong.** The branded chair appeared at four explicit call sites,
while `st.spinner` and the `show_spinner=` text on ten cached functions
rendered Streamlit's default grey spinner. Users saw one or the other
depending on internals.

**Fix.** Streamlit's own spinner is restyled to the chair app-wide
(`branding.spinner_css`, injected by `ui.inject_styles`). Every existing
contextual message is kept, and so is Streamlit's built-in 0.5 s delay,
which is what prevents fast work from flashing a loader. Style injection was
moved ahead of the first data load so the longest wait in the product is
branded too.

**Verified closed.** Confirmed in the browser: `[data-testid="stSpinnerIcon"]`
carries the chair, and the map ground renders during a rebuild. Pinned by
`tests/test_performance.py`.

---

## ISSUE-04 · MEDIUM · CLOSED
### Unscored neighbourhoods had no fill and no boundary

**What was wrong.** The thematic trace contained only the areas with a value
for the current layer (201 of 262 on concept fit), so the remaining 61 had
neither fill nor border. Scored areas appeared to float as disconnected
shapes, and a neighbourhood that simply was not evaluated looked like it did
not exist.

**Fix.** Every area is in the single trace. Areas without a value get an
explicit "Not evaluated" band — dim fill, full-strength border, and a hover
reading "not evaluated for this layer". The legend lists the band with its
count and carries a note: "Every neighbourhood is outlined; colour shows
what was measured."

**Verified closed.** All layers render one trace with 262 polygons. Concept
fit reconciles exactly: 9 Strong + 50 Promising + 35 Mixed + 107 Limited
evidence + 61 Not evaluated = 262. "Limited evidence" (measured, sample too
small) and "Not evaluated" (not measured here) remain distinct, so neither
can read as a poor result.

---

## ISSUE-05 · MEDIUM · CLOSED
### Cached reference data was deep-copied on every rerun

**What was wrong.** `st.cache_data` returns a deep copy on every hit. Asking
for the PLUTO lots frame cost 355–466 ms *per rerun* — on a cache hit, in
area mode, where lot data is never used. Locations cost 75–190 ms and the
geometry 57–64 ms. Over half of every warm rerun was spent duplicating data
that is never written to.

**Fix.** The read-only reference frames moved to `st.cache_resource`, which
returns the same object; lots, locations and pedestrian counts are also
loaded lazily at the point of use.

**Verified closed.** Warm area click 1,759 ms → 155 ms; filter switch
1,074 ms → 114 ms. Sharing is safe only while nothing mutates the frames, so
`tests/test_performance.py` fingerprints all four across a full interaction
sequence and fails if any changes.

---

## ISSUE-06 · MEDIUM · CLOSED
### The map re-sent ~1 MB of unchanging geometry on every interaction

**What was wrong.** The FeatureCollection was embedded in the figure, so
Streamlit shipped it across the websocket with every map change, and plotly
deep-copied it on every figure build (155 ms).

**Fix.** Geometry is attached to the trace *after* it is on the figure,
which skips plotly's copy for an identical result, and is published once to
`static/nta_display.<content-hash>.geojson` and referenced by URL. Display
coordinates are rounded to 5 decimals (~1.1 m — display only; analysis uses
the original rings).

**Verified closed.** Figure build 222 ms → 5.8 ms, serialisation 116 ms →
0.5 ms, payload 1.81 MB → 34.3 KB. Verified in a real browser: HTTP 200 for
the published file, 262 polygons drawn, hover resolved the correct
neighbourhood. A failed write falls back to embedding rather than returning
a URL that may not be fetchable — an unfetchable URL would draw an empty map
with no error, which is the one failure mode this product must not have.

---

## ISSUE-07 · LOW · CLOSED
### `isinstance` on a cached value broke the map after a code reload

**What was wrong.** `as_geometry()` recognised the geometry wrapper with
`isinstance`. Because the value is held in `st.cache_resource`, which
outlives a module reload, an edit during a running session left a cached
instance of the *previous* class. The map crashed with
`'DisplayGeometry' object has no attribute 'get'` — observed live.

**Fix.** Recognition is structural (`has .ref and .ids`). Pinned by a test
that reloads the module and passes a stale instance through.

---

## ISSUE-08 · LOW · CLOSED
### Selected-area outline width was duplicated instead of shared

`app.py` drew the selection outline at a hard-coded `width=2.5` while
`workspace_map.SELECTED_LINE_WIDTH` declared 3.5, so the emphasis constant
did not control the emphasis. Now sourced from the constant.

---

## ISSUE-11 · MEDIUM · CLOSED
### The cached area assignment was validated by row count alone

**What was wrong.** The restaurant → neighbourhood join is cached to
`data/restaurant_nta_2020.parquet` and was reused whenever the cached row
count matched the number of placed restaurants. A count match is not
identity: a rebuild that geocodes one new restaurant and loses another keeps
the count identical while both assignments are wrong, and nothing downstream
would notice — the same class of silent staleness as ISSUE-01.

**Found by.** Checking whether the cache was still valid after rebuilding
the panel for ISSUE-01. It *was* valid — the CAMIS sets matched exactly, and
44,201 placed restaurants were unchanged because only cuisine labels moved —
but the check that permitted the reuse could not have established that.

**Fix.** Validation now compares the CAMIS set. Cost is a few milliseconds
once per session.

**Verified closed.** Two tests: one asserts the shipped cache matches the
shipped panel; the other swaps a single CAMIS while keeping the count
constant and asserts the join is recomputed.

---

## ISSUE-09 · LOW · OPEN (accepted)
### Blank-cuisine cohort survives at 1.3%

973 establishments carry an empty cuisine label, of which 13 appear in the
2026 extract. The rate is almost certainly an artifact of blank labels being
back-filled in the newer extract rather than a real signal. It is **not**
user-visible: `cuisine_options()` excludes the empty label, so it can never
be selected as a concept or used as a competitive set. Recorded rather than
fixed because no user-facing surface reads it.

---

## ISSUE-12 · LOW · CLOSED (2026-08-27)
### The restaurant filter wrapped to two rows at 1280px

The three filter segments (Exact concept · Same cuisine · All restaurants)
sat on one row at 1440px wide but wrapped to two at 1280px, a common laptop
width. Purely cosmetic — all three options remained visible, labelled and
operable — and it was left open in the previous pass because re-tuning the
toolbar column ratios warranted its own verification rather than a late
adjustment.

**Verified closed by re-measurement, not by assumption.** The v8.2 toolbar
rework (moving the controls to a full-width row and targeting the real
`stButtonGroup` testid, with `flex: 1 0 auto; min-width: max-content` on the
options) resolved it as a side effect. Measured live at 1280px: all three
options report the same `y` (321) — one row — with widths 202/196/201 and a
right edge at 1262px against a 1280px viewport, so 18px of margin remains.
No label is truncated.

This entry is a reminder of the rule this audit ran under: the previous
report said OPEN, and it was wrong by the time it was read.

---

## ISSUE-10 · LOW · OPEN (deferred, unchanged from 2026-08-24)

- **L0** — location-history ratio is unbenchmarked against a base closure
  rate; conservative rather than wrong, and the evidence text states the raw
  numbers.
- **L1** — history uses the user's radius while live competition is fixed at
  750 m. Both are labelled; the page does not explain *why* they differ.
- **L2** — the `goneDark` 12-month threshold is documented in-app but the
  exact number is not user-visible.

---

## ISSUE-13 · MEDIUM · CLOSED (2026-08-27)
### The competition reference distribution was rebuilt on every score

**What was wrong.** `scoring.competitor_reference` builds the reference
distribution the competition component is scored against: it samples 1200
restaurants from the cuisine pool and counts each one's neighbours within
the radius. It ran in full on **every single score**, costing 1114 ms each
time — and it is a pure function of `(panel, cuisine_set, radius_m, sample,
seed)`. It does not depend on the site being scored, and with a fixed seed
it is deterministic, so every one of those rebuilds produced exactly the
value the previous one had produced.

**Why it is a bug and not a preference.** This is not "the code could be
faster". The same inputs were being recomputed to the same output, on the
user's critical path, for the single most expensive operation in a site
analysis. Nothing about the result depended on the work being repeated.

**Fix.** Memoised on that exact key, bounded at 64 entries. The subtle part
is the invalidation: the cache entry stores the panel object alongside the
result and is only reused when `cached[0] is panel`. A rebuilt panel is a
new object, so it can never inherit a stale distribution — which is what
would have turned a performance fix into a data-integrity bug.

**Verified closed.** `tests/test_v9_scoring_reference_memo.py` (5 tests):
the distribution is identical on a memo hit; a different panel object is
never served a cached reference; cuisine and radius separate the key; the
cache is bounded; and an end-to-end site score, band and component
breakdown are unchanged. `test_scoring.py` and
`test_validation_scenarios.py` re-run green (37 tests together).

**No scoring output changed.** Caching only.

---

## ISSUE-14 · LOW · CLOSED (2026-08-27)
### The map-fit pane constants had gone stale, so every area fit over-zoomed

**What was wrong.** `MAP_PANE_PX` tells the fitBounds maths how wide the map
pane is, and held `explore: 806, assess: 532` — measured in the browser
before v8.2. The v8.2 sticky two-pane layout narrowed both columns. Measured
live at a 1280px viewport, three samples per view, identical each time:
**explore 731, assess 507**. The constants were over by 10.3% and 4.9%.

**Effect.** A fit that aims to put the binding axis at 71% of the pane put
it at 78% in Explore, so a district's boundary sat closer to the frame edge
than `AREA_FIT_PADDING` is designed to keep clear. Still inside the stated
65–80% target, which is why nothing looked obviously broken — but drifting
in the direction the padding exists to prevent.

**Why no test caught it.** `test_whole_district_fits_with_context_on_every_
side` feeds `MAP_PANE_PX` into the fit *and* into its own expectation. It is
self-consistent by construction: it cannot see a pane constant that has
stopped describing the page. A test that derives its expectation from the
value under test can only ever confirm arithmetic.

**Fix.** Constants corrected to the measured widths, with the measurement
and its date recorded in the comment. Added
`tests/test_v9_map_pane_constants.py` (5 tests) checking what the old test
cannot: that the declared widths still match the browser measurement, that
their ratio matches the `st.columns` layout ratio the panes actually come
from, that both views imply the same underlying row width, that the pane
height matches the figure height, and that `compare` still renders no map.

**The guard was proven to fail.** Reintroducing 806/532 trips all three
width assertions with specific messages. A guard that has never been seen to
fail is not a guard.

**Also documented.** `MAP_PANE_PX["compare"]` never reaches a fit —
`render_compare_view` returns before the map is built. The entry is kept so
the fit sweep covers every declared view, and the new test fails if compare
ever grows a map without its pane being measured for real.

---

## ISSUE-15 · LOW · OPEN (accepted, reported not deleted)
### Eleven functions have no call site

Measured by running the whole suite under a `sys.setprofile` hook and then
searching every function the profiler never entered for a call site
anywhere in the repository. Eleven have exactly one occurrence — their own
`def` line:

`app.render_competition`, `app._an` (a duplicate of `narrative._an`, which
is the live one), `app.render_context_bar`, `app.render_hero`,
`app.panel_for_compare`, `app.neighborhood_to_nta`,
`locations.occupancy_history`, `workspace_map.add_nta_boundaries`,
`workspace_map.legend_for`, `ui.decision_hero` (reachable only from dead
`render_hero`), `ui.query_context` (callers are dead `render_context_bar`
and the flag-gated `simulate_page`).

**No functional loss.** Each is superseded by the code that replaced it. The
one worth checking explicitly was `add_nta_boundaries`, since NTA outlines
are a product requirement: the live thematic choropleth draws its own
boundaries with the same `BOUNDARY_WIDTH`/`BOUNDARY_LINE` constants, and the
running figure was confirmed in the browser to carry
`marker.line = {width: 1.2, color: "rgba(210,225,240,0.45)"}`.

**Not deleted.** Removing dead code is not a bug fix, and this release's
remit is integrity. Listed here so the next release can remove it
deliberately.

---

## ISSUE-16 · LOW · OPEN (accepted, no action)
### The test total is larger than the reachable product

`stage == "simulate"` is set **only by tests**. In production `app.py`
assigns `stage` exactly three values: `landing`, `confirm`, `results`. This
is deliberate and documented — `simulation_enabled()` gates the subsystem
behind a deployer flag (`ENABLE_SIMULATION`, or a session flag the
regression tests set) and the route guard redirects to `results` when the
flag is off.

So the financial-simulation subsystem is dark-shipped, not dead and not a
reintroduced Simulation UI. The only thing worth recording is the reporting
consequence: 57 tests (`test_financial_simulation.py` 20,
`test_financial_v2.py` 28, `test_sim_animation.py` 9) plus four cases in
`test_app_integration.py` exercise code no user reaches in the default
configuration. The 680-test total should be read with that in mind rather
than as a measure of the shipped surface.

---

## Summary

| Severity | Closed | Open |
|---|---:|---:|
| CRITICAL | 1 | 0 |
| HIGH | 1 | 1 |
| MEDIUM | 6 | 0 |
| LOW | 4 | 4 |

Closed in this pass: ISSUE-01b and ISSUE-12 — both verified by direct
re-checking, and **both were already stale when read**; plus ISSUE-13 and
ISSUE-14, found and fixed here. Opened and accepted: ISSUE-15, ISSUE-16.

**Nothing blocks release.** The item that previously did — ISSUE-01b, the
uncommitted data rebuild — was committed as `2fdcd41` before this pass
began, and the committed panel was read and matched against every figure
this register pins. No commit or push was made by this audit.

The single open HIGH (ISSUE-02) is a user-experience defect with a measured
root cause and a documented fix path; it does not affect any number the
product reports. No open issue causes the app to state something untrue
about the data.

**Two integrations could not be verified** because no credentials exist in
this environment: the Anthropic report narrative and Google Places. Both
degrade to labelled fallbacks, and the fallbacks are marked PASS separately
from the primary paths, which remain `NOT VERIFIED — EXTERNAL ACCESS
REQUIRED`. A working fallback is not evidence that the thing it replaces
works.
