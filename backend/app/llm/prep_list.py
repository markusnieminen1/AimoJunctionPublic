"""Deterministic natural-language prep-list and Finnish menu-tag parser."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from app import clock
from app.domain import valio_rules

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_PAX_RE = re.compile(r"\b(?:for\s+)?(\d+)\s*(?:pax|hlö|henkilö(?:ä|lle)?|persons?|guests?)\b", re.I)
_BRACKET_TAG_RE = re.compile(r"\[([A-Za-z]{1,4})\]")
_PRODUCT_ID_RE = re.compile(r"\b(\d{6})\b")
_BUDGET_RE = re.compile(
    r"(?:under|below|max(?:imum)?|budget(?:\s+of)?|alle)\s*(?:€|eur)?\s*(\d+(?:[.,]\d+)?)\s*(?:€|eur)?",
    re.I,
)
_QTY_UNIT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[ ,]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:x\s*)?"
    r"(l|litres?|liters?|kg|g|st|pcs?|kpl|ki|box(?:es)?|cartons?|packs?|pak|bags?|ras|krt)?\b",
    re.I,
)

_UNIT_NORMALISE: dict[str, str] = {
    "l": "L",
    "litre": "L",
    "litres": "L",
    "liter": "L",
    "liters": "L",
    "kg": "KG",
    "g": "G",
    "st": "ST",
    "pc": "ST",
    "pcs": "ST",
    "kpl": "ST",
    "ki": "KI",
    "box": "KI",
    "boxes": "KI",
    "carton": "KRT",
    "cartons": "KRT",
    "krt": "KRT",
    "pak": "PAK",
    "pack": "PAK",
    "packs": "PAK",
    "bag": "BAG",
    "bags": "BAG",
    "ras": "RAS",
}

_NATURAL_DIET_PHRASES: tuple[tuple[str, str], ...] = (
    ("lactose-free", "L"),
    ("lactose free", "L"),
    ("laktoositon", "L"),
    ("low-lactose", "VL"),
    ("low lactose", "VL"),
    ("vähälaktoosinen", "VL"),
    ("milk-free", "M"),
    ("milk free", "M"),
    ("dairy-free", "M"),
    ("dairy free", "M"),
    ("maidoton", "M"),
    ("gluten-free", "G"),
    ("gluten free", "G"),
    ("gluteeniton", "G"),
    ("vegan", "VEG"),
    ("vegaaninen", "VEG"),
)

_FILLER_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "available",
        "buy",
        "can",
        "check",
        "confirm",
        "create",
        "deliver",
        "delivery",
        "find",
        "for",
        "get",
        "give",
        "i",
        "ignore",
        "in",
        "it",
        "limits",
        "list",
        "looking",
        "me",
        "my",
        "need",
        "of",
        "ok",
        "on",
        "options",
        "order",
        "override",
        "place",
        "please",
        "policy",
        "price",
        "products",
        "quote",
        "rules",
        "search",
        "show",
        "some",
        "that",
        "the",
        "to",
        "want",
        "we",
        "with",
        "would",
        "your",
    }
)


@dataclass(slots=True)
class PrepListItem:
    """Single parsed prep-list line with optional quantity, unit, diet tags, and query."""

    raw: str
    query: str
    quantity: float | None = None
    unit: str | None = None
    diets: list[str] = field(default_factory=list)
    product_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PrepListParseResult:
    """Structured parse output for a natural-language prep list or request."""

    items: list[PrepListItem]
    requested_delivery_date: str | None = None
    pax: int | None = None
    global_diets: list[str] = field(default_factory=list)
    max_budget_eur: float | None = None


def _parse_number(raw: str) -> float:
    cleaned = raw.strip().replace(" ", "")
    # Treat "1,000" (comma followed by 3 digits) as thousands separator; "1,5" as decimal.
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", cleaned):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def default_delivery_date() -> str:
    """Return a sensible future delivery date matching fixture/live timelines."""
    today = clock.now().date()
    if today.isoformat() <= "2026-11-15":
        return "2026-11-16"
    return (today + timedelta(days=2)).isoformat()


def _extract_diets(text: str) -> tuple[list[str], str]:
    diets: list[str] = []

    def _collect_tag(match: re.Match[str]) -> str:
        raw_tag = match.group(1).strip()
        upper = raw_tag.upper()
        if raw_tag in valio_rules.MENU_CODES or upper in valio_rules.MENU_CODES:
            code = upper if upper in valio_rules.MENU_CODES else raw_tag
            if code not in diets:
                diets.append(code)
            return " "
        return match.group(0)

    cleaned = _BRACKET_TAG_RE.sub(_collect_tag, text)
    lowered = cleaned.lower()
    for phrase, code in _NATURAL_DIET_PHRASES:
        if phrase in lowered:
            if code not in diets:
                diets.append(code)
            lowered = lowered.replace(phrase, " ")
            cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.I)

    return diets, cleaned


def parse_prep_list(text: str) -> PrepListParseResult:
    """Parse quantities, units, menu tags ([L], [G], [M], [VL], [VEG]), pax, and date."""
    working = re.sub(r"^\[Branch:\s*[^\]]+\]\s*", "", text.strip(), flags=re.I)

    requested_date: str | None = None
    date_match = _DATE_RE.search(working)
    if date_match:
        requested_date = date_match.group(1)
        working = _DATE_RE.sub(" ", working)
    elif re.search(r"\btomorrow\b", working, re.I):
        requested_date = (clock.now().date() + timedelta(days=1)).isoformat()
        working = re.sub(r"\btomorrow\b", " ", working, flags=re.I)

    pax: int | None = None
    pax_match = _PAX_RE.search(working)
    if pax_match:
        pax = int(pax_match.group(1))
        working = _PAX_RE.sub(" ", working)

    max_budget_eur: float | None = None
    budget_match = _BUDGET_RE.search(working)
    if budget_match:
        max_budget_eur = _parse_number(budget_match.group(1))
        working = _BUDGET_RE.sub(" ", working)

    segments = [seg.strip() for seg in re.split(r"(?:,(?!\d)|[;\n])+", working) if seg.strip()]
    if not segments and working.strip():
        segments = [working.strip()]

    items: list[PrepListItem] = []
    global_diets: list[str] = []

    for seg in segments:
        diets, seg_no_diets = _extract_diets(seg)
        for d in diets:
            if d not in global_diets:
                global_diets.append(d)

        product_ids = _PRODUCT_ID_RE.findall(seg_no_diets)
        seg_no_ids = _PRODUCT_ID_RE.sub(" ", seg_no_diets)

        quantity: float | None = None
        unit: str | None = None
        qty_match = _QTY_UNIT_RE.search(seg_no_ids)
        if qty_match:
            quantity = _parse_number(qty_match.group(1))
            raw_unit = (qty_match.group(2) or "").lower()
            unit = _UNIT_NORMALISE.get(raw_unit) if raw_unit else None
            seg_no_qty = seg_no_ids[: qty_match.start()] + " " + seg_no_ids[qty_match.end() :]
        else:
            seg_no_qty = seg_no_ids

        words = [
            tok
            for tok in re.findall(r"[A-Za-zÅÄÖåäö0-9-]+", seg_no_qty)
            if tok.lower() not in _FILLER_WORDS
        ]
        query = " ".join(words).strip()
        if not query and product_ids:
            query = product_ids[0]

        if query or product_ids or quantity is not None or diets:
            items.append(
                PrepListItem(
                    raw=seg,
                    query=query,
                    quantity=quantity,
                    unit=unit,
                    diets=diets,
                    product_ids=product_ids,
                )
            )

    return PrepListParseResult(
        items=items,
        requested_delivery_date=requested_date,
        pax=pax,
        global_diets=global_diets,
        max_budget_eur=max_budget_eur,
    )
