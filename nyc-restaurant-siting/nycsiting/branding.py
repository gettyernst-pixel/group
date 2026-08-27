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


#: Session key the overlay reads. An action that is about to cause real
#: work writes its message here; main() renders the overlay before doing
#: any of that work and clears the key once the page has finished.
LOADING_KEY = "_loading_message"

#: Above everything the app draws, just below Streamlit's own header
#: (999990). The header is not an escape hatch — assets/styles.css hides
#: its action elements — the ordering simply avoids fighting Streamlit's
#: chrome for the top of the stack. Nothing underneath is meant to be
#: reachable while the overlay is up: that is the point of it.
OVERLAY_Z = 999985


def overlay_css() -> str:
    """
    The one full-viewport loading overlay.

    WHY AN OVERLAY AND NOT A SPINNER: Streamlit streams its output, so a
    slow run paints a half-built page. Reproduced in the browser 600ms into
    an address analysis, the screen held: an empty map container, a cache
    spinner reading "RANKING CONCEPTS FOR THIS AREA…", and two orphaned
    methodology expander headers ("What does this mean?", "How each signal
    was judged") floating with no content under them. That collage — a
    stray rectangular box beside a loading animation — is what the loading
    state looked like. No per-element spinner can fix it, because the
    problem is the elements that HAVE rendered, not the ones that have not.

    `position: fixed` with `inset: 0` means the overlay covers the viewport
    no matter where in the document Streamlit places it, so it can be
    emitted first and still hide everything that renders afterwards.
    """
    return f"""
<style>
.jx-overlay {{
  position: fixed;
  inset: 0;
  z-index: {OVERLAY_Z};
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  background: rgba(7, 17, 31, 0.86);
  backdrop-filter: blur(7px) saturate(0.7);
  -webkit-backdrop-filter: blur(7px) saturate(0.7);
  /* DELAYED, so fast work never flashes a full-screen overlay.
     Warm interactions finish in well under 200ms — measured at
     18ms for a cached area click — and an overlay that appears and
     vanishes inside that window is a flicker, not feedback. The
     element is in the DOM immediately (so nothing behind it can be
     seen or clicked) but stays transparent until the work has
     lasted long enough to be worth reporting. Same reasoning as
     Streamlit's own 0.5s spinner delay, a little quicker. */
  opacity: 0;
  animation: jx-overlay-in 160ms ease-out 260ms forwards;
}}
/* No backdrop-filter (older Safari/Firefox): fall back to a heavier wash so
   the page underneath is still unreadable rather than half-legible. */
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {{
  .jx-overlay {{ background: rgba(7, 17, 31, 0.97); }}
}}
.jx-overlay img {{ animation: jx-float 1.25s ease-in-out infinite; }}
.jx-overlay .msg {{
  font-family: "DM Mono", SFMono-Regular, monospace;
  font-size: 12px; letter-spacing: 0.10em; text-transform: uppercase;
  color: #9AAABD; text-align: center; max-width: 34ch; line-height: 1.7;
}}
@keyframes jx-overlay-in {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
@media (prefers-reduced-motion: reduce) {{
  /* No fade, but it must still become visible — `animation: none` alone
     would leave opacity:0 and show a page that cannot be interacted with. */
  .jx-overlay {{ animation: none; opacity: 1; }}
  .jx-overlay img {{ animation: none; opacity: 0.9; }}
}}
</style>
"""


def overlay_html(message: str, height: int = 54) -> str:
    """The overlay's markup: one chair, one message, both centred."""
    return (f'<div class="jx-overlay" role="status" aria-live="polite" '
            f'aria-busy="true">'
            + logo_img(height=height, color=ACCENT, alt="")
            + f'<div class="msg">{message}</div></div>')


@contextmanager
def global_loader(message: str | None, clear_key: str | None = LOADING_KEY):
    """
    Cover the whole viewport while a block of real work runs.

    Emitted BEFORE the work, so the browser paints it immediately and every
    element produced afterwards is hidden behind it — which is the whole
    point: the user sees one deliberate loading state instead of a page
    assembling itself in pieces.

    On a rerun or st.stop() the overlay is deliberately LEFT UP and the
    session key is left set, so a transition that spans two runs (the
    normal Streamlit pattern of mutate-then-rerun) stays covered instead of
    flashing the half-built page between them. It comes down only when a
    run finishes normally, which is exactly when there is a complete page
    underneath it.

    An ERROR is the one case that must not follow that rule. If the body
    raises, Streamlit renders a traceback — and leaving the overlay up
    would hide it behind a full-screen blur with a chair on it, giving the
    user a frozen app instead of a message. So a real exception takes the
    overlay down and clears the pending message (otherwise the next run
    would raise it again over the same error) before re-raising.
    """
    import streamlit as st

    def dismiss() -> None:
        slot.empty()
        if clear_key:
            st.session_state.pop(clear_key, None)

    slot = st.empty()
    if message:
        slot.markdown(overlay_html(message), unsafe_allow_html=True)
    try:
        yield
    except BaseException as exc:
        # Streamlit signals rerun/stop with exceptions; those are control
        # flow, not failure, and the transition they start should stay
        # covered. Matched by name so this does not depend on the private
        # module path they live in, which moves between versions.
        # ONLY a rerun keeps it up. st.stop() looks similar but is not:
        # a rerun schedules another run that will finish the transition and
        # take the overlay down, while a stop ends the run with no
        # successor — so treating them alike would leave the overlay
        # covering whatever the app stopped to show (the "processed data
        # not found" error, for one) with no way back.
        if message and type(exc).__name__ != "RerunException":
            dismiss()
        raise
    if message:
        dismiss()


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
