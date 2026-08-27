"""
The competitor-reference memo (v9 release audit).

WHAT THIS PROTECTS
scoring.competitor_reference builds the distribution of "how many
competitors does a typical member of this concept sit among" by running
1200 radius queries. That distribution describes the CONCEPT, not the site
being scored — it depends only on the panel, the competitive set and the
radius, and with a fixed seed it is deterministic.

Measured during the release audit, uncached: 1114ms on EVERY score. A
radius change cost 1382ms end to end, and comparing three addresses of the
same cuisine paid for the identical array three times.

The memo must never change a number. These tests pin both halves: the
output is identical, and a panel that is not the one an entry was built
from is never served that entry.
"""
from __future__ import annotations

import numpy as np
import pytest

from nycsiting import config, cuisines, scoring

pytest.importorskip("streamlit")

NEEDS_DATA = pytest.mark.skipif(not config.RESTAURANTS_PQ.exists(),
                                reason="processed data not built")


@pytest.fixture(autouse=True)
def _clean_memo():
    scoring._REFERENCE_CACHE.clear()
    yield
    scoring._REFERENCE_CACHE.clear()


@NEEDS_DATA
def test_the_memo_returns_the_identical_distribution():
    import app as app_mod

    panel = app_mod.load_panel()
    compset = cuisines.competitive_set("Italian")

    cold = scoring.competitor_reference(panel, compset, 500)
    warm = scoring.competitor_reference(panel, compset, 500)
    scoring._REFERENCE_CACHE.clear()
    recomputed = scoring.competitor_reference(panel, compset, 500)

    assert np.array_equal(cold, warm), "a memo hit changed the distribution"
    assert np.array_equal(cold, recomputed), "the computation is not deterministic"
    assert len(cold) == 1200


@NEEDS_DATA
def test_a_different_panel_object_is_never_served_a_cached_reference():
    """
    The entry remembers the panel it was built from and is reused only for
    that exact object, so a rebuilt or reloaded panel cannot inherit a
    distribution computed from the previous one.
    """
    import app as app_mod

    panel = app_mod.load_panel()
    compset = cuisines.competitive_set("Italian")
    scoring.competitor_reference(panel, compset, 500)

    other = panel.head(2000).copy()          # genuinely different data
    result = scoring.competitor_reference(other, compset, 500)
    assert len(result) <= 1200
    # the entry now belongs to `other`; the original panel must recompute
    entry_panel, _ = scoring._REFERENCE_CACHE[
        (frozenset(compset), 500.0, 1200, 0)]
    assert entry_panel is other


@NEEDS_DATA
def test_the_key_separates_cuisine_and_radius():
    import app as app_mod

    panel = app_mod.load_panel()
    italian = cuisines.competitive_set("Italian")
    japanese = cuisines.competitive_set("Japanese")

    a = scoring.competitor_reference(panel, italian, 500)
    b = scoring.competitor_reference(panel, japanese, 500)
    c = scoring.competitor_reference(panel, italian, 250)
    assert len(scoring._REFERENCE_CACHE) == 3, "keys collapsed"
    assert not np.array_equal(a, b), "different cuisines share a reference"
    assert not np.array_equal(a, c), "different radii share a reference"


@NEEDS_DATA
def test_the_memo_is_bounded():
    import app as app_mod

    panel = app_mod.load_panel()
    compset = cuisines.competitive_set("Italian")
    for radius in range(200, 200 + 25 * 5, 5):     # 25 distinct radii
        scoring.competitor_reference(panel, compset, radius, sample=5)
    assert len(scoring._REFERENCE_CACHE) <= scoring._REFERENCE_CACHE_MAX


@NEEDS_DATA
def test_the_site_score_is_unchanged_by_the_memo():
    """End to end: the number a user sees must be byte-identical."""
    from nycsiting import analysis, context

    import app as app_mod

    panel = app_mod.load_panel()
    locs = app_mod.load_locations()
    site = app_mod.geocode_cached("195 Bowery, Manhattan")
    key = app_mod.resolve_location_key(locs, site)
    report = analysis.site_report(panel, locs, site["lat"], site["lon"],
                                  "Italian", 500, key)
    lot = context.lot_context(app_mod.load_lots(), site.get("bbl"))
    ped = context.nearest_pedestrian(app_mod.load_pedestrian(),
                                     site["lat"], site["lon"])

    scoring._REFERENCE_CACHE.clear()
    cold = scoring.score_site(report, panel, lot, ped, 500)
    warm = scoring.score_site(report, panel, lot, ped, 500)

    assert cold["score"] == warm["score"]
    assert cold["band"] == warm["band"]
    cold_comps = {c["key"]: c["score"] for c in cold["components"]}
    warm_comps = {c["key"]: c["score"] for c in warm["components"]}
    assert cold_comps == warm_comps, "a component moved"
