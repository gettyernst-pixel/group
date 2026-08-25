"""
NYC restaurant location screening — Streamlit front end.

Run with:   streamlit run app.py
Data first: python build_data.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from nycsiting import (acs, analysis, areas, config, geometry, plan_parser,
                       workspace_map,
                       context, cuisines, financial_simulation as fs,
                       geocode, google_places, mapview,
                       narrative, nta, pedestrian_dot,
                       scoring, sim_animation, ui)
from nycsiting.normalize import location_key_variants

st.set_page_config(page_title="Siting — NYC Restaurant Location Intelligence",
                   layout="wide")

# ---------------------------------------------------------------- data load
@st.cache_data(show_spinner="Loading restaurant panel…")
def load_panel() -> pd.DataFrame:
    df = pd.read_parquet(config.RESTAURANTS_PQ)
    for col in ("first_observed", "last_observed", "closed_after", "closed_before"):
        df[col] = pd.to_datetime(df[col])
    return df


@st.cache_data(show_spinner=False)
def load_locations() -> pd.DataFrame:
    return pd.read_parquet(config.LOCATIONS_PQ)


@st.cache_data(show_spinner=False)
def load_lots() -> pd.DataFrame:
    return pd.read_parquet(config.LOTS_PQ).set_index("bbl")


@st.cache_data(show_spinner=False)
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
    Identical prompts parse once. The secret itself stays out of the cache
    key (underscored), but `key_present` is IN it — without that, a fallback
    parse cached while the key was missing would keep replaying after the
    key appears, which is exactly the bug this fixes.
    """
    return plan_parser.parse_plan(text, _api_key)


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


@st.cache_data(show_spinner=False)
def nta_geojson():
    return nta_index().to_geojson()


@st.cache_data(show_spinner=False)
def nta_names() -> dict:
    return {code: f["name"] for code, f in nta_index().features.items()}


@st.cache_data(show_spinner="Assigning restaurants to areas…")
def nta_assignment(_panel) -> pd.Series:
    return geometry.assign_restaurants(_panel, nta_index())


@st.cache_data(show_spinner=False)
def area_features_cached(_panel) -> pd.DataFrame:
    return areas.area_features(_panel, nta_assignment(_panel))


@st.cache_data(show_spinner=False)
def concept_fit_cached(_panel, cuisine: str) -> pd.DataFrame:
    return areas.area_concept_fit(_panel, nta_assignment(_panel), cuisine)


@st.cache_data(show_spinner=False)
def density_cached(_panel, cuisine: str) -> pd.DataFrame:
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
def concept_ranking_cached(_panel, nta_code: str) -> list[dict]:
    return areas.rank_concepts_for_area(_panel, nta_assignment(_panel), nta_code)


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
    """Compact comparison rows; the statistical reading rules as captions.
    Rendered inside a collapsed detail expander, so no nested expanders."""
    cuisine = report["query"]["cuisine"]
    comps = report["comparisons"]
    if not comps:
        st.caption(f"No {cuisine} restaurant near here appears in the "
                   f"2011–2017 archive — no local track record to compare.")
        return

    glyph = {"below": "↓ lower", "above": "↑ higher",
             "inconclusive": "≈ not distinguishable"}
    ui.bench_rows([
        (c["question"].rstrip("?"),
         f"{100*c['subject_rate']:.0f}% vs {100*c['baseline_rate']:.0f}% · "
         f"{glyph[c['verdict']]}")
        for c in comps])
    smallest = min(c["subject_n"] for c in comps)
    st.caption(
        f"'Still listed' means a 2011–2017 restaurant appears in the 2026 "
        f"records — persistence, not profitability. Differences inside the "
        f"margin of error (smallest sample here: n={smallest}) read as "
        f"'not distinguishable', never as findings.")


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


