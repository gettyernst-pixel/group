"""
v8: the loading overlay, the comparison store, site-area context, the
single report control, and the button-layout system.

WHAT THESE PIN, AND WHY IT BROKE BEFORE

1. LOADING. Streamlit streams its output, so a slow run paints a half-built
   page. Reproduced in the browser 600ms into an address analysis, the
   screen held an empty map container, a cache spinner reading "RANKING
   CONCEPTS FOR THIS AREA…", and two orphaned methodology expander headers
   ("What does this mean?", "How each signal was judged") with no content
   under them. There was no overlay anywhere in the product: every loading
   affordance was an in-flow element that could only push content down,
   never hide it.

2. COMPARISON. Two stores existed. `comparison_area_ids` held NTA codes and
   was fed by an "Add to comparison" button rendered inside
   render_area_explorer — one branch of a mutually-exclusive panel chain,
   so the action was simply absent in site mode and in discovery. A second,
   older store (`saved`) sat behind "Save & compare another location" and
   set stage="landing", throwing the user back to the plan prompt.

3. REPORT. "Export report" and "Download PDF report" could both be on
   screen at once, and the stored PDF was not keyed to the comparison it
   described.
"""
from __future__ import annotations

import pytest

from nycsiting import branding, comparison, config

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from nycsiting import plan_parser  # noqa: E402

APP = str(config.APP_DIR / "app.py")
APP_SRC = (config.APP_DIR / "app.py").read_text()
CSS = (config.APP_DIR / "assets" / "styles.css").read_text()

NEEDS_DATA = pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                                reason="processed data not built")


def workspace(area: str = "MN0602", cuisine: str | None = "Italian",
              **extra) -> AppTest:
    """A warm workspace on an area, without going through the prompt."""
    at = AppTest.from_file(APP, default_timeout=300)
    state = dict(stage="results", plan_confirmed=True, cuisine=cuisine,
                 ws_concept=cuisine, workspace_mode="area",
                 workspace_view="assess", selected_area=area,
                 area_fit_token=1,
                 confirmed_plan=plan_parser.RestaurantPlan(
                     cuisine=cuisine, concept="brunch spot",
                     borough="Manhattan"))
    state.update(extra)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def _code_only(block: str) -> str:
    """Strip docstrings and comments — a handler is allowed to EXPLAIN the
    stage transition it removed without being accused of making one."""
    import re

    block = re.sub(r'"""..*?"""', "", block, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in block.splitlines())


def _area(code: str) -> dict:
    return {"kind": "area", "id": f"area:{code}", "area_code": code,
            "display_name": code, "address": None}


# ============================================================ 1. loading
def test_the_overlay_covers_the_viewport_and_blurs_what_is_behind():
    css = branding.overlay_css()
    assert "position: fixed" in css
    assert "inset: 0" in css
    assert "backdrop-filter" in css, "the background must be blurred"
    assert "-webkit-backdrop-filter" in css, "Safari needs the prefix"
    # centred on both axes
    assert "align-items: center" in css
    assert "justify-content: center" in css
    # and readable even where backdrop-filter is unsupported
    assert "@supports not" in css
    assert "prefers-reduced-motion" in css


def test_the_overlay_sits_above_everything_the_app_draws():
    """Measured in the browser: Streamlit's own header is z-index 999990."""
    assert branding.OVERLAY_Z < 999990, \
        "stay below Streamlit's chrome so its controls remain reachable"
    assert branding.OVERLAY_Z > 1000, "but above anything the app renders"
    assert f"z-index: {branding.OVERLAY_Z}" in branding.overlay_css()


def test_the_overlay_carries_the_chair_and_one_message():
    html = branding.overlay_html("Analyzing East Village…")
    assert "jx-overlay" in html
    assert "data:image/png;base64," in html, "the chair, embedded"
    assert html.count("class=\"msg\"") == 1, "exactly one message"
    assert "Analyzing East Village…" in html
    assert 'role="status"' in html and 'aria-busy="true"' in html


