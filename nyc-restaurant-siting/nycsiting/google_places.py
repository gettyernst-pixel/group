"""
Optional live-competitor layer, on top of the public-data analysis.

WHAT THIS ADDS
The DOHMH layer can say "ten Italian restaurants nearby". It cannot say whether
those are ten struggling places or ten institutions, and those are very
different things to open next to. Google Places supplies the present-tense
signal the public data has no equivalent for: ratings, review volume, and
whether a business is still trading.

WHAT IT DELIBERATELY DOES NOT DO
- It does not replace the DOHMH analysis. DOHMH answers the PAST (who operated
  here, what closed, how the concept fared); Google answers the PRESENT (who is
  trading now and how strong they look).
- It does not feed the screening score. Competitive pressure is reported beside
  that score as its own component, not folded into it — the scoring weights in
  scoring.py were set against measured evidence, and quietly slipping a new
  input into them would invalidate the whole thing.
- It does not geocode. The app already resolves addresses through NYC Planning's
  GeoSearch, which is free and returns a BBL besides; spending a Google call to
  re-answer a question we have already answered would be waste.

FAILURE IS EXPECTED, NOT EXCEPTIONAL
No key, an expired key, exhausted quota, a timeout, a cuisine Google has never
heard of — all of these are ordinary. Nothing here raises into the UI: every
entry point returns a CompetitorLandscape whose `ok` flag and `message` say what
happened, so the core analysis renders regardless.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
import requests

from .geo import haversine_m
from .normalize import normalize_building, normalize_street

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

#: ~10 minutes' walk. Wider than the DOHMH default because this question is
#: "who would a diner choose instead of me", not "what is on this block".
DEFAULT_RADIUS_M = 750
DEFAULT_PAGE_SIZE = 20
TIMEOUT_S = 10

#: Ask for exactly the fields we use. Google bills Text Search by field tier,
#: so a lazy "*" mask costs real money per call.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.location",
    "places.priceLevel",
])

#: Below this many reviews a high rating is noise, and a place cannot be
#: labelled a Strong competitor on the back of it.
MIN_REVIEWS_FOR_STRONG = 20

#: Google's own word for "this business is gone". Anything gone is not a
#: competitor. A missing status is treated as trading — Places omits the field
#: for most healthy listings rather than spelling out OPERATIONAL.
CLOSED_PERMANENTLY = "CLOSED_PERMANENTLY"

#: Google's price enum -> the notation a restaurateur actually uses.
PRICE_LABELS = {
    "PRICE_LEVEL_FREE": "Free",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

#: What the user can pick for their own concept.
PRICE_CHOICES = ["$", "$$", "$$$", "$$$$"]


def price_label(value: object) -> str:
    """'$$' from Google's PRICE_LEVEL_MODERATE, or '—' when unpriced."""
    return PRICE_LABELS.get(value, "—") if isinstance(value, str) else "—"


def price_mix(df) -> dict[str, int]:
    """
    How the nearby competitors are priced.

    Lets the app answer "am I pitching where this block already sits?" — Google
    leaves priceLevel unset on plenty of listings, so unpriced places are simply
    absent from the counts rather than being guessed at.
    """
    if df is None or df.empty or "price_level" not in df:
        return {}
    counts: dict[str, int] = {}
    for value in df["price_level"]:
        label = price_label(value)
        if label in PRICE_CHOICES:
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: PRICE_CHOICES.index(kv[0])))


COLUMNS = ["place_id", "name", "address", "rating", "reviews",
           "business_status", "price_level", "distance_m",
           "latitude", "longitude"]

SCORED_COLUMNS = COLUMNS + ["rating_score", "review_score", "distance_score",
                            "competitor_score", "competitor_strength"]