def render_google(landscape, cuisine: str, price: str = "$$") -> None:
    """
    Live competition: four numbers and one line by default; ranked rows,
    price mix and the strength methodology one expander away. Failure states
    are one calm line each — the assessment does not depend on this layer.
    """
    if not landscape.ok:
        if landscape.reason == "no_key":
            st.info("Live competitor data is not configured — add a "
                    "`GOOGLE_MAPS_API_KEY` in `.streamlit/secrets.toml` "
                    "(see README). The assessment above is unaffected.")
        else:
            st.info("Live competitor data unavailable. The assessment above "
                    "is built from public records and is unaffected.")
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
            same = mix.get(price, 0)
            st.caption(
                f"**Your price point: {price}.** Most priced competitors sit "
                f"at {common} ({mix[common]} of {priced}); {same} are at "
                f"{price}."
                + (" You would be pitching where this block already sits."
                   if common == price else
                   f" {price} is away from the local norm — an opening or a "
                   f"mismatch; this data cannot tell you which."))

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
    st.markdown('<div style="height:56px"></div>', unsafe_allow_html=True)
    ui.eyebrow("Restaurant location intelligence")
    ui.display("Where should your<br>restaurant go?")
    st.markdown("Tell us what you're planning.")
    st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)

    with st.container(border=True):
        text = st.text_area(
            "Your plan", value=st.session_state.get("plan_text", ""),
            placeholder=("I want to open an upscale Italian restaurant in "
                         "10003. About $70 per person and around 60 seats. "
                         "Good pedestrian activity, not extreme competition."),
            height=110, label_visibility="collapsed")
        go = st.button("Continue →", type="primary", width="stretch",
                       disabled=not text.strip())
    st.caption("Try: " + " · ".join(f"*{e}*" for e in EXAMPLE_PLANS))

    if go:
        # A new plan invalidates everything downstream of the old one.
        for key in ("plan_outcome", "plan_confirmed", "sim_results",
                    "sim_location_id", "address", "cuisine"):
            st.session_state.pop(key, None)
        st.session_state["plan_text"] = text.strip()
        key = get_anthropic_api_key()
        st.session_state["plan_outcome"] = parse_plan_cached(
            text.strip(), plan_parser.PARSER_VERSION,
            plan_parser.ANTHROPIC_PARSER_MODEL, bool(key), key)
        st.session_state["stage"] = "confirm"
        st.rerun()

    saved = st.session_state.get("saved", [])
    if saved:
        st.divider()
        ui.eyebrow("Analyzed locations")
        st.dataframe(pd.DataFrame(saved), width="stretch", hide_index=True)


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
    """
    Local-market detail: 2024 ACS 5-Year tract estimates with NYC-tract
    percentiles, plus the measured pedestrian context. Hosted in a collapsed
    expander. Neighbourhood context about residents — never a claim that
    residents are customers.
    """
    if tract:
        def cell(key, money=False, decimals=0):
            m = tract.get(key) or {}
            return (_fmt_compact(m.get("value"), money, decimals),
                    m.get("percentile"))

        pop, pop_pct = cell("population")
        inc, inc_pct = cell("median_household_income", money=True)
        age, age_pct = cell("median_age", decimals=1)
        emp, emp_pct = cell("employed_population")
        rent, rent_pct = cell("median_gross_rent", money=True)
        ui.stat_strip([
            (pop, "Population"), (inc, "Median household income"),
            (age, "Median age"), (emp, "Employed residents"),
            (rent, "Median gross rent"),
        ])
        pct_bits = [f"income {p:.0f}th" if k == "inc" else f"{k} {p:.0f}th"
                    for k, p in (("population", pop_pct), ("inc", inc_pct),
                                 ("age", age_pct), ("rent", rent_pct))
                    if p is not None]
        source_word = ("Census tract" if tract_source == "census_geocoder"
                       else "Census tract (borrowed from the nearest listed "
                            "restaurant)")
        st.caption(f"2024 ACS 5-Year · {source_word}"
                   + (" · percentile among NYC tracts: "
                      + ", ".join(pct_bits) if pct_bits else "")
                   + ". A dash means the Census suppressed that estimate.")
        st.caption(":grey[ACS values describe residents of this tract — they "
                   "do not directly measure restaurant customers.]")
    elif load_acs() is None:
        st.caption("Local demographics are not fetched yet — run "
                   "`python scripts/fetch_acs_nyc.py` with a CENSUS_API_KEY "
                   "configured (see README). Five requests, one per borough.")
    else:
        st.caption("No Census tract could be resolved for this address — "
                   "local demographics are not shown rather than guessed.")

    foot = next((v for v in verdicts if v["key"] == "foot_traffic"), None)
    if ped:
        tag = ("Measured nearby" if ped.get("represents_this_block")
               else "Nearby reference — district context, not this doorway")
        st.caption(f"Pedestrians: {ped['count']:,} counted on {ped['street']}, "
                   f"{ped['distance_m']:.0f}m away ({ped['period']}). "
                   f"Bi-annual NYC DOT reference · {tag}.")
    else:
        st.caption("No reliable pedestrian measurement is available near "
                   "this location.")


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


