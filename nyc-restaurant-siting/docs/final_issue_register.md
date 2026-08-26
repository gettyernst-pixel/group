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

## ISSUE-01b · HIGH · OPEN (action required before deploy)
### The corrected data reaches production only when committed

`processed/*.parquet` is **tracked in git** (committed as "Add processed
data for Streamlit deployment"), which is how the deployed app obtains its
data. The ISSUE-01 rebuild therefore exists only in the working tree:

```
 M processed/restaurants.parquet
 M processed/locations.parquet
```

Until those are committed and pushed, the deployed app continues to serve
the panel with the fabricated 0%-survival cuisines. **No commit or push was
made** — this release's instructions reserve that decision.

**Contributing cause, worth fixing separately.** `.gitignore` lists
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

## ISSUE-12 · LOW · OPEN
### The restaurant filter wraps to two rows at 1280px

The three filter segments (Exact concept · Same cuisine · All restaurants)
sit on one row at 1440px wide but wrap to two at 1280px, which is a common
laptop width. Observed in the browser at both widths; not caused by this
release's changes. Not fixed here because the toolbar column ratios were
tuned deliberately in the previous release and are pinned by a test —
re-tuning them warrants its own verification pass across widths rather than
a late adjustment in an integrity release. Purely cosmetic: all three
options remain visible, labelled and operable.

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

## Summary

| Severity | Closed | Open |
|---|---:|---:|
| CRITICAL | 1 | 0 |
| HIGH | 0 | 2 |
| MEDIUM | 5 | 0 |
| LOW | 2 | 3 |

**The one action blocking release** is ISSUE-01b: the rebuilt data must be
committed, or the deployed app keeps serving the fabricated cuisine survival
rates that ISSUE-01 describes.

The single open HIGH (ISSUE-02) is a user-experience defect with a measured
root cause and a documented fix path; it does not affect any number the
product reports. No open issue causes the app to state something untrue
about the data.
