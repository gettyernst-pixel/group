"""
NYC restaurant location screening — Streamlit front end.

Run with:   streamlit run app.py
Data first: python build_data.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from nycsiting import (acs, analysis, areas, branding, comparison, config,
                       geo, geometry, plan_parser, workspace_map,
                       context, cuisines, financial_simulation as fs,
                       geocode, google_places, mapview,
                       narrative, nta, pedestrian_dot, report_pdf,
                       report_writer, scoring, sim_animation, ui)
from nycsiting.normalize import location_key_variants

st.set_page_config(page_title="Siting — NYC Restaurant Location Intelligence",
                   layout="wide")

# ---------------------------------------------------------------- data load
# WHY cache_resource, NOT cache_data, for the reference frames
# st.cache_data returns a DEEP COPY on every hit, so asking for a frame is
# never free: measured per rerun, load_lots() cost 355-466ms, load_locations()
# 75-190ms and load_panel() 24-37ms purely in copying — over half of a warm
# rerun, spent duplicating data that is never written to. cache_resource hands
# back the same object, which is correct here because these frames are
# READ-ONLY reference data built by build_data.py: nothing in the request path
# assigns into them. tests/test_performance.py pins that invariant by hashing
# the frames across a full interaction sequence, so a future in-place edit
# fails a test instead of silently corrupting every later session.
@st.cache_resource(show_spinner="Loading restaurant panel…")
def load_panel() -> pd.DataFrame:
    df = pd.read_parquet(config.RESTAURANTS_PQ)
    for col in ("first_observed", "last_observed", "closed_after", "closed_before"):
        df[col] = pd.to_datetime(df[col])
    return df


@st.cache_resource(show_spinner=False)
def load_locations() -> pd.DataFrame:
    return pd.read_parquet(config.LOCATIONS_PQ)


@st.cache_resource(show_spinner=False)
def load_lots() -> pd.DataFrame:
    return pd.read_parquet(config.LOTS_PQ).set_index("bbl")


@st.cache_resource(show_spinner=False)
def load_pedestrian() -> pd.DataFrame:
    return pd.read_parquet(config.PEDESTRIAN_PQ)


@st.cache_data(show_spinner=False)
def cuisine_options(_panel: pd.DataFrame) -> list[str]:
    counts = _panel[_panel["cuisine"] != ""]["cuisine"].value_counts()
    return list(counts.index)


@st.cache_data(show_spinner=False)
def geocode_cached(address: str) -> dict:
    return geocode.geocode(address)


@st.cache_data(show_spinner="Scoring…")
def score_cached(_report, _panel, _lot, _ped, radius, cache_key):
    """
    Underscored arguments are excluded from Streamlit's hash: the frames are
    huge and the dicts hold numpy scalars that hash unreliably. `cache_key`
    carries the actual query identity, so the memo still invalidates correctly
    when the user changes address, cuisine or radius.
    """
    return scoring.score_site(_report, _panel, _lot, _ped, radius)


def get_anthropic_api_key() -> str | None:
    """
    The one key-resolution path: Streamlit secret first, then the
    environment, stripped, empty-as-None. Resolved lazily at parse time so a
    key added to secrets.toml is seen without a restart.
    """
    import os
    try:
        secret = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        secret = None
    return plan_parser.resolve_api_key(secret, os.getenv("ANTHROPIC_API_KEY"))


@st.cache_data(ttl=24 * 3600, show_spinner="Reading your plan…")
def parse_plan_cached(text: str, parser_version: str, model: str,
                      key_present: bool, _api_key: str | None):
    """
    Identical prompts parse once — Claude runs at most ONCE per submitted
    plan, and never again for tab changes, map clicks, or navigation. The
    secret itself stays out of the cache key (underscored), but
    `key_present` is IN it — without that, a fallback parse cached while
    the key was missing would keep replaying after the key appears, which
    is exactly the bug this fixes.
    """
    return plan_parser.parse_plan(text, _api_key,
                                  known_areas=area_name_lexicon())


def simulation_enabled() -> bool:
    """
    The validated financial engine stays intact, but the product currently
    ends at explore -> assess -> compare. Simulation UI is reachable only
    with an explicit developer flag (secrets ENABLE_SIMULATION, or the
    session flag the simulation regression tests set).
    """
    if st.session_state.get("_enable_sim"):
        return True
    try:
        return bool(st.secrets.get("ENABLE_SIMULATION"))
    except Exception:
        return False


def google_api_key() -> str | None:
    """
    The Google key, or None.

    st.secrets raises when no secrets file exists at all — which is the normal
    state for anyone who has just cloned this — so the lookup is guarded rather
    than assumed. The key is never logged, echoed, or written to the page.
    """
    try:
        return st.secrets.get("GOOGLE_MAPS_API_KEY") or None
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner="Looking up nearby competitors…")
def competitors_cached(lat: float, lon: float, cuisine: str, radius: int,
                       _site: dict, _api_key: str | None):
    """
    Memoised for six hours so panning the radius slider does not bill a fresh
    Text Search each time. The key is underscored to keep it out of the cache
    key — Streamlit hashes normal arguments, and an API key has no business in
    a hash Streamlit may persist.
    """
    return google_places.fetch_landscape(
        lat, lon, cuisine, _api_key, radius=radius, site=_site)


def nyc_token() -> str | None:
    """NYC Open Data app token, from secrets. Optional: public reads work
    without it, just throttled harder. Never logged, never rendered."""
    try:
        return st.secrets.get("NYC_OPEN_DATA_APP_TOKEN") or None
    except Exception:
        return None


@st.cache_data(ttl=24 * 3600, show_spinner="Checking measured pedestrian data…")
def pedestrian_cached(lat: float, lon: float, _token: str | None):
    """Measured footfall for a site, cached for a day. Never raises."""
    return pedestrian_dot.measure_location(lat, lon, token=_token)


@st.cache_data(show_spinner=False)
def load_acs() -> pd.DataFrame | None:
    """Local 2024 ACS tract table, or None until the fetch script has run."""
    return acs.load_cache()


@st.cache_data(show_spinner=False)
def load_tract_nta() -> pd.DataFrame:
    return nta.load_equivalency()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def site_tract_cached(lat: float, lon: float, _panel) -> tuple[str | None, str]:
    """One Census-geocoder call per address, cached for a day."""
    eq = load_tract_nta()
    return acs.site_tract_geoid(_panel, lat, lon, set(eq["tract_geoid"]))


@st.cache_resource(show_spinner="Preparing NYC geography…")
def nta_index():
    return geometry.NTAIndex()


@st.cache_resource(show_spinner=False)
def nta_geojson():
    """The display FeatureCollection, built once. cache_resource for the same
    reason as the frames above: deep-copying ~1.9MB of nested lists cost
    ~57ms on every rerun, and plotly only reads it."""
    return nta_index().to_geojson()


#: Where the published copy of the display geometry lives. Streamlit serves
#: this directory at `app/static/` when enableStaticServing is on (set in
#: .streamlit/config.toml, which ships with the repo).
STATIC_DIR = config.APP_DIR / "static"


@st.cache_resource(show_spinner=False)
def nta_display_geometry() -> workspace_map.DisplayGeometry:
    """
    The area shapes, published ONCE as a static file and referenced by URL.

    WHY: the shapes never change, but Streamlit re-sends the whole figure on
    every interaction, and st.plotly_chart's element identity includes the
    figure spec — so a new area, layer or filter remounts the chart and
    re-ships the geometry. Embedded, that is ~1.0MB across the websocket per
    click, re-parsed by the browser each time. Published, plotly.js fetches
    the file once, the browser caches it, and the per-interaction figure
    drops to ~40KB.

    The filename carries a hash of the content, so a browser can never serve
    a stale copy after the geometry changes: new bytes mean a new URL.

    If the file cannot be written (a read-only deployment, no permission),
    this falls back to embedding the geometry — slower, but correct. It
    never returns a URL it has not just written, because an unfetchable URL
    would render an EMPTY map with no error, and a blank map that looks
    deliberate is exactly the silent failure this product must not ship.
    """
    geojson = nta_geojson()
    try:
        payload = json.dumps(geojson, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        published = STATIC_DIR / f"nta_display.{digest}.geojson"
        if not published.exists():
            # Write-then-rename: a half-written file must never be served.
            tmp = published.with_suffix(".partial")
            tmp.write_text(payload)
            tmp.replace(published)
            for old in STATIC_DIR.glob("nta_display.*.geojson"):
                if old != published:
                    old.unlink(missing_ok=True)
        if published.stat().st_size != len(payload.encode()):
            raise OSError("published geometry is truncated")
        # Relative, so it resolves correctly under a base URL path too.
        return workspace_map.DisplayGeometry(
            f"app/static/{published.name}",
            [f["id"] for f in geojson["features"]])
    except Exception as exc:                  # noqa: BLE001 - never fatal
        logging.getLogger(__name__).warning(
            "Serving map geometry inline (could not publish it): %s", exc)
        return workspace_map.as_geometry(geojson)


@st.cache_data(show_spinner=False)
def nta_names() -> dict:
    return {code: f["name"] for code, f in nta_index().features.items()}


@st.cache_data(show_spinner="Assigning restaurants to areas…")
def nta_assignment(_panel) -> pd.Series:
    return geometry.assign_restaurants(_panel, nta_index())


@st.cache_data(show_spinner=False)
def area_features_cached(_panel) -> pd.DataFrame:
    return areas.area_features(_panel, nta_assignment(_panel))


@st.cache_resource(show_spinner=False)
def panel_with_nta_cached(_panel) -> pd.DataFrame:
    """
    The panel joined to its 2020 NTA assignment ONCE per session. Area
    clicks slice this frame instead of re-merging 48k rows on every rerun —
    measured 29.7ms -> 2.0ms per lookup.
    """
    return _panel.merge(nta_assignment(_panel).rename("nta_2020"),
                        left_on="camis", right_index=True)


#: Shape of an empty concept-fit table — returned when no cuisine was given,
#: so every caller reads "not measured" instead of crashing on an empty frame.
_EMPTY_FIT = pd.DataFrame(
    columns=["band", "fit_index", "cohort_n", "cohort_survived",
             "active_same", "baseline_rate", "baseline_n"],
    index=pd.Index([], name="nta_code"))
_EMPTY_DENSITY = pd.DataFrame(
    columns=["active_same", "density_percentile"],
    index=pd.Index([], name="nta_2020"))


@st.cache_data(show_spinner="Analyzing concept fit…")
def concept_fit_cached(_panel, cuisine: str | None) -> pd.DataFrame:
    """Per-NTA concept fit, or an empty (correctly-shaped) table when the
    plan named no cuisine — concept fit is then simply not measured."""
    if not cuisine:
        return _EMPTY_FIT.copy()
    return areas.area_concept_fit(_panel, nta_assignment(_panel), cuisine)


@st.cache_data(show_spinner="Analyzing restaurant persistence…")
def conceptfree_fit_cached(_panel) -> pd.DataFrame:
    """
    Concept-INDEPENDENT area table for plans with no cuisine: overall
    restaurant persistence (2011–17 cohort still listed 2026) versus the
    citywide rate, on the same Wilson-gated 50-neutral scale as concept
    fit. Honestly labeled everywhere it renders: this measures restaurants
    in general, never the user's specific concept.
    """
    from nycsiting.stats import rate_differs
    feats = area_features_cached(_panel)
    cohort = _panel[_panel["seen_2017"]]
    city = float(cohort["seen_2026"].mean())
    rows = []
    for code, row in feats.iterrows():
        n, survived = int(row["cohort_n"]), int(row["cohort_survived"])
        if n < areas.MIN_AREA_SAMPLE:
            band, index = "Limited evidence", float("nan")
        else:
            rate = survived / n
            gap = rate - city
            verdict = rate_differs(survived, n, city)
            index = float(np.clip(
                areas.FIT_NEUTRAL + gap * areas.FIT_SLOPE, 0, 100))
            band = ("Strong" if verdict == "above"
                    else "Promising" if gap > 0 else "Mixed")
        rows.append(dict(nta_code=code, band=band, fit_index=index,
                         cohort_n=n, cohort_survived=survived,
                         baseline_rate=city))
    return pd.DataFrame(rows).set_index("nta_code")


@st.cache_data(show_spinner=False)
def density_cached(_panel, cuisine: str | None) -> pd.DataFrame:
    if not cuisine:
        return _EMPTY_DENSITY.copy()
    return areas.restaurant_density_by_cuisine(
        _panel, nta_assignment(_panel), cuisine)


@st.cache_data(show_spinner=False)
def turnover_cached(_panel) -> pd.DataFrame:
    return areas.area_turnover_context(area_features_cached(_panel), _panel)


@st.cache_data(show_spinner=False)
def acs_by_nta_cached() -> tuple[pd.Series | None, pd.DataFrame | None]:
    """(availability flags, NTA demographics) via the safeguarded rollup."""
    table = load_acs()
    if table is None:
        return None, None
    equivalency = load_tract_nta()
    demo = nta.nta_demographics(table, equivalency).set_index("nta_code")
    return pd.Series(True, index=demo.index), demo


@st.cache_data(show_spinner=False)
def evidence_cached(_panel) -> pd.DataFrame:
    flags, _ = acs_by_nta_cached()
    return areas.evidence_quality_by_area(area_features_cached(_panel), flags)


@st.cache_data(show_spinner=False)
def concept_candidates_cached(_panel) -> list[str]:
    """Cuisines eligible for concept ranking — same gate as areas.py."""
    active = _panel[_panel["seen_2026"] & (_panel["cuisine"] != "")]
    return [c for c, n in active["cuisine"].value_counts().items()
            if n >= areas.MIN_CITYWIDE_CUISINE]


@st.cache_data(show_spinner="Ranking concepts for this area…")
def concept_ranking_cached(_panel, nta_code: str, top: int = 8) -> list[dict]:
    """
    Same candidates, same formula, same ordering as
    areas.rank_concepts_for_area — but assembled from the per-cuisine fit
    tables that concept_fit_cached already memoises ACROSS areas. The first
    area pays to build the tables once; every later area click ranks from
    cache (measured 1167ms -> 0.5ms).
    """
    rows = []
    for cuisine in concept_candidates_cached(_panel):
        fit = concept_fit_cached(_panel, cuisine)
        if nta_code not in fit.index:
            continue
        row = fit.loc[nta_code]
        if row["band"] == "Limited evidence" or row["fit_index"] is None:
            continue
        rows.append(dict(cuisine=cuisine, fit_index=float(row["fit_index"]),
                         band=row["band"], cohort_n=int(row["cohort_n"]),
                         cohort_survived=int(row["cohort_survived"]),
                         active_same=int(row["active_same"]),
                         baseline_rate=float(row["baseline_rate"]),
                         baseline_n=int(row["baseline_n"])))
    rows.sort(key=lambda r: -r["fit_index"])
    return rows[:top]


@st.cache_data(show_spinner=False)
def area_name_lexicon(_names_key: int = 0) -> tuple[str, ...]:
    """
    Every neighborhood-name segment the 2020 geography actually maps — the
    ONLY vocabulary the deterministic parsers may recognize. Residential
    NTAs only, and no fragments under four characters: hyphen shards of
    park/cemetery names ("green" from Green-Wood Cemetery, "co" from
    Co-op City) would otherwise match ordinary prose in a plan.
    """
    segments = set()
    for feature in nta_index().features.values():
        if not feature["residential"]:
            continue
        segments.update(s for s in nta.name_segments(feature["name"])
                        if len(s) >= 4)
    return tuple(sorted(segments, key=len, reverse=True))


def resolve_area_candidates(text: str, borough: str | None = None
                            ) -> list[str]:
    """
    Deterministic area-name resolution against the app's own geography,
    candidates ordered by current restaurant inventory (a park polygon or a
    thinly-mapped namesake never outranks the neighborhood people mean).
    """
    names = nta_names()
    boroughs = {c: f["borough"] for c, f in nta_index().features.items()}
    codes = nta.resolve_area_name(text, names, boroughs, borough)
    if len(codes) > 1:
        feats = area_features_cached(load_panel())
        codes.sort(key=lambda c: -int(feats.loc[c, "restaurants_active"])
                   if c in feats.index else 0)
    return codes


@st.cache_data(show_spinner=False)
def ped_sites_by_nta_cached() -> dict[str, list[float]]:
    """The 114 DOT bi-annual count sites located ONCE into their NTAs —
    existing data, the existing point-in-polygon."""
    out: dict[str, list[float]] = {}
    for _, row in load_pedestrian().iterrows():
        code = nta_index().locate(row["lat"], row["lon"])
        if code:
            out.setdefault(code, []).append(float(row["count"]))
    return out


def area_ped_context(code: str) -> dict:
    """
    DOT bi-annual pedestrian evidence for one NTA. Terciles of the citywide
    counts give a relative High/Moderate/Low; no site inside means NOT
    MEASURED — a statement about coverage, never about the area.
    """
    inside = ped_sites_by_nta_cached().get(code)
    if not inside:
        return {"band": None, "sites": 0}
    lo, hi = load_pedestrian()["count"].quantile([1 / 3, 2 / 3])
    peak = max(inside)
    band = "High" if peak >= hi else ("Moderate" if peak >= lo else "Low")
    return {"band": band, "sites": len(inside), "peak": peak}


def active_theme() -> str:
    """
    Which palette to draw with.

    The map's colours are validated separately against the light and dark chart
    surfaces — a dark palette on a light basemap fails the contrast check — so
    the figure has to know which one it is rendering on.

    `st.context.theme.type` reports None in some setups rather than raising,
    which is why .streamlit/config.toml declares the theme explicitly. This
    reads the live value where one exists and falls back to the declared
    default otherwise.
    """
    try:
        resolved = st.context.theme.type
    except Exception:
        resolved = None
    if resolved in ("light", "dark"):
        return resolved
    try:
        base = st.get_option("theme.base")
    except Exception:
        base = None
    return "light" if base == "light" else "dark"


def resolve_location_key(locs: pd.DataFrame, site: dict) -> str | None:
    """Match the geocoded address to a storefront we have history for."""
    known = set(locs["location_key"])
    for variant in location_key_variants(
            site.get("borough"), site.get("housenumber"), site.get("street")):
        if variant in known:
            return variant
    return None


# ---------------------------------------------------------------- rendering
def _occupancy_gantt(timeline: pd.DataFrame) -> go.Figure:
    """
    Horizontal bars showing when each restaurant was observed.

    Built from go.Bar rather than px.timeline because px.timeline (plotly 5.9)
    hands the figure an object-dtype array of datetime.timedelta, which
    plotly's own JSON encoder then refuses — taking the whole tab down on any
    address with more than one datable restaurant. Passing an explicit
    millisecond width with a datetime `base` sidesteps that entirely.

    A restaurant inspected only once has zero width, so every bar gets a
    one-day floor to stay visible.
    """
    df = timeline.sort_values("first_observed").copy()
    day_ms = 86_400_000
    width = (df["last_observed"] - df["first_observed"]).dt.total_seconds() * 1000
    width = width.clip(lower=day_ms)

    colours = {"active": "#2f8d6e", "closed": "#a83232", "unknown": "#786c78"}
    labels = {"active": "In 2026 data", "closed": "Gone by 2026", "unknown": "Unknown"}

    fig = go.Figure()
    for status in ["active", "closed", "unknown"]:
        mask = df["status"] == status
        if not mask.any():
            continue
        sub = df[mask]
        fig.add_trace(go.Bar(
            y=sub["name"], x=width[mask].tolist(), base=sub["first_observed"].tolist(),
            orientation="h", name=labels[status],
            marker_color=colours[status],
            customdata=list(zip(sub["first_observed"].dt.date.astype(str),
                                sub["last_observed"].dt.date.astype(str),
                                sub["cuisine"].fillna("unspecified"))),
            hovertemplate=("<b>%{y}</b><br>%{customdata[2]}"
                           "<br>observed %{customdata[0]} to %{customdata[1]}"
                           "<extra></extra>")))

    fig.update_layout(
        barmode="overlay", height=110 + 26 * len(df),
        margin=dict(l=0, r=10, t=10, b=0),
        legend=dict(orientation="h", y=1.06, title=None),
        xaxis=dict(type="date", title=None),
        yaxis=dict(autorange="reversed", title=None))
    return fig


def render_history(report: dict) -> None:
    """Snapshot first: three numbers and the timeline. The tenant-by-tenant
    record and its reading rules sit behind View evidence."""
    loc = report["location"]
    if loc["is_multi_vendor"]:
        st.warning(
            "This address is a food hall, market or terminal — many "
            "restaurants trade at once, so tenant counts here do not measure "
            "turnover and are excluded from the score.")

    occ = loc["occupancy"]
    if occ is None or occ.empty:
        st.markdown("No exact address history found — **limited evidence**, "
                    "not negative evidence.")
        st.caption("The address may never have held a restaurant, or may be "
                   "written differently in the source records.")
        return

    a, b, c = st.columns(3)
    a.metric("Restaurants on record here", loc["restaurants_ever"])
    b.metric("No longer in the 2026 data", loc["closed_here"])
    c.metric("Distinct cuisines tried", len(loc["cuisines_here"]))

    timeline = occ.dropna(subset=["first_observed", "last_observed"]).copy()
    if len(timeline) > 1:
        st.plotly_chart(_occupancy_gantt(timeline), width="stretch")

    with st.expander("View evidence"):
        show = occ.copy()
        show["Observed from"] = show["first_observed"].dt.date
        show["Observed to"] = show["last_observed"].dt.date
        show["Status"] = show["status"].map(
            {"active": "In 2026 data", "closed": "Gone by 2026",
             "unknown": "Unknown"})
        st.dataframe(
            show[["name", "cuisine", "Observed from", "Observed to", "Status"]]
            .rename(columns={"name": "Restaurant", "cuisine": "Cuisine"}),
            width="stretch", hide_index=True)
        st.caption(
            "Bars show when inspectors **observed** each restaurant, not when "
            "it opened or closed; single-inspection restaurants are drawn one "
            "day wide. Each extract windows a restaurant to three years, so a "
            "bar is a slice of activity, never a lifespan.")


def render_cuisine(report: dict) -> None:
    """Cuisine track record, in plain words: observed persistence here vs
    the benchmarks, one read per comparison, no statistics jargon."""
    cuisine = report["query"]["cuisine"]
    comps = report["comparisons"]
    if not comps:
        st.caption(f"No {cuisine} restaurant near here appears in the "
                   f"historical records — no local track record to compare.")
        return

    read = {"below": "Lower", "above": "Higher",
            "inconclusive": "No clear difference"}
    labels = {
        "location_vs_area": "This address vs nearby",
        "cuisine_vs_area": f"{cuisine} vs other cuisines nearby",
        "cuisine_vs_city": f"{cuisine} here vs citywide",
        "area_vs_city": "This area vs citywide",
    }
    ui.bench_rows([
        (labels.get(c["key"], c["question"].rstrip("?")),
         f"{100*c['subject_rate']:.0f}% vs {100*c['baseline_rate']:.0f}% · "
         f"{read[c['verdict']]}")
        for c in comps])
    smallest = min(c["subject_n"] for c in comps)
    st.caption(f"Observed persistence: the share of restaurants in the "
               f"2011–17 records still listed in 2026. Smallest comparison "
               f"here rests on {smallest} historical restaurants.")
    with st.expander("What is observed persistence?"):
        st.caption("Restaurants present in the 2011–2017 historical records "
                   "that also appear in the 2026 public record. It measures "
                   "observed persistence in the datasets, not profitability. "
                   "\"No clear difference\" means the gap is within the "
                   "sampling uncertainty for the sample size.")


def render_competition(report: dict, site: dict, locations: pd.DataFrame) -> None:
    """The map, visible without prose: one summary line above, detail behind."""
    area = report["area"]
    cuisine = report["query"]["cuisine"]
    radius = report["query"]["radius_m"]
    compset = set(area["competitive_set"])

    st.caption(f"Similar restaurants: **{area['active_competitors']}** · "
               f"exactly {cuisine}: **{area['active_same_cuisine']}** · "
               f"radius {radius:.0f}m")

    everything = area["all"]
    if everything.empty:
        st.info("No food business of any kind is on record within the radius.")
        return

    mode_label = st.radio(
        "Colour the map by", list(mapview.MODES.values()),
        horizontal=True, label_visibility="collapsed")
    mode = next(k for k, v in mapview.MODES.items() if v == mode_label)

    st.plotly_chart(
        mapview.build_map(everything, site, cuisine, compset, radius,
                          mode=mode, theme=active_theme(), locations=locations),
        width="stretch")

    # The colour-free path to the same information lives here too: one light-
    # mode palette slot sits below 3:1 contrast, which obligates the table.
    with st.expander("Map detail & table"):
        st.caption("Counted as a similar concept: "
                   + ", ".join(area["competitive_set"]) + ".")
        missing = int(everything["lat"].isna().sum())
        note = (f"{int(everything['lat'].notna().sum())} restaurants within "
                f"{radius:.0f}m, including ones that have since closed — a "
                f"block where many marks are gone is the clearest thing this "
                f"data can show.")
        if missing:
            note += (f" {missing} more could not be placed: the 2017 archive "
                     f"carries no coordinates, so a closed restaurant is only "
                     f"mappable if its address still resolves.")
        st.caption(note)
        st.dataframe(
            mapview.map_table(everything, mode, cuisine, compset, locations),
            width="stretch", hide_index=True)


def render_context(lot: dict | None, ped: dict | None) -> None:
    """Site data, compact: the six decision-relevant fields and the nearest
    pedestrian count with its quality tag. Hosted in a collapsed expander."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**The property**")
        if not lot:
            st.caption("No PLUTO record matched this tax lot — condominium "
                       "addresses often resolve to a billing lot without "
                       "building data.")
        else:
            rows = [
                ("Land use", lot.get("land_use")),
                ("Zoning", lot.get("zoning")),
                ("Building class", lot.get("building_class")),
                ("Floors", f"{lot['num_floors']:.0f}" if lot.get("num_floors") else None),
                ("Year built", lot.get("year_built")),
                ("Retail area (sq ft)", f"{lot['retail_area']:,}" if lot.get("retail_area") else None),
            ]
            # Strings throughout: the column otherwise mixes ints with text,
            # which Arrow cannot type.
            st.table(pd.DataFrame(
                [(k, str(v)) for k, v in rows if v not in (None, "", 0)],
                columns=["", "Value"]).set_index(""))

    with col2:
        st.markdown("**Pedestrian activity**")
        if not ped:
            st.caption("No pedestrian counting site could be matched.")
        else:
            st.metric(f"{ped['street']}", f"{ped['count']:,}",
                      help=f"Between {ped['between']} · {ped['period']}")
            tag = ("Measured nearby" if ped["represents_this_block"]
                   else "Nearby reference — district context only")
            st.caption(f"{ped['distance_m']:.0f}m from your site · {tag}.")


