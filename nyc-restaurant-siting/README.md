# NYC Restaurant Location Screening

**Is this a good location for the type of restaurant I want to open?**

A Streamlit decision-support tool that answers that question from public NYC
data, and is equally explicit about the parts of it the data cannot answer.

```bash
pip install -r requirements.txt
python build_data.py          # ~20s, writes processed/*.parquet
streamlit run app.py
```

**Data files are not in the repository** (they are large and public, so they
are gitignored). `build_data.py` requires these five files in the project
root, and will name any that are missing:

| File | Source |
|---|---|
| `DOHMH_New_York_City_Restaurant_Inspection_Results.csv` | 2017 archive of NYC Open Data dataset 43nn-pn8j |
| `DOHMH_New_York_City_Restaurant_Inspection_Results_20260824.csv` | Current export of 43nn-pn8j |
| `cleanDb_Aug_31cuisines.csv` | Project-supplied 2021 snapshot |
| `Pre-Permit_Restaurant_Inspections_20260824.csv` | NYC Open Data (pre-permit inspections) |
| `Primary_Land_Use_Tax_Lot_Output_(PLUTO)_20260824.csv` | NYC Open Data, PLUTO |

`Bi-Annual_Pedestrian_Counts_20260824.csv` (NYC DOT) is optional context.

---

## 1. What the data can and cannot support

Four findings shaped every design decision here. Each was measured against the
supplied files, not assumed.

### Finding 1 — a single DOHMH extract is not a history

Both inspection files window **every restaurant to three years**:

| | 2017 archive | 2026 extract |
|---|---|---|
| Rows | 399,918 | 295,589 |
| Restaurants (CAMIS) | 26,505 | 31,319 |
| File date span | 2011-10 → 2017-08 | 2007 → 2026-08 |
| **Longest span for any one restaurant** | **3.00 years** | **3.00 years** |

The archive's six-year date range is the union *across* restaurants, not the
history of any one of them. So a duration taken from either file alone is not a
lifespan, and a forty-year institution is indistinguishable from a newcomer.

### Finding 2 — but the two files together *are* a history

Joining them on CAMIS produces a real longitudinal panel:

```
in 2017 AND 2026    9,723   survivors, median observable span 11.0 years
in 2017, NOT 2026  16,782   gone — a genuine closure cohort
```

**16,782 observed closures.** This is what makes the app possible, and it comes
entirely from having kept the 2017 archive.

### Finding 3 — durations from that panel are still not comparable

The two files observe two disjoint stretches of time:

```
2011-10 ───── 2017-08                              (archive)
                            2023-01 ──── 2026-08   (extract)
          2021 partial snapshot ^
```

A restaurant that closed in 2019 is last seen in 2017, so its observed duration
is capped at three years by the archive's cutoff. A survivor spans both files
and shows up to 13.4. Ranking those durations against each other would conclude
that *closing early causes short durations* — which is circular.

**So duration is not the primary measure.** The app instead asks a question the
window placement cannot distort: *of the restaurants trading in 2011–2017, which
were still on DOHMH's books in 2026?* Citywide that is **36.7%**. Durations are
still shown, carrying explicit censoring flags, but nothing is ranked on them.

### Finding 4 — half of all "high-turnover" addresses are food halls

Counting distinct CAMIS values per address makes Bryant Park's Winter Village
(83 food stalls at one street address) look like the most punishing storefront
in New York. Of the addresses with four or more restaurants on record, **249 are
multi-vendor venues and only 225 are genuinely sequential** — so this is the
majority case, not an edge case.

The app separates them by measuring peak *concurrent* occupancy. Above three at
once, the address is a food hall, stadium or terminal, and history-at-address is
excluded from the score rather than misreported. Detected examples: Penn Plaza,
Yankee Stadium, Barclays Center, JFK, LaGuardia, Macy's.

---

## 2. Natural-language plan input (Claude as parser only)

The landing page takes a sentence — *"upscale Italian in 10003, about $70 a
head, 60 seats, not too much competition"* — and the Anthropic API converts it
into a structured, validated `RestaurantPlan`. **That is Claude's entire
role.** The trust boundary is architectural and tested:

