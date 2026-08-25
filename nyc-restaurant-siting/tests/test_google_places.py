"""
The optional Google layer.

Every test here is offline. The point of this module is that it behaves
sensibly when the network misbehaves, so the tests fake the network rather than
using it — a suite that needs a live API key and quota would not get run.
"""
import math

import pandas as pd
import pytest
import requests

from nycsiting import google_places as gp


# --- fakes ------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_value_error=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raise = raise_value_error

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Stands in for `requests`. Records the call so we can assert on it."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


SITE = {"housenumber": "100", "street": "PRINCE STREET"}
LAT, LON = 40.7255, -73.9983


def place(name="Rival", lat=40.7258, lon=-73.9985, rating=4.5, reviews=800,
          status=None, pid=None, address="200 Prince Street, New York, NY"):
    out = {
        "id": pid or f"place-{name}",
        "displayName": {"text": name},
        "formattedAddress": address,
        "rating": rating,
        "userRatingCount": reviews,
        "location": {"latitude": lat, "longitude": lon},
    }
    if status:
        out["businessStatus"] = status
    return out


# --- request construction ---------------------------------------------------
class TestRequest:
    def test_asks_only_for_the_fields_we_use(self):
        # Text Search is billed by field tier; a lazy "*" mask costs money.
        session = FakeSession(FakeResponse(200, {"places": []}))
        gp.search_places(LAT, LON, "Italian", "KEY", session=session)
        mask = session.calls[0]["headers"]["X-Goog-FieldMask"]
        assert "places.rating" in mask and "places.userRatingCount" in mask
        assert "*" not in mask

    def test_sends_the_key_in_the_header_not_the_url(self):
        session = FakeSession(FakeResponse(200, {"places": []}))
        gp.search_places(LAT, LON, "Italian", "SECRET", session=session)
        assert session.calls[0]["headers"]["X-Goog-Api-Key"] == "SECRET"
        assert "SECRET" not in session.calls[0]["url"]

    def test_biases_the_search_to_the_site(self):
        session = FakeSession(FakeResponse(200, {"places": []}))
        gp.search_places(LAT, LON, "Italian", "KEY", radius=750, session=session)
        circle = session.calls[0]["json"]["locationBias"]["circle"]
        assert circle["center"] == {"latitude": LAT, "longitude": LON}
        assert circle["radius"] == 750.0

    def test_query_is_the_cuisine(self):
        session = FakeSession(FakeResponse(200, {"places": []}))
        gp.search_places(LAT, LON, "Italian", "KEY", session=session)
        assert session.calls[0]["json"]["textQuery"] == "Italian restaurant"


# --- every failure the spec lists ------------------------------------------
class TestFailures:
    @pytest.mark.parametrize("key", [None, "", 0])
    def test_missing_key_is_reported_not_raised(self, key):
        result = gp.fetch_landscape(LAT, LON, "Italian", key)
        assert result.ok is False and result.reason == "no_key"
        assert "API key" in result.message

    @pytest.mark.parametrize("status,reason", [
        (401, "auth"), (403, "auth"), (429, "quota"),
        (500, "http_error"), (400, "http_error"),
    ])
    def test_http_errors_map_to_readable_reasons(self, status, reason):
        session = FakeSession(FakeResponse(status))
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.ok is False and result.reason == reason
        assert result.message and "Traceback" not in result.message

    def test_timeout(self):
        session = FakeSession(exc=requests.Timeout())
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.reason == "timeout" and result.ok is False

    def test_network_failure(self):
        session = FakeSession(exc=requests.ConnectionError())
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.reason == "network" and result.ok is False

    def test_unparseable_body(self):
        session = FakeSession(FakeResponse(200, raise_value_error=True))
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.reason == "bad_response" and result.ok is False

    def test_missing_cuisine(self):
        result = gp.fetch_landscape(LAT, LON, "  ", "KEY",
                                    session=FakeSession(FakeResponse(200, {})))
        assert result.reason == "no_cuisine" and result.ok is False

    def test_response_with_no_places_key(self):
        session = FakeSession(FakeResponse(200, {}))
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.ok is True and result.total == 0
        assert result.reason == "empty"

    def test_no_competitors_found_is_success_not_failure(self):
        session = FakeSession(FakeResponse(200, {"places": []}))
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.ok is True and "no Italian restaurants" in result.message

    def test_a_bug_in_parsing_still_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gp, "to_dataframe",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        session = FakeSession(FakeResponse(200, {"places": [place()]}))
        result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", session=session)
        assert result.ok is False and result.reason == "unexpected"

    def test_landscape_properties_are_safe_when_empty(self):
        empty = gp.CompetitorLandscape(ok=False)
        assert empty.total == 0 and empty.strong == 0
        assert empty.mean_rating is None and empty.strongest is None