def render_next(site: dict, cuisine: str, fit: int | None,
                verdicts: list[dict], landscape) -> None:
    """Step 12: keep the decision moving — or go deeper on this address."""
    st.markdown("### What next?")
    if st.button("Simulate opening here →", type="primary", width="stretch"):
        # The simulation belongs to THIS address and concept; stamp the pair
        # so stale results can never render for a different query.
        st.session_state["sim_location_id"] = (site["label"], cuisine)
        st.session_state.pop("sim_results", None)
        st.session_state["stage"] = "simulate"
        st.rerun()
    st.caption(
        "A scenario-based financial model of opening at this address — every "
        "assumption visible and editable. Estimates, not forecasts.")

    st.markdown("#### Compare this against somewhere else")
    row = narrative.comparison_row(site["label"], cuisine, fit, verdicts, landscape)
    area_ctx = site_area_context(panel_for_compare(), site, cuisine, landscape)
    if area_ctx.get("nta_code"):
        row["Area"] = area_ctx["nta_name"]
        row["Competitor density"] = area_ctx["saturation"]["band"] or "—"
        row["Opportunity gap"] = area_ctx["gap"]["band"]
    saved = st.session_state.setdefault("saved", [])
    already = any(r["Location"] == row["Location"] and r["Concept"] == row["Concept"]
                  for r in saved)

    a, b, c = st.columns([1.2, 1.2, 1])
    if a.button("Save & compare another location", type="primary",
                width="stretch", disabled=already):
        saved.append(row)
        st.session_state.update(stage="landing")
        st.rerun()
    if b.button("Change concept", width="stretch"):
        st.session_state.update(stage="landing")
        st.rerun()
    if saved and c.button("Clear saved", width="stretch"):
        st.session_state["saved"] = []
        st.rerun()
    if already:
        a.caption("Already saved.")

    if saved:
        st.markdown("**Saved locations**")
        st.dataframe(pd.DataFrame(saved), width="stretch", hide_index=True)


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
            "| US Census ACS | *Not currently usable — see below* |\n")
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
        ui.query_context(cuisine, st.session_state.get("price", "$$"), address)

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
    assignment = nta_assignment(_panel)
    merged = _panel.merge(assignment.rename("nta_2020"), left_on="camis",
                          right_index=True)
    counts = merged[merged["zipcode"] == zipcode]["nta_2020"].value_counts()
    return counts.index[0] if len(counts) else None


def neighborhood_to_nta(name: str) -> str | None:
    """Name -> NTA code: exact match first, then unique containment."""
    if not name:
        return None
    wanted = name.strip().lower()
    names = nta_names()
    exact = [c for c, n in names.items() if n.lower() == wanted]
    if exact:
        return exact[0]
    contains = [c for c, n in names.items()
                if wanted in n.lower() or n.lower() in wanted]
    return contains[0] if len(contains) == 1 else None


def polygon_bounds(code: str) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) of one NTA."""
    x0, y0, x1, y1 = nta_index().features[code]["bbox"]
    return y0, y1, x0, x1


def zoom_for_bounds(bounds) -> tuple[tuple[float, float], float]:
    """fitBounds: center + a zoom derived from the polygon span + padding."""
    import math
    min_lat, max_lat, min_lon, max_lon = bounds
    center = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
    span = max(max_lat - min_lat, max_lon - min_lon, 1e-4) * 1.35
    zoom = float(np.clip(math.log2(360.0 / span) - 0.4, 10.0, 14.2))
    return center, zoom


def select_area(code: str) -> None:
    """THE one selection handler — polygon clicks and Top-match clicks share
    it, so they can never behave differently."""
    st.session_state["selected_area"] = code
    st.session_state.pop("selected_restaurant", None)


def area_restaurants(panel, code: str, cuisine: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    (similar, other) CURRENT establishments inside one NTA — spatial
    membership from the precomputed point-in-polygon assignment, one marker
    per CAMIS, never inspection rows.
    """
    assignment = nta_assignment(panel)
    merged = panel.merge(assignment.rename("nta_2020"), left_on="camis",
                         right_index=True)
    inside = merged[(merged["nta_2020"] == code) & merged["seen_2026"]
                    & merged["lat"].notna()]
    compset = cuisines.competitive_set(cuisine)
    similar = inside[inside["cuisine"].isin(compset)]
    other = inside[~inside["cuisine"].isin(compset)]
    return similar, other


