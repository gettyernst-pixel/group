"""The map layer: grouping, palettes, and the figure actually serialising."""
import numpy as np
import pandas as pd
import plotly.io
import pytest

from nycsiting import mapview


@pytest.fixture
def area():
    return pd.DataFrame({
        "name": ["Alpha", "Beta", None, "Delta", "Eps"],
        "cuisine": ["Italian", "Pizza", "Thai", "Italian", ""],
        "lat": [40.7200, 40.7205, 40.7210, 40.7215, 40.7220],
        "lon": [-73.9900, -73.9901, -73.9902, -73.9903, -73.9904],
        "seen_2026": [True, True, False, False, True],
        "seen_2017": [True, False, True, True, False],
        "distance_m": [10.0, 60.0, 110.0, 160.0, 210.0],
        "first_observed": pd.to_datetime(
            ["2013-01-01", "2024-01-01", "2012-05-01", "2011-11-01", None]),
        "last_observed": pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2016-05-01", "2015-11-01", None]),
        "location_key": ["MN|1|A ST", "MN|1|A ST", "MN|2|B ST", "MN|3|C ST", "MN|4|D ST"],
    })


@pytest.fixture
def locations():
    return pd.DataFrame({
        "location_key": ["MN|1|A ST", "MN|2|B ST", "MN|3|C ST", "MN|4|D ST"],
        "restaurants_ever": [2, 1, 5, 3],
    })


SITE = {"lat": 40.7200, "lon": -73.9900}
COMPSET = {"Italian", "Pizza"}


class TestPalettes:
    """
    Guards the measured values. These hexes came out of the validator; a later
    edit that 'tidies' them must fail here rather than silently ship a palette
    nobody re-checked.
    """

    def test_both_themes_are_defined(self):
        assert set(mapview.THEMES) == {"light", "dark"}

    def test_status_does_not_use_the_green_red_status_pair(self):
        # Green/red measures ΔE 4.1 under deuteranopia — a red-green colourblind
        # reader sees one colour. Blue/orange measures 24.7 for the same job.
        for theme in mapview.THEMES.values():
            first_two = {c.lower() for c in theme["categorical"][:2]}
            assert "#0ca30c" not in first_two
            assert "#d03b3b" not in first_two

    def test_categorical_has_exactly_three_slots(self):
        # A map is a scatter, so all pairs can be adjacent; only the first
        # three slots clear the all-pairs floors. A fourth would need folding
        # into "Other" instead.
        for theme in mapview.THEMES.values():
            assert len(theme["categorical"]) == 3

    def test_ordinal_ramp_is_monotone_in_lightness(self):
        def luminance(hex_colour):
            r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        for theme in mapview.THEMES.values():
            lums = [luminance(c) for c in theme["ordinal"]]
            assert lums == sorted(lums, reverse=True)

    def test_basemaps_match_their_surface(self):
        assert mapview.THEMES["light"]["basemap"] == "carto-positron"
        assert mapview.THEMES["dark"]["basemap"] == "carto-darkmatter"


class TestGrouping:
    def test_status_splits_on_presence_in_2026(self, area):
        df = mapview._assign_groups(area, "status", "Italian", COMPSET, None)
        assert (df["_group"] == "Still trading (2026)").sum() == 3
        assert (df["_group"] == "Gone since 2017").sum() == 2

    def test_concept_separates_exact_cuisine_from_substitutes(self, area):
        df = mapview._assign_groups(area, "concept", "Italian", COMPSET, None)
        counts = df["_group"].value_counts()
        assert counts["Italian (your concept)"] == 2
        assert counts["Competing concept"] == 1      # the Pizza place
        assert counts["Other food business"] == 2

    def test_turnover_buckets_come_from_the_location_table(self, area, locations):
        df = mapview._assign_groups(area, "turnover", "Italian", COMPSET, locations)
        # MN|3|C ST has 5 tenants on record -> the top bucket.
        assert df.loc[3, "_group"] == "4 or more"
        assert df.loc[2, "_group"] == "1 restaurant"

    def test_group_order_is_fixed_not_frequency_ranked(self, area):
        # Colour must follow the entity: narrowing the radius must not repaint
        # whichever group happens to be largest.
        full = mapview._assign_groups(area, "status", "Italian", COMPSET, None)
        subset = mapview._assign_groups(area.head(2), "status", "Italian", COMPSET, None)
        assert list(full["_group"].cat.categories) == list(subset["_group"].cat.categories)

    def test_unknown_mode_is_rejected(self, area):
        with pytest.raises(ValueError):
            mapview._assign_groups(area, "nonsense", "Italian", COMPSET, None)


class TestFigure:
    @pytest.mark.parametrize("mode", list(mapview.MODES))
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_every_mode_and_theme_serialises(self, area, locations, mode, theme):
        # The check that would have caught the plotly timeline crash: building
        # a figure is not the same as being able to send it to the browser.
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500,
                                mode=mode, theme=theme, locations=locations)
        plotly.io.to_json(fig, validate=False)

    def test_closed_restaurants_are_drawn_not_filtered_out(self, area):
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500, mode="status")
        gone = next(t for t in fig.data if t.name and t.name.startswith("Gone"))
        assert len(gone.lat) == 2

    def test_legend_labels_carry_counts(self, area):
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500, mode="status")
        assert any("(3)" in (t.name or "") for t in fig.data)

    def test_site_is_ink_not_a_fourth_hue(self, area):
        # A fourth colour would read as another category of restaurant.
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500,
                                mode="concept", theme="dark")
        site = next(t for t in fig.data if t.name == "Your site")
        assert site.marker.color == mapview.THEMES["dark"]["ink"]

    def test_radius_ring_is_drawn_and_stays_out_of_the_legend(self, area):
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500)
        ring = fig.data[0]
        assert ring.mode == "lines" and ring.showlegend is False

    def test_unmappable_restaurants_are_skipped_without_error(self, area):
        area.loc[0, "lat"] = np.nan
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500, mode="status")
        plotly.io.to_json(fig, validate=False)
        assert sum(len(t.lat) for t in fig.data if t.name and "trading" in t.name) == 2

    def test_zoom_widens_as_the_radius_grows(self):
        assert mapview._zoom_for(1500) < mapview._zoom_for(200)

    def test_missing_names_do_not_render_as_none(self, area):
        fig = mapview.build_map(area, SITE, "Italian", COMPSET, 500, mode="status")
        labels = [x for t in fig.data if t.text is not None for x in t.text]
        assert None not in labels and "(unnamed)" in labels


class TestTable:
    def test_table_covers_the_same_marks_as_the_map(self, area):
        # Relief for the light-mode contrast WARN: the same answer without
        # depending on colour.
        table = mapview.map_table(area, "status", "Italian", COMPSET)
        assert len(table) == len(area)
        assert "Group" in table.columns

    def test_table_is_sorted_by_distance(self, area):
        table = mapview.map_table(area, "status", "Italian", COMPSET)
        assert table["Distance (m)"].is_monotonic_increasing

    def test_undated_restaurants_show_a_dash_not_nat(self, area):
        table = mapview.map_table(area, "status", "Italian", COMPSET)
        assert "NaT" not in table.to_string()
