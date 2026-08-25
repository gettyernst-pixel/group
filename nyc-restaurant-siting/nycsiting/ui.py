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
    st.markdown(f"<style>{_CSS_PATH.read_text()}</style>", unsafe_allow_html=True)


def esc(value: object) -> str:
    return _html.escape(str(value))


# ---------------------------------------------------------------- shell
def page_header(stage: str, simulate_enabled: bool) -> None:
    """
    Wordmark + stage progression. Stages communicate where the user is;
    Simulate stays faint until an assessment exists to simulate from.
    """
    def cls(name: str) -> str:
        order = ["explore", "assess", "simulate"]
        if name == stage:
            return "jx-stage active"
        if name == "simulate" and not simulate_enabled:
            return "jx-stage"
        if order.index(name) < order.index(stage):
            return "jx-stage done"
        return "jx-stage"

    st.markdown(
        f"""<div class="jx-header">
          <div class="jx-wordmark">Siting</div>
          <div class="jx-stages">
            <span class="{cls('explore')}">Explore</span>
            <span class="{cls('assess')}">Assess</span>
            <span class="{cls('simulate')}">Simulate</span>
          </div>
          <div class="jx-header-right">Methodology below</div>
        </div>""", unsafe_allow_html=True)


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
