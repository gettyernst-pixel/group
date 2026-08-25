"""
DOT pedestrian engine — all offline. The live API's discovered semantics
(two directional flows per interval, lowercase 'pedestrian', deleted rows)
are baked into fixtures so a regression against them fails loudly; the live
reconciliation lives in docs/pedestrian_validation.md.
"""
import io
import json
from datetime import date

import pandas as pd
import pytest

from nycsiting import pedestrian_dot as ped


# ---- fake transport ---------------------------------------------------------
class FakeOpener:
    """Stands in for urllib.urlopen; routes by dataset id in the URL."""

    def __init__(self, sensors=None, counts=None, fail=False):
        self.sensors = sensors if sensors is not None else []
        self.counts = counts if counts is not None else []
        self.fail = fail
        self.queries = []

    def __call__(self, request, timeout=None):
        if self.fail:
            raise OSError("network down")
        body = json.loads(request.data.decode())
        self.queries.append(body["query"])
        payload = self.sensors if ped.SENSOR_DATASET in request.full_url else self.counts
        return io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):  # used via `with open_fn(...) as resp`
        return self

    # urlopen returns a context manager; emulate by wrapping in helper below.


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def opener_for(sensors=None, counts=None, fail=False):
    record = {"queries": []}

    def opener(request, timeout=None):
        if fail:
            raise OSError("network down")
        body = json.loads(request.data.decode())
        record["queries"].append(body["query"])
        payload = sensors if ped.SENSOR_DATASET in request.full_url else counts
        return FakeResponse(json.dumps(payload if payload is not None else []).encode())

    opener.record = record
    return opener


SENSOR = dict(id="300038509", name="Emmons Ave", lat="40.5841", lon="-73.93099",
              firstdata="2023-12-27T12:15:00.000", lastdata="2026-08-24T01:15:00.000",
              granularity="PT15M", travelmodes="bike, pedestrian", directional=True)


def flow_rows(day, hours, per_interval=(2, 3), status="raw"):
    """Two directional flow rows per 15-min interval, like the real API."""
    rows = []
    for h in hours:
        for q in (0, 15, 30, 45):
            ts = f"{day}T{h:02d}:{q:02d}:00.000"
            rows.append(dict(timestamp=ts, direction="in", flowid="1",
                             counts=str(per_interval[0]), status=status))
            rows.append(dict(timestamp=ts, direction="out", flowid="2",
                             counts=str(per_interval[1]), status=status))
    return rows


def full_day(day, per_interval=(2, 3)):
    return flow_rows(day, range(24), per_interval)


# ---- sensors ---------------------------------------------------------------
class TestSensors:
    def test_pedestrian_filter_and_dedupe(self):
        dupe = dict(SENSOR)
        bike = dict(SENSOR, id="1", travelmodes="bike")
        op = opener_for(sensors=[SENSOR, dupe, bike])
        df = ped.fetch_pedestrian_sensors(opener=op)
        assert len(df) == 1 and df.iloc[0]["id"] == "300038509"
        # the SoQL itself filters to pedestrian-capable sensors
        assert "pedestrian" in op.record["queries"][0]

    def test_invalid_coordinates_rejected(self):
        bad = dict(SENSOR, id="9", lat="not-a-number")
        far = dict(SENSOR, id="8", lat="10.0", lon="-73.9")
        df = ped.fetch_pedestrian_sensors(opener=opener_for(sensors=[bad, far]))
        assert df.empty

    def test_nearest_and_distance(self):
        df = ped.fetch_pedestrian_sensors(opener=opener_for(sensors=[SENSOR]))
        sensor, dist = ped.nearest_pedestrian_sensor(df, 40.5841, -73.93099)
        assert sensor["id"] == "300038509" and dist < 1

    @pytest.mark.parametrize("dist,quality", [
        (100, ped.QUALITY_DIRECT), (150, ped.QUALITY_DIRECT),
        (300, ped.QUALITY_REFERENCE), (500, ped.QUALITY_REFERENCE),
        (501, ped.QUALITY_REMOTE), (25_000, ped.QUALITY_REMOTE)])
    def test_distance_classification(self, dist, quality):
        assert ped.classify_distance(dist) == quality


# ---- counts: the double-count regression -----------------------------------
class TestCounts:
    def test_directional_flows_sum_per_interval_never_double(self):
        # THE regression: 2+3 per interval = 5, never 2x totals or one side.
        counts = flow_rows("2026-06-14", [18])
        iv = ped.fetch_counts("300038509", date(2026, 6, 14), date(2026, 6, 14),
                              opener=opener_for(counts=counts))
        assert len(iv) == 4                      # 4 intervals in the hour
        assert (iv["counts"] == 5).all()

    def test_deleted_rows_excluded(self):
        counts = flow_rows("2026-06-14", [18]) + flow_rows(
            "2026-06-14", [18], per_interval=(100, 100), status="deleted")
        iv = ped.fetch_counts("x", date(2026, 6, 14), date(2026, 6, 14),
                              opener=opener_for(counts=counts))
        assert (iv["counts"] == 5).all()

    def test_query_filters_pedestrian_mode_and_sensor(self):
        op = opener_for(counts=[])
        ped.fetch_counts("300038509", date(2026, 6, 1), date(2026, 6, 2), opener=op)
        q = op.record["queries"][0]
        assert "travelmode = 'pedestrian'" in q
        assert "sensor_id = '300038509'" in q
        # never an unbounded pull of the 21M-row dataset
        assert "timestamp >=" in q and "timestamp <" in q

    def test_token_travels_in_header_only(self):
        seen = {}

        def opener(request, timeout=None):
            seen["token"] = request.headers.get("X-app-token")
            seen["url"] = request.full_url
            return FakeResponse(b"[]")

        ped.fetch_counts("x", date(2026, 6, 1), date(2026, 6, 1),
                         token="SECRET", opener=opener)
        assert seen["token"] == "SECRET"
        assert "SECRET" not in seen["url"]


