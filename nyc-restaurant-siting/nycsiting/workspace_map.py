"""
The map workspace: every layer as a plotly figure, built from precomputed
frames. Pure figure assembly — nothing here derives an analytical value, so
the map can never disagree with the panel beside it.

Band colours follow the shell tokens: accent green for favourable, amber for
middling, red for adverse, muted slate for LIMITED EVIDENCE — which is
always drawn hollow/dim so absent evidence can never read as a bad area.
"""
from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go

TOKENS = dict(bg="#07111F", panel="#0D1B2B", line="rgba(255,255,255,0.28)",
              ink="#F5F8FC", muted="#9AAABD", accent="#65E3B0",
              warn="#F1B66C", risk="#E87878", dim="#31445C")

#: CARTO Dark Matter — the basemap this product has always used, restored.
#:
#: WHY THE STYLE URL AND NOT plotly's "carto-darkmatter": plotly's built-in
#: shorthand points at CARTO's LEGACY RASTER host, and CARTO now renders
#: "API KEY REQUIRED / carto.com/basemaps/apikey" into those tile images
#: (verified by fetching a tile). CARTO's current VECTOR style is a
#: different, fully-served endpoint: style JSON, vector tiles, label glyphs
#: and sprites all return 200 with no credential, and being vector rather
#: than raster it cannot carry a baked-in watermark. Same provider, same
#: design, real street and neighbourhood labels, no key.
CARTO_STYLES = {
    True: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    False: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
}


