"""
The 2024 ACS pipeline and NTA rollups. All offline: the Census payloads here
are fixtures shaped exactly like the live API's row-of-rows JSON.
"""
import numpy as np
import pandas as pd
import pytest

from nycsiting import acs, config, nta


def payload(county="061", rows=None):
    """A Census API response: header row then data rows, all strings."""
    header = ["NAME", "B01003_001E", "B19013_001E", "B01002_001E",
              "B23025_004E", "B25064_001E", "state", "county", "tract"]
    rows = rows if rows is not None else [
        ["Census Tract 18; New York County; New York",
         "8123", "132000", "34.1", "5211", "2450", "36", county, "001800"],
        ["Census Tract 29.01; New York County; New York",
         "4211", "98000", "38.2", "2410", "2100", "36", county, "002901"],
    ]
    return [header] + rows


class FakeSession:
    def __init__(self, by_county):
        self.by_county = by_county
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        county = params["in"].split("county:")[1]

        class R:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self, _c=county, _s=self):
                return _s.by_county[_c]
        return R()


class TestParser:
    def test_acs_api_response_parser(self):
        df = acs.parse_payload(payload())
        assert list(df["population"]) == [8123, 4211]
        assert list(df["median_household_income"]) == [132000, 98000]
        assert df.iloc[0]["borough"] == "Manhattan"
        assert df.iloc[0]["acs_year"] == 2024

    def test_geoid_is_11_digit_string(self):
        df = acs.parse_payload(payload())
        assert (df["tract_geoid"].str.len() == 11).all()
        assert df["tract_geoid"].dtype == object
        assert df.iloc[0]["tract_geoid"] == "36061001800"

    def test_bronx_leading_zero_preserved(self):
        df = acs.parse_payload(payload(county="005"))
        assert df.iloc[0]["tract_geoid"].startswith("36005")
        assert df.iloc[0]["county_fips"] == "005"

    def test_negative_acs_sentinel_to_nan(self):
        rows = [["Tract X", "1000", "-666666666", "-666666666.0",
                 "500", "-222222222", "36", "061", "000100"]]
        df = acs.parse_payload(payload(rows=rows))
        assert pd.isna(df.iloc[0]["median_household_income"])
        assert pd.isna(df.iloc[0]["median_age"])
        assert pd.isna(df.iloc[0]["median_gross_rent"])
        assert df.iloc[0]["population"] == 1000   # real values survive

    def test_missing_income_not_zero(self):
        # Zero medians are suppression noise, never estimates.
        rows = [["Tract X", "0", "0", "0", "0", "0", "36", "061", "000100"]]
        df = acs.parse_payload(payload(rows=rows))
        assert pd.isna(df.iloc[0]["median_household_income"])
        assert pd.isna(df.iloc[0]["median_gross_rent"])
        assert df.iloc[0]["population"] == 0      # a count CAN be zero

    @pytest.mark.parametrize("col", ["population", "median_household_income",
                                     "median_age", "employed_population",
                                     "median_gross_rent"])
    def test_metrics_numeric(self, col):
        df = acs.parse_payload(payload())
        assert pd.api.types.is_numeric_dtype(df[col])


class TestFetch:
    def test_all_five_nyc_counties(self):
        session = FakeSession({c: payload(county=c, rows=[
            [f"Tract; {b}", "100", "50000", "30", "50", "1500",
             "36", c, "000100"]]) for c, b in acs.NYC_COUNTIES.items()})
        df = acs.fetch_all_nyc("FAKE-KEY", session=session)
        assert len(session.calls) == 5              # never per-tract requests
        assert set(df["county_fips"]) == set(acs.NYC_COUNTIES)
        assert set(df["borough"]) == set(acs.NYC_COUNTIES.values())

    def test_key_travels_in_params_not_url(self):
        session = FakeSession({c: payload(county=c, rows=[
            ["T", "1", "1", "1", "1", "1", "36", c, "000100"]])
            for c in acs.NYC_COUNTIES})
        acs.fetch_all_nyc("SECRET-KEY", session=session)
        assert all(p["key"] == "SECRET-KEY" for p in session.calls)

    def test_missing_census_key_error_is_developer_friendly(self):
        err = acs.CensusKeyMissing()
        text = str(err)
        assert "CENSUS_API_KEY" in text
        assert "key_signup" in text
        assert "keeps working" in text              # app survives without it


class TestTractNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("025200", "025200"), ("348.0", "034800"), ("112", "011200"),
        ("29.01", "002901"), ("5", "000500"), ("70.02", "007002"),
        (None, None), ("nan", None), ("", None),
    ])
    def test_dohmh_spellings(self, raw, expected):
        assert acs.normalize_tract_code(raw) == expected

    def test_geoid_for_borough(self):
        assert acs.tract_geoid_for("Manhattan", "18") == "36061001800"
        assert acs.tract_geoid_for("Bronx", "63.01") == "36005006301"
        assert acs.tract_geoid_for("Elsewhere", "18") is None


class TestSiteTract:
    def _panel(self):
        return pd.DataFrame({
            "seen_2026": [True, True], "lat": [40.7208, 40.7210],
            "lon": [-73.9934, -73.9930], "boro": ["Manhattan", "Manhattan"],
            "census_tract": ["001800", "002900"], "geo_source": ["self", "self"],
        })

    class GeoOK:
        def get(self, url, params=None, timeout=None):
            class R:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"result": {"geographies": {"Census Tracts": [
                        {"GEOID": "36061001800"}]}}}
            return R()

    class GeoDown:
        def get(self, url, params=None, timeout=None):
            raise ConnectionError("census geocoder unreachable")

    def test_address_local_market_uses_tract_from_geocoder(self):
        geoid, source = acs.site_tract_geoid(
            self._panel(), 40.7208, -73.9934, {"36061001800"},
            session=self.GeoOK())
        assert geoid == "36061001800" and source == "census_geocoder"

    def test_geocoder_failure_falls_back_to_valid_neighbour(self):
        geoid, source = acs.site_tract_geoid(
            self._panel(), 40.7208, -73.9934, {"36061001800"},
            session=self.GeoDown())
        assert geoid == "36061001800" and source == "dohmh_neighbour"

    def test_split_2010_tract_is_refused_not_misused(self):
        # DOHMH carries 2010 codes; 002900 was split into 29.01/29.02 in
        # 2020, so borrowing it would fetch the wrong tract's demographics.
        panel = self._panel().iloc[[1]]
        geoid, source = acs.site_tract_geoid(
            panel, 40.7210, -73.9930,
            {"36061002901", "36061002902"}, session=self.GeoDown())
        assert geoid is None and source == "unavailable"

    def test_distant_neighbour_not_borrowed(self):
        geoid, source = acs.site_tract_geoid(
            self._panel(), 40.80, -73.90, {"36061001800"},
            session=self.GeoDown())
        assert geoid is None


class TestNTA:
    def _acs(self):
        return pd.DataFrame({
            "tract_geoid": ["36061000100", "36061000200", "36061000300"],
            "population": [1000.0, 3000.0, np.nan],
            "employed_population": [600.0, 1500.0, np.nan],
            "median_household_income": [100000.0, 50000.0, np.nan],
            "median_age": [30.0, 40.0, np.nan],
            "median_gross_rent": [2000.0, 1500.0, np.nan],
        })

    def _eq(self):
        return pd.DataFrame({
            "tract_geoid": ["36061000100", "36061000200", "36061000300"],
            "nta_code": ["MN0101"] * 3, "nta_name": ["Test NTA"] * 3,
            "borough": ["Manhattan"] * 3, "nta_type": ["0"] * 3,
        })

    def test_tract_to_nta_join(self):
        out = nta.nta_demographics(self._acs(), self._eq())
        assert len(out) == 1
        assert out.iloc[0]["tract_count"] == 3

    def test_nta_population_sum(self):
        out = nta.nta_demographics(self._acs(), self._eq())
        assert out.iloc[0]["population"] == 4000.0

    def test_nta_employment_sum(self):
        out = nta.nta_demographics(self._acs(), self._eq())
        assert out.iloc[0]["employed_population"] == 2100.0

    def test_no_naive_average_of_income_medians(self):
        out = nta.nta_demographics(self._acs(), self._eq())
        # No NTA column may claim to be a median of anything.
        assert not any("median" in c for c in out.columns)
        # The indicator is population-weighted, not the naive mean (75k).
        expected = (100000 * 1000 + 50000 * 3000) / 4000
        assert out.iloc[0]["income_context"] == pytest.approx(expected)
        assert out.iloc[0]["income_context"] != pytest.approx(75000)
        assert out.iloc[0]["income_context_type"] == "DERIVED_FROM_ACS_TRACTS"

    def test_missing_tract_values_excluded_from_weights_not_zeroed(self):
        acs_df = self._acs()
        # If the NaN tract were treated as income 0 with population weight,
        # the indicator would collapse; it must equal the two-tract answer.
        out = nta.nta_demographics(acs_df, self._eq())
        expected = (100000 * 1000 + 50000 * 3000) / 4000
        assert out.iloc[0]["income_context"] == pytest.approx(expected)

    def test_real_equivalency_file_loads_clean(self):
        eq = nta.load_equivalency()
        assert len(eq) == 2327
        assert (eq["tract_geoid"].str.len() == 11).all()
        assert eq["nta_code"].nunique() == 262

    def test_real_polygon_file_loads_clean(self):
        poly = nta.load_polygons()
        assert len(poly) == 262
        assert poly["geometry_wkt"].notna().all()


