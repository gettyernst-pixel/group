"""
The plain-English layer.

Wording that contradicts the numbers it summarises is a real defect, not a
cosmetic one — these tests exist to catch exactly that.
"""
import pytest

from nycsiting import narrative
from nycsiting.scoring import WEIGHTS, Component, combine


def comp(key, score, available=True):
    label = key.replace("_", " ").title()
    return Component(key, label, score, WEIGHTS[key],
                     f"evidence for {key}", available=available)


def result(**scores):
    """A scoring result with the named components at the given risk scores."""
    return combine([comp(k, v) for k, v in scores.items()])


class TestFitScore:
    def test_fit_is_the_inverse_of_risk(self):
        assert narrative.fit_score({"score": 25}) == 75
        assert narrative.fit_score({"score": 80}) == 20

    def test_no_score_stays_absent(self):
        assert narrative.fit_score({"score": None}) is None

    @pytest.mark.parametrize("fit,band", [
        (95, "Strong fit"), (80, "Strong fit"), (70, "Promising"),
        (50, "Mixed"), (35, "Higher risk"), (10, "High risk"), (0, "High risk"),
    ])
    def test_bands(self, fit, band):
        assert narrative.fit_band(fit) == band

    def test_missing_fit_says_so_rather_than_guessing(self):
        assert narrative.fit_band(None) == "Not enough data"

    def test_a_low_risk_site_reads_as_a_good_fit(self):
        # The whole point of inverting: users read a high number as good.
        low_risk = result(competition=10, area_retention=10)
        assert narrative.fit_band(narrative.fit_score(low_risk)) == "Strong fit"


class TestComponentVerdicts:
    def test_each_component_gets_words_suited_to_it(self):
        verdicts = narrative.component_verdicts(
            result(competition=90, cuisine_track_record=10))
        by_key = {v["key"]: v for v in verdicts}
        # Competition reads low/high; cuisine reads weak/strong.
        assert by_key["competition"]["verdict"] == "High"
        assert by_key["cuisine_track_record"]["verdict"] == "Strong"

    def test_high_risk_never_reads_as_favourable(self):
        for key in narrative.COMPONENTS:
            verdict = narrative.component_verdicts(result(**{key: 95}))[0]
            assert verdict["tone"] == "concern"

    def test_low_risk_never_reads_as_a_concern(self):
        for key in narrative.COMPONENTS:
            verdict = narrative.component_verdicts(result(**{key: 5}))[0]
            assert verdict["tone"] == "good"

    def test_foot_traffic_high_risk_means_low_traffic(self):
        # The one component where the risk direction inverts in plain English.
        verdict = narrative.component_verdicts(result(foot_traffic=95))[0]
        assert verdict["verdict"] == "Low"
        verdict = narrative.component_verdicts(result(foot_traffic=5))[0]
        assert verdict["verdict"] == "High"

    def test_unmeasured_components_are_shown_not_hidden(self):
        # Dropping the row would make the evidence look more complete than it is.
        res = combine([comp("competition", 50),
                       comp("foot_traffic", None, available=False)])
        verdicts = narrative.component_verdicts(res)
        missing = [v for v in verdicts if v["key"] == "foot_traffic"][0]
        assert missing["verdict"] == "Not measured"
        assert missing["available"] is False

    def test_every_component_carries_its_user_question(self):
        for v in narrative.component_verdicts(result(**{k: 50 for k in WEIGHTS})):
            assert v["question"].endswith("?")

    def test_thresholds_agree_with_the_scoring_bands(self):
        # narrative must never call something 'good' that scoring calls risky.
        from nycsiting.scoring import BANDS
        lower_risk_ceiling = BANDS[0][1]
        assert narrative.GOOD_BELOW == lower_risk_ceiling


class TestHeadline:
    def test_names_both_sides_when_evidence_is_mixed(self):
        verdicts = narrative.component_verdicts(
            result(foot_traffic=5, competition=95))
        line = narrative.headline(verdicts, 55, "Italian")
        assert "offset by" in line
        assert "footfall" in line and "competition" in line

    def test_all_good_says_nothing_counts_against(self):
        verdicts = narrative.component_verdicts(
            result(competition=10, foot_traffic=10))
        line = narrative.headline(verdicts, 88, "Italian")
        assert "nothing in the data counting against" in line
        assert "Italian" in line

    def test_all_bad_says_nothing_offsets_it(self):
        verdicts = narrative.component_verdicts(
            result(competition=95, foot_traffic=95))
        assert "nothing" in narrative.headline(verdicts, 12, "Italian").lower()

    def test_middling_evidence_does_not_invent_a_verdict(self):
        verdicts = narrative.component_verdicts(result(competition=50))
        line = narrative.headline(verdicts, 50, "Italian")
        assert "strongly distinguishes" in line

    def test_no_score_produces_no_claim(self):
        assert "not enough" in narrative.headline([], None, "Italian").lower()

    def test_headline_agrees_with_the_verdict_rows(self):
        # A sentence praising something the rows call a concern is a real bug.
        verdicts = narrative.component_verdicts(
            result(competition=95, area_retention=8))
        line = narrative.headline(verdicts, 45, "Italian")
        assert "crowded competition" in line
        assert "crowded competition" not in line.split("offset by")[0]


