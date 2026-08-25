"""How evidence becomes a score — and what happens when evidence is missing."""
import pytest

from nycsiting.geocode import _mismatch, has_house_number, split_query
from nycsiting.scoring import WEIGHTS, Component, combine


def comp(key, score, available=True):
    return Component(key, key.replace("_", " ").title(), score,
                     WEIGHTS[key], "evidence", available=available)


class TestCombine:
    def test_weighted_mean_over_available_components(self):
        result = combine([comp("competition", 100), comp("area_retention", 0)])
        expected = (100 * 20 + 0 * 15) / 35
        assert result["score"] == round(expected)

    def test_missing_components_are_excluded_not_scored_as_average(self):
        # Defaulting an unmeasured signal to 50 would let missing data
        # masquerade as a finding.
        with_gap = combine([comp("competition", 100),
                            comp("foot_traffic", None, available=False)])
        assert with_gap["score"] == 100
        assert len(with_gap["dropped"]) == 1

    def test_coverage_reports_how_much_evidence_was_available(self):
        full = combine([comp(k, 50) for k in WEIGHTS])
        assert full["coverage"] == pytest.approx(1.0)

        partial = combine([comp("competition", 50), comp("area_retention", 50)])
        assert partial["coverage"] == pytest.approx(35 / sum(WEIGHTS.values()))

    def test_nothing_measurable_yields_no_score(self):
        result = combine([comp("competition", None, available=False)])
        assert result["score"] is None
        assert result["band"] == "Not enough data"

    @pytest.mark.parametrize("score,band", [
        (10, "Lower risk"), (50, "Moderate risk"), (90, "Higher risk"),
    ])
    def test_bands(self, score, band):
        assert combine([comp("competition", score)])["band"] == band

    def test_band_boundaries_are_covered(self):
        # 100 must land somewhere rather than raising StopIteration.
        assert combine([comp("competition", 100)])["band"] == "Higher risk"
        assert combine([comp("competition", 0)])["band"] == "Lower risk"

    def test_headline_names_both_the_worst_and_best_signal(self):
        result = combine([comp("competition", 95), comp("area_retention", 5)])
        assert "competition" in result["headline"].lower()
        assert "retention" in result["headline"].lower()

    def test_headline_says_how_many_components_were_dropped(self):
        result = combine([comp("competition", 50),
                          comp("foot_traffic", None, available=False)])
        assert "could not be measured" in result["headline"]

    def test_contributions_sum_to_the_score(self):
        result = combine([comp("competition", 80), comp("area_retention", 20)])
        usable = [c for c in result["components"] if c["available"]]
        assert sum(c["contribution"] for c in usable) == pytest.approx(result["score"], abs=0.5)


class TestGeocodeGuards:
    @pytest.mark.parametrize("query,expected", [
        ("195 Bowery, Manhattan", True),
        ("136-20 38th Ave, Queens", True),
        ("Flushing Main Street, Queens", False),
        ("38th Avenue, Queens", False),
    ])
    def test_house_number_detection(self, query, expected):
        assert has_house_number(query) is expected

    def test_split_query_separates_street_from_borough(self):
        assert split_query("195 Bowery, Manhattan") == ("Bowery", "MN")
        assert split_query("136-20 38th Ave, Queens") == ("38th Ave", "QN")

    def test_a_different_borough_is_flagged(self):
        # The real failure: '999 Nowhere Road, Manhattan' silently resolves to
        # 999 Rutland Road in Brooklyn.
        warning = _mismatch("999 Nowhere Road, Manhattan",
                            {"street": "RUTLAND ROAD", "borough": "Brooklyn",
                             "label": "999 RUTLAND ROAD, Brooklyn"})
        assert warning is not None and "Brooklyn" in warning

    def test_a_different_street_is_flagged(self):
        warning = _mismatch("12345 Fakestreet Blvd, Brooklyn",
                            {"street": "SPRINGFIELD BOULEVARD", "borough": "Brooklyn",
                             "label": "12345 SPRINGFIELD BOULEVARD"})
        assert warning is not None and "Fakestreet" in warning

    def test_shared_street_type_does_not_hide_a_wrong_street(self):
        # 'Nowhere Road' and 'Rutland Road' share ROAD; the names do not.
        warning = _mismatch("999 Nowhere Road, Manhattan",
                            {"street": "RUTLAND ROAD", "borough": "Manhattan",
                             "label": "999 RUTLAND ROAD"})
        assert warning is not None

    @pytest.mark.parametrize("query,street,borough", [
        ("42 Broadway, Manhattan", "B'WAY", "Manhattan"),
        ("1 Fort Washington Ave, Manhattan", "FT WASHINGTON AVENUE", "Manhattan"),
        ("350 5th Ave, Manhattan", "5 AVENUE", "Manhattan"),
        ("195 Bowery, Manhattan", "BOWERY", "Manhattan"),
    ])
    def test_geosearch_abbreviations_are_not_false_positives(self, query, street, borough):
        assert _mismatch(query, {"street": street, "borough": borough,
                                 "label": query}) is None


