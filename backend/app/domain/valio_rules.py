"""valio_rules.py - code lookups + diet rule engine for the Valio Aimo Junction 2026 data.

status: "verified" = organizer docs, GS1 code list, law, or mechanically proven in data;
        "inferred" = deduced from data patterns (not organizer-confirmed).
Lookups are namespaced per field: "ST" is a unit (piece) AND an allergen (pistachio).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------- API ----
API = {
    "price_flags": {  # (netPriceFound, error)
        (False, False): "CATALOG_PRICE",  # validTo 9999-12-31
        (True, False): "NET_PRICE",  # customer net price, validTo 2026-12-31
        (False, True): "NO_PRICE",  # unknown id / deposit code; price objects omitted
    },
    "tax_rates": {25.5: "GENERAL", 13.5: "REDUCED_FOOD", 10.0: "REDUCED_PRESS"},  # verified (Vero)
    "comparison_units": {"KG": "per kg", "L": "per litre", None: "no comparison price"},
    "logistic_models": {
        "Stock": "own stock, real qty",
        "Terminal": "supplier-delivered, qty unknown",
        None: "no schedule lines -> not deliverable / unknown id",
    },
    "http": {
        200: "ok",
        307: "download redirect",
        401: "invalid or missing API key",
        404: "no such path",
        405: "wrong method (allow: POST)",
        422: "validation error",
        429: "rate limit (never observed)",
    },
    "http422_types": {
        "missing": "required field omitted",
        "string_type": "non-string customer/product id",
        "date_from_datetime_parsing": "requestedDate not YYYY-MM-DD or invalid day",
        "too_short": "products == []",
        "too_long": "more than 1000 products",
    },
    "max_ids_per_call": 1000,
}
TERMINAL_UNKNOWN_QTY = 999999
UNLIMITED_BASE_UNITS = 9_999_999_999


def availability_state(line: dict, size_in_base_units: float | None) -> str:
    """line = scheduleLines[i]; size = units[unitId == line['salesUnit']].sizeInBaseUnits."""
    if line.get("salesBlock"):
        return "BLOCKED"
    amt = line.get("availableAmount", 0) or 0
    if amt == 0:
        return "UNAVAILABLE"
    if amt == TERMINAL_UNKNOWN_QTY:
        return "UNKNOWN_QTY"
    if amt * (size_in_base_units or 1) >= 0.99 * UNLIMITED_BASE_UNITS:
        return "UNLIMITED"
    return "FINITE"


def _ts(x: str) -> datetime:
    return datetime.strptime(x, "%Y-%m-%dT%H:%M:%S%z")  # API format: 2026-11-15T17:00:00+0200


def date_feasible(product_avail: dict, time_now: str, requested_date: str) -> bool:
    """product_avail = availability products[i]; time_now = response timeNow (string).

    Absent logisticModel, no lines, salesBlock, or a past closingTime -> cannot make the date.
    """
    lines = product_avail.get("scheduleLines") or []
    if not product_avail.get("logisticModel") or not lines or product_avail.get("salesBlock"):
        return False
    ct = product_avail.get("closingTime")
    if ct is None or _ts(time_now) >= _ts(ct):
        return False
    return any(
        line.get("availableAmount")
        and line.get("availableDate")
        and line["availableDate"] <= requested_date
        and not line.get("salesBlock")
        for line in lines
    )


# ------------------------------------------------------------ units -------
UNIT = {  # code: (en, fi, unece, orderable, status)
    "ST": ("piece", "kpl", "H87", True, "verified"),
    "KG": ("kilogram", "kg", "KGM", True, "verified"),
    "KI": ("case/crate (SAP Kiste)", "laatikko", "CS", True, "verified-ish"),
    "PAK": ("pack", "paketti", "PK", True, "verified-ish"),
    "BAG": ("bag", "pussi", "BG", True, "inferred"),
    "RAS": ("box/tray/punnet", "rasia", "BX", True, "inferred"),
    "PRK": ("jar/tub", "purkki", "JR", True, "inferred"),
    "BOT": ("bottle", "pullo", "BO", True, "inferred"),
    "SK": ("sack", "säkki", "SA", True, "inferred"),
    "CS": ("case", "laatikko", "CS", True, "inferred"),
    "KLE": ("returnable beverage crate", "kori/kenno", "CR", True, "inferred"),
    "KRT": ("carton", "kartonki", "CT", True, "inferred"),
    "KAN": ("canister", "kanisteri", "CI", True, "inferred"),
    "PAL": ("pallet", "lava", "PX", True, "inferred"),
    "NIP": ("bundle", "nippu", "BE", True, "inferred"),
    "DSP": ("display unit", "display/teholava", None, True, "inferred"),
    "DZ": ("dozen", "tusina", "DZN", True, "inferred"),
    "KRS": ("pallet layer", "kerros", None, False, "inferred"),
    "ZML": ("dairy reporting unit ~1 l milk eq.", "?", None, False, "inferred_low"),
    "DOL": ("dolly ~1/4 pallet", "dolly", None, False, "inferred"),
    "KOR": ("dairy crate of 20 cartons", "kori", "CR", False, "inferred"),
    "PNT": ("bunch", "puntti", "BH", False, "inferred"),
    "MDO": ("mini-dolly 1/2 DOL", "minidolly", None, False, "inferred"),
}
SYNKKA_UNIT = {
    "GRM": "g",
    "MGM": "mg",
    "MC": "µg",
    "KGM": "kg",
    "KJO": "kJ",
    "E14": "kcal",
    "MLT": "ml",
    "LTR": "l",
    "CLT": "cl",
    "MMT": "mm",
    "CMT": "cm",
    "MTR": "m",
    "H87": "piece",
    "PTN": "portion",
    "PR": "pair",
    "GL": "g/l (misused as basis)",
}
TO_GRAMS = {"GRM": 1.0, "MGM": 1e-3, "MC": 1e-6, "KGM": 1000.0}

# temperatureCondition -> (band, routing class); inferred from Synkka min/max medians
TEMPERATURE = {
    1: ("0..+3 C", "chilled_strict"),
    2: ("+2..+4 C", "chilled"),
    3: ("+2..+6 C", "chilled"),
    4: ("+4..+8 C", "chilled"),
    5: ("fresh produce, cool", "produce_cool"),
    6: ("+4..+20 C cool ambient", "cool_ambient"),
    7: ("-25..-18 C", "frozen"),
    8: ("+10..+22 C ambient", "ambient"),
    9: ("mixed", "unknown"),
    None: ("missing", "unknown"),
}
DEPOSIT_EUR = {  # inferred (Palpa tiers)
    "1": 0.10,
    "4": 0.15,
    "147": 0.10,
    "3": 0.20,
    "2": 0.40,
    "171": 2.20,
    "9708918": None,
}
VATCODE_ADVISORY = {"STANDARD": 25.5, "MEDIUM": 13.5, "MEDIUM MEDIUM": 13.5, "LOW": 10.0}
COUNTRY_ALPHA2_ODD = {
    "zy": "EU (unspecified)",
    "zz": "unknown/various",
    "xs": "RS (Serbia, legacy)",
    "sv": "SV (El Salvador)",
}
COUNTRY_NUMERIC_ODD = {"097": "EU (pseudo-code, not Finland)", "NON_EU": "outside EU"}

# -------------------------------------------------------- allergens -------
ALLERGEN = {  # code: (en, annex_ii_no or None, parent)
    "AW": ("cereals containing gluten", 1, None),
    "UW": ("wheat", 1, "AW"),
    "NR": ("rye", 1, "AW"),
    "GB": ("barley", 1, "AW"),
    "GO": ("oats", 1, "AW"),
    "GS": ("spelt", 1, "UW"),
    "GK": ("khorasan", 1, "UW"),
    "TR": ("triticale", 1, "AW"),
    "AC": ("crustaceans", 2, None),
    "AE": ("eggs", 3, None),
    "AF": ("fish", 4, None),
    "ABD": ("tuna", 4, "AF"),
    "ADQ": ("pollock", 4, "AF"),
    "AP": ("peanuts", 5, None),
    "AY": ("soy", 6, None),
    "AM": ("milk", 7, None),
    "ML": ("lactose", 7, "AM"),
    "GOM": ("goat milk", 7, "AM"),
    "AN": ("tree nuts", 8, None),
    "SA": ("almond", 8, "AN"),
    "SH": ("hazelnut", 8, "AN"),
    "SW": ("walnut", 8, "AN"),
    "SC": ("cashew", 8, "AN"),
    "SP": ("pecan", 8, "AN"),
    "SR": ("brazil nut", 8, "AN"),
    "ST": ("pistachio", 8, "AN"),
    "SM": ("macadamia", 8, "AN"),
    "BC": ("celery", 9, None),
    "BM": ("mustard", 10, None),
    "AS": ("sesame", 11, None),
    "AU": ("sulphites >10 mg/kg", 12, None),
    "NL": ("lupin", 13, None),
    "UM": ("molluscs", 14, None),
    "UN": ("shellfish (crustaceans+molluscs)", None, None),  # rolls up to BOTH AC and UM
    # voluntary / non-Annex II (positive evidence only)
    "NC": ("cocoa", None, None),
    "NE": ("peas", None, None),
    "NP": ("pod fruits/legumes", None, None),
    "NK": ("coriander", None, None),
    "NM": ("corn", None, None),
    "NW": ("carrot", None, None),
    "PN": ("pine nut", None, None),
    "SO": ("coconut", None, None),
    "SN": ("chestnut", None, None),
    "BF": ("beef", None, None),
    "PO": ("pork", None, None),
    "CM": ("chicken", None, None),
    "BP": ("linalool (non-food fragrance)", None, None),
    "X99": ("no declaration-obligatory allergens", None, None),
}
ROLLUP_EXTRA = {"UN": ["AC", "UM"]}
ANNEX_II_PARENTS = [
    "AW",
    "AC",
    "AE",
    "AF",
    "AP",
    "AY",
    "AM",
    "AN",
    "BC",
    "BM",
    "AS",
    "AU",
    "NL",
    "UM",
]

# state order: higher = more restrictive
STATE_RANK = {"ABSENT": 0, "ABSENT_WEAK": 1, "UNKNOWN": 2, "TRACE": 3, "PRESENT": 4}
LEVEL_TO_STATE = {
    "CONTAINS": "PRESENT",
    "50": "PRESENT",
    "MAY_CONTAIN": "TRACE",
    "UNDECLARED": "ABSENT_WEAK",
    "30": "ABSENT_WEAK",
    "FREE_FROM": "ABSENT",
    "60": "ABSENT",
}


def _ancestors(code: str) -> list[str]:
    out, c = [], code
    while ALLERGEN.get(c, (None, None, None))[2]:
        c = ALLERGEN[c][2]
        out.append(c)
    for extra in ROLLUP_EXTRA.get(code, []):
        out.append(extra)
    return out


def _groups(product: dict) -> dict[str, list[dict]]:
    classifications = (product.get("synkkaData") or {}).get("classifications") or []
    return {g.get("name"): g.get("values") or [] for g in classifications}


def allergen_states(product: dict) -> dict[str, str]:
    """-> {code: state} for every code in ALLERGEN. No statement = UNKNOWN. Most restrictive wins.

    Child PRESENT/TRACE raises its parents; a parent that ends ABSENT/ABSENT_WEAK lowers unstated
    children; a child FREE_FROM never lowers its parent.
    """
    g = _groups(product)
    raw = [(v["id"], v.get("unit")) for v in g.get("allergen", []) + g.get("nonAllergen", [])]
    real = [(c, u) for c, u in raw if c != "X99" and c in ALLERGEN]
    explicit: dict[str, str] = {}
    for code, level in real:
        s = LEVEL_TO_STATE.get(level)
        if s and (code not in explicit or STATE_RANK[s] > STATE_RANK[explicit[code]]):
            explicit[code] = s
    # X99 'no declaration-obligatory allergens' counts only when no real CONTAINS/MAY_CONTAIN exists
    if any(c == "X99" and u == "CONTAINS" for c, u in raw) and not any(
        LEVEL_TO_STATE.get(u) in ("PRESENT", "TRACE") for _, u in real
    ):
        for parent in ANNEX_II_PARENTS:
            explicit.setdefault(parent, "ABSENT_WEAK")
    states = {c: "UNKNOWN" for c in ALLERGEN}
    states.update(explicit)
    for code, s in explicit.items():  # upward roll-up
        if s in ("PRESENT", "TRACE"):
            for anc in _ancestors(code):
                states[anc] = max(states[anc], s, key=lambda st: STATE_RANK[st])
    for child, (_, _, parent) in ALLERGEN.items():  # downward: final ABSENT parent -> unstated
        parents = [parent] if parent else ROLLUP_EXTRA.get(child, [])
        if (
            child not in explicit
            and parents
            and all(states.get(x, "UNKNOWN").startswith("ABSENT") for x in parents)
        ):
            states[child] = max((states[x] for x in parents), key=lambda st: STATE_RANK[st])
    return states


# ------------------------------------------------------------ claims ------
def claims(product: dict) -> set[str]:
    return {v["id"] for v in _groups(product).get("nutritionalClaim", [])}


def labels(product: dict) -> set[str]:
    return {v["id"] for v in _groups(product).get("packagingMarkedLabel", [])}


def nutrients(product: dict) -> dict[str, tuple[float, str]]:
    """-> {id: (value per 100 g/ml, precision)}. Mass units -> grams.

    Rescales GRM/MLT/KGM bases to 100 (e.g. Benecol is declared per 10 g); skips PTN/H87/GL bases
    and known-bad rows (ENER-GRM, ENER-MGM, FAT in KJO).
    """
    out: dict[str, tuple[float, str]] = {}
    for r in (product.get("synkkaData") or {}).get("nutritionalContent") or []:
        nid, unit, val = r["id"], r.get("unit"), r.get("value")
        bq, bu = r.get("basisQuantity"), r.get("basisQuantityUnit")
        if val is None or not bq or bu not in ("GRM", "MLT", "KGM"):
            continue
        if nid in ("ENER-GRM", "ENER-MGM") or (nid == "FAT" and unit == "KJO"):
            continue
        factor = 100.0 / (bq * (1000.0 if bu == "KGM" else 1.0))
        if unit in TO_GRAMS and nid not in ("ENER-KJO", "ENER-E14"):
            val = val * TO_GRAMS[unit]  # everything mass-based -> grams
        out[nid] = (val * factor, r.get("unitPrecision") or "APPROXIMATELY")
    return out


def _le(n: dict[str, tuple[float, str]], nid: str, limit: float) -> bool | None:
    """True / False / None(unknown). LESS_THAN x counts as <= limit only if x <= limit."""
    if nid not in n:
        return None
    v, _ = n[nid]
    return v <= limit


def _high_fibre(n: dict[str, tuple[float, str]]) -> bool | None:
    if "FIBTG" not in n:
        return None
    if n["FIBTG"][0] >= 6:
        return True
    if "ENER-E14" in n and n["ENER-E14"][0] > 0:
        return n["FIBTG"][0] / n["ENER-E14"][0] * 100 >= 3
    return False


def _high_protein(n: dict[str, tuple[float, str]]) -> bool | None:
    if not ("PRO-" in n and "ENER-E14" in n and n["ENER-E14"][0] > 0):
        return None
    return n["PRO-"][0] * 4 / n["ENER-E14"][0] >= 0.20


CLAIM_CHECKS = {  # claim id -> function(nutrients) -> True/False/None ; None = cannot verify
    # FI limits are "<10 mg" and "<1 g"; rounded values pass at declared 0.01 / 1.0
    "FREE_FROM_LACTOSE": lambda n: _le(n, "LACS", 0.01),
    "LOW_LACTOSE": lambda n: _le(n, "LACS", 1.0),
    "LOW_FAT": lambda n: _le(n, "FAT", 3.0),  # liquids: 1.5 (1.8 semi-skimmed) - caller decides
    "FREE_FROM_FAT": lambda n: _le(n, "FAT", 0.5),
    "LOW_SODIUM_SALT": lambda n: _le(n, "SALTEQ", 0.3),
    "FREE_FROM_SODIUM_SALT": lambda n: _le(n, "SALTEQ", 0.0125),
    "HIGH_FIBRE": _high_fibre,
    "HIGH_PROTEIN": _high_protein,
}


def verified_claims(product: dict) -> dict[str, str]:
    """-> {claim: 'VERIFIED'|'UNVERIFIABLE'|'FAILS_THRESHOLD'}."""
    n, out = nutrients(product), {}
    for c in claims(product):
        chk = CLAIM_CHECKS.get(c)
        r = chk(n) if chk else None
        out[c] = "UNVERIFIABLE" if r is None else ("VERIFIED" if r else "FAILS_THRESHOLD")
    return out


# EU Reg. 1169/2011 Annex II: group -> (fi, sv, GS1 parent, exemptions).
ANNEX_II = {
    1: (
        "gluteenia sisältävät viljat",
        "spannmål som innehåller gluten",
        "AW",
        [
            "wheat-based glucose syrups incl. dextrose",
            "wheat-based maltodextrins",
            "barley glucose syrups",
            "cereals for distillates / agricultural ethyl alcohol",
        ],
    ),
    2: ("äyriäiset", "kräftdjur", "AC", []),
    3: ("munat", "ägg", "AE", []),
    4: (
        "kalat",
        "fisk",
        "AF",
        [
            "fish gelatine as vitamin/carotenoid carrier",
            "fish gelatine or isinglass as beer/wine fining",
        ],
    ),
    5: ("maapähkinät", "jordnötter", "AP", []),
    6: (
        "soijapavut",
        "sojabönor",
        "AY",
        [
            "fully refined soybean oil and fat",
            "natural mixed tocopherols E306 and natural D-alpha tocopherol from soy",
            "soy phytosterols and phytosterol esters",
            "soy plant stanol ester",
        ],
    ),
    7: (
        "maito (laktoosi mukaan lukien)",
        "mjölk (inbegripet laktos)",
        "AM",
        ["whey for distillates", "lactitol E966"],
    ),
    8: ("pähkinät", "nötter", "AN", ["nuts for distillates"]),
    9: ("selleri", "selleri", "BC", []),
    10: ("sinappi", "senap", "BM", []),
    11: ("seesaminsiemenet", "sesamfrön", "AS", []),
    12: (
        "rikkidioksidi ja sulfiitit",
        "svaveldioxid och sulfiter",
        "AU",
        ["only > 10 mg/kg or 10 mg/l total SO2 as consumed"],
    ),
    13: ("lupiinit", "lupin", "NL", []),
    14: ("nilviäiset", "blötdjur", "UM", []),
}
NOT_ANNEX_II = [
    "coconut SO",
    "pine nut PN",
    "chestnut SN",
    "nutmeg",
    "buckwheat BWD",
    "peas NE",
    "pulses SX",
    "insects",
]

# Finnish menu letters -> diet ids. Per-kitchen override required: letters are convention, not law.
MENU_CODES = {
    "L": "LACTOSE_FREE",
    "VL": "LOW_LACTOSE",
    "M": "MILK_FREE",
    "MA": "MILK_FREE",
    "G": "GLUTEN_FREE_COELIAC",
    "GL": "GLUTEN_FREE_COELIAC",
    "VEG": "VEGAN",
    "Veg": "VEGAN",
    "VE": "VEGAN",
    "S": "PORK_FREE",
    "N": "BEEF_FREE",
    "K": "VEGETARIAN_LACTO_OVO",
    "MU": "EGG_FREE",
    "KM": "EGG_FREE",
    "SO": "TEXTURE_MINCED_PUREE",
    "SS": "TEXTURE_SMOOTH_PUREE",
}

# Sydänmerkki product criteria per 100 g ready-to-eat (ammattilaiset.sydanmerkki.fi).
SYDANMERKKI = {
    "ready_meal": {
        "FAT": 3.0,
        "fat_alt": (8.0, ("FASAT_pct", 30)),
        "SUGAR-": 7.0,
        "SALTEQ": 0.75,
        "cats": ["17300", "41755"],
    },
    "soup": {
        "FAT": 3.0,
        "fat_alt": (6.0, ("FASAT_g", 1.5)),
        "SALTEQ": 0.70,
        "cats": ["21900"],
    },
    "sauce": {
        "FAT": 4.0,
        "fat_alt": (8.0, ("FASAT_pct", 30)),
        "SALTEQ": 0.75,
        "cats": ["21904", "21920"],
    },
    "meat_dish": {"FAT": 12.0, "FASAT_pct": 38, "SALTEQ": 1.00, "cats": ["41705"]},
    "fish_dish": {"FAT": 18.0, "FASAT_pct": 25, "SALTEQ": 1.00, "cats": ["41723"]},
    "plant_protein_dish": {
        "FAT": 3.0,
        "fat_alt": (15.0, ("FASAT_pct", 25)),
        "SUGAR-": 8.0,
        "SALTEQ": 1.00,
        "cats": ["17306", "41753", "41754"],
    },
    "veg_side": {
        "FAT": 1.5,
        "fat_alt": (3.0, ("FASAT_pct", 20)),
        "SALTEQ": 0.50,
        "cats": ["41732"],
    },
    "unseasoned_meat": {
        "FAT": 7.0,
        "fat_alt": (10.0, ("FASAT_pct", 30)),
        "SALTEQ": "no_added_salt",
        "cats": ["18710", "18720", "18750", "18760", "41701", "41702", "41710"],
    },
    "cooked_meat": {"FAT": 9.0, "SALTEQ": 1.00, "cats": ["17209", "17210"]},
    "cold_cuts": {
        "FAT": 4.0,
        "fat_alt": (12.0, ("FASAT_pct", 20)),
        "SALTEQ": 1.90,
        "cats": ["17203", "17204", "17205"],
    },
    "sausage": {"FAT": 12.0, "FASAT_pct": 38, "SALTEQ": 1.50, "cats": ["17200", "17201"]},
    "plant_sausage": {"FAT": 15.0, "FASAT_pct": 20, "SALTEQ": 1.50, "cats": []},
    "ripened_cheese": {
        "FAT": 17.0,
        "fat_alt": (30.0, ("FASAT_pct", 30)),
        "SALTEQ": 1.20,
        "cats": ["10600", "10602", "10603"],
    },
    "cottage_cheese": {"FAT": 2.0, "SALTEQ": 0.75, "cats": []},
    "plain_milk": {
        "FAT": 0.5,
        "FASAT_g": 0.4,
        "SUGAR-": 5.0,
        "SALTEQ": 0.13,
        "cats": ["10400"],
    },
    "plant_drink": {
        "FAT": 3.0,
        "FASAT_g": 0.4,
        "SUGAR-": 5.0,
        "SALTEQ": 0.10,
        "cats": ["10402"],
    },
    "yoghurt_rahka_viili": {
        "FAT": 1.0,
        "fat_alt": (5.0, ("FASAT_g", 1.0)),
        "SUGAR-": 10.0,
        "SALTEQ": 0.25,
        "cats": ["10411", "10412", "10413", "10430"],
    },
    "cooking_cream": {
        "FAT": 7.0,
        "fat_alt": (15.0, ("FASAT_pct", 30)),
        "SALTEQ": 0.75,
        "cats": ["10401", "10406"],
    },
    "bread": {
        "FAT": 5.0,
        "SUGAR-": 7.0,
        "FIBTG_min": 6.0,
        "SALTEQ": 0.90,
        "cats": ["10900", "10901", "10902", "41760", "41761"],
    },
    "crispbread": {"FIBTG_min": 10.0, "SALTEQ": 1.20, "cats": ["21211", "21200"]},
    "porridge": {"SALTEQ": 0.30, "cats": []},
    "potato_product": {
        "FAT": 5.0,
        "FASAT_g": 1.0,
        "SALTEQ": 0.60,
        "cats": ["17423", "41751"],
    },
    "spread": {"FASAT_pct": 30, "SALTEQ": 1.00, "cats": ["10515", "10516"]},
    "oil": {"FASAT_pct": 20, "cats": ["10530"]},
    "stock_prepared": {"SALTEQ": 0.50, "cats": ["21902", "21903"]},
}


def sydanmerkki_check(product: dict, group: str) -> bool | None:
    """-> True/False/None. Values are as sold; caller must dilute stocks/porridge flakes first."""
    n, lim = nutrients(product), SYDANMERKKI[group]

    def v(k: str) -> float | None:
        return n[k][0] if k in n else None

    fat, sat = v("FAT"), v("FASAT")
    if "FAT" in lim:
        if fat is None:
            return None
        if fat > lim["FAT"]:
            alt = lim.get("fat_alt")
            if not alt or fat > alt[0] or sat is None:
                return False if not alt or fat > alt[0] else None
            kind, x = alt[1]
            if (kind == "FASAT_pct" and sat / fat * 100 > x) or (kind == "FASAT_g" and sat > x):
                return False
    if "FASAT_pct" in lim and fat and sat is not None and sat / fat * 100 > lim["FASAT_pct"]:
        return False
    if "FASAT_g" in lim and sat is not None and sat > lim["FASAT_g"]:
        return False
    for k in ("SUGAR-", "SALTEQ"):
        if isinstance(lim.get(k), float):
            if v(k) is None:
                return None
            if v(k) > lim[k]:
                return False
    if "FIBTG_min" in lim:
        if v("FIBTG") is None:
            return None
        if v("FIBTG") < lim["FIBTG_min"]:
            return False
    return True


def load_category_mapping(path: str | Path | None = None) -> dict[str, dict]:
    """category_mapping.json: {code: {name_en, name_fi, parent_group, food, confidence}}."""
    target = Path(path) if path is not None else Path(__file__).with_name("category_mapping.json")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------- diets -------
STRICT = ("PRESENT", "TRACE", "UNKNOWN")
DIETS = {
    "MILK_FREE": {"tier": "medical", "block": [("AM", STRICT)], "menu": ["M"]},
    "EGG_FREE": {"tier": "medical", "block": [("AE", STRICT)], "menu": ["MU", "KM"]},
    "FISH_FREE": {"tier": "medical", "block": [("AF", STRICT)]},
    "SHELLFISH_FREE": {"tier": "medical", "block": [("AC", STRICT), ("UM", STRICT)]},
    "PEANUT_FREE": {
        "tier": "medical",
        "block": [("AP", STRICT)],
        "trace_never_allowed": True,
    },
    "TREE_NUT_FREE": {
        "tier": "medical",
        "block": [("AN", STRICT)],
        "trace_never_allowed": True,
    },
    "SOY_FREE": {"tier": "medical", "block": [("AY", STRICT)]},
    "SESAME_FREE": {"tier": "medical", "block": [("AS", STRICT)]},
    "CELERY_FREE": {"tier": "medical", "block": [("BC", STRICT)]},
    "MUSTARD_FREE": {"tier": "medical", "block": [("BM", STRICT)]},
    "LUPIN_FREE": {"tier": "medical", "block": [("NL", STRICT)]},
    "SULPHITE_FREE": {"tier": "medical", "block": [("AU", STRICT)]},
    "WHEAT_ALLERGY": {
        "tier": "medical",
        "block": [("UW", STRICT)],
        "note": "FREE_FROM_GLUTEN is NOT sufficient",
    },
    "RYE_ALLERGY": {"tier": "medical", "block": [("NR", STRICT)]},
    "BARLEY_ALLERGY": {"tier": "medical", "block": [("GB", STRICT)]},
    "OAT_ALLERGY": {"tier": "medical", "block": [("GO", STRICT)]},
    "GLUTEN_FREE_COELIAC": {"tier": "medical", "menu": ["G"], "special": "coeliac"},
    "LACTOSE_FREE": {"tier": "intolerance", "menu": ["L"], "special": "lactose_free"},
    "LOW_LACTOSE": {"tier": "intolerance", "menu": ["VL"], "special": "low_lactose"},
    "VEGAN": {"tier": "ethical", "menu": ["VEG", "Veg", "VE"], "special": "vegan"},
    "VEGETARIAN_LACTO_OVO": {
        "tier": "ethical",
        "menu": ["K"],
        "special": "vegetarian",
    },
    "PORK_FREE": {"tier": "religious", "menu": ["S"], "special": "pork_free"},
    "BEEF_FREE": {"tier": "religious", "menu": ["N"], "special": "beef_free"},
    "HALAL": {"tier": "religious", "special": "certification_required"},
    "KOSHER": {"tier": "religious", "special": "certification_required"},
    "HEART_LOW_SALT": {"tier": "guidance", "special": "sydanmerkki"},
    "ENERGY_PROTEIN_ENRICHED": {
        "tier": "guidance",
        "special": "not_worse_energy_protein",
    },
    "TEXTURE_SOFT": {
        "tier": "texture",
        "special": "curated_texture",
        "iddsi_approx": 6,
    },
    "TEXTURE_MINCED_PUREE": {
        "tier": "texture",
        "menu": ["SO"],
        "special": "curated_texture",
        "iddsi_approx": 5,
    },
    "TEXTURE_SMOOTH_PUREE": {
        "tier": "texture",
        "menu": ["SS"],
        "special": "curated_texture",
        "iddsi_approx": 4,
    },
    "TEXTURE_LIQUID": {
        "tier": "texture",
        "special": "curated_texture",
        "iddsi_approx": "0-3",
    },
}
ANIMAL_ALLERGENS = ["AM", "AE", "AF", "AC", "UM", "BF", "PO", "CM"]
MEAT_FISH_ALLERGENS = ["AF", "AC", "UM", "BF", "PO", "CM"]


def evaluate(
    product: dict,
    diet: str,
    profile: dict | None = None,
    text_states: dict[str, str] | None = None,
    category_food: bool = True,
) -> tuple[str, list[str]]:
    """-> (verdict, reasons). verdict in PASS | FAIL | REVIEW."""
    p = {
        "allow_trace": False,
        "allow_absent_weak": False,
        "allow_gf_oats": True,
        "allow_gf_wheat_starch": True,
    }
    p.update(profile or {})
    if not category_food:
        return "REVIEW", ["non-food category: diets not applicable"]
    st = allergen_states(product)
    for c, s in (text_states or {}).items():
        if s in ("PRESENT", "TRACE") and STATE_RANK[s] > STATE_RANK[st.get(c, "UNKNOWN")]:
            st[c] = s
            for anc in _ancestors(c):
                st[anc] = max(st[anc], s, key=lambda val: STATE_RANK[val])
    cl, n, lb = claims(product), nutrients(product), labels(product)
    rule, why = DIETS[diet], []
    if "block" in rule:
        verdict = "PASS"
        for code, _blocked in rule["block"]:
            s = st[code]
            if s == "PRESENT":
                return "FAIL", [f"{code}=PRESENT"]
            if s == "TRACE":
                if rule.get("trace_never_allowed") or not p["allow_trace"]:
                    return "FAIL", [f"{code}=MAY_CONTAIN"]
                why.append(f"{code}=MAY_CONTAIN allowed by profile")
            if s == "UNKNOWN":
                verdict = "REVIEW"
                why.append(f"{code}=UNKNOWN (no statement)")
            if s == "ABSENT_WEAK" and not p["allow_absent_weak"]:
                verdict = "REVIEW"
                why.append(f"{code}=UNDECLARED/X99 only")
            if s == "ABSENT":
                why.append(f"{code}=FREE_FROM")
        return verdict, why
    sp = rule.get("special")
    if sp == "coeliac":
        gf = "FREE_FROM_GLUTEN" in cl
        if gf:
            if st["GO"] == "PRESENT" and not p["allow_gf_oats"]:
                return "FAIL", ["GF claim but contains oats; diner excludes GF oats"]
            if st["UW"] == "PRESENT" and not p["allow_gf_wheat_starch"]:
                return "FAIL", ["GF claim but contains wheat (starch); diner excludes it"]
            return "PASS", ["FREE_FROM_GLUTEN (<=20 mg/kg, Reg. 828/2014)"]
        sub = [st[c] for c in ("AW", "UW", "NR", "GB", "GO", "GS", "GK")]
        if "PRESENT" in sub:
            return "FAIL", ["gluten cereal CONTAINS without gluten-free claim"]
        if "TRACE" in sub:
            return ("REVIEW" if p["allow_trace"] else "FAIL"), ["gluten cereal MAY_CONTAIN"]
        if st["AW"] == "ABSENT":
            return "PASS", ["AW FREE_FROM"]
        return "REVIEW", ["no gluten claim and AW not FREE_FROM"]
    if sp in ("lactose_free", "low_lactose"):
        limit = 0.01 if sp == "lactose_free" else 1.0
        ok_claims = {"FREE_FROM_LACTOSE"} | ({"LOW_LACTOSE"} if sp == "low_lactose" else set())
        if st["AM"] == "ABSENT":
            return "PASS", ["AM FREE_FROM (milk-free implies lactose-free)"]
        if "LACS" in n:
            v, prec = n["LACS"]
            if v <= limit:
                return "PASS", [f"LACS={v} g/100 g ({prec})"]
            if not prec.startswith("LESS_THAN"):
                return ("REVIEW" if ok_claims & cl else "FAIL"), [
                    f"LACS={v} g/100 g exceeds {limit}"
                ]
        if ok_claims & cl:
            return "PASS", [f"claim {sorted(ok_claims & cl)} (unverified: no LACS)"]
        if st["AM"] in ("PRESENT", "UNKNOWN") or st["ML"] == "PRESENT":
            return "REVIEW", ["milk present/unknown and no lactose claim or value"]
        return "PASS", [f"AM={st['AM']} (trace milk irrelevant for intolerance)"]
    if sp == "vegan":
        hits = [c for c in ANIMAL_ALLERGENS if st[c] == "PRESENT"]
        if hits:
            return "FAIL", [f"animal allergen PRESENT: {hits}"]
        if "VEGAN" in cl:
            return "PASS", ["VEGAN claim; check E120/E441/E542/E901/E904/E966/E1105 in text"]
        return "REVIEW", ["no VEGAN claim; needs ingredient text"]
    if sp == "vegetarian":
        hits = [c for c in MEAT_FISH_ALLERGENS if st[c] == "PRESENT"]
        if hits:
            return "FAIL", [f"meat/fish PRESENT: {hits}"]
        if cl & {"VEGAN", "VEGETARIAN"}:
            return "PASS", ["VEGAN or VEGETARIAN claim"]
        return "REVIEW", ["no claim; check text for meat, gelatine (E441), rennet"]
    if sp in ("pork_free", "beef_free"):
        code = "PO" if sp == "pork_free" else "BF"
        if st[code] == "PRESENT":
            return "FAIL", [f"{code}=PRESENT"]
        if cl & {"VEGAN", "VEGETARIAN"}:
            return "PASS", ["vegetarian/vegan claim"]
        return "REVIEW", ["needs ingredient text: species of meat, gelatine, casing, broth"]
    if sp == "certification_required":
        return "REVIEW", ["no halal/kosher label or claim exists in this catalog"]
    if sp == "sydanmerkki":
        if "FINNISH_HEART_SYMBOL" in lb:
            return "PASS", ["FINNISH_HEART_SYMBOL label"]
        return "REVIEW", ["no label; compute nutrients vs SYDANMERKKI[group] limits"]
    if sp == "not_worse_energy_protein":
        return ("PASS" if "PRO-" in n and "ENER-KJO" in n else "REVIEW"), [
            "compare PRO-/ENER- vs original"
        ]
    if sp == "curated_texture":
        return "REVIEW", ["no texture attribute in Synkka; needs curated texture class"]
    return "REVIEW", ["unhandled diet"]


# ----------------------------------------------------- text fallback ------
LEX = {
    "AW": {"stems": ["gluteeni", "gluten"], "ff": []},
    "UW": {
        "stems": [
            "vehnä",
            "vehnän",
            "vehnää",
            "durum",
            "manna",
            "mannasuurimo",
            "mannaryyni",
            "seitan",
            "bulgur",
            "couscous",
            "kuskus",
            "graham",
            "korppujauho",
            "vete",
            "vetemjöl",
            "wheat",
            "semolina",
            "farro",
            "freekeh",
            "emmer",
            "einkorn",
            "panko",
        ],
        "ff": ["mannaa", "tattari", "bovete", "buckwheat", "pepparkorn", "maissisemolina"],
    },
    "GS": {"stems": ["speltti", "spelt", "dinkel"], "ff": []},
    "GK": {"stems": ["khorasan", "kamut"], "ff": []},
    "NR": {
        "stems": ["ruis", "rukii", "ruista", "råg", "rågmjöl", "rye"],
        "ff": ["rågvete", "ruiskaunok"],
    },
    "GB": {
        "stems": [
            "ohra",
            "ohran",
            "ohraa",
            "mallas",
            "mallasuute",
            "mallasetikka",
            "korn",
            "kornmjöl",
            "korngryn",
            "kornmalt",
            "malt",
            "maltextrakt",
            "barley",
        ],
        "ff": [
            "majskorn",
            "fullkorn",
            "pepparkorn",
            "maltodekstriini",
            "maltodextrin",
            "maltodek",
            "maltodes",
            "maltodex",
            "maltitol",
            "maltitoli",
            "maltos",
            "maltoosi",
            "mallasjuoma?",
        ],
    },
    "GO": {
        "stems": ["kaura", "kauran", "havre", "oat", "oats", "oatmeal"],
        "ff": ["goat"],
    },
    "TR": {"stems": ["ruisvehnä", "rågvete", "triticale"], "ff": []},
    "AC": {
        "stems": [
            "äyriäi",
            "katkarapu",
            "katkarav",
            "rapu",
            "ravu",
            "rapuj",
            "hummeri",
            "taskurapu",
            "langustiini",
            "langusti",
            "krilli",
            "scampi",
            "kräftdjur",
            "räka",
            "räkor",
            "kräfta",
            "kräftor",
            "hummer",
            "krabba",
            "havskräfta",
            "crustacea",
            "shrimp",
            "prawn",
            "crayfish",
            "lobster",
            "crab",
            "langoustine",
            "krill",
        ],
        "ff": ["rapea", "rapsi", "raps", "crabapple", "makrill"],
    },
    "AE": {
        "stems": [
            "kananmuna",
            "muna",
            "munan",
            "munaa",
            "munia",
            "munat",
            "keltuai",
            "valkuai",
            "kokomuna",
            "munajauhe",
            "albumiini",
            "ovalbumiini",
            "lysotsyymi",
            "e1105",
            "majoneesi",
            "marenki",
            "ägg",
            "äggula",
            "äggvita",
            "helägg",
            "äggpulver",
            "albumin",
            "lysozym",
            "majonnäs",
            "maräng",
            "egg",
            "eggs",
            "albumen",
            "lysozyme",
            "mayonnaise",
            "meringue",
        ],
        "ff": [
            "munakoiso",
            "aubergine",
            "äggplanta",
            "eggplant",
            "seerumialbumiini",
            "maitoproteiini",
            "vehnänvalkuainen?",
            "valkuaisaine",
            "kasvivalkuai",
            "soijavalkuai",
            "herneproteiini",
        ],
    },
    "AF": {
        "stems": [
            "kala",
            "kalan",
            "kalaa",
            "kalat",
            "kalaliemi",
            "kalakastike",
            "kalaöljy",
            "anjovis",
            "sardelli",
            "sardiini",
            "silli",
            "silakka",
            "silaka",
            "lohi",
            "lohen",
            "lohta",
            "kirjolohi",
            "turska",
            "seiti",
            "kolja",
            "tonnikala",
            "makrilli",
            "muikku",
            "kuha",
            "ahven",
            "hauki",
            "siika",
            "taimen",
            "pangasius",
            "tilapia",
            "kampela",
            "kilohaili",
            "mäti",
            "kaviaari",
            "worcester",
            "surimi",
            "fisk",
            "fiskolja",
            "ansjovis",
            "sardin",
            "sill",
            "strömming",
            "lax",
            "torsk",
            "tonfisk",
            "makrill",
            "fish",
            "anchov",
            "sardine",
            "herring",
            "salmon",
            "trout",
            "pollock",
            "saithe",
            "haddock",
            "mackerel",
        ],
        "exact": ["tuna", "cod", "lax", "sill", "sej"],
        "ff": [
            "mustekala",
            "bläckfisk",
            "kalamata",
            "kalja",
            "kalkkuna",
            "kalium",
            "kalsium",
            "kalkki",
            "kaali",
            "sillä",
            "rommi",
            "codeine",
            "laxativ",
            "vahven",
            "basilik",
        ],
    },
    "AP": {
        "stems": ["maapähkinä", "maapähkinöi", "arachis", "jordnöt", "peanut", "groundnut"],
        "ff": [],
    },
    "AY": {
        "stems": [
            "soija",
            "soijan",
            "soijaa",
            "soja",
            "tofu",
            "tempeh",
            "edamame",
            "miso",
            "natto",
            "tamari",
            "soy",
            "soya",
            "soybean",
            "shoyu",
        ],
        "ff": ["annatto", "tamarind", "isoja"],
    },
    "AM": {
        "stems": [
            "maito",
            "maidon",
            "maitoa",
            "kerma",
            "kerman",
            "kermaa",
            "voi",
            "voin",
            "voita",
            "voirasva",
            "voiöljy",
            "ghee",
            "hera",
            "heran",
            "herajauhe",
            "heraproteiini",
            "kaseiini",
            "kaseinaatti",
            "laktoosi",
            "juusto",
            "rahka",
            "kvarkki",
            "jogurtti",
            "piimä",
            "viili",
            "kefiri",
            "jäätelö",
            "smetana",
            "mascarpone",
            "ricotta",
            "mozzarella",
            "parmesaani",
            "emmental",
            "cheddar",
            "fetajuusto",
            "halloumi",
            "paneer",
            "mjölk",
            "mjölkpulver",
            "grädde",
            "smör",
            "smörfett",
            "vassle",
            "kasein",
            "laktos",
            "ost",
            "osten",
            "färskost",
            "kvarg",
            "yoghurt",
            "filmjölk",
            "milk",
            "cream",
            "butter",
            "butterfat",
            "buttermilk",
            "whey",
            "casein",
            "caseinate",
            "lactose",
            "cheese",
            "yogurt",
            "quark",
            "curd",
        ],
        "ff": [
            "maitohappo",
            "kookosmaito",
            "kookoskerma",
            "kaurajuoma",
            "kauramaito",
            "kaurakerma",
            "soijamaito",
            "soijakerma",
            "mantelimaito",
            "riisimaito",
            "mantelijuoma",
            "kaakaovoi",
            "sheavoi",
            "maapähkinävoi",
            "pähkinävoi",
            "voidaan",
            "mjölksyra",
            "kokosmjölk",
            "kokosgrädde",
            "kakaosmör",
            "sheasmör",
            "jordnötssmör",
            "mjöl",
            "ostron",
            "lactic",
            "lactate",
            "coconut",
            "cocoa",
            "shea",
            "peanut",
            "tartar",
            "laktitoli",
            "laktitol",
            "lactitol",
            "maitohappobakteeri",
            "kasvirasva",
            "juustokumin",
            "voitatti",
            "paneerau",
            "paneroi",
        ],
    },
    "AN": {
        "stems": [
            "pähkinä",
            "pähkinän",
            "pähkinää",
            "pähkinöi",
            "manteli",
            "marsipaani",
            "hasselpähkinä",
            "nougat",
            "gianduja",
            "saksanpähkinä",
            "cashew",
            "pekaani",
            "parapähkinä",
            "brasilianpähkinä",
            "pistaasi",
            "makadamia",
            "queensland",
            "praliini",
            "nöt",
            "nötter",
            "mandel",
            "mandlar",
            "marsipan",
            "hasselnöt",
            "valnöt",
            "pekan",
            "paranöt",
            "pistasch",
            "nut",
            "nuts",
            "almond",
            "marzipan",
            "frangipane",
            "hazelnut",
            "filbert",
            "praline",
            "walnut",
            "pecan",
            "pistachio",
            "macadamia",
        ],
        "ff": [
            "maapähkinä",
            "jordnöt",
            "peanut",
            "groundnut",
            "kookos",
            "kokos",
            "coconut",
            "pinja",
            "pinjenöt",
            "pine",
            "kastanja",
            "kastanj",
            "chestnut",
            "muskotti",
            "muskotnöt",
            "nutmeg",
            "nötkött",
            "nötfärs",
            "nötstek",
            "nötbuljong",
            "butternut",
            "doughnut",
            "donut",
            "shea",
            "kola",
            "tigernöt",
            "tiger",
        ],
    },
    "BC": {"stems": ["selleri", "sellerin", "celery", "celeriac"], "ff": []},
    "BM": {
        "stems": ["sinappi", "sinapin", "sinappia", "dijon", "senap", "senaps", "mustard"],
        "ff": [],
    },
    "AS": {
        "stems": [
            "seesami",
            "seesamin",
            "tahini",
            "tahina",
            "halva",
            "gomasio",
            "sesam",
            "sesame",
            "benne",
            "gingelly",
        ],
        "ff": [],
    },
    "AU": {
        "stems": [
            "sulfiitti",
            "sulfiitin",
            "sulfiitte",
            "rikkidioksidi",
            "disulfiitti",
            "metabisulfiitti",
            "sulfit",
            "svaveldioxid",
            "sulphite",
            "sulfite",
            "sulphur dioxide",
            "sulfur dioxide",
            "e220",
            "e221",
            "e222",
            "e223",
            "e224",
            "e226",
            "e227",
            "e228",
        ],
        "ff": [
            "sulfaatti",
            "sulfat",
            "sulphate",
            "sulfate",
            "sulfiittimenetelm",
            "sulfiittisokeriku",
            "ammoniumsulfiit",
            "e150",
        ],
    },
    "NL": {"stems": ["lupiini", "lupin", "lupine"], "ff": ["lupulin"]},
    "UM": {
        "stems": [
            "nilviäi",
            "simpukka",
            "simpukan",
            "simpuko",
            "osteri",
            "kampasimpukka",
            "kalmari",
            "mustekala",
            "seepia",
            "etana",
            "escargot",
            "abalone",
            "blötdjur",
            "mussla",
            "musslor",
            "ostron",
            "bläckfisk",
            "kalmar",
            "snigel",
            "mollusc",
            "mollusk",
            "mussel",
            "oyster",
            "scallop",
            "clam",
            "cockle",
            "squid",
            "calamari",
            "octopus",
            "cuttlefish",
            "snail",
            "whelk",
        ],
        "ff": [
            "osterivinok",
            "ostronskivling",
            "oyster mushroom",
            "simpukkapasta",
            "snäckor",
            "snäckpasta",
            "smetana",
        ],
    },
    "PO": {
        "stems": [
            "sianliha",
            "sian",
            "porsas",
            "porsaan",
            "possu",
            "kinkku",
            "pekoni",
            "silava",
            "laardi",
            "ihra",
            "fläsk",
            "gris",
            "skinka",
            "bacon",
            "pork",
            "ham",
            "lard",
        ],
        "ff": ["hamburger", "graham", "champignon"],
    },
    "BF": {
        "stems": ["nauta", "naudan", "härkä", "vasikka", "nötkött", "nötfärs", "beef", "veal"],
        "ff": [],
    },
    "CM": {
        "stems": ["broileri", "kana", "kanan", "kananliha", "kyckling", "chicken"],
        "ff": ["kananmuna", "kanapes?"],
    },
    "INSECT": {
        "stems": [
            "hyönteis",
            "jauhomato",
            "kotisirkka",
            "sirkkajauhe",
            "kulkusirkka",
            "insekt",
            "mjölmask",
            "syrsa",
            "insect",
            "mealworm",
            "cricket",
            "locust",
        ],
        "ff": ["sirkkalehti", "sirkus"],
    },
}
UNCERTAIN_TERMS = [
    "vilja",
    "spannmål",
    "cereal",
    "lesitiini",
    "e322",
    "lecithin",
    "lecitin",
    "emulgointiaine",
    "emulsifier",
    "aromi",
    "arom",
    "flavouring",
    "mausteet",
    "mausteseos",
    "kryddor",
    "spices",
    "liemi",
    "buljong",
    "stock",
    "tärkkelys",
    "stärkelse",
    "starch",
    "margariini",
    "margarin",
    "margarine",
    "kasvirasva",
    "liivate",
    "gelatiini",
    "gelatin",
    "gelatine",
    "e441",
    "e471",
    "e472",
    "e120",
    "e542",
    "e904",
    "e920",
    "juoksete",
    "löpe",
    "rennet",
    "pesto",
    "hummus",
    "surimi",
]
EXEMPT_RE = re.compile(
    r"(glukoosi(-fruktoosi)?siirappi|glukoosisiirappi|dekstroosi|maltodekstriini|"
    r"glukossirap|dextros|maltodextrin|glucose(-fructose)? syrup|dextrose|"
    r"täysin puhdistettu soijaöljy|soijaöljy|soijapapuöljy|sojaolja|soybean oil|"
    r"soya oil|tokoferoli\w*|tocopherol\w*|e306|kasvisteroli\w*|fytosteroli\w*|"
    r"kasvistanoliesteri\w*|plant stanol ester\w*|laktitoli|laktitol|lactitol|e966)"
    r"\s*(\([^)]*\))?",
    re.I,
)
PAL_RE = re.compile(
    r"(saattaa\s+sisältää|voi\s+sisältää|jäämiä|"
    r"valmistettu\s+(tiloissa|linjalla|laitoksessa)|"
    r"samalla\s+(tuotanto)?linjalla|käsitellään\s+myös|pakattu\s+tiloissa|"
    r"kan\s+innehålla|spår\s+av|kan\s+förekomma|may\s+contain|traces?\s+of|"
    r"made\s+(in|on)\s+(a\s+)?(factory|line|premises))",
    re.I,
)
NEG_TOKEN_RE = re.compile(
    r"(ton|tön|ttoma\w*|ttömä\w*|tonta|töntä|vapaa\w*|fri|fritt|fria|free)$",
    re.I,
)
TOKEN_RE = re.compile(r"[a-zåäöéèüß0-9]+", re.I)


def _stem_hit(token: str, stem: str) -> bool:
    s = stem.rstrip("?")
    if " " in s:
        return False
    if len(s) >= 5:
        return s in token
    if len(s) == 4:
        return token.startswith(s) or token.endswith(s)
    return token == s


def _ff_hit(token: str, ff: list[str]) -> bool:
    return any(f.rstrip("?") in token for f in ff if " " not in f)


def parse_ingredients(text: str | None) -> tuple[dict[str, str], list[str]]:
    """-> {code: 'PRESENT'|'TRACE'}, uncertain_terms. Adds restrictions only."""
    out: dict[str, str] = {}
    unc: set[str] = set()
    cleaned = EXEMPT_RE.sub(" ", text or "")
    for seg in re.split(r"(?<=[.;])\s+|\n", cleaned):
        level = "TRACE" if PAL_RE.search(seg) else "PRESENT"
        seg_l = PAL_RE.sub(" ", seg).lower()
        tokens = TOKEN_RE.findall(seg_l)
        for code, entry in LEX.items():
            multi = [s for s in entry["stems"] if " " in s]
            hit = any(m in seg_l for m in multi)
            for t in tokens:
                if hit:
                    break
                if NEG_TOKEN_RE.search(t) and len(t) > 6:
                    continue
                if _ff_hit(t, entry["ff"]):
                    continue
                hit = any(_stem_hit(t, s) for s in entry["stems"]) or t in entry.get("exact", ())
            if hit and STATE_RANK.get(level, 0) > STATE_RANK.get(out.get(code, "ABSENT"), 0):
                out[code] = level
        unc.update(t for t in tokens if any(t.startswith(u) for u in UNCERTAIN_TERMS))
    return out, sorted(unc)


def text_states(
    product: dict, langs: tuple[str, ...] = ("fi", "sv", "en")
) -> tuple[dict[str, str], list[str]]:
    ki = (product.get("synkkaData") or {}).get("keyIngredients") or []
    by_lang = {k.get("language"): k.get("value") for k in ki if k.get("value")}
    for lang in langs:
        if lang in by_lang:
            return parse_ingredients(by_lang[lang])
    return {}, []