def test_the_loader_is_raised_before_any_analysis_runs():
    """
    The overlay has to be emitted BEFORE the work, or Streamlit paints the
    partial page first and the overlay arrives too late to hide it.
    """
    body = APP_SRC[APP_SRC.index("def main("):]
    body = body[:body.index("def _main_body(")]
    assert "global_loader" in body
    assert body.index("ui.inject_styles()") < body.index("global_loader")
    # nothing analytical happens in main() before the overlay
    for forbidden in ("load_panel()", "geocode_cached", "site_report",
                      "concept_fit_cached"):
        assert forbidden not in body, (
            f"{forbidden} runs before the overlay is raised")


def test_only_declared_work_raises_the_loader():
    """
    A loader on work that finishes instantly is a flash, not feedback, so
    cheap interactions must not call request_loading.
    """
    assert "def request_loading(" in APP_SRC
    for handler, should_load in (("def _set_view(", False),
                                 ("def remove_from_comparison(", False),
                                 ("def clear_comparison(", False),
                                 ("def select_area(", True),
                                 ("def _request_report(", True)):
        start = APP_SRC.index(handler)
        block = APP_SRC[start:APP_SRC.index("\ndef ", start + 5)]
        assert ("request_loading(" in block) is should_load, handler


def _loader_script(body: str) -> str:
    return f"""
import sys
sys.path.insert(0, {str(config.APP_DIR)!r})
import streamlit as st
from nycsiting import branding

st.session_state.setdefault(branding.LOADING_KEY, "Analyzing your plan…")
with branding.global_loader(st.session_state.get(branding.LOADING_KEY)):
    {body}
st.write("key_after:", branding.LOADING_KEY in st.session_state)
"""


def test_the_overlay_stays_up_across_a_rerun_and_comes_down_after():
    """
    The two halves of the contract.

    A Streamlit transition normally spans two runs (mutate, then rerun). If
    the overlay came down when the first run ended, the half-built page
    would flash between them — which is the bug. So it is removed ONLY when
    a run finishes normally, and left standing when one is interrupted.
    """
    # interrupted run: the overlay must still be on screen
    at = AppTest.from_string(_loader_script("st.stop()"))
    at.run()
    assert any('class="jx-overlay"' in m.value for m in at.markdown), \
        "an interrupted run must leave the overlay covering the page"
    assert "Analyzing your plan…" in " ".join(m.value for m in at.markdown)

    # completed run: the overlay is gone and the message is spent
    at = AppTest.from_string(_loader_script('st.write("done")'))
    at.run()
    assert not any('class="jx-overlay"' in m.value for m in at.markdown), \
        "a completed run must take the overlay down"
    # st.write renders values in backticks, so compare on a stripped copy
    body = " ".join(str(m.value) for m in at.markdown).replace("`", "")
    assert "key_after: False" in body, body


def test_an_error_takes_the_overlay_down_instead_of_hiding_it():
    """
    The one case where keeping the overlay up would be actively harmful.

    If the body raises, Streamlit renders a traceback. Leaving a
    full-screen blur with a chair on it over that traceback gives the user
    a frozen app instead of a message — and because the pending message
    would still be set, the next run would raise the overlay again over
    the same error. So a real exception dismisses it; rerun and stop, which
    are control flow rather than failure, do not.
    """
    guarded = """
try:
    with branding.global_loader(st.session_state.get(branding.LOADING_KEY)):
        raise ValueError("boom")
except ValueError as exc:
    st.write("raised:", type(exc).__name__)
"""
    at = AppTest.from_string(_loader_script("pass").replace(
        "    pass", "    pass").replace(
        "with branding.global_loader"
        "(st.session_state.get(branding.LOADING_KEY)):\n    pass",
        guarded.strip()))
    at.run()
    body = " ".join(str(m.value) for m in at.markdown).replace("`", "")
    assert "raised: ValueError" in body, body
    assert not any('class="jx-overlay"' in m.value for m in at.markdown), \
        "an error must not be hidden behind the overlay"
    assert "key_after: False" in body, \
        "a spent message would re-raise the overlay over the same error"


