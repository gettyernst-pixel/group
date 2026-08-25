"""Small statistics helpers, kept separate so they can be tested in isolation."""
from __future__ import annotations

import math


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """
    Confidence interval for a proportion.

    Needed because the counts here range from 3 restaurants at one address to
    900 in a neighbourhood. A 40% survival rate off 5 restaurants and off 500
    are not the same claim, and comparing the raw percentages would treat them
    as though they were. Wilson rather than normal-approximation because it
    stays sensible at the tiny counts that a single address produces.
    """
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def rate_differs(successes: int, total: int, baseline: float) -> str:
    """
    'below', 'above' or 'inconclusive' — whether a rate really differs from a
    baseline, given how few observations it rests on.
    """
    if total <= 0:
        return "inconclusive"
    lo, hi = wilson_interval(successes, total)
    if hi < baseline:
        return "below"
    if lo > baseline:
        return "above"
    return "inconclusive"
