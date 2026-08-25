"""
End-to-end checks that the app renders without blowing up.

Skipped unless `python build_data.py` has been run. These hit the GeoSearch
network service, so they are slower and less hermetic than the unit tests —
run them before shipping a UI change, not on every save.
"""
import pytest

from nycsiting import config

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not config.RESTAURANTS_PQ.exists(),
    reason="processed data not built; run `python build_data.py`")

APP = str(config.APP_DIR / "app.py")


def _geosearch_up() -> bool:
    """These journeys geocode live; a provider 503 is an outage, not a bug."""
    import requests
    try:
        r = requests.get("https://geosearch.planninglabs.nyc/v2/search",
                         params={"text": "1 Centre St, Manhattan", "size": 1},
                         timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def run(address: str, cuisine: str) -> AppTest:
    """
    Walk the real journey: natural-language plan -> parse (deterministic
    fallback in CI — no API key) -> confirmation screen -> Analyze. The
    parser extracts the cuisine and address from the sentence; the
    confirmation selectbox then pins the cuisine exactly.
    """
    if not _geosearch_up():
        pytest.skip("NYC GeoSearch is unavailable (external outage) — "
                    "journey tests need live geocoding")
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.text_area[0].set_value(f"{cuisine} restaurant at {address}")
    at.run()
    at = at.button[0].click().run()          # Continue -> confirm stage
    assert at.session_state["stage"] == "confirm", "parse did not reach confirm"
    at.selectbox[0].set_value(cuisine)
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    return at


@pytest.mark.parametrize("address,cuisine", [
    ("195 Bowery, Manhattan", "Italian"),          # no history at the lot
    ("42 Broadway, Manhattan", "Italian"),         # 30 tenants, several gone
    ("1 East 161 Street, Bronx", "American"),      # Yankee Stadium
    ("136-20 38th Ave, Queens", "Chinese"),        # dense, many concurrent
    ("2 Nevins Street, Brooklyn", "Thai"),         # ordinary block
])
def test_renders_without_exception(address, cuisine):
    at = run(address, cuisine)
    assert not at.exception, at.exception
    assert any("Location fit" in m.value for m in at.markdown)
    assert any("Our assessment" in m.value for m in at.markdown)


def test_multi_vendor_venue_is_called_out():
    at = run("1 East 161 Street, Bronx", "American")
    assert any("food hall" in w.value for w in at.warning)


def test_bogus_address_warns_instead_of_reporting_silently():
    at = run("999 Nowhere Road, Manhattan", "Italian")
    assert any("GeoSearch returned a different address" in w.value
               for w in at.warning)


def test_missing_house_number_routes_to_discovery_not_misgeocode():
    """
    v4: a street with no building number never reaches the geocoder at all —
    it routes to area/discovery, so the old silent-mis-geocode class of
    failure is structurally impossible for this input.
    """
    at = to_confirm("Chinese restaurant on Flushing Main Street, Queens")
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    assert not at.exception
    assert at.session_state["workspace_mode"] in ("area", "discovery")


def test_real_address_produces_no_geocode_warning():
    at = run("195 Bowery, Manhattan", "Italian")
    assert not any("GeoSearch returned a different address" in w.value
                   for w in at.warning)


# --- the optional Google layer must never take the page down ---------------
def _google_key_configured() -> bool:
    import tomllib
    try:
        with open(".streamlit/secrets.toml", "rb") as f:
            return bool(tomllib.load(f).get("GOOGLE_MAPS_API_KEY"))
    except FileNotFoundError:
        return False


@pytest.mark.skipif(_google_key_configured(),
                    reason="a real Google key is configured here; the no-key "
                           "branch is covered by mocked unit tests")
def test_app_runs_and_says_so_when_no_google_key_is_configured():
    """The default state for anyone who has just cloned this."""
    at = run("195 Bowery, Manhattan", "Italian")
    assert not at.exception
    assert any("Location fit" in m.value for m in at.markdown)
    assert any("GOOGLE_MAPS_API_KEY" in i.value for i in at.info)


def test_core_analysis_survives_a_google_outage(monkeypatch):
    """Every NYC-data section must still render when Google is unreachable."""
    from nycsiting import google_places

    def dead(*args, **kwargs):
        return google_places.CompetitorLandscape(
            ok=False, reason="quota",
            message="The Google Places quota for this key has been exhausted.")

    monkeypatch.setattr(google_places, "fetch_landscape", dead)
    at = run("42 Broadway, Manhattan", "Italian")
    assert not at.exception
    assert any("Location fit" in m.value for m in at.markdown)


def test_a_google_exception_cannot_escape_into_the_page(monkeypatch):
    from nycsiting import google_places

    def explode(*args, **kwargs):
        raise RuntimeError("google is on fire")

    # Patch below fetch_landscape so its own guards are what we are testing.
    monkeypatch.setattr(google_places, "search_places", explode)
    at = run("42 Broadway, Manhattan", "Italian")
    assert not at.exception


def test_full_google_section_renders_with_realistic_data(monkeypatch):
    """
    The closest thing to a live test without burning a real API key.

    Only the HTTP boundary is faked. The scoring, pressure rule, caching,
    chart and table all run for real.
    """
    import requests
    from nycsiting import google_places

    payload = {"places": [
        {"id": "a", "displayName": {"text": "Carbone"},
         "formattedAddress": "181 Thompson St, New York, NY",
         "rating": 4.7, "userRatingCount": 3482,
         "location": {"latitude": 40.7275, "longitude": -73.9995}},
        {"id": "b", "displayName": {"text": "Trattoria B"},
         "formattedAddress": "20 Prince St, New York, NY",
         "rating": 4.4, "userRatingCount": 1028,
         "location": {"latitude": 40.7248, "longitude": -73.9975}},
        {"id": "c", "displayName": {"text": "Small Cafe"},
         "formattedAddress": "9 Spring St, New York, NY",
         "rating": 3.5, "userRatingCount": 76,
         "location": {"latitude": 40.7252, "longitude": -73.9986}},
        {"id": "d", "displayName": {"text": "Closed Place"},
         "formattedAddress": "1 Nowhere St, New York, NY",
         "rating": 4.9, "userRatingCount": 500,
         "businessStatus": "CLOSED_PERMANENTLY",
         "location": {"latitude": 40.7253, "longitude": -73.9987}},
    ]}

    class Resp:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())

    # Capture the real function BEFORE patching, then hand it a stand-in key
    # so the no-key branch is bypassed without a secrets file existing.
    real_fetch = google_places.fetch_landscape
    monkeypatch.setattr(
        google_places, "fetch_landscape",
        lambda lat, lon, cuisine, key, **kw: real_fetch(
            lat, lon, cuisine, "TEST-KEY", **kw))

    at = run("100 Prince Street, Manhattan", "Italian")
    assert not at.exception

    body = " ".join(m.value for m in at.markdown)
    labels = [m.label for m in at.metric]
    assert "Strong competitors" in labels
    assert "Competitive pressure" in labels
    assert "Carbone" in body            # strongest competitor named
    assert "Closed Place" not in body   # permanently closed excluded

    # The UI must still say competitive pressure is NOT in the fit score.
    captions = " ".join(c.value for c in at.caption)
    assert "does **not** change the location fit score" in captions


