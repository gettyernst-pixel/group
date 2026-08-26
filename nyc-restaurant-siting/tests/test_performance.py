"""
The performance work, pinned as behaviour rather than as timings.

WHAT THIS FILE EXISTS TO PREVENT
Three changes made the workspace roughly five times faster, and each one is
easy to undo by accident while "tidying":

1. The reference frames are cached with st.cache_resource, not cache_data.
   cache_data deep-copies its return value on every hit, so merely ASKING
   for the lots frame cost 355-466ms per rerun. cache_resource is only safe
   while nothing mutates the frames, so that invariant is tested too — if a
   future change writes into a shared frame, a test fails here instead of
   corrupting every later session in the process.

2. The map geometry is handed to plotly AFTER the trace is on the figure.
   Passing it to the constructor makes plotly deep-copy ~1MB of nested
   lists: 155ms versus 0.9ms, for an identical figure.

3. The geometry is published once as a static file and referenced by URL,
   so it stops riding the websocket with every figure. The fallback to an
   embedded FeatureCollection must keep working, because a URL that cannot
   be fetched would render an empty map with no error at all.

Timings are deliberately NOT asserted — they belong to the machine, not to
the code. What is asserted is the structure that produced them.
"""
from __future__ import annotations

import hashlib
import json
import pickle

import pandas as pd
import pytest

from nycsiting import branding, config, geometry, workspace_map

pytest.importorskip("streamlit")

NEEDS_DATA = pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                                reason="processed data not built")


def _fingerprint(df: pd.DataFrame) -> tuple:
    """
    Content identity: shape, columns, and a hash of the serialised values.

    Pickle rather than pd.util.hash_pandas_object — some columns hold
    arrays, which that helper cannot hash.
    """
    return (df.shape, tuple(df.columns),
            hashlib.sha256(pickle.dumps(df)).hexdigest())


# ----------------------------------------------------- 1. no deep copies
@NEEDS_DATA
@pytest.mark.parametrize("loader", ["load_panel", "load_locations",
                                    "load_lots", "load_pedestrian",
                                    "nta_geojson", "panel_with_nta_cached"])
def test_reference_data_is_not_copied_on_every_call(loader):
    """Two calls must return the SAME object, not equal copies."""
    import app as app_mod

    fn = getattr(app_mod, loader)
    first = fn(app_mod.load_panel()) if loader == "panel_with_nta_cached" \
        else fn()
    second = fn(app_mod.load_panel()) if loader == "panel_with_nta_cached" \
        else fn()
    assert first is second, (
        f"{loader} returned a copy — it is probably back on st.cache_data, "
        "which deep-copies on every cache hit")


@NEEDS_DATA
def test_shared_frames_are_never_written_to():
    """
    The safety net that makes cache_resource correct.

    Runs a realistic interaction sequence and checks the shared frames come
    out byte-identical. If anything in the request path assigns into them,
    this fails — which is the whole reason sharing them is allowed.
    """
    from streamlit.testing.v1 import AppTest

    from nycsiting import plan_parser

    import app as app_mod

    frames = {name: getattr(app_mod, name)()
              for name in ("load_panel", "load_locations", "load_lots",
                           "load_pedestrian")}
    before = {name: _fingerprint(df) for name, df in frames.items()}

    at = AppTest.from_file(str(config.APP_DIR / "app.py"), default_timeout=300)
    for key, value in dict(
            stage="results", plan_confirmed=True, cuisine="Italian",
            ws_concept="Italian", workspace_mode="area",
            workspace_view="explore", selected_area="MN0603",
            area_fit_token=1,
            confirmed_plan=plan_parser.RestaurantPlan(
                cuisine="Italian", concept="brunch spot",
                borough="Manhattan")).items():
        at.session_state[key] = value
    at.run()
    for area, token in (("MN0303", 2), ("MN0401", 3)):
        at.session_state["selected_area"] = area
        at.session_state["area_fit_token"] = token
        at.run()
    at.session_state["ws_concept"] = "Thai"
    at.run()

    for name, df in frames.items():
        assert _fingerprint(df) == before[name], (
            f"{name} was mutated during a normal interaction — sharing it "
            "via cache_resource is no longer safe")


# ------------------------------------------- 2. geometry attached, not copied
def test_attach_geojson_does_not_copy_the_geometry():
    import plotly.graph_objects as go

    payload = {"type": "FeatureCollection",
               "features": [{"type": "Feature", "id": "MN0101",
                             "properties": {},
                             "geometry": {"type": "MultiPolygon",
                                          "coordinates": []}}]}
    fig = go.Figure()
    fig.add_trace(go.Choroplethmapbox(locations=["MN0101"], z=[1]))
    workspace_map.attach_geojson(fig, payload)
    assert fig.data[0].geojson is payload, (
        "the geometry was copied — pass it to attach_geojson AFTER the trace "
        "is on the figure, never to the trace constructor")


@NEEDS_DATA
def test_layer_figures_never_embed_geometry_in_the_constructor():
    """Every choropleth builder must go through attach_geojson."""
    source = (config.APP_DIR / "nycsiting" / "workspace_map.py").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    assert "geojson=geojson" not in code, (
        "a trace constructor is being handed the geometry again")


