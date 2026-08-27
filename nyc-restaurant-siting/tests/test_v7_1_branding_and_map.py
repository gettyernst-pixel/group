"""
V7.1: the chair mark and loader, the compact restaurant filter, and the
area-selection/auto-zoom pipeline.

THE MAP REGRESSION THIS PINS
The fit used to be driven by plotly's uirevision keyed on the area id alone
(`area:MN0603`). Re-selecting the SAME area after the user panned produced
an identical revision string, so plotly kept the stale viewport and the map
never moved — "clicking an area doesn't zoom". Selection now bumps a token
that is part of the revision, so every deliberate selection refits exactly
once while ordinary reruns leave the user's view alone.
"""
import json
import re

import pytest

from nycsiting import branding, config, plan_parser

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not config.RESTAURANTS_PQ.exists(),
    reason="processed data not built; run `python build_data.py`")

APP = str(config.APP_DIR / "app.py")
APP_SRC = (config.APP_DIR / "app.py").read_text()
CSS = (config.APP_DIR / "assets" / "styles.css").read_text()

FIVE_AREAS = ["MN0303", "MN0201", "BK0102", "QN0101", "MN0602"]


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except Exception:
        return default


def area_app(cuisine="Italian", neighborhood="Murray Hill") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=900)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(
            cuisine=cuisine, neighborhood=neighborhood, borough="Manhattan"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    at = next(b for b in at.button if "Analyze" in b.label).click().run()
    assert not at.exception, at.exception
    return at


# ------------------------------------------------------------------- logo
def test_logo_exists_in_repository():
    assert branding.LOGO_PATH.exists(), branding.LOGO_PATH
    assert branding.LOGO_PATH.name == "noun-chair-8459396.png"
    assert branding.LOGO_PATH.parent.name == "assets"


def test_logo_path_is_project_relative_not_absolute():
    """It must resolve from the module, so it works from any cwd and on
    Streamlit Cloud where no developer filesystem exists."""
    source = (config.APP_DIR / "nycsiting" / "branding.py").read_text()
    code = "\n".join(l.split("#", 1)[0] for l in source.splitlines())
    assert "/Users/" not in code
    assert "PROJECT_ROOT" in code and "Path(__file__)" in code


def test_no_absolute_mac_path_anywhere_in_runtime_source():
    for path in [config.APP_DIR / "app.py"] + sorted(
            (config.APP_DIR / "nycsiting").glob("*.py")):
        code = "\n".join(l.split("#", 1)[0]
                         for l in path.read_text().splitlines())
        assert "/Users/gettyernst" not in code, path.name


def test_logo_renders_as_embedded_data_uri():
    uri = branding.logo_data_uri()
    assert uri.startswith("data:image/png;base64,")
    assert "file://" not in uri and "localhost" not in uri
    tag = branding.logo_img(height=30)
    assert 'height="30"' in tag and tag.startswith("<img")


def test_logo_appears_on_landing_and_in_workspace():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    landing = " ".join(m.value for m in at.markdown)
    assert "data:image/png;base64," in landing, "no chair on the landing page"
    at2 = area_app()
    workspace = " ".join(m.value for m in at2.markdown)
    assert "jx-brand" in workspace and "data:image/png;base64," in workspace


# ----------------------------------------------------------------- loader
def test_loader_has_chair_animation_and_status_text():
    html = branding.loader_html("Loading restaurants…")
    assert "data:image/png;base64," in html
    assert 'role="status"' in html and 'aria-live' in html
    assert "Loading restaurants" in html
    assert "@keyframes jx-float" in html
    # gentle motion only: a small translate, no spin, ~1.1-1.4s
    assert "translateY(-5px)" in html
    duration = float(re.search(r"jx-float (\d+\.?\d*)s", html).group(1))
    assert 1.1 <= duration <= 1.4
    assert "rotate" not in html.lower()
    assert "prefers-reduced-motion" in html


def test_loader_is_skipped_for_already_warm_work():
    """A cached repeat must not flash a loader for one frame."""
    script = f"""
import sys
sys.path.insert(0, {str(config.APP_DIR)!r})
import streamlit as st
from nycsiting import branding
st.write("cold1:", branding.is_cold("k"))
st.write("cold2:", branding.is_cold("k"))
"""
    at = AppTest.from_string(script)
    at.run()
    # st.write renders values in backticks; compare on a stripped copy
    body = " ".join(str(m.value) for m in at.markdown).replace("`", "")
    assert "cold1: True" in body
    assert "cold2: False" in body


def test_loader_used_for_slow_operations_only():
    for message in ("Loading restaurants…", "Analyzing your location…",
                    "Checking nearby competition…",
                    "Updating concept analysis…"):
        assert message in APP_SRC, message
    # and never for instant UI: Method / tab switches take no loader
    method_block = APP_SRC.split("def render_method_page")[1][:2000]
    assert "chair_spinner" not in method_block


# ------------------------------------------------------- compact filter
def test_restaurant_filter_is_a_compact_segmented_control():
    assert "st.segmented_control(" in APP_SRC
    filter_block = APP_SRC.split('comp_options = ')[1][:1200]
    assert "st.radio(" not in filter_block, "still a stacked radio group"
    assert '"Restaurants"' in filter_block


def test_filter_renders_horizontally_at_matching_height():
    assert '[data-testid="stSegmentedControl"] button' in CSS
    block = CSS.split('[data-testid="stSegmentedControl"] button')[1][:400]
    height = int(re.search(r"min-height:\s*(\d+)px", block).group(1))
    assert 40 <= height <= 44, height
    assert "flex-wrap: nowrap" in CSS


def test_filter_control_present_and_options_intact():
    at = area_app()
    controls = getattr(at, "segmented_control", [])
    assert len(controls) == 1, "expected exactly one segmented control"
    assert list(controls[0].options) == ["Exact concept", "Same cuisine",
                                        "All restaurants"]
    assert not at.radio, "no radio groups should remain in the toolbar"


# ------------------------------------------------- area selection & zoom
def test_select_area_is_the_single_canonical_entry_point():
    """Every selection route must go through select_area with a source."""
    calls = re.findall(r"select_area\((?!code: str)([^)]*)\)", APP_SRC)
    routes = [c for c in calls if "source=" in c]
    assert len(routes) >= 4, calls
    for expected in ("polygon_click", "prompt", "back_to_explore",
                     "top_match"):
        assert expected in APP_SRC, expected


def test_selection_bumps_the_fit_token_every_time():
    """The regression fix: re-selecting the SAME area must still refit."""
    script = f"""
import sys
sys.path.insert(0, {str(config.APP_DIR)!r})
import streamlit as st
import app
app.select_area("MN0603", source="test")
first = st.session_state["area_fit_token"]
app.select_area("MN0603", source="test")       # same area again
second = st.session_state["area_fit_token"]
app.select_area("NOT_A_REAL_AREA", source="test")
st.write("first:", first)
st.write("second:", second)
st.write("area:", st.session_state["selected_area"])
"""
    at = AppTest.from_string(script)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown).replace("`", "")
    assert "first: 1" in body and "second: 2" in body
    assert "area: MN0603" in body, "an invalid code must not be selected"