@NEEDS_DATA
def test_a_pending_message_does_not_survive_a_completed_run():
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state[branding.LOADING_KEY] = "Analyzing your plan…"
    at = at.run()
    assert branding.LOADING_KEY not in at.session_state, \
        "a spent loading message would raise the overlay on the next run too"


@NEEDS_DATA
def test_a_completed_run_leaves_no_loader_behind():
    at = workspace()
    # the stylesheet always defines .jx-overlay; what must be absent is
    # an overlay ELEMENT left behind by a finished run
    assert not any('class="jx-overlay"' in m.value for m in at.markdown)


# ======================================================= 2. comparison state
@NEEDS_DATA
def test_add_to_comparison_is_available_in_area_and_in_site_mode():
    """
    The heart of "Add to comparison only works sometimes": the control used
    to be rendered by render_area_explorer, one branch of a mutually
    exclusive panel chain, so it did not exist in site mode at all.
    """
    area = workspace()
    assert any("Add to comparison" in str(b.label) for b in area.button), \
        "missing in AREA mode"

    site = workspace(area=None, workspace_mode="site",
                     address="195 Bowery, Manhattan")
    site.session_state["selected_area"] = None
    site = site.run()
    assert any("Add to comparison" in str(b.label) for b in site.button), \
        "missing in SITE mode"


@NEEDS_DATA
def test_the_add_control_is_rendered_by_the_shell_not_by_one_panel():
    """One call site, outside the branch chain, so no state combination can
    make the action disappear."""
    assert APP_SRC.count("render_add_to_comparison(") == 2, \
        "one definition and exactly one call site"
    shell = APP_SRC[APP_SRC.index("        render_compare_tray()"):]
    assert "current_comparison_subject" in shell[:1600]


@NEEDS_DATA
def test_clicking_an_area_while_a_site_is_open_makes_the_area_the_subject():
    """
    THE BLIND SPOT: every other site-mode test forces selected_area=None,
    so nothing exercised "a site is open AND an area is selected" — which
    is the state "← Back to explore" then a polygon click produces.

    In it, workspace_mode stays "site" while the panel shows the clicked
    NEIGHBOURHOOD. The add control used to offer the ADDRESS anyway, so the
    user queued a place they were not looking at; and because the store
    dedupes by area, every neighbourhood clicked afterwards then reported
    "already added" and the comparison could never reach two entries.
    "Open full analysis →" on that neighbourhood opened the site, too.
    """
    import app as app_mod

    at = workspace(area=None, workspace_mode="site",
                   address="6 E 1st Street, Manhattan")
    at.session_state["selected_area"] = None
    at = at.run()

    # back to explore: the site's own area is CONTEXT, the site is subject
    back = next(b for b in at.button if "Back to explore" in str(b.label))
    at = back.click().run()
    assert at.session_state["workspace_mode"] == "site"
    assert at.session_state[app_mod.AREA_IS_SUBJECT] is False

    # now the user explicitly clicks a DIFFERENT neighbourhood
    at.session_state["workspace_view"] = "assess"
    app_state = at.session_state
    at.session_state["selected_area"] = "MN0602"          # Gramercy
    at.session_state[app_mod.AREA_IS_SUBJECT] = True      # what a click sets
    at = at.run()

    body = " ".join(str(m.value) for m in at.markdown)
    assert "Gramercy" in body, "the panel must show the area that was clicked"
    assert "6 EAST 1 STREET" not in body.upper(), \
        "asking for an area's analysis must not open the address"

    add = next(b for b in at.button if "Add to comparison" in str(b.label))
    at = add.click().run()
    entries = at.session_state[app_mod.COMPARISON_KEY]
    assert [e["area_code"] for e in entries] == ["MN0602"], \
        "the add control must queue the area on screen, not the open site"
    assert entries[0]["kind"] == "area"


