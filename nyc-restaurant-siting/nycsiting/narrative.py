"""
Turning the analysis into an answer.

Everything upstream of this module thinks in risk scores and confidence
intervals. An entrepreneur asks "is this a good place to open?" — so this layer
translates, and nothing else does. Keeping the translation here rather than
scattered through the UI means the wording can be tested: a sentence that
contradicts the numbers it summarises is a real defect, not a cosmetic one.

TWO DELIBERATE CHOICES

Fit, not risk. `scoring.py` produces a risk score where high is bad. Users read
"76/100" as good, every time, no matter what the label says — so the number
shown is inverted into a fit score and the underlying components are untouched.

Words, not numbers, per component. A component reading "Competition 87/100"
tells an entrepreneur nothing; "Competition: high" tells them everything. Each
component gets vocabulary that suits it, which is why competition reads
low/moderate/high while cuisine performance reads weak/average/strong.
"""
from __future__ import annotations

#: Fit bands. Ordered high-to-low; the first whose floor is met wins.
FIT_BANDS = [
    (80, "Strong fit"),
    (65, "Promising"),
    (45, "Mixed"),
    (30, "Higher risk"),
    (0, "High risk"),
]

#: Below GOOD_BELOW a component is favourable; above BAD_ABOVE it is a concern.
#: The same 35/65 cut points scoring.py already uses for its own bands, so the
#: words here never disagree with the number they describe.
GOOD_BELOW = 35
BAD_ABOVE = 65

#: component key -> (section heading, question it answers,
#:                   (favourable word, middling word, adverse word))
COMPONENTS = {
    "location_history": (
        "Location history", "What happened at this address before?",
        ("Stable", "Mixed", "High turnover")),
    "cuisine_track_record": (
        "Cuisine performance", "Does my concept make sense here?",
        ("Strong", "Average", "Weak")),
    "competition": (
        "Competition", "Who would I compete with?",
        ("Low", "Moderate", "High")),
    "area_retention": (
        "Area track record", "Do restaurants last around here?",
        ("Strong", "Average", "Weak")),
    "foot_traffic": (
        "Foot traffic", "Will people walk past?",
        ("High", "Moderate", "Low")),
    "property_fit": (
        "Property", "Is the building suited to food service?",
        ("Suitable", "Moderate", "Poor fit")),
}

#: Plain-English fragments for the summary sentence, keyed by component.
_PRAISE = {
    "location_history": "a settled history at this address",
    "cuisine_track_record": "a good track record for this concept nearby",
    "competition": "little direct competition",
    "area_retention": "restaurants that tend to last in this area",
    "foot_traffic": "strong footfall",
    "property_fit": "a building suited to food service",
}
_CONCERN = {
    "location_history": "heavy turnover at this address",
    "cuisine_track_record": "a poor track record for this concept nearby",
    "competition": "crowded competition",
    "area_retention": "restaurants that struggle to last in this area",
    "foot_traffic": "thin footfall",
    "property_fit": "a building poorly suited to food service",
}


def _an(word: str) -> str:
    """'an Italian concept', 'a Japanese concept'."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def fit_score(result: dict) -> int | None:
    """
    The risk score, inverted, so higher reads better.

    Not a new calculation — the same weighted total scoring.py already
    produced, pointed the way users instinctively read it.
    """
    score = result.get("score")
    return None if score is None else int(round(100 - score))


def fit_band(fit: int | None) -> str:
    if fit is None:
        return "Not enough data"
    return next(name for floor, name in FIT_BANDS if fit >= floor)


def tone(risk: float | None) -> str:
    """'good' / 'neutral' / 'concern' for one component's risk score."""
    if risk is None:
        return "unknown"
    if risk < GOOD_BELOW:
        return "good"
    if risk > BAD_ABOVE:
        return "concern"
    return "neutral"


def component_verdicts(result: dict) -> list[dict]:
    """
    One plain-language row per component, in weight order.

    Components that could not be measured are kept and marked, not dropped:
    "we could not check this" is information an entrepreneur should have, and
    silently omitting a row makes the evidence look more complete than it is.
    """
    out = []
    for component in result.get("components", []):
        key = component.get("key")
        if key not in COMPONENTS:
            continue
        heading, question, words = COMPONENTS[key]
        risk = component.get("score") if component.get("available") else None
        if risk is None:
            verdict, mood = "Not measured", "unknown"
        else:
            verdict = words[0] if risk < GOOD_BELOW else (
                words[2] if risk > BAD_ABOVE else words[1])
            mood = tone(risk)
        out.append({
            "key": key, "label": heading, "question": question,
            "verdict": verdict, "tone": mood, "risk": risk,
            "evidence": component.get("evidence", ""),
            "detail": component.get("detail", ""),
            "weight": component.get("weight", 0),
            "available": bool(component.get("available")),
        })
    return out


def _ranked(verdicts: list[dict]) -> tuple[list[dict], list[dict]]:
    """(favourable, concerning) — each sorted by how strongly it counts."""
    measured = [v for v in verdicts if v["risk"] is not None]
    good = sorted([v for v in measured if v["tone"] == "good"],
                  key=lambda v: (v["risk"], -v["weight"]))
    bad = sorted([v for v in measured if v["tone"] == "concern"],
                 key=lambda v: (-v["risk"], -v["weight"]))
    return good, bad


