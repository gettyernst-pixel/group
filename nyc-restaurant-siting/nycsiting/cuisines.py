"""
Cuisine vocabulary.

Three files, three vocabularies — and the two DOHMH files do NOT speak the
same one. DOHMH revised its labels between the 2017 archive and the 2026
extract (11 labels exist only in the archive, 17 only in the extract); the
2021 snapshot collapses everything into 31 labels of its own invention
('Indian Subcontinent', 'Barbecue & Steakhouse'). We standardise on the 2026
vocabulary: archive labels are mapped forward where the rename is verified,
and 2021 labels are mapped only where the meaning is unambiguous.
"""
from __future__ import annotations

#: Labels carrying no information about the concept.
UNINFORMATIVE = {"Not Listed/Not Applicable", "Other", "", "nan", None}

#: 2017-archive label -> 2026-extract label.
#:
#: Why this map must exist: the panel keeps each restaurant's most recent
#: label, so a survivor gets relabelled by the 2026 extract while a closure
#: keeps its archive label. Left unmapped, a renamed category splits into a
#: legacy label carrying only closures (0% cohort survival) and a successor
#: carrying every survivor (100%) — a taxonomy artifact that poisons every
#: persistence comparison for the category.
#:
#: Each pair was verified by matching CAMIS across the two files: of the
#: establishments carrying the old label in 2017 and still present in 2026,
#: the fraction carrying the new label is in the comment. 'Delicatessen' and
#: 'Pizza/Italian' were retired into labels that already existed; the same
#: cohort argument applies. 'Soups & Sandwiches' rests on the name
#: correspondence — only 6 CAMIS survive to vote, 2 of them for this target.
DOHMH_2017_TO_2026 = {
    "Asian": "Asian/Asian Fusion",                            # 61/87
    "Bakery": "Bakery Products/Desserts",                     # 297/327
    "Bottled beverages, including water, sodas, juices, etc.":
        "Bottled Beverages",                                  # 27/48
    "Café/Coffee/Tea": "Coffee/Tea",                          # 453/537
    "CafÃ©/Coffee/Tea": "Coffee/Tea",                         # same label, mojibake in the archive
    "Delicatessen": "Sandwiches",                             # 63/82
    "Ice Cream, Gelato, Yogurt, Ices": "Frozen Desserts",     # 75/77
    "Latin (Cuban, Dominican, Puerto Rican, South & Central American)":
        "Latin American",                                     # 315/349
    "Pizza/Italian": "Pizza",                                 # 178/189
    "Soups & Sandwiches": "Soups/Salads/Sandwiches",          # 2/6, by name
    "Steak": "Steakhouse",                                    # 43/48
    "Vietnamese/Cambodian/Malaysia": "Southeast Asian",       # 26/26
}

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
    """2026-vocabulary DOHMH label, or empty string if it carries no concept
    information. Archive-only labels are mapped forward so that renamed
    categories keep one identity across both extracts."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    if v in UNINFORMATIVE:
        return ""
    return DOHMH_2017_TO_2026.get(v, v)


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