def render_google(landscape, cuisine: str, price: str | None = None,
                  report=None) -> None:
    """
    Live competition: four numbers and one line by default; ranked rows,
    price mix and the strength methodology one expander away. Failure states
    are one calm line each — the assessment does not depend on this layer.
    """
    if landscape is None or not landscape.ok:
        if landscape is None:
            st.info("No cuisine or concept was specified, so there is no "
                    "live competitor query to run. Public-record inventory "
                    "below.")
        elif landscape.reason == "no_key":
            # Name the secret: on a fresh clone or a new Cloud deployment
            # this is the one message that says what to add and where.
            st.info("Live competitor enrichment is off because no "
                    "GOOGLE_MAPS_API_KEY is configured in Streamlit "
                    "secrets. The public-record analysis below is "
                    "unaffected.")
        else:
            st.info("Live competitor enrichment is temporarily unavailable."
                    + (" The configured Google key was rejected — check it "
                       "in Google Cloud."
                       if landscape.reason == "auth" else ""))
        # Honest fallback: the public-record inventory still answers the
        # question, just without live ratings.
        if report is not None:
            area = report["area"]
            if cuisine and cuisine != "restaurant":
                a, b, c = st.columns(3)
                a.metric("Similar concepts nearby",
                         area["active_competitors"])
                b.metric(f"Exactly {cuisine}", area["active_same_cuisine"])
                c.metric("Food businesses nearby", area["active_all"])
            else:
                # Without a cuisine the same-cuisine counters are zero by
                # construction — showing them would read as "no competitors
                # here", which is not what the records say.
                st.metric("Food businesses nearby", area["active_all"])
            st.caption("From NYC public records within the site radius — "
                       "live ratings and review counts return when the "
                       "Google key is restored.")
        return

    if landscape.total == 0:
        st.info(f"Google lists no {cuisine} restaurants within "
                f"{landscape.radius_m:.0f}m. Worth knowing — but check the "
                f"concept wording, since Google matches on it.")
        return

    pressure, because = google_places.classify_pressure(landscape.competitors)
    a, b, c, d = st.columns(4)
    a.metric(f"{cuisine} competitors nearby", landscape.total)
    b.metric("Strong competitors", landscape.strong)
    rating = landscape.mean_rating
    c.metric("Average rating", f"{rating:.1f} ★" if rating is not None else "—")
    d.metric("Competitive pressure", pressure)
    st.caption(
        f":grey[{pressure} because {because}. Reported separately — it does "
        f"**not** change the location fit score, which uses public records "
        f"only.]")

    tone_for = {"Strong": "concern", "Moderate": "neutral", "Weak": "good"}
    with st.expander("View competition detail"):
        st.caption(
            f"Restaurants trading now that Google lists for “{cuisine} "
            f"restaurant” within {landscape.radius_m:.0f}m — live third-party "
            f"data, separate from the public records used elsewhere.")

        mix = google_places.price_mix(landscape.competitors)
        if mix:
            priced = sum(mix.values())
            common = max(mix, key=mix.get)
            if price:
                # Only assert "your price point" when the user actually
                # stated one — a defaulted $$ is not the user's plan.
                same = mix.get(price, 0)
                st.caption(
                    f"**Your price point: {price}.** Most priced competitors "
                    f"sit at {common} ({mix[common]} of {priced}); {same} "
                    f"are at {price}."
                    + (" You would be pitching where this block already "
                       "sits." if common == price else
                       f" {price} is away from the local norm — an opening "
                       f"or a mismatch; this data cannot tell you which."))
            else:
                st.caption(
                    f"**Price mix.** Most priced competitors sit at {common} "
                    f"({mix[common]} of {priced} with price data).")

        ui.competitor_rows([
            dict(name=r["name"],
                 meta=(f"{r['rating']:.1f} ★ · " if pd.notna(r["rating"])
                       else "unrated · ")
                      + f"{int(r['reviews'] or 0):,} reviews · "
                        f"{int(r['distance_m'])}m",
                 status=f"{r['competitor_strength']} · {r['competitor_score']:.0f}",
                 tone=tone_for.get(r["competitor_strength"], "neutral"))
            for _, r in landscape.competitors.head(8).iterrows()])

        show = landscape.competitors.copy()
        show["Rating"] = show["rating"].map(
            lambda v: f"{v:.1f} ★" if pd.notna(v) else "unrated")
        show["Reviews"] = show["reviews"].fillna(0).astype(int).map("{:,}".format)
        st.dataframe(
            show[["name", "Rating", "Reviews", "distance_m",
                  "competitor_strength", "competitor_score"]]
            .rename(columns={"name": "Restaurant", "distance_m": "Distance (m)",
                             "competitor_strength": "Strength",
                             "competitor_score": "Score / 100"}),
            width="stretch", hide_index=True)

        st.markdown(
            "**How competitor strength is calculated** — each competitor "
            "scores out of 100:\n\n"
            "| Part | Max | Why |\n|---|---|---|\n"
            "| Rating | 50 | How well it is reviewed |\n"
            "| Review volume | 30 | How many people it reaches |\n"
            "| Proximity | 20 | How directly it competes for your footfall |\n\n"
            "Review volume is **logarithmic**: 5.0 ★ from four reviews is a "
            "weaker signal than 4.6 ★ from three thousand; it saturates at "
            "10,000 reviews. **Strong** ≥ 70 · **Moderate** 45–70 · "
            "**Weak** < 45. Pressure thresholds:\n\n"
            + "\n".join(f"- **{label}** — {because}"
                        for label, _, because in google_places.PRESSURE_RULES))
        st.caption(
            "An unrated restaurant scores zero on the rating part — the honest "
            "reading, though it means a brand-new rival scores low. A Strong "
            "label also needs at least 20 reviews.")


#: The no-cuisine sentinel for every cuisine selector. Internally the value
#: is None — a user who named no cuisine must never inherit the first
#: taxonomy label.
CUISINE_ANY = "Any cuisine"

EXAMPLE_PLANS = [
    "Italian restaurant at 195 Bowery, Manhattan",
    "Small coffee shop in 10012 with strong foot traffic",
    "Upscale Japanese restaurant in Manhattan, around $100 per person",
]


def landing_page(panel: pd.DataFrame) -> None:
    """
    Explore: one natural-language input. Claude converts the sentence into a
    structured plan; the user confirms it before anything is analysed. Not a
    chat — intelligent search.
    """
    # Composition: a deliberately narrow column, centred, sitting slightly
    # above the mathematical centre of the viewport. The old fixed 40px
    # spacer plus a full-width form left the prompt bar around two-thirds
    # of the way down an 800px window with dead space above it; the pad is
    # now viewport-relative (clamp in styles.css) so the hero rises on
    # short screens instead of pushing the CTA below the fold.
    st.markdown('<div class="jx-landing-pad"></div>', unsafe_allow_html=True)
    # A real centred column, not a CSS wrapper: Streamlit widgets cannot be
    # nested inside a markdown div, so the form would have escaped any
    # max-width applied that way and kept stretching edge to edge.
    _, hero, _ = st.columns([1, 3.2, 1])
    with hero:
        st.markdown('<div style="margin-bottom:14px;">'
                    + branding.logo_img(height=44) + '</div>',
                    unsafe_allow_html=True)
        ui.eyebrow("Restaurant location intelligence")
        ui.display("Where should your<br>restaurant go?")
        st.markdown("Tell us what you're planning.")
        st.markdown('<div style="height:14px"></div>',
                    unsafe_allow_html=True)

        # A form, deliberately: clicking the submit button commits the
        # textarea value in the same run. The previous st.text_area +
        # disabled-button pair only saw the text after a blur or Cmd+Enter
        # — the reported "Continue needs Cmd+Enter first" bug.
        with st.form("plan_form", border=True):
            text = st.text_area(
                "Your plan", value=st.session_state.get("plan_text", ""),
                placeholder=("I want to open an upscale Italian restaurant "
                             "in 10003. About $70 per person and around 60 "
                             "seats. Good pedestrian activity, not extreme "
                             "competition."),
                height=110, label_visibility="collapsed")
            go = st.form_submit_button("Continue →", type="primary",
                                       width="stretch")
        if go and not text.strip():
            st.caption(":orange[Tell us a little about the restaurant "
                       "you're planning first.]")
            go = False
        st.caption("Try: " + " · ".join(f"*{e}*" for e in EXAMPLE_PLANS))

    if go:
        # A new plan invalidates everything downstream of the old one —
        # including any cuisine, filter, or comparison the last plan left.
        for key in ("plan_outcome", "plan_confirmed", "sim_results",
                    "sim_location_id", "address", "cuisine", "ws_concept",
                    "ws_comp", "_comp_mirror", COMPARISON_KEY,
                    "report_pdf", "confirmed_plan", "selected_area",
                    "selected_restaurant", "requested_area"):
            st.session_state.pop(key, None)
        st.session_state["plan_text"] = text.strip()
        key = get_anthropic_api_key()
        st.session_state["plan_outcome"] = parse_plan_cached(
            text.strip(), plan_parser.PARSER_VERSION,
            plan_parser.ANTHROPIC_PARSER_MODEL, bool(key), key)
        st.session_state["stage"] = "confirm"
        st.rerun()

    # The legacy "saved locations" table is gone with the second comparison
    # store it belonged to. Comparison lives in the workspace tray now, and
    # the landing page is for authoring a plan — nothing else.