def headline(verdicts: list[dict], fit: int | None, cuisine: str) -> str:
    """
    One sentence a person can act on.

    Built from the strongest point in the site's favour and the strongest
    against it, so the sentence always matches the rows printed beneath it.
    """
    if fit is None:
        return ("There is not enough observable evidence about this address to "
                "form a view either way.")
    good, bad = _ranked(verdicts)

    def phrase(items, table, limit=2):
        names = [table[v["key"]] for v in items[:limit]]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return names[0] if names else ""

    if good and bad:
        return (f"{phrase(good, _PRAISE).capitalize()} "
                f"{'are' if len(good) > 1 else 'is'} offset by "
                f"{phrase(bad, _CONCERN)}.")
    if good:
        return (f"This address shows {phrase(good, _PRAISE)}, with nothing in "
                f"the data counting against a {cuisine} concept.")
    if bad:
        return (f"The main difficulty here is {phrase(bad, _CONCERN)}; nothing "
                f"in the data offsets it.")
    return (f"Nothing in the data strongly distinguishes this address for "
            f"{_an(cuisine)} {cuisine} concept, in either direction.")


def reason_to_proceed(verdicts: list[dict]) -> dict | None:
    """The single strongest argument for the site, with its evidence."""
    good, _ = _ranked(verdicts)
    if not good:
        return None
    top = good[0]
    return {"title": f"{top['label']}: {top['verdict'].lower()}",
            "detail": top["evidence"]}


def reason_for_caution(verdicts: list[dict], landscape=None) -> dict | None:
    """
    The single strongest argument against.

    Live competition outranks the public-data components when it is available
    and severe: an entrepreneur about to sign a lease should hear "three strong
    rivals within walking distance" before anything derived from a decade-old
    inspection archive.
    """
    if landscape is not None and getattr(landscape, "ok", False):
        if landscape.strong >= 3:
            return {
                "title": f"{landscape.strong} strong competitors within "
                         f"{landscape.radius_m:.0f}m",
                "detail": ("These are trading now, well rated and heavily "
                           "reviewed. They compete for the same customers on "
                           "the same evenings.")}
    _, bad = _ranked(verdicts)
    if not bad:
        return None
    top = bad[0]
    return {"title": f"{top['label']}: {top['verdict'].lower()}",
            "detail": top["evidence"]}


def assessment_label(fit: int | None, landscape=None) -> str:
    """
    The recommendation heading — the band, qualified when live competition
    disagrees with it.
    """
    band = fit_band(fit)
    if band == "Not enough data":
        return band
    if landscape is not None and getattr(landscape, "ok", False):
        if landscape.strong >= 3 and fit is not None and fit >= 65:
            return f"{band} — with competitive risk"
    return band


def comparison_row(address: str, cuisine: str, fit: int | None,
                   verdicts: list[dict], landscape=None) -> dict:
    """One saved candidate, flattened for the side-by-side table."""
    by_key = {v["key"]: v["verdict"] for v in verdicts}
    row = {
        "Location": address,
        "Concept": cuisine,
        "Overall fit": fit if fit is not None else "—",
        "Location history": by_key.get("location_history", "—"),
        "Cuisine performance": by_key.get("cuisine_track_record", "—"),
        "Competition (history)": by_key.get("competition", "—"),
        "Area track record": by_key.get("area_retention", "—"),
        "Foot traffic": by_key.get("foot_traffic", "—"),
    }
    if landscape is not None and getattr(landscape, "ok", False):
        row["Competitors now"] = landscape.total
        row["Strong rivals"] = landscape.strong
    return row


def evidence_quality(result: dict, report: dict, landscape=None) -> tuple[str, list[str]]:
    """
    'Strong' / 'Moderate' / 'Limited', with the reasons.

    A verdict built from all six components and a deep local sample deserves
    more trust than one built from half the weight and three restaurants — and
    without this label the two render identically. Three stated checks, no
    tuning: evidence-weight coverage, local cohort depth, live-data presence.
    """
    reasons = []
    points = 0

    coverage = result.get("coverage") or 0.0
    if coverage >= 0.8:
        points += 1
        reasons.append(f"{coverage*100:.0f}% of the evidence weight was measurable")
    else:
        reasons.append(f"only {coverage*100:.0f}% of the evidence weight was measurable")

    area_n = report.get("area", {}).get("cohort", {}).get("total", 0)
    if area_n >= 100:
        points += 1
        reasons.append(f"{area_n} nearby restaurants in the historical cohort")
    else:
        reasons.append(f"only {area_n} nearby restaurants in the historical cohort")

    if landscape is not None and getattr(landscape, "ok", False):
        points += 1
        reasons.append("live competitor data is available")
    else:
        reasons.append("live competitor data is unavailable")

    label = ["Limited", "Limited", "Moderate", "Strong"][points]
    return label, reasons
