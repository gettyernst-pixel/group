"""
The area-intelligence derivations. Synthetic panels throughout, plus a few
real-data invariants at the end.
"""
import numpy as np
import pandas as pd
import pytest

from nycsiting import areas, geometry


def make_panel(rows):
    """rows: (camis, cuisine, seen_2017, seen_2026, lat, lon)"""
    df = pd.DataFrame(rows, columns=["camis", "cuisine", "seen_2017",
                                     "seen_2026", "lat", "lon"])
    df["status"] = np.where(df.seen_2026, "active",
                            np.where(df.seen_2017, "closed", "unknown"))
    return df


def assign_all(panel, code="AA01"):
    return pd.Series([code] * len(panel), index=panel["camis"], name="nta_2020")


def cohort_rows(code_n, survived, cuisine="Italian", start=0):
    rows = []
    for i in range(code_n):
        rows.append((f"C{start+i}", cuisine, True, i < survived, 40.7, -73.9))
    return rows


class TestGeometry:
    def test_wkt_roundtrip_square(self):
        wkt = "MULTIPOLYGON (((0 0, 4 0, 4 4, 0 4, 0 0)))"
        polys = geometry.parse_wkt_multipolygon(wkt)
        assert len(polys) == 1 and len(polys[0][0]) == 5

    def test_point_in_polygon_with_hole(self):
        polys = geometry.parse_wkt_multipolygon(
            "MULTIPOLYGON (((0 0, 10 0, 10 10, 0 10, 0 0),"
            "(4 4, 6 4, 6 6, 4 6, 4 4)))")
        assert geometry.point_in_multipolygon(2, 2, polys)
        assert not geometry.point_in_multipolygon(5, 5, polys)   # in the hole
        assert not geometry.point_in_multipolygon(11, 5, polys)

    def test_real_index_locates_known_points(self):
        idx = geometry.NTAIndex()
        assert idx.locate(40.720842, -73.993431) == "MN0302"   # 195 Bowery
        assert idx.locate(40.75, -74.02) is None               # Hudson River


class TestFeatures:
    def test_camis_deduplicated_by_construction(self):
        # Two establishments, one address — counts are establishments.
        panel = make_panel(cohort_rows(30, 20))
        features = areas.area_features(panel, assign_all(panel))
        assert features.loc["AA01", "restaurants_ever"] == 30
        assert features.loc["AA01", "cohort_n"] == 30
        assert features.loc["AA01", "cohort_survived"] == 20

    def test_density_counts_establishments_not_rows(self):
        panel = make_panel(cohort_rows(10, 10))
        dens = areas.restaurant_density_by_cuisine(
            panel, assign_all(panel), "Italian")
        assert dens.loc["AA01", "active_same"] == 10


class TestConceptFit:
    def test_small_sample_is_limited_evidence_not_a_verdict(self):
        panel = make_panel(cohort_rows(5, 5) + cohort_rows(200, 100, start=500))
        assignment = pd.Series(
            ["AA01"] * 5 + ["BB01"] * 200,
            index=panel["camis"], name="nta_2020")
        fit = areas.area_concept_fit(panel, assignment, "Italian")
        assert fit.loc["AA01", "band"] == "Limited evidence"
        assert pd.isna(fit.loc["AA01", "fit_index"])

    def test_no_success_probability_language(self):
        import inspect
        source = inspect.getsource(areas)
        for banned in ("success probability", "chance of survival",
                       "expected success"):
            assert banned not in source.lower()

    def test_distinguishably_better_area_reads_strong(self):
        # Area AA: 40/40 survive; city baseline dragged down by BB: 10/160.
        panel = make_panel(cohort_rows(40, 40)
                           + cohort_rows(160, 10, start=500))
        assignment = pd.Series(["AA01"] * 40 + ["BB01"] * 160,
                               index=panel["camis"], name="nta_2020")
        fit = areas.area_concept_fit(panel, assignment, "Italian")
        assert fit.loc["AA01", "band"] == "Strong"
        assert fit.loc["BB01", "band"] == "Mixed"