def _an(word: str) -> str:
    """'an Italian concept', 'a Japanese concept'."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def render_context_bar(site: dict, cuisine: str, price: str) -> None:
    """Query context: what am I analysing, compact, with the edit action."""
    left, right = st.columns([4, 1])
    with left:
        ui.query_context(cuisine, price, site["label"])
    with right:
        if st.button("Edit search", width="stretch"):
            st.session_state["stage"] = "landing"
            st.rerun()
        if st.button("Start over", width="stretch", key="ctx_startover"):
            for k in ("sim_results", "sim_location_id"):
                st.session_state.pop(k, None)
            st.session_state["stage"] = "landing"
            st.rerun()
    if site.get("warning"):
        st.warning(f"**Check the address.** {site['warning']}")


def render_hero(fit: int | None, band: str, headline: str,
                quality: tuple[str, list[str]] | None = None) -> None:
    """Band before number: the categorical read is the message; the index is
    for comparing addresses and is labelled as relative, never a probability."""
    # Nothing under the hero by default: the quality reasons move into the
    # signals expander, the screening caveat into Limits of this analysis.
    ui.decision_hero(band, headline, fit, quality[0] if quality else None)


def _short_stat(v: dict, report: dict, ped: dict | None) -> str:
    """The single strongest number behind each criterion, in mono shorthand."""
    key = v["key"]
    area = report["area"]
    loc = report["location"]
    if key == "location_history":
        if loc["restaurants_ever"]:
            return (f"{loc['restaurants_ever']} on record · "
                    f"{loc['closed_here']} no longer listed")
        return "no restaurant on record at this address"
    if key == "cuisine_track_record":
        same = area["cohort_same_cuisine"]
        if same["total"]:
            return (f"{same['survived']} of {same['total']} still listed "
                    f"({same['rate']:.0%})")
        return "no local cohort to follow"
    if key == "competition":
        return (f"{area['active_competitors']} competing within "
                f"{report['query']['radius_m']:.0f}m")
    if key == "area_retention":
        cohort = area["cohort"]
        if cohort["total"]:
            return f"{cohort['rate']:.0%} of {cohort['total']} nearby still listed"
        return "no nearby cohort"
    if key == "foot_traffic":
        if ped:
            return (f"{ped['count']:,} counted · {ped['distance_m']:.0f}m away")
        return "no counting site nearby"
    if key == "property_fit":
        return ""
    return ""


#: Short strip labels for the signal read — the full names live in the detail.
_SIGNAL_LABELS = {
    "location_history": "History", "cuisine_track_record": "Cuisine fit",
    "competition": "Competition", "area_retention": "Area record",
    "foot_traffic": "Foot traffic", "property_fit": "Property",
}


def render_why(verdicts: list[dict], report: dict, ped: dict | None,
               quality: tuple[str, list[str]] | None = None) -> None:
    """
    The 3-second read: label — state, six cells, no sentences. The full
    criteria rows (conclusion, strongest number, caveats) sit one expander
    away. LIMITED EVIDENCE stays a distinct state from any negative verdict,
    so no-data can never be misread as bad-data.
    """
    ui.signal_strip([
        (_SIGNAL_LABELS.get(v["key"], v["label"]),
         "Limited evidence" if v["verdict"] == "Not measured" else v["verdict"],
         v["tone"])
        for v in verdicts])

    with st.expander("How each signal was judged"):
        if quality:
            st.caption(f"Evidence quality: {quality[0].lower()} — "
                       + " · ".join(quality[1]) + ".")
        ui.evidence_rows([
            dict(label=v["label"],
                 verdict=("Limited evidence" if v["verdict"] == "Not measured"
                          else v["verdict"]),
                 tone=v["tone"], conclusion=v["evidence"] or v["question"],
                 evidence_stat=_short_stat(v, report, ped))
            for v in verdicts])
        for v in verdicts:
            if v["detail"]:
                st.caption(f"{v['label']}: {v['detail']}")


def _fmt_compact(value: float | None, money: bool = False,
                 decimals: int = 0) -> str:
    if value is None:
        return "—"
    prefix = "$" if money else ""
    if value >= 1_000_000:
        return f"{prefix}{value/1_000_000:.1f}M"
    if value >= 10_000:
        return f"{prefix}{value/1_000:.1f}K"
    return f"{prefix}{value:,.{decimals}f}"


def render_market(ped: dict | None, verdicts: list[dict],
                  tract: dict | None = None,
                  tract_source: str = "unavailable") -> None:
    """Local market: 2x2 metric grid inside the panel width — never a wide
    strip, never horizontal scroll. Context about residents, not customers."""
    if tract:
        def value_of(key, money=False, decimals=0):
            m = tract.get(key) or {}
            return (_fmt_compact(m.get("value"), money, decimals),
                    m.get("percentile"))

        pop, _ = value_of("population")
        inc, inc_pct = value_of("median_household_income", money=True)
        age, _ = value_of("median_age", decimals=1)
        emp, _ = value_of("employed_population")
        r1a, r1b = st.columns(2)
        r1a.metric("Population", pop)
        r1b.metric("Income", inc,
                   help="Median household income for this Census tract")
        r2a, r2b = st.columns(2)
        r2a.metric("Median age", age)
        r2b.metric("Employed", emp)
        if inc_pct is not None:
            st.caption(f"Income at the {inc_pct:.0f}th percentile of NYC "
                       f"tracts.")
        st.caption("ACS 2024 · Census tract"
                   + ("" if tract_source == "census_geocoder"
                      else " (borrowed from the nearest listed restaurant)"))
        with st.expander("About this data"):
            st.caption("2024 ACS 5-Year estimates describe residents of the "
                       "surrounding Census tract — they do not directly "
                       "measure restaurant customers. A dash means the "
                       "Census suppressed that estimate.")
    elif load_acs() is None:
        st.caption("Local demographics not fetched yet — run "
                   "`python scripts/fetch_acs_nyc.py` (see README).")
    else:
        st.caption("No Census tract could be resolved for this address — "
                   "demographics are not shown rather than guessed.")

    ui.eyebrow("Pedestrian context")
    if ped:
        c1, c2 = st.columns(2)
        c1.metric("Observed pedestrians", f"{ped['count']:,}")
        c2.metric("Distance", f"{ped['distance_m']:.0f}m",
                  help=ped["street"])
        tag = ("Measured nearby" if ped.get("represents_this_block")
               else "Bi-annual reference")
        st.caption(f"{ped['street']} · {tag}")
        with st.expander("About this measure"):
            st.caption("The nearest NYC DOT counting location — a periodic "
                       "observed count, not this doorway's live footfall.")
    else:
        st.caption("No pedestrian measurement available near this location.")


def render_recommendation(label: str, headline: str, proceed: dict | None,
                          caution: dict | None, cuisine: str) -> None:
    """The synthesis, on the one soft-plum surface in the product."""
    body = [headline]
    if proceed:
        body.append(proceed["detail"])
    if caution:
        body.append(caution["detail"])
    ui.recommendation_panel(
        label, body,
        positive=proceed["title"] if proceed else None,
        risk=caution["title"] if caution else None)


def render_limitations() -> None:
    with st.expander("Limits of this analysis"):
        st.markdown("**What this analysis cannot tell you**")
        st.markdown(
            "- **Rent, lease terms and fit-out cost.** Usually the largest "
            "number in the decision, and entirely invisible here.\n"
            "- **Whether a restaurant made money.** Nothing here measures "
            "profitability; a busy, well-reviewed restaurant can still lose.\n"
            "- **Management, food and marketing.** The things that most "
            "decide the outcome are in no public dataset.\n"
            "- **Exact opening and closing dates.** Inspection records show "
            "when a restaurant was *observed operating*, never when it "
            "opened or shut.\n"
            "- **Why anything closed.** A closure at an address does not "
            "establish that the address caused it.")
        st.caption(
            "A location screening and decision-support tool, not a predictor "
            "of restaurant success. The fit index weights are editorial, not "
            "fitted coefficients — the score compares addresses and is never "
            "a probability. Full disclosure in Data & methodology.")


def panel_for_compare():
    return load_panel()


def _compare_another(site: dict) -> None:
    """
    Put THIS location into the comparison and hand the map back.

    Stays inside the workspace. The previous version appended to a separate
    `saved` list and then set stage="landing", which dropped the user back
    at the opening prompt — a plan-authoring screen — merely because they
    wanted a second location. The prompt exists to start a NEW PLAN; it is
    not a location picker.

    The add's verdict is KEPT and shown. Discarding it made the primary
    call to action a silent no-op whenever the add was refused — a full
    tray, or an address whose neighbourhood is already queued — and then
    navigated to the map as though it had worked.
    """
    added, message = add_to_comparison(make_site_entry(site))
    st.session_state["_cmp_flash_subject_site"] = (added, message)
    if added:
        st.session_state["workspace_view"] = "explore"


def _change_concept() -> None:
    """The concept control lives in the workspace toolbar, so this just
    puts the user in front of it. It does NOT reset the plan — New Search
    is the only action that does that."""
    st.session_state["workspace_view"] = "explore"


def render_next(site: dict, cuisine: str, fit: int | None,
                verdicts: list[dict], landscape) -> None:
    """Step 12: keep the decision moving, without leaving the workspace."""
    st.markdown("### What next?")
    st.caption("Compare this location against another area or site.")
    # Both labels are short enough to stay on one line at desktop widths, so
    # neither button grows taller than the one beside it.
    left, right = ui.button_row(2)
    with left:
        st.button("Compare another location", type="primary",
                  width="stretch", key="next_compare",
                  on_click=_compare_another, args=(site,))
    with right:
        st.button("Change concept", width="stretch", key="next_concept",
                  on_click=_change_concept)


def render_trace(site, key, report, result, landscape, ped, lot) -> None:
    """
    The full chain from input to verdict, for validation — not styled, on
    purpose. Every number the page shows should be findable in here.
    """
    with st.expander("Developer trace", expanded=True):
        st.markdown("**Input → coordinates**")
        st.json({"address": site["label"], "lat": site["lat"], "lon": site["lon"],
                 "bbl": site.get("bbl"), "location_key": key,
                 "geocode_warning": site.get("warning")})
        st.markdown("**Matched restaurants at this address (CAMIS)**")
        occ = report["location"]["occupancy"]
        if occ is not None and len(occ):
            st.dataframe(occ[[c for c in ("camis", "name", "cuisine", "status",
                              "first_observed", "last_observed")
                              if c in occ.columns]],
                         width="stretch", hide_index=True)
        else:
            st.caption("none")
        st.markdown("**Cohorts**")
        st.json({"area": report["area"]["cohort"],
                 "area_same_cuisine": report["area"]["cohort_same_cuisine"],
                 "city": report["city"]["cohort"],
                 "city_same_cuisine": report["city"]["cohort_same_cuisine"],
                 "competitive_set": report["area"]["competitive_set"]})
        st.markdown("**Score components (risk, 0–100)**")
        st.json([{k: c.get(k) for k in ("key", "score", "weight", "available",
                                        "evidence")}
                 for c in result["components"]])
        st.json({"risk_total": result["score"], "coverage": result["coverage"],
                 "fit_shown": narrative.fit_score(result)})
        st.markdown("**Live competitors (Google)**")
        st.json({"ok": landscape.ok, "reason": landscape.reason,
                 "total": landscape.total, "strong": landscape.strong})
        if landscape.total:
            st.dataframe(landscape.competitors, width="stretch", hide_index=True)
        st.markdown("**Context joins**")
        st.json({"pedestrian": ped, "pluto": lot})


def render_methodology(result: dict, radius: int) -> None:
    with st.expander("Data & methodology"):
        st.markdown(
            "**Where the analysis comes from**\n\n"
            "| Source | Used for |\n|---|---|\n"
            "| NYC Health Department restaurant inspections, 2011–2017 archive "
            "| Which restaurants operated here and which have since gone |\n"
            "| NYC Health Department inspections, 2026 extract | Who is on the "
            "books now, with coordinates and tax lot |\n"
            "| NYC pre-permit inspections | Loaded and cross-checked; adds no dates "
            "beyond the main extract (verified during the audit) |\n"
            "| NYC PLUTO | Building and land-use characteristics |\n"
            "| NYC DOT pedestrian counts | Footfall at the nearest of 114 "
            "counting sites |\n"
            "| NYC Planning GeoSearch | Turning an address into coordinates "
            "and a tax lot |\n"
            "| Google Places (optional) | Who is trading now, ratings and "
            "review volume |\n"
            "| US Census 2024 ACS 5-Year tracts | Local demographic context "
            "— population, income and age indicators (details below) |\n")
        st.markdown(
            f"**How the fit score is built.** Six components, each measured "
            f"from public records within {radius}m, combined as a weighted "
            f"average and then inverted so that a higher number reads better. "
            f"A component that cannot be measured is dropped and named rather "
            f"than scored as average — this assessment used "
            f"{result['coverage']*100:.0f}% of the available evidence weight.")
        st.markdown(
            "| Component | Weight |\n|---|---|\n"
            + "\n".join(f"| {narrative.COMPONENTS[k][0]} | {w} |"
                        for k, w in scoring.WEIGHTS.items()))
        st.markdown(
            "**Survival is measured as a cross-check between two files, not as "
            "a duration.** Each health-department extract only keeps three "
            "years of inspections per restaurant, so a duration taken from one "
            "of them is not a lifespan. The analysis instead asks which "
            "restaurants trading in 2011–2017 were still on the books in 2026.")
        st.markdown(
            "**Claude is used only to convert natural-language restaurant plans "
            "into structured search parameters.** It does not score locations, "
            "provide market facts, or generate the recommendation; the parsed "
            "plan is shown for confirmation and is editable before anything "
            "runs. All assessment outputs are calculated from the "
            "application's listed data sources, and the app runs fully "
            "without the Anthropic API (a deterministic parser takes over).\n\n"
            "**Local demographic context comes from the 2024 ACS 5-Year "
            "Census tract estimates** (variables B01003, B19013, B01002, "
            "B23025_004, B25064), fetched for the five NYC counties by "
            "`scripts/fetch_acs_nyc.py` and read from a local CSV — the app "
            "never calls the Census API on a rerun. The address's tract comes "
            "from the official Census coordinate geocoder (2020 vintage); a "
            "neighbouring restaurant's tract is borrowed only when it exists "
            "unchanged in the 2020 tract table. ACS values are estimates "
            "about residents and do not directly measure restaurant "
            "customers. The legacy national-level DP05 file is unused. NTA "
            "rollups sum population and employment; tract medians are never "
            "averaged — income/age context indicators are population-weighted "
            "and labelled as derived.")
        st.caption(
            "Google ratings and review counts indicate customer sentiment and "
            "visibility. They do not measure profitability, and they favour "
            "places that have been open longer or that draw tourists.")




# ---------------------------------------------------------------- simulate
def render_sim_inputs(measurement=None, plan=None) -> tuple[fs.SimulationInputs, int, dict]:
    """
    The four questions that matter, then everything else behind a fold.

    Every widget range comes from INPUT_SPECS so the UI can never accept a
    value the engine would reject.
    """
    spec = fs.INPUT_SPECS
    # Values the user stated in their plan arrive as USER INPUT defaults;
    # everything else keeps the labelled model-assumption defaults.
    plan_spend = float(plan.average_spend) if plan and plan.average_spend else None
    plan_seats = int(plan.seats) if plan and plan.seats else None
    st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
    ui.eyebrow("Restaurant assumptions")
    a, b = st.columns(2)
    with a:
        spend = st.number_input(spec["average_customer_spend"]["label"],
                                min_value=spec["average_customer_spend"]["min"],
                                max_value=spec["average_customer_spend"]["max"],
                                value=plan_spend or spec["average_customer_spend"]["default"],
                                step=1.0,
                                help="From your plan (user input)" if plan_spend
                                else None)
        seats = st.number_input(spec["seats"]["label"],
                                min_value=spec["seats"]["min"],
                                max_value=spec["seats"]["max"],
                                value=plan_seats or spec["seats"]["default"],
                                step=1,
                                help="From your plan (user input)" if plan_seats
                                else None)
        days = st.slider(spec["operating_days_per_week"]["label"],
                         spec["operating_days_per_week"]["min"],
                         spec["operating_days_per_week"]["max"],
                         spec["operating_days_per_week"]["default"])
    with b:
        rent = st.number_input(spec["monthly_rent"]["label"],
                               min_value=spec["monthly_rent"]["min"],
                               max_value=spec["monthly_rent"]["max"],
                               value=spec["monthly_rent"]["default"], step=500.0)
        invest = st.number_input(spec["initial_investment"]["label"],
                                 min_value=spec["initial_investment"]["min"],
                                 max_value=spec["initial_investment"]["max"],
                                 value=spec["initial_investment"]["default"],
                                 step=10_000.0)
        horizon = st.selectbox("Simulation horizon", fs.HORIZON_CHOICES, index=2,
                               format_func=lambda m: f"{m//12} year"
                               + ("s" if m > 12 else ""))

    advanced: dict[str, float] = {}
    extras: dict = {"financing": None, "demand_method": "capacity",
                    "capture_rate": None, "service_period": "dinner",
                    "ramp_months": 0, "ramp_start_factor": 1.0,
                    "startup_breakdown": None}
    with st.expander("Advanced assumptions (model defaults — not observed NYC facts)"):
        st.caption(
            "These ship as model assumptions. They are editable, and none of "
            "them is derived from the location data.")
        cols = st.columns(2)
        model_keys = [k for k, v in spec.items() if v["source"] == "MODEL"]
        for i, key in enumerate(model_keys):
            v = spec[key]
            with cols[i % 2]:
                is_pct = v["max"] <= 1.0
                advanced[key] = st.number_input(
                    v["label"], min_value=float(v["min"]),
                    max_value=float(v["max"]), value=float(v["default"]),
                    step=0.01 if is_pct else 100.0, key=f"adv_{key}")

        if (measurement is not None
                and measurement.quality == pedestrian_dot.QUALITY_DIRECT
                and measurement.periods.get("dinner")):
            st.markdown("**Demand method**")
            method = st.radio(
                "Demand method",
                ["Capacity-based (default)", "Footfall-anchored (measured)"],
                horizontal=True, key="adv_demand", label_visibility="collapsed")
            if method.startswith("Footfall"):
                extras["demand_method"] = "footfall"
                extras["service_period"] = st.selectbox(
                    "Anchor service period", list(pedestrian_dot.SERVICE_PERIODS),
                    index=1)
                extras["capture_rate"] = st.number_input(
                    "Pedestrian capture rate — ASSUMPTION", 0.0001, 0.5,
                    0.01, 0.001, format="%.4f",
                    help="The share of measured passers-by assumed to become "
                         "seated covers. This is an assumption, not a measured "
                         "value; no calibrated NYC benchmark exists.")
                st.caption(
                    "Pedestrian capture models local walk-in demand and may "
                    "not capture destination/reservation traffic. Scenarios "
                    "use the measured p25 / median / p75 footfall — traffic "
                    "is never varied by editorial multipliers in this mode.")

        st.markdown("**Opening ramp** — model assumption, off by default")
        if st.checkbox("Ramp utilisation up over the first months",
                       value=False, key="adv_ramp"):
            extras["ramp_months"] = st.slider("Ramp length (months)", 2, 12, 6)
            extras["ramp_start_factor"] = st.slider(
                "Month-1 utilisation factor", 0.1, 1.0, 0.5, 0.05)

        st.markdown("**Financing**")
        if st.radio("Funding", ["No debt", "Debt financing"], horizontal=True,
                    key="adv_fin", label_visibility="collapsed") == "Debt financing":
            fc1, fc2, fc3 = st.columns(3)
            principal = fc1.number_input("Loan principal ($)", 0.0,
                                         20_000_000.0, 200_000.0, 10_000.0)
            rate = fc2.number_input("Annual interest rate", 0.0, 0.30, 0.08, 0.005)
            term = fc3.number_input("Loan term (months)", 12, 360, 84, 12)
            extras["financing"] = fs.FinancingInputs(
                enabled=True, loan_principal=float(principal),
                annual_interest_rate=float(rate), loan_term_months=int(term))
            st.caption("Principal repayments are financing cash flows, never "
                       "operating costs. Owner equity = startup investment − "
                       "debt; sources must equal uses.")

        st.markdown("**Startup investment breakdown** — optional; the sum "
                    "replaces the single total above")
        if st.checkbox("Itemise startup investment", value=False, key="adv_breakdown"):
            breakdown = {}
            bcols = st.columns(3)
            for i, component in enumerate(fs.STARTUP_COMPONENTS):
                label = component.replace("_", " ").title()
                with bcols[i % 3]:
                    breakdown[component] = st.number_input(
                        label, 0.0, 20_000_000.0, 0.0, 5_000.0,
                        key=f"su_{component}")
            extras["startup_breakdown"] = breakdown
            st.caption(f"Itemised total: "
                       f"${fs.total_startup_investment(breakdown, 0):,.0f} — "
                       f"no hidden startup costs; the total is exactly this sum.")

    total_investment = fs.total_startup_investment(
        extras["startup_breakdown"], float(invest))
    inputs = fs.SimulationInputs(
        average_customer_spend=float(spend), seats=int(seats),
        monthly_rent=float(rent), initial_investment=total_investment,
        operating_days_per_week=int(days), **advanced)
    return inputs, int(horizon), extras


def _money(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def _be_text(month: int | None, horizon: int) -> str:
    if month is None:
        return f"Not reached within {horizon // 12} years"
    return f"Month {month} (year {(month - 1) // 12 + 1})"


def render_sim_results(inputs: fs.SimulationInputs, horizon: int,
                       scenarios: dict, sensitivity: list[dict],
                       verdicts: list[dict], landscape, quality,
                       measurement=None, extras=None) -> None:
    expected = scenarios["expected"]
    theme = mapview.theme_for(active_theme())

    # --- animation (a rendering of the expected frame, skippable by scroll) --
    st.markdown("#### Watch the expected scenario unfold")
    st.caption("One step = one month. Occupancy on the floor matches that "
               "month's modelled utilisation. Skip it by scrolling — every "
               "number below is already computed.")
    components.html(
        sim_animation.build_animation_html(
            expected["frame"], inputs.seats,
            expected["summary"]["break_even_month"]),
        height=440)

    # --- headline outlook: the v2 metric taxonomy ----------------------------
    v2 = expected["v2"]
    be = v2["break_even"]
    financing_on = bool(extras and extras.get("financing")
                        and extras["financing"].enabled)
    ui.section("01", "Financial outlook",
               f"{horizon // 12}-year financial outlook")
    st.caption("Expected scenario · pre-tax scenario model · all figures are "
               "estimates under the assumptions listed below, not forecasts.")
    r1 = st.columns(4)
    r1[0].metric("Year 1 net sales (expected scenario)",
                 _money(v2["year1_net_sales"]))
    r1[1].metric("Pre-tax operating cash flow (Y1)",
                 _money(v2["year1_operating_cash_flow"]))
    r1[2].metric("Pre-tax operating cash margin",
                 f"{v2['operating_cash_margin']:.0%}")
    r1[3].metric("Prime cost",
                 f"{v2['prime_cost_pct']:.0%} of sales",
                 help="COGS plus total labour, as a share of net sales.")
    r2 = st.columns(4)
    r2[0].metric("Monthly operating break-even",
                 _money(be["break_even_sales"]) if be["reachable"] else "Not reachable",
                 help="Sales at which contribution margin covers fixed cash "
                      "costs — a different question from investment payback.")
    if be["reachable"] and be["break_even_covers"]:
        r2[0].caption(f"≈ {be['break_even_covers']:,.0f} covers")
    payback = v2["payback_month"]
    r2[1].metric("Owner investment payback",
                 f"Month {payback}" if payback is not None
                 else f"Not reached within {horizon // 12} years",
                 help="When cumulative owner cash flow has recovered the "
                      "owner's investment.")
    r2[2].metric(f"{horizon // 12}-year cumulative owner ROI",
                 f"{v2['cumulative_owner_roi_pct']:.0f}%"
                 if v2["cumulative_owner_roi_pct"] is not None else "n/a",
                 help="Cumulative owner cash flow over owner equity. Excludes "
                      "any future sale value of the business.")
    r2[3].metric("Cash return multiple",
                 f"{v2['cash_return_multiple']:.2f}×"
                 if v2["cash_return_multiple"] is not None else "n/a")
    r3 = st.columns(4)
    r3[0].metric("Sales-to-investment",
                 f"{v2['sales_to_investment']:.1f}×"
                 if v2["sales_to_investment"] is not None else "n/a",
                 help="Year-1 net sales over total startup investment — a "
                      "feasibility screen, not an ROI.")
    r3[1].metric("Project ROI (Y1)",
                 f"{v2['project_roi_year1_pct']:.0f}%"
                 if v2["project_roi_year1_pct"] is not None else "n/a",
                 help="Pre-tax, before financing: operating cash flow over "
                      "TOTAL startup investment.")
    if financing_on:
        r3[2].metric("Owner ROI (Y1)",
                     f"{v2['owner_roi_year1_pct']:.0f}%"
                     if v2["owner_roi_year1_pct"] is not None else "n/a",
                     help="Pre-tax cash return on equity: owner cash flow "
                          "after debt service over owner equity of "
                          f"${v2['owner_equity']:,.0f}.")

    # Required footfall capture: what the capacity assumptions imply against
    # the measured street. Context only — never classified easy/hard.
    if (measurement is not None
            and measurement.quality == pedestrian_dot.QUALITY_DIRECT
            and (not extras or extras.get("demand_method") != "footfall")):
        dinner = measurement.periods.get("dinner", {})
        if dinner.get("median"):
            daily_covers = expected["frame"]["customers"].iloc[0] / (
                inputs.operating_days_per_week * 52 / 12)
            req = pedestrian_dot.required_capture_rate(
                daily_covers, dinner["median"])
            if req is not None:
                st.caption(
                    f"**Required local footfall capture: {req:.2%}.** The "
                    f"capacity assumptions imply {daily_covers:,.0f} covers a "
                    f"day against a measured median dinner-period footfall of "
                    f"{dinner['median']:,.0f} — no validated benchmark exists "
                    f"for whether a given share is achievable.")

    # --- scenario table -------------------------------------------------------
    ui.section("02", "Scenarios", None)
    st.markdown("#### Three scenarios")
    cfg_rows = []
    for name, cfg in fs.SCENARIO_CONFIG.items():
        cfg_rows.append(f"**{name.title()}**: utilisation ×{cfg['utilization_multiplier']}, "
                        f"turns ×{cfg['table_turns_multiplier']}, "
                        f"spend ×{cfg['spend_multiplier']}, "
                        f"fixed costs ×{cfg['fixed_cost_multiplier']}")
    st.caption("Scenario deltas (model assumptions, applied to your inputs): "
               + " · ".join(cfg_rows))
    if scenarios["expected"].get("footfall"):
        ft = pd.DataFrame([
            dict(Scenario=k.title(),
                 **{"Footfall quantile": v["footfall"]["quantile"],
                    "Measured footfall": f"{v['footfall']['footfall']:,.0f}",
                    "Capture rate (assumption)": f"{v['footfall']['capture_rate']:.2%}",
                    "Capacity (covers/day)": f"{v['footfall']['capacity_daily']:,.0f}",
                    "Resulting covers/day": f"{v['footfall']['covers_daily']:,.0f}"})
            for k, v in scenarios.items()])
        st.dataframe(ft, width="stretch", hide_index=True)
        st.caption("Footfall-anchored: traffic varies only by observed "
                   "quantile; the capture rate is one explicit assumption.")

    rows = {
        "Year 1 revenue": lambda s: _money(s["year1_revenue"]),
        "Year 1 operating profit": lambda s: _money(s["year1_operating_profit"]),
        "Year 1 operating margin": lambda s: f"{s['year1_operating_margin']:.0%}",
        "Break-even": lambda s: _be_text(s["break_even_month"], horizon),
        f"{horizon // 12}-year cumulative operating profit":
            lambda s: _money(s["cumulative_operating_profit"]),
        f"{horizon // 12}-year ROI":
            lambda s: "n/a" if s["roi"] is None else f"{s['roi']:.1f}×",
    }

    table = {name.title(): {label: fmt(scenarios[name]["summary"])
                            for label, fmt in rows.items()}
             for name in ("conservative", "expected", "optimistic")}
    for name in table:
        sv2 = scenarios[name.lower()]["v2"]
        table[name][f"{horizon // 12}-yr cumulative owner ROI"] = (
            f"{sv2['cumulative_owner_roi_pct']:.0f}%"
            if sv2["cumulative_owner_roi_pct"] is not None else "n/a")
        table[name]["Owner payback"] = (
            f"Month {sv2['payback_month']}" if sv2["payback_month"] is not None
            else "Not reached")
    st.dataframe(pd.DataFrame(table), width="stretch")

    with st.expander("Annual ROI table"):
        st.dataframe(pd.DataFrame([
            {"Year": row["year"],
             "Project ROI": f"{row['project_roi_pct']:.1f}%"
                            if row["project_roi_pct"] is not None else "n/a",
             "Owner ROI": f"{row['owner_roi_pct']:.1f}%"
                          if row["owner_roi_pct"] is not None else "n/a",
             "Cumulative owner ROI": f"{row['cumulative_owner_roi_pct']:.1f}%"
                          if row["cumulative_owner_roi_pct"] is not None else "n/a",
             "Cash return multiple": f"{row['cash_return_multiple']:.2f}×"
                          if row["cash_return_multiple"] is not None else "n/a"}
            for row in scenarios["expected"]["v2"]["annual"]]),
            width="stretch", hide_index=True)

    # --- chart 1: cumulative return ------------------------------------------
    palette = dict(zip(("conservative", "expected", "optimistic"),
                       theme["categorical"]))
    fig = go.Figure()
    for name in ("conservative", "expected", "optimistic"):
        f = scenarios[name]["frame"]
        fig.add_trace(go.Scatter(
            x=f["month"], y=f["cumulative_return_after_investment"],
            mode="lines", name=name.title(),
            line=dict(color=palette[name], width=2)))
    fig.add_hline(y=0, line_color=theme["muted"], line_width=1)
    fig.update_layout(
        height=340, margin=dict(l=0, r=10, t=30, b=0),
        title=dict(text="Cumulative return after investment (crosses zero at "
                        "break-even)", font=dict(size=13)),
        paper_bgcolor=theme["surface"], plot_bgcolor=theme["surface"],
        font=dict(color=theme["ink"]), xaxis_title="Month", yaxis_title=None,
        legend=dict(orientation="h", y=1.12))
    fig.update_xaxes(gridcolor="#e6e2e6")
    fig.update_yaxes(gridcolor="#e6e2e6")
    st.plotly_chart(fig, width="stretch")

    # --- chart 2: monthly revenue + profit, expected --------------------------
    f = expected["frame"]
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=f["month"], y=f["revenue"], mode="lines",
                              name="Revenue (expected)",
                              line=dict(color=theme["categorical"][0], width=2)))
    fig2.add_trace(go.Scatter(x=f["month"], y=f["operating_profit"], mode="lines",
                              name="Operating profit (expected)",
                              line=dict(color=theme["categorical"][2], width=2)))
    fig2.add_hline(y=0, line_color=theme["muted"], line_width=1)
    fig2.update_layout(height=300, margin=dict(l=0, r=10, t=30, b=0),
                       title=dict(text="Monthly revenue and operating profit — "
                                       "expected scenario", font=dict(size=13)),
                       paper_bgcolor=theme["surface"], plot_bgcolor=theme["surface"],
                       font=dict(color=theme["ink"]), xaxis_title="Month",
                       legend=dict(orientation="h", y=1.14))
    fig2.update_yaxes(gridcolor="#e6e2e6")
    st.plotly_chart(fig2, width="stretch")

    # --- sensitivity ----------------------------------------------------------
    st.markdown("#### What the outcome hinges on (±10%)")
    st.caption("Each lever moved ±10% on its own, expected scenario re-run in "
               "full. Computed, not narrated.")
    sens_rows = []
    for r in sensitivity:
        def fmt_side(side):
            d = r[side]
            roi = ("—" if d["roi_delta"] is None
                   else f"{d['roi_delta']:+.2f}× ROI")
            be = ("" if d["break_even_delta_months"] in (None, 0)
                  else f", break-even {abs(d['break_even_delta_months'])} mo "
                       f"{'later' if d['break_even_delta_months'] > 0 else 'earlier'}")
            return roi + be
        sens_rows.append({"Assumption": r["label"],
                          "+10%": fmt_side("up"), "−10%": fmt_side("down")})
    st.dataframe(pd.DataFrame(sens_rows), width="stretch", hide_index=True)

    # --- why does this simulation look like this? ----------------------------
    st.markdown("#### Why does this simulation look like this?")
    st.caption("The financial model runs on your assumptions; these location "
               "signals from the assessment are context beside it, not inputs "
               "multiplied into it.")
    good = [v for v in verdicts if v["tone"] == "good"]
    bad = [v for v in verdicts if v["tone"] == "concern"]
    if landscape is not None and getattr(landscape, "ok", False) and landscape.strong >= 3:
        bad = bad + [{"label": "Live competition",
                      "verdict": f"{landscape.strong} strong rivals",
                      "evidence": f"{landscape.strong} strong competitors "
                                  f"within {landscape.radius_m:.0f}m are "
                                  f"trading now."}]
    left, right = st.columns(2)
    with left:
        st.markdown("**Positive location signals**")
        if good:
            for v in good:
                st.success(f"**{v['label']}: {v['verdict']}**")
                st.caption(v["evidence"])
        else:
            st.caption("None of the measured signals favours this site.")
    with right:
        st.markdown("**Pressure points**")
        if bad:
            for v in bad:
                st.error(f"**{v['label']}: {v['verdict']}**")
                st.caption(v["evidence"])
        else:
            st.caption("No measured signal counts against this site.")

    # --- assumptions, sources, evidence language ------------------------------
    with st.expander("Simulation assumptions and their sources"):
        rows = []
        for f_ in fs.INPUT_SPECS:
            spec = fs.INPUT_SPECS[f_]
            value = getattr(inputs, f_)
            shown = f"{value:.0%}" if spec["max"] <= 1.0 and isinstance(value, float) else (
                f"${value:,.0f}" if "($" in spec["label"] or "($/month)" in spec["label"]
                else str(value))
            rows.append({"Assumption": spec["label"], "Value": shown,
                         "Source": {"USER": "User input",
                                    "MODEL": "Model assumption"}[spec["source"]]})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("No location-informed inputs feed the financial model in "
                   "this version: there is no calibrated relationship between "
                   "the location signals and restaurant revenue, and an "
                   "uncalibrated multiplier would be a black box.")

    with st.expander("How customer volume was derived"):
        frame1 = expected["frame"].iloc[0]
        daily = frame1["customers"] / (inputs.operating_days_per_week * 52 / 12)
        if extras and extras.get("demand_method") == "footfall" and expected.get("footfall"):
            ft = expected["footfall"]
            ui.bench_rows([
                (f"{extras['service_period'].title()} footfall (median)",
                 f"{ft['footfall']:,.0f} · MEASURED"),
                ("Capture rate", f"{ft['capture_rate']:.2%} · ASSUMPTION"),
                ("Potential walk-in covers",
                 f"{ft['footfall'] * ft['capture_rate']:,.0f} · DERIVED"),
                ("Restaurant capacity (covers/day)",
                 f"{ft['capacity_daily']:,.0f} · DERIVED"),
                ("Simulated covers/day", f"{ft['covers_daily']:,.0f} · DERIVED"),
            ])
        else:
            rows = [
                ("Seats", f"{inputs.seats} · USER INPUT"),
                ("Table turns", f"{inputs.table_turns_per_day} · MODEL ASSUMPTION"),
                ("Utilisation", f"{frame1['utilization']:.0%} · MODEL ASSUMPTION"),
                ("Simulated covers/day", f"{daily:,.0f} · DERIVED"),
            ]
            if (measurement is not None
                    and measurement.quality == pedestrian_dot.QUALITY_DIRECT
                    and measurement.periods.get("dinner", {}).get("median")):
                med = measurement.periods["dinner"]["median"]
                req = pedestrian_dot.required_capture_rate(daily, med)
                rows += [("Measured dinner footfall", f"{med:,.0f} · MEASURED"),
                         ("Required footfall capture",
                          f"{req:.2%} · DERIVED" if req else "n/a")]
            ui.bench_rows(rows)
        st.caption("Average customer spend is interpreted as the pre-sales-tax "
                   "net check, before voluntary tips passed through to staff. "
                   "Sales tax collected is not restaurant revenue.")

    with st.expander("How returns are calculated"):
        ui.bench_rows([
            ("Project ROI", "annual pre-financing cash flow ÷ total startup investment"),
            ("Owner ROI", "annual owner cash flow after debt service ÷ owner equity"),
            ("Cumulative ROI", "cumulative owner cash flow ÷ owner equity"),
            ("Cash return multiple", "same ratio, expressed as ×"),
            ("Payback", "when cumulative owner cash flow equals owner investment"),
            ("Operating break-even", "fixed cash costs ÷ contribution-margin ratio"),
            ("Sales-to-investment", "annual net sales ÷ startup investment"),
            ("Prime cost", "COGS + total labour, as % of net sales"),
        ])
        st.caption("Pre-tax, cash-based, before any exit value. Definitions "
                   "follow standard restaurant-industry usage — see "
                   "docs/financial_methodology_sources.md.")

    st.caption(
        "**Pre-tax scenario model.** Financial results are scenario estimates "
        "based on selected assumptions and available location indicators. "
        "They are not guaranteed forecasts. Measured pedestrian activity does "
        "not guarantee restaurant customer conversion. "
        f"{horizon // 12}-year ROI excludes any future sale value of the "
        "business.")

    label = quality[0] if quality else "—"
    st.info(
        f"**Location evidence: {label.lower()}** · **Financial model: "
        f"scenario-based estimate.** Financial results are scenario estimates "
        f"based on selected assumptions and available location indicators. "
        f"They are not guaranteed forecasts.")


def simulate_page(panel, locs) -> None:
    address = st.session_state.get("address")
    cuisine = st.session_state.get("cuisine")
    if not address or not cuisine:
        st.session_state["stage"] = "landing"
        st.rerun()
        return

    top_l, top_r = st.columns([4, 1.4])
    with top_r:
        if st.button("← Back to assessment", width="stretch"):
            st.session_state["stage"] = "results"
            st.rerun()
        if st.button("Start over", width="stretch", key="sim_startover"):
            st.session_state["stage"] = "landing"
            st.rerun()
    with top_l:
        ui.query_context(cuisine, st.session_state.get("price") or "—",
                         address)

    try:
        site = geocode_cached(address)
    except geocode.GeocodeError as exc:
        st.error(str(exc))
        return

    # Assessment context, from the same cached path the results page uses.
    key = resolve_location_key(locs, site)
    radius = config.DEFAULT_RADIUS_M
    report = analysis.site_report(panel, locs, site["lat"], site["lon"],
                                  cuisine, radius, key)
    result = score_cached(report, panel, None, None, radius,
                          (site["lat"], site["lon"], cuisine, radius, key))
    landscape = competitors_cached(site["lat"], site["lon"], cuisine,
                                   google_places.DEFAULT_RADIUS_M, site,
                                   google_api_key())
    verdicts = narrative.component_verdicts(result)
    quality = narrative.evidence_quality(result, report, landscape)

    ui.eyebrow("Financial simulation")
    ui.display("What could this restaurant<br>look like financially?")
    st.markdown("Explore revenue, costs, profitability and break-even under "
                "transparent assumptions — every one visible and editable. "
                "Pre-tax scenario model.")

    measurement = pedestrian_cached(site["lat"], site["lon"], nyc_token())
    if measurement.quality in (pedestrian_dot.QUALITY_DIRECT,
                               pedestrian_dot.QUALITY_REFERENCE):
        dinner = measurement.periods.get("dinner", {})
        if dinner.get("median") is not None:
            ui.eyebrow("Location demand — measured nearby")
            ui.stat_strip([
                (f"{dinner['median']:,.0f}",
                 "Typical measured dinner-period pedestrians"),
                (f"{measurement.distance_m:.0f}m",
                 "From NYC DOT sensor"),
                ({"DIRECT_NEARBY": "Measured nearby",
                  "NEARBY_REFERENCE": "Reference only"}[measurement.quality],
                 "Measurement quality"),
            ])
            st.caption(
                f"Sensor: {measurement.sensor_name} · measured "
                f"{measurement.period_start} to {measurement.period_end} · "
                f"{measurement.valid_days} sufficiently complete days. "
                + ("This sensor is close enough to describe this block."
                   if measurement.quality == pedestrian_dot.QUALITY_DIRECT else
                   "Reference context only — too far to stand in for this "
                   "block, and never used in the financial model."))
    elif measurement.quality == pedestrian_dot.QUALITY_UNAVAILABLE:
        st.caption("Measured pedestrian data is currently unavailable; the "
                   "simulation runs on capacity assumptions as always.")

    with st.container(border=True):
        inputs, horizon, extras = render_sim_inputs(
            measurement, st.session_state.get("confirmed_plan"))
        run = st.button("Run simulation", type="primary", width="stretch")

    # Stale-state guard: results are only ever shown for the stamped query.
    sim_id = (site["label"], cuisine)
    if st.session_state.get("sim_location_id") != sim_id:
        st.session_state.pop("sim_results", None)
        st.session_state["sim_location_id"] = sim_id

    if run:
        errors, warnings = fs.validate_simulation_inputs(inputs)
        errors += fs.validate_financing(inputs.initial_investment,
                                        extras["financing"])
        if errors:
            for e in errors:
                st.error(e)
            return
        for w in warnings:
            st.warning(w)
        footfall_cov = None
        if extras["demand_method"] == "footfall":
            stats = measurement.periods.get(extras["service_period"], {})
            footfall_cov = fs.footfall_scenario_covers(
                inputs, stats, extras["capture_rate"])
        st.session_state["sim_results"] = {
            "inputs": inputs, "horizon": horizon, "extras": extras,
            "measurement": measurement,
            "scenarios": fs.calculate_all_scenarios_v2(
                inputs, horizon, financing=extras["financing"],
                footfall_covers_by_scenario=footfall_cov,
                ramp_months=extras["ramp_months"],
                ramp_start_factor=extras["ramp_start_factor"]),
            "sensitivity": fs.calculate_sensitivity(inputs, horizon),
        }

    stored = st.session_state.get("sim_results")
    if stored:
        st.divider()
        render_sim_results(stored["inputs"], stored["horizon"],
                           stored["scenarios"], stored["sensitivity"],
                           verdicts, landscape, quality,
                           measurement=stored.get("measurement"),
                           extras=stored.get("extras"))




# ---------------------------------------------------------------- workspace
LAYER_CHOICES = {
    "Concept fit": "concept_fit",
    "Competition (live)": "competition",
    "Cuisine density": "cuisine_density",
    "Observed turnover": "turnover",
    "Persistence": "persistence",
    "Demographics — income context": "income_context",
    "Demographics — population": "population",
    "Pedestrian context": "pedestrian",
    "Opportunity gap": "opportunity_gap",
    "Evidence quality": "evidence",
}


#: The restaurant filter's user-facing vocabulary. These strings are the
#: SINGLE source of truth: the segmented control, the microcopy and the map
#: legend all read from them, so the control and the legend can never drift
#: apart. "Closest / Similar / All" said nothing about what was being
#: matched; these name the actual evidence tier.
EXACT = "Exact concept"
SAME_CUISINE = "Same cuisine"
ALL_RESTAURANTS = "All restaurants"

FILTER_HELP = ("Exact concept uses the most specific match supported by the "
               "available data. Same cuisine shows broader cuisine "
               "competitors. All restaurants shows the full restaurant "
               "landscape.")


#: Where the map's explanatory lines are parked for this run.
FILTER_EXPLANATION_KEY = "_filter_explanation"


def record_filter_explanation(mode: str, cuisine: str | None,
                              concept: str | None, tiers: dict,
                              radius_m: int | None = None) -> None:
    """
    Keep the map's explanations, stop printing them above the map.

    Three captions used to stack between the toolbar and the map — what the
    current filter shows, the exact-concept caveat, and the reconciliation
    between the map's tiers and the competition read. Each is worth saying;
    none is worth a paragraph in the one place the map should start. They
    are collected here and rendered inside the analysis pane instead, where
    the numbers they explain actually appear.
    """
    lines = [filter_caption(mode, cuisine, concept,
                            limited=bool(tiers.get("unidentifiable")))
             + (f" Within {radius_m}m of this address." if radius_m else "")]
    if tiers.get("note"):
        lines.append(tiers["note"])
    comparable = len(tiers.get("closest", [])) + len(tiers.get("similar", []))
    if cuisine and comparable:
        lines.append(
            f"Exact concept ({len(tiers['closest'])}) and same cuisine "
            f"({len(tiers['similar'])}) together are the {comparable} "
            f"comparable competitors the competition read uses; “other” "
            f"restaurants are counted in the {tiers['total']} total, not as "
            f"competitors.")
    st.session_state[FILTER_EXPLANATION_KEY] = lines


def render_filter_explanation() -> None:
    """The map's explanations, inside the analysis pane and collapsed."""
    lines = st.session_state.get(FILTER_EXPLANATION_KEY)
    if not lines:
        return
    with st.expander("What the map is showing"):
        for line in lines:
            st.caption(line)


