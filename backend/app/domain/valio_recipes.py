"""Curated Nordic foodservice recipes featuring subtle Valio & Oddlygood pairings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MealType = Literal["breakfast", "lunch", "dinner"]


@dataclass(frozen=True)
class RecipeIngredientSpec:
    """Ingredient role mapped to catalog search hints and per-10-pax portion scaling."""

    role: str
    label: str
    preferred_product_ids: tuple[str, ...]
    search_keywords: tuple[str, ...]
    category_prefixes: tuple[str, ...]
    qty_per_10_pax: float
    unit_hint: str


@dataclass(frozen=True)
class ValioRecipe:
    """Foodservice recipe definition with diet compatibility tags and ingredient specs."""

    id: str
    name: str
    meal_type: MealType
    diet_tags: tuple[str, ...]
    ingredients: tuple[RecipeIngredientSpec, ...]
    chef_note: str


VALIO_RECIPES: tuple[ValioRecipe, ...] = (
    ValioRecipe(
        id="lohikeitto",
        name="Creamy Nordic Salmon Soup (Lohikeitto)",
        meal_type="lunch",
        diet_tags=("L", "G"),
        ingredients=(
            RecipeIngredientSpec(
                role="fish",
                label="Nordic salmon or white fish",
                preferred_product_ids=("461814", "461871", "461503", "460038"),
                search_keywords=("lohi", "kirjolohi", "salmon", "turska"),
                category_prefixes=("187", "4172"),
                qty_per_10_pax=1.5,
                unit_hint="KG/KI",
            ),
            RecipeIngredientSpec(
                role="cream",
                label="Valio lactose-free cream or milk",
                preferred_product_ids=("461490", "464699", "464306", "464398"),
                search_keywords=("kuohukerma", "kerma", "kevytmaitojuoma"),
                category_prefixes=("10401", "10400"),
                qty_per_10_pax=2.0,
                unit_hint="L/KI",
            ),
            RecipeIngredientSpec(
                role="root_veg",
                label="Potato & soup root vegetables",
                preferred_product_ids=("460078", "460096", "460006", "460000"),
                search_keywords=("peruna", "keittojuures", "porkkana"),
                category_prefixes=("1742", "4173", "1730"),
                qty_per_10_pax=2.0,
                unit_hint="KG/KI",
            ),
            RecipeIngredientSpec(
                role="butter",
                label="Valio lactose-free butter",
                preferred_product_ids=("462421", "463437"),
                search_keywords=("voi", "oivariini"),
                category_prefixes=("105",),
                qty_per_10_pax=1.0,
                unit_hint="ST",
            ),
        ),
        chef_note=(
            "Finish gently off the heat with Valio lactose-free cream and butter for a "
            "silky, traditional Finnish archipelago broth."
        ),
    ),
    ValioRecipe(
        id="koskenlaskija_gratin",
        name="Koskenlaskija Root Vegetable Gratin",
        meal_type="dinner",
        diet_tags=("L", "G"),
        ingredients=(
            RecipeIngredientSpec(
                role="cheese",
                label="Valio Koskenlaskija processed cheese",
                preferred_product_ids=("463592", "463788", "462630", "460086"),
                search_keywords=("koskenlaskija", "sulatejuusto", "juusto"),
                category_prefixes=("1063", "1060", "4177"),
                qty_per_10_pax=1.0,
                unit_hint="KI/ST",
            ),
            RecipeIngredientSpec(
                role="root_veg",
                label="Root vegetable puree or mix",
                preferred_product_ids=("460096", "460106", "460006"),
                search_keywords=("porkkana", "palsternakka", "peruna", "juures"),
                category_prefixes=("4173", "1742"),
                qty_per_10_pax=2.0,
                unit_hint="KI/BAG",
            ),
            RecipeIngredientSpec(
                role="cream",
                label="Valio lactose-free cream or milk",
                preferred_product_ids=("461490", "464699", "464306"),
                search_keywords=("kuohukerma", "kevytmaitojuoma"),
                category_prefixes=("10401", "10400"),
                qty_per_10_pax=2.0,
                unit_hint="L/KI",
            ),
        ),
        chef_note=(
            "Melt Valio Koskenlaskija into warm lactose-free cream for a rich, sliceable "
            "Nordic root gratin that holds well on buffet lines."
        ),
    ),
    ValioRecipe(
        id="berry_quark_parfait",
        name="Lactose-Free Berry Quark Parfait",
        meal_type="breakfast",
        diet_tags=("L", "G"),
        ingredients=(
            RecipeIngredientSpec(
                role="quark",
                label="Valio lactose-free quark or yogurt",
                preferred_product_ids=("461571", "462718", "463472", "464306"),
                search_keywords=("rahka", "jogurtti", "maitojuoma"),
                category_prefixes=("1043", "1041", "1040"),
                qty_per_10_pax=4.0,
                unit_hint="KI/ST",
            ),
            RecipeIngredientSpec(
                role="berries",
                label="Nordic bilberry / berry puree",
                preferred_product_ids=("460138", "460128", "460011", "460126"),
                search_keywords=("mustikka", "marja", "mansikka", "ruusunmarja"),
                category_prefixes=("4174", "2145"),
                qty_per_10_pax=1.0,
                unit_hint="KI/BAG",
            ),
        ),
        chef_note=(
            "Layer whipped Valio vanilla quark or Turkish yogurt with Nordic berry puree "
            "for a bright, portion-controlled hotel breakfast cup."
        ),
    ),
    ValioRecipe(
        id="herb_butter_chicken_mash",
        name="Herb Butter Roasted Chicken & Mash",
        meal_type="dinner",
        diet_tags=("L", "G"),
        ingredients=(
            RecipeIngredientSpec(
                role="poultry",
                label="Roast chicken / protein",
                preferred_product_ids=("460711", "460773", "460121"),
                search_keywords=("broileri", "kana", "pyörykkä"),
                category_prefixes=("4171", "4170"),
                qty_per_10_pax=1.0,
                unit_hint="KRT/KI",
            ),
            RecipeIngredientSpec(
                role="butter",
                label="Valio lactose-free butter",
                preferred_product_ids=("462421", "463437", "462792"),
                search_keywords=("voi", "oivariini"),
                category_prefixes=("105",),
                qty_per_10_pax=1.0,
                unit_hint="ST",
            ),
            RecipeIngredientSpec(
                role="mash_base",
                label="Potato mash base & Valio Eila milk",
                preferred_product_ids=("464306", "460243", "463038"),
                search_keywords=("perunamuusi", "maitojuoma", "peruna"),
                category_prefixes=("10400", "2139"),
                qty_per_10_pax=2.0,
                unit_hint="ST/KI",
            ),
        ),
        chef_note=(
            "Baste poultry with Valio lactose-free herb butter and whip smooth potato "
            "mash with warm Valio Eila milk."
        ),
    ),
    ValioRecipe(
        id="spinach_cheese_frittata",
        name="Spinach & Valio Cheese Frittata",
        meal_type="breakfast",
        diet_tags=("L", "G"),
        ingredients=(
            RecipeIngredientSpec(
                role="cheese",
                label="Valio Oltermanni or Koskenlaskija cheese",
                preferred_product_ids=("462584", "463788", "462604", "460086"),
                search_keywords=("oltermanni", "juusto", "mozzarella"),
                category_prefixes=("1060", "1063", "4177"),
                qty_per_10_pax=1.0,
                unit_hint="ST/RAS",
            ),
            RecipeIngredientSpec(
                role="greens",
                label="Spinach or green vegetable puree",
                preferred_product_ids=("461226", "460363", "460094", "460037"),
                search_keywords=("pinaatti", "parsa", "maissi"),
                category_prefixes=("4173", "1740"),
                qty_per_10_pax=1.0,
                unit_hint="BAG/KI",
            ),
            RecipeIngredientSpec(
                role="dairy",
                label="Valio lactose-free milk or cream",
                preferred_product_ids=("464306", "461490", "464398"),
                search_keywords=("maitojuoma", "kuohukerma"),
                category_prefixes=("10400", "10401"),
                qty_per_10_pax=2.0,
                unit_hint="ST/KI",
            ),
        ),
        chef_note=(
            "Fold sliced or grated Valio cheese and tender greens into a gentle custard "
            "for golden, sliceable breakfast trays."
        ),
    ),
    ValioRecipe(
        id="oat_berry_breakfast_bowl",
        name="Oat & Berry Hotel Breakfast Bowl",
        meal_type="breakfast",
        diet_tags=("L", "VL", "M", "G", "VEG"),
        ingredients=(
            RecipeIngredientSpec(
                role="oat_base",
                label="Oddlygood oat drink or plant yogurt",
                preferred_product_ids=("468465", "460005"),
                search_keywords=("kaurajuoma", "oddlygood", "soijavalmiste"),
                category_prefixes=("10402", "10413"),
                qty_per_10_pax=3.0,
                unit_hint="ST/PAK",
            ),
            RecipeIngredientSpec(
                role="berries",
                label="Nordic bilberry / rosehip puree",
                preferred_product_ids=("460138", "460128", "460011"),
                search_keywords=("mustikka", "marja", "mustaherukka"),
                category_prefixes=("4174", "2145"),
                qty_per_10_pax=1.0,
                unit_hint="KI/BAG",
            ),
        ),
        chef_note=(
            "100% plant-based, milk-free, and gluten-free breakfast option pairing "
            "Oddlygood gluten-free oat drink with Nordic berries."
        ),
    ),
    ValioRecipe(
        id="creamy_oat_root_pasta",
        name="Creamy Nordic Plant-Based Root & Herb Pasta",
        meal_type="lunch",
        diet_tags=("L", "VL", "M", "VEG"),
        ingredients=(
            RecipeIngredientSpec(
                role="plant_cream",
                label="Oddlygood Easy Cooking oat cream",
                preferred_product_ids=("469094", "468465", "460005"),
                search_keywords=("oddlygood", "easy cooking", "kaurajuoma", "soija"),
                category_prefixes=("10406", "10402", "10413"),
                qty_per_10_pax=2.0,
                unit_hint="KI/ST",
            ),
            RecipeIngredientSpec(
                role="pasta",
                label="Foodservice pasta",
                preferred_product_ids=("460008", "460001"),
                search_keywords=("pasta", "fusilli", "makaroni"),
                category_prefixes=("2138",),
                qty_per_10_pax=1.0,
                unit_hint="BAG/SK",
            ),
            RecipeIngredientSpec(
                role="vegetables",
                label="Root vegetable or corn puree",
                preferred_product_ids=("460096", "460037", "460013"),
                search_keywords=("porkkana", "maissi", "tomaatti"),
                category_prefixes=("4173", "1740"),
                qty_per_10_pax=1.0,
                unit_hint="KI/KG",
            ),
        ),
        chef_note=(
            "Silky plant-based pasta sauce using Oddlygood Easy Cooking UHT oat cream — "
            "naturally milk-free and vegan."
        ),
    ),
)


def match_recipes(
    *,
    meal_type: MealType | None = None,
    diet_tokens: list[str] | None = None,
) -> list[ValioRecipe]:
    """Select Valio foodservice recipes matching meal_type and optional diet codes."""
    candidates = list(VALIO_RECIPES)
    if meal_type is not None:
        candidates = [r for r in candidates if r.meal_type == meal_type]

    if diet_tokens:
        upper_tokens = [tok.upper() for tok in diet_tokens if tok.strip()]
        exact = [
            r
            for r in candidates
            if all(tok in {t.upper() for t in r.diet_tags} for tok in upper_tokens)
        ]
        if exact:
            return exact
    return candidates