# ------------------------------------------------ 3. published by reference
@NEEDS_DATA
def test_geometry_is_published_and_referenced_by_url():
    import app as app_mod

    geom = app_mod.nta_display_geometry()
    assert isinstance(geom.ref, str), "geometry should be referenced by URL"
    assert geom.ref.startswith("app/static/nta_display."), geom.ref
    assert geom.ref.endswith(".geojson")
    assert len(geom.ids) == len(app_mod.nta_geojson()["features"])

    published = app_mod.STATIC_DIR / geom.ref.split("/")[-1]
    assert published.exists(), "the URL must point at a file just written"
    payload = json.loads(published.read_text())
    assert len(payload["features"]) == len(geom.ids)
    # no half-written files left behind
    assert not list(app_mod.STATIC_DIR.glob("*.partial"))


@NEEDS_DATA
def test_published_filename_changes_with_the_content():
    """A browser caches the file, so different bytes must mean a new URL."""
    import app as app_mod

    one = app_mod.nta_display_geometry().ref
    assert one == app_mod.nta_display_geometry().ref, "must be stable"
    digest = one.split(".")[-2]
    assert len(digest) == 12 and all(c in "0123456789abcdef" for c in digest)


@NEEDS_DATA
def test_falls_back_to_embedded_geometry_when_it_cannot_publish(monkeypatch):
    """
    An unfetchable URL would draw an EMPTY map and report nothing, so a
    failed publish must degrade to embedding the geometry, never to a URL.
    """
    import app as app_mod

    monkeypatch.setattr(app_mod.STATIC_DIR.__class__, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("read-only file system")))
    app_mod.nta_display_geometry.clear()
    try:
        geom = app_mod.nta_display_geometry()
        assert isinstance(geom.ref, dict), "must embed, not return a URL"
        assert len(geom.ref["features"]) == len(geom.ids) == 262
    finally:
        app_mod.nta_display_geometry.clear()


def test_geometry_reference_survives_a_module_reload():
    """
    st.cache_resource outlives module reloads, so a cached DisplayGeometry
    can be an instance of a PREVIOUS class object. as_geometry must accept
    it structurally — isinstance crashed the map with
    "'DisplayGeometry' object has no attribute 'get'".
    """
    import importlib

    stale = workspace_map.DisplayGeometry("app/static/x.geojson", ["MN0101"])
    reloaded = importlib.reload(workspace_map)
    try:
        resolved = reloaded.as_geometry(stale)
        assert resolved.ref == "app/static/x.geojson"
        assert resolved.ids == ["MN0101"]
    finally:
        importlib.reload(workspace_map)


# -------------------------------------------------- display-only rounding
def test_display_geometry_is_rounded_but_analysis_geometry_is_not():
    index = geometry.NTAIndex.__new__(geometry.NTAIndex)
    ring = [(-73.991234567890, 40.712345678901), (-73.981111111111, 40.72),
            (-73.97, 40.73), (-73.991234567890, 40.712345678901)]
    index.features = {"XX0000": dict(name="Test", borough="Manhattan",
                                     residential=True, polygons=[[ring]],
                                     bbox=(-74, 40.7, -73.9, 40.8))}
    coords = index.to_geojson()["features"][0]["geometry"]["coordinates"]
    for lon, lat in coords[0][0]:
        assert round(lon, geometry.DISPLAY_DECIMALS) == lon
        assert round(lat, geometry.DISPLAY_DECIMALS) == lat
    # the source rings the analysis uses keep full precision
    assert index.features["XX0000"]["polygons"][0][0][0][0] == -73.991234567890


@NEEDS_DATA
def test_display_rounding_does_not_move_any_boundary_perceptibly():
    """5 decimals is ~1.1m: far below one screen pixel at any zoom used."""
    import app as app_mod

    index = app_mod.nta_index()
    published = app_mod.nta_geojson()
    by_id = {f["id"]: f for f in published["features"]}
    for code in list(index.features)[:20]:
        source_pt = index.features[code]["polygons"][0][0][0]
        shown_pt = by_id[code]["geometry"]["coordinates"][0][0][0]
        assert abs(shown_pt[0] - source_pt[0]) <= 1e-5
        assert abs(shown_pt[1] - source_pt[1]) <= 1e-5


# ------------------------------------------------------- loading affordances
def test_every_loading_state_uses_the_chair():
    """
    One loading affordance, not two. The app's own loader was branded but
    st.spinner and the cached functions' show_spinner text rendered
    Streamlit's default, so which loader appeared depended on which code
    path was slow.
    """
    css = branding.spinner_css()
    assert '[data-testid="stSpinnerIcon"]' in css
    assert "data:image/png;base64," in css, "the chair must be embedded"
    assert "border: none !important" in css, "the default arc must be hidden"
    assert "prefers-reduced-motion" in css


def test_the_map_has_a_branded_ground_while_it_rebuilds():
    """
    st.plotly_chart's identity includes the figure, so any real change
    remounts the chart and the container is briefly bare — the "pulsing".
    The ground turns that window into a deliberate loading panel.
    """
    css = branding.map_ground_css("ws_map", height=640)
    assert ".st-key-ws_map" in css, "must target the map's own container"
    assert "min-height: 640px" in css, "the column must not collapse"
    assert "data:image/png;base64," in css


def test_styles_are_injected_before_the_first_slow_call():
    """The panel load is the longest wait in the product; its spinner must
    already be branded when it runs."""
    source = (config.APP_DIR / "app.py").read_text()
    body = source[source.index("def main("):]
    assert body.index("ui.inject_styles()") < body.index("panel = load_panel()")