def filter_caption(mode: str, cuisine: str | None, concept: str | None,
                   limited: bool) -> str:
    """One line saying exactly what the selected tier is showing."""
    if mode == ALL_RESTAURANTS:
        return "All current restaurant establishments in this area."
    if mode == SAME_CUISINE:
        return f"All {cuisine} restaurants in this area."
    if limited:
        return ("Limited data: the records identify the broader cuisine but "
                "do not reliably classify restaurants at this concept "
                "level.")
    if cuisine and concept:
        return (f"{cuisine} restaurants with {concept.lower()}-related "
                f"concept evidence.")
    if cuisine:
        return f"The closest {cuisine} matches in this area."
    return f"Establishments matching “{concept}” in this area." if concept \
        else "The closest matches in this area."


#: Layers that require a cuisine. With no cuisine given, the layer list
#: simply omits them — concept-independent evidence carries the workspace.
CUISINE_LAYERS = {"concept_fit", "cuisine_density", "opportunity_gap"}


def layer_choices_for(cuisine: str | None) -> dict:
    if cuisine:
        return LAYER_CHOICES
    filtered = {label: key for label, key in LAYER_CHOICES.items()
                if key not in CUISINE_LAYERS}
    # Persistence leads when no cuisine is set — it is the default read.
    ordered = {"Persistence": filtered.pop("Persistence")}
    ordered.update(filtered)
    return ordered


#: Concept term -> DOHMH-name token for the CLOSEST MATCH tier. Only
#: concepts whose names actually announce them ("X BRUNCH", "Y COFFEE") are
#: listed — everything else honestly reads limited concept-level evidence.
CONCEPT_NAME_TOKENS = {
    "brunch": "BRUNCH", "coffee": "COFFEE", "cafe": "CAFE",
    "café": "CAFE", "bakery": "BAKERY", "patisserie": "PATISSERIE",
    "deli": "DELI", "diner": "DINER", "pizzeria": "PIZZERIA",
    "steakhouse": "STEAKHOUSE", "izakaya": "IZAKAYA",
    "wine bar": "WINE", "cocktail": "COCKTAIL", "juice": "JUICE",
    "tea": "TEA", "sandwich": "SANDWICH", "dessert": "DESSERT",
}


def concept_token(concept: str | None) -> str | None:
    """The name-matchable token inside the user's concept phrase, if any."""
    if not concept:
        return None
    lowered = concept.lower()
    for term in sorted(CONCEPT_NAME_TOKENS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            return CONCEPT_NAME_TOKENS[term]
    return None


def _hover_frame(panel) -> pd.DataFrame:
    names = nta_names()
    return pd.DataFrame({"name": pd.Series(names)})


@st.cache_data(show_spinner=False)
def zip_to_nta(_panel, zipcode: str) -> str | None:
    """
    ZIP -> the modal 2020 NTA among that ZIP's DOHMH restaurants.
    Deterministic and existing-data only. GeoSearch is NEVER used for bare
    ZIPs — verified live, it resolves "10003" to 10003 Springfield Boulevard
    in Queens Village, the exact silent mis-placement this avoids.
    """
    merged = panel_with_nta_cached(_panel)
    counts = merged[merged["zipcode"] == zipcode]["nta_2020"].value_counts()
    return counts.index[0] if len(counts) else None


def neighborhood_to_nta(name: str, borough: str | None = None) -> str | None:
    """
    Name -> best-candidate NTA code via the deterministic resolver. A name
    that maps to several areas returns the inventory-leading one here; the
    confirm page is where the alternatives are surfaced for the user.
    """
    codes = resolve_area_candidates(name or "", borough)
    return codes[0] if codes else None


def polygon_bounds(code: str) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) of one NTA."""
    x0, y0, x1, y1 = nta_index().features[code]["bbox"]
    return y0, y1, x0, x1


#: The map PANE in CSS pixels per workspace view: the map column is ~66% of
#: the content width in Explore and ~44% in Assess, and the figure height is
#: fixed at 640 (workspace_map._base_layout). A fit needs BOTH: a zoom level
#: is meaningless without the viewport it has to fill.
#:
#: MEASURED, not estimated, and measured at the NARROWEST desktop window the
#: product targets (1280px). The previous values — 920 and 615 — were about
#: 14% wider than the panes actually are there (806 and 532 measured in the
#: browser), so a fit computed to fill 71% of an imagined pane overflowed a
#: real one: an East Village site showed only two of the district's four
#: bbox corners. Assuming the narrow case is the safe direction, because a
#: wider window then simply leaves more margin around the same content,
#: whereas assuming a wide one crops the content on every smaller screen.
#:
#: RE-MEASURED after v8.2. The sticky two-pane layout narrowed both columns,
#: so 806/532 became stale by 10.3% and 4.9% — a fit aiming for 71% of the
#: pane actually filled 78% in Explore, pushing a district's boundary toward
#: the edge the padding exists to keep clear. Live values at 1280px, taken
#: three times per view and identical each time: Explore 731, Assess 507.
#: A platform with classic (non-overlay) scrollbars loses a further ~15px,
#: which is ~1% here and well inside the padding, so it is not subtracted.
#:
#: "compare" never reaches a fit — render_compare_view returns before the
#: map is built — so its entry exists only to keep the fit test's sweep over
#: every declared view total. It is deliberately left at the Assess pane.
MAP_PANE_PX = {"explore": (731, 640), "assess": (507, 640),
               "compare": (507, 640)}

#: Padding factor: the binding axis lands at ~1/1.4 = 71% of the pane, so
#: the whole district shows with visible surrounding geography on every
#: side (the 65-80% target).
AREA_FIT_PADDING = 1.4

#: Tile size the basemap renders at. CARTO's VECTOR styles serve 512px
#: tiles, so at a given zoom a pixel covers HALF the longitude that the old
#: 256px raster tiles did. Measured on the live map: the viewport spanned
#: 0.0295° where a 256px assumption predicted 0.0588°. Using 256 here made
#: every area fit exactly one zoom level too deep — the reported "zooms too
#: far into the district".
BASEMAP_TILE_PX = 512


def zoom_for_bounds(bounds, viewport_px: int = 820,
                    viewport_h_px: int = 640
                    ) -> tuple[tuple[float, float], float]:
    """
    True fitBounds: the centre of the area, and the largest zoom at which
    the WHOLE polygon still fits — both axes, with padding.

    Two earlier bugs are corrected here. `log2(360 / span)` fits a span to
    one 256px tile, which on a real map left the area covering a fifth of
    the view. Fitting only the width then over-zoomed TALL districts:
    Murray Hill, Gramercy and Chelsea filled 94% of the map's height, so
    their boundaries sat flush against the edge with no context around
    them. The zoom is now the MINIMUM of the width- and height-constrained
    zooms, so whichever axis binds is the one that fits.
    """
    import math
    min_lat, max_lat, min_lon, max_lon = bounds
    center = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
    # Web Mercator: a degree of longitude is a constant screen width, while
    # a degree of latitude covers 1/cos(lat) MORE screen.
    lat_scale = max(math.cos(math.radians(center[0])), 1e-6)
    span_lon = max(max_lon - min_lon, 1e-4) * AREA_FIT_PADDING
    span_lat = max((max_lat - min_lat) / lat_scale, 1e-4) * AREA_FIT_PADDING
    zoom_w = math.log2(360.0 * viewport_px / (BASEMAP_TILE_PX * span_lon))
    zoom_h = math.log2(360.0 * viewport_h_px / (BASEMAP_TILE_PX * span_lat))
    return center, float(np.clip(min(zoom_w, zoom_h), 9.5, 16.0))


def request_loading(message: str) -> None:
    """
    Declare that the run about to happen will do real work.

    THE ONE WAY a loading state is raised. The action that causes the work
    names it; main() paints the overlay before any of that work starts and
    takes it down when the page is complete. Nothing else in the app decides
    when a loader appears, so two loaders can never race and a loader can
    never be left behind by a code path that forgot to close it.

    Deliberately NOT called for cheap interactions — opening Method,
    switching a tab, removing a comparison entry, flipping a cached
    restaurant filter. A loader on work that finishes in 100ms is a flash,
    not feedback.
    """
    st.session_state[branding.LOADING_KEY] = message


#: The closest a SITE view is ever framed, and how much room it leaves
#: around the district that contains it. A site used to be pinned at a flat
#: 13.6 regardless of where it was, which works for a compact neighbourhood
#: and fails badly for a large one: the containing district fell entirely
#: outside the frame, so the map answered "what is near this address?"
#: while losing "which neighbourhood is this?" — the context the site
#: analysis is read against.
SITE_MAX_ZOOM = 13.6
#: The search radius gets this much room around it, so the ring is never
#: flush with the pane edge and the markers just outside it still read as
#: "outside the search" rather than "off the map".
SITE_RADIUS_HEADROOM = 1.35
#: And the nearest district edge gets this much, so the boundary lands
#: inside the frame rather than exactly on it.
SITE_EDGE_HEADROOM = 1.25


def site_zoom(site: dict, radius_m: int | None = None) -> float:
    """
    Frame a site so BOTH things a site map is for are visible: the whole
    search radius the competitors are drawn from, and the nearest edge of
    the district containing the address.

    Two rejected rules, and why:

    * The district's OWN fit (zoom_for_bounds on its bbox) answers "at what
      zoom does this polygon fit when centred on ITS centre". A site map is
      centred on the ADDRESS, so for an address near an edge most of the
      district still fell outside the frame.
    * Mirroring the district's bbox around the site fixes that, but at the
      cost that matters most: an address at the edge of a large district
      forces a view wide enough to reach the far side, which shrinks the
      competitor dots — the actual subject — to specks.

    So the rule is the union of the two things that must be on screen. The
    radius is the analytical scope of every marker, and the nearest
    boundary is enough to answer "which neighbourhood is this?" — the whole
    district is not required for that, and demanding it would trade away
    the competitive detail the view exists to show.
    """
    lat, lon = site["lat"], site["lon"]
    pane_w, pane_h = MAP_PANE_PX.get(
        st.session_state.get("workspace_view", "assess"), (820, 640))
    radius_m = int(radius_m or st.session_state.get(
        "ws_radius", st.session_state.get("_radius_mirror",
                                          config.DEFAULT_RADIUS_M)))
    # the search radius, in degrees, with room to breathe around it
    half_lat = (radius_m / 111_320.0) * SITE_RADIUS_HEADROOM
    half_lon = half_lat / max(math.cos(math.radians(lat)), 1e-6)

    code = nta_index().locate(lat, lon)
    if code is not None:
        x0, y0, x1, y1 = nta_index().features[code]["bbox"]
        # distance to the NEAREST edge on each axis, so at least one piece
        # of boundary is in frame wherever inside the district the site is
        near_lon = min(abs(lon - x0), abs(x1 - lon)) * SITE_EDGE_HEADROOM
        near_lat = min(abs(lat - y0), abs(y1 - lat)) * SITE_EDGE_HEADROOM
        half_lon = max(half_lon, near_lon)
        half_lat = max(half_lat, near_lat)

    _, fit = zoom_for_bounds((lat - half_lat, lat + half_lat,
                              lon - half_lon, lon + half_lon),
                             viewport_px=pane_w, viewport_h_px=pane_h)
    return float(min(SITE_MAX_ZOOM, fit))


def select_area(code: str, source: str = "unknown") -> None:
    """
    THE one area-selection handler. Every route — polygon click, Top-match
    button, prompt-resolved neighborhood, Back to explore, any area list —
    goes through here, so they can never behave differently.

    The FIT TOKEN is the fix for the "clicking an area doesn't zoom" bug.
    The fit used to be driven by plotly's uirevision keyed on the area id
    alone: re-selecting the SAME area after panning produced an identical
    revision string, so plotly kept the user's stale viewport and the map
    never moved. Every selection EVENT now bumps a counter, so the revision
    is new every time an area is deliberately chosen (guaranteeing a fit),
    while ordinary reruns — filter flips, tab changes, navigation — leave it
    untouched and preserve the user's pan/zoom.
    """
    if code not in nta_index().features:
        return
    st.session_state["selected_area"] = code
    st.session_state["area_fit_token"] = (
        st.session_state.get("area_fit_token", 0) + 1)
    st.session_state["area_fit_source"] = source
    # IS THIS AREA THE SUBJECT, OR JUST CONTEXT?
    # Every route here sets `selected_area`, but they do not all mean the
    # same thing. Clicking a polygon, searching a neighbourhood or picking
    # a top match means "analyse THIS area". "Back to explore" means "put
    # my site's neighbourhood on the map, my site analysis is one click
    # away" — the site is still the subject.
    #
    # Without the distinction the workspace contradicted itself: after
    # Back-to-explore, `workspace_mode` stays "site", so clicking Gramercy
    # showed "### Gramercy" in the panel while the add control still
    # offered the ADDRESS, and "Open full analysis →" on Gramercy opened
    # the site analysis. Mode alone cannot express this, because the site
    # must survive an excursion to another area.
    st.session_state[AREA_IS_SUBJECT] = source != "back_to_explore"
    st.session_state.pop("selected_restaurant", None)
    # An outstanding "which one?" from an ambiguous search is now answered,
    # whichever route answered it.
    st.session_state.pop("_ws_search_choices", None)
    request_loading(f"Analyzing {nta_names().get(code, code)}…")


@st.cache_data(show_spinner=False)
def area_tiers_cached(_panel, code: str, cuisine: str | None,
                      concept: str | None) -> dict:
    """
    Tiers computed ONCE per area+concept and then filtered locally — the
    Closest / Similar / All control never recomputes anything (spec 68).
    """
    return area_restaurant_tiers(_panel, code, cuisine, concept)


def area_restaurant_tiers(panel, code: str, cuisine: str | None,
                          concept: str | None = None) -> dict:
    """CURRENT establishments inside one NTA, tiered. Geography only — the
    tiering itself is restaurant_tiers()."""
    merged = panel_with_nta_cached(panel)
    inside = merged[(merged["nta_2020"] == code) & merged["seen_2026"]
                    & merged["lat"].notna()]
    return restaurant_tiers(inside, cuisine, concept)


@st.cache_data(show_spinner=False)
def site_tiers_cached(_panel, lat: float, lon: float, radius_m: int,
                      cuisine: str | None, concept: str | None) -> dict:
    """
    The SAME tiers, for the restaurants within `radius_m` of an address.

    Cached on the coordinate/radius/concept tuple, so flipping the
    Exact-concept / Same-cuisine / All-restaurants filter re-slices an
    already-computed result instead of recomputing anything — the contract
    area mode has had since V6.
    """
    return site_restaurant_tiers(_panel, lat, lon, radius_m, cuisine,
                                 concept)


def site_restaurant_tiers(panel, lat: float, lon: float, radius_m: int,
                          cuisine: str | None,
                          concept: str | None = None) -> dict:
    """
    CURRENT establishments within `radius_m` of a site, tiered IDENTICALLY
    to an area.

    Geography is the ONLY difference from area_restaurant_tiers: the
    candidate set is a radius around a point rather than an NTA polygon.
    Everything downstream — which cuisines count as the competitive set,
    how a named concept is matched, when the exact tier is unmeasurable —
    is the one shared implementation, so a restaurant can never qualify in
    one mode and not the other.

    geo.within_radius bounding-boxes before any trigonometry, which keeps
    this off the critical path with 48k establishments in the frame.
    """
    current = panel[panel["seen_2026"] & panel["lat"].notna()]
    near = geo.within_radius(current, lat, lon, radius_m)
    return restaurant_tiers(near, cuisine, concept)


def restaurant_tiers(inside, cuisine: str | None,
                     concept: str | None = None) -> dict:
    """
    THE tiering, shared by area mode and site mode.

    Splits an already-selected set of CURRENT establishments into three
    honesty-graded tiers — one marker per CAMIS, never inspection rows,
    because the panel is one row per establishment:

    closest — the highest specificity CURRENT data supports: the exact
        DOHMH cuisine label, narrowed to name-announced concept matches
        ("BRUNCH", "COFFEE") when the user named a concept the records can
        actually identify. When they can't, `note` says so — never a fake
        exact match.
    similar — the concept's competitive set (the V5/V6 similar tier).
    other — everything else in scope.

    Callers supply the geography and nothing else; the honesty rules live
    here so the two modes cannot drift apart.
    """
    token = concept_token(concept)
    note = None
    #: True when current records cannot identify the concept AT ALL, so the
    #: closest tier is unmeasurable rather than empty — the difference
    #: between "none here" and "we cannot tell", which must never be
    #: reported as a measured zero.
    unidentifiable = False

    if cuisine:
        compset = cuisines.competitive_set(cuisine)
        exact = inside[inside["cuisine"] == cuisine]
        if token is not None:
            named = exact[exact["name"].str.upper().str.contains(
                rf"\b{token}\b", regex=True, na=False)]
            if len(named):
                closest = named
                note = (f"Name-identified {token.lower()} concepts among "
                        f"{cuisine} listings — public records don't "
                        f"classify concepts, so this is indicative.")
            else:
                closest = exact
                note = (f"Limited exact-concept evidence: current records "
                        f"identify {cuisine} restaurants reliably but not "
                        f"which operate as {concept} concepts.")
        else:
            closest = exact
        similar = inside[inside["cuisine"].isin(compset)
                         & ~inside.index.isin(closest.index)]
    else:
        # No cuisine: closest = name-announced concept matches area-wide;
        # there is no defensible "similar" set without a cuisine.
        if token is not None:
            closest = inside[inside["name"].str.upper().str.contains(
                rf"\b{token}\b", regex=True, na=False)]
            if not len(closest):
                unidentifiable = True
                note = (f"No establishment in this area announces "
                        f"“{token.lower()}” in its public-record name — "
                        f"concept-level evidence is limited here, so every "
                        f"restaurant is shown instead.")
            else:
                note = (f"Name-identified {token.lower()} concepts — "
                        f"public records don't classify concepts, so this "
                        f"is indicative, not exhaustive.")
        else:
            closest = inside.iloc[0:0]
            unidentifiable = True
            if concept:
                note = (f"Current records cannot identify “{concept}” "
                        f"establishments — showing all restaurants instead.")
        similar = inside.iloc[0:0]

    other = inside[~inside.index.isin(closest.index)
                   & ~inside.index.isin(similar.index)]
    return dict(closest=closest, similar=similar, other=other, note=note,
                unidentifiable=unidentifiable, total=len(inside))


def area_restaurants(panel, code: str, cuisine: str | None
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Back-compat two-way split: (similar-or-closest, other)."""
    tiers = area_restaurant_tiers(panel, code, cuisine)
    grouped = pd.concat([tiers["closest"], tiers["similar"]])
    return grouped, tiers["other"]


def _apply_map_selection(event, panel) -> None:
    """
    One handler for every map click: polygons select areas, restaurant
    points select restaurants. Top-match buttons call select_area directly,
    so both paths share the same state transition.

    A plotly selection is STICKY widget state: while the figure spec is
    unchanged, the exact same event is re-delivered on every rerun. Without
    the handled-snapshot guard, acting on it unconditionally is an infinite
    st.rerun() loop (each rerun re-handles the event before the panel ever
    renders) — and popping selected_restaurant elsewhere would bounce
    straight back here. Handle each distinct selection exactly once.
    """
    if not event or not event.get("selection"):
        return
    points = event["selection"].get("points") or []
    if not points:
        return
    snapshot = repr(points)
    if st.session_state.get("_map_sel_handled") == snapshot:
        return
    st.session_state["_map_sel_handled"] = snapshot
    for point in points:
        custom = point.get("customdata")
        if isinstance(custom, (list, tuple)) and custom and \
                str(custom[0]).startswith("camis:"):
            camis = str(custom[0])[6:]
            if st.session_state.get("selected_restaurant") != camis:
                st.session_state["selected_restaurant"] = camis
                st.rerun()
        location = point.get("location")
        if location and location in nta_index().features:
            if st.session_state.get("selected_area") != location:
                select_area(location, source="polygon_click")
                st.rerun()


def render_workspace_toolbar(panel, cuisine: str | None,
                             mode: str = "site") -> tuple[str, str]:
    """
    The control row: Concept, Layer, Restaurants, and Radius in site mode.

    RENDERED AT FULL CONTENT WIDTH, above the map/analysis panes rather
    than inside the map column. It used to live in the map column, which is
    ~532px on a 1280px window — not enough for four controls, so Streamlit's
    column wrapper wrapped them: Concept ended up on a different row from
    Layer, and the three Restaurants options stacked into a 132px tower
    beside two 68px dropdowns. Widening the columns could not fix that
    because the container itself was too narrow. Full width gives the row
    ~1200px, which fits every control on one line with room to spare.

    Every keyed control is mirrored into a plain session key: Streamlit
    drops keyed-widget state for widgets that skip a run (e.g. while the
    Method view is open), and the mirror re-seeds them so layer/filter
    selections survive any navigation.

    Returns (layer_label, comp_mode) for the map to render from.
    """
    # Restaurants gets the widest share: its three labels are words, not
    # abbreviations, and they must sit on ONE row at desktop widths.
    st.markdown('<div class="jx-toolbar"></div>', unsafe_allow_html=True)
    if mode == "site":
        top1, top2, top3, top4 = st.columns([1.0, 1.15, 2.1, 1.15],
                                            gap="small")
    else:
        top1, top2, top3 = st.columns([1.0, 1.2, 2.1], gap="small")
        top4 = None
    with top1:
        options = [CUISINE_ANY] + cuisine_options(panel)
        if st.session_state.get("ws_concept") not in options:
            st.session_state["ws_concept"] = (cuisine if cuisine in options
                                              else CUISINE_ANY)
        st.selectbox("Concept", options, key="ws_concept")
    with top2:
        choices = layer_choices_for(cuisine)
        if st.session_state.get("ws_layer") not in choices:
            mirrored = st.session_state.get("_layer_mirror")
            st.session_state["ws_layer"] = (mirrored if mirrored in choices
                                            else list(choices)[0])
        layer_label = st.selectbox("Layer", list(choices), key="ws_layer")
        st.session_state["_layer_mirror"] = layer_label
    with top3:
        # "Same cuisine" only exists when a cuisine was given; with none,
        # there is no broader-cuisine set to show.
        comp_options = ([EXACT, SAME_CUISINE, ALL_RESTAURANTS] if cuisine
                        else [EXACT, ALL_RESTAURANTS])
        fallback = SAME_CUISINE if cuisine else ALL_RESTAURANTS
        if st.session_state.get("ws_comp") not in comp_options:
            mirrored = st.session_state.get("_comp_mirror")
            st.session_state["ws_comp"] = (mirrored if mirrored
                                           in comp_options else fallback)
        # A segmented control, not a radio stack: three vertically stacked
        # options made this column far taller than Concept and Layer and
        # left a large empty block beneath it. Selecting a segment only
        # re-filters already-computed tiers — no reanalysis, no refit.
        # `default` is deliberately omitted: the value is already seeded in
        # session state above, and passing both makes Streamlit warn about a
        # widget created with a default AND a session-state value.
        comp_mode = st.segmented_control(
            "Restaurants", comp_options, key="ws_comp",
            selection_mode="single", required=True, help=FILTER_HELP)
        if comp_mode is None:                 # nothing selected yet
            comp_mode = st.session_state["ws_comp"]
        # Only remember a real choice: writing back the coerced fallback
        # would permanently downgrade the cuisine tier after a no-cuisine
        # detour.
        if comp_mode != fallback or st.session_state.get(
                "_comp_mirror") in (None, comp_mode):
            st.session_state["_comp_mirror"] = comp_mode
    if top4 is not None:
        with top4:
            # Keyed widget: the new value is in session state BEFORE the
            # next run's analysis reads it — no one-rerun lag.
            if "ws_radius" not in st.session_state:
                st.session_state["ws_radius"] = st.session_state.get(
                    "_radius_mirror", config.DEFAULT_RADIUS_M)
            st.slider("Radius (m)", 200, 1500, key="ws_radius", step=50)
            st.session_state["_radius_mirror"] = \
                st.session_state["ws_radius"]
    return layer_label, comp_mode


