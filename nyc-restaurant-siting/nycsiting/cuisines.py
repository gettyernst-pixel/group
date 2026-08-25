"""
Cuisine vocabulary.

Three files, three vocabularies. DOHMH uses 88 informative labels; the 2021
snapshot collapses them into 31 of its own invention ('Indian Subcontinent',
'Barbecue & Steakhouse'). We standardise on the DOHMH labels because both large
inspection files speak them, and map the 2021 labels onto that where a mapping
is unambiguous.
"""
from __future__ import annotations

#: Labels carrying no information about the concept.
UNINFORMATIVE = {"Not Listed/Not Applicable", "Other", "", "nan", None}

#: 2021-snapshot label -> DOHMH label. Only where the meaning is unambiguous;
#: 'European' and 'Other' deliberately have no target, because collapsing them
#: back would invent a precision the 2021 file destroyed.
SNAPSHOT_2021_TO_DOHMH = {
    "African": "African", "American": "American", "Australian": "Australian",
    "Bakery": "Bakery Products/Desserts", "Caribbean": "Caribbean",
    "Chicken": "Chicken", "Chinese": "Chinese", "Coffee/Tea": "Coffee/Tea",
    "French": "French", "Frozen Desserts": "Frozen Desserts",
    "Irish": "Irish", "Italian": "Italian", "Japanese": "Japanese",
    "Jewish/Kosher": "Jewish/Kosher", "Korean": "Korean",
    "Latin American": "Latin American", "Mediterranean": "Mediterranean",
    "Mexican": "Mexican", "Middle Eastern": "Middle Eastern", "Pizza": "Pizza",
    "Seafood": "Seafood", "Southeast Asian": "Southeast Asian",
    "Spanish": "Spanish", "Tex-Mex": "Tex-Mex", "Thai": "Thai",
}

#: Concepts that compete for the same diner on the same evening.
#: An editorial judgement, not something the data states — but counting only
#: exact-label matches badly understates the competition a new Italian place
#: faces from the pizzeria next door. Declared one-way, applied both ways.
COMPETES_WITH: dict[str, list[str]] = {
    "Italian": ["Pizza", "Mediterranean"],
    "Pizza": ["Italian"],
    "Japanese": ["Chinese/Japanese", "Asian/Asian Fusion", "Korean"],
    "Chinese": ["Chinese/Cuban", "Chinese/Japanese", "Asian/Asian Fusion"],
    "Korean": ["Japanese", "Asian/Asian Fusion"],
    "Thai": ["Southeast Asian", "Asian/Asian Fusion"],
    "Southeast Asian": ["Thai", "Asian/Asian Fusion"],
    "Mexican": ["Tex-Mex", "Latin American", "Southwestern"],
    "Tex-Mex": ["Mexican", "Latin American"],
    "Latin American": ["Mexican", "Caribbean", "Peruvian", "Brazilian"],
    "Mediterranean": ["Greek", "Middle Eastern", "Turkish", "Lebanese", "Italian"],
    "Middle Eastern": ["Mediterranean", "Turkish", "Lebanese", "Egyptian", "Moroccan"],
    "Greek": ["Mediterranean", "Turkish"],
    "Indian": ["Pakistani", "Bangladeshi", "Afghan"],
    "Pakistani": ["Indian", "Bangladeshi"],
    "American": ["New American", "Hamburgers", "Barbecue", "Steakhouse"],
    "New American": ["American", "Continental", "Haute Cuisine"],
    "French": ["New French", "Continental", "Haute Cuisine"],
    "Coffee/Tea": ["Bakery Products/Desserts", "Donuts", "Sandwiches"],
    "Sandwiches": ["Soups/Salads/Sandwiches", "Sandwiches/Salads/Mixed Buffet",
                   "Salads", "Coffee/Tea"],
    "Vegan": ["Vegetarian", "Salads"],
    "Vegetarian": ["Vegan", "Salads"],
    "Caribbean": ["Latin American", "Soul Food"],
    "Seafood": ["American", "Steakhouse"],
    "Steakhouse": ["American", "Barbecue"],
}

#: Everyday words a user types that are not DOHMH labels.
ALIASES = {
    "sushi": "Japanese", "ramen": "Japanese", "izakaya": "Japanese",
    "trattoria": "Italian", "pasta": "Italian", "osteria": "Italian",
    "taqueria": "Mexican", "tacos": "Mexican", "bbq": "Barbecue",
    "burgers": "Hamburgers", "burger": "Hamburgers", "cafe": "Coffee/Tea",
    "coffee": "Coffee/Tea", "coffee shop": "Coffee/Tea",
    "bakery": "Bakery Products/Desserts", "deli": "Sandwiches",
    "sandwich": "Sandwiches", "steak": "Steakhouse", "dim sum": "Chinese",
    "pho": "Southeast Asian", "vietnamese": "Southeast Asian",
    "falafel": "Middle Eastern", "shawarma": "Middle Eastern",
    "halal": "Middle Eastern", "kosher": "Jewish/Kosher",
    "ice cream": "Frozen Desserts", "gelato": "Frozen Desserts",
    "pub": "Irish", "gastropub": "American", "poke": "Hawaiian",
    "fried chicken": "Chicken",
}


def clean_label(value: object) -> str:
    """DOHMH label, or empty string if it carries no concept information."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    return "" if v in UNINFORMATIVE else v


def competitive_set(cuisine: str) -> set[str]:
    """The cuisine plus its near-substitutes, in both directions."""
    out = {cuisine}
    out.update(COMPETES_WITH.get(cuisine, []))
    for key, values in COMPETES_WITH.items():
        if cuisine in values:
            out.add(key)
    return out


def resolve(user_input: str, known: set[str]) -> str | None:
    """Map whatever the user typed onto a label present in the data."""
    if not user_input:
        return None
    q = user_input.strip()
    for label in known:
        if label.lower() == q.lower():
            return label
    alias = ALIASES.get(q.lower())
    if alias and alias in known:
        return alias
    prefix = [c for c in known if c.lower().startswith(q.lower())]
    if len(prefix) == 1:
        return prefix[0]
    return None