class TestSaturationAndGap:
    def test_missing_competition_is_not_low_competition(self):
        out = areas.competitor_saturation(0, None)
        assert out["band"] is None
        gap = areas.opportunity_gap("Strong", out["band"])
        assert gap["band"] == "Insufficient evidence"

    def test_high_fit_low_competition_beats_high_fit_high_competition(self):
        order = {"High": 3, "Moderate": 2, "Low": 1,
                 "Insufficient evidence": 0}
        low = areas.opportunity_gap("Strong", "Low")["band"]
        high = areas.opportunity_gap("Strong", "High")["band"]
        assert order[low] > order[high]

    def test_limited_fit_evidence_never_produces_a_gap_claim(self):
        assert areas.opportunity_gap("Limited evidence", "Low")["band"] == \
            "Insufficient evidence"

    def test_every_gap_traces_to_component_bands(self):
        for fit in ("Strong", "Promising", "Mixed"):
            for sat in ("Low", "Moderate", "High"):
                out = areas.opportunity_gap(fit, sat)
                assert out["band"] in areas.GAP_BANDS
                assert fit.lower() in out["reason"]

    def test_strong_nearby_competitors_raise_saturation(self):
        base = areas.competitor_saturation(3, 30.0)
        with_strong = areas.competitor_saturation(3, 30.0, strong_nearby=4)
        assert base["band"] == "Low" and with_strong["band"] == "High"


class TestTurnover:
    def test_unknown_area_is_limited_evidence_not_high_risk(self):
        panel = make_panel(cohort_rows(3, 1))
        features = areas.area_features(panel, assign_all(panel))
        turn = areas.area_turnover_context(features, panel)
        assert turn.loc["AA01", "band"] == "Limited evidence"

    def test_wording_is_observed_turnover_never_failure_rate(self):
        assert all("failure" not in band.lower()
                   for band in areas.TURNOVER_BANDS)


class TestEvidence:
    def test_missing_data_lowers_evidence_not_fit(self):
        panel = make_panel(cohort_rows(3, 2))
        features = areas.area_features(panel, assign_all(panel))
        evidence = areas.evidence_quality_by_area(features, None)
        assert evidence.loc["AA01", "band"] == "Limited"
        # and the fit for the same tiny area declines to score at all
        fit = areas.area_concept_fit(panel, assign_all(panel), "Italian")
        assert fit.loc["AA01", "band"] == "Limited evidence"

    def test_full_evidence_reads_high(self):
        panel = make_panel(cohort_rows(60, 40))
        features = areas.area_features(panel, assign_all(panel))
        acs = pd.Series({"AA01": True})
        assert areas.evidence_quality_by_area(features, acs).loc[
            "AA01", "band"] == "High"


class TestConceptRanking:
    def _two_cuisine_panel(self):
        rows = (cohort_rows(200, 160, "Japanese")
                + cohort_rows(200, 60, "Caribbean", start=500)
                + cohort_rows(4, 4, "Afghan", start=900))
        return make_panel(rows)

    def test_ranking_respects_minimum_samples(self, monkeypatch):
        monkeypatch.setattr(areas, "MIN_CITYWIDE_CUISINE", 50)
        panel = self._two_cuisine_panel()
        ranking = areas.rank_concepts_for_area(
            panel, assign_all(panel), "AA01")
        names = [r["cuisine"] for r in ranking]
        assert "Afghan" not in names          # below citywide minimum
        assert names[0] == "Japanese"         # best survival ranks first

    def test_comparison_uses_the_same_formulas(self, monkeypatch):
        monkeypatch.setattr(areas, "MIN_CITYWIDE_CUISINE", 50)
        panel = self._two_cuisine_panel()
        assignment = assign_all(panel)
        table = areas.compare_concepts(panel, assignment, "AA01",
                                       ["Japanese", "Caribbean"])
        fit = areas.area_concept_fit(panel, assignment, "Japanese")
        assert table.loc["Fit", "Japanese"] == \
            f"{fit.loc['AA01', 'fit_index']:.0f}"

    def test_compare_locations_fills_gaps_with_dashes(self):
        rows = [dict(label="A", Fit=72, Competition="High"),
                dict(label="B", Fit=61)]
        table = areas.compare_locations(rows)
        assert table.loc["Competition", "B"] == "—"