class TestReasons:
    def test_reason_to_proceed_is_the_strongest_favourable_component(self):
        verdicts = narrative.component_verdicts(
            result(foot_traffic=30, area_retention=5))
        reason = narrative.reason_to_proceed(verdicts)
        assert "Area track record" in reason["title"]

    def test_no_favourable_component_means_no_reason_to_proceed(self):
        verdicts = narrative.component_verdicts(result(competition=95))
        assert narrative.reason_to_proceed(verdicts) is None

    def test_reason_for_caution_is_the_strongest_concern(self):
        verdicts = narrative.component_verdicts(
            result(competition=70, location_history=95))
        assert "Location history" in narrative.reason_for_caution(verdicts)["title"]

    def test_live_competition_outranks_historical_components(self):
        # Someone about to sign a lease should hear about rivals trading NOW
        # before anything derived from a decade-old archive.
        class Landscape:
            ok, strong, radius_m, total = True, 4, 750, 12

        verdicts = narrative.component_verdicts(result(location_history=95))
        reason = narrative.reason_for_caution(verdicts, Landscape())
        assert "4 strong competitors" in reason["title"]

    def test_weak_live_competition_does_not_override(self):
        class Landscape:
            ok, strong, radius_m, total = True, 1, 750, 3

        verdicts = narrative.component_verdicts(result(location_history=95))
        assert "Location history" in narrative.reason_for_caution(
            verdicts, Landscape())["title"]

    def test_unavailable_google_is_simply_ignored(self):
        class Landscape:
            ok, strong, radius_m, total = False, 0, 750, 0

        verdicts = narrative.component_verdicts(result(competition=90))
        assert narrative.reason_for_caution(verdicts, Landscape()) is not None


class TestAssessmentLabel:
    def test_plain_band_when_google_is_quiet(self):
        assert narrative.assessment_label(70) == "Promising"

    def test_qualified_when_a_good_score_meets_strong_rivals(self):
        class Landscape:
            ok, strong, radius_m, total = True, 3, 750, 12

        assert narrative.assessment_label(70, Landscape()) == \
            "Promising — with competitive risk"

    def test_a_poor_score_is_not_further_qualified(self):
        class Landscape:
            ok, strong, radius_m, total = True, 5, 750, 20

        assert narrative.assessment_label(20, Landscape()) == "High risk"


class TestComparison:
    def test_row_carries_the_headline_numbers(self):
        verdicts = narrative.component_verdicts(
            result(competition=90, cuisine_track_record=10))
        row = narrative.comparison_row("42 Broadway", "Italian", 61, verdicts)
        assert row["Location"] == "42 Broadway"
        assert row["Overall fit"] == 61
        assert row["Competition (history)"] == "High"
        assert row["Cuisine performance"] == "Strong"

    def test_google_columns_appear_only_when_google_answered(self):
        verdicts = narrative.component_verdicts(result(competition=50))
        assert "Strong rivals" not in narrative.comparison_row(
            "X", "Italian", 50, verdicts)

        class Landscape:
            ok, strong, radius_m, total = True, 2, 750, 9

        row = narrative.comparison_row("X", "Italian", 50, verdicts, Landscape())
        assert row["Strong rivals"] == 2 and row["Competitors now"] == 9

    def test_missing_fit_does_not_render_as_none(self):
        row = narrative.comparison_row("X", "Italian", None, [])
        assert row["Overall fit"] == "—"


class TestEvidenceQuality:
    def _report(self, area_n):
        return {"area": {"cohort": {"total": area_n}}}

    def test_full_evidence_reads_strong(self):
        class Landscape:
            ok = True
        label, reasons = narrative.evidence_quality(
            {"coverage": 1.0}, self._report(400), Landscape())
        assert label == "Strong" and len(reasons) == 3

    def test_sparse_everything_reads_limited(self):
        label, reasons = narrative.evidence_quality(
            {"coverage": 0.5}, self._report(12), None)
        assert label == "Limited"
        assert any("only" in r for r in reasons)

    def test_middle_reads_moderate(self):
        label, _ = narrative.evidence_quality(
            {"coverage": 0.9}, self._report(400), None)
        assert label == "Moderate"

    def test_reasons_always_name_all_three_checks(self):
        _, reasons = narrative.evidence_quality(
            {"coverage": 0.0}, self._report(0), None)
        joined = " ".join(reasons)
        assert "evidence weight" in joined
        assert "cohort" in joined
        assert "live competitor" in joined