@pytest.mark.parametrize("code", FIVE_AREAS)
def test_every_area_selection_fits_and_shows_restaurants(code):
    import app as app_mod
    at = area_app()
    token = ss(at, "area_fit_token")
    at.session_state["selected_area"] = code
    at.session_state["area_fit_token"] = token + 1
    at = at.run()
    assert not at.exception, at.exception
    # the viewport was fitted to THIS selection event
    assert ss(at, "last_fitted_view") == f"area:{code}:{token + 1}"
    assert ss(at, "last_fitted_area") == code
    # and restaurants are present without any further interaction
    tiers = app_mod.area_tiers_cached(app_mod.load_panel(), code,
                                      "Italian", None)
    total = (len(tiers["closest"]) + len(tiers["similar"])
             + len(tiers["other"]))
    assert total > 0
    assert tiers["total"] == total


def test_prompt_selected_area_autofits_without_a_click():
    at = area_app()
    assert ss(at, "selected_area") == "MN0603"
    assert ss(at, "area_fit_source") == "prompt"
    assert ss(at, "last_fitted_view") == "area:MN0603:1"


def test_filter_change_preserves_the_viewport():
    """Flipping Closest/Similar/All must not refit or reset the area."""
    at = area_app()
    before_view = ss(at, "last_fitted_view")
    before_token = ss(at, "area_fit_token")
    for choice in ("All", "Closest", "Similar"):
        at.session_state["ws_comp"] = choice
        at = at.run()
        assert not at.exception, at.exception
        assert ss(at, "selected_area") == "MN0603"
        assert ss(at, "area_fit_token") == before_token, choice
        assert ss(at, "last_fitted_view") == before_view, choice


def test_second_area_refits_after_manual_pan():
    """Viewport persistence must never block a NEW selection."""
    at = area_app()
    first_view = ss(at, "last_fitted_view")
    token = ss(at, "area_fit_token")
    at.session_state["selected_area"] = "BK0102"
    at.session_state["area_fit_token"] = token + 1
    at = at.run()
    assert ss(at, "last_fitted_view") != first_view
    assert ss(at, "last_fitted_view") == f"area:BK0102:{token + 1}"