| | |
|---|---|
| Claude receives | the plan text, a schema, the parser system prompt |
| Claude does NOT receive | any market, competitor, census, property or pedestrian data |
| Claude does NOT determine | the location score, any band, any evidence bullet, any recommendation |
| Analytical engine | deterministic application code over the listed datasets |
| Financial simulation | deterministic |

The parsed plan renders on **"Here's what I understood"** — editable field by
field, and nothing runs until the user confirms. Routing after confirmation
(exact address vs. area vs. no location) is plain Python. Missing information
stays missing: the parser never invents seats, spend, concepts or
neighborhoods, and phrases like "downtown" stay unresolved. Injection has
nowhere to land: user text travels only as user content, output is
schema-forced.

Configuration: `ANTHROPIC_API_KEY` in `.streamlit/secrets.toml` (model
`claude-haiku-4-5`, defined once as `ANTHROPIC_PARSER_MODEL`). **Without the
key the app still works** — a deterministic regex/taxonomy parser takes over
and the confirmation screen says which parser ran. Stated spend and seats
carry into the simulator as USER INPUT defaults.

## 3. The experience: a guided decision, not a dashboard

The interface follows the decision an entrepreneur is actually making, not the
shape of the datasets underneath:

```
What are you planning to open?        (cuisine, price point)
Where are you considering?            (address)
        ↓
LOCATION FIT  59/100 · MIXED          the answer, before any chart
one sentence saying why
        ↓
Why — six plain-language verdicts     Location history · Cuisine performance ·
                                      Competition · Area track record ·
                                      Foot traffic · Property
        ↓
What happened at this address before?
Does an Italian concept make sense here?
Who would you compete with?           (live, when Google is configured + map)
Who are your potential customers?
The property itself
        ↓
OUR ASSESSMENT                        main reason to proceed /
                                      main reason for caution
What this analysis cannot tell you
Save & compare another location       side-by-side table of saved candidates
        ↓
Data & methodology                    the technical layer, last
```

**Fit, not risk.** The engine computes a risk score where high is bad. People
read "76/100" as good no matter what the label says, so the interface shows the
inverted *Location fit* number and leaves the underlying components untouched.
The bands are Strong fit / Promising / Mixed / Higher risk / High risk.

**Words per component, chosen per component.** "Competition 87/100" tells an
entrepreneur nothing; "Competition: high" tells them everything. Each component
gets its own vocabulary (competition reads low/high, cuisine performance reads
weak/strong, foot traffic high/low), mapped from the same 35/65 cut points the
scoring engine already uses — so the words can never disagree with the numbers.
The translation lives in `nycsiting/narrative.py` and is tested for exactly
that: a headline that praises something the verdict rows call a concern fails
the suite.

**An unmeasured component says "Not measured".** Hiding the row would make the
evidence look more complete than it is.

**The recommendation is synthesised, not left to the reader.** The strongest
favourable component becomes *Main reason to proceed*, the strongest adverse one
*Main reason for caution* — and live competition outranks the historical
components there when three or more strong rivals are trading, because someone
about to sign a lease should hear about them before anything derived from a
decade-old archive.

**Compare another location** saves the current verdict row (fit, per-component
words, live competitor counts) into a session-scoped table on the landing page,
so two or three candidates can be weighed side by side.

The underlying components and weights are unchanged:

| Component | Weight | Question it answers |
|---|---|---|
| Location history | 25 | What happened at this address before? |
| Cuisine performance | 25 | Does my concept make sense here? |
| Competition | 20 | Who would I compete with? |
| Area track record | 15 | Do restaurants last around here? |
| Foot traffic | 10 | Will people walk past? |
| Property | 5 | Is the building suited to food service? |

A component that cannot be measured is dropped from the score and named, never
defaulted to average. Comparisons still carry sample sizes and refuse to call a
difference the counts cannot support.

---

## 3. The map

One map, three colourings, chosen by a radio above it:

