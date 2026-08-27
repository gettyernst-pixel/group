# Final system QA report — 2026-08-27

The release audit for **Siting**. Everything below was re-verified in this
pass. Nothing is marked PASS because a previous report said so, because a
test exists, or because the code reads correctly — the standing rule for
this audit was to verify it now or say that it was not verified.

Where verification was impossible, the row says `NOT VERIFIED — EXTERNAL
ACCESS REQUIRED` and names what is missing. Where a fallback carried the
feature, the fallback is marked PASS and the primary path is marked
NOT VERIFIED, separately — a working fallback is not evidence that the thing
it replaces works.

---

## 1. Headline

| | |
|---|---|
| Regression suite | **680 passed, 0 failed, 0 skipped**, 2 warnings, 147.23 s — reproduced at 148.43 s |
| Functions inventoried | 378 (see `final_function_coverage.md`) |
| Functions with no call site | 11 |
| Bugs found this pass | 2 objective, both fixed and re-tested |
| Bugs found and **not** fixed | 0 objective; 1 pre-existing issue remains open by prior decision |
| Stale issues closed by re-checking | 2 — ISSUE-01b and ISSUE-12 |
| Blocking release | **none** |
| Secrets tracked or rendered | none found |

**Release recommendation.** Ship. The application is functionally sound and
its analytics are unchanged by this pass.

The item that previously blocked deployment no longer does, and finding
that out was itself a result of this audit's standing rule. The register
said ISSUE-01b was open — the corrected `processed/*.parquet` existing only
in the working tree, so the deployed app would keep serving the old panel.
Re-checked rather than repeated, it was already resolved: the rebuild was
committed as `2fdcd41` on 2026-08-26, `git status processed/` is clean, and
the committed panel reads back at 48,101 rows with cohort survival
9,723/26,505 = 36.7% and zero retired DOHMH labels — matching every figure
the register pins. ISSUE-12 was stale in the same way.

Two of the four issues closed in this pass were closed by discovering that
a previous report was out of date. That is the argument for the rule, not an
incidental detail: had this audit trusted its own register, it would have
reported a blocking defect that did not exist.

This audit made no commit and no push.

---

## 2. What was fixed in this pass

Two objective defects. Both were measured before and after, and the tests
around them were re-run.

### FIX-1 · `competitor_reference` recomputed a site-independent distribution on every score

`scoring.competitor_reference` builds the reference distribution that the
competition component is scored against: it samples 1200 restaurants from
the cuisine pool and counts neighbours within the radius for each. That
result depends only on `(panel, cuisine_set, radius_m, sample, seed)` — it
does **not** depend on the site being scored — and it is deterministic. It
was nevertheless rebuilt from scratch on every single score, costing
**1114 ms** each time.

Memoised on exactly that key. The cache entry stores the panel object
alongside the result and is reused only when `cached[0] is panel`, so a
rebuilt panel can never inherit a stale distribution — the failure mode
that would have made this a data-integrity bug rather than a performance
one. Bounded at 64 entries.

Verified: `tests/test_v9_scoring_reference_memo.py` (5 tests) proves the
distribution is identical on a memo hit, that a different panel object is
never served a cached reference, that cuisine and radius separate the key,
that the cache is bounded, and that an end-to-end site score, band and
component breakdown are unchanged. `test_scoring.py` and
`test_validation_scenarios.py` re-run green.

**No scoring output changed.** This is a caching fix, not a methodology
change.

### FIX-2 · The map-fit pane constants had gone stale, so every area fit over-zoomed

`MAP_PANE_PX` tells the fitBounds maths how wide the map pane is. It held
`explore: 806, assess: 532`, measured in the browser before v8.2. The v8.2
sticky two-pane layout narrowed both columns. Measured live at a 1280px
viewport, three samples per view, identical each time: **explore 731,
assess 507** — the constants were over by 10.3% and 4.9%.

Consequence: a fit that aims to put the binding axis at 71% of the pane
actually put it at **78%** in Explore, so a district's boundary sat closer
to the edge than the padding is designed to keep clear. Within the stated
65–80% target, but at the top of it, and drifting the wrong way.

Why the existing test did not catch it: `test_whole_district_fits_with_
context_on_every_side` feeds `MAP_PANE_PX` into the fit *and* into its own
expectation. It is self-consistent by construction and cannot see a pane
constant that has stopped describing the page.

Fixed the constants, and added `tests/test_v9_map_pane_constants.py` (5
tests) which checks the one thing the old test cannot — that the declared
widths are still in the ratio the `st.columns` layout actually uses, and
that both views imply the same underlying row width. **The guard was proven
to fail**: reintroducing 806/532 trips all three assertions.

