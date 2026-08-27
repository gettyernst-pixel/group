"""
The component system: every repeated visual pattern, built once.

These emit HTML against the classes in assets/styles.css — the single source
of visual truth. Nothing here computes anything: components receive already-
validated values from the analysis layer and arrange them. If a component
seems to need logic, the logic belongs upstream.
"""
from __future__ import annotations

import html as _html
from pathlib import Path

import streamlit as st

_CSS_PATH = Path(__file__).resolve().parent.parent / "assets" / "styles.css"


def inject_styles() -> None:
    """Shell tokens, plus the branded loader.

    The spinner rules ship here so EVERY loading state — st.spinner, the
    show_spinner text on cached functions, and the explicit chair loader —
    renders the same mark on every page, without each call site opting in.
    """
    from nycsiting import branding

    st.markdown(f"<style>{_CSS_PATH.read_text()}</style>", unsafe_allow_html=True)
    st.markdown(branding.spinner_css(), unsafe_allow_html=True)
    st.markdown(branding.map_ground_css(), unsafe_allow_html=True)
    st.markdown(branding.overlay_css(), unsafe_allow_html=True)


def esc(value: object) -> str:
    return _html.escape(str(value))


# ------------------------------------------------------------ button rows
def button_row(count: int = 2, gap: str = "small"):
    """
    Equal columns for buttons that sit side by side.

    THE ONE PLACE adjacent-button layout is decided. Rows used to be built
    ad hoc — st.columns([1.2, 1.2, 1]) here, [2.2, 1] there — which left
    dead space beside short buttons and, worse, gave neighbours different
    widths: a long label wrapped onto a second line while the button next
    to it stayed one line, so the two were visibly different heights.

    Equal columns plus width="stretch" on every button in the row means the
    row always fills its container and every button in it has the same box.
    Height parity is finished in assets/styles.css, which gives buttons in
    a horizontal block a shared min-height and centres their labels, so
    even a label that does wrap cannot make its row ragged.
    """
    return st.columns([1] * max(1, count), gap=gap)


# ---------------------------------------------------------------- shell
def eyebrow(text: str, number: str | None = None) -> None:
    num = f'<span class="num">[{esc(number)}]</span>' if number else ""
    st.markdown(f'<div class="jx-eyebrow">{num}{esc(text)}</div>',
                unsafe_allow_html=True)


def display(text: str) -> None:
    st.markdown(f'<div class="jx-display">{text}</div>', unsafe_allow_html=True)


def section(number: str, title: str, question: str | None = None) -> None:
    """Numbered section header: eyebrow, then the user's question as headline."""
    st.markdown('<div style="height:40px"></div>', unsafe_allow_html=True)
    eyebrow(title, number)
    if question:
        st.markdown(f"## {question}")


def plan_chips(values: list, on_remove=None) -> None:
    """
    YOUR PLAN as compact chips — the user's own explicit inputs, visibly
    still driving the analysis. Never a paragraph, never hidden defaults.

    Chips are REMOVABLE when `on_remove` is given and the chip names a plan
    field. A constraint you can see but cannot change is just a label; the
    × makes the plan editable in place, without going back to the prompt.

    Rendered as real Streamlit buttons rather than HTML, because an <a> in
    st.markdown cannot call Python. The chip appearance is CSS on the
    button (see .jx-chip-row in assets/styles.css), so it still reads as a
    chip rather than a row of form controls.

    Accepts the legacy list-of-strings form as well, so any caller that has
    not been updated still renders (without × controls).
    """
    if not values:
        return
    chips = [v if isinstance(v, dict) else {"label": v, "field": None}
             for v in values]
    removable = [c for c in chips if c["field"] and on_remove]

    if not removable:
        inner = "".join(f'<span class="chip">{esc(c["label"])}</span>'
                        for c in chips)
        st.markdown(
            f'<div class="jx-plan"><span class="k">Your plan</span>'
            f'{inner}</div>', unsafe_allow_html=True)
        return

    st.markdown('<div class="jx-plan-label">Your plan</div>',
                unsafe_allow_html=True)
    with st.container(key="plan_chip_row"):
        # One column per chip, sized to its label, PLUS a trailing spacer
        # that absorbs the rest of the row. Without the spacer Streamlit
        # divides the full content width between the chips, so three short
        # constraints stretched into three 400px slabs instead of reading
        # as chips.
        widths = [max(len(c["label"]), 6) + 4 for c in chips]
        spacer = max(sum(widths) * 1.8, 20)
        cols = st.columns(widths + [spacer], gap="small")
        for col, chip in zip(cols, chips):
            with col:
                if chip["field"] and on_remove:
                    # A NON-BREAKING space: with an ordinary one the
                    # label wrapped onto a second line at narrow column
                    # widths ("Brunch Spot" measured 58px tall beside 30px
                    # chips), which is the exact ragged-height problem the
                    # button system exists to prevent.
                    st.button(f"{chip['label']}\u00a0 ✕",
                              key=f"chip_rm_{chip['field']}",
                              on_click=on_remove, args=(chip["field"],),
                              help=f"Remove {chip['label']} from your plan",
                              width="stretch")
                else:
                    st.markdown(
                        f'<span class="chip chip-static">'
                        f'{esc(chip["label"])}</span>',
                        unsafe_allow_html=True)


