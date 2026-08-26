"""
Optional LLM narrative for the comparison report.

TRUST BOUNDARY
Claude receives EXACTLY one thing: the serialized ComparisonReportPayload —
the deterministic engine's frozen output. It is a reporting editor, never an
analyst: it may re-express the supplied evidence, and nothing else.

Guards, strongest first:

1. `_validate` MECHANICALLY drops any sentence carrying a quantity —
   digits, currency, percentages, or spelled-out numbers. Every figure in
   the report is inserted by the renderer from the payload, so a quantity
   in model prose is unverifiable by construction.
2. Area paragraphs are keyed to the payload's own area codes; a code the
   payload does not contain is discarded, so the model cannot invent an
   area.
3. The system prompt forbids external facts, invented pros/cons/rankings,
   and probability framing. This one is a prompt, not a mechanism: purely
   QUALITATIVE invention ("the area feels established") would survive it,
   which is why the report's factual rows, levels and rankings are always
   rendered from the payload rather than from this text.

If anything fails — no key, API error, malformed output — the caller falls
back to deterministic sentences. The report never depends on the model.
"""
from __future__ import annotations

import json
import re

from .comparison import ComparisonReportPayload

#: Same low-latency model as the plan parser; defined here so the report
#: layer never imports the parser module.
NARRATIVE_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You are a reporting editor for a restaurant location \
analysis product.

You will receive ONE structured JSON payload of deterministic analytical \
results comparing NYC areas. Rewrite that evidence as short, plain-English \
narrative for a prospective restaurant operator.

STRICT RULES:
- Use ONLY facts present in the payload. No external knowledge about NYC, \
neighborhoods, cuisines, or restaurants. No assumptions.
- Never write numerals or statistics of any kind — the report inserts all \
numbers separately from the payload. Describe qualitatively ("high", \
"lower than the comparison areas").
- Never invent pros, cons, risks, or rankings not present in the payload.
- Never present anything as a probability of success. Keep the framing \
relative and observational ("relative fit", "observed", "evidence").
- Do not change or contradict any level, band, ranking, or recommendation.

Return JSON: {"executive": "...", "areas": {"<code>": "...", ...}, \
"tradeoffs": "..."} — executive of at most four sentences, one two-to-three \
sentence paragraph per area code, and a one-sentence tradeoff summary."""

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Quantity language the model must not produce either: a spelled-out
#: figure is exactly as unverifiable as a digit, and "%"/"percent" can only
#: come from a number the renderer, not the model, is responsible for.
_NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "million", "percent", "percentage", "half", "third", "quarter",
    "double", "triple", "twice",
)
_QUANTITY = re.compile(
    r"[\d%$]|\b(?:" + "|".join(_NUMBER_WORDS) + r")\b", re.I)


def _validate(text: str) -> str:
    """
    Drop any sentence carrying a quantity — digits, currency, percentages,
    or spelled-out numbers. Every figure in the report is inserted by the
    renderer straight from the payload, so a quantity in model prose is
    unverifiable by construction and is removed rather than trusted.
    """
    kept = [s for s in _SENTENCE_SPLIT.split(text or "")
            if s and not _QUANTITY.search(s)]
    return " ".join(kept).strip()


def narrate(payload: ComparisonReportPayload,
            api_key: str | None, client=None) -> dict | None:
    """Validated narrative dict, or None when the LLM layer is unavailable
    — the caller then uses deterministic prose. Never raises."""
    if not api_key and client is None:
        return None
    try:
        import anthropic
        client = client or anthropic.Anthropic(api_key=api_key)
        # The structured-output dialect rejects schema-valued
        # additionalProperties (verified live, 400) — the area map is built
        # with explicit per-code properties from the payload instead.
        area_props = {a.code: {"type": "string"} for a in payload.areas}
        response = client.messages.create(
            model=NARRATIVE_MODEL, max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": payload.json()}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "executive": {"type": "string"},
                    "areas": {"type": "object",
                              "additionalProperties": False,
                              "properties": area_props,
                              "required": list(area_props)},
                    "tradeoffs": {"type": "string"},
                },
                "required": ["executive", "areas", "tradeoffs"],
            }}})
        raw = json.loads(next(b.text for b in response.content
                              if b.type == "text"))
    except Exception:
        return None

    # Validation is inside the guard too: a malformed shape from the model
    # must degrade to deterministic prose, never raise into the export.
    try:
        known_codes = {a.code for a in payload.areas}
        out: dict = {}
        executive = _validate(raw.get("executive", ""))
        if executive:
            out["executive"] = executive
        tradeoffs = _validate(raw.get("tradeoffs", ""))
        if tradeoffs:
            out["tradeoffs"] = tradeoffs
        for code, text in (raw.get("areas") or {}).items():
            if code in known_codes:             # never a hallucinated area
                cleaned = _validate(str(text))
                if cleaned:
                    out[code] = cleaned
        return out or None
    except Exception:
        return None
