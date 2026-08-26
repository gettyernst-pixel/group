"""
The basemap must be CARTO Dark Matter — and must never fall back to the
watermarked raster endpoint.

HISTORY THIS PINS
plotly's built-in "carto-darkmatter" shorthand points at CARTO's LEGACY
RASTER host, and CARTO now renders "API KEY REQUIRED /
carto.com/basemaps/apikey" into those tile images. Swapping the provider
out for OSM removed the watermark but lost the product's dark design. The
resolution is CARTO's VECTOR style: same provider, same Dark Matter
design, real labels, served without a credential — and, being vector,
incapable of carrying a baked-in watermark.
"""
import json

import pytest

from nycsiting import config, mapview, workspace_map

pytest.importorskip("streamlit")

MAP_MODULES = ("workspace_map.py", "mapview.py")

#: The raster endpoints and plotly shorthands that serve the watermark.
WATERMARKED = ("carto-darkmatter", "carto-positron", "cartodb-basemaps",
               "rastertiles", "dark_all", "light_all", "dark_nolabels",
               "stamen-toner", "stamen-terrain")


def _style(fig) -> str:
    style = fig.layout.mapbox.style
    assert isinstance(style, str), f"expected a style URL, got {style!r}"
    return style


def _assert_good(style: str) -> None:
    assert style.startswith("https://")
    assert "basemaps.cartocdn.com/gl/" in style, style
    assert "-gl-style/style.json" in style, style
    for banned in WATERMARKED:
        assert banned not in style, f"{banned} in {style}"


# ------------------------------------------------------------------ source
def test_no_watermarked_raster_endpoint_is_referenced():
    """Comments may explain the history; executable code may not use it."""
    for name in MAP_MODULES:
        text = (config.APP_DIR / "nycsiting" / name).read_text()
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        for banned in WATERMARKED:
            assert banned not in code, f"{name} still uses {banned}"


def test_dark_style_is_carto_dark_matter_vector():
    _assert_good(workspace_map.basemap_style(dark=True))
    assert "dark-matter" in workspace_map.basemap_style(dark=True)


def test_light_variant_is_the_matching_carto_style():
    light = workspace_map.basemap_style(dark=False)
    _assert_good(light)
    assert "positron" in light
    assert light != workspace_map.basemap_style(dark=True)


def test_basemap_needs_no_credential_by_default():
    """CARTO's public vector basemaps are served without a key; the app
    must not require one to draw a map."""
    style = workspace_map.basemap_style(api_key=None)
    assert "api_key" not in style
    _assert_good(style)


# --------------------------------------------------------------- key path
def test_api_key_is_appended_only_when_configured():
    with_key = workspace_map.basemap_style(api_key="SECRET-VALUE")
    assert with_key.endswith("?api_key=SECRET-VALUE")
    assert workspace_map.basemap_style(api_key=None).count("?") == 0


def test_key_resolution_is_centralised_and_never_uses_the_google_key():
    source = (config.APP_DIR / "nycsiting" / "workspace_map.py").read_text()
    assert "def get_carto_api_key(" in source
    assert "CARTO_API_KEY" in source
    # the Google credential must never reach the map provider
    code = "\n".join(l.split("#", 1)[0] for l in source.splitlines())
    assert "GOOGLE_MAPS_API_KEY" not in code


def test_carto_key_reads_secrets_then_environment(monkeypatch):
    monkeypatch.delenv("CARTO_API_KEY", raising=False)
    assert workspace_map.get_carto_api_key() is None
    monkeypatch.setenv("CARTO_API_KEY", "  env-key  ")
    assert workspace_map.get_carto_api_key() == "env-key"
    monkeypatch.setenv("CARTO_API_KEY", "   ")
    assert workspace_map.get_carto_api_key() is None