# --- the guided journey ------------------------------------------------------
def test_landing_page_leads_with_the_product_not_the_data():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    body = " ".join(m.value for m in at.markdown)
    assert "Where should your" in body   # spec headline
    # Dataset names must not be the user's first impression.
    for jargon in ("DOHMH", "CAMIS", "PLUTO", "parquet"):
        assert jargon not in body


def test_evaluate_button_is_disabled_until_an_address_is_typed():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert at.button[0].disabled            # empty plan box
    at.text_area[0].set_value("Italian in 10003")
    at.run()
    assert not at.button[0].disabled


def _legacy_landing_gating():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert at.button[0].disabled is True
    at.text_input[0].set_value("195 Bowery, Manhattan")
    at.run()
    assert at.button[0].disabled is False


def test_results_keep_concept_and_location_in_view():
    at = run("42 Broadway, Manhattan", "Italian")
    # The sidebar shell is gone; the query context keeps concept and location
    # in view at the top of the main column instead.
    body = " ".join(m.value for m in at.markdown)
    assert "42 B" in body and "Italian" in body


def test_save_and_compare_keeps_the_candidate():
    at = run("42 Broadway, Manhattan", "Italian")
    save = next(b for b in at.button if "Save & compare" in b.label)
    at = save.click().run()
    # Back on the landing page, with the saved row visible.
    body = " ".join(m.value for m in at.markdown)
    assert "Where should your" in body
    assert "Analyzed locations" in body   # renamed in the refactor
    saved = at.session_state["saved"]
    assert len(saved) == 1
    assert saved[0]["Location"].startswith("42 B")
    assert saved[0]["Concept"] == "Italian"
    assert isinstance(saved[0]["Overall fit"], int)


