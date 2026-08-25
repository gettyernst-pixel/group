"""
The map layer.

COLOUR CHOICES ARE MEASURED, NOT PICKED BY EYE
A map is a scatter plot, so every pair of series can end up adjacent on screen
and the palette has to hold under the all-pairs test, not just neighbouring
ones. Every palette below was run through the data-viz validator in both light
and dark mode before being written down; the numbers in each comment are that
tool's output, not an impression.

The one that matters most: "still trading" versus "gone" is the obvious place to
reach for a green/red status pair, and that pair FAILS — deuteranopia separation
ΔE 4.1, less than a sixth of the ≥8 target, meaning a red-green colourblind
reader sees one colour. Categorical blue/orange measures ΔE 24.7 for the same
distinction. It is also the more honest encoding: a closed restaurant is a fact
about history, not a severity, and painting it "critical" editorialises.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# --- validated palettes -----------------------------------------------------
# Categorical, all-pairs: light worst CVD ΔE 9.2, normal-vision 24.0;
#                         dark  worst CVD ΔE 9.4, normal-vision 20.9. Both PASS.
# Ordinal blue ramp: monotone lightness, adjacent ΔL ≥ 0.06, light end clears
#                    the surface in both modes. Both PASS.
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "muted": "#898781",
        "categorical": ["#2a78d6", "#eb6834", "#1baf7a"],
        "ordinal": ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"],
        "basemap": "carto-positron",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "muted": "#898781",
        "categorical": ["#3987e5", "#d95926", "#199e70"],
        "ordinal": ["#b7d3f6", "#86b6ef", "#3987e5", "#1c5cab"],
        "basemap": "carto-darkmatter",
    },
}

#: In light mode the third categorical slot sits at 2.74:1 against the surface,
#: below the 3:1 bar. The validator calls that a WARN, not a pass, and the
#: obligation it carries is "relief": the same information must be reachable
#: without relying on the colour. The table under the map is that relief, so it
#: ships with the map rather than being optional.
RELIEF_REQUIRED = True

MODES = {
    "status": "Still trading vs closed",
    "concept": "Fit with your concept",
    "turnover": "Turnover at each address",
}


def theme_for(name: str) -> dict:
    return THEMES.get(name, THEMES["dark"])


def _circle(lat: float, lon: float, radius_m: float, points: int = 90):
    """Points tracing the comparison radius, so the boundary is visible."""
    angles = np.linspace(0, 2 * np.pi, points)
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(np.cos(np.radians(lat)), 1e-6))
    return (lat + dlat * np.sin(angles)).tolist(), (lon + dlon * np.cos(angles)).tolist()


def _hover(df: pd.DataFrame) -> tuple[list, str]:
    """Customdata plus a template. Every mark names its own group in words."""
    def fmt(value):
        return "—" if pd.isna(value) else str(pd.Timestamp(value).date())

    custom = list(zip(
        df["cuisine"].replace("", "unspecified").fillna("unspecified"),
        [fmt(v) for v in df["first_observed"]],
        [fmt(v) for v in df["last_observed"]],
        df["_group"],
        df["distance_m"].round().astype(int),
    ))
    template = (
        "<b>%{text}</b><br>%{customdata[0]}"
        "<br>observed %{customdata[1]} → %{customdata[2]}"
        "<br>%{customdata[3]} · %{customdata[4]}m away"
        "<extra></extra>"
    )
    return custom, template


def _assign_groups(area: pd.DataFrame, mode: str, cuisine: str,
                   competitive: set[str], locations: pd.DataFrame | None) -> pd.DataFrame:
    """
    Label every restaurant with the group the chosen mode paints it by.

    Group order is fixed and returned as an ordered category, because colour
    must follow the entity rather than its rank: filtering the map to a smaller
    radius must not repaint the groups that remain.
    """
    df = area.copy()

    if mode == "status":
        df["_group"] = np.where(df["seen_2026"],
                                "Still trading (2026)", "Gone since 2017")
        order = ["Still trading (2026)", "Gone since 2017"]

    elif mode == "concept":
        df["_group"] = np.select(
            [df["cuisine"] == cuisine, df["cuisine"].isin(competitive)],
            [f"{cuisine} (your concept)", "Competing concept"],
            default="Other food business")
        order = [f"{cuisine} (your concept)", "Competing concept",
                 "Other food business"]

    elif mode == "turnover":
        # How many restaurants that storefront has been through. A count, so an
        # ordered ramp rather than unrelated hues.
        if locations is None:
            counts = pd.Series(1, index=df.index)
        else:
            lookup = locations.set_index("location_key")["restaurants_ever"]
            counts = df["location_key"].map(lookup).fillna(1)
        df["_group"] = pd.cut(
            counts, bins=[0, 1, 2, 3, np.inf],
            labels=["1 restaurant", "2 restaurants", "3 restaurants",
                    "4 or more"]).astype(str)
        order = ["1 restaurant", "2 restaurants", "3 restaurants", "4 or more"]
    else:
        raise ValueError(f"unknown map mode: {mode}")

    df["_group"] = pd.Categorical(df["_group"], categories=order, ordered=True)
    return df


def build_map(area: pd.DataFrame, site: dict, cuisine: str,
              competitive: set[str], radius_m: float, mode: str = "status",
              theme: str = "dark", locations: pd.DataFrame | None = None) -> go.Figure:
    """
    One map, coloured by whichever question the reader picked.

    Closed restaurants are drawn, not filtered out. They are the reason the 2017
    archive is worth having: a block where half the marks are 'gone since 2017'
    is the single most legible thing this dataset can show, and a map of only
    the survivors would hide it.
    """
    t = theme_for(theme)
    palette = t["ordinal"] if mode == "turnover" else t["categorical"]

    placed = area[area["lat"].notna() & area["lon"].notna()].copy()
    # A handful of DOHMH records carry no DBA at all; "None" on a hover card
    # looks like a bug rather than missing data.
    placed["name"] = placed["name"].fillna("(unnamed)").replace("", "(unnamed)")
    df = _assign_groups(placed, mode, cuisine, competitive, locations)

    fig = go.Figure()

    # Comparison boundary, drawn first so marks sit on top of it.
    ring_lat, ring_lon = _circle(site["lat"], site["lon"], radius_m)
    fig.add_trace(go.Scattermapbox(
        lat=ring_lat, lon=ring_lon, mode="lines",
        line=dict(width=1, color=t["muted"]),
        hoverinfo="skip", showlegend=False, name="radius"))

    for i, group in enumerate(df["_group"].cat.categories):
        sub = df[df["_group"] == group]
        if sub.empty:
            continue
        custom, template = _hover(sub)
        fig.add_trace(go.Scattermapbox(
            lat=sub["lat"], lon=sub["lon"], mode="markers",
            marker=dict(size=9, color=palette[i % len(palette)],
                        # Closed restaurants read as slightly faded — a second,
                        # non-colour cue that matches what the group means.
                        opacity=0.65 if "Gone" in str(group) else 0.9),
            name=f"{group} ({len(sub)})",
            text=sub["name"], customdata=custom, hovertemplate=template))

    # The site itself, in ink rather than a fourth hue, so it never reads as
    # another category.
    fig.add_trace(go.Scattermapbox(
        lat=[site["lat"]], lon=[site["lon"]], mode="markers",
        marker=dict(size=17, color=t["ink"]),
        name="Your site", text=["Your site"],
        hovertemplate="<b>Your site</b><extra></extra>"))

    # The legend sits BELOW the map on the paper, not floating over it: a
    # legend overlaid on a street map either obscures marks or needs a panel
    # that does. Mapbox fills the whole paper, so the bottom margin is what
    # creates the strip it lives on — with b=0 the legend is simply clipped.
    fig.update_layout(
        mapbox=dict(style=t["basemap"], zoom=_zoom_for(radius_m),
                    center=dict(lat=site["lat"], lon=site["lon"])),
        height=480, margin=dict(l=0, r=0, t=0, b=48),
        paper_bgcolor=t["surface"],
        legend=dict(orientation="h", y=0, yanchor="top", x=0, xanchor="left",
                    bgcolor="rgba(0,0,0,0)", itemsizing="constant",
                    font=dict(color=t["ink"], size=12)),
        hoverlabel=dict(font_size=12))
    return fig


def _zoom_for(radius_m: float) -> float:
    """Keep the whole comparison circle in frame across the radius slider."""
    return float(np.clip(15.6 - np.log2(radius_m / 200.0), 11.5, 15.6))


def map_table(area: pd.DataFrame, mode: str, cuisine: str,
              competitive: set[str], locations: pd.DataFrame | None = None,
              limit: int = 250) -> pd.DataFrame:
    """
    The map's data as a table.

    Not a nicety: in light mode one of the three categorical slots sits below
    3:1 against the surface, and the validator's WARN there obligates relief —
    the reader must be able to get the same answer without depending on the
    colour. This is that path, and it also serves anyone reading on a phone in
    sunlight.
    """
    placed = area[area["lat"].notna() & area["lon"].notna()].copy()
    placed["name"] = placed["name"].fillna("(unnamed)").replace("", "(unnamed)")
    df = _assign_groups(placed, mode, cuisine, competitive, locations)
    out = pd.DataFrame({
        "Restaurant": df["name"],
        "Cuisine": df["cuisine"].replace("", "unspecified"),
        "Group": df["_group"].astype(str),
        "Distance (m)": df["distance_m"].round().astype(int),
        "Observed from": pd.to_datetime(df["first_observed"]).dt.date.astype(str),
        "Observed to": pd.to_datetime(df["last_observed"]).dt.date.astype(str),
    })
    return out.sort_values("Distance (m)").head(limit).replace("NaT", "—")