| Mode | Encoding | Answers |
|---|---|---|
| **Still trading vs closed** | categorical, 2 hues | Where are the restaurants that *didn't* make it? |
| **Fit with your concept** | categorical, 3 hues | Your cuisine, near-substitutes, everything else |
| **Turnover at each address** | ordinal blue ramp | Which storefronts have churned through tenants? |

**Closed restaurants are drawn, not filtered out.** A block where a third of the
marks are "gone since 2017" is the single most legible thing this dataset can
show, and a map of only the survivors would hide exactly the evidence the 2017
archive exists to provide.

### The colours are measured, not chosen by eye

A map is a scatter plot: any two groups can end up adjacent, so the palette has
to hold under the *all-pairs* test rather than only neighbouring pairs. Every
palette in `mapview.py` was run through a CVD validator in both light and dark
mode, and the numbers are recorded next to the hexes.

The one worth stating plainly: "still trading vs closed" is the obvious place to
reach for a green/red status pair, and **that pair fails** —

| Pair | Deuteranopia ΔE | Verdict |
|---|---|---|
| status green `#0ca30c` ↔ status red `#d03b3b` | **4.1** | FAIL — under 8, a red-green colourblind reader sees one colour |
| categorical blue `#2a78d6` ↔ orange `#eb6834` | **24.7** | PASS |

Blue/orange is also the more honest encoding: a closed restaurant is a fact about
history, not a severity, and painting it "critical" editorialises.

Two consequences fall out of the same validation. Categorical modes cap at
**three** hues, because only the first three slots clear the all-pairs floors —
a fourth would have to fold into "Other". And in light mode the third slot sits
at 2.74:1 against the surface, a contrast WARN whose obligation is *relief*: the
same information reachable without colour. That is why the table under the map
ships with it rather than sitting behind a setting.

The site itself is drawn in ink rather than a fourth hue, so it never reads as
another category of restaurant.

---

## 4. Optional: live competitor data (Google Places)

The public data can say *"ten Italian restaurants nearby"*. It cannot say
whether those are ten struggling places or ten institutions — and those are very
different things to open next to. With a Google key configured, the **Live
competition** tab adds the present-tense signal the NYC records have no
equivalent for.

### Setup

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Then paste your key in:

```toml
GOOGLE_MAPS_API_KEY = "your-key-here"
```

The key needs **Places API (New)** enabled, and should be restricted to that API.
`.streamlit/secrets.toml` is in `.gitignore` — never commit it. The key is read
only through `st.secrets`, is never written into source, never logged, never
shown on the page, and is deliberately kept out of Streamlit's cache key.

### This layer is optional, by construction

**The app works fully without it.** No key, an expired key, exhausted quota, a
timeout, a cuisine Google has never heard of — all are ordinary, and none of
them reach the UI as an error. `fetch_landscape()` never raises; it returns a
result object whose `ok` flag and `message` say what happened, and the tab
prints a sentence explaining that the rest of the report is unaffected:

> Live competitor data is currently unavailable. The location analysis in the
> other tabs is based on NYC public data and is unaffected.

No Google **geocoding** call is made. The app already resolves addresses through
NYC Planning's GeoSearch — free, and it returns the BBL besides — so the
coordinates are reused.

### Competitor strength

Counting competitors is not enough, so each one is scored out of 100:

| Part | Max | Why |
|---|---|---|
| Rating | 50 | How well it is reviewed |
| Review volume | 30 | How many people it reaches |
| Proximity | 20 | How directly it competes for your footfall |

**Strong** ≥ 70 · **Moderate** 45–70 · **Weak** < 45

The review term is **logarithmic** on purpose. `5.0 stars from 4 reviews` is a
weaker signal than `4.6 from 3,200`, and a linear term would let one landmark
flatten every other score on the block. It saturates at 10,000 reviews.

Competitive pressure then follows four stated thresholds, checked in order — a
rule you can verify by eye rather than a fitted formula:

| | Rule |
|---|---|
| **High** | 3 or more strong competitors nearby |
| **High** | 10 or more competitors nearby, at least one strong |
| **Low** | 3 or fewer competitors and none strong |
| **Moderate** | anything else |