@NEEDS_DATA
def test_back_to_explore_keeps_the_site_as_the_subject():
    """The other half: an area that is only CONTEXT must not steal the
    panel from the site the user is analysing."""
    import app as app_mod

    at = workspace(area=None, workspace_mode="site",
                   address="6 E 1st Street, Manhattan")
    at.session_state["selected_area"] = None
    at = at.run()
    back = next(b for b in at.button if "Back to explore" in str(b.label))
    at = back.click().run()

    at.session_state["workspace_view"] = "assess"
    at = at.run()
    body = " ".join(str(m.value) for m in at.markdown)
    assert "6 EAST 1 STREET" in body.upper(), \
        "Back-to-explore must leave the site one click away, not replace it"


@NEEDS_DATA
def test_adding_commits_in_one_run_and_never_leaves_the_workspace():
    at = workspace()
    add = next(b for b in at.button if "Add to comparison" in str(b.label))
    at = add.click().run()

    assert at.session_state["stage"] == "results"
    entries = at.session_state[__import__("app").COMPARISON_KEY]
    assert [e["area_code"] for e in entries] == ["MN0602"]
    assert any("Added to comparison" in str(b.label) for b in at.button)


@NEEDS_DATA
def test_adding_twice_never_duplicates():
    import app as app_mod

    at = workspace()
    at.session_state[app_mod.COMPARISON_KEY] = [_area("MN0602")]
    at = at.run()
    added = [b for b in at.button if "Added to comparison" in str(b.label)]
    assert added and added[0].disabled, \
        "an already-added location offers no second add"
    assert len(at.session_state[app_mod.COMPARISON_KEY]) == 1


def test_add_to_comparison_is_atomic_and_idempotent():
    """Pure-function contract, independent of any widget."""
    import app as app_mod
    import streamlit as st

    st.session_state.clear()
    entry = _area("MN0602")
    ok, msg = app_mod.add_to_comparison(entry)
    assert ok and "added" in msg.lower()

    ok, msg = app_mod.add_to_comparison(entry)
    assert not ok and "already" in msg.lower()
    assert len(app_mod.comparison_entries()) == 1

    ok, _ = app_mod.add_to_comparison(None)
    assert not ok, "an unplaceable location is refused, not stored"

    for code in ("MN0603", "MN0401"):
        assert app_mod.add_to_comparison(_area(code))[0]
    ok, msg = app_mod.add_to_comparison(_area("MN0303"))
    assert not ok and str(comparison.MAX_COMPARE_AREAS) in msg
    assert len(app_mod.comparison_entries()) == comparison.MAX_COMPARE_AREAS
    st.session_state.clear()


def test_a_site_and_its_own_area_are_the_same_comparison_unit():
    """
    The engine compares AREAS, so a site is compared through the area that
    contains it. Adding both would compare an area against itself.
    """
    import app as app_mod
    import streamlit as st

    st.session_state.clear()
    site = {"kind": "site", "id": "site:40.72,-73.99", "area_code": "MN0602",
            "display_name": "6 E 1st St", "address": "6 E 1st St",
            "area_name": "Gramercy"}
    assert app_mod.add_to_comparison(site)[0]
    ok, msg = app_mod.add_to_comparison(_area("MN0602"))
    assert not ok
    assert "6 E 1st St" in msg, "say WHICH entry already covers the area"
    st.session_state.clear()


@NEEDS_DATA
def test_comparison_survives_every_view_change():
    import app as app_mod

    at = workspace()
    at.session_state[app_mod.COMPARISON_KEY] = [_area("MN0602"),
                                                _area("MN0603")]
    for view in ("explore", "assess", "method", "explore"):
        at.session_state["workspace_view"] = view
        at = at.run()
        assert len(at.session_state[app_mod.COMPARISON_KEY]) == 2, view


