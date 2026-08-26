# Application audit — 2026-08-24

Scope: full pipeline (raw CSV → panel → analysis → scoring → narrative → UI),
verified by independent computation rather than code reading. Every numbered
claim below was reproduced by a script during the audit; the commands live in
the git history of this commit.

## 1. What currently works correctly

- **Data grain.** Panel is exactly one row per CAMIS (48,101 rows, 48,101
  unique). No count anywhere is inflated by inspection/violation rows.
- **Address normalization.** 10/10 adversarial street pairs behave (merge the
  same street, keep different streets apart). Empirical false-merge scan:
  of 11,109 location keys holding >1 restaurant, **zero** have coordinate
  spread >~550m — no evidence of distinct places being merged.
- **Dates.** No 1900-sentinel leaks into `first_observed` (0 rows <1990); no
  impossible spans (0 rows >15y). Sentinel handled at parse time.
- **Haversine.** Matches an independent implementation to 4 decimal places on
  3 test pairs; lat/lon order correct (nearest match to 195 Bowery is 189
  Bowery at 38m).
- **Radius enforcement.** 500m query returns max distance 499.5m; Google
  results are re-filtered by our own distance, not trusted to locationBias.
- **Independent headline verification (item 26).** For 195 Bowery / Italian /
  500m, a from-scratch recount over the raw 2026 CSV (own reader, own
  haversine, first-row-per-CAMIS): **580 establishments, 45 Italian**.
  Pipeline: **580, 45**. Exact match.
- **Duration language.** UI says "observed", never lifespan; survival is the
  2011-17→2026 binary, not duration comparison (which the two disjoint
  3-year windows would bias).
- **Multi-vendor venues** (food halls, stadiums, airports) are detected by
  peak concurrent occupancy and excluded from turnover claims.
- **Competitor strength A/B (item 13).** 4.6★×4,000 reviews outranks
  4.9★×8 reviews (87.7 vs 73.5). Closed-permanently excluded; subject site
  excluded by address, not distance.
- **PLUTO join.** 6/6 sampled BBL joins land on the correct lot (house-number
  offsets like 4115→4109 are one lot spanning several numbers).
- **Security.** No secrets tracked; no key material in any tracked file; key
  never rendered, logged, or hashed into cache keys.
- **Google failure modes.** All 11 failure classes return a message, never a
  traceback; core analysis renders regardless (tested).