Places Google marks `CLOSED_PERMANENTLY` are not competitors and are dropped. A
missing status is read as trading, since Places omits the field for most healthy
listings. The subject address itself is excluded — matched on house number and
street rather than distance, so a genuine rival next door still counts. The
750m radius is enforced by us: `locationBias` is a hint, and Google will happily
return a famous restaurant a kilometre away.

### What ratings do and do not mean

**Google ratings and review counts are indicators of customer sentiment and
competitor visibility. They do not measure profitability or financial
performance.** A restaurant can be busy, well-reviewed and losing money. Review
counts also favour places that have been open longer and places that draw
tourists, so they are not a like-for-like measure of local strength.

### Google and DOHMH answer different questions

| | Used for |
|---|---|
| **DOHMH (past)** | historical presence, cuisine history, observed operating periods, turnover, previous restaurants at the address |
| **Google (present)** | who is trading now, ratings, review volume, distance, competitor strength |

Google **does not feed the screening score.** Competitive pressure is reported
beside it as its own component and labelled as such. The scoring weights were
set against measured public-data evidence; quietly adding a live third-party
input would change every number in the Evidence tab with nothing there to
explain why. Folding it in is a deliberate decision to make later, with a stated
rationale — not a side effect of adding a data source.

---

## 5. Financial simulation (Explore → Assess → **Simulate**)

After an assessment, **Simulate opening here →** runs a scenario-based
financial model of opening at that address. It is arithmetic over assumptions
the user can see and change — never a forecast, and the UI language holds that
line ("scenario estimate", "not guaranteed forecasts").

### The model, in full

```
daily customers   = seats × table turns × utilisation        (clamped to max)
monthly customers = daily × operating days/week × 52/12
revenue           = monthly customers × average spend
variable costs    = revenue × (food% + labour% + other%)
fixed costs       = rent + other fixed + marketing
operating profit  = revenue − variable − fixed
cumulative return = −initial investment + Σ profit
break-even        = first month cumulative return ≥ 0
ROI               = Σ operating profit ÷ initial investment  (N/A at $0 in)
```

Average spend compounds at the revenue-growth assumption, fixed costs at the
cost-growth assumption; variable percentages stay constant (they are shares of
revenue). `docs/simulation_validation.md` reconciles a worked example by hand
against the engine, row by row.

### Inputs, classified

Four **user inputs** up front (spend, seats, rent, investment) plus operating
days; ten **model assumptions** behind a collapsed fold, each with a labelled
default, range and validation — none presented as observed NYC facts. Three
scenarios (conservative / expected / optimistic) apply centrally-defined
multipliers that the results page prints verbatim. Sensitivity re-runs the
expected scenario at ±10% on the five levers an owner actually negotiates.

### Location data is context, not a multiplier

The simulation shows the assessment's location signals ("Why does this
simulation look like this?") **beside** the numbers and does not multiply them
in: there is no calibrated relationship between our location signals and
restaurant revenue, and an uncalibrated `revenue × location_score` would be a
black box. The simulator therefore also works when Google Places is
unavailable.

### Financial metric taxonomy (v2)

ROI is never one number here. The simulator reports, separately: **project
ROI** (pre-financing cash flow ÷ total startup investment), **owner ROI**
(after-debt-service cash ÷ owner equity), **cumulative owner ROI** and the
**cash return multiple** (both over owner equity — $504K on $350K is 144% and
1.44×, never 44%), **investment payback** (interpolated month when cumulative
owner cash recovers the investment), **operating break-even** (fixed cash
costs ÷ contribution-margin ratio — a different question from payback),
**sales-to-investment**, and **prime cost** (COGS + labour, % of sales).
Everything is pre-tax, cash-based, and excludes any future sale value.
Optional debt financing uses an exact amortizing schedule; principal is a
financing flow, never an operating cost. Definitions follow
restaurant-industry usage — see `docs/financial_methodology_sources.md`, and
`docs/financial_validation_v2.md` reconciles a full scenario by hand.

### Measured pedestrian data (NYC DOT)

