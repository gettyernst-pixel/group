"""Statistics, geometry, cuisine vocabulary, and the venue detector."""
import numpy as np
import pandas as pd
import pytest

from nycsiting.cuisines import clean_label, competitive_set, resolve
from nycsiting.geo import haversine_m, within_radius
from nycsiting.locations import _max_concurrent
from nycsiting.panel import _parse_dates
from nycsiting.stats import rate_differs, wilson_interval


class TestParseDates:
    def test_the_1900_sentinel_becomes_missing(self):
        # DOHMH writes 01/01/1900 for 'never inspected'. Read literally it
        # would hand those restaurants a 120-year lifespan.
        out = _parse_dates(pd.Series(["01/01/1900", "03/17/2025"]))
        assert pd.isna(out.iloc[0])
        assert out.iloc[1] == pd.Timestamp("2025-03-17")

    def test_unparseable_dates_become_missing_not_errors(self):
        assert pd.isna(_parse_dates(pd.Series(["not a date"])).iloc[0])


class TestGeo:
    def test_known_nyc_distance(self):
        # Empire State Building to Times Square, ~1.1km.
        d = haversine_m(40.748817, -73.985428, 40.758896, -73.985130)
        assert 1_100 < d < 1_200

    def test_radius_filter_keeps_only_what_is_inside(self):
        df = pd.DataFrame({"lat": [40.7200, 40.7205, 40.7500],
                           "lon": [-73.99, -73.99, -73.99]})
        assert len(within_radius(df, 40.72, -73.99, 400)) == 2

    def test_results_are_sorted_nearest_first(self):
        df = pd.DataFrame({"lat": [40.7205, 40.7200], "lon": [-73.99, -73.99]})
        out = within_radius(df, 40.72, -73.99, 1_000)
        assert out["distance_m"].is_monotonic_increasing

    def test_bounding_box_does_not_drop_points_due_east(self):
        # Due east is where the longitude box is tightest.
        east = -73.99 + 350 / (111_320 * np.cos(np.radians(40.72)))
        df = pd.DataFrame({"lat": [40.72], "lon": [east]})
        assert len(within_radius(df, 40.72, -73.99, 400)) == 1

    def test_empty_input_returns_empty_with_the_column(self):
        empty = pd.DataFrame({"lat": [], "lon": []})
        assert "distance_m" in within_radius(empty, 40.72, -73.99, 400).columns


class TestMaxConcurrent:
    """The check that separates a churning storefront from a food hall."""

    def test_sequential_tenants_never_overlap(self):
        first = pd.to_datetime(pd.Series(["2012-01-01", "2016-01-01", "2020-01-01"]))
        last = pd.to_datetime(pd.Series(["2015-01-01", "2019-01-01", "2023-01-01"]))
        assert _max_concurrent(first, last) == 1

    def test_a_food_hall_shows_many_at_once(self):
        first = pd.to_datetime(pd.Series(["2020-01-01"] * 8))
        last = pd.to_datetime(pd.Series(["2024-01-01"] * 8))
        assert _max_concurrent(first, last) == 8

    def test_partial_overlap_is_counted(self):
        first = pd.to_datetime(pd.Series(["2012-01-01", "2014-01-01"]))
        last = pd.to_datetime(pd.Series(["2015-01-01", "2018-01-01"]))
        assert _max_concurrent(first, last) == 2

    def test_undatable_restaurants_yield_zero_meaning_unknown(self):
        # Zero must be read as 'cannot tell', not 'no overlap' — Macy's has 39
        # food businesses and no usable dates.
        nat = pd.Series([pd.NaT, pd.NaT])
        assert _max_concurrent(nat, nat) == 0


class TestWilson:
    def test_no_data_is_maximally_uncertain(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_interval_narrows_as_the_sample_grows(self):
        small = wilson_interval(2, 5)
        large = wilson_interval(200, 500)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_stays_inside_zero_to_one_at_the_extremes(self):
        lo, hi = wilson_interval(5, 5)
        assert 0.0 <= lo <= hi <= 1.0


class TestRateDiffers:
    def test_three_of_five_cannot_beat_a_baseline(self):
        # A single address holds a handful of restaurants; the app must not
        # turn that into a finding.
        assert rate_differs(3, 5, 0.37) == "inconclusive"

    def test_a_large_clear_gap_is_detected(self):
        assert rate_differs(20, 200, 0.50) == "below"
        assert rate_differs(180, 200, 0.50) == "above"

    def test_no_observations_is_inconclusive(self):
        assert rate_differs(0, 0, 0.5) == "inconclusive"


class TestCuisines:
    def test_uninformative_labels_are_dropped(self):
        assert clean_label("Not Listed/Not Applicable") == ""
        assert clean_label("Other") == ""
        assert clean_label("Italian") == "Italian"

    def test_competitive_set_includes_substitutes(self):
        assert "Pizza" in competitive_set("Italian")
        assert "Thai" not in competitive_set("Italian")

    def test_competitive_set_is_symmetric(self):
        # The table is declared one-way; both directions must hold.
        assert "Mediterranean" in competitive_set("Greek")
        assert "Greek" in competitive_set("Mediterranean")

    def test_unlisted_cuisine_competes_only_with_itself(self):
        assert competitive_set("Czech") == {"Czech"}

    def test_everyday_words_resolve(self):
        known = {"Japanese", "Italian", "Mexican"}
        assert resolve("sushi", known) == "Japanese"
        assert resolve("ITALIAN", known) == "Italian"

    def test_ambiguous_input_resolves_to_nothing(self):
        # Guessing would produce a confident report about the wrong concept.
        assert resolve("zzz", {"Japanese", "Italian"}) is None