@NEEDS_DATA
def test_only_new_search_clears_the_comparison():
    """New Search is the single intentional reset; no comparison control is
    allowed to double as one."""
    reset_lists = [seg for seg in APP_SRC.split("for key in (")[1:]]
    assert any("COMPARISON_KEY" in seg[:400] for seg in reset_lists), \
        "New Search must clear the comparison"
    # and no comparison handler sets the stage
    for handler in ("def _compare_another(", "def _handle_add(",
                    "def add_to_comparison(", "def _open_comparison("):
        start = APP_SRC.index(handler)
        block = APP_SRC[start:APP_SRC.index("\ndef ", start + 5)]
        code = _code_only(block)
        assert "stage" not in code, f"{handler} must not touch the stage"


# ================================================= 3. site + area context
@NEEDS_DATA
def test_site_analysis_carries_its_area_context():
    at = workspace(area=None, workspace_mode="site",
                   address="195 Bowery, Manhattan")
    at.session_state["selected_area"] = None
    at = at.run()
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Area context" in body or "AREA CONTEXT" in body.upper()
    assert any("View full area analysis" in str(b.label) for b in at.button)


@NEEDS_DATA
def test_site_and_area_scores_are_reported_separately():
    """Two indices, two questions, never averaged into one number."""
    src = APP_SRC[APP_SRC.index("def render_area_context("):]
    src = src[:src.index("\ndef ", 5)]
    assert "never combined" in src
    for blend in ("(site_fit + ", "/ 2", "average", "combined_score",
                  "overall_probability"):
        assert blend not in src, f"site and area must not be blended: {blend}"


@NEEDS_DATA
def test_area_context_reports_unavailable_rather_than_a_midpoint():
    src = APP_SRC[APP_SRC.index("def render_area_context("):]
    src = src[:src.index("\ndef ", 5)]
    assert "if index is None" in src
    assert "not a negative signal" in src


@NEEDS_DATA
def test_view_area_analysis_keeps_the_plan_and_the_concept():
    import app as app_mod

    src = APP_SRC[APP_SRC.index("def _view_containing_area("):]
    src = src[:src.index("\ndef ", 5)]
    assert "select_area(" in src
    assert 'workspace_mode"] = "area"' in src
    for reset in ("confirmed_plan", "cuisine", "plan_outcome",
                  "parse_plan_cached"):
        assert reset not in src, f"must not reset {reset}"
    assert hasattr(app_mod, "_view_containing_area")


# ======================================================= 4. report control
@NEEDS_DATA
def test_only_one_report_control_is_ever_on_screen():
    src = APP_SRC[APP_SRC.index("def render_report_control("):]
    src = src[:src.index("\ndef ", 5)]
    # the export button and the download button are on opposite branches
    assert "if stored is None:" in src
    assert src.index("cmp_export") < src.index("return")
    assert src.index("cmp_download") > src.index("if stored is None:")


@NEEDS_DATA
def test_a_stored_report_is_discarded_when_the_comparison_changes():
    import app as app_mod

    bundles = [{"code": "MN0602"}, {"code": "MN0603"}]
    plan = plan_parser.RestaurantPlan(cuisine="Italian", concept="brunch")
    base = app_mod.report_signature(bundles, "Italian", plan)
    assert base == app_mod.report_signature(bundles, "Italian", plan)
    assert base != app_mod.report_signature(
        [{"code": "MN0602"}, {"code": "MN0401"}], "Italian", plan)
    assert base != app_mod.report_signature(bundles, "Thai", plan)
    assert base != app_mod.report_signature(
        bundles, "Italian",
        plan_parser.RestaurantPlan(cuisine="Italian", concept="wine bar"))


def test_report_generation_shows_the_loader():
    src = APP_SRC[APP_SRC.index("def _request_report("):]
    src = src[:src.index("\ndef ", 5)]
    assert "request_loading(" in src
    assert "Creating your report" in src
    assert "_build_report" in src, "the build is deferred to the covered run"