def render_map_workspace(panel, site, cuisine: str, landscape,
                         report, mode: str = "site",
                         top_matches: list | None = None,
                         layer_label: str = "", comp_mode: str = "") -> None:
    """The persistent map: one layer figure and selection handling. The
    controls are rendered above by render_workspace_toolbar, which hands
    this the layer and filter to draw."""
    layer = LAYER_CHOICES[layer_label]
    # The published (URL-referenced) geometry, so the shapes are fetched once
    # per browser rather than re-sent with every figure.
    geojson = nta_display_geometry()
    hover = _hover_frame(panel)
    selected_area = st.session_state.get("selected_area")

    # --- fitBounds: exactly once per NEW selection, then the view holds ----
    # plotly's uirevision preserves the user's pan/zoom across Streamlit
    # reruns; changing the revision key applies the computed fit. Without
    # this, every rerun (marker hover, tab click) snapped the view back —
    # the reported "clicking sometimes doesn't zoom" and jumpy-map bugs.
    if selected_area:
        pane_w, pane_h = MAP_PANE_PX.get(
            st.session_state.get("workspace_view", "assess"), (820, 640))
        center, zoom = zoom_for_bounds(polygon_bounds(selected_area),
                                       viewport_px=pane_w,
                                       viewport_h_px=pane_h)
        # The token makes the revision unique per SELECTION EVENT, not per
        # area id, so choosing the same area again still refits.
        view_key = (f"area:{selected_area}:"
                    f"{st.session_state.get('area_fit_token', 0)}")
    elif site is not None:
        center, zoom = (site["lat"], site["lon"]), site_zoom(site)
        view_key = f"site:{site['label']}"
    else:
        center, zoom = (40.72, -73.97), 9.9
        view_key = "nyc"
    # Recorded AFTER the fit is applied so state is inspectable and
    # testable: last_fitted_view == view_key means this viewport is current.
    st.session_state["last_fitted_view"] = view_key
    st.session_state["last_fitted_area"] = selected_area

    # Restaurant markers are the point of a selected-area view: mute the
    # thematic fill under them so points stay readable (spec section 32).
    fig = _layer_figure(panel, layer, cuisine, geojson, hover, center, zoom,
                        site, report,
                        # Mute the thematic fill wherever restaurant markers
                        # are drawn — in SITE mode too, now that a site
                        # shows its local competitors. Points on a
                        # full-strength choropleth are hard to read, and
                        # the markers are the subject of both views.
                        fill_scale=0.5 if (selected_area or site is not None)
                        else 1.0)
    fig.update_layout(uirevision=view_key,
                      mapbox_uirevision=view_key)

    # --- containing district, in SITE mode ---------------------------------
    # A site analysis is read against its neighbourhood, so the map has to
    # show which neighbourhood that is. Without this the site marker floated
    # on an unlabelled thematic wash: the address was visible, the district
    # it belongs to was not. Drawn only when no area is separately selected,
    # so the two emphases can never compete for the same outline.
    if site is not None and not selected_area:
        # --- local competitive environment ---------------------------------
        # A site map that shows only its own marker answers "where is this
        # address?" and nothing else. The question a site analysis exists
        # to answer is "how crowded is it HERE", so the same restaurant
        # tiers area mode draws are drawn around the address, using the
        # SAME shared tiering and the SAME three-way filter — only the
        # geography differs (a radius instead of a polygon).
        plan = st.session_state.get("confirmed_plan")
        radius_m = int(st.session_state.get(
            "ws_radius", st.session_state.get("_radius_mirror",
                                              config.DEFAULT_RADIUS_M)))
        site_tiers = site_tiers_cached(panel, site["lat"], site["lon"],
                                       radius_m, cuisine,
                                       plan.concept if plan else None)
        # The ring first, so every marker draws on top of it.
        workspace_map.add_radius_ring(fig, site["lat"], site["lon"],
                                      radius_m)
        show_other = (comp_mode == ALL_RESTAURANTS
                      or (comp_mode == EXACT
                          and site_tiers["unidentifiable"]))
        workspace_map.add_restaurant_markers(
            fig,
            site_tiers["similar"] if comp_mode in (SAME_CUISINE,
                                                   ALL_RESTAURANTS)
            else site_tiers["similar"].iloc[0:0],
            site_tiers["other"], show_other=show_other,
            closest=site_tiers["closest"])
        # NOTHING is printed above the map. The filter caption, the
        # exact-concept caveat and the count reconciliation used to stack
        # here as three paragraphs between the controls and the map, which
        # pushed the map down the page and made the workspace read like a
        # document. All three are still available — as the Restaurants
        # tooltip and inside the analysis — but the space between the
        # toolbar and the map is now the map's.
        record_filter_explanation(comp_mode, cuisine,
                                  plan.concept if plan else None,
                                  site_tiers, radius_m=radius_m)

        containing = nta_index().locate(site["lat"], site["lon"])
        cfeat = nta_index().features.get(containing) if containing else None
        if cfeat:
            for i, poly in enumerate(cfeat["polygons"]):
                # SIMPLIFIED rings, like every other polygon on this map.
                # feature["polygons"] holds the original float64 geometry
                # kept for point-in-polygon; drawing from it shipped ~18
                # characters per coordinate and pushed the site figure from
                # 34KB to 128-156KB per rerun, undoing the payload work the
                # published geometry exists to do. Display only — the
                # analytic rings are untouched.
                ring = geometry.simplify_ring(poly[0])
                lons = [round(pt[0], geometry.DISPLAY_DECIMALS)
                        for pt in ring] + [round(ring[0][0],
                                                 geometry.DISPLAY_DECIMALS)]
                lats = [round(pt[1], geometry.DISPLAY_DECIMALS)
                        for pt in ring] + [round(ring[0][1],
                                                 geometry.DISPLAY_DECIMALS)]
                fig.add_trace(go.Scattermapbox(
                    lat=lats, lon=lons, mode="lines",
                    line=dict(width=workspace_map.SELECTED_LINE_WIDTH,
                              color=workspace_map.TOKENS["accent"]),
                    name=cfeat["name"], legendgroup="containing_district",
                    showlegend=(i == 0),
                    hovertemplate=f"{cfeat['name']}<extra></extra>"))

    # --- selection emphasis -------------------------------------------------
    if selected_area:
        fset = nta_index().features[selected_area]
        for poly in fset["polygons"]:
            lons = [pt[0] for pt in poly[0]] + [poly[0][0][0]]
            lats = [pt[1] for pt in poly[0]] + [poly[0][0][1]]
            fig.add_trace(go.Scattermapbox(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=workspace_map.SELECTED_LINE_WIDTH,
                          color=workspace_map.TOKENS["accent"]),
                hoverinfo="skip", showlegend=False))
        plan = st.session_state.get("confirmed_plan")
        tiers = area_tiers_cached(panel, selected_area, cuisine,
                                  plan.concept if plan else None)
        # When the concept cannot be identified at all, "Closest" would
        # otherwise draw an empty map while the caption promises every
        # restaurant — show them, exactly as the caption says.
        show_other = (comp_mode == ALL_RESTAURANTS
                      or (comp_mode == EXACT and tiers["unidentifiable"]))
        workspace_map.add_restaurant_markers(
            fig,
            tiers["similar"] if comp_mode in (SAME_CUISINE, ALL_RESTAURANTS)
            else tiers["similar"].iloc[0:0],
            tiers["other"], show_other=show_other,
            closest=tiers["closest"])
        # Same as site mode: no paragraphs between the toolbar and the map.
        # The caveat still travels with the analysis and the tooltip — the
        # exact-concept tier must never look like a verified match — it
        # just no longer sits on top of the map.
        record_filter_explanation(comp_mode, cuisine,
                                  plan.concept if plan else None, tiers)

    # Areas queued for comparison get numbered amber outlines — visually
    # obvious, thematic fill intact, never overpowering restaurant markers.
    for rank, ccode in enumerate(
            comparison_area_codes(), 1):
        cset = nta_index().features.get(ccode)
        if not cset:
            continue
        for poly in cset["polygons"]:
            lons = [pt[0] for pt in poly[0]] + [poly[0][0][0]]
            lats = [pt[1] for pt in poly[0]] + [poly[0][0][1]]
            fig.add_trace(go.Scattermapbox(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=2, color=workspace_map.TOKENS["warn"]),
                hoverinfo="skip", showlegend=False))
        x0, y0, x1, y1 = cset["bbox"]
        fig.add_trace(go.Scattermapbox(
            lat=[(y0 + y1) / 2], lon=[(x0 + x1) / 2], mode="text",
            text=[str(rank)],
            textfont=dict(size=18, color=workspace_map.TOKENS["warn"]),
            hovertemplate=f"Comparison #{rank}: "
                          f"{nta_names().get(ccode, ccode)}"
                          "<extra></extra>", showlegend=False))

    if top_matches:
        for rank, match in enumerate(top_matches[:3], 1):
            fset = nta_index().features.get(match["code"])
            if not fset:
                continue
            for poly in fset["polygons"]:
                lons = [pt[0] for pt in poly[0]] + [poly[0][0][0]]
                lats = [pt[1] for pt in poly[0]] + [poly[0][0][1]]
                fig.add_trace(go.Scattermapbox(
                    lat=lats, lon=lons, mode="lines",
                    line=dict(width=2, color=workspace_map.TOKENS["warn"]),
                    name=f"#{rank} {match['name']}" if poly is fset["polygons"][0] else None,
                    showlegend=poly is fset["polygons"][0],
                    hoverinfo="skip"))

    # Competitors first, then the site marker — the selected site draws on
    # top. The competition layer's mapview figure already carries its own
    # site marker; adding a second would double it.
    if (site is not None and landscape is not None
            and getattr(landscape, "ok", False)
            and comp_mode == SAME_CUISINE
            and not selected_area):
        fig = workspace_map.competitor_markers(fig, landscape.competitors)
    if site is not None and not (layer == "competition"
                                 and report is not None):
        workspace_map.add_site_marker(fig, site["lat"], site["lon"],
                                      site["label"])

    event = st.plotly_chart(fig, width="stretch", key="ws_map",
                            on_select="rerun",
                            selection_mode=("points",))
    _apply_map_selection(event, panel)


def _layer_figure(panel, layer, cuisine, geojson, hover, center, zoom,
                  site, report, fill_scale: float = 1.0):
    if not cuisine and layer in CUISINE_LAYERS:
        layer = "persistence"
    if layer == "concept_fit":
        fit = concept_fit_cached(panel, cuisine)
        return workspace_map.band_choropleth(
            geojson, fit["band"], "concept_fit", hover, center, zoom,
            fill_scale=fill_scale)
    if layer == "opportunity_gap":
        fit = concept_fit_cached(panel, cuisine)
        dens = density_cached(panel, cuisine)
        bands = {}
        for code in fit.index:
            sat = areas.competitor_saturation(
                int(dens.loc[code, "active_same"]) if code in dens.index else 0,
                float(dens.loc[code, "density_percentile"])
                if code in dens.index else None)
            bands[code] = areas.opportunity_gap(
                fit.loc[code, "band"], sat["band"])["band"]
        return workspace_map.band_choropleth(
            geojson, pd.Series(bands), "opportunity_gap", hover, center, zoom,
            fill_scale=fill_scale)
    if layer == "turnover":
        return workspace_map.band_choropleth(
            geojson, turnover_cached(panel)["band"], "turnover", hover,
            center, zoom, fill_scale=fill_scale)
    if layer == "evidence":
        return workspace_map.band_choropleth(
            geojson, evidence_cached(panel)["band"], "evidence", hover,
            center, zoom, fill_scale=fill_scale)
    if layer == "cuisine_density":
        dens = density_cached(panel, cuisine)
        return workspace_map.continuous_choropleth(
            geojson, dens["active_same"].astype(float),
            f"{cuisine} (active)", center=center, zoom=zoom,
            fill_scale=fill_scale)
    if layer == "persistence":
        feats = area_features_cached(panel)
        return workspace_map.continuous_choropleth(
            geojson, (feats["persistence_rate"] * 100).round(0),
            "Still listed (%)", hover_fmt="%{z:.0f}%", center=center,
            zoom=zoom, fill_scale=fill_scale)
    if layer in ("population", "income_context"):
        _, demo = acs_by_nta_cached()
        if demo is None:
            return workspace_map.band_choropleth(
                geojson, pd.Series(dtype=object), "evidence", hover, center,
                zoom)
        column = "population" if layer == "population" else "income_context"
        title = "Residents" if layer == "population" else "Income context ($)"
        return workspace_map.continuous_choropleth(
            geojson, demo[column], title, center=center, zoom=zoom,
            fill_scale=fill_scale)
    if layer == "pedestrian":
        ped_sites = load_pedestrian()
        fig = workspace_map.band_choropleth(
            geojson, pd.Series(dtype=object), "evidence", hover, center, zoom)
        fig.add_trace(go.Scattermapbox(
            lat=ped_sites["lat"], lon=ped_sites["lon"], mode="markers",
            marker=dict(size=8, color=workspace_map.TOKENS["warn"]),
            name=f"Bi-annual sites ({len(ped_sites)})",
            text=ped_sites["Street_Nam"] + " · " +
                 ped_sites["count"].round(0).astype(int).map("{:,}".format),
            hovertemplate="%{text}<br>Historical reference<extra></extra>"))
        return fig
    # competition
    if report is not None and site is not None:
        everything = report["area"]["all"]
        compset = set(report["area"]["competitive_set"])
        return mapview.build_map(everything, site, cuisine, compset,
                                 report["query"]["radius_m"], mode="status",
                                 theme=active_theme(), locations=None)
    if not cuisine:
        feats = area_features_cached(panel)
        return workspace_map.continuous_choropleth(
            geojson, (feats["persistence_rate"] * 100).round(0),
            "Still listed (%)", hover_fmt="%{z:.0f}%", center=center,
            zoom=zoom, fill_scale=fill_scale)
    fit = concept_fit_cached(panel, cuisine)
    return workspace_map.band_choropleth(
        geojson, fit["band"], "concept_fit", hover, center, zoom,
        fill_scale=fill_scale)


def site_area_context(panel, site: dict, cuisine: str, landscape) -> dict:
    """Saturation / gap / ranking for the NTA containing the site."""
    code = nta_index().locate(site["lat"], site["lon"])
    if code is None:
        return {"nta_code": None}
    # SAME source the area page uses, including the no-cuisine case. With no
    # cuisine, concept_fit_cached returns the empty table by design, so
    # reading it here would tell a plan with no cuisine that the area has
    # "not enough comparable history" while the area page one click away
    # shows that area a real concept-independent persistence index. Two
    # screens of the same product must not disagree about the same area.
    fit = (concept_fit_cached(panel, cuisine) if cuisine
           else conceptfree_fit_cached(panel))
    dens = density_cached(panel, cuisine)
    strong = (landscape.strong if landscape is not None
              and getattr(landscape, "ok", False) else None)
    saturation = areas.competitor_saturation(
        int(dens.loc[code, "active_same"]) if code in dens.index else 0,
        float(dens.loc[code, "density_percentile"])
        if code in dens.index else None,
        strong_nearby=strong)
    fit_band = (fit.loc[code, "band"] if code in fit.index
                else "Limited evidence")
    gap = areas.opportunity_gap(fit_band, saturation["band"])
    # The AREA's own relative index, carried alongside its band so SITE mode
    # can show the neighbourhood read at the same resolution as the area
    # page does. Deliberately a separate number from the site fit — never
    # blended with it (see render_area_context).
    raw_index = fit.loc[code, "fit_index"] if code in fit.index else None
    fit_index = (None if raw_index is None or pd.isna(raw_index)
                 else float(raw_index))
    evidence = evidence_cached(panel)
    ev_band = (evidence.loc[code, "band"] if code in evidence.index else None)
    return {"nta_code": code, "nta_name": nta_names().get(code, code),
            "fit_band": fit_band, "fit_index": fit_index,
            "evidence_band": ev_band,
            "saturation": saturation, "gap": gap}


# ------------------------------------------------------- plan-driven display
#: RestaurantPlan fields -> how the deterministic app actually uses them.
#: Fields without a defensible current-data signal say so honestly — they
#: are retained as context, never fake-scored (V6 spec sections 60/68).
PLAN_FIELD_USAGE = {
    "cuisine": "concept fit, similar competitors, cuisine density, "
               "cuisine track record, concept map layers",
    "concept": "kept as concept context on the confirmation and plan chips",
    "address": "site analysis routing, geocoded once, map focus",
    "zipcode": "area routing via that ZIP's restaurant records",
    "borough": "discovery constraint and area-name disambiguation",
    "neighborhood": "area analysis routing and map focus",
    "average_spend": "kept as concept context — not scored against any "
                     "dataset",
    "seats": "kept as concept context — not scored",
    "price_positioning": "competitor price comparison context",
    "foot_traffic_preference": "compared with DOT pedestrian evidence "
                               "where measured",
    "competition_tolerance": "compared with comparable-restaurant density",
    "income_preference": "compared with ACS area income context",
    "restaurant_density_preference": "compared with cuisine density "
                                     "percentile",
    "target_customer_description": "kept as plan context — the current "
                                   "data cannot score it",
    "additional_constraints": "kept as plan context — not scored",
}

_LEVEL_WORD = {"low": "Low", "moderate": "Moderate", "high": "High"}


#: The plan fields a chip can clear, and the fields each one owns. The
#: LOCATION chip is special: whichever location field is actually driving
#: the analysis is the one it removes, and removing it clears the derived
#: site state too (see remove_plan_constraint).
PLAN_LOCATION_FIELDS = ("address", "zipcode", "neighborhood", "borough")


def plan_chip_values(plan, area_name: str | None = None,
                     site_label: str | None = None) -> list[dict]:
    """
    Compact YOUR PLAN chips — only values the user explicitly provided,
    never hidden defaults.

    Each chip carries the plan FIELD it stands for, so removing it clears
    exactly that constraint and nothing else. Chips without a field (the
    "not specified" placeholder) are informational and carry no ×, because
    there is nothing to remove.
    """
    if plan is None:
        return []
    chips: list[dict] = []

    def add(label, field=None):
        chips.append({"label": label, "field": field})

    if plan.cuisine:
        add(plan.cuisine, "cuisine")
    if plan.concept:
        add(plan.concept.title() if plan.concept.islower() else plan.concept,
            "concept")
    if not plan.cuisine:
        add("Cuisine · Not specified")          # nothing to remove
    place = site_label or area_name or plan.zipcode or plan.borough
    if place:
        add(place, "location")
    if plan.average_spend:
        add(f"~${plan.average_spend:.0f}/person", "average_spend")
    if plan.seats:
        add(f"{plan.seats} seats", "seats")
    if plan.price_positioning:
        add(plan.price_positioning, "price_positioning")
    if plan.foot_traffic_preference:
        add(f"{_LEVEL_WORD[plan.foot_traffic_preference]} foot traffic",
            "foot_traffic_preference")
    if plan.competition_tolerance:
        add("Prefer lower competition"
            if plan.competition_tolerance == "low" else
            f"{_LEVEL_WORD[plan.competition_tolerance]} competition "
            f"tolerance", "competition_tolerance")
    if plan.income_preference:
        add(f"{_LEVEL_WORD[plan.income_preference]}-income area",
            "income_preference")
    if plan.restaurant_density_preference:
        add(f"{_LEVEL_WORD[plan.restaurant_density_preference]} restaurant "
            f"density", "restaurant_density_preference")
    return chips[:8]


def plan_chip_labels(plan, area_name: str | None = None,
                     site_label: str | None = None) -> list[str]:
    """Just the text of each chip, for callers that render or export the
    plan rather than edit it (the PDF, any plain summary)."""
    return [c["label"] for c in plan_chip_values(plan, area_name, site_label)]


def remove_plan_constraint(field: str) -> None:
    """
    THE one way a plan constraint is cleared.

    Clears exactly the requested structured field, drops only the state
    genuinely derived from it, and re-runs the SAME deterministic routing
    the confirm page uses. Claude is never called: the plan is already
    parsed, and editing it is an edit, not a re-parse.

    Removing the location is the case that matters. It must not feel like
    New Search — the cuisine, the concept and every preference survive —
    and it must not send the user back to the prompt. With no location the
    product already has a defined answer: deterministic DISCOVERY over the
    remaining plan, which is where this routes.

    Saved comparison entries are deliberately untouched. They are a
    separate, explicit collection; editing the plan in front of you is not
    a reason to silently discard locations you already chose to compare.
    """
    plan = st.session_state.get("confirmed_plan")
    if plan is None:
        return

    if field == "location":
        # Clear every location field plus the state derived from an
        # address; provenance is not tracked separately, so the chip
        # represents whichever location was driving the analysis.
        plan = plan.copy(update={f: None for f in PLAN_LOCATION_FIELDS})
        for key in ("address", "selected_area", "selected_restaurant",
                    "requested_area", "discovery_borough", "area_fit_token",
                    "area_fit_source", AREA_IS_SUBJECT):
            st.session_state.pop(key, None)
        request_loading("Finding the best areas…")
    else:
        plan = plan.copy(update={field: None})
        if field == "cuisine":
            # The concept survives; the workspace concept selector must
            # follow the plan rather than re-seeding the alphabetically
            # first cuisine, which is how "Afghan" used to reappear.
            st.session_state["cuisine"] = None
            st.session_state["ws_concept"] = CUISINE_ANY
            for key in ("ws_comp", "_comp_mirror", "sim_results",
                        "report_pdf"):
                st.session_state.pop(key, None)

    st.session_state["confirmed_plan"] = plan
    route_plan(plan)


def route_plan(plan) -> None:
    """
    The deterministic routing rules, applied to an already-parsed plan.

    address -> SITE · ZIP/neighbourhood -> AREA · borough -> DISCOVERY in
    that borough · nothing -> DISCOVERY. Identical in spirit to the confirm
    page's routing, reachable without going anywhere near the prompt.
    """
    if plan.address:
        st.session_state["workspace_mode"] = "site"
        st.session_state["address"] = plan.address
        st.session_state["workspace_view"] = "assess"
        return
    st.session_state.pop("address", None)

    codes = []
    if plan.zipcode or plan.neighborhood:
        codes = resolve_area_candidates(plan.neighborhood or plan.zipcode,
                                        plan.borough)
    if len(codes) == 1:
        st.session_state["workspace_mode"] = "area"
        select_area(codes[0], source="plan_edit")
        st.session_state["workspace_view"] = "assess"
        return

    st.session_state["workspace_mode"] = "discovery"
    st.session_state["discovery_borough"] = plan.borough
    st.session_state["workspace_view"] = "assess"
    st.session_state.pop("selected_area", None)


def _tercile_band(percentile: float | None) -> str | None:
    if percentile is None or not np.isfinite(percentile):
        return None
    return ("High" if percentile >= 200 / 3
            else "Moderate" if percentile >= 100 / 3 else "Low")


@st.cache_data(show_spinner=False)
def income_percentile_cached(code: str) -> float | None:
    """Area income context as a percentile among NTAs with ACS coverage."""
    _, demo = acs_by_nta_cached()
    if demo is None or code not in demo.index:
        return None
    value = demo.loc[code, "income_context"]
    valid = demo["income_context"].dropna()
    if pd.isna(value) or not len(valid):
        return None
    return float((valid < value).mean() * 100)


def preference_alignment(plan, saturation_band: str | None,
                         income_pct: float | None,
                         density_pct: float | None,
                         ped_band: str | None) -> list[dict]:
    """
    Explicit preferences vs. measured relative signals — pure and
    deterministic, entirely separate from the validated core scores.
    Statuses: match / mixed / conflict / unmeasured. Nothing unstated is
    ever included; nothing unmeasured is ever scored.
    """
    if plan is None:
        return []

    def level_vs_band(pref: str, band: str | None) -> str:
        if band is None:
            return "unmeasured"
        gap = abs(("low", "moderate", "high").index(pref)
                  - ("Low", "Moderate", "High").index(band))
        return ("match", "mixed", "conflict")[gap]

    rows = []
    if plan.foot_traffic_preference:
        status = level_vs_band(plan.foot_traffic_preference, ped_band)
        rows.append(dict(
            key="foot_traffic", label="Foot traffic", status=status,
            measured=ped_band,
            detail=(f"You asked for {plan.foot_traffic_preference} foot "
                    f"traffic; " +
                    (f"the busiest measured DOT count site in this area "
                     f"reads {ped_band.lower()} relative to the 114 "
                     f"citywide sites."
                     if ped_band else
                     "no DOT count site falls in this area — pedestrian "
                     "evidence is not measured here."))))
    if plan.competition_tolerance:
        if saturation_band is None:
            status = "unmeasured"
        elif plan.competition_tolerance == "high":
            status = "match"
        else:
            order = ("Low", "Moderate", "High").index(saturation_band)
            limit = ("low", "moderate", "high").index(
                plan.competition_tolerance)
            status = ("match" if order <= limit
                      else "conflict" if order - limit > 1 else "mixed")
        rows.append(dict(
            key="competition", label="Competition", status=status,
            measured=saturation_band,
            detail=(f"You preferred "
                    f"{plan.competition_tolerance} competition; comparable "
                    f"restaurant density here is "
                    f"{saturation_band.lower() if saturation_band else 'not measured'}.")))
    if plan.income_preference:
        band = _tercile_band(income_pct)
        rows.append(dict(
            key="income", label="Income context",
            status=level_vs_band(plan.income_preference, band),
            measured=band,
            detail=(f"You asked for a {plan.income_preference}-income area; "
                    + (f"ACS income context here is {band.lower()} relative "
                       f"to NYC areas." if band else
                       "ACS income context is unavailable here."))))
    if plan.restaurant_density_preference:
        band = _tercile_band(density_pct)
        rows.append(dict(
            key="density", label="Restaurant density",
            status=level_vs_band(plan.restaurant_density_preference, band),
            measured=band,
            detail=(f"You asked for {plan.restaurant_density_preference} "
                    f"restaurant density; this area reads "
                    + (f"{band.lower()} for your concept."
                       if band else "unmeasured."))))
    return rows


_PRIORITY_MARK = {"match": ("✓", "good"), "mixed": ("~", "neutral"),
                  "conflict": ("✕", "concern"), "unmeasured": ("—", "unknown")}


def render_priorities(plan, alignment: list[dict], cuisine: str,
                      area_name: str | None) -> None:
    """MATCHES YOUR PRIORITIES — stated preferences only, max ~5 rows,
    entirely separate from the validated core scores (spec section 63)."""
    if plan is None:
        return
    rows = []
    if area_name:
        rows.append(("Area", f"✓ {area_name}"))
    if plan.cuisine:
        rows.append(("Cuisine", f"✓ {cuisine} analysis"))
    for r in alignment[:3]:
        mark, _tone = _PRIORITY_MARK[r["status"]]
        word = {"match": "Matches", "mixed": "Mixed",
                "conflict": "Conflicts", "unmeasured": "Not measured"}[
            r["status"]]
        state = (f"{mark} {word}" if not r.get("measured")
                 else f"{mark} {word} · {r['measured']}")
        rows.append((r["label"], state))
    if plan.average_spend or plan.price_positioning:
        rows.append(("Price context", "— Not directly scored"))
    if not rows:
        return
    ui.eyebrow("Matches your priorities")
    ui.bench_rows(rows[:5])
    conflicts = [r["detail"] for r in alignment if r["status"] == "conflict"]
    for line in conflicts[:2]:
        st.caption(line)


def render_plan_usage(plan) -> None:
    """How your plan was used — one row per PROVIDED field, from the
    deterministic mapping table. Unsupported details are named as context,
    never fake-scored."""
    if plan is None:
        return
    with st.expander("How your plan was used"):
        rows = []
        for field, usage in PLAN_FIELD_USAGE.items():
            value = getattr(plan, field, None)
            if value in (None, "", []):
                continue
            shown = ", ".join(value) if isinstance(value, list) else value
            rows.append((str(shown)[:40], usage))
        ui.bench_rows(rows)
        st.caption("Claude only converts your wording into these fields. "
                   "Every comparison above comes from the app's own "
                   "datasets.")


# --------------------------------------------------------------- concepts
def _label_artifact(row: dict) -> bool:
    """
    A citywide cohort that "survived" wholesale is a label DOHMH introduced
    with the 2026 vocabulary: every carrier is seen_2026 by construction.
    Straight renames are repaired upstream (cuisines.DOHMH_2017_TO_2026),
    so what remains here are genuinely new categories with no 2017
    counterpart — New American, Vegan, Fusion and the like. The fit index
    self-neutralizes to 50 for these — but the raw rate must never be
    presented as a genuine persistence read.
    """
    return row.get("baseline_rate", 0) >= 0.999


def concept_reason(row: dict, sat_band: str | None) -> str:
    """Short reason from signals the fit computation ACTUALLY used, plus the
    separately-computed competition context. Never an invented explanation."""
    if _label_artifact(row):
        persistence = "Category label changed between extracts — " \
                      "persistence comparison uninformative"
    else:
        persistence = {
            "Strong": "Strong local track record",
            "Promising": "Above-baseline observed persistence",
            "Mixed": "At or below the citywide baseline",
        }.get(row["band"], "Limited local history")
    competition = (f"{sat_band.lower()} comparable competition"
                   if sat_band else "competition unmeasured")
    return f"{persistence} · {competition}"


