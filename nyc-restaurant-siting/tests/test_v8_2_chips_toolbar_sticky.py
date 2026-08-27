"""
v8.2: removable plan chips, a single-row toolbar, no paragraphs above the
map, and a sticky map beside a scrolling analysis.

WHY EACH EXISTS

1. CHIPS. The plan was visible but frozen: a user who typed an address
   could not drop it without starting a whole new plan through the prompt,
   which discarded the cuisine, the concept and every preference with it.

2. TOOLBAR. st.segmented_control renders as [data-testid="stButtonGroup"]
   in this Streamlit version, NOT "stSegmentedControl". Every alignment
   rule written against the old name matched nothing, so the group stayed
   display:block and its three options stacked into a 132px tower beside
   two 68px dropdowns — measured in the browser. The toolbar also sat
   inside the ~532px map column, too narrow for four controls, so
   Streamlit wrapped Concept onto a different row from Layer.

3. ABOVE THE MAP. Three captions stacked between the controls and the map.

4. STICKY. The map scrolled away, leaving the left half of a long analysis
   as blank dark page.
"""
from __future__ import annotations

import pytest

from nycsiting import config

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from nycsiting import plan_parser  # noqa: E402

APP = str(config.APP_DIR / "app.py")
APP_SRC = (config.APP_DIR / "app.py").read_text()
UI_SRC = (config.APP_DIR / "nycsiting" / "ui.py").read_text()
CSS = (config.APP_DIR / "assets" / "styles.css").read_text()

NEEDS_DATA = pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                                reason="processed data not built")


def workspace(plan: plan_parser.RestaurantPlan, **extra) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    state = dict(stage="results", plan_confirmed=True,
                 cuisine=plan.cuisine, ws_concept=plan.cuisine or "Any",
                 workspace_view="assess", confirmed_plan=plan)
    state.update(extra)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


# ======================================================= 1. plan chips
@NEEDS_DATA
def test_every_stated_constraint_gets_a_removable_chip():
    import app as app_mod

    plan = plan_parser.RestaurantPlan(
        cuisine="Italian", concept="brunch spot", address="64 Wooster Street",
        average_spend=70.0, foot_traffic_preference="high",
        competition_tolerance="low")
    chips = app_mod.plan_chip_values(plan, site_label="64 Wooster Street")
    fields = {c["field"] for c in chips}
    for expected in ("cuisine", "concept", "location", "average_spend",
                     "foot_traffic_preference", "competition_tolerance"):
        assert expected in fields, expected
    # a chip with nothing to remove carries no field, so it renders no ×
    placeholder = app_mod.plan_chip_values(
        plan_parser.RestaurantPlan(concept="brunch spot"))
    assert any(c["field"] is None for c in placeholder)


@NEEDS_DATA
def test_removing_the_address_keeps_the_plan_and_routes_to_discovery():
    """§3/§4/§15: the plan survives; only the location goes."""
    import app as app_mod

    plan = plan_parser.RestaurantPlan(
        cuisine="Chinese", concept="brunch spot", address="64 Wooster Street",
        foot_traffic_preference="high", competition_tolerance="low")
    at = workspace(plan, workspace_mode="site", address="64 Wooster Street")
    at.session_state["confirmed_plan"] = plan

    chip = next(b for b in at.button if "64 Wooster Street" in str(b.label))
    at = chip.click().run()

    after = at.session_state["confirmed_plan"]
    assert after.address is None
    assert after.cuisine == "Chinese", "cuisine must survive"
    assert after.concept == "brunch spot", "concept must survive"
    assert after.foot_traffic_preference == "high", "preferences must survive"
    assert after.competition_tolerance == "low"
    assert at.session_state["workspace_mode"] == "discovery"
    assert at.session_state["stage"] == "results", "never back to the prompt"


@NEEDS_DATA
def test_removing_the_area_routes_to_discovery_for_the_same_cuisine():
    import app as app_mod

    plan = plan_parser.RestaurantPlan(cuisine="Italian",
                                      neighborhood="East Village")
    at = workspace(plan, workspace_mode="area", selected_area="MN0303",
                   area_fit_token=1)
    chip = next(b for b in at.button
                if "East Village" in str(b.label) and "✕" in str(b.label))
    at = chip.click().run()
    after = at.session_state["confirmed_plan"]
    assert after.neighborhood is None and after.cuisine == "Italian"
    assert at.session_state["workspace_mode"] == "discovery"