# ================================================== 5. simulation copy gone
def test_no_simulation_copy_reaches_the_product():
    """
    The caption "A scenario-based financial model of opening at this
    address…" rendered unconditionally in the site panel, outside the
    simulation_enabled() guard that hid the button it described — the app
    advertised a feature it does not offer.
    """
    src = APP_SRC[APP_SRC.index("def render_next("):]
    src = src[:src.index("\ndef ", 5)]
    for gone in ("scenario-based financial model", "Simulate opening here",
                 "Estimates, not forecasts"):
        assert gone not in src, gone
    assert "simulation_enabled" not in src


def test_what_next_offers_two_balanced_actions():
    src = APP_SRC[APP_SRC.index("def render_next("):]
    src = src[:src.index("\ndef ", 5)]
    assert "ui.button_row(2)" in src, "equal columns"
    assert src.count('width="stretch"') == 2
    assert "Compare another location" in src
    assert "Change concept" in src
    # short enough to stay on one line beside each other
    for label in ("Compare another location", "Change concept"):
        assert len(label) <= 26, label


# ==================================================== 6. button symmetry
def test_button_rows_use_equal_columns():
    import inspect

    from nycsiting import ui

    src = inspect.getsource(ui.button_row)
    assert "[1] * max(1, count)" in src, "equal columns, always"


def test_adjacent_buttons_share_a_height():
    assert '[data-testid="stHorizontalBlock"] .stButton > button' in CSS
    block = CSS[CSS.index("v8: adjacent-button symmetry"):]
    assert "min-height: 44px" in block
    assert "height: 100%" in block
    assert "white-space: normal" in block, "wrap rather than clip"
    assert "@media (max-width: 760px)" in block, "stacked buttons stay full width"


def test_the_redundant_column_padding_is_removed():
    """
    Streamlit spaces columns twice — a 16px flex gap on the row AND a 6px
    right padding on each column. The padding comes out of the column's
    CONTENT box, so two buttons in columns of identical width measured
    329.5px and 323.6px. Measured after the fix: 333px and 333px.
    """
    block = CSS[CSS.index("v8: adjacent-button symmetry"):]
    assert "padding-right: 0 !important" in block
    assert '[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]' in block


@NEEDS_DATA
def test_no_button_row_leaves_an_empty_column():
    """A column kept for a control that has moved is just dead space."""
    assert "with b:\n        pass" not in APP_SRC
    assert "a, b = st.columns([1.2, 1])" not in APP_SRC


# ======================================================= 7. landing layout
def test_the_landing_hero_is_a_centred_column():
    src = APP_SRC[APP_SRC.index("def landing_page("):]
    src = src[:src.index("\ndef ", 5)]
    assert "st.columns([1, 3.2, 1])" in src, \
        "a real column centres the form; a markdown wrapper cannot"
    assert "with hero:" in src


def test_the_header_rule_no_longer_eats_the_fold():
    """56px above AND below a 1px rule spent 112px of vertical space."""
    rule = next(line for line in CSS.splitlines()
                if line.startswith("hr {"))
    margin = rule.split("margin:")[1].split("!important")[0].strip()
    assert margin.startswith("14px"), margin


# ============================== 8. site-mode restaurant competitors (v8.1)
# A site map that showed only its own marker answered "where is this
# address?" and nothing else. The question a site analysis exists to answer
# is "how crowded is it HERE", so the same restaurant tiers area mode draws
# are now drawn around the address — through the SAME tiering, with only
# the geography differing (a radius instead of a polygon).
SITE = {"lat": 40.724836, "lon": -73.991665,
        "label": "6 EAST 1 STREET, New York, NY, USA"}


@NEEDS_DATA
def test_site_and_area_share_one_tiering_implementation():
    """§79: not two independent restaurant-selection algorithms."""
    import inspect

    import app as app_mod

    area_src = inspect.getsource(app_mod.area_restaurant_tiers)
    site_src = inspect.getsource(app_mod.site_restaurant_tiers)
    assert "restaurant_tiers(" in area_src and "restaurant_tiers(" in site_src
    # neither may re-implement the honesty rules
    for src, name in ((area_src, "area"), (site_src, "site")):
        for rule in ("competitive_set", "unidentifiable", "str.contains"):
            assert rule not in src, (
                f"{name} tiering re-implements '{rule}' instead of sharing it")