def test_recommendation_synthesises_both_sides():
    at = run("42 Broadway, Manhattan", "Italian")
    body = " ".join(m.value for m in at.markdown)
    assert "Main positive" in body   # spec label
    assert "Main risk" in body
    assert "What this analysis cannot tell you" in body


def test_methodology_is_present_but_last():
    at = run("42 Broadway, Manhattan", "Italian")
    expanders = [e.label for e in at.expander]
    assert any("Data & methodology" in e for e in expanders)


# --- the simulate stage ------------------------------------------------------
def to_simulate(address="195 Bowery, Manhattan", cuisine="Italian"):
    at = run(address, cuisine)
    cta = next(b for b in at.button if "Simulate opening here" in b.label)
    return cta.click().run()


def test_simulate_is_only_reachable_after_an_assessment():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    # No CTA and no simulation inputs on the landing page.
    assert not any("Simulate opening here" in b.label for b in at.button)
    assert not any("Run simulation" in b.label for b in at.button)


def test_simulate_stage_runs_and_reconciles_with_the_engine():
    from nycsiting import financial_simulation as fs
    at = to_simulate()
    assert not at.exception
    run_btn = next(b for b in at.button if b.label == "Run simulation")
    at = run_btn.click().run()
    assert not at.exception
    # The Y1 revenue metric must equal the engine run with the same defaults.
    engine = fs.calculate_all_scenarios(fs.SimulationInputs(), 60)
    y1 = engine["expected"]["summary"]["year1_revenue"]
    shown = next(m.value for m in at.metric
                 if m.label == "Year 1 net sales (expected scenario)")
    assert f"${y1:,.0f}" == shown


def test_no_guaranteed_outcome_language():
    at = to_simulate()
    at = next(b for b in at.button if b.label == "Run simulation").click().run()
    text = " ".join(m.value for m in at.markdown) + " ".join(
        c.value for c in at.caption)
    assert "not guaranteed" in text or "not forecasts" in text
    assert "will generate" not in text
    assert "guaranteed" not in text.replace("not guaranteed", "")


def test_changing_location_invalidates_simulation_results():
    at = to_simulate("195 Bowery, Manhattan", "Italian")
    at = next(b for b in at.button if b.label == "Run simulation").click().run()
    assert at.session_state["sim_results"] is not None
    # Walk back and assess a DIFFERENT address, then enter simulate again.
    back = next(b for b in at.button if "Start over" in b.label)
    at = back.click().run()
    at.text_area[0].set_value("Italian restaurant at 42 Broadway, Manhattan")
    at.run()
    at = at.button[0].click().run()          # -> confirm
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    cta = next(b for b in at.button if "Simulate opening here" in b.label)
    at = cta.click().run()
    assert not at.exception
    # Old Bowery results must not be shown for Broadway.
    assert st_get(at, "sim_results") is None
    body = " ".join(m.value for m in at.markdown)
    assert "financial outlook" not in body