def render_concept_rows(panel, code: str, ranking: list[dict],
                        limit: int = 3, own_concept: bool = False) -> None:
    """Concept ranking — each with score, band, reason, and a Why?
    breakdown built from the actual area_concept_fit components. When the
    user searched their OWN concept this renders as the secondary "other
    concepts" module, never upstaging what they actually asked about."""
    if not ranking:
        return
    ui.eyebrow("Other concepts that fit here" if own_concept
               else "Concepts that fit this area")
    st.caption(("Relative alternative-concept scores under this area's "
                "evidence — they do not mean those cuisines have a 100% "
                "probability of success. Your own concept stays the "
                "primary analysis above. "
                if own_concept else "")
               + "Ranks cuisines using the same local evidence framework. "
                 "Higher scores indicate a stronger relative match — not a "
                 "probability of success.")
    for i, r in enumerate(ranking[:limit], 1):
        dens = density_cached(panel, r["cuisine"])
        sat = areas.competitor_saturation(
            int(dens.loc[code, "active_same"]) if code in dens.index else 0,
            float(dens.loc[code, "density_percentile"])
            if code in dens.index else None)
        score = f"{r['fit_index']:.0f}"
        band_label = ("Top relative fit" if r["fit_index"] >= 100
                      else f"{r['band']} relative fit")
        st.markdown(
            f"**{i}. {r['cuisine']}** — {score} / 100 · {band_label}  \n"
            f"<span style='font-size:13px;color:var(--text-muted);'>"
            f"{concept_reason(r, sat['band'])}</span>",
            unsafe_allow_html=True)
        with st.expander("Why?", expanded=False):
            local_rate = (r["cohort_survived"] / r["cohort_n"]
                          if r["cohort_n"] else float("nan"))
            gap = local_rate - r["baseline_rate"]
            ui.bench_rows([
                ("Local still-listed (2011–17 → 2026)",
                 f"{r['cohort_survived']}/{r['cohort_n']} "
                 f"= {local_rate:.0%}"),
                ("Same set citywide",
                 f"{r['baseline_rate']:.0%} (n={r['baseline_n']:,})"),
                ("Observed persistence gap", f"{gap * 100:+.1f} pts"),
                ("Fit index",
                 f"{areas.FIT_NEUTRAL} + ({gap:+.3f}) × {areas.FIT_SLOPE} "
                 f"→ {r['fit_index']:.0f} (clipped 0–100)"),
                ("Local sample",
                 f"{r['cohort_n']} (min {areas.MIN_CUISINE_SAMPLE})"),
                ("Comparable competition",
                 (sat["band"] or "Unmeasured")
                 + (f" · {sat['density_percentile']:.0f}th pct"
                    if sat.get("density_percentile") is not None else "")),
            ])
            if r["band"] == "Promising":
                st.caption("Promising = above the citywide baseline but "
                           "within sampling uncertainty — directional, not "
                           "statistically established. Strong requires the "
                           "sample to clear the margin.")
            if r["fit_index"] >= 100:
                st.caption("100 marks the top of the current relative scale "
                           "for this area's available evidence — several "
                           "concepts can share it, and it does not mean a "
                           "100% probability of success.")
            if _label_artifact(r):
                st.caption("This DOHMH category exists only under a newer "
                           "label, so every record carrying it is in the "
                           "2026 extract by construction — the 100% rates "
                           "are a labelling artifact, not restaurant "
                           "longevity, and the comparison is neutral.")
            st.caption("Competition is shown for context; the fit index "
                       "itself is the observed-persistence comparison "
                       "above.")


# ------------------------------------------------------------- comparison
def google_concept_query(cuisine: str | None, concept: str | None) -> str | None:
    """Deterministic Google text query from EXPLICIT plan fields only —
    'Italian brunch', 'French bakery', 'brunch'. None when neither given."""
    parts = [p.strip() for p in (cuisine, concept) if p and p.strip()]
    return " ".join(dict.fromkeys(parts)) or None


@st.cache_data(show_spinner=False)
def area_bundle_cached(_panel, code: str, cuisine: str | None,
                       concept: str | None) -> dict:
    """
    One area's comparison bundle, assembled from the SAME cached analyses
    the standalone views use — never a second analytical implementation.
    Unmeasured stays None throughout; nothing is defaulted.
    """
    name = nta_names().get(code, code)
    tiers = area_tiers_cached(_panel, code, cuisine, concept)

    # With a cuisine this is concept fit; without one it is the SAME index
    # computed over all concepts (restaurant persistence) — the identical
    # number the standalone area view shows, so a no-cuisine comparison is
    # never content-free. `fit_is_concept` records which one it is.
    fit_index = fit_band = None
    sat = {"band": None, "detail": None}
    fit = (concept_fit_cached(_panel, cuisine) if cuisine
           else conceptfree_fit_cached(_panel))
    if code in fit.index:
        fit_band = fit.loc[code, "band"]
        if pd.notna(fit.loc[code, "fit_index"]):
            fit_index = float(fit.loc[code, "fit_index"])
    if cuisine:
        dens = density_cached(_panel, cuisine)
        sat = areas.competitor_saturation(
            int(dens.loc[code, "active_same"]) if code in dens.index else 0,
            float(dens.loc[code, "density_percentile"])
            if code in dens.index else None)

    turn = turnover_cached(_panel)
    evidence = evidence_cached(_panel)
    feats = area_features_cached(_panel)
    ped = area_ped_context(code)
    _, demo = acs_by_nta_cached()

    persistence = cohort_n = None
    if code in feats.index:
        cohort_n = int(feats.loc[code, "cohort_n"])
        if pd.notna(feats.loc[code, "persistence_rate"]):
            persistence = float(feats.loc[code, "persistence_rate"])

    income = population = None
    if demo is not None and code in demo.index:
        if pd.notna(demo.loc[code, "income_context"]):
            income = float(demo.loc[code, "income_context"])
        if pd.notna(demo.loc[code, "population"]):
            population = float(demo.loc[code, "population"])

    turn_band = turn.loc[code, "band"] if code in turn.index else None
    return dict(
        code=code, name=name, cuisine=cuisine,
        fit_is_concept=bool(cuisine),
        fit_index=fit_index, fit_band=fit_band,
        evidence=(evidence.loc[code, "band"]
                  if code in evidence.index else None),
        competition_band=sat["band"], competition_detail=sat.get("detail"),
        turnover=turn_band, ped_band=ped["band"],
        ped_sites=ped.get("sites", 0),
        restaurants_total=tiers["total"],
        # Counts mirror the map legend exactly: the tiers are disjoint, so
        # "Similar concept" is the similar tier alone. Closest is None —
        # not 0 — when the records cannot identify the concept at all.
        similar_count=len(tiers["similar"]) if cuisine else None,
        closest_count=(None if tiers.get("unidentifiable")
                       else len(tiers["closest"])),
        persistence_rate=persistence, cohort_n=cohort_n or 0,
        income_context=income, population=population)


def build_comparison_payload(bundles: list[dict],
                             plan) -> comparison.ComparisonReportPayload:
    """The frozen report payload — validated data only, methodology from the
    live constants, limitations always present."""
    import datetime
    leaders, recommendation = comparison.comparison_summary(bundles)
    area_reports = []
    for b in bundles:
        area_reports.append(comparison.AreaReport(
            **{k: b.get(k) for k in (
                "code", "name", "fit_is_concept", "fit_index", "fit_band",
                "evidence", "competition_band", "competition_detail",
                "turnover", "ped_band", "ped_sites", "restaurants_total",
                "similar_count", "closest_count", "persistence_rate",
                "cohort_n", "income_context", "population")},
            pros=comparison.derive_area_pros(b),
            cons=comparison.derive_area_cons(b),
            risks=comparison.derive_risk_matrix(b)))

    # Taken from the bundles, not session state: the payload must describe
    # the analyses it actually contains, whoever calls it.
    cuisine = next((b.get("cuisine") for b in bundles if b.get("cuisine")),
                   None)
    concept_bits = [p for p in (cuisine, plan.concept if plan else None)
                    if p]
    methodology = [
        ["Location fit", "A 0–100 relative decision index for comparing "
                         "locations — never a probability of success. "
                         "Weighted components (editorial weights): "
                         + ", ".join(f"{narrative.COMPONENTS[k][0]} {w}%"
                                     for k, w in scoring.WEIGHTS.items())],
        ["Concept fit", f"Observed persistence of the concept's competitive "
                        f"set (2011–17 cohort still listed in 2026) versus "
                        f"the same set citywide; index "
                        f"{areas.FIT_NEUTRAL} + gap × {areas.FIT_SLOPE}, "
                        f"clipped 0–100, local sample of at least "
                        f"{areas.MIN_CUISINE_SAMPLE} required."],
        ["Evidence quality", f"High / Moderate / Limited from cohort depth "
                             f"(≥{areas.MIN_AREA_SAMPLE}), active inventory "
                             f"(≥{areas.MIN_AREA_SAMPLE}), and ACS "
                             f"coverage."],
        ["Competition", "Comparable-restaurant density from public records "
                        "(percentile among NYC areas). Missing data never "
                        "reads as low competition."],
        ["Observed persistence & turnover", "The share of 2011–17 "
                                            "restaurants still listed in "
                                            "2026, and the share gone since "
                                            "2017 — never survival or "
                                            "failure rates."],
        ["Opportunity gap", "A lookup combining relative concept fit with "
                            "the competition reading — not unmet demand."],
        ["Data sources", "NYC DOHMH restaurant records, Google Places, "
                         "2024 ACS 5-Year, PLUTO, NYC DOT pedestrian "
                         "counts, NYC Planning 2020 NTA geographies."],
    ]
    limitations = [
        comparison.LIMITATION,
        "Both DOHMH extracts window inspections to about three years, so "
        "longitudinal reads are cohort cross-checks, never lifespans.",
        "Pedestrian evidence comes from 114 DOT bi-annual count sites; "
        "areas without a site read Not measured.",
        "Google Places enrichment reflects indexed listings, not exhaustive "
        "coverage.",
    ]
    return comparison.ComparisonReportPayload(
        concept_line=" ".join(concept_bits).title() if concept_bits else
        "General restaurant concept",
        # Labels only: the report describes the plan, it does not edit it.
        plan_items=plan_chip_labels(plan) if plan else [],
        generated=datetime.date.today().isoformat(),
        areas=area_reports, leaders=leaders,
        recommendation=recommendation, methodology=methodology,
        limitations=limitations)


@st.cache_data(ttl=3600, show_spinner=False)
def narrative_cached(payload_json: str, key_present: bool,
                     _api_key: str | None) -> dict | None:
    """One narrative call per frozen payload — never re-invoked by reruns,
    tab switches, or repeated exports of the same comparison."""
    payload = comparison.ComparisonReportPayload.parse_raw(payload_json)
    return report_writer.narrate(payload, _api_key)


# ======================================================= comparison state
#: THE canonical comparison store. One list, one shape, one set of
#: mutators.
#:
#: WHY THIS REPLACED comparison_area_ids: the previous design kept a bare
#: list of NTA codes, and an "Add to comparison" button existed only inside
#: render_area_explorer — so the action was present in AREA mode and simply
#: absent in SITE mode. From the user's side that reads as "Add to
#: comparison only works sometimes". Meanwhile a SECOND, unrelated store
#: (st.session_state["saved"]) sat behind "Save & compare another location"
#: in render_next_steps and pushed stage="landing", which is why a
#: comparison action could throw the user back to the opening prompt.
#: Both are gone: one store, reachable from every mode, and nothing in the
#: comparison path touches `stage`.
COMPARISON_KEY = "comparison_locations"

#: Set by select_area: True when the area was explicitly chosen, False when
#: it is only the spatial context for a site (see select_area).
AREA_IS_SUBJECT = "_area_is_subject"

#: What the comparison ENGINE consumes is an area bundle, so a site is
#: compared through the area that contains it. That is stated in the UI
#: rather than smoothed over: a site entry always shows both its address
#: and the area standing in for it, and the two scores are never merged.
COMPARE_KIND_AREA = "area"
COMPARE_KIND_SITE = "site"


def comparison_entries() -> list[dict]:
    """The canonical list. Always a list, never None."""
    entries = st.session_state.get(COMPARISON_KEY)
    if not isinstance(entries, list):
        entries = []
        st.session_state[COMPARISON_KEY] = entries
    return entries


def comparison_area_codes() -> list[str]:
    """The areas the engine will actually compare, in selection order."""
    return [e["area_code"] for e in comparison_entries()][
        :comparison.MAX_COMPARE_AREAS]


def make_area_entry(code: str) -> dict | None:
    """A neighbourhood, chosen from the map or the area panel."""
    if code not in nta_index().features:
        return None
    return {"kind": COMPARE_KIND_AREA, "id": f"area:{code}",
            "area_code": code, "display_name": nta_names().get(code, code),
            "address": None}


def make_site_entry(site: dict) -> dict | None:
    """
    An exact address. Stored WITH the area that contains it, because that
    is the unit the comparison engine works in; both are shown.
    """
    code = nta_index().locate(site["lat"], site["lon"])
    if code is None:
        return None
    label = site.get("label") or "Selected site"
    # Identity is the resolved coordinate, not the typed string: "6 E 1st St"
    # and "6 East 1 Street, New York, NY, USA" are the same place and must
    # not be addable twice.
    return {"kind": COMPARE_KIND_SITE,
            "id": f"site:{site['lat']:.5f},{site['lon']:.5f}",
            "area_code": code, "display_name": label,
            "address": label, "area_name": nta_names().get(code, code)}


def add_to_comparison(entry: dict | None) -> tuple[bool, str]:
    """
    THE one way anything enters the comparison. Atomic and idempotent.

    Validates, de-duplicates, enforces the maximum and commits — all before
    returning, so the caller never has to trigger a second run for the
    selection to "take". The old add mutated state and immediately called
    st.rerun(), which meant the commit and the confirmation lived in
    different runs; anything that reset state in between (a New Search
    cleanup, a route change) could silently drop the addition.

    De-duplication is by AREA CODE, not by entry id, because the area is
    what actually gets compared: adding a site and then the neighbourhood
    around it would otherwise produce a comparison of an area against
    itself. Returns (added, message) so the caller can say what happened.
    """
    if not entry or not entry.get("area_code"):
        return False, "That location could not be placed in a neighbourhood."
    entries = comparison_entries()
    existing = next((e for e in entries
                     if e["area_code"] == entry["area_code"]), None)
    if existing is not None:
        if existing["id"] == entry["id"]:
            return False, "Already in the comparison."
        return False, (f"{existing['display_name']} already covers this "
                       f"neighbourhood in the comparison.")
    if len(entries) >= comparison.MAX_COMPARE_AREAS:
        return False, (f"Maximum {comparison.MAX_COMPARE_AREAS} locations — "
                       "remove one first.")
    st.session_state[COMPARISON_KEY] = entries + [entry]
    invalidate_comparison_report()
    return True, f"{entry['display_name']} added."


def remove_from_comparison(entry_id: str) -> None:
    st.session_state[COMPARISON_KEY] = [
        e for e in comparison_entries() if e["id"] != entry_id]
    invalidate_comparison_report()


def clear_comparison() -> None:
    st.session_state[COMPARISON_KEY] = []
    invalidate_comparison_report()
    if st.session_state.get("workspace_view") == "compare":
        st.session_state["workspace_view"] = "explore"


def invalidate_comparison_report() -> None:
    """A generated PDF describes one exact selection; change the selection
    and it must not remain downloadable."""
    st.session_state.pop("report_pdf", None)


def _open_comparison() -> None:
    st.session_state["workspace_view"] = "compare"
    request_loading("Preparing comparison…")


def render_compare_tray() -> None:
    """
    The persistent tray. Visible in Explore, Assess and Method alike, so a
    selection made in one view is still visible from the others.
    """
    entries = comparison_entries()
    if not entries:
        return
    n, cap = len(entries), comparison.MAX_COMPARE_AREAS
    with st.container(border=True, key="compare_tray"):
        ui.eyebrow("Compare")
        for entry in entries:
            row_l, row_r = st.columns([6, 1])
            with row_l:
                st.markdown(f"**{entry['display_name']}**")
                if entry["kind"] == COMPARE_KIND_SITE:
                    # Never let a site silently masquerade as its area.
                    st.caption(f"Site · compared as "
                               f"{entry.get('area_name', entry['area_code'])}")
            row_r.button("×", key=f"cmp_rm_{entry['id']}",
                         on_click=remove_from_comparison,
                         args=(entry["id"],),
                         help=f"Remove {entry['display_name']}")
        st.caption(f"{n} / {cap} selected"
                   + ("" if n >= 2 else " — add one more to compare"))
        left, right = ui.button_row(2)
        with left:
            st.button("Compare →", key="cmp_go", type="primary",
                      width="stretch", disabled=n < 2,
                      on_click=_open_comparison)
        with right:
            st.button("Clear", key="cmp_clear", width="stretch",
                      on_click=clear_comparison)


def _submit_workspace_search(text: str) -> None:
    """
    Resolve a typed location INSIDE the workspace, keeping the plan.

    Tries the app's own neighbourhood names first, then treats the text as
    a street address. Either way the concept, the confirmed plan and the
    comparison tray are untouched — this is a location picker, not a new
    plan, so Claude is never re-run and nothing downstream is reset.
    """
    # Every submission starts from a clean slate: leftover choices or a
    # leftover error from a PREVIOUS search would otherwise sit under the
    # box offering stale neighbourhoods to jump to, or scolding the user
    # about input they have already corrected.
    st.session_state.pop("_ws_search_choices", None)
    st.session_state.pop("_ws_search_error", None)
    query = (text or "").strip()
    if not query:
        st.session_state["_ws_search_error"] = "Type an address or a " \
                                               "neighbourhood first."
        return
    plan = st.session_state.get("confirmed_plan")
    borough = getattr(plan, "borough", None)
    codes = resolve_area_candidates(query, borough)
    if len(codes) == 1:
        st.session_state["workspace_mode"] = "area"
        st.session_state.pop("address", None)
        select_area(codes[0], source="workspace_search")
        st.session_state["workspace_view"] = "assess"
        return
    if len(codes) > 1:
        # Genuinely ambiguous ("Murray Hill" is two neighbourhoods) — offer
        # the choice rather than guessing one.
        st.session_state["_ws_search_choices"] = codes[:6]
        return
    # Not a neighbourhood we know: treat it as an address.
    st.session_state["workspace_mode"] = "site"
    st.session_state["workspace_view"] = "assess"
    st.session_state["address"] = query
    st.session_state.pop("selected_area", None)
    st.session_state.pop("selected_restaurant", None)
    request_loading("Analyzing your location…")


def _pick_search_choice(code: str) -> None:
    st.session_state["workspace_mode"] = "area"
    st.session_state.pop("address", None)
    st.session_state.pop("_ws_search_choices", None)
    select_area(code, source="workspace_search")
    st.session_state["workspace_view"] = "assess"


def render_workspace_search() -> None:
    """
    Search another location WITHOUT leaving the workspace.

    The opening prompt authors a PLAN; using it to pick a second location
    threw away the plan and the comparison with it. This is the location
    picker that belongs beside the map: same concept, new place.
    """
    with st.container(border=True, key="ws_search"):
        ui.eyebrow("Search another location")
        with st.form("ws_search_form", border=False,
                     clear_on_submit=False):
            text = st.text_input(
                "Address or neighbourhood", key="ws_search_text",
                label_visibility="collapsed",
                placeholder="Address or neighbourhood — e.g. Gramercy")
            st.form_submit_button(
                "Analyze location →", width="stretch",
                on_click=lambda: _submit_workspace_search(
                    st.session_state.get("ws_search_text", "")))
        # Popped, not read: this is a flash about the submission that just
        # happened. Leaving it in state meant the hint reappeared every
        # time the user came back to this view, scolding them about input
        # they had corrected — or abandoned — several screens ago.
        error = st.session_state.pop("_ws_search_error", None)
        if error:
            st.caption(f":orange[{error}]")
        choices = st.session_state.get("_ws_search_choices")
        if choices:
            st.caption("Which one?")
            names = nta_names()
            for code in choices:
                st.button(names.get(code, code), key=f"ws_pick_{code}",
                          width="stretch", on_click=_pick_search_choice,
                          args=(code,))
        concept = st.session_state.get("cuisine")
        st.caption(f"Keeps your current concept"
                   + (f" — {concept}." if concept else "."))


def _abandon_address() -> None:
    """Drop the unresolvable address and put the workspace back on the map,
    keeping the plan, the concept and the comparison."""
    st.session_state.pop("address", None)
    st.session_state["workspace_mode"] = "area"
    st.session_state["workspace_view"] = "explore"


def render_address_failure(message: str, address: str) -> None:
    """
    An address that will not resolve must not strand the user.

    The workspace search can send any typed string down the site path — one
    transposed letter is enough. Before, the only way out was a button that
    set stage="landing", which threw away the plan, the concept and the
    whole comparison to recover from a typo. Both exits now stay inside the
    workspace; New Search remains available in the header for anyone who
    actually does want to start over.
    """
    st.error(message)
    st.caption(f"Searched for: {address}")
    left, right = ui.button_row(2)
    with left:
        st.button("Back to the map", key="addr_fail_back", width="stretch",
                  type="primary", on_click=_abandon_address)
    with right:
        st.button("Search another location", key="addr_fail_search",
                  width="stretch", on_click=_abandon_address)
    render_workspace_search()


def area_is_subject(selected_area: str | None, view: str) -> bool:
    """
    Is the selected area what the workspace is ABOUT, or just context?

    THE ONE rule, used by both the panel and the add control so the two can
    never disagree about what the user is looking at. An area is the
    subject when it was explicitly chosen (see select_area), and always in
    Explore, where the map — and therefore the area under it — is the view.
    """
    if not selected_area:
        return False
    return view == "explore" or bool(st.session_state.get(AREA_IS_SUBJECT))


def current_comparison_subject(site: dict | None, mode: str,
                               selected_area: str | None,
                               view: str) -> tuple[dict | None, str]:
    """
    What the workspace is analysing right now, as a comparison entry.

    Uses the SAME rule as the panel, because the add control has to offer
    whatever the panel is actually showing. Getting this wrong is worse
    than having no button: it put the user's address into the comparison
    while they were looking at a neighbourhood, and then — because the
    store dedupes by area — refused every area they clicked afterwards,
    leaving the comparison permanently stuck at one entry.

    Returns (entry, key_suffix) so the caller renders one stable widget.
    """
    if area_is_subject(selected_area, view):
        return make_area_entry(selected_area), "subject_area"
    if mode == "site" and site is not None:
        return make_site_entry(site), "subject_site"
    if selected_area:
        return make_area_entry(selected_area), "subject_area"
    return None, "subject_none"


def _handle_add(entry: dict | None, flash_key: str) -> None:
    """on_click handler: commit, then leave a message for this run to show.
    Runs BEFORE the script body, so the tray and the button both render
    from already-committed state in the very next paint — one run, no
    second rerun, nothing left half-applied."""
    added, message = add_to_comparison(entry)
    st.session_state[flash_key] = (added, message)


def render_add_to_comparison(entry: dict | None,
                             key_suffix: str) -> None:
    """
    The intentional add action, offered identically for an area and for a
    site. Map clicks never auto-add — selecting is not choosing.
    """
    if entry is None:
        return
    entries = comparison_entries()
    flash_key = f"_cmp_flash_{key_suffix}"
    already = any(e["id"] == entry["id"] for e in entries)
    covered = next((e for e in entries
                    if e["area_code"] == entry["area_code"]
                    and e["id"] != entry["id"]), None)
    full = len(entries) >= comparison.MAX_COMPARE_AREAS

    if already:
        st.button("✓ Added to comparison", key=f"cmp_added_{key_suffix}",
                  disabled=True, width="stretch")
    elif covered is not None:
        st.button(f"✓ {covered['display_name']} already added",
                  key=f"cmp_cov_{key_suffix}", disabled=True,
                  width="stretch")
    elif full:
        st.button(f"Comparison full — maximum "
                  f"{comparison.MAX_COMPARE_AREAS}",
                  key=f"cmp_full_{key_suffix}", disabled=True,
                  width="stretch")
    else:
        st.button("+ Add to comparison", key=f"cmp_add_{key_suffix}",
                  width="stretch", on_click=_handle_add,
                  args=(entry, flash_key))
    flash = st.session_state.pop(flash_key, None)
    if flash and not flash[0]:
        st.caption(f":orange[{flash[1]}]")


def render_compare_view(panel, cuisine: str | None, plan) -> None:
    """AREA COMPARISON — 2–3 areas against the SAME plan, same dark shell."""
    entries = comparison_entries()[:comparison.MAX_COMPARE_AREAS]
    ids = [e["area_code"] for e in entries]
    names = nta_names()
    bundles = [area_bundle_cached(panel, code, cuisine,
                                  plan.concept if plan else None)
               for code in ids]

    top_l, top_r = st.columns([2.2, 1])
    with top_l:
        ui.eyebrow("Compare areas")
        concept_bits = " ".join(p for p in (
            cuisine, plan.concept if plan else None) if p)
        st.markdown(f"### {' vs '.join(b['name'] for b in bundles)}")
        if concept_bits:
            st.caption(f"{concept_bits.title()} — every area compared "
                       f"against the same plan.")
        # An address the user queued must not quietly become its
        # neighbourhood. The comparison engine works in AREAS, so a site is
        # compared through the area containing it — which is legitimate,
        # but only while it is said out loud. Without this the heading read
        # "East Village vs Gramercy" for a user who had queued a specific
        # street address, and nothing on the page connected the two.
        sites = [e for e in entries if e["kind"] == COMPARE_KIND_SITE]
        for entry in sites:
            st.caption(f"Site: **{entry['display_name']}** — compared using "
                       f"its area, {entry.get('area_name', entry['area_code'])}.")
    with top_r:
        if st.button("← Back to map", key="cmp_back", width="stretch"):
            st.session_state["workspace_view"] = "explore"
            st.rerun()

    # ----- summary matrix ---------------------------------------------------
    fit_label = ("Relative concept fit" if cuisine
                 else "Restaurant persistence (all concepts)")
    matrix_rows = {
        fit_label: [
            f"{b['fit_index']:.0f} / 100" if b["fit_index"] is not None
            else "Not measured" for b in bundles],
        "Band": [b["fit_band"] or "Not measured" for b in bundles],
        "Evidence": [b["evidence"] or "—" for b in bundles],
        "Competition": [b["competition_band"] or "Not measured"
                        for b in bundles],
        "Observed turnover": [
            _TURNOVER_READ.get(b["turnover"], b["turnover"] or "—")
            for b in bundles],
        "Pedestrian context": [b["ped_band"] or "Not measured"
                               for b in bundles],
        "Restaurants": [f"{b['restaurants_total']:,}" for b in bundles],
    }
    if cuisine:
        matrix_rows["Similar concept"] = [
            str(b["similar_count"]) for b in bundles]
        matrix_rows["Closest match"] = [
            str(b["closest_count"]) for b in bundles]
    frame = pd.DataFrame(matrix_rows,
                         index=[b["name"] for b in bundles]).T
    st.dataframe(frame, width="stretch")

    # ----- deterministic leaders -------------------------------------------
    leaders, recommendation = comparison.comparison_summary(bundles)
    ui.eyebrow("Where each area leads")
    low_band = leaders.get("lowest_competition_band")
    ui.bench_rows([
        ("Leading on relative fit",
         " / ".join(leaders["leading_fit"]) or "Insufficient evidence"),
        ("Lowest competition",
         (" / ".join(leaders["lowest_competition"])
          + (f" (still {low_band.lower()})" if low_band == "High" else ""))
         or "Insufficient evidence"),
        ("Strongest evidence",
         " / ".join(leaders["strongest_evidence"]) or
         "Insufficient evidence"),
    ])
    st.caption(recommendation + " Relative comparisons only — never a "
                                "success prediction.")

    # ----- per-area detail --------------------------------------------------
    cols = st.columns(len(bundles), gap="medium")
    for col, b in zip(cols, bundles):
        with col:
            st.markdown(f"#### {b['name']}")
            pros = comparison.derive_area_pros(b)
            cons = comparison.derive_area_cons(b)
            ui.eyebrow("Pros")
            for p in pros or []:
                st.markdown(f"<span style='color:var(--positive);'>+</span> "
                            f"{p.label}", unsafe_allow_html=True)
            if not pros:
                st.caption("No standout strengths under current evidence.")
            ui.eyebrow("Cons")
            for c in cons or []:
                st.markdown(f"<span style='color:var(--negative);'>–</span> "
                            f"{c.label}", unsafe_allow_html=True)
            if not cons:
                st.caption("No material flags under current evidence.")
            with st.expander("Risk analysis"):
                ui.bench_rows([(r.category, r.level)
                               for r in comparison.derive_risk_matrix(b)])
                for r in comparison.derive_risk_matrix(b):
                    st.caption(f"{r.category}: {r.why} (evidence: "
                               f"{r.evidence})")

    # ----- report export ----------------------------------------------------
    st.divider()
    export_l, export_r = st.columns([2.2, 1])
    with export_r:
        render_report_control(bundles, plan, cuisine)
    with export_l:
        st.caption(comparison.LIMITATION)


