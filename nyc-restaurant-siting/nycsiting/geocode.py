"""Address -> coordinates via NYC Planning's GeoSearch (free, no key, returns BBL)."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import json

from .normalize import DIRECTION, SUFFIX, normalize_street, normalize_borough

GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"
_HOUSE_NUMBER = re.compile(r"^\s*\d+(-\d+)?\s+\S")


class GeocodeError(Exception):
    pass


def has_house_number(query: str) -> bool:
    return bool(_HOUSE_NUMBER.match(query or ""))


def split_query(query: str) -> tuple[str, str]:
    """(street, borough) as the user wrote them. Either may be empty."""
    parts = [p.strip() for p in (query or "").split(",")]
    street = re.sub(r"^\s*\d+(-\d+)?\s+", "", parts[0]) if parts else ""
    borough = ""
    for part in parts[1:] + parts[:1]:
        code = normalize_borough(part)
        if code in ("MN", "BX", "BK", "QN", "SI"):
            borough = code
            break
    return street, borough


def _street_name_tokens(street: str) -> set[str]:
    """
    The distinguishing words of a street name.

    Street types and compass directions are dropped before comparing, because
    they are shared by half the streets in the city: 'Nowhere Road' and
    'Rutland Road' overlap on ROAD, which would make a completely wrong match
    look like agreement. What must match is the name itself.
    """
    tokens = set()
    for token in normalize_street(street).split():
        if token in SUFFIX or token in SUFFIX.values():
            continue
        if token in DIRECTION or token in DIRECTION.values():
            continue
        tokens.add(token)
    return tokens


def _mismatch(query: str, props: dict) -> str | None:
    """
    Did GeoSearch answer a different question from the one we asked?

    It fuzzy-matches hard and never says so. "999 Nowhere Road, Manhattan"
    comes back as "999 RUTLAND ROAD, Brooklyn" — a different street in a
    different borough — with no error and the same confidence score it gives an
    exact hit. Someone screening a site they have not visited would have no way
    to notice. So we compare what we asked for against what came back.
    """
    want_street, want_boro = split_query(query)
    got_boro = normalize_borough(props.get("borough") or "")

    problems = []
    if want_street and props.get("street"):
        want = _street_name_tokens(want_street)
        got = _street_name_tokens(props["street"])
        if want and got and not (want & got):
            problems.append(
                f"you asked for '{want_street.strip()}' but it returned "
                f"'{props['street']}'")
    if want_boro and got_boro and want_boro != got_boro:
        problems.append(
            f"you asked for a {want_boro} address but it returned one in "
            f"{props.get('borough')}")

    if not problems:
        return None
    return (
        "NYC GeoSearch returned a different address from the one you typed: "
        + "; ".join(problems)
        + f". It resolved to {props.get('label')}. Everything below describes "
          f"THAT location. Check the spelling before trusting any of it.")


def geocode(address: str, timeout: float = 15.0) -> dict:
    """
    Resolve an address. Raises GeocodeError if NYC does not recognise it.

    `warning` is set when the query named no building and GeoSearch supplied
    one itself: ask for "Flushing Main Street, Queens" and it returns 8455 Main
    Street in Briarwood, eight kilometres away, reporting confidence 0.8 —
    exactly the confidence it reports for an exact match. Its own score cannot
    distinguish the two, so we check whether we supplied a house number and it
    returned one.
    """
    url = f"{GEOSEARCH}?{urllib.parse.urlencode({'text': address, 'size': 1})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:  # network, DNS, timeout
        raise GeocodeError(f"Could not reach GeoSearch: {exc}") from exc

    features = body.get("features") or []
    if not features:
        raise GeocodeError(
            f'NYC GeoSearch does not recognise "{address}". '
            f'Include the borough, e.g. "195 Bowery, Manhattan".')

    f = features[0]
    lon, lat = f["geometry"]["coordinates"]
    props = f.get("properties", {})
    pad = (props.get("addendum") or {}).get("pad", {})

    warning = _mismatch(address, props)
    if warning is None and not has_house_number(address) and props.get("housenumber"):
        warning = (
            f"You did not give a building number, so GeoSearch chose "
            f"{props.get('label')} by itself. Everything below describes that "
            f"spot. If it is not the block you meant, add a street number.")

    return {
        "label": props.get("label", address),
        "lat": lat, "lon": lon,
        "bbl": pad.get("bbl"), "bin": pad.get("bin"),
        "borough": props.get("borough"),
        "housenumber": props.get("housenumber"),
        "street": props.get("street"),
        "warning": warning,
    }
