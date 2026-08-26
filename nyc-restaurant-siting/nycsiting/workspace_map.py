"""
The map workspace: every layer as a plotly figure, built from precomputed
frames. Pure figure assembly — nothing here derives an analytical value, so
the map can never disagree with the panel beside it.

Band colours follow the shell tokens: accent green for favourable, amber for
middling, red for adverse, muted slate for LIMITED EVIDENCE — which is
always drawn hollow/dim so absent evidence can never read as a bad area.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

TOKENS = dict(bg="#07111F", panel="#0D1B2B", line="rgba(255,255,255,0.28)",
              ink="#F5F8FC", muted="#9AAABD", accent="#65E3B0",
              warn="#F1B66C", risk="#E87878", dim="#31445C")

BASEMAP = "carto-darkmatter"

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
MARKER_STYLE = {
    "closest": dict(size=12, color="#65E3B0", opacity=1.0),
    "similar": dict(size=9, color="#8FB4D9", opacity=0.8),
    "other": dict(size=7, color="#6B8098", opacity=0.62),
    "site": dict(size=16, color="#FFFFFF", opacity=1.0),
}


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


def _legend_annotation(fig: go.Figure, entries: list[tuple[str, str]],
                       title: str) -> go.Figure:
    """Compact in-map legend, bottom-left — replaces per-trace legends and
    keeps every thematic map self-explanatory."""
    lines = [f"<span style='color:{TOKENS['muted']}'><b>{title}</b></span>"]
    for band, colour in entries:
        dot = "○" if "vidence" in band or "data" in band else "●"
        lines.append(f"<span style='color:{colour}'>{dot}</span> "
                     f"<span style='color:{TOKENS['ink']}'>{band}</span>")
    fig.add_annotation(
        x=0.012, y=0.02, xref="paper", yref="paper",
        xanchor="left", yanchor="bottom", align="left", showarrow=False,
        text="<br>".join(lines), font=dict(size=11),
        bgcolor="rgba(7,17,31,0.82)", borderpad=6)
    return fig


def band_choropleth(geojson: dict, bands: pd.Series, layer: str,
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
    title, palette = LAYER_BANDS[layer]
    band_names = list(palette)
    # "Limited evidence" / "Insufficient evidence" — and the evidence
    # layer's own band, which is named plain "Limited".
    limited = [b for b in band_names if "vidence" in b or b == "Limited"]

    codes, z, texts = [], [], []
    names = (hover["name"] if hover is not None else pd.Series(dtype=object))
    for code, band in bands.items():
        if band not in palette:
            continue
        codes.append(code)
        z.append(band_names.index(band))
        label = names.get(code, code)
        if band in limited:
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
        opacity = [(0.22 if band_names[int(v)] in limited else 0.62)
                   * fill_scale for v in z]
        fig.add_trace(go.Choroplethmapbox(
            geojson=geojson, locations=codes, z=z, zmin=0, zmax=n,
            colorscale=colorscale, showscale=False,
            marker=dict(opacity=opacity,
                        line=dict(width=0.6, color=TOKENS["line"])),
            text=texts, hovertemplate="%{text}<extra></extra>",
            showlegend=False))
    # An empty series means this figure is a neutral base for another layer
    # (pedestrian sites, ACS fallback) — a legend of zero-counts would only
    # mislead, so it renders without one.
    if len(bands):
        counts = bands.value_counts()
        entries = [(f"{band} ({counts.get(band, 0)})", colour)
                   for band, colour in palette.items()]
        _legend_annotation(fig, entries, title)
    return _base_layout(fig, center, zoom)


def continuous_choropleth(geojson: dict, values: pd.Series, title: str,
                          hover_fmt: str = "%{z:,.0f}",
                          center=(40.72, -73.97), zoom=9.9,
                          fill_scale: float = 1.0) -> go.Figure:
    valid = values.dropna()
    fig = go.Figure(go.Choroplethmapbox(
        geojson=geojson, locations=list(valid.index), z=list(valid.values),
        colorscale=[[0, "#12314F"], [0.5, "#2E6FAF"], [1, "#65E3B0"]],
        marker=dict(opacity=0.65 * fill_scale,
                    line=dict(width=0.6, color=TOKENS["line"])),
        colorbar=dict(title=dict(text=title, font=dict(color=TOKENS["muted"],
                                                       size=11)),
                      tickfont=dict(color=TOKENS["muted"], size=10),
                      thickness=8, len=0.42, orientation="h",
                      x=0.02, xanchor="left", y=0.03, yanchor="bottom",
                      bgcolor="rgba(7,17,31,0.7)"),
        hovertemplate="%{location}<br>" + title + ": " + hover_fmt
                      + "<extra></extra>"))
    # center/zoom MUST reach the layout — dropping them silently pinned
    # every continuous layer to the NYC default and broke fit-on-selection.
    return _base_layout(fig, center, zoom)


def add_site_marker(fig: go.Figure, lat: float, lon: float,
                    label: str = "Selected site") -> go.Figure:
    fig.add_trace(go.Scattermapbox(
        lat=[lat], lon=[lon], mode="markers",
        marker=dict(size=MARKER_STYLE["site"]["size"],
                    color=MARKER_STYLE["site"]["color"]),
        name="Selected site", showlegend=True,
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


def add_restaurant_markers(fig: go.Figure, similar: pd.DataFrame,
                           other: pd.DataFrame | None = None,
                           show_other: bool = True,
                           closest: pd.DataFrame | None = None) -> go.Figure:
    """
    Current establishments as exactly one vectorized trace per tier —
    "Closest match", "Similar concept", "Other restaurant" — never one
    trace per point, drawn dimmest-first so the closest tier sits on top.
    Every present tier carries a legend entry so nobody has to infer marker
    meaning, and camis customdata so clicks select the establishment.
    """
    if show_other and other is not None and len(other):
        _marker_trace(fig, other, "other", "Other restaurant")
    if similar is not None and len(similar):
        _marker_trace(fig, similar, "similar", "Similar concept")
    if closest is not None and len(closest):
        _marker_trace(fig, closest, "closest", "Closest match")
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