def _apply_map_selection(event, panel) -> None:
    """One handler for every map click: polygons select areas, restaurant
    points select restaurants. Top-match buttons call select_area directly,
    so both paths share the same state transition."""
    if not event or not event.get("selection"):
        return
    points = event["selection"].get("points") or []
    for point in points:
        custom = point.get("customdata")
        if isinstance(custom, (list, tuple)) and custom and                 str(custom[0]).startswith("camis:"):
            st.session_state["selected_restaurant"] = str(custom[0])[6:]
            st.rerun()
        location = point.get("location")
        if location and location in nta_index().features:
            if st.session_state.get("selected_area") != location:
                select_area(location)
                st.rerun()


def render_map_workspace(panel, site, cuisine: str, landscape,
                         report, mode: str = "site",
                         top_matches: list | None = None) -> None:
    """The persistent map: toolbar, one layer figure, selection handling."""
    top1, top2, top3 = st.columns([1.1, 1.5, 1])
    with top1:
        options = cuisine_options(panel)
        idx = options.index(cuisine) if cuisine in options else 0
        new_cuisine = st.selectbox("Concept", options, index=idx,
                                   key="ws_concept")
        if new_cuisine != cuisine:
            st.session_state["cuisine"] = new_cuisine
            for k in ("sim_results", "sim_location_id",
                      "selected_restaurant"):
                st.session_state.pop(k, None)
            st.rerun()
    with top2:
        layer_label = st.selectbox("Layer", list(LAYER_CHOICES),
                                   key="ws_layer")
    with top3:
        comp_mode = st.radio("Competitors", ["Similar", "All"],
                             horizontal=True, key="ws_comp",
                             label_visibility="collapsed")

    layer = LAYER_CHOICES[layer_label]
    geojson = nta_geojson()
    hover = _hover_frame(panel)
    selected_area = st.session_state.get("selected_area")

    # --- fitBounds: selected area > exact site > citywide ------------------
    if selected_area:
        center, zoom = zoom_for_bounds(polygon_bounds(selected_area))
    elif site is not None:
        center, zoom = (site["lat"], site["lon"]), 13.6
    else:
        center, zoom = (40.72, -73.97), 9.9

    fig = _layer_figure(panel, layer, cuisine, geojson, hover, center, zoom,
                        site, report)

    # --- selection emphasis -------------------------------------------------
    if selected_area:
        fset = nta_index().features[selected_area]
        for poly in fset["polygons"]:
            lons = [pt[0] for pt in poly[0]] + [poly[0][0][0]]
            lats = [pt[1] for pt in poly[0]] + [poly[0][0][1]]
            fig.add_trace(go.Scattermapbox(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=2.5, color=workspace_map.TOKENS["accent"]),
                hoverinfo="skip", showlegend=False))
        similar, other = area_restaurants(panel, selected_area, cuisine)
        if comp_mode == "All" and len(other):
            fig.add_trace(go.Scattermapbox(
                lat=other["lat"], lon=other["lon"], mode="markers",
                marker=dict(size=6, color=workspace_map.TOKENS["muted"],
                            opacity=0.45),
                name=f"Other restaurants ({len(other)})",
                customdata=[[f"camis:{c}"] for c in other["camis"]],
                text=other["name"].str.title() + " · " +
                     other["cuisine"].replace("", "unspecified"),
                hovertemplate="%{text}<extra></extra>"))
        if len(similar):
            fig.add_trace(go.Scattermapbox(
                lat=similar["lat"], lon=similar["lon"], mode="markers",
                marker=dict(size=10, color=workspace_map.TOKENS["accent"]),
                name=f"Similar ({len(similar)})",
                customdata=[[f"camis:{c}"] for c in similar["camis"]],
                text=similar["name"].str.title() + " · " + similar["cuisine"],
                hovertemplate="%{text}<extra></extra>"))

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

    if site is not None:
        fig.add_trace(go.Scattermapbox(
            lat=[site["lat"]], lon=[site["lon"]], mode="markers",
            marker=dict(size=16, color="#FFFFFF"),
            name="Selected site",
            hovertemplate=f"<b>{site['label']}</b><br>Selected site"
                          "<extra></extra>"))
    if (site is not None and landscape is not None
            and getattr(landscape, "ok", False) and comp_mode == "Similar"
            and not selected_area):
        fig = workspace_map.competitor_markers(fig, landscape.competitors)

    event = st.plotly_chart(fig, width="stretch", key="ws_map",
                            on_select="rerun",
                            selection_mode=("points",))
    _apply_map_selection(event, panel)