Where a DOT pedestrian sensor sits within 150m, the simulator shows measured
footfall (12 complete weeks, days under 90% interval coverage excluded,
directional flows summed per interval — semantics verified against the live
API and reconciled to zero difference in `docs/pedestrian_validation.md`).
Capacity-based demand stays the default; measured data adds a **required
footfall capture** context line, and an optional **footfall-anchored** mode
(capture rate is an explicit assumption; scenarios use observed p25/median/p75,
never editorial traffic multipliers). Honest caveat: NYC publishes only five
pedestrian sensor locations, so most addresses are `TOO_REMOTE` and the
simulator says so rather than stretching a distant sensor. Sensors within
500m appear as reference context only and never enter the model.

### Guardrails

Inputs are validated (NaN/∞ rejected; ranges enforced); implausible
assumptions warn out loud (variable costs ≥ 100% of revenue, revenue below
fixed costs, impossible cover counts, $0 investment). Simulation state is
stamped with the address+cuisine pair and invalidated when either changes, so
results can never render for a previous address. The animation is a rendering
of the precomputed monthly frame — play/pause/restart/speed run client-side —
and every number it shows also appears in the tables below it.

---

## 6. Two traps the app defends against

**Silent mis-geocoding.** Ask GeoSearch for `"999 Nowhere Road, Manhattan"` and
it returns **999 Rutland Road, Brooklyn** — different street, different borough —
with `confidence: 0.8`, exactly what it reports for an exact match. Its own score
cannot distinguish the two. So `geocode.py` compares what was asked against what
came back, on street *name* with types stripped (otherwise a shared "ROAD" hides
"Nowhere" vs "Rutland") and on borough. GeoSearch's abbreviations (`B'WAY`, `FT WASHINGTON`) are
canonicalised in `normalize.py`, so they neither raise false alarms here nor
break the join to the panel.

**Sample size.** A single address holds one or two restaurants ever. Percentages
from such counts are descriptive, not findings, and the app labels them
inconclusive using a Wilson interval rather than quoting a confident number
computed from three data points.

---

## 7. Address matching

The 2017 archive has no coordinates, so a restaurant that closed before 2026 must
be placed by address. Measured coverage of the 16,782 closures:

| Rule | Coverage |
|---|---|
| Raw string match against the 2026 extract | 25.1% |
| \+ street canonicalisation (`EAST 17 STREET` → `E 17 ST`) | 50.6% |
| \+ PLUTO's 858,602 tax lots as a fallback | 63.7% |
| \+ Queens hyphens (`2507` → `25-07`) and spelled ordinals (`SECOND` → `2`) | **81.7%** |

The Queens hyphen rule alone is worth ~18 points. Overall, 92.9% of all
restaurants get coordinates.

GeoSearch's own abbreviations are folded into the same normaliser rather than
handled separately: it answers "42 Broadway" with `42 B'WAY`, and left unmapped
that address silently reported *no history* for a storefront with thirty tenants
on record.

---

## 8. Layout

```
app.py              Streamlit UI
build_data.py       ETL: raw CSVs -> processed/*.parquet
nycsiting/
  config.py         paths, windows, thresholds
  normalize.py      address canonicalisation — every join depends on this
  cuisines.py       DOHMH vocabulary (2017 labels mapped forward to 2026), competitive sets
  panel.py          restaurant-level table; grain and censoring live here
  locations.py      storefront-level table; concurrency / venue detection
  analysis.py       the site query and its comparisons
  scoring.py        transparent components -> one screening score
  context.py        PLUTO, pedestrian counts, census
  mapview.py        the map: grouping, validated palettes, figure
  google_places.py  optional live-competitor layer; never raises
  narrative.py      risk scores -> plain-English verdicts, headline, recommendation
  financial_simulation.py  scenario engine: P&L, break-even, ROI, sensitivity
  sim_animation.py  floor-plan animation as a rendering of the monthly frame
docs/simulation_validation.md   hand-vs-engine reconciliation
  geo.py stats.py geocode.py
tests/              252 tests
```

`python -m pytest tests/ -q` — unit tests are hermetic (the Google tests fake
the HTTP boundary rather than calling the API); the app integration
tests hit GeoSearch and skip if `processed/` has not been built.