Also documented there: `MAP_PANE_PX["compare"]` never reaches a fit, because
`render_compare_view` returns before the map is built. The entry is kept so
the fit sweep covers every declared view, and a test now fails if compare
ever grows a map without its pane being measured.

---

## 3. User journeys

Eleven journeys driven through the real widget surface with `AppTest` —
widgets, callbacks and reruns, not by writing session state. 52 of 53
checks passed; the one failure was an artifact of the harness (the test
called `add_to_comparison` in a bare context whose `st.session_state` is
not AppTest's) and the same rule was then confirmed through the UI.

| Journey | Input | Verified |
|---|---|---|
| A | *Italian restaurant in East Village* | routes to AREA, area `MN0303`, score and evidence render |
| B | *I want to open a brunch spot* | cuisine stays `None`, routes to DISCOVERY, no cuisine invented |
| C | *brunch spot in Gramercy* | concept-only plus area, routes to AREA `MN0602` |
| D | *Japanese restaurant in Murray Hill* | cuisine + named area routes to AREA, not DISCOVERY |
| E | *Chinese restaurant at 64 Wooster Street* | routes to SITE, address kept, site score renders, area context present with "View full area analysis" |
| F | remove the location chip | cuisine preserved, address cleared, falls back to DISCOVERY, no onboarding reset, no stale `selected_area`/`address` |
| G | remove the cuisine chip | cuisine cleared, concept and area kept, no cuisine leaks back |
| H | comparison from areas | first and second add commit, no duplicate area codes, max 3 enforced |
| K | New Search | stage returns to landing, plan/cuisine/address/area and the comparison tray all cleared |

The "no Afghan anywhere" assertions in B and C are deliberate: a
concept-only plan must not acquire a cuisine, and `Afghan` is the first
label alphabetically, which is what a naive default would pick.

## 4. Live browser pass

Chromium at 1280×820 against `localhost:8801`.

| Area | Result |
|---|---|
| Landing → Continue → Analyze | PASS — plan parsed, routed to area, workspace rendered |
| Workspace search (`Gramercy`) | PASS — re-analysed in place, concept preserved ("Keeps your current concept — Italian") |
| Toolbar | PASS — CONCEPT / LAYER / RESTAURANTS on one row, options at full width, no truncation |
| Plan chips | PASS — one row, removable, 32px tall; the duplicate nodes in the DOM are Streamlit's 0×0 tooltip measurement copies, not visible |
| Map figure | PASS — 262-NTA choropleth with geometry served by URL, 6 marker traces, boundaries drawn at 1.2px `rgba(210,225,240,0.45)` |
| Sticky map | PASS — natural position at scroll 0, then pinned at exactly `top: 12px` through all 1532px of scroll, height constant 640, never leaves the viewport |
| Marker click → restaurant card | PASS — *Bravo Pizza · Pizza · 115 East 14 Street, Manhattan · In 2026 records*; "← Back to area" dismisses it cleanly |
| Add to comparison ×2 | PASS — tray shows both, "Compare →" enabled at 2 |
| `Compare →` at one entry | PASS — control is present but `disabled`, with "1 / 3 selected — add one more to compare" |
| Remove one entry / Clear | PASS — tray drops to one, then disappears; "+ Add to comparison" is offered again; no exception |
| Compare view | PASS — *East Village vs Gramercy*, leaders section, exactly **one** report control |
| Report export | PASS (fallback) — built in 1082 ms, download control appeared. See §6: the language-model path did not run |
| Loading overlay | PASS — appears at 150 ms with the contextual message "ANALYZING YOUR PLAN…", clears at 750 ms |
| New Search | **PASS via `AppTest`, not via the browser** — see below |

### New Search: what was actually verified

The browser attempt was **inconclusive**, and is not recorded as a pass.
Clicking New Search in the pane left the workspace untouched. The cause was
environmental rather than a product defect: partway through the session the
browser pane stopped compositing, which had already produced two other
artifacts in this pass — a suspended WebGL context that rendered the map as
a blank rectangle, and `setTimeout` throttling that stretched a 0.9 s script
past 30 s. Streamlit's event delivery is impaired in the same state.

Rather than guess, the behaviour was re-verified deterministically with
`AppTest` on the current source. After clicking `nav_new`: `stage` returns
to `landing`, the landing text area is present, and `confirmed_plan`,
`cuisine`, `address`, `selected_area`, `plan_confirmed`, `workspace_mode`,
`workspace_view`, `report_pdf` and the comparison list are all absent from
session state. No exception. That matches journey K.

So New Search is verified — by the instrument that could measure it. This
row is deliberately not merged into the browser table above.

### Responsive

Measured by layout geometry, which is computed even when the pane is not
compositing.

| Viewport | Map pane | Restaurant filter | Plan chips | Horizontal overflow |
|---|---|---|---|---|
| 1440 × 900 | 827 × 640 | one row | — | none |
| 1280 × 820 | 731 × 640 | one row, right edge 1262 | one row | none |
| 1152 × 800 | 654 × 640 | one row, right edge 1134 | one row | none |

No horizontal overflow at any width tested, and nothing wraps or truncates.

One consequence worth stating: `MAP_PANE_PX` is measured at 1280, which
`app.py` documents as the narrowest desktop window the product targets.
Below that the pane is narrower than the constant, so the fit over-zooms
slightly — at 1152 the binding axis reaches about 79% of the pane against
the 65–80% target. Still inside tolerance, at a width outside the stated
target range. Assuming the narrow case remains the safe direction: a wider
window simply leaves more margin around the same content.

### The overlay's covering guarantee, tested directly

The overlay is `position: fixed; inset: 0; z-index: 999985` with
`rgba(7,17,31,0.86)` and `backdrop-filter: blur(7px)`. Probed at 77 points
on a 120px grid across the viewport: **every point hit-tests to the overlay**,
and its box is exactly 1280×820. Elements rendering behind it are therefore
covered, not visible — an earlier count of "stray expanders while the
overlay was in the DOM" was counting DOM presence, not visibility, and is
not a defect.

One honest limitation: the overlay is deliberately `opacity: 0` for its
first 260 ms (`animation: jx-overlay-in 160ms ease-out 260ms forwards`) so
that fast work never flashes a full-screen cover. During that window the
page underneath remains visible, by design and for the same reason
Streamlit delays its own spinner by 500 ms. Hit-testing still blocks
interaction throughout, because an `opacity: 0` element still receives
pointer events.

### A measurement that nearly became a false failure

Three separate attempts to click a restaurant marker produced no selection,
which looked like a broken feature. Instrumenting plotly's own event
emitter showed why: `plotly_hover` fired correctly at the exact same
coordinate, but `plotly_click` did not — plotly's mapbox hit-testing needs a
mousemove onto the target before a click registers. Hovering first and then
clicking fired `plotly_click` **and** `plotly_selected` with
`camis:41485091`, and the card rendered. The defect was in the harness. It
is recorded here because the opposite mistake — reporting a harness artifact
as a product bug — is as damaging as hiding a real one.

## 5. Performance

Measured warm, median of repeated runs.

| Interaction | Median |
|---|---|
| Area click | 171 ms |
| Layer switch | 172 ms |
| Restaurant filter | 174 ms |
| Explore ↔ Assess | 174 ms |
| Add to comparison | 135 ms |
| Open comparison | 164 ms |
| PDF build | 137 ms |
| Site analysis | 216 ms |
| Radius change (new radius) | ~1.4 s |
| App startup | 1469 ms |
| Report export, end to end (browser) | 1082 ms |

The radius change is the one interaction over a second, because a new
radius invalidates the neighbour counts that every competitor calculation
depends on. It is covered by the global overlay with a named message.

## 6. External integrations

| Integration | Status | Evidence |
|---|---|---|
| Anthropic (report narrative) | **NOT VERIFIED — EXTERNAL ACCESS REQUIRED** | No `ANTHROPIC_API_KEY` in the environment and no `.streamlit/secrets.toml`. The live export ran the deterministic-template path. |
| Anthropic fallback | **PASS** | Report built in 1082 ms and labelled itself honestly: *"Narrative written from deterministic templates (language model unavailable) — all analytics identical."* |
| Google Places | **NOT VERIFIED — EXTERNAL ACCESS REQUIRED** | No key configured; `google_api_key()` returns `None` and the competitor-landscape panel degrades rather than fabricating. |
| Geocoding (GeoSearch) | **PASS, with an observed outage** | Address lookups resolved during this pass. An HTTP 503 seen earlier in the session was handled by `render_address_failure` with in-workspace recovery rather than an error page. |
| DOHMH / ACS / PLUTO / DOT | **PASS** | Local extracts; joins and derived values validated in the dedicated documents in this directory. |

The two `NOT VERIFIED` rows are the honest status. The fallbacks work and
are labelled, which is a different claim from the primary paths working,
and this report does not merge the two.

## 7. Secrets

- `ANTHROPIC_API_KEY` is not set in this environment.
- No `.streamlit/secrets.toml` exists; only `secrets.toml.example`, which is
  tracked and contains no literal `sk-ant-` or `AIza` values.
- `.gitignore:5` covers `.streamlit/secrets.toml`.
- No tracked file matches a live key pattern.
- `st.secrets` is never passed to `st.write`, `st.markdown`, `st.caption`,
  `st.text` or `print` — no code path can render a secret.

No secret values are reproduced in this document.

## 8. Dead code

Eleven functions have no call site anywhere in the product. None of them
affects behaviour; all are superseded by the code that replaced them, and
they are reported rather than deleted because deletion is not a bug fix and
this release's remit is integrity, not tidying.

| Function | Superseded by |
|---|---|
| `app.render_competition` | the workspace competitor panel |
| `app._an` | duplicate of `narrative._an`, which is the live one |
| `app.render_context_bar` | the plan-chip row |
| `app.render_hero` | `render_workspace` panel header |
| `app.panel_for_compare` | `comparison` module |
| `app.neighborhood_to_nta` | `areas` resolution |
| `locations.occupancy_history` | not surfaced since the location detail view was removed |
| `workspace_map.add_nta_boundaries` | the thematic choropleth draws its own boundaries with the same constants |
| `workspace_map.legend_for` | in-figure legend annotation |
| `ui.decision_hero` | reachable only from dead `render_hero` |
| `ui.query_context` | callers are dead `render_context_bar` and flag-gated `simulate_page` |

## 9. The simulation subsystem

`stage == "simulate"` is set **only by tests**. No UI control anywhere
assigns it — `app.py` assigns `stage` exactly three values in production:
`landing`, `confirm`, `results`. This is deliberate and documented:
`simulation_enabled()` gates the whole subsystem behind an explicit
deployer flag (`ENABLE_SIMULATION` in secrets, or a session flag the
regression tests set), and the route guard redirects to `results` and
reruns when the flag is off.

So this is **not** dead code and **not** a reintroduced Simulation UI. It
is a dark-shipped subsystem: 23 module functions plus three `app.py` render
functions, kept correct by 57 dedicated tests. The only thing worth saying
plainly is that those 57 tests inflate the 680 total relative to the
surface a user can actually reach.

## 10. Known issues carried into the release

**ISSUE-01b · HIGH · CLOSED in this pass.** The register carried it as the
one item blocking deploy: the ISSUE-01 data rebuild existing only in the
working tree. Re-checked rather than repeated, it was already resolved —
committed as `2fdcd41`, working tree clean, and the committed panel verified
row-for-row against the register's pinned figures (48,101 rows; 9,723/26,505
= 36.7%; Bakery 333/734; Coffee/Tea 531/1,547; Pizza 574/1,655;
Asian/Asian Fusion 84/364; no retired DOHMH labels).

