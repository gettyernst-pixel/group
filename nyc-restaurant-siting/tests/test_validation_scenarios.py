"""
Final validation (audit item: three realistic scenarios, full chain).

Each test traces INPUT -> RAW EVIDENCE -> DERIVED METRICS -> OUTPUT for one
real address and asks the audit's closing question: does the recommendation
logically follow from the evidence? "Follow" is asserted structurally — every
claim in the output must be traceable to a number in the evidence, and the
direction of the verdict must match the direction of the numbers.
"""
import pandas as pd
import pytest

from nycsiting import analysis, config, narrative
from nycsiting.scoring import score_site

pytestmark = pytest.mark.skipif(
    not config.RESTAURANTS_PQ.exists(),
    reason="processed data not built; run `python build_data.py`")


@pytest.fixture(scope="module")
def data():
    panel = pd.read_parquet(config.RESTAURANTS_PQ)
    for c in ("first_observed", "last_observed"):
        panel[c] = pd.to_datetime(panel[c])
    locs = pd.read_parquet(config.LOCATIONS_PQ)
    return panel, locs


def chain(data, lat, lon, cuisine, key=None):
    panel, locs = data
    report = analysis.site_report(panel, locs, lat, lon, cuisine, 500, key)
    result = score_site(report, panel, None, None, 500)
    verdicts = narrative.component_verdicts(result)
    fit = narrative.fit_score(result)
    return report, result, verdicts, fit


def test_dense_manhattan_corner_is_internally_consistent(data):
    """195 Bowery / Italian: crowded but the cuisine survives well here."""
    report, result, verdicts, fit = chain(data, 40.720842, -73.993431, "Italian",
                                          "MN|195|BOWERY")
    v = {x["key"]: x for x in verdicts}

    # RAW EVIDENCE: audit-verified numbers (independent recount matched).
    assert report["area"]["active_all"] == 580
    assert report["area"]["active_same_cuisine"] == 45

    # DERIVED: competition verdict direction must match the raw density.
    assert v["competition"]["tone"] == "concern"
    # Italian cohort here (27/41 vs 49% citywide) is favourable and n passes
    # the Wilson gate, so the verdict may claim it.
    same = report["area"]["cohort_same_cuisine"]
    assert same["total"] >= 30 and same["rate"] > 0.6
    assert v["cuisine_track_record"]["tone"] == "good"

    # OUTPUT: with real evidence both ways the band cannot be an extreme.
    assert narrative.fit_band(fit) in ("Mixed", "Promising", "Higher risk")
    line = narrative.headline(verdicts, fit, "Italian")
    assert "competition" in line.lower()


def test_history_direction_matches_the_ratio_it_summarises(data):
    """
    Two real addresses, same machinery, opposite evidence.

    42 Broadway: 30 tenants, 7 gone — BELOW the citywide closure base rate, so
    the app must read it as stable (the audit's first draft of this test
    assumed 30 tenants meant churn, and the data corrected it).
    348 Bowery: 5 tenants, 4 gone — genuine churn, and the fit must be lower
    than the identical query with the history unknown.
    """
    _, _, verdicts_stable, fit_stable = chain(
        data, 40.7060, -74.0132, "Italian", "MN|42|BROADWAY")
    v_stable = {x["key"]: x for x in verdicts_stable}
    # 7/30 gone reads neutral-to-good under the stated rule (raw ratio plus a
    # bump for repeat departures); what it must never read is "concern".
    assert v_stable["location_history"]["tone"] in ("good", "neutral")
    assert "30" in v_stable["location_history"]["evidence"]

    _, _, verdicts_churn, fit_churn = chain(
        data, 40.726484, -73.991770, "Italian", "MN|348|BOWERY")
    _, _, _, fit_nohist = chain(data, 40.726484, -73.991770, "Italian", None)
    v_churn = {x["key"]: x for x in verdicts_churn}
    assert v_churn["location_history"]["tone"] == "concern"
    assert fit_churn < fit_nohist


def test_sparse_address_declines_rather_than_guesses(data):
    """A residential Staten Island street: the honest answer is 'not enough'."""
    report, result, verdicts, fit = chain(data, 40.5740, -74.1120, "Thai")
    v = {x["key"]: x for x in verdicts}

    # RAW: verify it really is sparse before asserting on the handling.
    assert report["area"]["cohort"]["total"] < 30

    # DERIVED: no strong cuisine claims can exist here.
    assert v["cuisine_track_record"]["verdict"] in ("Not measured", "Average")
    assert v["location_history"]["verdict"] == "Not measured"

    # OUTPUT: evidence quality must say Limited out loud.
    label, reasons = narrative.evidence_quality(result, report, None)
    assert label == "Limited"
    assert any("only" in r for r in reasons)