class TestPercentiles:
    def test_percentile_uses_valid_values_only(self):
        table = pd.DataFrame({
            "tract_geoid": ["A", "B", "C", "D"],
            "borough": ["Manhattan"] * 4, "census_name": ["a", "b", "c", "d"],
            "population": [100.0, 200.0, 300.0, 400.0],
            "median_household_income": [50000.0, np.nan, 100000.0, 150000.0],
            "median_age": [30.0] * 4, "employed_population": [50.0] * 4,
            "median_gross_rent": [1500.0] * 4,
        })
        out = acs.tract_percentiles(table, "C")
        assert out["median_household_income"]["value"] == 100000.0
        # 1 of 3 valid incomes below -> 33rd percentile, NaN excluded.
        assert out["median_household_income"]["percentile"] == pytest.approx(100 / 3)

    def test_missing_metric_reported_as_none_not_zero(self):
        table = pd.DataFrame({
            "tract_geoid": ["A"], "borough": ["Bronx"], "census_name": ["a"],
            "population": [100.0], "median_household_income": [np.nan],
            "median_age": [30.0], "employed_population": [50.0],
            "median_gross_rent": [np.nan],
        })
        out = acs.tract_percentiles(table, "A")
        assert out["median_household_income"]["value"] is None
        assert out["median_gross_rent"]["value"] is None


class TestLegacyFileRetired:
    def test_old_national_census_not_used(self):
        # The legacy path exists in config as documentation, but no module
        # may read it. A grep-level guard: nothing imports or opens it.
        import pathlib
        offenders = []
        for py in pathlib.Path("nycsiting").glob("*.py"):
            text = py.read_text()
            if "ACSDP" in text and py.name != "config.py":
                offenders.append(py.name)
        assert not offenders, offenders

    def test_api_failure_does_not_break_cached_app(self, tmp_path):
        # load_cache returns None (no file) without raising; the UI renders
        # its honest not-fetched state from that.
        assert acs.load_cache(tmp_path / "nope.csv") is None


def test_marble_hill_nta_is_one_row_not_two():
    """
    BX0802 spans tracts labelled Bronx AND Manhattan (Marble Hill's famous
    quirk). The rollup must produce one row per NTA code, with the sum over
    every component tract — a duplicate index here crashed the evidence map.
    """
    table = acs.load_cache()
    if table is None:
        pytest.skip("ACS cache not fetched")
    demo = nta.nta_demographics(table, nta.load_equivalency())
    assert demo["nta_code"].duplicated().sum() == 0
    row = demo[demo["nta_code"] == "BX0802"]
    assert len(row) == 1
    members = nta.load_equivalency().query("nta_code == 'BX0802'")["tract_geoid"]
    by_hand = table[table["tract_geoid"].isin(members)]["population"].fillna(0).sum()
    assert row.iloc[0]["population"] == by_hand