class PlacesError(Exception):
    """Something went wrong upstream. Carries text fit to show a user."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class CompetitorLandscape:
    """
    The whole result of asking Google about a site — including the failures.

    Returned by `fetch_landscape` in every case, so the caller never needs a
    try/except and the UI has one shape to render.
    """
    ok: bool
    competitors: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=SCORED_COLUMNS))
    reason: str | None = None
    message: str | None = None
    radius_m: float = DEFAULT_RADIUS_M
    cuisine: str = ""

    @property
    def total(self) -> int:
        return len(self.competitors)

    @property
    def strong(self) -> int:
        return int((self.competitors.get("competitor_strength") == "Strong").sum())

    @property
    def moderate(self) -> int:
        return int((self.competitors.get("competitor_strength") == "Moderate").sum())

    @property
    def mean_rating(self) -> float | None:
        if self.competitors.empty:
            return None
        rated = self.competitors["rating"].dropna()
        return float(rated.mean()) if len(rated) else None

    @property
    def strongest(self) -> dict | None:
        if self.competitors.empty:
            return None
        return self.competitors.iloc[0].to_dict()


# --------------------------------------------------------------------------
# talking to Google
# --------------------------------------------------------------------------
def search_places(lat: float, lon: float, cuisine: str, api_key: str,
                  radius: float = DEFAULT_RADIUS_M,
                  page_size: int = DEFAULT_PAGE_SIZE,
                  session=None) -> list[dict]:
    """
    Raw Text Search call. Raises PlacesError with user-facing text on failure.

    `locationBias` rather than `locationRestriction`: bias lets Google rank by
    relevance around the point and still return the well-known place just
    outside the circle, which we then drop ourselves. Restriction makes Google
    the arbiter of the boundary, and we would rather enforce a radius we can
    explain.
    """
    if not api_key:
        raise PlacesError("no_key", "No Google Maps API key is configured.")
    if not cuisine or not str(cuisine).strip():
        raise PlacesError("no_cuisine", "No restaurant concept was given to search for.")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": f"{cuisine} restaurant",
        "pageSize": int(page_size),
        "locationBias": {
            "circle": {
                "center": {"latitude": float(lat), "longitude": float(lon)},
                "radius": float(radius),
            }
        },
    }

    poster = (session or requests).post
    try:
        response = poster(SEARCH_URL, headers=headers, json=body, timeout=TIMEOUT_S)
    except requests.Timeout as exc:
        raise PlacesError("timeout", "Google Places did not respond in time.") from exc
    except requests.RequestException as exc:
        raise PlacesError("network", "Could not reach Google Places.") from exc

    if response.status_code in (401, 403):
        raise PlacesError(
            "auth",
            "Google rejected the API key. Check that the key is valid and that "
            "the Places API (New) is enabled for it.")
    if response.status_code == 429:
        raise PlacesError(
            "quota",
            "The Google Places quota for this key has been exhausted.")
    if response.status_code >= 400:
        # Google reports an invalid key as a plain 400, not a 401/403.
        try:
            detail = str(response.json())
        except Exception:
            detail = ""
        if "API key not valid" in detail or "API_KEY_INVALID" in detail:
            raise PlacesError(
                "auth",
                "Google rejected the API key as invalid — check the key in "
                "Google Cloud (billing, rotation, or API restrictions).")
        raise PlacesError(
            "http_error",
            f"Google Places returned an error (HTTP {response.status_code}).")

    try:
        payload = response.json()
    except ValueError as exc:
        raise PlacesError("bad_response",
                          "Google Places returned a response we could not read.") from exc

    if not isinstance(payload, dict):
        raise PlacesError("bad_response",
                          "Google Places returned a response we could not read.")
    return payload.get("places") or []


# --------------------------------------------------------------------------
# shaping the response
# --------------------------------------------------------------------------
def _is_subject_site(address: str | None, site: dict | None) -> bool:
    """
    Is this Google result the very building the user is evaluating?

    Someone screening a site they already occupy would otherwise see their own
    restaurant listed as their strongest competitor. Matched on house number and
    street rather than on distance, because a genuine rival next door can sit
    within a few metres and must not be discarded.
    """
    if not site or not address:
        return False
    building = normalize_building(site.get("housenumber"))
    street = normalize_street(site.get("street"))
    if not building or not street:
        return False
    parts = str(address).split(",")
    if not parts:
        return False
    head = parts[0].strip()
    tokens = head.split(None, 1)
    if len(tokens) < 2:
        return False
    return (normalize_building(tokens[0]) == building
            and normalize_street(tokens[1]) == street)


def to_dataframe(places: list[dict], lat: float, lon: float,
                 radius: float = DEFAULT_RADIUS_M,
                 site: dict | None = None) -> pd.DataFrame:
    """
    Google's JSON into one row per current competitor.

    Drops, in order: results with no coordinates (we cannot place or measure
    them), results Google marks permanently closed, the subject site itself, and
    anything outside the radius. The radius filter is ours because `locationBias`
    is a hint — Google will happily return a famous restaurant a kilometre away.
    """
    rows = []
    for place in places or []:
        location = place.get("location") or {}
        place_lat = location.get("latitude")
        place_lon = location.get("longitude")
        if place_lat is None or place_lon is None:
            continue

        status = place.get("businessStatus")
        if status == CLOSED_PERMANENTLY:
            continue

        address = place.get("formattedAddress")
        if _is_subject_site(address, site):
            continue

        distance = float(haversine_m(lat, lon, float(place_lat), float(place_lon)))
        if distance > radius:
            continue

        display = place.get("displayName") or {}
        rows.append({
            "place_id": place.get("id"),
            "name": display.get("text") or "(unnamed)",
            "address": address,
            "rating": place.get("rating"),
            "reviews": place.get("userRatingCount") or 0,
            "business_status": status or "OPERATIONAL",
            "price_level": place.get("priceLevel"),
            "distance_m": round(distance),
            "latitude": float(place_lat),
            "longitude": float(place_lon),
        })

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    # One listing per place: Text Search can repeat a chain across pages.
    return df.drop_duplicates("place_id", keep="first").reset_index(drop=True)


# --------------------------------------------------------------------------
# competitor strength
# --------------------------------------------------------------------------
def add_competitor_strength(df: pd.DataFrame,
                            radius: float = DEFAULT_RADIUS_M) -> pd.DataFrame:
    """
    A deliberately simple, fully explainable strength score out of 100.

        rating      50 points
        reviews     30 points
        proximity   20 points

    The review term is logarithmic on purpose. Five stars from four reviews is a
    weaker signal than 4.6 from three thousand, and a linear term would let one
    landmark restaurant flatten every other score on the block. log10(n+1)/4
    saturates at 10,000 reviews.

    An unrated place scores zero on the rating term. That is the honest reading —
    we have no evidence it is strong — but it does mean a brand-new rival is
    scored low, which the UI says out loud.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=SCORED_COLUMNS)

    out = df.copy()
    radius = float(radius) or DEFAULT_RADIUS_M

    rating = pd.to_numeric(out["rating"], errors="coerce").fillna(0).clip(0, 5)
    out["rating_score"] = rating / 5 * 50

    reviews = pd.to_numeric(out["reviews"], errors="coerce").fillna(0).clip(lower=0)
    out["review_score"] = reviews.apply(
        lambda n: min(math.log10(n + 1) / 4, 1.0)) * 30

    distance = pd.to_numeric(out["distance_m"], errors="coerce").fillna(radius)
    out["distance_score"] = (1 - distance / radius).clip(0, 1) * 20

    out["competitor_score"] = (
        out["rating_score"] + out["review_score"] + out["distance_score"]
    ).round(1)
    out["competitor_strength"] = pd.cut(
        out["competitor_score"], bins=[-0.01, 45, 70, 100.01],
        labels=["Weak", "Moderate", "Strong"]).astype(str)
    # A 4.9 from eight reviews can cross the Strong threshold on the rating
    # term alone, and a rating on that few reviews is too unstable to ground
    # the label. The SCORE is left alone (the ranking is fine — review volume
    # already outweighs it); only the label is capped, and the audit trail
    # keeps why.
    thin = out["reviews"].fillna(0) < MIN_REVIEWS_FOR_STRONG
    out.loc[thin & (out["competitor_strength"] == "Strong"),
            "competitor_strength"] = "Moderate"

    return out.sort_values("competitor_score", ascending=False).reset_index(drop=True)