@NEEDS_DATA
def test_site_tiers_partition_the_radius_one_row_per_establishment():
    import app as app_mod

    panel = app_mod.load_panel()
    tiers = app_mod.site_restaurant_tiers(panel, SITE["lat"], SITE["lon"],
                                          350, "Japanese", "brunch spot")
    total = (len(tiers["closest"]) + len(tiers["similar"])
             + len(tiers["other"]))
    assert total == tiers["total"], "tiers must partition the radius exactly"
    for tier in ("closest", "similar", "other"):
        frame = tiers[tier]
        assert len(frame) == frame["camis"].nunique(), \
            f"{tier} plots inspection rows, not establishments"


@NEEDS_DATA
def test_a_larger_radius_never_finds_fewer_restaurants():
    """§85: the marker set must change logically with the radius."""
    import app as app_mod

    panel = app_mod.load_panel()
    counts = []
    for radius in (250, 350, 500, 750):
        tiers = app_mod.site_restaurant_tiers(panel, SITE["lat"], SITE["lon"],
                                              radius, "Japanese",
                                              "brunch spot")
        counts.append(tiers["total"])
    assert counts == sorted(counts), counts
    assert counts[0] < counts[-1], "the radius must actually bind"


@NEEDS_DATA
def test_every_site_restaurant_in_the_district_is_in_the_area_tiers():
    """
    §86: the same establishment must qualify for the same tier whichever
    mode is asking. Shared tiering makes this structural; this proves it on
    real data rather than trusting the refactor.
    """
    import app as app_mod

    panel = app_mod.load_panel()
    code = app_mod.nta_index().locate(SITE["lat"], SITE["lon"])
    assignment = app_mod.nta_assignment(panel)
    site_tiers = app_mod.site_restaurant_tiers(
        panel, SITE["lat"], SITE["lon"], 500, "Japanese", "brunch spot")
    area_tiers = app_mod.area_restaurant_tiers(panel, code, "Japanese",
                                               "brunch spot")
    for tier in ("closest", "similar", "other"):
        in_district = set(site_tiers[tier]["camis"][
            site_tiers[tier]["camis"].map(assignment) == code])
        assert in_district <= set(area_tiers[tier]["camis"]), (
            f"{tier}: restaurants qualify at site level but not at area "
            "level — the two modes are filtering differently")


@NEEDS_DATA
@pytest.mark.parametrize("mode,expect_tiers", [
    ("Exact concept", {"Exact concept"}),
    ("Same cuisine", {"Exact concept", "Same cuisine"}),
    ("All restaurants", {"Exact concept", "Same cuisine",
                         "Other restaurant"}),
])
def test_each_site_filter_draws_its_own_marker_tiers(mode, expect_tiers):
    """§74: switching the filter must change the markers, not just the
    selected button — and the legend counts must match what is drawn."""
    import app as app_mod
    from nycsiting import workspace_map

    panel = app_mod.load_panel()
    fig = app_mod._layer_figure(panel, "concept_fit", "Japanese",
                                app_mod.nta_display_geometry(),
                                app_mod._hover_frame(panel),
                                (SITE["lat"], SITE["lon"]), 13.6, SITE, None,
                                fill_scale=0.5)
    tiers = app_mod.site_restaurant_tiers(panel, SITE["lat"], SITE["lon"],
                                          350, "Japanese", "brunch spot")
    workspace_map.add_radius_ring(fig, SITE["lat"], SITE["lon"], 350)
    show_other = (mode == "All restaurants"
                  or (mode == "Exact concept" and tiers["unidentifiable"]))
    workspace_map.add_restaurant_markers(
        fig,
        tiers["similar"] if mode in ("Same cuisine", "All restaurants")
        else tiers["similar"].iloc[0:0],
        tiers["other"], show_other=show_other, closest=tiers["closest"])
    workspace_map.add_site_marker(fig, SITE["lat"], SITE["lon"],
                                  SITE["label"])

    drawn = {t.name.rsplit(" (", 1)[0]: t for t in fig.data
             if getattr(t, "showlegend", False) and t.name}
    assert expect_tiers <= set(drawn), (mode, sorted(drawn))
    assert "Selected site" in drawn, "the address must stay visible"
    assert any("search radius" in n for n in drawn), "the radius is stated"
    # legend counts must equal the markers actually plotted
    for label, trace in drawn.items():
        if "(" not in (trace.name or ""):
            continue
        stated = int(trace.name.rsplit("(", 1)[1].rstrip(")").replace(",", ""))
        if label.endswith("search radius"):
            continue
        assert stated == len(trace.lat), (label, stated, len(trace.lat))