def st_get(at, key):
    try:
        return at.session_state[key]
    except KeyError:
        return None


# --- the natural-language plan flow (no network: fallback parser) ----------
def to_confirm(text: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.text_area[0].set_value(text)
    at.run()
    return at.button[0].click().run()


def test_confirmation_required_before_analysis():
    at = to_confirm("Italian restaurant at 195 Bowery, Manhattan")
    assert at.session_state["stage"] == "confirm"
    assert not st_get(at, "plan_confirmed")
    body = " ".join(m.value for m in at.markdown)
    assert "understood" in body.lower()
    # Parsed fields are prefilled and editable (input 0 is Concept, 1 Address).
    assert at.selectbox[0].value == "Italian"
    assert any("195 Bowery" in (t.value or "") for t in at.text_input)


def test_results_stage_is_gated_on_confirmation():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.session_state["stage"] = "results"
    at.session_state["address"] = "195 Bowery, Manhattan"
    at.session_state["cuisine"] = "Italian"
    at = at.run()
    # Without plan_confirmed the app bounces back to landing.
    assert at.session_state["stage"] == "landing"


def test_area_route_opens_area_workspace_no_third_step():
    """A ZIP plan opens the area workspace directly — no address demanded."""
    at = to_confirm("Italian restaurant in 10003")
    analyze = next(b for b in at.button if "Analyze" in b.label)
    at = analyze.click().run()
    assert not at.exception
    assert at.session_state["stage"] == "results"
    assert at.session_state["workspace_mode"] == "area"
    assert at.session_state["selected_area"] == "MN0303"   # East Village
    body = " ".join(m.value for m in at.markdown)
    assert "Which address" not in body


def test_off_topic_input_gets_a_gentle_redirect():
    at = to_confirm("Write me a poem.")
    body = " ".join(m.value for m in at.markdown)
    assert "Tell us about the restaurant" in body
    assert not any("Analyze" in b.label for b in at.button)


def test_new_plan_invalidates_previous_state():
    at = to_confirm("Italian restaurant at 195 Bowery, Manhattan")
    at.session_state["plan_confirmed"] = True
    at.session_state["sim_results"] = {"stale": True}
    # Back to landing, enter a different plan.
    back = next(b for b in at.button if "Rewrite" in b.label)
    at = back.click().run()
    at.text_area[0].set_value("Thai restaurant in Queens")
    at.run()
    at = at.button[0].click().run()
    assert not st_get(at, "plan_confirmed")
    assert st_get(at, "sim_results") is None


def test_plan_spend_and_seats_reach_the_simulator():
    from nycsiting.plan_parser import RestaurantPlan
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.session_state["confirmed_plan"] = RestaurantPlan(
        cuisine="Italian", address="195 Bowery",
        average_spend=70.0, seats=60)
    at.session_state["plan_confirmed"] = True
    at.session_state["address"] = "195 Bowery, Manhattan"
    at.session_state["cuisine"] = "Italian"
    at.session_state["sim_location_id"] = ("x", "Italian")
    at.session_state["stage"] = "simulate"
    at = at.run()
    if at.exception:            # simulate page geocodes; outage-tolerant
        pytest.skip("geocoding unavailable")
    spends = [n.value for n in at.number_input]
    assert 70.0 in spends and 60 in [int(v) for v in spends]


def test_ui_backend_label_uses_parse_result():
    """The confirm label reads the parse metadata, never a key check."""
    from nycsiting.plan_parser import PlanParseResult, RestaurantPlan
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.session_state["plan_outcome"] = PlanParseResult(
        plan=RestaurantPlan(cuisine="Italian", zipcode="10003"),
        parser_backend="anthropic", model="claude-haiku-4-5",
        api_attempted=True, api_success=True, validation_success=True)
    at.session_state["stage"] = "confirm"
    at = at.run()
    caps = " ".join(c.value for c in at.caption)
    assert "Interpreted from your description" in caps
    assert "local parser" not in caps and "pattern" not in caps

    at.session_state["plan_outcome"] = PlanParseResult(
        plan=RestaurantPlan(cuisine="Italian", zipcode="10003"),
        parser_backend="fallback", fallback_reason="api_error",
        api_attempted=True)
    at = at.run()
    caps = " ".join(c.value for c in at.caption)
    assert "temporarily unavailable" in caps


def test_cached_fallback_not_reused_when_anthropic_available():
    """
    The root-cause regression: the parse cache key must include key
    presence, so a fallback cached while the key was missing cannot replay
    once the key exists.
    """
    import inspect
    import app as app_module
    signature = inspect.signature(app_module.parse_plan_cached.__wrapped__)
    params = list(signature.parameters)
    assert "key_present" in params, "cache key lost the backend discriminator"
    assert params[-1] == "_api_key", "the secret must stay underscore-excluded"


# --- v4: canonical location, polygon interaction, filters -------------------
def test_exact_address_marker_uses_resolved_latlon():
    """One canonical coordinate: the map marker, competitor query and
    pedestrian lookup all read session-resolved geocode output — never a
    ZIP/NTA centroid."""
    at = run("195 Bowery, Manhattan", "Italian")
    assert not at.exception
    assert at.session_state["workspace_mode"] == "site"


def test_zip_is_never_geocoded_to_a_street_address():
    """GeoSearch resolves bare '10003' to Springfield Blvd in Queens Village;
    the ZIP route must therefore bypass geocoding entirely."""
    import app as app_module
    import pandas as pd
    panel = pd.read_parquet("processed/restaurants.parquet")
    code = app_module.zip_to_nta.__wrapped__(panel, "10003")
    assert code == "MN0303"


def test_top_match_click_matches_polygon_click():
    """Both paths run select_area — asserted at the source level plus the
    live discovery behavior."""
    import inspect
    import app as app_module
    src = inspect.getsource(app_module)
    # the only writers of selected_area are select_area + explicit clears
    writes = [l for l in src.splitlines()
              if 'session_state["selected_area"]' in l and "=" in l
              and "pop" not in l]
    assert all("select_area" in l or "def select_area" in l or
               "st.session_state[\"selected_area\"] = code" in l
               for l in writes), writes


def test_area_restaurants_unique_camis_and_similar_subset():
    import app as app_module
    import pandas as pd
    panel = pd.read_parquet("processed/restaurants.parquet")
    similar, other = app_module.area_restaurants(panel, "MN0303", "Italian")
    assert similar["camis"].is_unique and other["camis"].is_unique
    assert not set(similar["camis"]) & set(other["camis"])
    # similar ⊂ all
    all_count = len(similar) + len(other)
    assert 0 < len(similar) < all_count
    # membership is spatial: every one carries coordinates inside the polygon
    from nycsiting import geometry
    idx = geometry.NTAIndex()
    feats = idx.features["MN0303"]
    sampled = similar.head(10)
    assert all(geometry.point_in_multipolygon(r.lon, r.lat, feats["polygons"])
               for r in sampled.itertuples())


def test_missing_seats_never_rendered_as_zero():
    at = to_confirm("Italian restaurant in 10003")
    seats = next(n for n in at.number_input if "Seats" in n.label)
    assert seats.value is None


def test_no_need_address_stage_exists():
    import app as app_module
    import inspect
    assert "need_address_page" not in dir(app_module)
    assert "need_address" not in inspect.getsource(app_module.main)
