"""
v9 — the map-fit pane constants must track the layout that produces them.

The drift this pins actually happened. v8.2 introduced the sticky two-pane
workspace, which narrowed both map columns; MAP_PANE_PX still held the
pre-v8.2 widths (806/532 against a real 731/507). Nothing failed, because
the existing fit test feeds MAP_PANE_PX into the fit AND into its own
expectation — it is self-consistent by construction and cannot see a pane
constant that has stopped describing the page. The visible consequence was
quiet: a fit aiming for 71% of the pane filled 78% of it, so a district's
boundary sat closer to the edge than the padding intends.

So this checks the one thing that test cannot: that the declared pane widths
are still in the ratio the column layout actually uses. A future change to
st.columns([...]) that forgets these constants fails here.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

APP_PATH = pathlib.Path(__file__).resolve().parent.parent / "app.py"
APP_SRC = APP_PATH.read_text()

#: Measured live in the browser at a 1280px viewport, three samples per view,
#: identical each time (no scrollbar inset — macOS overlay scrollbars).
MEASURED = {"explore": 731, "assess": 507}


def _workspace_column_ratios() -> dict[str, float]:
    """The map column's share of the row, read from the source itself.

    Taken from the `if view == "explore":` branch in render_workspace, so
    the test reads the same literal a developer would edit rather than a
    copy of it kept somewhere else.
    """
    tree = ast.parse(APP_SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(APP_SRC, node.test) or ""
        if 'view == "explore"' not in test_src:
            continue

        def first_columns_ratio(body) -> float | None:
            for sub in body:
                for call in ast.walk(sub):
                    if (isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "columns"
                            and call.args
                            and isinstance(call.args[0], ast.List)):
                        vals = [e.value for e in call.args[0].elts
                                if isinstance(e, ast.Constant)]
                        if len(vals) == 2:
                            return float(vals[0])
            return None

        explore = first_columns_ratio(node.body)
        assess = first_columns_ratio(node.orelse)
        if explore is not None and assess is not None:
            return {"explore": explore, "assess": assess}
    pytest.fail("could not locate the workspace two-pane column split")


def test_measured_widths_match_the_declared_constants():
    """The constants are the measurement, not a rounded memory of it."""
    import app as app_mod

    for view, px in MEASURED.items():
        assert app_mod.MAP_PANE_PX[view][0] == px, (
            f"MAP_PANE_PX[{view!r}] says "
            f"{app_mod.MAP_PANE_PX[view][0]}px, the browser measured {px}px")


def test_pane_widths_are_consistent_with_the_column_ratios():
    """
    Both panes come out of the same row, so the ratio between the declared
    widths must be the ratio between the declared column shares. This is
    what catches a layout edit that leaves the fit constants behind.
    """
    import app as app_mod

    ratios = _workspace_column_ratios()
    declared = (app_mod.MAP_PANE_PX["explore"][0]
                / app_mod.MAP_PANE_PX["assess"][0])
    expected = ratios["explore"] / ratios["assess"]
    # 3%: Streamlit distributes a flex row with rounding, and the medium gap
    # is taken out of the row before the shares are applied, so the two
    # panes do not land at exactly the nominal ratio (measured 1.442 for a
    # nominal 1.429).
    assert declared == pytest.approx(expected, rel=0.03), (
        f"map columns are {ratios} but MAP_PANE_PX implies a "
        f"{declared:.3f} ratio; update the constants against a real "
        f"1280px window")


def test_row_width_implied_by_each_view_agrees():
    """Both views must imply the same underlying content width."""
    import app as app_mod

    ratios = _workspace_column_ratios()
    implied = {v: app_mod.MAP_PANE_PX[v][0] / ratios[v] for v in ratios}
    lo, hi = min(implied.values()), max(implied.values())
    assert hi / lo == pytest.approx(1.0, abs=0.03), (
        f"the two views imply different row widths: {implied}")


def test_pane_heights_match_the_figure_height():
    """A fit needs the real figure height; 640 is set in workspace_map."""
    import app as app_mod
    from nycsiting import workspace_map

    src = pathlib.Path(workspace_map.__file__).read_text()
    heights = {int(m) for m in re.findall(r"height=(\d{3})", src)}
    assert 640 in heights, f"figure heights found: {heights}"
    for view, (_, pane_h) in app_mod.MAP_PANE_PX.items():
        assert pane_h == 640, (view, pane_h)


def test_compare_declares_a_pane_but_never_fits_one():
    """
    Documented dead entry: render_compare_view returns before the map is
    built, so MAP_PANE_PX["compare"] never reaches zoom_for_bounds. It is
    kept so the fit sweep covers every declared view. If compare ever grows
    a map, this test fails and the width has to be measured for real.
    """
    import app as app_mod

    tree = ast.parse(APP_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "render_compare_view")
    body = ast.get_source_segment(APP_SRC, fn) or ""
    assert "plotly_chart" not in body and "workspace_map" not in body, (
        "compare now renders a map — measure its pane and set "
        "MAP_PANE_PX['compare'] from the browser")
    assert app_mod.MAP_PANE_PX["compare"] == app_mod.MAP_PANE_PX["assess"]