@st.cache_resource(show_spinner=False)
def data_version() -> str:
    """Identity of the built data, so a cached report cannot outlive a
    rebuild of the panel it was written from."""
    return str(int(config.RESTAURANTS_PQ.stat().st_mtime))


def report_signature(bundles: list[dict], cuisine: str | None, plan) -> tuple:
    """
    What a generated report is FOR. If any of this changes, the stored PDF
    describes a comparison the user is no longer looking at, and must not
    stay downloadable.
    """
    return (tuple(b["code"] for b in bundles), cuisine,
            getattr(plan, "concept", None), data_version())


def _request_report() -> None:
    """Raise the loader and defer the build to the next run, so the PDF is
    built with the overlay already covering the screen rather than after
    it."""
    st.session_state["_build_report"] = True
    request_loading("Creating your report…")


def render_report_control(bundles: list[dict], plan,
                          cuisine: str | None) -> None:
    """
    ONE report control, in one place.

    Streamlit cannot start a download from a callback — the bytes must
    exist before st.download_button renders — so one click genuinely
    cannot both build and deliver a PDF. What it can do is never show two
    controls at once: before the PDF exists this slot is an Export button;
    the moment it exists, the same slot is the Download button. The user
    experiences one control that changes state, not a two-step form with
    both halves on screen.

    The PDF is keyed to the exact comparison it describes, so reopening the
    view or switching tabs never rebuilds it, and changing the selection,
    the concept or the underlying data discards it rather than offering a
    report that contradicts the screen.
    """
    signature = report_signature(bundles, cuisine, plan)
    stored = st.session_state.get("report_pdf")
    if stored and stored[0] != signature:
        stored = None
        st.session_state.pop("report_pdf", None)

    # Deferred build: the overlay raised by _request_report is up right now.
    if stored is None and st.session_state.pop("_build_report", False):
        payload = build_comparison_payload(bundles, plan)
        key = get_anthropic_api_key()
        narrative_dict = narrative_cached(payload.json(), bool(key), key)
        pdf = report_pdf.render_pdf(payload, narrative_dict)
        stored = (signature, report_pdf.report_filename(payload), pdf,
                  narrative_dict is not None)
        st.session_state["report_pdf"] = stored

    if stored is None:
        st.button("Export comparison report ↓", key="cmp_export",
                  type="primary", width="stretch", on_click=_request_report)
        return

    _, filename, pdf_bytes, used_llm = stored
    st.download_button("Download comparison report ↓", data=pdf_bytes,
                       file_name=filename, mime="application/pdf",
                       type="primary", width="stretch", key="cmp_download")
    if not used_llm:
        st.caption("Narrative written from deterministic templates "
                   "(language model unavailable) — all analytics "
                   "identical.")


# ---------------------------------------------------------------- confirm
def confirm_page(panel) -> None:
    """
    "Here's what I understood" — Claude's interpretation is not authoritative
    until the user confirms it here, editable field by field. Routing after
    confirmation is plain Python on the plan's location fields.
    """
    outcome = st.session_state.get("plan_outcome")
    if outcome is None or outcome.plan is None:
        st.session_state["stage"] = "landing"
        st.rerun()
        return
    plan = outcome.plan

    ui.eyebrow("Here's what I understood")
    if not plan.has_restaurant_plan():
        st.markdown("### Tell us about the restaurant you're planning.")
        st.caption("That didn't read as a restaurant plan — try something "
                   "like *“Italian restaurant at 195 Bowery, Manhattan”*.")
        if st.button("← Try again", width="stretch"):
            st.session_state["stage"] = "landing"
            st.rerun()
        return

    if outcome.parser_backend == "fallback":
        if outcome.fallback_reason == "missing_key":
            st.caption("Parsed with the local pattern parser (language "
                       "parsing not configured) — please check the fields "
                       "below.")
        else:
            st.caption("Language parsing temporarily unavailable; using "
                       "local parser — please check the fields below.")
    elif outcome.parser_backend == "anthropic":
        st.caption("Interpreted from your description.")
    if outcome.parser_backend == "anthropic" and plan.confidence == "low":
        st.warning("That was ambiguous — please check every field before "
                   "analysing.")

    known = set(cuisine_options(panel))
    normalized = plan_parser.normalize_cuisine(plan.cuisine, known)

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            # "Any cuisine" heads the list and is the default whenever the
            # user named no cuisine: an unspecified cuisine stays None —
            # NEVER the alphabetically-first taxonomy label (the "brunch
            # spot -> Afghan" bug was exactly index=0 on a sorted list).
            options = [CUISINE_ANY] + sorted(known)
            index = options.index(normalized) if normalized in options else 0
            cuisine_choice = st.selectbox(
                "Cuisine", options, index=index,
                help=None if (normalized or not plan.cuisine) else
                "We couldn't match your wording to a known cuisine — "
                "pick one, or leave it as Any cuisine.")
            cuisine = None if cuisine_choice == CUISINE_ANY else cuisine_choice
            concept = st.text_input("Concept", value=plan.concept or "",
                                    placeholder="e.g. brunch spot, upscale, "
                                                "casual")
        with c2:
            address = st.text_input(
                "Address", value=plan.address or "",
                placeholder="Street address for an exact assessment")
            area = st.text_input(
                "Area (ZIP · neighborhood · borough)",
                value=plan.zipcode or plan.neighborhood or plan.borough or "")
        with c3:
            spend = st.number_input(
                "Average spend ($, optional)", min_value=0.0, max_value=500.0,
                value=(float(plan.average_spend) if plan.average_spend
                       else None), step=5.0, placeholder="—")
            seats = st.number_input(
                "Seats (optional)", min_value=0, max_value=500,
                value=int(plan.seats) if plan.seats else None, step=5,
                placeholder="—")

        # --- deterministic area resolution, alternatives surfaced HERE ----
        # (never a third onboarding screen). The Area field is authoritative:
        # a ZIP routes by restaurant records, a borough routes discovery,
        # and a neighborhood name resolves against the app's own 2020
        # geography — with a choice shown when one name maps to several.
        area_text = area.strip()
        area_zip = re.fullmatch(r"\d{5}", area_text)
        # Borough compare is normalized ("Brooklyn." / "the bronx" both
        # count) so a punctuated borough can never leak into name matching.
        area_folded = re.sub(r"^the\s+", "",
                             re.sub(r"[^a-z ]", "", area_text.lower())).strip()
        area_borough = next(
            (b for b in plan_parser.NYC_BOROUGHS
             if b.lower() == area_folded), None)
        chosen_code = None
        if area_text and not area_zip and not area_borough:
            candidates = resolve_area_candidates(area_text, plan.borough)
            if len(candidates) > 1:
                names = nta_names()
                boroughs = {c: f["borough"]
                            for c, f in nta_index().features.items()}
                labels = {f"{names[c]} ({boroughs.get(c, '?')})": c
                          for c in candidates}
                pick = st.selectbox(
                    f"“{area_text}” matches more than one NYC area — which "
                    f"one?", list(labels), index=0, key="area_disambig")
                chosen_code = labels[pick]
            elif candidates:
                chosen_code = candidates[0]
            else:
                st.caption(f"“{area_text}” doesn't match a neighborhood in "
                           f"the current NYC geography — Analyze will rank "
                           f"the best-fitting areas instead.")

        prefs = [f"{label}: {value}" for label, value in [
            ("Foot traffic", plan.foot_traffic_preference),
            ("Competition tolerance", plan.competition_tolerance),
            ("Income", plan.income_preference),
            ("Density", plan.restaurant_density_preference),
            ("Price", plan.price_positioning)] if value]
        if prefs:
            st.caption("Preferences you stated: " + " · ".join(prefs))
        if plan.unresolved_phrases:
            st.caption("Couldn't place geographically: "
                       + ", ".join(f"“{u}”" for u in plan.unresolved_phrases)
                       + " — add a ZIP or address if it matters.")

        left, right = st.columns([1, 2])
        rewrite = left.button("← Rewrite", width="stretch")
        analyze = right.button("Analyze →", type="primary", width="stretch")
        if not (address.strip() or area_text):
            st.caption("No location yet — Analyze will rank the areas where "
                       "this concept shows the best relative fit.")

    if st.checkbox("Developer trace", value=False, key="dev_trace_confirm",
                   help="Safe parser diagnostics — backend, reasons, latency. "
                        "Never the key."):
        st.json(outcome.diagnostics())

    if rewrite:
        st.session_state["stage"] = "landing"
        st.rerun()

    if analyze:
        # Deterministic routing — plain Python over the confirmed fields.
        edited = plan.copy(update=dict(
            cuisine=cuisine, concept=concept.strip() or None,
            address=address.strip() or None,
            average_spend=spend or None,
            seats=int(seats) if seats else None))
        st.session_state["plan_confirmed"] = True
        st.session_state["cuisine"] = cuisine        # None = no cuisine given
        # A new plan must beat any stale workspace concept selection.
        st.session_state["ws_concept"] = cuisine or CUISINE_ANY
        st.session_state["price"] = plan.price_positioning
        # A cleared Area field is a decision: the parse prefilled it, so
        # emptying it means "no area" — stale parsed location fields must
        # not resurface in routing, chips, or the plan-usage panel.
        prefill = plan.zipcode or plan.neighborhood or plan.borough or ""
        cleared = bool(prefill.strip()) and not area_text
        # An "address" with no building number is an area phrase, whatever
        # parser produced it — geocoding it invites the silent-mis-placement
        # class of failure (GeoSearch picks an arbitrary building).
        demoted_name = None
        if (edited.location_kind() == "address"
                and not geocode.has_house_number(edited.address or "")):
            demoted_name = edited.address
            edited = edited.copy(update=dict(address=None))
        # Priority: address -> ZIP -> recognized neighborhood -> borough ->
        # discovery. A recognized neighborhood ALWAYS overrides discovery,
        # and the Area FIELD is the location authority throughout.
        if edited.location_kind() == "address" and edited.address:
            target = edited.address
            if area_text and not any(
                    tok.lower() in target.lower()
                    for tok in area_text.replace(",", " ").split()):
                target = f"{target}, {area_text}"
            st.session_state["address"] = target
            st.session_state["workspace_mode"] = "site"
            edited = edited.copy(update=dict(address=target, zipcode=None,
                                             neighborhood=None))
        else:
            # AREA / DISCOVERY — no street address required, ever.
            st.session_state["address"] = None
            panel_df = load_panel()
            code = None
            if area_zip:
                code = zip_to_nta(panel_df, area_text)
            elif chosen_code:
                code = chosen_code
            elif demoted_name and not cleared:
                # The numberless "address" the field never saw — resolve it
                # here (never a stale parsed neighborhood: only the phrase
                # demoted in THIS submit).
                demoted = resolve_area_candidates(demoted_name,
                                                  edited.borough)
                code = demoted[0] if demoted else None
            if code:
                st.session_state["workspace_mode"] = "area"
                st.session_state["requested_area"] = code
                select_area(code, source="prompt")
                edited = edited.copy(update=dict(
                    zipcode=area_text if area_zip else None,
                    neighborhood=(None if area_zip
                                  else nta_names().get(code))))
            else:
                st.session_state["workspace_mode"] = "discovery"
                st.session_state["discovery_borough"] = (
                    area_borough or (None if cleared else edited.borough))
                edited = edited.copy(update=dict(
                    zipcode=None, neighborhood=None,
                    borough=st.session_state["discovery_borough"]))
        # Stored AFTER routing so chips and "how your plan was used" always
        # describe the fields the analysis actually used.
        st.session_state["confirmed_plan"] = edited
        st.session_state["workspace_view"] = "assess"
        st.session_state["stage"] = "results"
        request_loading("Analyzing your plan…")
        st.rerun()




def render_method_page(status: list[tuple[str, bool]] | None = None) -> None:
    """
    METHOD — a full in-app page in the top navigation, never behind the
    sidebar. Every number renders from the live constants in scoring.py /
    areas.py / narrative.py, so this page cannot drift from the code.
    """
    ui.eyebrow("Method")
    st.markdown("### How the analysis works")

    left, right = st.columns(2, gap="large")
    with left:
        ui.eyebrow("Location fit")
        st.caption("A 0–100 relative decision index for comparing locations "
                   "— never a probability of success. Weighted components "
                   "(weights are editorial, from scoring.py):")
        ui.bench_rows([
            (narrative.COMPONENTS[k][0], f"{w}%")
            for k, w in scoring.WEIGHTS.items()])
        lower, upper = scoring.BANDS[0][1], scoring.BANDS[1][1]
        st.caption(f"Component reads: favourable below {lower} risk, concern "
                   f"above {upper} (the same cut points as the bands). Fit "
                   f"bands: " + " · ".join(
                       f"{name} ≥ {floor}"
                       for floor, name in narrative.FIT_BANDS
                       if name != "High risk") + ".")

        ui.eyebrow("Concept fit")
        cap_gap = (100 - areas.FIT_NEUTRAL) / areas.FIT_SLOPE
        st.caption(
            f"Observed persistence of the concept's competitive set "
            f"(2011–17 cohort still listed in 2026) versus the same set "
            f"citywide, only where the local sample reaches "
            f"{areas.MIN_CUISINE_SAMPLE} restaurants; smaller samples read "
            f"Limited evidence. The index is {areas.FIT_NEUTRAL} + "
            f"(local − citywide rate) × {areas.FIT_SLOPE}, clipped to "
            f"0–100, and tracks the raw gap. Ranked concepts need at least "
            f"{areas.MIN_CITYWIDE_CUISINE} active NYC restaurants. STRONG "
            f"is reserved for local rates that clear sampling uncertainty "
            f"(Wilson interval) above the citywide baseline; above-baseline "
            f"differences inside that margin read PROMISING — directional, "
            f"not statistically established.")
        st.caption(
            f"Because the index is capped, any concept whose local "
            f"still-listed rate beats the citywide baseline by "
            f"{cap_gap * 100:.0f}+ percentage points reaches 100 — multiple "
            f"concepts can receive 100 when they reach the top score under "
            f"the available evidence; 100 does not imply one concept is "
            f"definitively better than all others, and never a 100% "
            f"success probability.")

        ui.eyebrow("Opportunity gap")
        st.caption("A lookup that combines relative concept fit with the "
                   "comparable-competition reading — NOT unmet demand, "
                   "market demand, or a guaranteed opportunity. Anything "
                   "unmeasured reads Insufficient evidence. The full matrix, "
                   "rendered from the live function:")
        ui.bench_rows([
            (f"{fit} fit · {sat} competition",
             areas.opportunity_gap(fit, sat)["band"])
            for fit in ("Strong", "Promising", "Mixed")
            for sat in ("Low", "Moderate", "High")])

    with right:
        ui.eyebrow("History")
        st.caption("Both DOHMH extracts window each restaurant to about "
                   "three years, so observed durations are not comparable "
                   "between closed and surviving restaurants. Every "
                   "longitudinal read is therefore OBSERVED PERSISTENCE — "
                   "the share of 2011–17 restaurants still listed in 2026 — "
                   "and OBSERVED TURNOVER, the share gone since 2017. "
                   "Neither is a survival nor a failure rate.")

        ui.eyebrow("Competition")
        st.caption("Comparable-restaurant density from public records "
                   "(percentile among NYC areas), sharpened at a site by "
                   "live Google competitor strength when available. Missing "
                   "competition data is never read as low competition.")

        ui.eyebrow("Restaurant similarity")
        st.caption("Map markers are graded by how specifically current data "
                   "can match your concept. CLOSEST MATCH is the highest "
                   "specificity the records support: the exact cuisine "
                   "label, narrowed to establishments whose public-record "
                   "NAME announces the concept (a “brunch”, “coffee” or "
                   "“bakery” in the name). SIMILAR is the cuisine's "
                   "competitive set. ALL is every current establishment in "
                   "the area. Public records do not classify concepts, so "
                   "when a concept cannot be identified the app says so "
                   "rather than relabelling every same-cuisine restaurant "
                   "as a match.")

        ui.eyebrow("Area comparison")
        st.caption(f"Up to {comparison.MAX_COMPARE_AREAS} areas, each "
                   f"analysed with the SAME functions the standalone area "
                   f"view uses — never a second implementation. Pros, cons "
                   f"and the risk matrix are derived from those signals by "
                   f"lookup; risk is categorical "
                   f"({' / '.join(comparison.RISK_LEVELS)}) and never a "
                   f"probability. Absent evidence is reported as "
                   f"insufficient, never as a negative finding.")

        ui.eyebrow("Evidence quality")
        st.caption(f"Two systems, both from stated checks. AREA pages and "
                   f"the map layer: High / Moderate / Limited from cohort "
                   f"depth (≥{areas.MIN_AREA_SAMPLE}), active inventory "
                   f"(≥{areas.MIN_AREA_SAMPLE}), and ACS coverage. SITE "
                   f"analysis: Strong / Moderate / Limited from evidence-"
                   f"weight coverage (≥80% measurable), the nearby "
                   f"historical cohort (≥100 restaurants), and live "
                   f"competitor data being available — its reasons are "
                   f"listed with the score. Evidence describes the data, "
                   f"never the location — an unmeasured component is "
                   f"dropped and named, never scored as average or "
                   f"negative.")

        ui.eyebrow("Data sources")
        ui.bench_rows([("Restaurant records", "NYC DOHMH"),
                       ("Live competitors", "Google Places"),
                       ("Demographics", "2024 ACS 5-Year"),
                       ("Property", "PLUTO"), ("Pedestrians", "NYC DOT"),
                       ("Geography", "NYC Planning 2020 NTAs")])
        if status:
            st.caption("Connected now: " + " · ".join(
                f"{'●' if ok else '○'} {name}" for name, ok in status))

        ui.eyebrow("Claude's role")
        st.caption("Claude converts your restaurant description into "
                   "structured search parameters — language parsing only. "
                   "It does not score locations, rank areas, or supply any "
                   "market fact; every analytical number on every page "
                   "comes from the datasets above.")



# ---------------------------------------------------------------- panels
def _view_containing_area(code: str) -> None:
    """Site → area, keeping the plan and the concept exactly as they are.

    Nothing is re-parsed and nothing is re-geocoded: this only moves the
    canonical selection and the view, so the same concept is carried
    straight into the area analysis.
    """
    select_area(code, source="site_area_context")
    st.session_state["workspace_mode"] = "area"
    st.session_state["workspace_view"] = "assess"
    st.session_state.pop("address", None)


def render_area_context(site: dict, cuisine: str | None,
                        area_ctx: dict) -> None:
    """
    LEVEL 2 of a site analysis: the neighbourhood the address sits in.

    A site score answers "how does this address look?". On its own it
    leaves the user without the other half of the question — "and what
    kind of area is it in?". This block supplies that, as a SEPARATE
    reading with its own number, its own band and its own evidence grade.

    The two indices are never combined. A site fit of 49 and an area fit of
    68 are answers to different questions on different evidence; averaging
    them, or presenting either as a probability, would invent a quantity
    neither one supports.
    """
    code = area_ctx.get("nta_code")
    if not code:
        st.caption("This address could not be placed inside a mapped "
                   "neighbourhood, so no area context is available.")
        return
    with st.container(border=True, key="site_area_context"):
        ui.eyebrow("Area context")
        st.markdown(f"**{area_ctx['nta_name']}**")
        index = area_ctx.get("fit_index")
        band = area_ctx.get("fit_band") or "Limited evidence"
        if index is None:
            # No fabricated midpoint: the area simply has too little
            # comparable history for this concept, and says so.
            st.markdown(
                f"<span style='font-size:15px;color:var(--text-secondary);"
                f"font-weight:600;'>{band}</span>", unsafe_allow_html=True)
            st.caption("Not enough comparable history in this area to place "
                       "a relative concept fit — not a negative signal.")
        else:
            st.markdown(
                f"<span style='font-size:26px;font-weight:600;'>"
                f"{index:.0f}</span>"
                f"<span style='font-size:14px;color:var(--text-muted);'>"
                f" / 100</span>  <span style='font-size:14px;"
                f"color:var(--text-secondary);font-weight:600;'>{band}"
                f"</span>", unsafe_allow_html=True)
            st.caption(
                (f"Area concept fit · {cuisine} · Evidence: "
                 f"{area_ctx.get('evidence_band') or '—'}" if cuisine else
                 "Area restaurant persistence (all concepts) · "
                 f"Evidence: {area_ctx.get('evidence_band') or '—'}"))
        st.caption("Separate from the site fit above — the two measure "
                   "different things and are never combined.")
        st.button("View full area analysis →", key="site_view_area",
                  width="stretch", on_click=_view_containing_area,
                  args=(code,))


def render_site_panel(panel, site, cuisine, price, report, result, landscape,
                      verdicts, fit, band, headline, quality, area_ctx,
                      lot, ped) -> None:
    """ONE verdict, above the fold, then tabs. No duplicated band anywhere."""
    if st.button("← Back to explore", key="back_explore"):
        # Non-destructive: select the site's area for spatial context and
        # switch the VIEW — the site analysis stays one Assess click away.
        code = nta_index().locate(site["lat"], site["lon"])
        if code:
            select_area(code, source="back_to_explore")
        st.session_state["workspace_view"] = "explore"
        st.rerun()
    ui.eyebrow(f"{cuisine or 'Restaurant'} · Site analysis")
    st.markdown(f"#### {site['label']}")
    # Full width, not a 1.2/1 split. The right-hand cell existed to hold
    # the Simulate button and later the add-to-comparison control; both
    # have moved, and a column kept for a control that is no longer there
    # is just dead space beside the headline number.
    st.markdown(
        f'<span style="font-size:34px;font-weight:600;">'
        f'{fit if fit is not None else "–"}</span>'
        f'<span style="font-size:16px;color:var(--text-muted);"> / 100'
        f'</span>  <span style="font-size:15px;color:var(--text-secondary);'
        f'font-weight:600;">{band}</span>', unsafe_allow_html=True)
    st.markdown(f"<span style='font-size:12.5px;color:var(--text-muted);'>"
                f"Location fit · relative index · Evidence quality: "
                f"{quality[0] if quality else '—'}</span>",
                unsafe_allow_html=True)
    with st.expander("What does this mean?"):
        st.caption(
            "A relative location-fit index combining the evidence "
            "available for this site, for comparing locations. It is "
            "not a probability of restaurant success.")
    st.markdown(headline)
    if site.get("warning"):
        st.warning(f"**Check the address.** {site['warning']}")

    # LEVEL 2: the neighbourhood containing this address, as its own read.
    render_area_context(site, cuisine, area_ctx)

    tab_overview, tab_comp, tab_market, tab_history, tab_property = st.tabs(
        ["Overview", "Competition", "Market", "History", "Property"])
    with tab_overview:
        ui.eyebrow("Key signals")
        render_why(verdicts, report, ped, quality)
        if area_ctx.get("nta_code"):
            sat, gap = area_ctx["saturation"], area_ctx["gap"]
            ui.evidence_rows([
                dict(label="Direct competitor density",
                     verdict=sat["band"] or "Unavailable",
                     tone={"High": "concern", "Moderate": "neutral",
                           "Low": "good"}.get(sat["band"], "unknown"),
                     conclusion=f"{area_ctx['nta_name']} area",
                     evidence_stat=sat.get("detail", "")),
                dict(label="Opportunity gap", verdict=gap["band"],
                     tone={"High": "good", "Moderate": "neutral",
                           "Low": "concern"}.get(gap["band"], "unknown"),
                     conclusion=gap["reason"].capitalize(),
                     evidence_stat=""),
            ])
            plan = st.session_state.get("confirmed_plan")
            sat_pct = area_ctx["saturation"].get("density_percentile")
            alignment = preference_alignment(
                plan, area_ctx["saturation"]["band"],
                income_percentile_cached(area_ctx["nta_code"]),
                float(sat_pct) if sat_pct is not None else None,
                area_ped_context(area_ctx["nta_code"])["band"])
            render_priorities(plan, alignment, cuisine,
                              area_ctx["nta_name"])
            ranking = concept_ranking_cached(panel, area_ctx["nta_code"])
            if ranking:
                render_concept_rows(panel, area_ctx["nta_code"], ranking,
                                    own_concept=bool(cuisine))
                with st.expander("More concepts & comparison"):
                    ui.bench_rows([
                        (f"{i}. {r['cuisine']}",
                         f"{r['fit_index']:.0f} · {r['band']} · "
                         f"n={r['cohort_n']}"
                         + (" · label artifact" if _label_artifact(r)
                            else ""))
                        for i, r in enumerate(ranking[3:8], 4)])
                    picks = st.multiselect(
                        "Compare (max 3)", [r["cuisine"] for r in ranking],
                        default=[], max_selections=3, key="ws_concepts")
                    if picks:
                        st.dataframe(areas.compare_concepts(
                            panel, nta_assignment(panel),
                            area_ctx["nta_code"], picks), width="stretch")
            render_plan_usage(plan)
        # Strengths / risks — the ONLY other place conclusions appear.
        render_recommendation(
            narrative.assessment_label(fit, landscape), headline,
            narrative.reason_to_proceed(verdicts),
            narrative.reason_for_caution(verdicts, landscape), cuisine)
        render_next(site, cuisine, fit, verdicts, landscape)
    with tab_comp:
        render_google(landscape, cuisine or "restaurant", price, report)
    with tab_market:
        acs_table = load_acs()
        tract_metrics, tract_source = None, "unavailable"
        if acs_table is not None:
            geoid, tract_source = site_tract_cached(
                site["lat"], site["lon"], panel)
            if geoid:
                tract_metrics = acs.tract_percentiles(acs_table, geoid)
        render_market(ped, verdicts, tract_metrics, tract_source)
    with tab_history:
        render_history(report)
        if cuisine:
            with st.expander(f"Cuisine track record — {cuisine}"):
                render_cuisine(report)
        else:
            st.caption("No cuisine was specified, so there is no "
                       "cuisine-specific track record to compare — the "
                       "location history above covers all concepts.")
    with tab_property:
        render_context(lot, ped)
    render_limitations()


_TURNOVER_READ = {
    "Lower observed turnover": "Lower than comparison areas",
    "Typical": "Typical of comparison areas",
    "Higher observed turnover": "Higher than comparison areas",
    "Limited evidence": "Limited evidence",
}


