"""
The comparison PDF: a professional analyst report rendered entirely from a
frozen ComparisonReportPayload — never from live analyses, never from the
network. Pure reportlab (no system binaries), bytes in memory, so it runs
identically on Streamlit Community Cloud.

Narrative text, when supplied, is the optional LLM layer's output — already
validated upstream to contain no numerals. Every number printed here comes
from the payload directly.
"""
from __future__ import annotations

import io
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from .comparison import (TURNOVER_READ, ComparisonReportPayload,
                         deterministic_summary)

NAVY = colors.HexColor("#0B1F33")
MINT = colors.HexColor("#1F8A66")
MUTED = colors.HexColor("#5A6B7C")
HAIR = colors.HexColor("#D7DEE6")
BG = colors.HexColor("#F4F7FA")

_RISK_COLOR = {"Low": colors.HexColor("#1F8A66"),
               "Moderate": colors.HexColor("#B07C24"),
               "High": colors.HexColor("#B04A4A"),
               "Insufficient evidence": MUTED}


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Helvetica", textColor=NAVY)
    return {
        "title": ParagraphStyle("title", fontSize=22, leading=26,
                                fontName="Helvetica-Bold", textColor=NAVY),
        "subtitle": ParagraphStyle("subtitle", fontSize=12, leading=16,
                                   textColor=MINT,
                                   fontName="Helvetica-Bold"),
        "h2": ParagraphStyle("h2", fontSize=14, leading=18,
                             fontName="Helvetica-Bold", textColor=NAVY,
                             spaceBefore=14, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontSize=10, leading=13,
                             fontName="Helvetica-Bold", textColor=MINT,
                             spaceBefore=10, spaceAfter=2),
        "body": ParagraphStyle("body", fontSize=9.5, leading=13.5, **base),
        "muted": ParagraphStyle("muted", fontSize=8.5, leading=12,
                                fontName="Helvetica", textColor=MUTED),
        "cell": ParagraphStyle("cell", fontSize=8.5, leading=11.5, **base),
        "cellm": ParagraphStyle("cellm", fontSize=8.5, leading=11.5,
                                fontName="Helvetica", textColor=MUTED),
    }


def _table(data, widths, style_extras=(), header=True) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, HAIR),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), BG))
    table.setStyle(TableStyle(style + list(style_extras)))
    return table


def _fmt(value, dash="—") -> str:
    return dash if value in (None, "", []) else str(value)


