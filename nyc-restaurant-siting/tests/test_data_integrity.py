"""
Invariants of the BUILT data, not of the code that builds it.

WHY THIS FILE EXISTS
The 2026-08-25 audit repaired DOHMH's cuisine-vocabulary change: eleven
labels exist only in the 2011-17 archive, so a renamed category left every
closure under the legacy label and every survivor under the new one — the
legacy label read 0% cohort survival and its successor 100%, both pure
taxonomy artifacts. The repair lives in cuisines.clean_label and is applied
while the panel is built.

The final audit found the repair ABSENT FROM THE SHIPPED DATA: processed/
still held a panel built before the fix, so 'Asian' showed 0/280 survival
and 'Asian/Asian Fusion' 84/84, and both were selectable concepts. The
entire test suite passed against that panel, because every test exercised
the code and none checked the artifact it produces.

These tests read the built parquet. They fail if the data is stale, however
correct the code is.
"""
from __future__ import annotations

import pandas as pd
import pytest

from nycsiting import config, cuisines

pytest.importorskip("streamlit")

NEEDS_DATA = pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                                reason="processed data not built")


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    return pd.read_parquet(config.RESTAURANTS_PQ)


@NEEDS_DATA
def test_no_archive_only_cuisine_label_survives_in_the_panel(panel):
    """
    Every label DOHMH retired must already be mapped to its successor in
    the built data. One that survives splits a cuisine's cohort in two and
    makes both halves lie in opposite directions.
    """
    present = set(panel["cuisine"].unique())
    leaked = sorted(present & set(cuisines.DOHMH_2017_TO_2026))
    assert not leaked, (
        f"{len(leaked)} retired label(s) still in processed/: {leaked}. "
        "The panel predates cuisines.DOHMH_2017_TO_2026 — rerun "
        "`python build_data.py`.")


@NEEDS_DATA
def test_every_offered_concept_is_a_current_vocabulary_label(panel):
    """
    Whatever the concept picker offers, a user can analyse. A retired or
    mojibake label reaching that list is a defect the user sees directly.
    """
    import app as app_mod

    offered = app_mod.cuisine_options(panel)
    retired = [c for c in offered if c in cuisines.DOHMH_2017_TO_2026]
    assert not retired, f"retired labels offered as concepts: {retired}"
    mojibake = [c for c in offered if "Ã" in c or "â€" in c]
    assert not mojibake, f"encoding-damaged labels offered: {mojibake}"


@NEEDS_DATA
def test_no_cuisine_cohort_is_wholly_dead(panel):
    """
    A whole cohort dying is the DANGEROUS half of a vocabulary split, and
    the only half nothing else catches.

    When a label is retired, its closures keep the old name and its
    survivors get the new one: the old label reads 0% and the new one 100%.
    The 100% side is caught at presentation (app._label_artifact fires at a
    baseline of >=0.999 and neutralises the fit). The 0% side is not — a
    baseline of 0.0 looks like an ordinary, catastrophic result, and would
    be shown to a user as a genuine local track record for that concept.
    """
    cohort = panel[panel["seen_2017"]]
    rates = cohort.groupby("cuisine")["seen_2026"].agg(["sum", "size"])
    meaningful = rates[rates["size"] >= 30]
    rate = meaningful["sum"] / meaningful["size"]
    wiped_out = sorted(rate[rate == 0.0].index)
    assert not wiped_out, (
        f"cohorts with no survivors at all: {wiped_out} — the hallmark of a "
        "retired label, and the presentation guard does not catch this side")


@NEEDS_DATA
def test_every_fully_surviving_cohort_is_a_genuinely_new_2026_label(panel):
    """
    The other half of the same split. A 100% cohort is legitimate ONLY for a
    category DOHMH introduced in 2026 — its carriers are seen_2026 by
    construction. Any OTHER label at 100% is an unrepaired rename, and the
    fit must be neutralised rather than read as a perfect track record.
    """
    import app as app_mod

    cohort = panel[panel["seen_2017"]]
    rates = cohort.groupby("cuisine")["seen_2026"].agg(["sum", "size"])
    meaningful = rates[rates["size"] >= 30]
    rate = meaningful["sum"] / meaningful["size"]
    for label in rate[rate == 1.0].index:
        assert app_mod._label_artifact({"baseline_rate": float(rate[label])}), (
            f"{label!r} shows 100% survival but is not flagged as a label "
            "artifact, so it would be presented as a real track record")