def get_carto_api_key() -> str | None:
    """
    The ONE place a map credential is resolved: Streamlit secrets first,
    then the environment, empty as None. CARTO's public basemaps need no
    key today, so this is normally None; it exists so a paid/authenticated
    tier can be switched on by configuration alone. The key is never
    logged, echoed, or written to the page — and the Google key is never
    used here.
    """
    import os

    secret = None
    try:
        import streamlit as st

        secret = st.secrets.get("CARTO_API_KEY")
    except Exception:
        secret = None
    for candidate in (secret, os.getenv("CARTO_API_KEY")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def basemap_style(dark: bool = True, api_key: str | None = "auto"):
    """
    The basemap: CARTO's vector style URL, with an api_key appended only
    when one is configured. Returned as a plain URL string, which plotly
    hands to Mapbox GL to fetch — that is what pulls in the full 93-layer
    Dark Matter design rather than a flat raster.
    """
    style = CARTO_STYLES[bool(dark)]
    key = get_carto_api_key() if api_key == "auto" else api_key
    return f"{style}?api_key={key}" if key else style


#: The dark product basemap, built once.
BASEMAP = basemap_style(dark=True)

#: layer key -> (title, band -> colour). Limited evidence is always dim.
LAYER_BANDS = {
    "concept_fit": ("Concept fit", {
        "Strong": "#65E3B0", "Promising": "#5AA9E6",
        "Mixed": "#F1B66C", "Limited evidence": "#31445C"}),
    "opportunity_gap": ("Opportunity gap", {
        "High": "#65E3B0", "Moderate": "#5AA9E6", "Low": "#F1B66C",
        "Insufficient evidence": "#31445C"}),
    "turnover": ("Observed turnover", {
        "Lower observed turnover": "#65E3B0", "Typical": "#5AA9E6",
        "Higher observed turnover": "#E87878",
        "Limited evidence": "#31445C"}),
    "evidence": ("Evidence quality", {
        "High": "#65E3B0", "Moderate": "#5AA9E6", "Limited": "#31445C"}),
}

#: Continuous layers: (title, value column source description).
CONTINUOUS_LAYERS = ("cuisine_density", "persistence", "population",
                     "income_context", "median_age")

#: Restaurant marker styling — named constants so tests can pin readability:
#: closest-match is the unmistakably prominent tier (largest, accent mint),
#: similar sits between (light blue), and "other" restaurants must never
#: disappear into the dark basemap (min size 5, min opacity 0.55). The
#: selected site draws above everything.
#: Tuned for the CARTO Dark Matter ground. The light-basemap palette that
#: briefly replaced these read washed-out here: on a dark map the neutral
#: slate "other" tier is legible at 7px/0.62 while staying recessive, and
#: the mint closest tier carries the emphasis. Hierarchy is mint > light
#: blue > neutral slate, reinforced by size (12 / 9 / 7).
MARKER_STYLE = {
    "closest": dict(size=12, color="#65E3B0", opacity=1.0),
    "similar": dict(size=9, color="#8FB4D9", opacity=0.85),
    "other": dict(size=7, color="#6B8098", opacity=0.62),
    "site": dict(size=16, color="#FFFFFF", opacity=1.0),
}


#: Every NTA gets a visible border, whether or not it has a metric.
#:
#: WHY THIS LAYER EXISTS: the boundary used to live on the thematic trace's
#: marker.line, so only the areas WITH a value were drawn at all. The ~60
#: NTAs a layer cannot score (parks, airports, thin-sample areas) were
#: absent from the figure entirely — no fill, no border — which left the
#: scored areas floating as disconnected coloured shapes and made the map
#: look randomly fragmented. Boundaries are now their own layer over the
#: fill, so geography reads independently of any metric.
BOUNDARY_LINE = "rgba(210,225,240,0.45)"
BOUNDARY_WIDTH = 1.2
SELECTED_LINE_WIDTH = 3.5

#: Band shown for areas this layer cannot evaluate. Dim fill, full border.
NOT_EVALUATED = "Not evaluated"
NOT_EVALUATED_COLOR = "#243244"


class DisplayGeometry(NamedTuple):
    """
    How a choropleth gets its shapes, and the areas it must cover.

    `ref` is what plotly receives: either the FeatureCollection itself or a
    URL string that plotly.js fetches (and the browser then caches). `ids`
    is always the full list of area codes, held server-side, so the figure
    can enumerate every NTA — including ones a layer cannot evaluate —
    without reading the geometry back.
    """
    ref: dict | str
    ids: list[str]


def as_geometry(geojson: dict | DisplayGeometry) -> DisplayGeometry:
    """
    Accept either form. A bare FeatureCollection carries its own ids.

    Recognised STRUCTURALLY (has .ref and .ids), not with isinstance: the
    geometry is held in st.cache_resource, which outlives a module reload,
    so after an edit the cached object is an instance of the PREVIOUS
    DisplayGeometry class and an isinstance check fails — which crashed the
    map with "'DisplayGeometry' object has no attribute 'get'". Duck typing
    keeps a live-reloaded session working.
    """
    ref = getattr(geojson, "ref", None)
    ids = getattr(geojson, "ids", None)
    if ref is not None and ids is not None:
        return DisplayGeometry(ref, list(ids))
    ids = [f.get("id") for f in geojson.get("features", []) if f.get("id")]
    return DisplayGeometry(geojson, ids)


def attach_geojson(fig: go.Figure, ref: dict | str,
                   index: int = -1) -> go.Figure:
    """
    Give a choropleth trace its geometry WITHOUT paying for a deep copy.

    Do not "tidy" this back into the trace constructor. Plotly deep-copies
    every property when a trace is attached to a Figure, and this geojson is
    ~1MB of nested lists: passing it to the constructor cost a measured
    155ms per figure — the single largest cost in a rerun — while assigning
    it to the trace that is ALREADY on the figure costs 0.9ms. The resulting
    figure and its JSON are identical either way; only the copy is skipped,
    which is safe because the FeatureCollection is cached, shared and never
    written to.
    """
    if fig.data:
        fig.data[index].geojson = ref
    return fig


def add_nta_boundaries(fig: go.Figure, geojson: dict | DisplayGeometry,
                       codes: list[str] | None = None) -> go.Figure:
    """
    ONE trace carrying every NTA outline: transparent fill, visible line.

    A single choropleth rather than a trace per area (that would undo the
    V5 figure-weight work), and hover is skipped so the thematic trace
    underneath keeps its richer tooltip.
    """
    geometry = as_geometry(geojson)
    ids = [i for i in (codes if codes is not None else geometry.ids) if i]
    if not ids:
        return fig
    fig.add_trace(go.Choroplethmapbox(
        locations=ids, z=[0] * len(ids),
        colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
        showscale=False, showlegend=False, hoverinfo="skip",
        marker=dict(opacity=1.0, line=dict(width=BOUNDARY_WIDTH,
                                           color=BOUNDARY_LINE))))
    return attach_geojson(fig, geometry.ref)


def _base_layout(fig: go.Figure, center=(40.72, -73.97), zoom=9.9,
                 height=640) -> go.Figure:
    # Marker legend top-right, vertical — the thematic band legend owns the
    # bottom-left corner, so the two can never overlap.
    fig.update_layout(
        mapbox=dict(style=BASEMAP, zoom=zoom,
                    center=dict(lat=center[0], lon=center[1])),
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=TOKENS["bg"],
        legend=dict(orientation="v", yanchor="top", y=0.985,
                    xanchor="right", x=0.99, bgcolor="rgba(7,17,31,0.82)",
                    font=dict(color=TOKENS["ink"], size=11)),
        hoverlabel=dict(font_size=12))
    return fig


#: Explains the outlines themselves. Every neighbourhood is drawn on every
#: layer, so an area with no colour still has a boundary — and the reader
#: needs to know that a plain outline means "not measured here", not "no
#: neighbourhood" and certainly not "a bad neighbourhood".
BOUNDARY_NOTE = "Every neighbourhood is outlined; colour shows what was measured"


def _legend_annotation(fig: go.Figure, entries: list[tuple[str, str]],
                       title: str) -> go.Figure:
    """Compact in-map legend, bottom-left — replaces per-trace legends and
    keeps every thematic map self-explanatory."""
    lines = [f"<span style='color:{TOKENS['muted']}'><b>{title}</b></span>"]
    for band, colour in entries:
        dot = "○" if "vidence" in band or "data" in band else "●"
        lines.append(f"<span style='color:{colour}'>{dot}</span> "
                     f"<span style='color:{TOKENS['ink']}'>{band}</span>")
    lines.append(f"<span style='color:{TOKENS['muted']};font-size:10px'>"
                 f"{BOUNDARY_NOTE}</span>")
    fig.add_annotation(
        x=0.012, y=0.02, xref="paper", yref="paper",
        xanchor="left", yanchor="bottom", align="left", showarrow=False,
        text="<br>".join(lines), font=dict(size=11),
        bgcolor="rgba(7,17,31,0.82)", borderpad=6)
    return fig


def band_choropleth(geojson: dict | DisplayGeometry, bands: pd.Series,
                    layer: str,
                    hover: pd.DataFrame | None = None,
                    center=(40.72, -73.97), zoom=9.9,
                    fill_scale: float = 1.0) -> go.Figure:
    """
    ONE trace for all bands (z-indexed discrete colourscale) so the geojson
    is embedded once, not once per band — measured at 4x the figure weight
    otherwise. The legend is an in-map annotation; areas without a usable
    value are drawn dim and their hover says why.

    fill_scale < 1 mutes the polygon fill — used when restaurant markers are
    the point of the view, so points stay readable above the thematic layer.
    """
    title, base_palette = LAYER_BANDS[layer]
    # Areas this layer cannot evaluate get their own dim band rather than
    # being dropped from the figure: they keep a fill, a border and an
    # honest hover instead of vanishing.
    palette = dict(base_palette)
    palette[NOT_EVALUATED] = NOT_EVALUATED_COLOR
    band_names = list(palette)
    # "Limited evidence" / "Insufficient evidence" — and the evidence
    # layer's own band, which is named plain "Limited".
    limited = [b for b in band_names
               if "vidence" in b or b in ("Limited", NOT_EVALUATED)]

    geometry = as_geometry(geojson)
    codes, z, texts = [], [], []
    names = (hover["name"] if hover is not None else pd.Series(dtype=object))
    all_ids = geometry.ids
    scored = {code: band for code, band in bands.items()
              if band in base_palette}
    for code in (all_ids or list(scored)):
        band = scored.get(code, NOT_EVALUATED)
        codes.append(code)
        z.append(band_names.index(band))
        label = names.get(code, code)
        if band == NOT_EVALUATED:
            texts.append(f"<b>{label}</b><br>{title}: not evaluated for "
                         f"this layer")
        elif band in limited:
            texts.append(f"<b>{label}</b><br>{title}: {band}<br>"
                         "Historical sample too small for a reliable "
                         "comparison — not a negative signal.")
        else:
            texts.append(f"<b>{label}</b><br>{title}: {band}")

    n = max(len(band_names) - 1, 1)
    colorscale = []
    for i, band in enumerate(band_names):
        colorscale += [[max(i - 0.001, 0) / n, palette[band]],
                       [i / n, palette[band]]]

    fig = go.Figure()
    if codes:
        # Semi-transparent by design: the basemap's streets and labels have
        # to stay readable THROUGH the thematic fill, and restaurant markers
        # sit above it. fill_scale halves this again when markers are shown.
        # The fill carries NO line — boundaries are their own layer, so a
        # dim fill can never mean a dim border.
        opacity = [(0.18 if band_names[int(v)] in limited else 0.45)
                   * fill_scale for v in z]
        # ONE trace carries every area's fill AND its border. The border's
        # colour has its own alpha, independent of the per-point fill
        # opacity array, so a dim fill still gets a full-strength boundary —
        # and because EVERY area is in this trace, no polygon is missing.
        fig.add_trace(go.Choroplethmapbox(
            locations=codes, z=z, zmin=0, zmax=n,
            colorscale=colorscale, showscale=False,
            marker=dict(opacity=opacity,
                        line=dict(width=BOUNDARY_WIDTH,
                                  color=BOUNDARY_LINE)),
            text=texts, hovertemplate="%{text}<extra></extra>",
            showlegend=False))
        attach_geojson(fig, geometry.ref)
    # An empty series means this figure is a neutral base for another layer
    # (pedestrian sites, ACS fallback) — a legend of zero-counts would only
    # mislead, so it renders without one.
    if len(bands):
        counts = pd.Series(
            [band_names[int(v)] for v in z]).value_counts()
        entries = [(f"{band} ({counts.get(band, 0)})", colour)
                   for band, colour in palette.items()
                   if counts.get(band, 0)]
        _legend_annotation(fig, entries, title)
    return _base_layout(fig, center, zoom)


def continuous_choropleth(geojson: dict | DisplayGeometry, values: pd.Series,
                          title: str, hover_fmt: str = "%{z:,.0f}",
                          center=(40.72, -73.97), zoom=9.9,
                          fill_scale: float = 1.0) -> go.Figure:
    geometry = as_geometry(geojson)
    valid = values.dropna()
    # Every area is in the ONE trace, so every area keeps a boundary. Areas
    # with no value here are painted at token opacity and say so on hover —
    # they are not evaluated for this layer, which is not the same as zero.
    all_ids = geometry.ids or list(valid.index)
    floor = float(valid.min()) if len(valid) else 0.0
    z, opacity, texts = [], [], []
    for code in all_ids:
        if code in valid.index:
            value = float(valid.loc[code])
            z.append(value)
            opacity.append(0.48 * fill_scale)
            texts.append(f"<b>{code}</b><br>{title}: {value:,.0f}")
        else:
            z.append(floor)
            opacity.append(0.05 * fill_scale)
            texts.append(f"<b>{code}</b><br>{title}: not evaluated")
    fig = go.Figure(go.Choroplethmapbox(
        locations=all_ids, z=z,
        colorscale=[[0, "#12314F"], [0.5, "#2E6FAF"], [1, "#65E3B0"]],
        marker=dict(opacity=opacity,
                    line=dict(width=BOUNDARY_WIDTH, color=BOUNDARY_LINE)),
        colorbar=dict(title=dict(text=title, font=dict(color=TOKENS["muted"],
                                                       size=11)),
                      tickfont=dict(color=TOKENS["muted"], size=10),
                      thickness=8, len=0.42, orientation="h",
                      x=0.02, xanchor="left", y=0.03, yanchor="bottom",
                      bgcolor="rgba(7,17,31,0.7)"),
        text=texts, hovertemplate="%{text}<extra></extra>"))
    attach_geojson(fig, geometry.ref)
    # center/zoom MUST reach the layout — dropping them silently pinned
    # every continuous layer to the NYC default and broke fit-on-selection.
    return _base_layout(fig, center, zoom)


def add_site_marker(fig: go.Figure, lat: float, lon: float,
                    label: str = "Selected site") -> go.Figure:
    fig.add_trace(go.Scattermapbox(
        lat=[lat], lon=[lon], mode="markers",
        marker=dict(size=MARKER_STYLE["site"]["size"],
                    color=MARKER_STYLE["site"]["color"]),
        name=TIER_LABELS["site"], showlegend=True,
        hovertemplate=f"<b>{label}</b><br>Selected site<extra></extra>"))
    return fig


def _marker_hover(frame: pd.DataFrame) -> pd.Series:
    """Name · cuisine, address on the second line — never internal IDs."""
    cuisine = frame["cuisine"].replace("", "unspecified")
    names = frame["name"].fillna("Unnamed").astype(str).str.title()
    text = names + " · " + cuisine
    if "address" in frame.columns:
        text = text + "<br>" + frame["address"].fillna("").astype(str)
    return text


def _marker_trace(fig: go.Figure, frame: pd.DataFrame, tier: str,
                  label: str) -> None:
    style = MARKER_STYLE[tier]
    fig.add_trace(go.Scattermapbox(
        lat=frame["lat"], lon=frame["lon"], mode="markers",
        marker=dict(size=style["size"], color=style["color"],
                    opacity=style["opacity"]),
        name=f"{label} ({len(frame)})", showlegend=True,
        customdata=[[f"camis:{c}"] for c in frame["camis"]],
        text=_marker_hover(frame),
        hovertemplate="%{text}<extra></extra>"))


#: Legend wording — deliberately IDENTICAL to the filter control's labels
#: (app.EXACT / SAME_CUISINE). A control that says "Exact concept" beside a
#: legend that says "Closest match" makes the user translate between two
#: vocabularies for the same thing.
TIER_LABELS = {"closest": "Exact concept", "similar": "Same cuisine",
               "other": "Other restaurant", "site": "Selected site"}


def add_radius_ring(fig: go.Figure, lat: float, lon: float,
                    radius_m: float, points: int = 72) -> go.Figure:
    """
    The search radius, drawn as one thin ring.

    Says which restaurants the map is and is not showing, so an empty patch
    beyond the edge reads as "outside the search" rather than "no
    restaurants there". Deliberately quiet — a hairline and a barely-there
    fill — because the district boundary and the markers are the content;
    this is a scale reference. One 72-point trace, so it costs a couple of
    kilobytes and cannot compete with the marker layers for attention.
    """
    if not radius_m:
        return fig
    # metres -> degrees, with longitude corrected for latitude
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    angles = np.linspace(0, 2 * np.pi, points)
    lats = [round(lat + dlat * math.sin(a), 5) for a in angles]
    lons = [round(lon + dlon * math.cos(a), 5) for a in angles]
    fig.add_trace(go.Scattermapbox(
        lat=lats, lon=lons, mode="lines", fill="toself",
        fillcolor="rgba(101,227,176,0.05)",
        line=dict(width=1, color="rgba(101,227,176,0.45)"),
        name=f"{radius_m:,.0f}m search radius", showlegend=True,
        hoverinfo="skip"))
    return fig


def add_restaurant_markers(fig: go.Figure, similar: pd.DataFrame,
                           other: pd.DataFrame | None = None,
                           show_other: bool = True,
                           closest: pd.DataFrame | None = None) -> go.Figure:
    """
    Current establishments as exactly one vectorized trace per tier —
    never one trace per point — drawn dimmest-first so the exact-concept
    tier sits on top. Every present tier carries a legend entry so nobody
    has to infer marker meaning, and camis customdata so clicks select the
    establishment.
    """
    if show_other and other is not None and len(other):
        _marker_trace(fig, other, "other", TIER_LABELS["other"])
    if similar is not None and len(similar):
        _marker_trace(fig, similar, "similar", TIER_LABELS["similar"])
    if closest is not None and len(closest):
        _marker_trace(fig, closest, "closest", TIER_LABELS["closest"])
    return fig


def competitor_markers(fig: go.Figure, competitors: pd.DataFrame,
                       mode: str = "similar") -> go.Figure:
    """
    Google competitors (already deduplicated by place id upstream, closed
    excluded upstream). 'similar' colours by strength; 'all' would need a
    wider fetch — the caller controls what frame arrives here.
    """
    if competitors is None or competitors.empty:
        return fig
    strength_colour = {"Strong": TOKENS["risk"], "Moderate": TOKENS["warn"],
                       "Weak": TOKENS["muted"]}
    for strength, colour in strength_colour.items():
        sub = competitors[competitors["competitor_strength"] == strength]
        if sub.empty:
            continue
        fig.add_trace(go.Scattermapbox(
            lat=sub["latitude"], lon=sub["longitude"], mode="markers",
            marker=dict(size=10, color=colour),
            name=f"{strength} ({len(sub)})",
            text=[f"{r['name']} · "
                  + (f"{r['rating']:.1f} ★ · " if pd.notna(r['rating']) else "")
                  + f"{int(r['reviews'] or 0):,} reviews · "
                    f"{r['competitor_score']:.0f}/100"
                  for _, r in sub.iterrows()],
            hovertemplate="%{text}<extra></extra>"))
    return fig


def legend_for(layer: str) -> list[tuple[str, str]]:
    """(band, colour) pairs for the side legend, in declared order."""
    if layer in LAYER_BANDS:
        return list(LAYER_BANDS[layer][1].items())
    return []