def esc(text) -> str:
    """
    reportlab Paragraphs parse a mini-markup, so any '&', '<' or '>' in a
    value — or an unbalanced tag in model-written narrative — would abort
    the build. Everything variable goes through here.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def report_filename(payload: ComparisonReportPayload) -> str:
    """siting_<concept>_<area>_<area>.pdf — sanitized."""
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:28]
    parts = ["siting"]
    if payload.concept_line:
        parts.append(slug(payload.concept_line))
    parts += [slug(a.name) for a in payload.areas]
    return "_".join(p for p in parts if p) + ".pdf"


def render_pdf(payload: ComparisonReportPayload,
               narrative: dict | None = None) -> bytes:
    """The whole report, in memory. `narrative` maps 'executive' and area
    codes to validated prose; anything missing falls back to deterministic
    sentences built from the payload."""
    st = _styles()
    narrative = narrative or {}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER, leftMargin=0.85 * inch,
        rightMargin=0.85 * inch, topMargin=0.8 * inch,
        bottomMargin=0.8 * inch, title="NYC Restaurant Location Comparison")
    width = doc.width
    flow = []

    # ---------------------------------------------------------- page 1
    flow.append(Paragraph("NYC RESTAURANT LOCATION COMPARISON", st["title"]))
    if payload.concept_line:
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(esc(payload.concept_line), st["subtitle"]))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(esc(" · ".join(a.name for a in payload.areas)),
                          st["body"]))
    flow.append(Paragraph(esc(f"Generated by Siting · {payload.generated}"),
                          st["muted"]))
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", color=HAIR, thickness=0.75))

    concept_fit = all(a.fit_is_concept for a in payload.areas)

    flow.append(Paragraph("Executive comparison", st["h2"]))
    exec_text = narrative.get("executive") or " ".join(
        deterministic_summary(a) for a in payload.areas)
    flow.append(Paragraph(esc(exec_text), st["body"]))

    flow.append(Paragraph("Where each area leads", st["h3"]))
    leaders = payload.leaders
    rows = [[Paragraph("<b>Dimension</b>", st["cell"]),
             Paragraph("<b>Leading area(s)</b>", st["cell"])]]
    low_band = leaders.get("lowest_competition_band")
    for key, label in (("leading_fit", "Relative concept fit"),
                       ("lowest_competition", "Lowest competition"),
                       ("strongest_evidence", "Strongest evidence")):
        if key == "leading_fit" and not concept_fit:
            label = "Highest restaurant persistence"
        names = leaders.get(key) or []
        value = " / ".join(names)
        # "Lowest" among three crowded areas is still crowded — say so.
        if key == "lowest_competition" and names and low_band == "High":
            value += " (still high)"
        rows.append([Paragraph(label, st["cell"]),
                     Paragraph(esc(value) if names
                               else "Insufficient evidence", st["cellm"])])
    flow.append(_table(rows, [width * 0.45, width * 0.55]))

    flow.append(Paragraph("Recommendation", st["h3"]))
    flow.append(Paragraph(esc(payload.recommendation), st["body"]))

    flow.append(Spacer(1, 10))
    flow.append(Paragraph((f"<b>Important limitation.</b> "
                           f"{esc(payload.limitations[0])}")
                          if payload.limitations else "", st["muted"]))

    # ------------------------------------------------------- comparison
    flow.append(Paragraph("Side-by-side comparison", st["h2"]))
    header = [Paragraph("<b>Metric</b>", st["cell"])] + [
        Paragraph(f"<b>{esc(a.name)}</b>", st["cell"]) for a in payload.areas]
    metric_rows = [
        ("Relative concept fit" if concept_fit
         else "Restaurant persistence (all concepts)",
         lambda a: (f"{a.fit_index:.0f} / 100" if a.fit_index is not None
                    else "Not measured")),
        ("Band", lambda a: _fmt(a.fit_band, "Not measured")),
        ("Evidence quality", lambda a: _fmt(a.evidence)),
        ("Competition", lambda a: _fmt(a.competition_band, "Not measured")),
        ("Observed turnover", lambda a: TURNOVER_READ.get(
            a.turnover, _fmt(a.turnover))),
        ("Pedestrian context", lambda a: _fmt(a.ped_band, "Not measured")),
        ("Restaurants (current)", lambda a: f"{a.restaurants_total:,}"),
        ("Similar concept", lambda a: _fmt(a.similar_count)),
        ("Closest match", lambda a: _fmt(a.closest_count)),
    ]
    table_rows = [header]
    for label, getter in metric_rows:
        table_rows.append([Paragraph(label, st["cell"])] + [
            Paragraph(esc(getter(a)), st["cellm"]) for a in payload.areas])
    col = [width * 0.28] + [(width * 0.72) / len(payload.areas)] * len(
        payload.areas)
    flow.append(_table(table_rows, col))

    # ------------------------------------------------------- per area
    for area in payload.areas:
        flow.append(PageBreak())
        flow.append(Paragraph(esc(area.name.upper()), st["h2"]))
        index_name = ("Relative concept fit" if area.fit_is_concept
                      else "Restaurant persistence (all concepts)")
        fit = (f"{area.fit_index:.0f} / 100 · {area.fit_band} "
               f"({index_name})" if area.fit_index is not None else
               f"{index_name}: not measured")
        flow.append(Paragraph(
            esc(f"{fit} · Evidence quality: {_fmt(area.evidence)}"),
            st["subtitle"]))
        text = narrative.get(area.code) or deterministic_summary(area)
        flow.append(Paragraph(esc(text), st["body"]))

        flow.append(Paragraph("Key metrics", st["h3"]))
        km = [
            ["Current restaurants", f"{area.restaurants_total:,}"],
            ["Similar concept", _fmt(area.similar_count)],
            ["Closest match", _fmt(area.closest_count)],
            ["Competition", _fmt(area.competition_band, "Not measured")],
            ["Observed turnover", _fmt(area.turnover)],
            ["Pedestrian context",
             (f"{area.ped_band} ({area.ped_sites} DOT site(s))"
              if area.ped_band else "Not measured in this area")],
            ["Historical cohort", f"{area.cohort_n:,} restaurants"],
        ]
        if area.persistence_rate is not None:
            km.append(["Still listed (2011–17 → 2026)",
                       f"{area.persistence_rate:.0%}"])
        if area.income_context is not None:
            # NOT a median: nta.py rolls tract medians up as an explicitly
            # population-weighted indicator, and the label has to say so.
            km.append(["ACS income context (population-weighted indicator, "
                       "not a median)", f"${area.income_context:,.0f}"])
        if area.population is not None:
            km.append(["ACS population (sum of component tracts)",
                       f"{area.population:,.0f}"])
        flow.append(_table(
            [[Paragraph(k, st["cell"]), Paragraph(esc(v), st["cellm"])]
             for k, v in km], [width * 0.45, width * 0.55], header=False))

        # Parenthesised deliberately: `[header] + [items] or [dash]` binds
        # as `[header] + ([items] or [dash])`, so an empty list would print
        # a bare heading rather than the dash.
        pros_items = [
            Paragraph(f"+ {esc(p.label)}"
                      + (f" <font color='#5A6B7C'>({esc(p.metric)})</font>"
                         if p.metric else ""), st["cell"])
            for p in area.pros]
        cons_items = [
            Paragraph(f"– {esc(c.label)}"
                      + (f" <font color='#5A6B7C'>({esc(c.metric)})</font>"
                         if c.metric else ""), st["cell"])
            for c in area.cons]
        none_found = "No standout signals under current evidence."
        pros_cell = [Paragraph("<b>Pros</b>", st["cell"])] + (
            pros_items or [Paragraph(none_found, st["cellm"])])
        cons_cell = [Paragraph("<b>Cons</b>", st["cell"])] + (
            cons_items or [Paragraph(none_found, st["cellm"])])
        flow.append(Spacer(1, 8))
        flow.append(_table([[pros_cell, cons_cell]],
                           [width * 0.5, width * 0.5], header=False))

        flow.append(Paragraph("Risk analysis", st["h3"]))
        risk_rows = [[Paragraph("<b>Risk</b>", st["cell"]),
                      Paragraph("<b>Level</b>", st["cell"]),
                      Paragraph("<b>Evidence</b>", st["cell"]),
                      Paragraph("<b>Why</b>", st["cell"])]]
        extras = []
        for i, r in enumerate(area.risks, start=1):
            risk_rows.append([
                Paragraph(esc(r.category), st["cell"]),
                Paragraph(esc(r.level), st["cell"]),
                Paragraph(esc(r.evidence), st["cellm"]),
                Paragraph(esc(r.why), st["cellm"])])
            extras.append(("TEXTCOLOR", (1, i), (1, i),
                           _RISK_COLOR.get(r.level, NAVY)))
        flow.append(_table(risk_rows,
                           [width * 0.28, width * 0.16, width * 0.14,
                            width * 0.42], style_extras=extras))

    # ------------------------------------------------------ method
    flow.append(PageBreak())
    flow.append(Paragraph("Methodology", st["h2"]))
    for label, text in payload.methodology:
        flow.append(Paragraph(f"<b>{esc(label)}.</b> {esc(text)}", st["body"]))
        flow.append(Spacer(1, 4))
    flow.append(Paragraph("Limitations", st["h2"]))
    for item in payload.limitations:
        flow.append(Paragraph(f"• {esc(item)}", st["body"]))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(esc(payload.claude_disclosure), st["muted"]))

    doc.build(flow)
    return buffer.getvalue()