---

## 9. Data files

| File | Used | Role |
|---|---|---|
| `DOHMH_..._Inspection_Results.csv` (2017) | ✅ | The closure cohort. Without it there is no history. |
| `DOHMH_..._20260824.csv` (2026) | ✅ | Current roster, coordinates, BBL, NTA, tract. |
| `Pre-Permit_Restaurant_Inspections_20260824.csv` | ✅ Loaded | Cross-checked in the audit: adds **no dates** beyond the main extract, which already carries pre-permit inspection rows. |
| `Primary_Land_Use_Tax_Lot_Output_(PLUTO)...csv` | ✅ | Building characteristics, and the address fallback that lifts coverage to 81.5%. |
| `Bi-Annual_Pedestrian_Counts_20260824.csv` | ✅ | 114 sites citywide. Used only when one is within 400m. |
| `cleanDb_Aug_31cuisines.csv` (2021) | ⚠️ Limited | See below. |
| `cleaned_db_Aug_31.csv`, both `.json` files | ❌ | Duplicates of the 2021 CSV. |
| `Violations1.csv`, `violations.json` | ❌ | Health violations are not the business question. |
| `ACSDP1Y2024.DP05...csv` | ❌ Unusable | See below. |

### The 2021 snapshot is presence-only evidence

`cleanDb_Aug_31cuisines.csv` holds 12,090 restaurants against roughly 26,000
trading in NYC, and its cuisine labels are **remapped into 31 invented
categories** ("Indian Subcontinent", "Barbecue & Steakhouse", "European") that do
not exist in DOHMH's vocabulary. It is a filtered subset, so **absence from it is
not evidence of closure** — a restaurant may simply have been filtered out. The
app records `seen_2021` as positive evidence only and never infers closure from
it.

### Local demographics: 2024 ACS 5-Year, Census tracts

Neighbourhood context comes from the official Census API — **2024 ACS 5-Year
Detailed Tables** at census-tract geography, five variables:

| Variable | Meaning |
|---|---|
| B01003_001E | Total population |
| B19013_001E | Median household income (2024 $) |
| B01002_001E | Median age |
| B23025_004E | Civilian employed population 16+ |
| B25064_001E | Median gross rent |

Refresh (five county requests — the app itself never calls the Census API):

```bash
python scripts/fetch_acs_nyc.py
```

Requires a free key in `.streamlit/secrets.toml` as `CENSUS_API_KEY`
(https://api.census.gov/data/key_signup.html). Sentinel negatives become
missing values, never zeros; suppressed tracts show a dash. The address's
tract comes from the official Census coordinate geocoder (2020 vintage); the
DOHMH tract field is 2010-vintage and is borrowed only when the code exists
unchanged in the 2020 **Census Tract → NTA equivalency** (2,327 tracts →
262 NTAs). NTA rollups sum population/employment; tract medians are never
averaged — income/age context is population-weighted and labelled derived.
The legacy national-level `ACSDP1Y2024.DP05` file is retired (one geography,
no income column) and nothing reads it.


---

## 10. What this cannot tell you

- **Inspection dates are not opening and closing dates.** They are observations
  that a restaurant existed on a given day. Every duration is an *observed period
  of operation*.
- **Closure is inferred from absence.** A restaurant in the 2017 archive but not
  the 2026 extract left DOHMH's books somewhere in that nine-year gap. Not when,
  and not why.
- **Correlation is not causation.** Restaurants closing at an address does not
  establish that the address caused it.
- **The largest variables are invisible here** — rent, lease terms, management,
  food quality, marketing, financing, reviews, macroeconomic shocks.

This is a screening tool for comparing locations on observable evidence. It is
not a prediction of success, and historical performance at a location does not
determine future results.

## 11. Not done yet

- Census tract demographics (§9).
- Subway entrance proximity as a second foot-traffic input.
- Rent and retail vacancy, the largest missing variable.
- Snapshotting the DOHMH roster on a schedule, so future closures get real dates
  instead of a nine-year interval.