def _layer_figure(panel, layer, cuisine, geojson, hover, center, zoom,
                  site, report):
    if layer == "concept_fit":
        fit = concept_fit_cached(panel, cuisine)
        return workspace_map.band_choropleth(
            geojson, fit["band"], "concept_fit", hover, center, zoom)
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
            geojson, pd.Series(bands), "opportunity_gap", hover, center, zoom)
    if layer == "turnover":
        return workspace_map.band_choropleth(
            geojson, turnover_cached(panel)["band"], "turnover", hover,
            center, zoom)
    if layer == "evidence":
        return workspace_map.band_choropleth(
            geojson, evidence_cached(panel)["band"], "evidence", hover,
            center, zoom)
    if layer == "cuisine_density":
        dens = density_cached(panel, cuisine)
        return workspace_map.continuous_choropleth(
            geojson, dens["active_same"].astype(float),
            f"{cuisine} (active)", center=center, zoom=zoom)
    if layer == "persistence":
        feats = area_features_cached(panel)
        return workspace_map.continuous_choropleth(
            geojson, (feats["persistence_rate"] * 100).round(0),
            "Still listed (%)", hover_fmt="%{z:.0f}%", center=center,
            zoom=zoom)
    if layer in ("population", "income_context"):
        _, demo = acs_by_nta_cached()
        if demo is None:
            return workspace_map.band_choropleth(
                geojson, pd.Series(dtype=object), "evidence", hover, center,
                zoom)
        column = "population" if layer == "population" else "income_context"
        title = "Residents" if layer == "population" else "Income context ($)"
        return workspace_map.continuous_choropleth(
            geojson, demo[column], title, center=center, zoom=zoom)
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
    fit = concept_fit_cached(panel, cuisine)
    return workspace_map.band_choropleth(
        geojson, fit["band"], "concept_fit", hover, center, zoom)


def site_area_context(panel, site: dict, cuisine: str, landscape) -> dict:
    """Saturation / gap / ranking for the NTA containing the site."""
    code = nta_index().locate(site["lat"], site["lon"])
    if code is None:
        return {"nta_code": None}
    fit = concept_fit_cached(panel, cuisine)
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
    return {"nta_code": code, "nta_name": nta_names().get(code, code),
            "fit_band": fit_band, "saturation": saturation, "gap": gap}


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
            options = sorted(known)
            index = options.index(normalized) if normalized in options else 0
            cuisine = st.selectbox(
                "Cuisine", options, index=index,
                help=None if normalized else
                "We couldn't match your wording to a known cuisine — "
                "pick one.")
            concept = st.text_input("Concept", value=plan.concept or "",
                                    placeholder="e.g. upscale, casual")
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
        if not (address.strip() or area.strip()):
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
        st.session_state["confirmed_plan"] = edited
        st.session_state["plan_confirmed"] = True
        st.session_state["cuisine"] = cuisine
        st.session_state["price"] = plan.price_positioning or "$$"
        # An "address" with no building number is an area phrase, whatever
        # parser produced it — geocoding it invites the silent-mis-placement
        # class of failure (GeoSearch picks an arbitrary building).
        if (edited.location_kind() == "address"
                and not geocode.has_house_number(edited.address or "")):
            edited = edited.copy(update=dict(
                address=None,
                neighborhood=edited.neighborhood or edited.address))
        if edited.location_kind() == "address":
            target = edited.address
            if area.strip() and not any(
                    tok.lower() in target.lower()
                    for tok in area.replace(",", " ").split()):
                target = f"{target}, {area.strip()}"
            st.session_state["address"] = target
            st.session_state["workspace_mode"] = "site"
        else:
            # AREA / DISCOVERY — no street address required, ever.
            st.session_state["address"] = None
            panel_df = load_panel()
            area_text = (edited.zipcode or edited.neighborhood or area.strip()
                         or "")
            code = None
            if edited.zipcode:
                code = zip_to_nta(panel_df, edited.zipcode)
            elif area_text:
                code = neighborhood_to_nta(area_text)
            if code:
                st.session_state["workspace_mode"] = "area"
                select_area(code)
            else:
                st.session_state["workspace_mode"] = "discovery"
                st.session_state["discovery_borough"] = edited.borough
        st.session_state["stage"] = "results"
        st.rerun()