@NEEDS_DATA
def test_citywide_cohort_survival_is_unchanged(panel):
    """
    The vocabulary repair renames labels; it must never add, drop or
    reclassify an establishment. This is the invariant that proves it.
    """
    cohort = panel[panel["seen_2017"]]
    survived = int(cohort["seen_2026"].sum())
    assert (survived, len(cohort)) == (9723, 26505), (
        f"citywide 2011-17 cohort moved to {survived}/{len(cohort)} — "
        "expected 9,723/26,505")


@NEEDS_DATA
def test_panel_is_one_row_per_establishment(panel):
    assert len(panel) == panel["camis"].nunique() == 48101


@NEEDS_DATA
def test_no_fabricated_coordinates(panel):
    """0,0 is DOHMH's 'could not geocode', not a place in the Atlantic."""
    assert int(((panel["lat"] == 0) & (panel["lon"] == 0)).sum()) == 0
    placed = panel.dropna(subset=["lat", "lon"])
    outside = ~(placed["lat"].between(40.4, 41.0)
                & placed["lon"].between(-74.3, -73.6))
    assert int(outside.sum()) == 0
    # and the honest coverage figure, not one inflated by fake placements
    assert 0.915 <= len(placed) / len(panel) <= 0.925


@NEEDS_DATA
def test_cached_area_assignment_matches_the_current_panel(panel):
    """
    The restaurant -> neighbourhood join is cached on disk. It must be
    invalidated by WHICH restaurants are placed, not merely how many: a
    rebuild that geocodes one new restaurant and loses another keeps the
    count identical while both assignments are wrong.
    """
    from nycsiting import geometry

    placed = panel[panel["lat"].notna()]
    if not geometry.ASSIGNMENT_CACHE.exists():
        pytest.skip("assignment cache not built")
    stored = pd.read_parquet(geometry.ASSIGNMENT_CACHE)
    assert set(stored["camis"]) == set(placed["camis"]), (
        "the cached area assignment is for a different set of restaurants "
        "than the current panel")


@NEEDS_DATA
def test_a_count_preserving_panel_change_invalidates_the_assignment_cache():
    """The guard itself: swap one CAMIS, keep the count, expect a rebuild."""
    from nycsiting import geometry

    frame = pd.DataFrame({"camis": ["A", "B"], "lat": [40.72, 40.73],
                          "lon": [-73.99, -73.98]})
    calls = {"n": 0}

    class CountingIndex:
        def locate(self, lat, lon):
            calls["n"] += 1
            return "MN0101"

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "assignment.parquet"
        geometry.assign_restaurants(frame, CountingIndex(), cache=cache)
        first = calls["n"]
        geometry.assign_restaurants(frame, CountingIndex(), cache=cache)
        assert calls["n"] == first, "an unchanged panel must reuse the cache"
        swapped = frame.assign(camis=["A", "C"])   # same length, new member
        geometry.assign_restaurants(swapped, CountingIndex(), cache=cache)
        assert calls["n"] > first, (
            "a count-preserving change must invalidate the cache")


@NEEDS_DATA
def test_built_data_is_not_older_than_the_code_that_defines_it():
    """
    A stale parquet is invisible to every other test in this suite: the code
    is right, the tests pass, and the app serves data built by an earlier
    version. This is the check that catches it.
    """
    build_inputs = [config.APP_DIR / "nycsiting" / name
                    for name in ("cuisines.py", "panel.py", "locations.py",
                                 "normalize.py")]
    built = config.RESTAURANTS_PQ.stat().st_mtime
    stale = [p.name for p in build_inputs
             if p.exists() and p.stat().st_mtime > built]
    assert not stale, (
        f"processed/restaurants.parquet predates {stale} — "
        "rerun `python build_data.py`")