def test_fit_bounds_centre_the_area():
    """Centring is checked here; the full two-axis framing contract is
    pinned by test_whole_district_fits_with_context_on_every_side."""
    import app as app_mod
    for code in FIVE_AREAS:
        min_lat, max_lat, min_lon, max_lon = app_mod.polygon_bounds(code)
        center, zoom = app_mod.zoom_for_bounds(
            (min_lat, max_lat, min_lon, max_lon))
        assert min_lat <= center[0] <= max_lat
        assert min_lon <= center[1] <= max_lon
        assert 9.5 <= zoom <= 16.0


# ------------------------------------------------------ no hidden work
def test_area_interaction_calls_neither_claude_nor_google():
    """Area exploration must run on local DOHMH data alone."""
    import app as app_mod
    src = APP_SRC.split("def area_restaurant_tiers")[1][:2500]
    for banned in ("parse_plan", "anthropic", "competitors_cached",
                   "fetch_landscape", "geocode"):
        assert banned not in src, banned
    panel = app_mod.load_panel()
    tiers = app_mod.area_tiers_cached(panel, "MN0603", "Italian", None)
    assert tiers["total"] > 0        # markers come from the local panel


def test_markers_are_one_trace_per_group_and_unique_per_camis():
    import app as app_mod
    from nycsiting import workspace_map
    import plotly.graph_objects as go
    panel = app_mod.load_panel()
    tiers = app_mod.area_tiers_cached(panel, "MN0603", "Italian", None)
    fig = go.Figure()
    workspace_map.add_restaurant_markers(
        fig, tiers["similar"], tiers["other"], closest=tiers["closest"])
    assert len(fig.data) <= 3, "one vectorized trace per tier, never per row"
    for frame in (tiers["closest"], tiers["similar"], tiers["other"]):
        assert frame["camis"].is_unique, "one marker per establishment"


# ============================================================ v7.2 area fit
FIT_AREAS = {"MN0303": "East Village", "MN0603": "Murray Hill-Kips Bay",
             "MN0602": "Gramercy", "MN0401": "Chelsea-Hudson Yards",
             "MN0701": "Upper West Side-Lincoln Square"}


@pytest.mark.parametrize("code,name", sorted(FIT_AREAS.items()))
def test_whole_district_fits_with_context_on_every_side(code, name):
    """
    The regression this pins: the fit constrained WIDTH only, so tall
    districts filled 94% of the map's height and their boundaries sat
    flush against the edge. Both axes must fit, with padding.
    """
    import math
    import app as app_mod
    min_lat, max_lat, min_lon, max_lon = app_mod.polygon_bounds(code)
    for mode, (pane_w, pane_h) in app_mod.MAP_PANE_PX.items():
        center, zoom = app_mod.zoom_for_bounds(
            (min_lat, max_lat, min_lon, max_lon),
            viewport_px=pane_w, viewport_h_px=pane_h)
        # 512px tiles: CARTO's vector style, same constant the fit uses
        deg_per_px = 360.0 / (app_mod.BASEMAP_TILE_PX * 2 ** zoom)
        lat_scale = math.cos(math.radians(center[0]))
        width_fill = (max_lon - min_lon) / (deg_per_px * pane_w)
        height_fill = ((max_lat - min_lat) / lat_scale) / (deg_per_px
                                                           * pane_h)
        # nothing clipped on either axis...
        assert width_fill < 1.0, (name, mode, "clipped horizontally")
        assert height_fill < 1.0, (name, mode, "clipped vertically")
        # ...and the binding axis lands in the 65-80% target, so there is
        # visible surrounding geography rather than a flush edge
        assert 0.60 <= max(width_fill, height_fill) <= 0.80, (
            name, mode, width_fill, height_fill)


def test_site_and_area_use_different_framing():
    """AREA fits the whole district from its polygon; SITE keeps a closer
    street-level view; DISCOVERY stays a city overview."""
    block = APP_SRC.split("if selected_area:")[1].split("st.session_state[")[0]
    assert "zoom_for_bounds(polygon_bounds(selected_area)" in block
    assert "site_zoom(site)" in block, "site mode has its own framing rule"
    assert "9.9" in block, "discovery should keep the city overview"