#: The pressure rule, written out so it can be shown to the user verbatim.
PRESSURE_RULES = [
    ("High", lambda n, strong: strong >= 3,
     "3 or more strong competitors nearby"),
    ("High", lambda n, strong: n >= 10 and strong >= 1,
     "10 or more competitors nearby, at least one of them strong"),
    ("Low", lambda n, strong: n <= 3 and strong == 0,
     "3 or fewer competitors nearby and none of them strong"),
    ("Moderate", lambda n, strong: True,
     "neither the high nor the low threshold is met"),
]


def classify_pressure(df: pd.DataFrame) -> tuple[str, str]:
    """
    'Low' / 'Moderate' / 'High', plus the reason in words.

    Four stated rules checked in order rather than a fitted formula. Nothing in
    the available data would justify tuned coefficients here, and a rule the
    user can check by eye is worth more than a number they have to trust.
    """
    if df is None or df.empty:
        return "Low", "no competing restaurants were found within the radius"
    n = len(df)
    strong = int((df["competitor_strength"] == "Strong").sum())
    for label, rule, because in PRESSURE_RULES:
        if rule(n, strong):
            return label, because
    return "Moderate", "neither the high nor the low threshold is met"


# --------------------------------------------------------------------------
# the one entry point the UI needs
# --------------------------------------------------------------------------
def fetch_landscape(lat: float, lon: float, cuisine: str, api_key: str | None,
                    radius: float = DEFAULT_RADIUS_M,
                    page_size: int = DEFAULT_PAGE_SIZE,
                    site: dict | None = None,
                    session=None) -> CompetitorLandscape:
    """
    Everything, safely. Never raises — inspect `.ok` and `.message` instead.

    The enrichment is optional by design, so any failure has to degrade to "the
    core analysis is still below", never to a traceback in the user's face.
    """
    empty = CompetitorLandscape(ok=False, radius_m=radius, cuisine=cuisine)

    if not api_key:
        empty.reason = "no_key"
        empty.message = ("Live competitor analysis is unavailable because no "
                         "Google Maps API key has been configured.")
        return empty

    try:
        places = search_places(lat, lon, cuisine, api_key, radius, page_size,
                               session=session)
    except PlacesError as exc:
        empty.reason = exc.reason
        empty.message = exc.message
        return empty
    except Exception:
        empty.reason = "unexpected"
        empty.message = "Live competitor data is currently unavailable."
        return empty

    # Parsing and scoring get their own guard. The network is the obvious
    # failure, but a malformed field or an unforeseen type from Google would
    # crash here just as hard, and this layer is supposed to be optional.
    try:
        frame = add_competitor_strength(
            to_dataframe(places, lat, lon, radius, site=site), radius)
    except Exception:
        empty.reason = "unexpected"
        empty.message = ("Live competitor data could not be interpreted. "
                         "The core location analysis below is unaffected.")
        return empty

    return CompetitorLandscape(
        ok=True, competitors=frame, radius_m=radius, cuisine=cuisine,
        reason=None if len(frame) else "empty",
        message=None if len(frame) else
        f"Google returned no {cuisine} restaurants within {radius:.0f}m of this address.")