@NEEDS_DATA
def test_removing_the_cuisine_never_falls_back_to_another_one():
    """§6/§17: no substitute cuisine, and never the alphabetically first."""
    import app as app_mod

    plan = plan_parser.RestaurantPlan(cuisine="Italian",
                                      concept="brunch spot",
                                      neighborhood="Gramercy")
    at = workspace(plan, workspace_mode="area", selected_area="MN0602",
                   area_fit_token=1)
    chip = next(b for b in at.button
                if str(b.label).startswith("Italian"))
    at = chip.click().run()

    after = at.session_state["confirmed_plan"]
    assert after.cuisine is None
    assert after.concept == "brunch spot", "the concept survives"
    assert after.neighborhood == "Gramercy", "the area survives"
    assert at.session_state["cuisine"] is None
    assert at.session_state["ws_concept"] == app_mod.CUISINE_ANY
    assert at.session_state["ws_concept"] != "Afghan"


@NEEDS_DATA
def test_removing_the_concept_broadens_but_keeps_cuisine_and_area():
    plan = plan_parser.RestaurantPlan(cuisine="Italian",
                                      concept="brunch spot",
                                      neighborhood="Gramercy")
    at = workspace(plan, workspace_mode="area", selected_area="MN0602",
                   area_fit_token=1)
    chip = next(b for b in at.button
                if str(b.label).startswith("Brunch Spot"))
    at = chip.click().run()
    after = at.session_state["confirmed_plan"]
    assert after.concept is None
    assert after.cuisine == "Italian" and after.neighborhood == "Gramercy"


def test_chip_removal_never_calls_claude():
    """§9: the plan is already parsed; editing it is an edit."""
    src = APP_SRC[APP_SRC.index("def remove_plan_constraint("):]
    src = src[:src.index("\ndef route_plan(")]
    for forbidden in ("parse_plan_cached", "plan_parser.parse", "anthropic",
                      "get_anthropic_api_key"):
        assert forbidden not in src, forbidden
    route = APP_SRC[APP_SRC.index("def route_plan("):]
    route = route[:route.index("\ndef ", 5)]
    for forbidden in ("parse_plan_cached", "stage"):
        assert forbidden not in route, f"routing must not touch {forbidden}"


@NEEDS_DATA
def test_removing_a_chip_leaves_saved_comparisons_alone():
    """§70: the editable plan and the saved comparison are separate."""
    import app as app_mod

    plan = plan_parser.RestaurantPlan(cuisine="Italian",
                                      address="64 Wooster Street")
    at = workspace(plan, workspace_mode="site", address="64 Wooster Street")
    at.session_state[app_mod.COMPARISON_KEY] = [
        {"kind": "area", "id": "area:MN0602", "area_code": "MN0602",
         "display_name": "Gramercy", "address": None}]
    at = at.run()
    chip = next(b for b in at.button if "64 Wooster Street" in str(b.label))
    at = chip.click().run()
    assert len(at.session_state[app_mod.COMPARISON_KEY]) == 1, \
        "a plan edit must not silently drop saved comparison entries"


def test_one_canonical_removal_function():
    assert APP_SRC.count("def remove_plan_constraint(") == 1
    assert "on_remove=remove_plan_constraint" in APP_SRC
    assert "def plan_chips(values: list, on_remove=None)" in UI_SRC


# ========================================================== 2. toolbar
def test_the_toolbar_is_rendered_at_full_width_above_the_panes():
    """Four controls do not fit in the map column; the row spans the page."""
    body = APP_SRC[APP_SRC.index("# ---------------- workspace ---"):]
    body = body[:body.index("with panel_col:")]
    assert body.index("render_workspace_toolbar(") < body.index("st.columns(")
    # and the map renderer no longer builds the controls
    mapfn = APP_SRC[APP_SRC.index("def render_map_workspace("):]
    mapfn = mapfn[:mapfn.index("\ndef ", 5)]
    for widget in ('st.selectbox("Concept"', 'st.segmented_control(',
                   'st.slider("Radius'):
        assert widget not in mapfn, f"{widget} still lives in the map column"


def test_the_segmented_control_targets_the_real_testid():
    """
    The bug the previous attempt missed: this Streamlit version renders
    st.segmented_control as stButtonGroup. Rules written against
    "stSegmentedControl" matched nothing at all.
    """
    block = CSS[CSS.index("v8.2: workspace toolbar"):]
    assert '[data-testid="stButtonGroup"]' in block
    assert "flex-wrap: nowrap !important" in block
    assert "height: 40px !important" in block, \
        "must equal a selectbox control exactly"


