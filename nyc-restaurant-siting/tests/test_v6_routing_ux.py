"""
V6 regression battery: neighborhood routing, free Explore/Assess/Method
navigation, the Analyze-site CTA, explained concept scores, marker
readability, and the split-word formatting fixes.

Area journeys never geocode, so unlike the site journeys these are hermetic
apart from the optional Anthropic parse (the deterministic fallback extracts
neighborhoods from the app's own geography lexicon either way).
"""
import pytest

from nycsiting import config, plan_parser

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not config.RESTAURANTS_PQ.exists(),
    reason="processed data not built; run `python build_data.py`")

APP = str(config.APP_DIR / "app.py")
CSS = (config.APP_DIR / "assets" / "styles.css").read_text()
APP_SRC = (config.APP_DIR / "app.py").read_text()


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except Exception:
        return default


def area_run(text: str) -> AppTest:
    """Plan -> confirm -> Analyze for prompts that need no geocoding."""
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.text_area[0].set_value(text)
    at.run()
    at = next(b for b in at.button if "Continue" in str(b.label)).click().run()
    assert ss(at, "stage") == "confirm", "parse did not reach confirm"
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    assert not at.exception, at.exception
    return at


def injected_run(plan: plan_parser.RestaurantPlan) -> AppTest:
    """Journey with a directly-injected parsed plan — hermetic regardless of
    which parser backend the environment has."""
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan, parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    assert not at.exception, at.exception
    return at


# ------------------------------------------------------------------ routing
def test_murray_hill_routes_area():
    at = area_run("I want to open a Japanese in Murray Hill")
    assert ss(at, "workspace_mode") == "area"
    assert ss(at, "selected_area") == "MN0603"      # Murray Hill-Kips Bay
    body = " ".join(m.value for m in at.markdown)
    assert "Murray Hill-Kips Bay" in body
    assert "Area analysis" in body
    assert "Top matches" not in body                # never Top 3 as primary


@pytest.mark.parametrize("text,code", [
    ("Italian restaurant in East Village", "MN0303"),
    ("Cafe in West Village", "MN0203"),
    ("Mexican restaurant in Gramercy", "MN0602"),
    ("Bakery in Williamsburg", "BK0102"),
])
def test_specific_neighborhood_never_routes_discovery(text, code):
    at = area_run(text)
    assert ss(at, "workspace_mode") == "area", text
    assert ss(at, "selected_area") == code


def test_borough_only_routes_discovery():
    at = area_run("Japanese restaurant somewhere in Manhattan")
    assert ss(at, "workspace_mode") == "discovery"
    assert ss(at, "discovery_borough") == "Manhattan"


def test_no_location_routes_discovery_nyc():
    at = injected_run(plan_parser.RestaurantPlan(cuisine="Thai"))
    assert ss(at, "workspace_mode") == "discovery"
    assert ss(at, "discovery_borough") in (None, "")


def test_zip_routes_area():
    at = injected_run(plan_parser.RestaurantPlan(cuisine="Italian",
                                                 zipcode="10003"))
    assert ss(at, "workspace_mode") == "area"
    assert ss(at, "selected_area")