# --- shaping the response ---------------------------------------------------
class TestToDataFrame:
    def test_permanently_closed_places_are_not_competitors(self):
        places = [place("Open"), place("Shut", status=gp.CLOSED_PERMANENTLY)]
        df = gp.to_dataframe(places, LAT, LON)
        assert list(df["name"]) == ["Open"]

    def test_missing_status_is_treated_as_trading(self):
        df = gp.to_dataframe([place("NoStatus")], LAT, LON)
        assert df.iloc[0]["business_status"] == "OPERATIONAL"

    def test_places_without_coordinates_are_dropped(self):
        broken = place("NoCoords")
        broken["location"] = {}
        assert gp.to_dataframe([broken], LAT, LON).empty

    def test_radius_is_enforced_by_us_not_by_google(self):
        # locationBias is a hint — Google returns famous places well outside it.
        far = place("FarAway", lat=40.7600, lon=-73.9800)
        near = place("Close")
        df = gp.to_dataframe([far, near], LAT, LON, radius=750)
        assert list(df["name"]) == ["Close"]
        assert (df["distance_m"] <= 750).all()

    def test_the_users_own_address_is_not_listed_as_a_competitor(self):
        own = place("My Own Place", address="100 Prince Street, New York, NY")
        rival = place("Rival", address="120 Prince Street, New York, NY")
        df = gp.to_dataframe([own, rival], LAT, LON, site=SITE)
        assert list(df["name"]) == ["Rival"]

    def test_a_rival_metres_away_is_still_counted(self):
        # Exclusion matches on address, not distance — a rival next door can be
        # within a few metres and must survive.
        rival = place("Next Door", lat=LAT, lon=LON,
                      address="102 Prince Street, New York, NY")
        df = gp.to_dataframe([rival], LAT, LON, site=SITE)
        assert len(df) == 1

    def test_duplicate_listings_collapse(self):
        df = gp.to_dataframe([place("Chain", pid="x"), place("Chain", pid="x")],
                             LAT, LON)
        assert len(df) == 1

    def test_missing_review_count_becomes_zero(self):
        p = place("NoReviews")
        del p["userRatingCount"]
        assert gp.to_dataframe([p], LAT, LON).iloc[0]["reviews"] == 0

    def test_missing_rating_is_kept_as_missing(self):
        p = place("Unrated")
        del p["rating"]
        assert pd.isna(gp.to_dataframe([p], LAT, LON).iloc[0]["rating"])

    def test_empty_input_returns_the_right_columns(self):
        df = gp.to_dataframe([], LAT, LON)
        assert df.empty and list(df.columns) == gp.COLUMNS


# --- the score --------------------------------------------------------------
class TestCompetitorStrength:
    def test_weights_are_fifty_thirty_twenty(self):
        best = pd.DataFrame([{"rating": 5.0, "reviews": 10_000, "distance_m": 0,
                              **{c: None for c in gp.COLUMNS
                                 if c not in ("rating", "reviews", "distance_m")}}])
        scored = gp.add_competitor_strength(best, radius=750)
        row = scored.iloc[0]
        assert row["rating_score"] == pytest.approx(50)
        assert row["review_score"] == pytest.approx(30)
        assert row["distance_score"] == pytest.approx(20)
        assert row["competitor_score"] == pytest.approx(100)

    def test_five_stars_from_four_reviews_loses_to_four_six_from_thousands(self):
        # The whole reason review volume is in the model.
        df = pd.DataFrame([
            {"rating": 5.0, "reviews": 4, "distance_m": 200},
            {"rating": 4.6, "reviews": 3_200, "distance_m": 200},
        ])
        scored = gp.add_competitor_strength(df, radius=750)
        assert scored.iloc[0]["reviews"] == 3_200

    def test_review_term_is_logarithmic_not_linear(self):
        df = pd.DataFrame([
            {"rating": 4.0, "reviews": 100, "distance_m": 0},
            {"rating": 4.0, "reviews": 1_000, "distance_m": 0},
            {"rating": 4.0, "reviews": 10_000, "distance_m": 0},
        ])
        s = gp.add_competitor_strength(df, radius=750).sort_values("reviews")
        gaps = s["review_score"].diff().dropna()
        # A tenfold jump adds a constant, not a proportional, amount.
        assert gaps.iloc[0] == pytest.approx(gaps.iloc[1], abs=0.1)

    def test_review_term_saturates(self):
        df = pd.DataFrame([{"rating": 0, "reviews": 10_000_000, "distance_m": 750}])
        assert gp.add_competitor_strength(df, 750).iloc[0]["review_score"] == pytest.approx(30)

    def test_proximity_decays_to_zero_at_the_radius(self):
        df = pd.DataFrame([{"rating": 0, "reviews": 0, "distance_m": 750}])
        assert gp.add_competitor_strength(df, 750).iloc[0]["distance_score"] == 0

    def test_unrated_place_scores_zero_on_rating(self):
        df = pd.DataFrame([{"rating": None, "reviews": 10, "distance_m": 100}])
        assert gp.add_competitor_strength(df, 750).iloc[0]["rating_score"] == 0

    @pytest.mark.parametrize("score_inputs,expected", [
        ({"rating": 5.0, "reviews": 5_000, "distance_m": 50}, "Strong"),
        ({"rating": 4.0, "reviews": 200, "distance_m": 400}, "Moderate"),
        ({"rating": 2.0, "reviews": 3, "distance_m": 700}, "Weak"),
    ])
    def test_bands(self, score_inputs, expected):
        df = pd.DataFrame([score_inputs])
        assert gp.add_competitor_strength(df, 750).iloc[0]["competitor_strength"] == expected

    def test_sorted_strongest_first(self):
        df = pd.DataFrame([
            {"rating": 2.0, "reviews": 5, "distance_m": 700},
            {"rating": 4.8, "reviews": 3_000, "distance_m": 100},
        ])
        scored = gp.add_competitor_strength(df, 750)
        assert scored["competitor_score"].is_monotonic_decreasing

    def test_empty_frame_returns_scored_columns(self):
        out = gp.add_competitor_strength(pd.DataFrame(), 750)
        assert out.empty and "competitor_score" in out.columns

    def test_score_never_leaves_zero_to_hundred(self):
        df = pd.DataFrame([
            {"rating": 9.9, "reviews": -50, "distance_m": -10},
            {"rating": -1, "reviews": 0, "distance_m": 99_999},
        ])
        scored = gp.add_competitor_strength(df, 750)
        assert scored["competitor_score"].between(0, 100).all()