def test_the_selected_segment_cannot_change_the_box_size():
    """§25: colour only — an added border would shift its neighbours."""
    block = CSS[CSS.index("v8.2: workspace toolbar"):]
    active = block[block.index('button[aria-checked="true"]'):]
    active = active[:active.index("}")]
    for shifting in ("padding", "height", "margin", "border-width"):
        assert shifting not in active, f"selected state changes {shifting}"
    assert "box-shadow: inset" in active, "use an inset ring, not a border"


def test_restaurants_gets_the_widest_toolbar_column():
    """§23: it stacked partly because its column was too narrow."""
    src = APP_SRC[APP_SRC.index("def render_workspace_toolbar("):]
    src = src[:src.index("return layer_label, comp_mode")]
    site_row = src[src.index('if mode == "site":'):]
    ratios = site_row[site_row.index("st.columns([") + 12:]
    ratios = [float(x) for x in ratios[:ratios.index("]")].split(",")]
    assert ratios[2] == max(ratios), "Restaurants must be the widest column"
    assert ratios[2] / sum(ratios) >= 0.30, ratios


# ============================================ 3. nothing above the map
@NEEDS_DATA
def test_no_explanatory_paragraph_renders_above_the_map():
    site_block = APP_SRC[APP_SRC.index("# --- local competitive environment"):]
    site_block = site_block[:site_block.index("# --- selection emphasis")]
    assert "st.caption(" not in site_block
    assert "record_filter_explanation(" in site_block


def test_the_explanations_are_kept_not_deleted():
    """§30: moved to the analysis pane and the tooltip, not discarded."""
    recorder = APP_SRC[APP_SRC.index("def record_filter_explanation("):]
    recorder = recorder[:recorder.index("\ndef ", 5)]
    assert "filter_caption(" in recorder
    assert 'tiers.get("note")' in recorder
    assert "comparable competitors the competition read" in recorder
    assert "def render_filter_explanation(" in APP_SRC
    assert "render_filter_explanation()" in APP_SRC
    # and the tooltip still explains the three tiers
    assert "FILTER_HELP" in APP_SRC
    assert "help=FILTER_HELP" in APP_SRC


# ============================================================ 4. sticky
def test_the_map_column_is_sticky_and_below_the_loading_overlay():
    from nycsiting import branding

    block = CSS[CSS.index("v8.2: sticky map"):]
    assert ".st-key-sticky_map" in block
    assert "position: sticky" in block
    # The geometry that actually makes sticky work, each measured in the
    # browser when it was wrong:
    #  - the COLUMN must stretch (shrink-wrapped to the map, the sticky
    #    element had no track and ran to -1212px)
    #  - the layout wrapper Streamlit inserts must stretch too (it sat at
    #    640px around a 640px child: a zero-height track)
    #  - the sticky element itself must NOT stretch (at 2032px it never
    #    moved while the map inside it scrolled out of view)
    assert "align-self: stretch !important" in block
    assert '[data-testid="stLayoutWrapper"]:has(> .st-key-sticky_map)' in block
    assert "flex: 0 0 auto !important" in block
    z = int(block.split("z-index:")[1].split(";")[0].strip())
    assert z < branding.OVERLAY_Z, "the loader must still cover the map"
    assert "@media (max-width: 900px)" in block, "narrow screens stack"
    assert "position: static" in block


def test_the_map_is_wrapped_in_the_sticky_container():
    body = APP_SRC[APP_SRC.index("# ---------------- workspace ---"):]
    body = body[:body.index("with panel_col:")]
    assert 'st.container(key="sticky_map")' in body
    assert body.index('st.container(key="sticky_map")') < \
        body.index("render_map_workspace(")


def test_sticky_is_css_only_with_no_scroll_listener():
    """§53/§54/§73: scrolling must cause zero Streamlit reruns."""
    for forbidden in ("addEventListener('scroll'", 'addEventListener("scroll"',
                      "onscroll", "IntersectionObserver", "setInterval"):
        assert forbidden not in APP_SRC, forbidden
        assert forbidden not in UI_SRC, forbidden


@NEEDS_DATA
def test_the_analysis_stays_inside_the_right_pane():
    """§47/§48/§49: nothing renders full-width beneath the two columns."""
    body = APP_SRC[APP_SRC.index("with panel_col:"):]
    body = body[:body.index("\ndef ", 5)] if "\ndef " in body else body
    for section in ("render_site_panel(", "render_area_explorer(",
                    "render_compare_tray(", "render_workspace_search(",
                    "render_add_to_comparison("):
        assert section in body, f"{section} must render inside the right pane"