def test_ambiguous_name_offers_alternatives_in_confirmation():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(cuisine="Japanese",
                                        neighborhood="Murray Hill"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    disamb = [s for s in at.selectbox if "matches more than one" in s.label]
    assert disamb, "no disambiguation choice offered"
    assert any("Queens" in str(o) for o in disamb[0].options)
    assert any("Manhattan" in str(o) for o in disamb[0].options)


def test_edited_area_field_beats_stale_parsed_zip():
    """The Area field is authoritative: a user who replaces the parsed ZIP
    with a neighborhood name must get that neighborhood, never the ZIP."""
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(cuisine="Italian", zipcode="10016"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    area_field = next(t for t in at.text_input if t.label.startswith("Area"))
    assert area_field.value == "10016"          # prefilled from the parse
    area_field.set_value("East Village")
    at = at.run()
    at = next(b for b in at.button if "Analyze" in b.label).click().run()
    assert not at.exception, at.exception
    assert ss(at, "selected_area") == "MN0303", \
        "stale parsed ZIP overrode the edited Area field"


def test_cleared_area_field_routes_discovery_not_stale_zip():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(cuisine="Italian", zipcode="10016"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    next(t for t in at.text_input
         if t.label.startswith("Area")).set_value("")
    at = at.run()
    at = next(b for b in at.button if "Analyze" in b.label).click().run()
    assert ss(at, "workspace_mode") == "discovery"


def test_numberless_address_resolves_demoted_neighborhood():
    at = injected_run(plan_parser.RestaurantPlan(
        cuisine="Chinese", address="Flushing Main Street"))
    assert ss(at, "workspace_mode") == "area", \
        "demoted numberless address should resolve to its neighborhood"
    assert ss(at, "selected_area")


def test_punctuated_borough_still_routes_discovery():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(cuisine="Pizza"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    next(t for t in at.text_input
         if t.label.startswith("Area")).set_value("Brooklyn.")
    at = at.run()
    at = next(b for b in at.button if "Analyze" in b.label).click().run()
    assert ss(at, "workspace_mode") == "discovery"
    assert ss(at, "discovery_borough") == "Brooklyn"


def test_lexicon_excludes_prose_fragments():
    """Hyphen shards of park/cemetery names must never reach the fallback
    lexicon — 'green tea' in a plan is not Green-Wood Cemetery."""
    import app as app_mod
    lex = app_mod.area_name_lexicon()
    assert "green" not in lex and "co" not in lex
    assert all(len(s) >= 4 for s in lex)
    assert "soho" in lex and "murray hill" in lex


def test_continuous_layer_respects_center_and_zoom():
    import pandas as pd
    import app as app_mod
    from nycsiting import workspace_map
    fig = workspace_map.continuous_choropleth(
        app_mod.nta_geojson(), pd.Series({"MN0603": 12.0}), "test",
        center=(40.75, -73.98), zoom=13.2)
    assert fig.layout.mapbox.center.lat == pytest.approx(40.75)
    assert fig.layout.mapbox.zoom == pytest.approx(13.2)


def test_evidence_layer_limited_band_draws_dim():
    import pandas as pd
    import app as app_mod
    from nycsiting import workspace_map
    fig = workspace_map.band_choropleth(
        app_mod.nta_geojson(),
        pd.Series({"MN0603": "Limited", "MN0303": "High"}), "evidence")
    opacities = dict(zip(fig.data[0].locations, fig.data[0].marker.opacity))
    assert opacities["MN0603"] < opacities["MN0303"], \
        "'Limited' evidence must draw dim, like every limited band"


def test_fallback_parser_extracts_known_neighborhood():
    plan = plan_parser.parse_fallback(
        "I want to open a Japanese in Murray Hill",
        known_areas=("murray hill", "east village"))
    assert plan.neighborhood == "Murray Hill"
    # and never invents one without a lexicon
    assert plan_parser.parse_fallback(
        "I want to open a Japanese in Murray Hill").neighborhood is None


# --------------------------------------------------------------- navigation
def _nav(at, label):
    at = next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, at.exception
    return at


def test_explore_assess_free_navigation_preserves_area():
    at = area_run("Japanese restaurant in Murray Hill")
    outcome_before = ss(at, "plan_outcome")
    at = _nav(at, "Explore")
    assert ss(at, "workspace_view") == "explore"
    assert ss(at, "selected_area") == "MN0603"
    at = _nav(at, "Assess")
    assert ss(at, "workspace_view") == "assess"
    assert ss(at, "selected_area") == "MN0603"
    # no parser rerun: the parse result object is untouched
    assert ss(at, "plan_outcome") is outcome_before


def test_toolbar_state_survives_navigation():
    """Nav clicks must never reset the restaurant filter or layer — the
    keyed-widget state is mirrored so it survives runs it does not render
    in (the Method view has no toolbar at all)."""
    at = area_run("Japanese restaurant in Murray Hill")
    at.radio[0].set_value("All")
    at = at.run()
    assert ss(at, "ws_comp") == "All"
    at = _nav(at, "Explore")
    assert ss(at, "ws_comp") == "All", "filter reset by view switch"
    at = _nav(at, "Method")            # toolbar absent in this view
    at = _nav(at, "Assess")
    assert ss(at, "ws_comp") == "All", "filter reset by Method roundtrip"
    assert ss(at, "selected_area") == "MN0603"


def test_method_visible_without_sidebar():
    assert "st.sidebar" not in APP_SRC, "important UI must not hide in the " \
                                        "sidebar (it is display:none)"
    at = area_run("Japanese restaurant in Murray Hill")
    at = _nav(at, "Method")
    caps = " ".join(c.value for c in at.caption)
    body = " ".join(m.value for m in at.markdown)
    assert "How the analysis works" in body
    assert "50 + (local − citywide rate) × 250" in caps
    assert "Multiple concepts" in caps or "multiple concepts" in caps
    assert "language parsing only" in caps


def test_selected_area_prompt_autofits_bounds():
    at = area_run("Japanese restaurant in Murray Hill")
    assert ss(at, "last_fitted_area") == f"area:{ss(at, 'selected_area')}"


# ---------------------------------------------------------------- area panel
def test_analyze_site_primary_cta():
    at = area_run("Japanese restaurant in Murray Hill")
    cta = [b for b in at.button if "Analyze a specific site" in str(b.label)]
    assert cta, "Analyze-site CTA missing from area analysis"
    # primary styling is declared at the call site
    tail = APP_SRC.split("Analyze a specific site →", 1)[1][:120]
    assert 'type="primary"' in tail
    caps = " ".join(c.value for c in at.caption)
    assert "Have a specific address in this area?" in caps


def test_concept_fit_score_has_relative_label():
    at = area_run("Japanese restaurant in Murray Hill")
    body = " ".join(m.value for m in at.markdown)
    caps = " ".join(c.value for c in at.caption)
    # V7: with the user's own concept set, the module renders as secondary
    assert ("Other concepts that fit here" in body
            or "Concepts that fit this area" in body)
    assert "/ 100" in body
    assert "not a probability of success" in caps
    assert "Best-fitting" not in body                # renamed away


def test_concept_fit_why_breakdown_matches_components():
    """The Why? breakdown and the ranking must be the SAME computation the
    analytics module performs — proven by equality, not resemblance."""
    import app as app_mod
    from nycsiting import areas
    panel = app_mod.load_panel()
    assignment = app_mod.nta_assignment(panel)
    reference = areas.rank_concepts_for_area(panel, assignment, "MN0603",
                                             top=8)
    cached = app_mod.concept_ranking_cached(panel, "MN0603")
    assert [r["cuisine"] for r in cached] == [r["cuisine"] for r in reference]
    for c, r in zip(cached, reference):
        assert c["fit_index"] == pytest.approx(r["fit_index"])
        assert c["band"] == r["band"]
        assert c["cohort_n"] == r["cohort_n"]
    # breakdown inputs exposed for the Why? expander
    assert {"cohort_survived", "baseline_rate", "baseline_n"} <= set(cached[0])


def test_top_relative_fit_label_for_100():
    """Find an area whose ranking actually contains a 100 and assert the
    rendered label there — never a conditional that can silently skip."""
    import app as app_mod
    panel = app_mod.load_panel()
    target = next(
        (code for code in app_mod.area_features_cached(panel)
         .sort_values("restaurants_active", ascending=False).index[:40]
         if any(r["fit_index"] >= 100
                for r in app_mod.concept_ranking_cached(panel, code)[:3])),
        None)
    assert target is not None, "no area with a top-3 fit of 100 found"
    at = injected_run(plan_parser.RestaurantPlan(
        cuisine="Japanese",
        neighborhood=app_mod.nta_names()[target]))
    if ss(at, "selected_area") != target:      # name may be ambiguous
        at.session_state["selected_area"] = target
        at = at.run()
    body = " ".join(m.value for m in at.markdown)
    assert "100 / 100" in body
    assert "Top relative fit" in body


def test_why_breakdown_renders_actual_numbers():
    """The rendered Why? expander must show the same cohort numbers the
    analytics computed — asserted against the rendered body, not just the
    data layer."""
    import app as app_mod
    at = area_run("Japanese restaurant in Murray Hill")
    panel = app_mod.load_panel()
    top = app_mod.concept_ranking_cached(panel, "MN0603")[0]
    body = " ".join(m.value for m in at.markdown)
    assert f"{top['cohort_survived']}/{top['cohort_n']}" in body
    assert f"(n={top['baseline_n']:,})" in body


def test_restaurant_click_handles_sticky_selection_once():
    """A plotly selection is sticky: the same event re-delivers every rerun
    while the figure is unchanged. The handler must act exactly once —
    acting every time was an infinite st.rerun() loop."""
    script = f"""
import sys
sys.path.insert(0, {str(config.APP_DIR)!r})
import streamlit as st
import app
st.session_state["runs"] = st.session_state.get("runs", 0) + 1
# the sticky event is delivered on EVERY run, exactly like st.plotly_chart
event = {{"selection": {{"points": [{{"customdata": ["camis:123"]}}]}}}}
app._apply_map_selection(event, None)
st.write("runs:", st.session_state["runs"])
st.write("selected:", st.session_state.get("selected_restaurant"))
"""
    at = AppTest.from_string(script)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "selected: 123" in body              # the click took effect
    # run 1 handled the event and rerean; run 2 saw the sticky re-delivery,
    # ignored it, and fell through — an unguarded handler never settles.
    assert "runs: `2`" in body or "runs: 2" in body


def test_radius_survives_method_roundtrip():
    at = area_run("Japanese restaurant in Murray Hill")
    addr = next(t for t in at.text_input if t.label == "Address")
    addr.set_value("460 Third Avenue")
    cta = next(b for b in at.button if "Analyze a specific site" in str(b.label))
    at = cta.click().run()
    if at.exception or at.error:
        pytest.skip("geocoding unavailable")
    at.slider[0].set_value(1000)
    at = at.run()
    assert ss(at, "ws_radius") == 1000
    at = _nav(at, "Method")                    # slider absent: state GC'd
    at = _nav(at, "Assess")
    assert at.slider[0].value == 1000, "radius reset by Method roundtrip"


def test_discovery_after_cleared_field_shows_no_stale_chips():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    at.session_state["plan_outcome"] = plan_parser.PlanParseResult(
        plan=plan_parser.RestaurantPlan(cuisine="Italian", zipcode="10016"),
        parser_backend="fallback", fallback_reason="missing_key")
    at.session_state["stage"] = "confirm"
    at = at.run()
    next(t for t in at.text_input
         if t.label.startswith("Area")).set_value("")
    at = at.run()
    at = next(b for b in at.button if "Analyze" in b.label).click().run()
    assert ss(at, "workspace_mode") == "discovery"
    body = " ".join(m.value for m in at.markdown)
    assert "10016" not in body, "stale parsed ZIP resurfaced in the UI"


def test_opportunity_gap_no_split_columns():
    at = area_run("Japanese restaurant in Murray Hill")
    bench_blocks = [m.value for m in at.markdown if "jx-bench" in m.value]
    joined = " ".join(bench_blocks)
    assert "Opportunity gap" in joined, \
        "gap must render as label/value rows, not narrow columns"
    assert "Concept fit" in joined and "Competition" in joined
    assert "Observed turnover" in joined and "Evidence" in joined


def test_no_word_break_css_for_normal_text():
    assert "word-break: break-word" not in CSS
    assert "overflow-wrap: anywhere" not in CSS
    assert "word-break: normal" in CSS
    # the panel evidence-row fix is in place
    assert "grid-template-columns: 1fr auto" in CSS


# ------------------------------------------------------------------- markers
def test_all_marker_opacity_and_size_above_threshold():
    from nycsiting.workspace_map import MARKER_STYLE
    assert MARKER_STYLE["other"]["opacity"] >= 0.55
    assert 5 <= MARKER_STYLE["other"]["size"] <= 7
    assert MARKER_STYLE["similar"]["size"] >= 9
    # V7 hierarchy: closest is the top tier; similar sits between
    assert MARKER_STYLE["similar"]["opacity"] >= 0.75
    assert MARKER_STYLE["closest"]["opacity"] >= 0.9


def test_marker_legend_present_and_traces_vectorized():
    import pandas as pd
    from nycsiting import workspace_map
    frame = pd.DataFrame({
        "lat": [40.7, 40.71], "lon": [-73.99, -73.98],
        "camis": ["1", "2"], "name": ["a", "b"],
        "cuisine": ["Japanese", "Pizza"],
        "address": ["1 Main St", "2 Main St"]})
    import plotly.graph_objects as go
    fig = go.Figure()
    workspace_map.add_restaurant_markers(fig, frame, frame)
    names = [t.name for t in fig.data]
    assert any(n.startswith("Similar concept") for n in names)
    assert any(n.startswith("Other restaurant") for n in names)
    assert len(fig.data) == 2, "one vectorized trace per group, never " \
                               "one trace per restaurant"
    assert all(t.showlegend for t in fig.data)
    hover = fig.data[0].text[0]
    assert "1 Main St" in hover and "camis" not in hover


def test_similar_filter_hides_other_markers():
    import pandas as pd
    import plotly.graph_objects as go
    from nycsiting import workspace_map
    frame = pd.DataFrame({
        "lat": [40.7], "lon": [-73.99], "camis": ["1"], "name": ["a"],
        "cuisine": ["Japanese"], "address": ["1 Main St"]})
    fig = go.Figure()
    workspace_map.add_restaurant_markers(fig, frame, frame, show_other=False)
    assert [t.name for t in fig.data] == ["Similar concept (1)"]


def test_polygon_fill_mutes_under_markers():
    from nycsiting import workspace_map
    import pandas as pd
    import app as app_mod
    geojson = app_mod.nta_geojson()
    bands = pd.Series({"MN0603": "Strong"})
    full = workspace_map.band_choropleth(geojson, bands, "concept_fit")
    muted = workspace_map.band_choropleth(geojson, bands, "concept_fit",
                                          fill_scale=0.5)
    assert muted.data[0].marker.opacity[0] == pytest.approx(
        full.data[0].marker.opacity[0] * 0.5)


# ------------------------------------------------------------ personalization
def test_preference_alignment_is_deterministic_and_separate():
    import app as app_mod
    plan = plan_parser.RestaurantPlan(
        cuisine="Japanese", foot_traffic_preference="high",
        competition_tolerance="low")
    rows = app_mod.preference_alignment(plan, saturation_band="High",
                                        income_pct=None, density_pct=None,
                                        ped_band="High")
    by_key = {r["key"]: r for r in rows}
    assert by_key["foot_traffic"]["status"] == "match"
    assert by_key["competition"]["status"] == "conflict"
    assert "You preferred" in by_key["competition"]["detail"]
    # unstated preferences never appear
    assert "income" not in by_key and "density" not in by_key
    # unmeasured is never scored
    rows2 = app_mod.preference_alignment(plan, None, None, None, None)
    assert all(r["status"] == "unmeasured" for r in rows2)
    # and the validated scoring module knows nothing about preferences
    scoring_src = (config.APP_DIR / "nycsiting" / "scoring.py").read_text()
    areas_src = (config.APP_DIR / "nycsiting" / "areas.py").read_text()
    for src in (scoring_src, areas_src):
        assert "preference" not in src.lower()


def test_discovery_shows_core_fit_and_alignment_separately():
    at = injected_run(plan_parser.RestaurantPlan(
        cuisine="Japanese", borough="Manhattan",
        foot_traffic_preference="high", competition_tolerance="low"))
    assert ss(at, "workspace_mode") == "discovery"
    body = " ".join(m.value for m in at.markdown)
    assert "Preference-adjusted discovery" in body
    tops = [b.label for b in at.button if b.label and "Core fit" in b.label]
    assert tops and all("align" in t for t in tops)
    caps = " ".join(c.value for c in at.caption)
    assert "+10 per stated priority" in caps      # the ordering is documented


def test_your_plan_chips_show_only_explicit_values():
    import app as app_mod
    plan = plan_parser.RestaurantPlan(cuisine="Japanese",
                                      average_spend=60.0,
                                      foot_traffic_preference="high")
    chips = app_mod.plan_chip_values(plan, area_name="Murray Hill-Kips Bay")
    assert "Japanese" in chips and "~$60/person" in chips
    assert "High foot traffic" in chips
    assert not any("$$" in c for c in chips)        # unstated price absent
    assert app_mod.plan_chip_values(None) == []


def test_matches_your_priorities_rendered_for_area():
    at = injected_run(plan_parser.RestaurantPlan(
        cuisine="Japanese", neighborhood="East Village",
        competition_tolerance="low"))
    assert ss(at, "workspace_mode") == "area"
    body = " ".join(m.value for m in at.markdown)
    assert "Matches your priorities" in body
    assert "How your plan was used" in " ".join(
        e.label for e in at.expander if e.label)


def test_unsupported_fields_are_context_not_scores():
    import app as app_mod
    usage = app_mod.PLAN_FIELD_USAGE
    assert "not" in usage["target_customer_description"]
    assert "not scored" in usage["average_spend"] or \
           "not scored against" in usage["average_spend"]