def test_secrets_example_documents_the_optional_key():
    example = (config.APP_DIR / ".streamlit"
               / "secrets.toml.example").read_text()
    assert "CARTO_API_KEY" in example
    assert 'CARTO_API_KEY = ""' in example, "must ship empty, never a key"


# ------------------------------------------------------------------ figures
@pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                    reason="processed data not built")
@pytest.mark.parametrize("layer", [
    "concept_fit", "opportunity_gap", "turnover", "persistence", "evidence",
    "cuisine_density", "income_context", "population", "pedestrian"])
def test_every_workspace_layer_uses_the_restored_style(layer):
    import app as app_mod
    panel = app_mod.load_panel()
    fig = app_mod._layer_figure(
        panel, layer, "Italian", app_mod.nta_geojson(),
        app_mod._hover_frame(panel), (40.72, -73.99), 12.0, None, None)
    _assert_good(_style(fig))


@pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                    reason="processed data not built")
def test_no_cuisine_layer_uses_the_restored_style():
    import app as app_mod
    panel = app_mod.load_panel()
    fig = app_mod._layer_figure(
        panel, "persistence", None, app_mod.nta_geojson(),
        app_mod._hover_frame(panel), (40.72, -73.99), 12.0, None, None)
    _assert_good(_style(fig))


@pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                    reason="processed data not built")
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_site_competition_map_uses_the_restored_style(theme):
    import app as app_mod
    from nycsiting import analysis
    panel = app_mod.load_panel()
    locs = app_mod.load_locations()
    site = {"lat": 40.7208, "lon": -73.9934, "label": "195 Bowery"}
    report = analysis.site_report(panel, locs, site["lat"], site["lon"],
                                  "Italian", 500, None)
    fig = mapview.build_map(report["area"]["all"], site, "Italian",
                            set(report["area"]["competitive_set"]), 500,
                            mode="status", theme=theme, locations=None)
    _assert_good(_style(fig))


# ------------------------------------------------- readability & structure
@pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                    reason="processed data not built")
def test_polygons_stay_semi_transparent_so_the_basemap_reads_through():
    import pandas as pd
    import app as app_mod
    geojson = app_mod.nta_geojson()
    bands = pd.Series({"MN0603": "Strong", "MN0303": "Limited evidence"})
    fig = workspace_map.band_choropleth(geojson, bands, "concept_fit")
    opacity = dict(zip(fig.data[0].locations, fig.data[0].marker.opacity))
    assert 0.35 <= opacity["MN0603"] <= 0.50, opacity
    # limited evidence stays dimmer still — absent data must not shout
    assert opacity["MN0303"] < opacity["MN0603"]
    # and markers dim the fill further so streets/points stay readable
    muted = workspace_map.band_choropleth(geojson, bands, "concept_fit",
                                          fill_scale=0.5)
    assert muted.data[0].marker.opacity[0] < opacity["MN0603"]


def test_marker_hierarchy_suits_the_dark_basemap():
    style = workspace_map.MARKER_STYLE
    assert style["closest"]["color"] == "#65E3B0"        # mint, largest
    assert style["closest"]["size"] > style["similar"]["size"]
    assert style["similar"]["size"] > style["other"]["size"]
    assert style["other"]["opacity"] >= 0.55             # clearly visible
    assert style["site"]["color"] == "#FFFFFF"


@pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                    reason="processed data not built")
def test_restore_preserved_the_single_trace_optimisation():
    """Old visuals, current performance architecture."""
    import app as app_mod
    panel = app_mod.load_panel()
    fig = app_mod._layer_figure(
        panel, "concept_fit", "Italian", app_mod.nta_geojson(),
        app_mod._hover_frame(panel), (40.72, -73.99), 12.0, None, None)
    choropleths = [t for t in fig.data if t.type == "choroplethmapbox"]
    assert len(choropleths) == 1
    # the style is a URL, so no tile data is embedded in the figure
    assert len(json.dumps(fig.layout.mapbox.style)) < 200
