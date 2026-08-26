"""
The product mark: one chair, resolved from the repository, used everywhere.

PATH RESOLUTION
The asset lives in the repo at assets/noun-chair-8459396.png and is located
relative to THIS module, never from the process working directory and never
from an absolute developer path. That makes it work identically when the app
is launched from another directory, on Linux, and on Streamlit Community
Cloud, where no developer filesystem exists.

COLOUR
The source artwork is pure black on transparency, which would be invisible
on the dark navy shell. It is recoloured to the brand tokens at load time by
rewriting the RGB of opaque pixels and keeping the alpha channel — exact
colours, rather than a stack of approximate CSS filters.
"""
from __future__ import annotations

import base64
import io
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

#: Repo root = the directory containing this package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = PROJECT_ROOT / "assets" / "noun-chair-8459396.png"

#: Brand tokens, matching assets/styles.css.
INK = "#F5F8FC"
ACCENT = "#65E3B0"


@lru_cache(maxsize=8)
def logo_data_uri(color: str = INK) -> str:
    """
    The chair as a recoloured base64 data URI — no network, no file:// URL,
    no absolute path. Cached per colour so the encode happens once.

    Falls back to the untouched source bytes if Pillow is somehow absent, so
    a missing optional dependency can never take the page down.
    """
    raw = LOGO_PATH.read_bytes()
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        pixels = img.getdata()
        img.putdata([(r, g, b, a) for (_, _, _, a) in pixels])
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        raw = buffer.getvalue()
    except Exception:
        pass
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def logo_img(height: int = 32, color: str = INK,
             alt: str = "Siting") -> str:
    """One <img> tag, sized by height with the aspect ratio preserved."""
    return (f'<img src="{logo_data_uri(color)}" alt="{alt}" '
            f'height="{height}" style="height:{height}px;width:auto;'
            f'display:block;" />')


#: The loader's motion: a slow float with a matching opacity breath. Gentle
#: on purpose — this is a B2B analysis tool, not a game.
LOADER_CSS = """
<style>
.jx-loader { display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 14px; padding: 26px 0; }
.jx-loader img { animation: jx-float 1.25s ease-in-out infinite; }
.jx-loader .msg { font-family: "DM Mono", SFMono-Regular, monospace;
  font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  color: #9AAABD; }
@keyframes jx-float {
  0%   { transform: translateY(0);     opacity: 0.72; }
  50%  { transform: translateY(-5px);  opacity: 1.00; }
  100% { transform: translateY(0);     opacity: 0.72; }
}
@media (prefers-reduced-motion: reduce) {
  .jx-loader img { animation: none; opacity: 0.9; }
}
</style>
"""


def spinner_css() -> str:
    """
    Make Streamlit's OWN spinner the chair, everywhere.

    WHY THIS RATHER THAN REPLACING EACH CALL SITE: loading feedback comes
    from three places — st.spinner, the show_spinner= text on ten cached
    functions, and the explicit chair loader — and only the last was
    branded, so the app showed two different loading affordances depending
    on which code path was slow. Restyling the one component Streamlit
    renders for all three makes every loading state identical, keeps the
    existing contextual messages ("Analyzing concept fit…"), and preserves
    Streamlit's built-in 0.5s delay before a spinner appears, which is what
    stops fast work from flashing a loader.

    Hooks: `[data-testid="stSpinner"]` and `[data-testid="stSpinnerIcon"]`.
    The icon is a bordered, rotating circle by default; its border is
    removed and the chair is painted in as a background image.
    """
    return f"""
<style>
[data-testid="stSpinnerIcon"] {{
  border: none !important;
  border-radius: 0 !important;
  width: 26px !important;
  height: 26px !important;
  background-image: url("{logo_data_uri(ACCENT)}") !important;
  background-repeat: no-repeat !important;
  background-position: center !important;
  background-size: contain !important;
  animation: jx-float 1.25s ease-in-out infinite !important;
}}
[data-testid="stSpinner"] [data-testid="stMarkdownContainer"] p {{
  font-family: "DM Mono", SFMono-Regular, monospace;
  font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  color: #9AAABD; margin: 0;
}}
@keyframes jx-float {{
  0%   {{ transform: translateY(0);    opacity: 0.72; }}
  50%  {{ transform: translateY(-5px); opacity: 1.00; }}
  100% {{ transform: translateY(0);    opacity: 0.72; }}
}}
@media (prefers-reduced-motion: reduce) {{
  [data-testid="stSpinnerIcon"] {{ animation: none !important; opacity: 0.9; }}
}}
</style>
"""


def map_ground_css(key: str = "ws_map", height: int = 640) -> str:
    """
    What the user sees WHILE the map is being rebuilt.

    THE PROBLEM THIS SOLVES: st.plotly_chart's element identity includes the
    figure spec, so any real change — new area, new layer, new filter —
    makes Streamlit unmount the chart and mount a new one. Measured in the
    browser: the plot div and the mapbox instance are both replaced, and for
    a few hundred milliseconds the container is EMPTY. That empty moment,
    followed by the map snapping back in, is the "pulsing" users reported.

    A server-side spinner cannot cover it: by the time the browser tears the
    chart down, the server run has already finished. So the loading state
    has to live on the container itself. This paints the map's own dark
    ground plus the chair mark behind the chart. While the chart is missing
    the user sees a branded, deliberate loading panel at exactly the map's
    size; the moment mapbox paints its opaque canvas the ground is covered
    again. No JavaScript, no timers, and nothing that can misfire — if the
    rules were dropped entirely the map would simply look as it did before.

    min-height keeps the column from collapsing during the gap, so the
    panel beside the map does not jump.
    """
    return f"""
<style>
.st-key-{key} {{
  background-color: #07111F;
  background-image: url("{logo_data_uri(ACCENT)}");
  background-repeat: no-repeat;
  background-position: center;
  background-size: 40px auto;
  min-height: {height}px;
  border-radius: 4px;
}}
.st-key-{key} [data-testid="stPlotlyChart"] {{
  animation: jx-map-in 220ms ease-out;
}}
@keyframes jx-map-in {{ from {{ opacity: 0.55; }} to {{ opacity: 1; }} }}
@media (prefers-reduced-motion: reduce) {{
  .st-key-{key} [data-testid="stPlotlyChart"] {{ animation: none; }}
}}
</style>
"""


def is_cold(key: str) -> bool:
    """
    True the first time this session sees `key`. Used to show the loader
    only for work that will actually take time: repeating an already-cached
    operation must not flash a loader for one frame.
    """
    import streamlit as st

    warm = st.session_state.setdefault("_warm_keys", set())
    if key in warm:
        return False
    warm.add(key)
    return True


@contextmanager
def chair_spinner(message: str, cold_key: str | None = None):
    """
    Branded loading state for a block of slow work.

    With `cold_key`, the loader is shown only on the first run for that key
    — cached repeats stay silent, so switching back to an already-computed
    area or tab does not flash.
    """
    import streamlit as st

    if cold_key is not None and not is_cold(cold_key):
        yield
        return
    slot = st.empty()
    slot.markdown(loader_html(message), unsafe_allow_html=True)
    try:
        yield
    finally:
        slot.empty()


def loader_html(message: str, height: int = 42) -> str:
    """
    The branded loading block. role/aria-live make the message a real status
    announcement rather than decoration, so it is reachable by screen
    readers as well as visible.
    """
    return (LOADER_CSS
            + f'<div class="jx-loader" role="status" aria-live="polite">'
            + logo_img(height=height, color=ACCENT, alt="")
            + f'<div class="msg">{message}</div></div>')