# ---------------------------------------------------------------- context
def query_context(cuisine: str, price: str, address: str) -> None:
    st.markdown(
        f"""<div class="jx-context">
          <span class="what">{esc(cuisine)} · {esc(price)}</span>
          <span class="where">{esc(address)}</span>
        </div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- decision
def decision_hero(band: str, headline: str, fit: int | None,
                  quality_label: str | None) -> None:
    """
    Band first, number second: the categorical read is the message; the
    0-100 index exists for comparing addresses, and is labelled as exactly
    that — never a probability.
    """
    index = "–" if fit is None else str(fit)
    quality = (f'<div class="cell"><div class="k">Evidence quality</div>'
               f'<div class="v">{esc(quality_label)}</div></div>'
               if quality_label else "")
    st.markdown(
        f"""<div class="jx-hero">
          <div class="jx-eyebrow"><span class="num">[01]</span>Location assessment</div>
          <div class="verdict">{esc(band)}.</div>
          <div class="explain">{esc(headline)}</div>
          <div class="jx-heroband">
            <div class="cell"><div class="k">Primary message</div>
              <div class="v band">{esc(band)}</div></div>
            <div class="cell"><div class="k">Location fit · relative index</div>
              <div class="v index">{index} / 100</div></div>
            {quality}
          </div>
        </div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- evidence
_TONE_CLASS = {"good": "good", "concern": "concern",
               "neutral": "", "unknown": "limited"}


def status_label(text: str, tone: str) -> str:
    return (f'<span class="jx-status {_TONE_CLASS.get(tone, "")}">'
            f'{esc(text)}</span>')


def evidence_rows(rows: list[dict]) -> None:
    """
    The criteria pattern: category · conclusion · strongest number · status.
    One row per criterion, hairline-separated — never a card each.

    Each row dict: label, verdict, tone, conclusion, evidence_stat.
    'Not measured' renders with a dashed border so absence of evidence can
    never be misread as negative evidence.
    """
    parts = []
    for r in rows:
        stat = (f'<div class="ev">{esc(r["evidence_stat"])}</div>'
                if r.get("evidence_stat") else "")
        parts.append(
            f"""<div class="jx-row">
              <div class="cat">{esc(r['label'])}</div>
              <div class="body"><div class="concl">{esc(r['conclusion'])}</div>{stat}</div>
              <div class="status">{status_label(r['verdict'], r['tone'])}</div>
            </div>""")
    st.markdown("".join(parts), unsafe_allow_html=True)


def signal_strip(cells: list[tuple[str, str, str]]) -> None:
    """
    The 3-second read: short label, one- or two-word state, tone colour.
    No sentences here by design — the full evidence lives one expander away.

    Each cell: (label, state, tone) with tone in good/neutral/concern/unknown.
    """
    inner = "".join(
        f'<div class="cell"><div class="k">{esc(label)}</div>'
        f'<div class="s">{status_label(state, tone)}</div></div>'
        for label, state, tone in cells)
    st.markdown(f'<div class="jx-signals">{inner}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- stats
def stat_strip(cells: list[tuple[str, str]]) -> None:
    """One horizontal statistic row with hairline separators — never cards."""
    inner = "".join(
        f'<div class="cell"><div class="v">{esc(v)}</div>'
        f'<div class="k">{esc(k)}</div></div>'
        for v, k in cells)
    st.markdown(f'<div class="jx-strip">{inner}</div>', unsafe_allow_html=True)


def bench_rows(rows: list[tuple[str, str]]) -> None:
    inner = "".join(
        f'<div class="r"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>' for k, v in rows)
    st.markdown(f'<div class="jx-bench">{inner}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- competitors
def competitor_rows(rows: list[dict]) -> None:
    """Ranked-result rows: rank, name, mono meta line, status. Search-result
    grammar, not cards."""
    parts = []
    for i, r in enumerate(rows, 1):
        parts.append(
            f"""<div class="jx-comp">
              <div class="rank">{i:02d}</div>
              <div><div class="name">{esc(r['name'])}</div>
                   <div class="meta">{esc(r['meta'])}</div></div>
              <div>{status_label(r['status'], r.get('tone', 'neutral'))}</div>
            </div>""")
    st.markdown("".join(parts), unsafe_allow_html=True)


# ---------------------------------------------------------------- recommendation
def recommendation_panel(verdict: str, body_paragraphs: list[str],
                         positive: str | None, risk: str | None) -> None:
    paras = "".join(f"<p>{esc(p)}</p>" for p in body_paragraphs)
    pair = ""
    if positive or risk:
        cells = ""
        if positive:
            cells += (f'<div><div class="k">Main positive</div>'
                      f'<div class="v">{esc(positive)}</div></div>')
        if risk:
            cells += (f'<div><div class="k">Main risk</div>'
                      f'<div class="v">{esc(risk)}</div></div>')
        pair = f'<div class="pair">{cells}</div>'
    st.markdown(
        f"""<div class="jx-reco">
          <div class="jx-eyebrow"><span class="num">[06]</span>Our assessment</div>
          <div class="verdict">{esc(verdict)}.</div>
          {paras}{pair}
        </div>""", unsafe_allow_html=True)