class TestSampleSizeGuards:
    """The audit's H2/H3: no strong verdicts from tiny samples."""

    def _report(self, loc_ever=0, loc_closed=0, same_surv=0, same_tot=0):
        import pandas as pd
        return {"query": {"cuisine": "Italian", "radius_m": 500},
                "location": {"is_multi_vendor": False,
                             "restaurants_ever": loc_ever,
                             "closed_here": loc_closed,
                             "occupancy": pd.DataFrame(), "cuisines_here": [],
                             "same_cuisine_here": pd.DataFrame(),
                             "cohort": {"survived": 0, "total": 0, "rate": None},
                             "row": None},
                "area": {"competitive_set": ["Italian"],
                         "active_competitors": 10, "active_all": 100,
                         "active_same_cuisine": 5, "all": pd.DataFrame(),
                         "competitors": pd.DataFrame(),
                         "same_cuisine": pd.DataFrame(),
                         "cohort": {"survived": 200, "total": 500,
                                    "rate": 0.4, "ci": (0, 1)},
                         "cohort_same_cuisine": {
                             "survived": same_surv, "total": same_tot,
                             "rate": (same_surv / same_tot) if same_tot else None,
                             "ci": (0, 1)}},
                "city": {"cohort": {"survived": 9700, "total": 26500,
                                    "rate": 0.366},
                         "cohort_same_cuisine": {"survived": 515,
                                                 "total": 1052, "rate": 0.49}},
                "comparisons": []}

    def _score(self, **kw):
        import pandas as pd
        from nycsiting.scoring import score_site
        empty = pd.DataFrame({"seen_2026": [], "cuisine": [], "lat": [], "lon": []})
        return score_site(self._report(**kw), empty, None, None, 500)

    def _component(self, result, key):
        return next(c for c in result["components"] if c["key"] == key)

    def test_one_closed_restaurant_is_not_high_turnover(self):
        # The "one Italian restaurant closed -> location kills restaurants"
        # fallacy, verbatim from the audit.
        c = self._component(self._score(loc_ever=1, loc_closed=1),
                            "location_history")
        assert c["available"] is False
        assert "too little history" in c["evidence"]

    def test_three_or_more_restaurants_do_get_scored(self):
        c = self._component(self._score(loc_ever=5, loc_closed=4),
                            "location_history")
        assert c["available"] is True and c["score"] > 65

    def test_two_cuisine_survivors_do_not_make_a_strong_verdict(self):
        c = self._component(self._score(same_surv=2, same_tot=2),
                            "cuisine_track_record")
        assert c["available"] is True
        assert c["score"] == 50.0
        assert "sampling noise" in c["evidence"]

    def test_two_cuisine_closures_do_not_make_a_weak_verdict(self):
        c = self._component(self._score(same_surv=0, same_tot=2),
                            "cuisine_track_record")
        assert c["score"] == 50.0

    def test_a_real_sample_is_still_scored_on_its_gap(self):
        c = self._component(self._score(same_surv=27, same_tot=41),
                            "cuisine_track_record")
        assert c["score"] < 35          # 66% vs 49% citywide, n=41: favourable
        assert "exceeds the margin of error" in c["evidence"]

    def test_scoring_agrees_with_the_comparison_cards(self):
        # H3 was the two layers disagreeing; pin the agreement.
        from nycsiting.stats import rate_differs
        for surv, tot in [(2, 2), (0, 2), (27, 41), (5, 8)]:
            c = self._component(self._score(same_surv=surv, same_tot=tot),
                                "cuisine_track_record")
            card = rate_differs(surv, tot, 0.49)
            if card == "inconclusive":
                assert c["score"] == 50.0
            else:
                assert c["score"] != 50.0
