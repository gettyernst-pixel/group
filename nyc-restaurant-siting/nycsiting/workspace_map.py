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


def _base_layout(fig: go.Figure, center=(40.72, -73.97), zoom=9.9,
                 height=640) -> go.Figure:
    fig.update_layout(
        mapbox=dict(style=BASEMAP, zoom=zoom,
                    center=dict(lat=center[0], lon=center[1])),
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=TOKENS["bg"],
        legend=dict(orientation="h", y=0.01, x=0.01, bgcolor="rgba(7,17,31,0.75)",
                    font=dict(color=TOKENS["ink"], size=11)),
        hoverlabel=dict(font_size=12))
    return fig


def band_choropleth(geojson: dict, bands: pd.Series, layer: str,
                    hover: pd.DataFrame | None = None,
                    center=(40.72, -73.97), zoom=9.9) -> go.Figure:
    """One trace per band so the legend is the band vocabulary itself."""
    title, palette = LAYER_BANDS[layer]
    fig = go.Figure()
    for band, colour in palette.items():
        codes = [c for c, b in bands.items() if b == band]
        if not codes:
            continue
        limited = "vidence" in band          # Limited/Insufficient evidence
        text = codes
        custom = None
        if hover is not None:
            custom = [[hover.loc[c].get("name", c)] +
                      [hover.loc[c].get(k, "") for k in hover.columns
                       if k != "name"]
                      if c in hover.index else [c] for c in codes]
        fig.add_trace(go.Choroplethmapbox(
            geojson=geojson, locations=codes,
            z=[1] * len(codes), showscale=False,
            colorscale=[[0, colour], [1, colour]],
            marker=dict(opacity=0.25 if limited else 0.62,
                        line=dict(width=0.6, color=TOKENS["line"])),
            name=f"{band} ({len(codes)})",
            text=text, hovertemplate="%{text}<br>" + f"{title}: {band}"
                                     + "<extra></extra>",
            customdata=custom, showlegend=True))
    return _base_layout(fig, center, zoom)


def continuous_choropleth(geojson: dict, values: pd.Series, title: str,
                          hover_fmt: str = "%{z:,.0f}",
                          center=(40.72, -73.97), zoom=9.9) -> go.Figure:
    valid = values.dropna()
    fig = go.Figure(go.Choroplethmapbox(
        geojson=geojson, locations=list(valid.index), z=list(valid.values),
        colorscale=[[0, "#12314F"], [0.5, "#2E6FAF"], [1, "#65E3B0"]],
        marker=dict(opacity=0.65, line=dict(width=0.6, color=TOKENS["line"])),
        colorbar=dict(title=dict(text=title, font=dict(color=TOKENS["muted"])),
                      tickfont=dict(color=TOKENS["muted"]), thickness=10,
                      bgcolor="rgba(0,0,0,0)"),
        hovertemplate="%{location}<br>" + title + ": " + hover_fmt
                      + "<extra></extra>"))
    return _base_layout(fig)


def add_site_marker(fig: go.Figure, lat: float, lon: float,
                    label: str = "Selected site") -> go.Figure:
    fig.add_trace(go.Scattermapbox(
        lat=[lat], lon=[lon], mode="markers",
        marker=dict(size=15, color=TOKENS["ink"]),
        name=label, hovertemplate=f"<b>{label}</b><extra></extra>"))
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