The contributing cause is still worth addressing separately, and remains
open as a note on that issue: `.gitignore` lists `processed/`, but an
ignore rule does nothing for already-tracked files, so the repository looks
as though its data is unversioned when it is not. Either untrack the
directory and build at deploy time, or drop the misleading rule and treat
the parquet as a versioned artifact.

**ISSUE-02 · HIGH · not fixed, by decision.** A map interaction discards
the user's own pan and zoom, because `st.plotly_chart` derives element
identity from the figure spec, so any figure change remounts the chart and
a `key` cannot stabilise it. The only workaround is a client-side camera
shim that polls Streamlit's internal DOM and races the map's
initialisation; shipping that in an integrity release was judged the worse
risk. The mitigations shipped in v7–v8 stand: the figure payload is 34.3 KB
rather than 1.81 MB, geometry is browser-cached, and the container paints a
branded ground so the rebuild reads as a loading state.

## 11. What this audit did not do

Stated plainly so the coverage above is not read as more than it is.

**A parallel eight-lane audit did not finish.** A background workflow was
started to audit data ingestion, ACS/PLUTO, calculations, comparison/PDF,
safety, copy claims, integrations and map geometry in parallel, with an
adversarial verification phase. It never completed and was stopped; its
transcript was not recoverable. **Nothing in this report comes from it.**
Every finding here was produced directly and is traceable to a measurement
recorded above. The lanes it would have covered are covered by the existing
validation documents plus this pass, but a second independent sweep of
those areas did not happen.

**Eleven reachable functions were not entered** by either the suite or the
browser pass. They are listed by name with the missing state in
`final_function_coverage.md`. Most need a failure condition (a geocode
error, an ambiguous address) or a developer flag.

**Two external integrations could not be exercised** — no credentials
exist here. Their fallbacks were verified; the primary paths were not.

**Coverage is entry, not correctness.** A `PASS` in the coverage document
means a function ran without failing, not that its behaviour is fully
specified.

## 12. Documents

| Document | Contents |
|---|---|
| `final_function_coverage.md` | all 378 functions, measured execution, per-function verdict |
| `final_issue_register.md` | every issue with its evidence and closure status |
| `final_data_integrity_audit.md` | panel construction, cohort survival, cuisine vocabulary |
| `census_validation.md`, `pedestrian_validation.md` | ACS and DOT derivations |
| `financial_methodology_sources.md`, `financial_validation_v2.md` | the flag-gated financial engine |
| `simulation_validation.md` | simulation scenarios |