def render_analyze_site_cta(code: str) -> None:
    """The primary next action of an area analysis — unmistakable, near the
    top: area analysis is complete, exact-site analysis is optional."""
    st.caption("Have a specific address in this area?")
    with st.form(f"site_cta_{code}", border=False):
        addr = st.text_input("Address", key=f"area_addr_{code}",
                             label_visibility="collapsed",
                             placeholder="e.g. 460 Third Avenue")
        go = st.form_submit_button("Analyze a specific site →",
                                   type="primary", width="stretch")
    if go and addr.strip():
        borough = nta_index().features.get(code, {}).get("borough", "")
        target = addr.strip()
        if borough and borough.lower() not in target.lower():
            target = f"{target}, {borough}"
        st.session_state.update(address=target, workspace_mode="site",
                                workspace_view="assess")
        st.session_state.pop("selected_area", None)
        st.session_state.pop("selected_restaurant", None)
        request_loading("Analyzing your location…")
        st.rerun()
    elif go:
        st.caption(":orange[Type the street address first.]")


def render_area_explorer(panel, code: str, cuisine: str,
                         compact: bool = False) -> None:
    """
    AREA ANALYSIS: the answer to "how suitable is THIS area for the concept
    I described?" — prominent header, the concept-fit read, the plan's
    priorities, then the evidence. Never a redirect to other neighborhoods.
    """
    name = nta_names().get(code, code)
    plan = st.session_state.get("confirmed_plan")
    concept = plan.concept if plan else None

    st.markdown(f"### {name}")
    header_bits = " · ".join(p for p in (
        cuisine, concept.title() if concept else None) if p)
    ui.eyebrow(f"{header_bits or 'Restaurant'} · Area analysis")

    # With no cuisine, the headline read is CONCEPT-INDEPENDENT restaurant
    # persistence — labeled as exactly that, never dressed up as concept fit.
    fit = (concept_fit_cached(panel, cuisine) if cuisine
           else conceptfree_fit_cached(panel))
    frow = fit.loc[code] if code in fit.index else None
    tiers = area_tiers_cached(panel, code, cuisine, concept)
    similar_n = len(tiers["closest"]) + len(tiers["similar"])
    if cuisine:
        dens = density_cached(panel, cuisine)
        sat = areas.competitor_saturation(
            int(dens.loc[code, "active_same"]) if code in dens.index else 0,
            float(dens.loc[code, "density_percentile"])
            if code in dens.index else None)
        gap = areas.opportunity_gap(
            frow["band"] if frow is not None else None, sat["band"])
    else:
        dens = None
        sat = {"band": None, "detail": "No cuisine specified — comparable "
                                       "competition unmeasured."}
        gap = {"band": "Insufficient evidence",
               "reason": "concept fit unmeasured without a cuisine"}
    turn = turnover_cached(panel)
    evidence = evidence_cached(panel)
    ev_band = (evidence.loc[code, "band"] if code in evidence.index else "—")

    fit_text = ("–" if frow is None or pd.isna(frow["fit_index"])
                else f"{frow['fit_index']:.0f}")
    band_text = frow["band"] if frow is not None else "Limited evidence"
    st.markdown(
        f'<span style="font-size:34px;font-weight:600;">{fit_text}</span>'
        f'<span style="font-size:16px;color:var(--text-muted);"> / 100'
        f'</span>  <span style="font-size:15px;'
        f'color:var(--text-secondary);font-weight:600;">{band_text}'
        f'</span>', unsafe_allow_html=True)
    st.caption((f"Relative concept fit · {cuisine} · Evidence: {ev_band}"
                if cuisine else
                f"Restaurant persistence (all concepts) · relative index · "
                f"Evidence: {ev_band}"))

    render_analyze_site_cta(code)
    # The add-to-comparison control is rendered once by the workspace
    # shell, so it exists in every mode rather than only where an area
    # panel happens to be the active branch.

    if compact:
        ui.stat_strip([
            (f"{tiers['total']:,}", "Restaurants"),
            (f"{similar_n:,}" if cuisine else f"{len(tiers['closest']):,}",
             "Similar concept" if cuisine else "Closest match"),
            (sat["band"] or "—", "Competitor density"),
        ])
        if st.button("Open full analysis →", width="stretch",
                     key="open_assess"):
            st.session_state["workspace_view"] = "assess"
            st.rerun()
        return

    ui.eyebrow("How this area looks for your plan")
    turn_band = (turn.loc[code, "band"] if code in turn.index
                 else "Limited evidence")
    ui.bench_rows([
        ("Concept fit" if cuisine else "Restaurant persistence", band_text),
        ("Competition", sat["band"] or "Not measured"),
        ("Opportunity gap", gap["band"]),
        ("Observed turnover", _TURNOVER_READ.get(turn_band, turn_band)),
        ("Evidence", ev_band),
    ])
    if gap["band"] != "Insufficient evidence":
        st.caption(gap["reason"].capitalize() + ".")
    elif not cuisine and concept:
        st.caption(f"“{concept.title()}” has no cuisine — the reads above "
                   f"are concept-independent restaurant evidence, clearly "
                   f"labeled; nothing is invented for the concept.")

    alignment = preference_alignment(
        plan, sat["band"], income_percentile_cached(code),
        float(dens.loc[code, "density_percentile"])
        if dens is not None and code in dens.index else None,
        area_ped_context(code)["band"])
    render_priorities(plan, alignment, cuisine or "Restaurant", name)

    ui.stat_strip([
        (f"{tiers['total']:,}", "Restaurants"),
        (f"{similar_n:,}" if cuisine else f"{len(tiers['closest']):,}",
         "Similar concept" if cuisine else "Closest match"),
        (sat["band"] or "—", "Competitor density"),
    ])
    if tiers["note"]:
        st.caption(tiers["note"])
    grouped = pd.concat([tiers["closest"], tiers["similar"]])
    top = (grouped["cuisine"].value_counts().head(1).index.tolist()
           + tiers["other"]["cuisine"].replace("", pd.NA).dropna()
             .value_counts().head(3).index.tolist())
    if top:
        st.caption("Top cuisines: " + " · ".join(dict.fromkeys(top)))

    with branding.chair_spinner("Loading restaurants…",
                                cold_key=f"area:{code}:{cuisine}"):
        ranking = concept_ranking_cached(panel, code)
    render_concept_rows(panel, code, ranking, own_concept=bool(cuisine))
    if ranking and len(ranking) > 3:
        with st.expander("More concepts & comparison"):
            ui.bench_rows([
                (f"{i}. {r['cuisine']}",
                 f"{r['fit_index']:.0f} · {r['band']} · n={r['cohort_n']}"
                 + (" · label artifact" if _label_artifact(r) else ""))
                for i, r in enumerate(ranking[3:8], 4)])
            picks = st.multiselect(
                "Compare (max 3)", [r["cuisine"] for r in ranking],
                default=[], max_selections=3, key="ws_concepts")
            if picks:
                st.dataframe(areas.compare_concepts(
                    panel, nta_assignment(panel), code, picks),
                    width="stretch")

    render_plan_usage(plan)
    if st.button("Clear area selection", width="stretch"):
        st.session_state.pop("selected_area", None)
        st.rerun()


def render_restaurant_card(panel, camis: str, landscape, site) -> None:
    """One clicked establishment, from records already on hand."""
    row = panel[panel["camis"] == camis]
    if row.empty:
        st.session_state.pop("selected_restaurant", None)
        st.rerun()
        return
    r = row.iloc[0]
    ui.eyebrow("Restaurant")
    st.markdown(f"#### {str(r['name']).title()}")
    ui.bench_rows([
        ("Cuisine", r["cuisine"] or "unspecified"),
        ("Address", r["address"]),
        ("Status", "In 2026 records" if r["seen_2026"] else "Gone by 2026"),
    ])
    # Google enrichment only via coordinates already fetched — no fuzzy match.
    if (landscape is not None and getattr(landscape, "ok", False)
            and pd.notna(r["lat"])):
        from nycsiting.geo import haversine_m
        comp = landscape.competitors
        if len(comp):
            d = haversine_m(r["lat"], r["lon"], comp["latitude"].values,
                            comp["longitude"].values)
            i = int(np.argmin(d))
            if d[i] <= 40:
                g = comp.iloc[i]
                rating = (f"{g['rating']:.1f} ★" if pd.notna(g["rating"])
                          else "unrated")
                st.caption(f"Live listing nearby: {g['name']} · {rating} · "
                           f"{int(g['reviews'] or 0):,} reviews · strength "
                           f"{g['competitor_score']:.0f}/100")
    if site is not None and pd.notna(r["lat"]):
        from nycsiting.geo import haversine_m
        dist = float(haversine_m(site["lat"], site["lon"], r["lat"], r["lon"]))
        st.caption(f"{dist:,.0f}m from your selected site")
    if st.button("← Back to area", width="stretch"):
        st.session_state.pop("selected_restaurant", None)
        st.rerun()


# ---------------------------------------------------------------- nav
def _set_view(view: str) -> None:
    st.session_state["workspace_view"] = view


def _reset_search() -> None:
    """New Search: nothing from the previous plan may leak into the next —
    especially not a cuisine the user never typed."""
    for k in ("selected_area", "selected_restaurant", "workspace_mode",
              "discovery_borough", "requested_area", "workspace_view",
              "cuisine", "ws_concept", "ws_comp", "_comp_mirror",
              COMPARISON_KEY, "report_pdf", "confirmed_plan",
              "plan_confirmed", "price", "plan_text", "plan_outcome",
              "address"):
        st.session_state.pop(k, None)
    st.session_state["stage"] = "landing"


def _to_workspace() -> None:
    if st.session_state.get("plan_confirmed"):
        st.session_state["stage"] = "results"


def render_top_header(stage: str) -> None:
    """
    The top product bar: wordmark · NEW SEARCH | WORKSPACE · caption.
    Exactly one navigation level lives here — the workspace tools (Explore
    / Assess / Compare / Method) are the secondary row, never duplicated.
    """
    in_workspace = stage == "results"
    c_mark, spacer, c_new, c_ws, c_right = st.columns(
        [0.9, 1.8, 0.85, 0.85, 1.3])
    c_mark.markdown(
        '<div class="jx-brand">' + branding.logo_img(height=30)
        + '<span class="jx-wordmark">Siting</span></div>',
        unsafe_allow_html=True)
    c_new.button("New Search", key="nav_new", width="stretch",
                 type="secondary" if in_workspace else "primary",
                 on_click=_reset_search)
    c_ws.button("Workspace", key="nav_workspace", width="stretch",
                type="primary" if in_workspace else "secondary",
                disabled=not st.session_state.get("plan_confirmed"),
                on_click=_to_workspace)
    c_right.markdown('<div class="jx-header-right" style="padding-top:12px;'
                     'text-align:right;">NYC restaurant intelligence</div>',
                     unsafe_allow_html=True)
    st.markdown('<hr style="margin:4px 0 14px 0 !important;">',
                unsafe_allow_html=True)


def render_workspace_nav(view: str) -> None:
    """
    EXPLORE · ASSESS · (COMPARE) · METHOD — the workspace tool row.

    on_click callbacks, deliberately: they run BEFORE the script body, so a
    view switch costs exactly ONE rerun with the new view already active —
    and the run is never interrupted mid-script, which would drop the keyed
    toolbar widget state (layer, filter) Streamlit garbage-collects for
    widgets that missed a run. Selection state is untouched: nothing
    re-parses, re-geocodes, or recomputes.
    """
    has_compare = len(comparison_entries()) >= 2
    widths = ([0.62, 0.62, 0.62, 0.62, 2.9] if has_compare
              else [0.62, 0.62, 0.62, 3.5])
    cols = st.columns(widths)
    cols[0].button("Explore", key="nav_explore", width="stretch",
                   type="primary" if view == "explore" else "secondary",
                   on_click=_set_view, args=("explore",))
    cols[1].button("Assess", key="nav_assess", width="stretch",
                   type="primary" if view == "assess" else "secondary",
                   on_click=_set_view, args=("assess",))
    next_i = 2
    if has_compare:
        cols[2].button("Compare", key="nav_compare", width="stretch",
                       type="primary" if view == "compare" else "secondary",
                       on_click=_set_view, args=("compare",))
        next_i = 3
    cols[next_i].button("Method", key="nav_method", width="stretch",
                        type="primary" if view == "method" else "secondary",
                        on_click=_set_view, args=("method",))


# ---------------------------------------------------------------- main
def main() -> None:
    if not config.RESTAURANTS_PQ.exists():
        st.error("Processed data not found. Run `python build_data.py` first.")
        st.stop()

    # Styles FIRST: the very first thing a cold session does is load the
    # panel, and its spinner has to be the branded one too — injecting
    # afterwards would show Streamlit's default loader for the longest wait
    # in the whole product.
    ui.inject_styles()

    # THEN the overlay, before a single byte of analysis. An action that is
    # about to cause real work leaves its message in session state (see
    # request_loading); painting the overlay here means everything rendered
    # afterwards is produced behind it, so the user never sees the page
    # assembling itself. Everything below runs inside this block.
    with branding.global_loader(st.session_state.get(branding.LOADING_KEY)):
        _main_body()


def _main_body() -> None:
    # Only the panel is needed by every stage. Locations, PLUTO lots and the
    # pedestrian counts feed SITE analysis alone, so they are loaded at the
    # point of use: an area-only session never pays their first-read cost,
    # and the landing page never touches them at all.
    panel = load_panel()

    st.session_state.setdefault("stage", "landing")
    stage = st.session_state["stage"]
    # ONE top-level navigation: New Search | Workspace. The old duplicated
    # Explore/Assess stage spans are gone — the workspace tools live in the
    # secondary row only.
    render_top_header(stage)
    if stage == "simulate":
        if not simulation_enabled():
            st.session_state["stage"] = "results"
            st.rerun()
            return
        simulate_page(panel, load_locations())
        return
    if stage == "confirm":
        confirm_page(panel)
        return
    if stage != "results":
        landing_page(panel)
        return

    if not st.session_state.get("plan_confirmed"):
        st.session_state["stage"] = "landing"
        st.rerun()
        return
    # Concept sync BEFORE analysis: the workspace selectbox is keyed, so its
    # new value is already in session state here — one rerun per concept
    # change, not the mutate-then-rerun double of earlier versions.
    ws_concept = st.session_state.get("ws_concept")
    if ws_concept:
        ws_value = None if ws_concept == CUISINE_ANY else ws_concept
        if ws_value != st.session_state["cuisine"]:
            st.session_state["cuisine"] = ws_value
            # report_pdf was rendered for the PREVIOUS concept; leaving it
            # downloadable would hand the user a report that contradicts
            # the screen.
            for k in ("sim_results", "sim_location_id",
                      "selected_restaurant", "report_pdf"):
                st.session_state.pop(k, None)
    cuisine = st.session_state["cuisine"]
    price = st.session_state.get("price")
    mode = st.session_state.get("workspace_mode", "site")
    address = st.session_state.get("address")
    view = st.session_state.setdefault("workspace_view", "assess")
    plan = st.session_state.get("confirmed_plan")

    # ---------------- top workspace navigation (no sidebar, ever) ----------
    render_workspace_nav(view)
    chips = plan_chip_values(
        plan,
        area_name=nta_names().get(st.session_state.get("selected_area")),
        site_label=address if mode == "site" else None)
    ui.plan_chips(chips, on_remove=remove_plan_constraint)

    if view == "method":
        render_method_page([
            ("DOHMH", True),
            ("Google Places", bool(google_api_key())),
            ("ACS", load_acs() is not None),
            ("PLUTO", True), ("DOT", True)])
        if "dev_trace_ws" not in st.session_state:
            st.session_state["dev_trace_ws"] = st.session_state.get(
                "_dev_trace", False)
        st.checkbox("Developer trace", key="dev_trace_ws")
        st.session_state["_dev_trace"] = st.session_state["dev_trace_ws"]
        return

    if view == "compare":
        if len(comparison_entries()) >= 2:
            render_compare_view(panel, cuisine, plan)
            return
        st.session_state["workspace_view"] = "explore"
        view = "explore"

    site = None
    report = lot = ped = result = landscape = None
    verdicts, fit, band, headline, quality = [], None, None, "", None
    # ws_radius may have been garbage-collected by a run where the slider
    # did not render (Method view, landing) — the mirror is the durable copy.
    # No setdefault here: pre-creating the key would make the toolbar's
    # mirror re-seed unreachable and silently reset the radius to default.
    radius = st.session_state.get(
        "ws_radius",
        st.session_state.get("_radius_mirror", config.DEFAULT_RADIUS_M))

    if mode == "site" and address:
        try:
            site = geocode_cached(address)
        except geocode.GeocodeError as exc:
            render_address_failure(str(exc), address)
            return
        # Canonical coordinates: validated once, reused by every module.
        if not (40.4 <= site["lat"] <= 41.0 and -74.3 <= site["lon"] <= -73.6):
            render_address_failure(
                "That address resolved outside NYC — check the borough.",
                address)
            return
        locs = load_locations()
        key = resolve_location_key(locs, site)
        report = analysis.site_report(panel, locs, site["lat"], site["lon"],
                                      cuisine, radius, key)
        lot = context.lot_context(load_lots(), site.get("bbl"))
        ped = context.nearest_pedestrian(load_pedestrian(), site["lat"],
                                         site["lon"])
        with branding.chair_spinner(
                "Analyzing your location…",
                cold_key=f"site:{site['label']}:{cuisine}:{radius}"):
            result = score_cached(
                report, panel, lot, ped, radius,
                (site["lat"], site["lon"], cuisine, radius, key))
        # The Google text query is built deterministically from EXPLICIT
        # plan fields — "Italian brunch", "French bakery" — so a stated
        # concept sharpens the live layer without any new API.
        gq = google_concept_query(cuisine,
                                  plan.concept if plan else None)
        with branding.chair_spinner(
                "Checking nearby competition…",
                cold_key=f"google:{site['label']}:{gq}"):
            landscape = (competitors_cached(
                site["lat"], site["lon"], gq,
                google_places.DEFAULT_RADIUS_M, site, google_api_key())
                if gq else None)
        verdicts = narrative.component_verdicts(result)
        fit = narrative.fit_score(result)
        band = narrative.fit_band(fit)
        headline = narrative.headline(verdicts, fit,
                                      cuisine or "restaurant")
        quality = narrative.evidence_quality(result, report, landscape)
        if site is not None and not st.session_state.get("selected_area"):
            code = nta_index().locate(site["lat"], site["lon"])
    area_ctx = (site_area_context(panel, site, cuisine, landscape)
                if site is not None else {"nta_code": None})

    # -------- discovery ranking (deterministic, preference-aware) ----------
    # Core fit stays the same validated evidence framework for every user;
    # explicit preferences only reorder DISCOVERY (the user named no area)
    # via a transparent ±10 per stated-preference match/conflict, and both
    # numbers are always shown separately (spec sections 63–65).
    top_matches = None
    prefs_active = 0
    if mode == "discovery":
        borough = st.session_state.get("discovery_borough")
        # No cuisine: rank on CONCEPT-INDEPENDENT evidence — overall
        # restaurant persistence on the same 50-neutral scale, honestly
        # labeled; concept-specific fit is simply not claimed.
        if cuisine:
            with branding.chair_spinner(
                    "Updating concept analysis…",
                    cold_key=f"discovery:{cuisine}:{borough}"):
                fit_table = concept_fit_cached(panel, cuisine)
                dens = density_cached(panel, cuisine)
        else:
            fit_table = conceptfree_fit_cached(panel)
            dens = None
        names = nta_names()
        boroughs = {c: f["borough"] for c, f in nta_index().features.items()}
        prefs_active = sum(1 for v in (
            plan.foot_traffic_preference, plan.competition_tolerance,
            plan.income_preference, plan.restaurant_density_preference)
            if v) if plan is not None else 0
        rows = []
        for code in fit_table.index:
            if borough and boroughs.get(code) != borough:
                continue
            frow = fit_table.loc[code]
            if frow["band"] == "Limited evidence" or pd.isna(frow["fit_index"]):
                continue
            if dens is not None:
                sat = areas.competitor_saturation(
                    int(dens.loc[code, "active_same"])
                    if code in dens.index else 0,
                    float(dens.loc[code, "density_percentile"])
                    if code in dens.index else None)
            else:
                sat = {"band": None}
            gap = (areas.opportunity_gap(frow["band"], sat["band"])
                   if cuisine else {"band": "Insufficient evidence"})
            core = float(frow["fit_index"])
            adjusted, matches, conflicts, measurable = core, 0, 0, 0
            if prefs_active:
                alignment = preference_alignment(
                    plan, sat["band"], income_percentile_cached(code),
                    float(dens.loc[code, "density_percentile"])
                    if dens is not None and code in dens.index else None,
                    area_ped_context(code)["band"])
                matches = sum(r["status"] == "match" for r in alignment)
                conflicts = sum(r["status"] == "conflict" for r in alignment)
                measurable = sum(r["status"] != "unmeasured"
                                 for r in alignment)
                adjusted = core + 10 * matches - 10 * conflicts
            rows.append(dict(code=code, name=names.get(code, code),
                             fit=adjusted, core=core, band=frow["band"],
                             competition=sat["band"], gap=gap["band"],
                             align=(matches, measurable),
                             conflicts=conflicts))
        rows.sort(key=lambda r: -r["fit"])
        top_matches = rows[:3]

    # ---------------- workspace ---------------------------------------------
    # The control row spans the full content width, ABOVE the panes: four
    # controls do not fit in a 532px map column, and Streamlit wrapped them
    # into a ragged two-row block when they did not.
    layer_label, comp_mode = render_workspace_toolbar(panel, cuisine, mode)

    # Explore leads with the map; Assess leads with the analysis. Both are
    # views over the SAME canonical selection state, and in both the map
    # column is STICKY (see .jx-sticky-map in assets/styles.css) so the
    # geography stays on screen while the analysis beside it scrolls.
    if view == "explore":
        map_col, panel_col = st.columns([0.60, 0.40], gap="medium")
    else:
        map_col, panel_col = st.columns([0.42, 0.58], gap="medium")
    with map_col:
        # The marker class the sticky CSS hooks; Streamlit gives the
        # container a st-key-* class of its own, which is what makes this
        # targetable without guessing at emotion hashes.
        with st.container(key="sticky_map"):
            render_map_workspace(panel, site, cuisine, landscape, report,
                                 mode=mode, top_matches=top_matches,
                                 layer_label=layer_label,
                                 comp_mode=comp_mode)

    with panel_col:
        selected_area = st.session_state.get("selected_area")
        selected_rest = st.session_state.get("selected_restaurant")
        # The tray first — a selection made anywhere stays visible from
        # every view — then the in-workspace location picker, so choosing
        # the next place to compare never needs the opening prompt.
        render_compare_tray()
        # The map's explanations live here now, beside the numbers they
        # explain, instead of as paragraphs stacked above the map.
        render_filter_explanation()
        render_workspace_search()
        # ONE add control, rendered from the shell rather than from inside a
        # panel renderer. The panel below is a mutually-exclusive chain over
        # (restaurant, view, mode, area), and the add action used to hang
        # off a single branch of it — so whether the button existed depended
        # on a four-way combination of state. That is the whole of "Add to
        # comparison only works sometimes". Deriving the subject here means
        # whatever the workspace is currently analysing can be compared.
        render_add_to_comparison(*current_comparison_subject(
            site, mode, selected_area, view))

        if selected_rest:
            render_restaurant_card(panel, selected_rest, landscape, site)
        elif area_is_subject(selected_area, view):
            # The area the user actually chose wins, in EITHER view — the
            # same rule the add control uses, so the panel and the button
            # can never describe different places. Previously this branch
            # was `view == "explore"` alone, so in Assess an explicitly
            # clicked neighbourhood fell through to the site branch below
            # and "Open full analysis →" on Gramercy opened the address.
            # A site is still reachable: Back-to-explore marks its area as
            # CONTEXT rather than subject, so the site keeps the panel.
            render_area_explorer(panel, selected_area, cuisine,
                                 compact=view == "explore")
        elif mode == "site" and site is not None:
            if view == "explore":
                ui.eyebrow(f"{cuisine or 'Restaurant'} · Site analysis")
                st.markdown(f"#### {site['label']}")
                st.markdown(
                    f'<span style="font-size:30px;font-weight:600;">'
                    f'{fit if fit is not None else "–"}</span>'
                    f'<span style="font-size:15px;color:var(--text-muted);">'
                    f' / 100</span>  <span style="font-size:14px;'
                    f'color:var(--text-secondary);font-weight:600;">{band}'
                    f'</span>', unsafe_allow_html=True)
                st.caption("Location fit · relative index")
                if st.button("Open full analysis →", width="stretch",
                             key="open_assess", type="primary"):
                    st.session_state["workspace_view"] = "assess"
                    st.rerun()
            else:
                render_site_panel(panel, site, cuisine, price, report,
                                  result, landscape, verdicts, fit, band,
                                  headline, quality, area_ctx, lot, ped)
        elif selected_area:
            render_area_explorer(panel, selected_area, cuisine,
                                 compact=view == "explore")
        elif mode == "discovery" and top_matches is not None:
            borough = st.session_state.get("discovery_borough")
            ui.eyebrow("Preference-adjusted discovery" if prefs_active
                       else "Top matches")
            st.markdown((f"### Where {cuisine} shows the best relative fit"
                         if cuisine else
                         "### Where restaurants persist best")
                        + (f" · {borough}" if borough else ""))
            if not cuisine:
                concept_word = (f"“{plan.concept}”" if plan and plan.concept
                                else "your concept")
                st.caption(f"No cuisine was specified, so this ranking uses "
                           f"concept-independent evidence — overall "
                           f"restaurant persistence, turnover and area "
                           f"records. Current data cannot rank areas for "
                           f"{concept_word} specifically.")
            if not top_matches:
                st.caption("No area currently meets the evidence gates for "
                           "this concept"
                           + (f" in {borough}" if borough else "")
                           + " — the local samples are too small for a "
                           "reliable comparison. Try a different concept"
                           + (" or drop the borough constraint."
                              if borough else "."))
            for i, match in enumerate(top_matches, 1):
                label = (f"{i:02d}  {match['name']}  ·  Core fit "
                         f"{match['core']:.0f}")
                if prefs_active:
                    matches_n, measurable = match["align"]
                    label += f"  ·  {matches_n}/{measurable} align"
                    if match.get("conflicts"):
                        label += f", {match['conflicts']} conflict"
                if st.button(label, key=f"top_{match['code']}",
                             width="stretch"):
                    select_area(match["code"], source="top_match")
                    st.rerun()
            if prefs_active:
                st.caption("Core fit uses the same evidence rules for "
                           "everyone. Ordering = core fit +10 per stated "
                           "priority that matches, −10 per conflict — "
                           "shown, never hidden. Neither number is a "
                           "success ranking.")
            else:
                st.caption("Relative fit under the same evidence rules — "
                           "not a success ranking. Click a match to "
                           "explore it.")
            render_plan_usage(plan)
        else:
            st.caption("Click an area on the map to explore it.")

        # STILL INSIDE panel_col. These were the last sections rendering
        # below both columns at full width; with a sticky map beside them,
        # anything outside the right pane leaves the left half blank.
        if mode == "site" and site is not None:
            if st.session_state.get("_dev_trace"):
                render_trace(site, key, report, result, landscape, ped, lot)
            if view == "assess":
                render_methodology(result, radius)
        else:
            with st.expander("Data & methodology"):
                st.caption("Full methodology lives under Method in the top "
                           "navigation; sources: DOHMH, Google Places, 2024 "
                           "ACS, PLUTO, NYC DOT, NYC Planning geographies.")


if __name__ == "__main__":
    main()