def test_site_framing_shows_the_radius_and_the_nearest_district_edge():
    """
    v8.1: a site view has two jobs — show the competitors inside the search
    radius, and say which neighbourhood the address is in.

    A flat zoom 13.6 did neither reliably (a large district fell entirely
    outside the frame). Fitting the district's own bbox failed for an
    address near an edge, because that fit assumes the map is centred on
    the district's centre while a site map is centred on the ADDRESS.
    Mirroring the bbox around the site fixed the framing but shrank the
    competitor dots to specks whenever the address sat at the edge of a
    large district.

    So the rule is the union of the two things that must be visible: the
    whole search radius, and the NEAREST boundary — which is all it takes
    to answer "which neighbourhood is this?".
    """
    import math

    import app as app_mod

    assert app_mod.SITE_MAX_ZOOM == 13.6, "the close framing is unchanged"
    idx = app_mod.nta_index()
    pane_w, pane_h = app_mod.MAP_PANE_PX["assess"]

    for code in ("MN0303", "MN0602", "SI0107", "QN0101", "BK0101"):
        x0, y0, x1, y1 = idx.features[code]["bbox"]
        # a site at the district's WEST edge, the case that used to fail
        for lat, lon, where in (((y0 + y1) / 2, (x0 + x1) / 2, "centre"),
                                ((y0 + y1) / 2, x0 + (x1 - x0) * 0.02,
                                 "west edge")):
            site = {"lat": lat, "lon": lon, "label": code}
            for radius in (350, 500):
                zoom = app_mod.site_zoom(site, radius_m=radius)
                assert zoom <= app_mod.SITE_MAX_ZOOM, (code, where)
                scale = app_mod.BASEMAP_TILE_PX * (2 ** zoom) / 360.0
                cos_lat = math.cos(math.radians(lat))

                # the whole search radius is inside the pane
                ring_h = 2 * (radius / 111_320.0) * scale / cos_lat
                ring_w = 2 * (radius / 111_320.0) * scale
                assert ring_h <= pane_h and ring_w <= pane_w, (
                    code, where, radius, "the search radius is cropped")

                # and at least one district edge is in frame
                half_lon_view = (pane_w / 2) / scale
                half_lat_view = (pane_h / 2) / scale * cos_lat
                near_lon = min(abs(lon - x0), abs(x1 - lon))
                near_lat = min(abs(lat - y0), abs(y1 - lat))
                assert (near_lon <= half_lon_view
                        or near_lat <= half_lat_view), (
                    code, where, radius,
                    "no district boundary visible from this address")


def test_site_mode_draws_the_containing_district_boundary():
    """The address marker alone never says which district it sits in."""
    block = APP_SRC.split("# --- containing district, in SITE mode")[1]
    block = block.split("# --- selection emphasis")[0]
    assert "site is not None and not selected_area" in block
    assert "nta_index().locate(" in block
    assert "SELECTED_LINE_WIDTH" in block


# ======================================================= v7.2 filter labels
def test_filter_labels_are_self_explanatory():
    import app as app_mod
    assert app_mod.EXACT == "Exact concept"
    assert app_mod.SAME_CUISINE == "Same cuisine"
    assert app_mod.ALL_RESTAURANTS == "All restaurants"
    for gone in ('"Closest"', '"Similar"', '"All"'):
        assert gone not in APP_SRC.split("comp_options =")[1][:600], gone


def test_legend_uses_the_same_words_as_the_control():
    import app as app_mod
    from nycsiting.workspace_map import TIER_LABELS
    assert TIER_LABELS["closest"] == app_mod.EXACT
    assert TIER_LABELS["similar"] == app_mod.SAME_CUISINE
    assert TIER_LABELS["site"] == "Selected site"


def test_control_adapts_when_no_cuisine_was_given():
    at = area_app(cuisine=None, neighborhood="Gramercy")
    options = list(at.segmented_control[0].options)
    assert options == ["Exact concept", "All restaurants"]
    assert "Same cuisine" not in options, "nothing to compare a cuisine to"


def test_control_offers_all_three_tiers_with_a_cuisine():
    at = area_app()
    assert list(at.segmented_control[0].options) == [
        "Exact concept", "Same cuisine", "All restaurants"]


def test_microcopy_explains_the_selected_tier():
    import app as app_mod
    caption = app_mod.filter_caption
    assert caption("All restaurants", "Italian", "brunch spot", False) == \
        "All current restaurant establishments in this area."
    assert caption("Same cuisine", "Italian", "brunch spot", False) == \
        "All Italian restaurants in this area."
    exact = caption("Exact concept", "Italian", "brunch spot", False)
    assert "Italian" in exact and "brunch" in exact
    limited = caption("Exact concept", "Italian", "brunch spot", True)
    assert "Limited data" in limited and "do not reliably classify" in limited


def test_filter_help_tooltip_defines_every_tier():
    import app as app_mod
    for term in ("Exact concept", "Same cuisine", "All restaurants"):
        assert term in app_mod.FILTER_HELP
    assert 'help=FILTER_HELP' in APP_SRC


def test_changing_the_filter_does_not_refit_the_map():
    """Only marker visibility may change — never the viewport."""
    at = area_app()
    before_view = ss(at, "last_fitted_view")
    before_token = ss(at, "area_fit_token")
    for choice in ("All restaurants", "Exact concept", "Same cuisine"):
        at.session_state["ws_comp"] = choice
        at = at.run()
        assert not at.exception, at.exception
        assert ss(at, "area_fit_token") == before_token, choice
        assert ss(at, "last_fitted_view") == before_view, choice
        assert ss(at, "selected_area") == "MN0603", choice
