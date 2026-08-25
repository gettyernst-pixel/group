"""Address canonicalisation — the joins everything downstream depends on."""
import pytest

from nycsiting.normalize import (
    building_variants, location_key, location_key_variants, normalize_borough,
    normalize_building, normalize_street, pretty_address,
)


class TestNormalizeStreet:
    @pytest.mark.parametrize("raw", [
        "EAST   17 STREET", "East 17th Street", "E 17 ST", "e. 17 st.",
    ])
    def test_spellings_of_one_street_converge(self, raw):
        assert normalize_street(raw) == "E 17 ST"

    def test_spelled_out_numbers_become_digits(self):
        # The archive writes 'SECOND AVENUE'; PLUTO writes '2 AVENUE'.
        assert normalize_street("SECOND AVENUE") == normalize_street("2 AVE")

    def test_only_the_last_token_is_a_street_type(self):
        # 'AVENUE' here is the name, not the suffix.
        assert normalize_street("AVENUE OF THE AMERICAS") == "AVENUE OF THE AMERICAS"

    def test_only_the_first_token_is_a_direction(self):
        assert normalize_street("NORTH HENRY STREET") == "N HENRY ST"

    def test_canonicalises_consistently_even_when_linguistically_wrong(self):
        # 'WEST' in West End Avenue is part of the name, but both sides of
        # every join run through this function, so agreement is what matters.
        assert normalize_street("WEST END AVE") == normalize_street("West End Avenue")

    @pytest.mark.parametrize("bad", [None, "", "   ", 42])
    def test_survives_junk(self, bad):
        assert normalize_street(bad) == ""

    def test_geosearch_abbreviations_reconcile(self):
        # GeoSearch answers "42 Broadway" with "42 B'WAY". Unmapped, the
        # geocoded address fails to join the panel and the app reports "no
        # history" for a storefront with thirty tenants on record.
        assert normalize_street("B'WAY") == normalize_street("BROADWAY")
        assert normalize_street("FT WASHINGTON AVE") == normalize_street(
            "Fort Washington Avenue")

    def test_leading_st_is_saint_but_trailing_st_is_street(self):
        assert normalize_street("ST NICHOLAS AVE") == "SAINT NICHOLAS AVE"
        assert normalize_street("MAIN STREET") == "MAIN ST"


class TestBuildingVariants:
    def test_queens_hyphen_is_restored(self):
        # The single rule worth ~18 points of match coverage.
        assert "25-07" in building_variants("2507", "QN")
        assert "138-58" in building_variants("13858", "QN")

    def test_hyphen_is_also_removed(self):
        assert "2507" in building_variants("25-07", "QN")

    def test_manhattan_numbers_are_left_alone(self):
        # 2507 Broadway in Manhattan is a real, unhyphenated address.
        assert building_variants("2507", "MN") == ["2507"]

    def test_empty_building_yields_nothing(self):
        assert building_variants("", "QN") == []


class TestLocationKey:
    def test_same_storefront_from_differently_written_sources(self):
        a = location_key("MANHATTAN", "195", "Bowery")
        b = location_key("MN", "195", "BOWERY")
        assert a == b == "MN|195|BOWERY"

    def test_geosearch_street_joins_to_the_panel(self):
        # The exact bug: 42 Broadway silently showed no history.
        assert location_key("MN", "42", "B'WAY") == location_key(
            "MANHATTAN", "42", "BROADWAY")

    def test_numeric_borough_codes_are_understood(self):
        assert location_key("1", "195", "BOWERY") == location_key("MANHATTAN", "195", "BOWERY")

    @pytest.mark.parametrize("args", [
        ("MANHATTAN", "", "BOWERY"), ("MANHATTAN", "195", ""), ("", "195", "BOWERY"),
    ])
    def test_incomplete_addresses_produce_no_key(self, args):
        # An empty key must never join to another empty key.
        assert location_key(*args) == ""

    def test_variants_cover_both_queens_spellings(self):
        variants = location_key_variants("QUEENS", "2507", "BROADWAY")
        assert "QN|2507|BROADWAY" in variants
        assert "QN|25-07|BROADWAY" in variants


def test_normalize_borough_handles_every_form():
    for value in ("MANHATTAN", "Manhattan", "MN", "1"):
        assert normalize_borough(value) == "MN"


def test_pretty_address_is_for_humans():
    assert pretty_address("MANHATTAN", "195", "BOWERY") == "195 Bowery, Manhattan"