# ---------------------------------------------------------------- panels
def render_site_panel(panel, site, cuisine, price, report, result, landscape,
                      verdicts, fit, band, headline, quality, area_ctx,
                      lot, ped) -> None:
    """ONE verdict, above the fold, then tabs. No duplicated band anywhere."""
    ui.eyebrow(f"{cuisine} · Site analysis")
    st.markdown(f"#### {site['label']}")
    a, b = st.columns([1.2, 1])
    with a:
        st.markdown(
            f'<span style="font-size:34px;font-weight:600;">'
            f'{fit if fit is not None else "–"}</span>'
            f'<span style="font-size:15px;color:var(--text-secondary);"> '
            f'{band}</span>', unsafe_allow_html=True)
        st.markdown(f"<span style='font-size:12.5px;color:var(--text-muted);'>"
                    f"Location fit · relative index · Evidence quality: "
                    f"{quality[0] if quality else '—'}</span>",
                    unsafe_allow_html=True)
    with b:
        if st.button("Simulate →", type="primary", width="stretch"):
            st.session_state["sim_location_id"] = (site["label"], cuisine)
            st.session_state.pop("sim_results", None)
            st.session_state["stage"] = "simulate"
            st.rerun()
    st.markdown(headline)
    if site.get("warning"):
        st.warning(f"**Check the address.** {site['warning']}")

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
            ranking = concept_ranking_cached(panel, area_ctx["nta_code"])
            if ranking:
                ui.eyebrow("Best-fitting concepts here")
                ui.bench_rows([
                    (f"{i}. {r['cuisine']}",
                     f"{r['fit_index']:.0f} · {r['band']}")
                    for i, r in enumerate(ranking[:3], 1)])
                with st.expander("More concepts & comparison"):
                    ui.bench_rows([
                        (f"{i}. {r['cuisine']}",
                         f"{r['fit_index']:.0f} · {r['band']} · "
                         f"n={r['cohort_n']}")
                        for i, r in enumerate(ranking[3:8], 4)])
                    picks = st.multiselect(
                        "Compare (max 3)", [r["cuisine"] for r in ranking],
                        default=[], max_selections=3, key="ws_concepts")
                    if picks:
                        st.dataframe(areas.compare_concepts(
                            panel, nta_assignment(panel),
                            area_ctx["nta_code"], picks), width="stretch")
        # Strengths / risks — the ONLY other place conclusions appear.
        render_recommendation(
            narrative.assessment_label(fit, landscape), headline,
            narrative.reason_to_proceed(verdicts),
            narrative.reason_for_caution(verdicts, landscape), cuisine)
        render_next(site, cuisine, fit, verdicts, landscape)
    with tab_comp:
        render_google(landscape, cuisine, price)
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
        with st.expander(f"Cuisine performance — {cuisine} track record"):
            render_cuisine(report)
    with tab_property:
        render_context(lot, ped)
    render_limitations()