# --- pressure ---------------------------------------------------------------
class TestPressure:
    def build(self, rows):
        return gp.add_competitor_strength(pd.DataFrame(rows), 750)

    def test_no_competitors_is_low(self):
        label, why = gp.classify_pressure(pd.DataFrame())
        assert label == "Low" and "no competing" in why

    def test_three_strong_competitors_is_high(self):
        strong = {"rating": 4.8, "reviews": 4_000, "distance_m": 100}
        label, why = gp.classify_pressure(self.build([strong] * 3))
        assert label == "High" and "3 or more strong" in why

    def test_many_competitors_with_one_strong_is_high(self):
        rows = [{"rating": 4.9, "reviews": 9_000, "distance_m": 50}]
        rows += [{"rating": 3.0, "reviews": 10, "distance_m": 700}] * 11
        label, _ = gp.classify_pressure(self.build(rows))
        assert label == "High"

    def test_a_couple_of_weak_competitors_is_low(self):
        rows = [{"rating": 3.0, "reviews": 5, "distance_m": 700}] * 2
        label, _ = gp.classify_pressure(self.build(rows))
        assert label == "Low"

    def test_in_between_is_moderate(self):
        # Middling on every term: ~60/100 each, so none reach Strong.
        rows = [{"rating": 4.0, "reviews": 60, "distance_m": 500}] * 5
        built = self.build(rows)
        assert (built["competitor_strength"] == "Moderate").all()
        assert gp.classify_pressure(built)[0] == "Moderate"

    def test_every_rule_carries_an_explanation(self):
        for label, rule, because in gp.PRESSURE_RULES:
            assert label in ("Low", "Moderate", "High") and because


# --- end to end -------------------------------------------------------------
def test_full_landscape_from_a_realistic_response():
    payload = {"places": [
        place("Carbone", lat=40.7275, lon=-73.9995, rating=4.7, reviews=3_482),
        place("Trattoria B", lat=40.7230, lon=-74.0010, rating=4.4, reviews=1_028),
        place("Small Cafe", lat=40.7250, lon=-73.9980, rating=3.5, reviews=76),
        place("Gone", status=gp.CLOSED_PERMANENTLY),
        place("Way Uptown", lat=40.8000, lon=-73.9500, rating=4.9, reviews=9_000),
    ]}
    result = gp.fetch_landscape(LAT, LON, "Italian", "KEY", radius=750,
                                session=FakeSession(FakeResponse(200, payload)))
    assert result.ok is True
    names = list(result.competitors["name"])
    assert "Gone" not in names and "Way Uptown" not in names
    assert names[0] == "Carbone"          # strongest first
    assert result.total == 3
    assert result.mean_rating == pytest.approx((4.7 + 4.4 + 3.5) / 3)
    assert result.strongest["name"] == "Carbone"


class TestStrongLabelFloor:
    def test_a_high_rating_on_eight_reviews_is_not_a_strong_competitor(self):
        df = pd.DataFrame([{"rating": 4.9, "reviews": 8, "distance_m": 100}])
        out = gp.add_competitor_strength(df, 750)
        assert out.iloc[0]["competitor_score"] > 70     # score untouched
        assert out.iloc[0]["competitor_strength"] == "Moderate"

    def test_twenty_reviews_is_enough_for_the_label(self):
        df = pd.DataFrame([{"rating": 4.9, "reviews": 20, "distance_m": 100}])
        assert gp.add_competitor_strength(df, 750).iloc[0][
            "competitor_strength"] == "Strong"

    def test_the_cap_never_promotes(self):
        df = pd.DataFrame([{"rating": 2.0, "reviews": 5, "distance_m": 700}])
        assert gp.add_competitor_strength(df, 750).iloc[0][
            "competitor_strength"] == "Weak"