# ---- aggregation ------------------------------------------------------------
class TestAggregation:
    def test_daily_total_and_dinner_window(self):
        iv = ped.fetch_counts("x", date(2026, 6, 14), date(2026, 6, 14),
                              opener=opener_for(counts=full_day("2026-06-14")))
        days = ped.daily_series(iv)
        assert days.iloc[0]["total"] == 5 * 96
        dinner = ped.service_period_series(iv, (17, 22))
        assert dinner.iloc[0]["total"] == 5 * 4 * 5   # 5 hours x 4 intervals x 5

    def test_service_window_uses_source_clock(self):
        # 11:00-15:00 lunch means those hours as written — no tz shift.
        iv = ped.fetch_counts("x", date(2026, 6, 14), date(2026, 6, 14),
                              opener=opener_for(counts=flow_rows("2026-06-14", [10, 11, 14, 15])))
        lunch = ped.service_period_series(iv, (11, 15))
        assert lunch.iloc[0]["total"] == 5 * 4 * 2    # only 11:xx and 14:xx

    def test_incomplete_day_is_marked_invalid(self):
        iv = ped.fetch_counts("x", date(2026, 6, 14), date(2026, 6, 14),
                              opener=opener_for(counts=flow_rows("2026-06-14", range(12))))
        days = ped.daily_series(iv)                    # 48/96 intervals = 50%
        assert bool(days.iloc[0]["valid"]) is False

    def test_low_coverage_days_excluded_from_metrics(self):
        counts = full_day("2026-06-08") + flow_rows("2026-06-09", range(10))
        iv = ped.fetch_counts("x", date(2026, 6, 8), date(2026, 6, 9),
                              opener=opener_for(counts=counts))
        m = ped.footfall_metrics(iv)
        assert m["raw_days"] == 2 and m["valid_days"] == 1
        assert m["daily"]["median"] == 5 * 96          # only the complete day

    def test_quantiles(self):
        counts = []
        for i, day in enumerate(["2026-06-08", "2026-06-09", "2026-06-10",
                                 "2026-06-11"]):
            counts += full_day(day, per_interval=(i + 1, 0))
        iv = ped.fetch_counts("x", date(2026, 6, 8), date(2026, 6, 11),
                              opener=opener_for(counts=counts))
        m = ped.footfall_metrics(iv)
        totals = sorted([96, 192, 288, 384])
        assert m["daily"]["median"] == (192 + 288) / 2
        assert m["daily"]["p25"] == pytest.approx(pd.Series(totals).quantile(0.25))
        assert m["daily"]["p75"] == pytest.approx(pd.Series(totals).quantile(0.75))


# ---- top level --------------------------------------------------------------
class TestMeasureLocation:
    def test_remote_sensor_never_gets_count_data(self):
        op = opener_for(sensors=[SENSOR], counts=full_day("2026-06-14"))
        # Midtown Manhattan: ~25km from Emmons Ave
        m = ped.measure_location(40.7550, -73.9840, opener=op, today=date(2026, 8, 24))
        assert m.quality == ped.QUALITY_REMOTE
        assert m.daily == {}                          # no P&L-usable numbers
        assert len(op.record["queries"]) == 1         # sensors only, no counts

    def test_direct_nearby_measures(self):
        counts = []
        for week in range(12):
            for dow in range(7):
                d = date(2026, 3, 2) + pd.Timedelta(days=week * 7 + dow)
                counts += full_day(d.isoformat())
        op = opener_for(sensors=[SENSOR], counts=counts)
        m = ped.measure_location(40.5841, -73.93099, opener=op,
                                 today=date(2026, 8, 24))
        assert m.quality == ped.QUALITY_DIRECT
        assert m.valid_days > 14
        assert m.periods["dinner"]["median"] == 5 * 20

    def test_api_failure_degrades_not_raises(self):
        m = ped.measure_location(40.58, -73.93, opener=opener_for(fail=True))
        assert m.quality == ped.QUALITY_UNAVAILABLE and m.message

    def test_too_few_valid_days_is_low_quality(self):
        op = opener_for(sensors=[SENSOR], counts=full_day("2026-08-16"))
        m = ped.measure_location(40.5841, -73.93099, opener=op,
                                 today=date(2026, 8, 24))
        assert m.quality == ped.QUALITY_LOW_DATA

    def test_measurement_window_is_complete_weeks(self):
        start, end = ped.measurement_window("2026-08-24T01:15:00.000",
                                            today=date(2026, 8, 24))
        assert end.weekday() == 6 and start.weekday() == 0    # Mon..Sun
        assert (end - start).days == 7 * 12 - 1
        assert end < date(2026, 8, 24)                        # never today


# ---- demand math ------------------------------------------------------------
class TestDemandMath:
    def test_required_capture_rate(self):
        # 69 covers against 8,000 measured = 0.8625%
        assert ped.required_capture_rate(69, 8_000) == pytest.approx(0.0086, abs=1e-4)

    def test_required_capture_with_no_footfall_is_none(self):
        assert ped.required_capture_rate(69, 0) is None

    def test_footfall_covers_capped_by_capacity(self):
        assert ped.footfall_covers(8_000, 0.008, capacity_covers=90) == 64
        assert ped.footfall_covers(80_000, 0.01, capacity_covers=90) == 90

    def test_negative_inputs_clamped(self):
        assert ped.footfall_covers(-5, 0.5, 90) == 0