def render_area_explorer(panel, code: str, cuisine: str) -> None:
    """AREA EXPLORER: the polygon's intelligence, compact, no site score."""
    name = nta_names().get(code, code)
    ui.eyebrow(f"{cuisine} · Area analysis")
    st.markdown(f"#### {name}")

    fit = concept_fit_cached(panel, cuisine)
    frow = fit.loc[code] if code in fit.index else None
    similar, other = area_restaurants(panel, code, cuisine)
    dens = density_cached(panel, cuisine)
    sat = areas.competitor_saturation(
        int(dens.loc[code, "active_same"]) if code in dens.index else 0,
        float(dens.loc[code, "density_percentile"])
        if code in dens.index else None)
    gap = areas.opportunity_gap(
        frow["band"] if frow is not None else None, sat["band"])
    turn = turnover_cached(panel)
    evidence = evidence_cached(panel)

    a, b = st.columns([1.2, 1])
    with a:
        fit_text = ("–" if frow is None or pd.isna(frow["fit_index"])
                    else f"{frow['fit_index']:.0f}")
        band_text = frow["band"] if frow is not None else "Limited evidence"
        st.markdown(
            f'<span style="font-size:34px;font-weight:600;">{fit_text}</span>'
            f'<span style="font-size:15px;color:var(--text-secondary);"> '
            f'{band_text}</span>', unsafe_allow_html=True)
        st.caption(f"{cuisine} concept fit · Evidence: "
                   f"{evidence.loc[code, 'band'] if code in evidence.index else '—'}")
    with b:
        st.caption("Have a specific address here?")
        addr = st.text_input("Address", key=f"area_addr_{code}",
                             label_visibility="collapsed",
                             placeholder="Analyze a site →")
        if addr.strip():
            st.session_state.update(
                address=f"{addr.strip()}", workspace_mode="site")
            st.session_state.pop("selected_area", None)
            st.rerun()

    ui.stat_strip([
        (f"{len(similar) + len(other):,}", "Restaurants"),
        (f"{len(similar):,}", "Similar concept"),
        (sat["band"] or "—", "Competitor density"),
    ])
    ui.evidence_rows([
        dict(label="Opportunity gap", verdict=gap["band"],
             tone={"High": "good", "Moderate": "neutral",
                   "Low": "concern"}.get(gap["band"], "unknown"),
             conclusion=gap["reason"].capitalize(), evidence_stat=""),
        dict(label="Observed turnover",
             verdict=turn.loc[code, "band"] if code in turn.index else "—",
             tone="neutral", conclusion="", evidence_stat=""),
    ])
    top = (similar["cuisine"].value_counts().head(1).index.tolist()
           + other["cuisine"].replace("", pd.NA).dropna()
             .value_counts().head(3).index.tolist())
    if top:
        st.caption("Top cuisines: " + " · ".join(dict.fromkeys(top)))

    ranking = concept_ranking_cached(panel, code)
    if ranking:
        ui.eyebrow("Best-fitting concepts")
        ui.bench_rows([(f"{i}. {r['cuisine']}",
                        f"{r['fit_index']:.0f} · {r['band']}")
                       for i, r in enumerate(ranking[:3], 1)])
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