@NEEDS_DATA
def test_site_markers_are_vectorised_one_trace_per_tier():
    """§83: never one Plotly trace per restaurant."""
    import app as app_mod
    from nycsiting import workspace_map

    panel = app_mod.load_panel()
    tiers = app_mod.site_restaurant_tiers(panel, SITE["lat"], SITE["lon"],
                                          500, "Japanese", "brunch spot")
    fig = __import__("plotly.graph_objects", fromlist=["x"]).Figure()
    workspace_map.add_restaurant_markers(fig, tiers["similar"],
                                         tiers["other"], show_other=True,
                                         closest=tiers["closest"])
    assert len(fig.data) == 3, "one trace per tier, whatever the point count"
    assert sum(len(t.lat) for t in fig.data) == tiers["total"]


@NEEDS_DATA
def test_the_site_map_still_shows_its_district_and_the_site_marker():
    """§63/§69: markers must not replace the boundary or the address."""
    src = APP_SRC[APP_SRC.index("# --- local competitive environment"):]
    src = src[:src.index("# --- selection emphasis")]
    assert "site_tiers_cached(" in src
    assert "add_restaurant_markers(" in src
    assert "add_radius_ring(" in src
    assert "nta_index().locate(" in src, "containing district still drawn"


@NEEDS_DATA
def test_the_map_reconciles_its_counts_with_the_competition_read():
    """
    §73: the map splits the comparable set by specificity while the
    competition read counts it whole. Both are right; the difference has to
    be stated rather than left as two numbers that look contradictory.
    """
    from nycsiting import analysis

    import app as app_mod

    panel = app_mod.load_panel()
    locs = app_mod.load_locations()
    key = app_mod.resolve_location_key(locs, SITE)
    report = analysis.site_report(panel, locs, SITE["lat"], SITE["lon"],
                                  "Japanese", 350, key)
    tiers = app_mod.site_restaurant_tiers(panel, SITE["lat"], SITE["lon"],
                                          350, "Japanese", "brunch spot")
    area = report["area"]
    assert tiers["total"] == area["active_all"], \
        "the map's total and the panel's current-establishment count differ"
    assert len(tiers["closest"]) == area["active_same_cuisine"], \
        "the exact tier and the panel's same-cuisine count differ"
    assert (len(tiers["closest"]) + len(tiers["similar"])
            == area["active_competitors"]), \
        "exact + same cuisine must be exactly the comparable competitors"
    # and the app still says so — in the analysis pane now, not as a
    # paragraph above the map (v8.2 section 28)
    recorder = APP_SRC[APP_SRC.index("def record_filter_explanation("):]
    recorder = recorder[:recorder.index("\ndef ", 5)]
    assert "comparable competitors the competition read" in recorder
    site_block = APP_SRC[APP_SRC.index("# --- local competitive environment"):]
    site_block = site_block[:site_block.index("# --- selection emphasis")]
    assert "st.caption(" not in site_block, \
        "no explanatory paragraphs may render above the map"
