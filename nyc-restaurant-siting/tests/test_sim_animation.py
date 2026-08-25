"""The animation is a rendering of the frame — these pin that contract."""
import json
import re

import pandas as pd
import pytest

from nycsiting import financial_simulation as fs
from nycsiting import sim_animation as anim
from tests.test_financial_simulation import make


class TestOccupancy:
    def test_seventy_percent_utilisation_fills_about_seventy_percent(self):
        assert anim.occupied_count(20, 0.70) == 14
        assert anim.occupied_count(10, 0.70) == 7

    def test_clamped_at_both_ends(self):
        assert anim.occupied_count(20, -1.0) == 0
        assert anim.occupied_count(20, 5.0) == 20

    def test_zero_utilisation_is_an_empty_room(self):
        assert anim.occupied_count(28, 0.0) == 0


class TestLayout:
    def test_layout_is_deterministic(self):
        assert anim.table_layout(55) == anim.table_layout(55)

    def test_seats_map_to_a_sane_table_count(self):
        assert len(anim.table_layout(55)) == 14        # ~4 seats per table
        assert len(anim.table_layout(4)) == 4          # floor
        assert len(anim.table_layout(1000)) == 28      # ceiling


class TestPayload:
    def test_animation_numbers_come_from_the_frame_not_recomputed(self):
        df = fs.build_simulation_dataframe(make(), "expected", 12)
        payload = anim.frame_payload(df, 14)
        assert len(payload) == 12
        month3 = payload[2]
        row = df[df.month == 3].iloc[0]
        assert month3["revenue"] == round(float(row.revenue))
        assert month3["cumulative_return"] == round(
            float(row.cumulative_return_after_investment))

    def test_html_embeds_the_payload_and_controls(self):
        df = fs.build_simulation_dataframe(make(), "expected", 12)
        html = anim.build_animation_html(df, 55, break_even_month=42)
        m = re.search(r"const DATA = (\[.*?\]);", html, re.S)
        assert m, "payload not embedded"
        data = json.loads(m.group(1))
        assert len(data) == 12
        for control in ("Play", "Restart", "1x", "2x", "4x"):
            assert control in html
        assert "BREAK_EVEN = 42" in html

    def test_no_break_even_embeds_null_not_a_fake_month(self):
        df = fs.build_simulation_dataframe(
            make(base_utilization=0.10), "expected", 12)
        html = anim.build_animation_html(df, 55, break_even_month=None)
        assert "BREAK_EVEN = null" in html

    def test_zero_investment_recovery_is_null_not_nan(self):
        df = fs.build_simulation_dataframe(
            make(initial_investment=0.0), "expected", 6)
        payload = anim.frame_payload(df, 10)
        assert all(p["recovery"] is None for p in payload)
        html = anim.build_animation_html(df, 55, None)
        assert "NaN" not in html