# ---------------------------------------------------------------- main
def main() -> None:
    if not config.RESTAURANTS_PQ.exists():
        st.error("Processed data not found. Run `python build_data.py` first.")
        st.stop()

    panel = load_panel()
    locs = load_locations()
    lots = load_lots()
    ped_sites = load_pedestrian()

    ui.inject_styles()
    st.session_state.setdefault("stage", "landing")
    stage = st.session_state["stage"]
    assessed = bool(st.session_state.get("address")) and stage in ("results", "simulate")
    ui.page_header({"landing": "explore", "confirm": "explore",
                    "results": "assess",
                    "simulate": "simulate"}[stage], simulate_enabled=assessed)
    if stage == "simulate":
        simulate_page(panel, locs)
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
    cuisine = st.session_state["cuisine"]
    price = st.session_state.get("price", "$$")
    mode = st.session_state.get("workspace_mode", "site")
    address = st.session_state.get("address")

    site = None
    report = lot = ped = result = landscape = None
    verdicts, fit, band, headline, quality = [], None, None, "", None
    radius = st.session_state.get("ws_radius", config.DEFAULT_RADIUS_M)

    if mode == "site" and address:
        try:
            site = geocode_cached(address)
        except geocode.GeocodeError as exc:
            st.error(str(exc))
            if st.button("Try another address"):
                st.session_state["stage"] = "landing"
                st.rerun()
            return
        # Canonical coordinates: validated once, reused by every module.
        if not (40.4 <= site["lat"] <= 41.0 and -74.3 <= site["lon"] <= -73.6):
            st.error("That address resolved outside NYC — check the borough.")
            return
        key = resolve_location_key(locs, site)
        report = analysis.site_report(panel, locs, site["lat"], site["lon"],
                                      cuisine, radius, key)
        lot = context.lot_context(lots, site.get("bbl"))
        ped = context.nearest_pedestrian(ped_sites, site["lat"], site["lon"])
        result = score_cached(report, panel, lot, ped, radius,
                              (site["lat"], site["lon"], cuisine, radius, key))
        landscape = competitors_cached(
            site["lat"], site["lon"], cuisine,
            google_places.DEFAULT_RADIUS_M, site, google_api_key())
        verdicts = narrative.component_verdicts(result)
        fit = narrative.fit_score(result)
        band = narrative.fit_band(fit)
        headline = narrative.headline(verdicts, fit, cuisine)
        quality = narrative.evidence_quality(result, report, landscape)
        if site is not None and not st.session_state.get("selected_area"):
            code = nta_index().locate(site["lat"], site["lon"])
    area_ctx = (site_area_context(panel, site, cuisine, landscape)
                if site is not None else {"nta_code": None})

    # ---------------- discovery ranking (deterministic) ----------------------
    top_matches = None
    if mode == "discovery":
        borough = st.session_state.get("discovery_borough")
        fit_table = concept_fit_cached(panel, cuisine)
        dens = density_cached(panel, cuisine)
        names = nta_names()
        boroughs = {c: f["borough"] for c, f in nta_index().features.items()}
        plan = st.session_state.get("confirmed_plan")
        rows = []
        for code in fit_table.index:
            if borough and boroughs.get(code) != borough:
                continue
            frow = fit_table.loc[code]
            if frow["band"] == "Limited evidence" or pd.isna(frow["fit_index"]):
                continue
            sat = areas.competitor_saturation(
                int(dens.loc[code, "active_same"]) if code in dens.index else 0,
                float(dens.loc[code, "density_percentile"])
                if code in dens.index else None)
            gap = areas.opportunity_gap(frow["band"], sat["band"])
            score = float(frow["fit_index"])
            if plan is not None and plan.competition_tolerance == "low":
                score += {"Low": 10, "Moderate": 0, "High": -10}.get(
                    sat["band"], 0)
            rows.append(dict(code=code, name=names.get(code, code),
                             fit=score, band=frow["band"],
                             competition=sat["band"], gap=gap["band"]))
        rows.sort(key=lambda r: -r["fit"])
        top_matches = rows[:3]

    # ---------------- left nav ----------------------------------------------
    with st.sidebar:
        st.markdown("### Siting")
        ui.eyebrow({"site": "Site analysis", "area": "Area analysis",
                    "discovery": "Discovery"}[mode])
        if site is not None:
            st.caption(f"**{site['label']}**")
        if st.button("New search", width="stretch"):
            for k in ("selected_area", "selected_restaurant",
                      "workspace_mode", "discovery_borough"):
                st.session_state.pop(k, None)
            st.session_state["stage"] = "landing"
            st.rerun()
        if mode == "site":
            st.session_state["ws_radius"] = st.slider(
                "Site radius (m)", 200, 1500, radius, step=50)
        st.divider()
        ui.eyebrow("Data")
        status = [("DOHMH", True),
                  ("Google", bool(getattr(landscape, "ok", False))),
                  ("ACS", load_acs() is not None),
                  ("PLUTO", lot is not None), ("DOT", ped is not None)]
        for name, up in status:
            st.caption(f"{'●' if up else '○'} {name}")
        st.divider()
        dev_trace = st.checkbox("Developer trace", value=False)

    # ---------------- workspace -----------------------------------------------
    map_col, panel_col = st.columns([0.62, 0.38], gap="medium")
    with map_col:
        render_map_workspace(panel, site, cuisine, landscape, report,
                             mode=mode, top_matches=top_matches)

    with panel_col:
        selected_area = st.session_state.get("selected_area")
        selected_rest = st.session_state.get("selected_restaurant")

        if selected_rest:
            render_restaurant_card(panel, selected_rest, landscape, site)
        elif mode == "site" and site is not None:
            render_site_panel(panel, site, cuisine, price, report, result,
                              landscape, verdicts, fit, band, headline,
                              quality, area_ctx, lot, ped)
        elif selected_area:
            render_area_explorer(panel, selected_area, cuisine)
        elif mode == "discovery" and top_matches is not None:
            ui.eyebrow("Top matches")
            st.markdown(f"### Where {cuisine} shows the best relative fit")
            for i, match in enumerate(top_matches, 1):
                if st.button(
                        f"{i:02d}  {match['name']}  ·  {match['fit']:.0f} · "
                        f"gap {match['gap']}",
                        key=f"top_{match['code']}", width="stretch"):
                    select_area(match["code"])
                    st.rerun()
            st.caption("Relative fit under the same evidence rules — not a "
                       "success ranking. Click a match to explore it.")
        else:
            st.caption("Click an area on the map to explore it.")

    if mode == "site" and site is not None:
        if dev_trace:
            render_trace(site, key, report, result, landscape, ped, lot)
        render_methodology(result, radius)
    else:
        with st.expander("Data & methodology"):
            st.caption("Full methodology is shown in site analysis; sources: "
                       "DOHMH, Google Places, 2024 ACS, PLUTO, NYC DOT, "
                       "NYC Planning geographies.")


if __name__ == "__main__":
    main()