- **Causal language.** Statements are associational ("observed", "compared
  with"); comparisons carry sample sizes and Wilson intervals in analysis.py.

## 2–4. Issues discovered

### HIGH

- **H1 — 475 restaurants at (0,0) pass as "placed".** The 2026 extract uses
  0,0 for ungeocodable rows; the panel keeps them as real coordinates
  (`geo_source='self'`). They sit outside NYC, so they silently vanish from
  every radius query, and — worse — having non-null lat blocks
  `attach_coordinates` from recovering them by address (BARBETTA, 321 W 46
  St, is among them and is address-resolvable).
- **H2 — one closed restaurant ⇒ "High turnover".** `location_history` scores
  risk=100 from n=1 (ever=1, closed=1). This is precisely the "one Italian
  restaurant closed → location kills restaurants" fallacy the product must
  not commit; it can become the headline and the Main reason for caution.
- **H3 — cuisine verdict ignores sample size and contradicts its own page.**
  `cuisine_track_record` with n=2 produces "Strong" (both survived) or
  "Weak" (both gone) at full confidence, while the Wilson-guarded comparison
  cards below correctly say "not distinguishable". Two layers of the same
  report disagree.

### MEDIUM

- **M1 — pre-permit dataset contributes nothing.** Its dates are already in
  the main 2026 file (0 rows where pre_first is strictly earliest; 11,440
  ties). Methodology text claims it provides "an earlier sighting" — an
  unsupported statement about a dataset's contribution.
- **M2 — "Strong" competitor label from 8 reviews.** Ranking is correct, but
  a 4.9★×8-review place still crosses the Strong threshold (73.5). A rating
  on <20 reviews is too unstable to ground that label.
- **M3 — reproducibility gap.** All raw CSVs are gitignored; a fresh clone
  cannot run `build_data.py`. README does not say which files are needed or
  where they come from (build_data does name missing files at runtime).
- **M4 — no evidence-quality signal.** A verdict built from 100% of evidence
  weight and rich local history looks identical to one built from 50% and
  n=3.
- **M5 — no traceability.** No way to inspect the chain (matched CAMIS,
  intermediate components, Google reasons) for a given address.

### LOW

- **L0 — location-history ratio is unbenchmarked.** The component scores raw
  closed/ever without comparing to a base closure rate, so 7/30 gone (below
  the citywide norm) reads "Mixed" rather than "Stable". Conservative rather
  than wrong — the evidence text states the numbers — but a benchmarked
  version would be fairer to stable high-traffic addresses. Deferred.
- **L1 — two radii on one page.** History uses the sidebar slider
  (200–1500m); live competition is fixed at 750m. Both are labelled, but the
  page never says *why* they differ.
- **L2 — `goneDark` 12-month heuristic** is documented in-app but the exact
  threshold is not user-visible.
- **L3 — price slider** does not display its current value prominently.

## 5. UI/output inconsistencies

Only H3 (verdict vs comparison cards). Counts, addresses, radius labels, and
recommendation text all traced to their sources; stale-state check passed
(address changes re-key every cached step; radius slider re-runs analysis).

## 6. Scoring audit (item 18)

Weights (25/25/20/15/10/5) are **editorial judgements, not fitted values** —
already stated in code comments, now also stated in the UI methodology. The
0–100 fit number implies more precision than the weights can defend; bands
carry the real information. Retained the number for comparability between
addresses, with the caveat surfaced in methodology.
Scenario tests: favourable-everything ⇒ Strong fit; adverse-everything ⇒ High
risk; sparse data ⇒ "Not enough data" + components read "Not measured".
Behavior verified in tests.

## 9. Fixes applied (this commit)

- H1: 0,0 treated as missing at parse; address-based recovery now applies.
- H2: `location_history` requires ≥3 restaurants on record; below that the
  component reports "too little history at this address" and is excluded.
- H3: `cuisine_track_record` first asks Wilson whether the local rate is
  distinguishable from the citywide rate; when it is not, the component is
  neutral (50) with "within sampling noise" as its evidence, matching the
  comparison cards exactly.
- M1: pre-permit no longer claimed as a date-improver; kept in ETL, labelled
  "redundant with the main extract (verified)".
- M2: Strong label requires ≥20 reviews; score unchanged, capped label noted.
- M3: README reproducibility section lists required files + sources.
- M4: evidence-quality indicator (Strong/Moderate/Limited) beside the hero,
  from measured weight coverage + local sample sizes + live-data presence.
- M5: developer trace expander (sidebar toggle) dumps the full chain.

## 10. Validation tests

- 203-test suite before fixes; 219 after (guards, evidence quality, strength
  label floor, three-scenario final validation).
- Post-fix re-audit executed: H1 (0 zero-coords, 0 outside NYC), H2 (n=1
  unavailable), H3 (n=2 neutral at 50), M2 (4.9×8 reviews → Moderate),
  M4 (sparse → Limited) — all confirmed FIXED by rerunning the original
  failing checks. Coordinate coverage restated honestly: 91.9% (the prior
  92.9% counted 475 fake placements).
- One test premise was corrected by the data during validation: 42 Broadway
  (7 of 30 tenants gone) is *below* the citywide closure norm and reads
  neutral, not churning; the genuine-churn scenario now uses 348 Bowery
  (4 of 5 gone), which scores strictly worse than the same query with
  unknown history.
- Independent recount (item 26) — exact match, documented above.
- Final validation: three scenarios traced end-to-end (see
  `tests/test_validation_scenarios.py`): dense Manhattan corner, Queens
  storefront with history, sparse outer-borough address.

# Addendum — 2026-08-25: DOHMH vocabulary rename repair

DOHMH revised its cuisine vocabulary between the 2017 archive and the 2026
extract: 11 labels exist only in the archive, 17 only in the extract. The
panel keeps each restaurant's most recent label, so for a renamed category
every closure kept the legacy label and every survivor was relabelled — the
legacy label read 0% cohort survival and its successor 100%, in both cases
a taxonomy artifact. Previously guarded only at presentation level
(`app.py _label_artifact`, baseline ≥ 0.999).

Fix: `cuisines.DOHMH_2017_TO_2026` maps each archive-only label to its 2026
successor, applied inside `clean_label` so the panel speaks one vocabulary.
Every pair was verified by matching CAMIS across the two files (fraction of
matched establishments carrying the proposed successor is in the map's
comments; weakest is 'Soups & Sandwiches' at 2/6, kept on name
correspondence). 'Delicatessen'→'Sandwiches' and 'Pizza/Italian'→'Pizza'
are retirements into pre-existing labels, mapped on the same cohort
argument.

Citywide 2011-17 cohort rates, before → after (survived/total):

| Category (2026 label)     | legacy label before | successor before | after |
|---------------------------|--------------------|------------------|-------|
| Asian/Asian Fusion        | 0/280 = 0%         | 84/84 = 100%     | 84/364 = 23.1% |
| Bakery Products/Desserts  | 0/401 = 0%         | 333/333 = 100%   | 333/734 = 45.4% |
| Bottled Beverages         | 0/53 = 0%          | 49/49 = 100%     | 49/102 = 48.0% |
| Coffee/Tea                | 0/1016 = 0%        | 531/531 = 100%   | 531/1547 = 34.3% |
| Frozen Desserts           | 0/270 = 0%         | 89/89 = 100%     | 89/359 = 24.8% |
| Latin American            | 0/525 = 0%         | 420/420 = 100%   | 420/945 = 44.4% |
| Pizza                     | 0/296 = 0%         | 574/1359 = 42.2% | 574/1655 = 34.7% |
| Sandwiches                | 0/219 = 0%         | 223/529 = 42.2%  | 223/748 = 29.8% |
| Soups/Salads/Sandwiches   | 0/42 = 0%          | 10/10 = 100%     | 10/52 = 19.2% |
| Southeast Asian           | 0/55 = 0%          | 38/38 = 100%     | 38/93 = 40.9% |
| Steakhouse                | 0/37 = 0%          | 56/56 = 100%     | 56/93 = 60.2% |

Invariants confirmed after rebuild: panel row count unchanged (48,101);
merged cohort counts are the exact sums of their parts; citywide cohort
unchanged at 9,723/26,505 = 36.7%; no archive-only label survives in the
panel. Labels still at a ≥99.9% baseline are genuinely new 2026 categories
with no 2017 counterpart (New American, Vegan, Fusion, New French, Haute
Cuisine) — `_label_artifact` still flags these, and now only these.

Regression pins re-verified against the rebuilt data: 195 Bowery / Italian
(580 active, 45 Italian, cohort 27/41), 42 Broadway (stable, 7/30 gone),
348 Bowery (churn, fit below no-history query). Full non-network suite:
424 passed.
