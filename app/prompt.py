import json

from .example import EXAMPLE_JSON
from .models import TM6Recipe

# Technique tables and adaptation rules partly adapted from
# github.com/guimatheus92/cookidoo-recipe-creator (MIT).
SYSTEM_PROMPT = """\
You convert recipes into step-by-step instructions for a Thermomix TM6 used in MANUAL mode \
(no guided cooking). The cook reads one step at a time on a tablet, weighs ingredients on the \
TM6's built-in scale, and dials in each setting by hand.

## TM6 limits
- Mixing bowl holds 2.2 L max; heating max fill is ~2.0 L.
- Temperature 37-120 C normally; 'varoma' (~120 C, steam) for steaming and fast reducing; \
up to 160 C only in the 'browning' mode.
- Speeds: 'spoon' (gentle stir), then 0.5 to 10 in 0.5 steps. With hot contents keep speed \
<= 3 to avoid splashing. Turbo is a mode: short pulses of 0.5-10 s.
- Reverse (counter-clockwise) stirs without cutting: use it whenever pieces must stay whole \
(meat chunks, risotto, stews, folding).
- Butterfly whisk: max speed 4, never with the spatula. Whipping cream/egg whites, emulsions.
- Simmering basket: steaming small items, straining, or set on the lid instead of the \
measuring cup to stop splatter while steam escapes.
- Measuring cup in: keeps heat and splashes in (default for chopping, blending, simmering). \
Measuring cup out / basket on lid: when reducing, sauteing or browning.
- Modes: kneading (time only), turbo, browning, slow_cook, sous_vide, fermentation, \
rice_cooker, kettle, warm_up, thicken, blend.

## Typical settings
| Operation | Setting |
|---|---|
| Chop onion/garlic | 3-5 s / speed 5, scrape down |
| Chop vegetables coarsely | 3-5 s / speed 4 |
| Grind nuts, grate hard cheese, mill sugar | 10-15 s / speed 10 |
| Mince meat | turbo pulses, 2-4 x 1 s, meat partly frozen |
| Mix dry ingredients | 5 s / speed 4 |
| Cream butter + sugar | 1 min / speed 4 |
| Add eggs / liquids to a batter | 20 s / speed 3 |
| Fold in dry ingredients | 15 s / speed 2, reverse |
| Add chunks without crushing | 10 s / speed 1, reverse (or by hand) |
| Knead dough | 2-3 min kneading mode |
| Saute aromatics | 3-5 min / 120 C / speed 1 (or spoon, reverse), measuring cup out |
| Simmer / boil | X min / 100 C / speed 1 |
| Stew / braise chunks | X min / 100 C / speed spoon, reverse |
| Steam (Varoma dish) | X min / varoma / speed 1, with >= 500 g water in the bowl |
| Reduce a sauce | 10-15 min / varoma / speed 1, reverse, measuring cup out |
| Whip cream | butterfly whisk, speed 3.5, watch closely |
| Puree soup | 1 min, ramp speed 5 -> 10, only once below ~70 C |

## Adapting what the TM6 cannot do
- Pressure cooker: no pressure mode. Braise at 100 C / speed spoon / reverse for roughly \
twice the pressure time, or use slow_cook (2-4 h) for best texture.
- Searing for a crust: the bowl steams instead of browning. Sear in a hot skillet in batches \
and return the meat to the bowl (tm6 = null for that step), mentioning browning mode \
(160 C, reverse, cup out) as the second-best in-bowl option.
- Deep/shallow frying, grilling, oven baking, resting, chilling, plating: keep them as steps \
with tm6 = null.
- Over capacity: if the total goes past the 2.2 L bowl (or ~2.0 L when heating), describe \
working in batches in the step text and add a note. Do not silently shrink the recipe.

## Conversion rules
- Reorder ingredients into the order the Thermomix uses them. Give weights in grams wherever \
reasonable: convert cups, spoons and volumes using the ingredient's density (water, stock, \
milk: 1 ml ~ 1 g). Keep countable items as pieces (eggs, cloves, bay leaves) and keep \
'pinch'/'to taste' as quantity null with the phrase in `note`.
- Group ingredients with `section` when the recipe has distinct parts (e.g. 'Sauce').
- One step per Thermomix setting. Each step's `ingredients` lists the 0-based indexes of the \
ingredients added in that step.
- In the step text, name every ingredient added with its exact amount from the ingredient \
list ('Add 450 g tomato passata and 6 bay leaves'), so the cook can weigh straight from the \
step. Do not write 'add the passata'.
- Put the machine settings in `tm6` only; the text says what to do and what to look for \
('until golden', 'scrape down the sides with the spatula', 'insert the butterfly whisk').
- Do not invent ingredients. If one is needed (e.g. water for steaming), list it and explain \
it in `notes`.
- Estimate prep_minutes (hands-on work), cook_minutes (machine/oven time) and \
total_minutes (everything, including resting/chilling) for the converted method.
- `notes`: assumptions, accessories needed, capacity warnings, anything uncertain.
- Respond with ONE JSON object and nothing else.
"""


def schema_json() -> str:
    return json.dumps(TM6Recipe.model_json_schema(), indent=1)


def _ingredient_line(ing: dict) -> str:
    prefix = f"[section: {ing['title']}] " if ing.get("title") else ""
    text = ing.get("display") or ing.get("originalText") or ing.get("note") or ""
    if not text:
        parts = [
            str(ing.get("quantity") or ""),
            (ing.get("unit") or {}).get("name", "") if ing.get("unit") else "",
            (ing.get("food") or {}).get("name", "") if ing.get("food") else "",
            ing.get("note") or "",
        ]
        text = " ".join(p for p in parts if p)
    return prefix + text.strip()


def _minutes(recipe: dict, seconds_key: str, text_key: str) -> str | None:
    if recipe.get(seconds_key):
        return f"{round(recipe[seconds_key] / 60)} min"
    return recipe.get(text_key) or None


def recipe_to_prompt(recipe: dict) -> str:
    lines = [f"# {recipe.get('name')}"]
    if recipe.get("description"):
        lines.append(recipe["description"])
    yield_bits = [
        recipe.get("recipeYield"),
        f"{recipe['recipeServings']:g} servings" if recipe.get("recipeServings") else None,
    ]
    if any(yield_bits):
        lines.append("Yield: " + ", ".join(b for b in yield_bits if b))
    for label, sec, txt in [
        ("Prep", "prepTimeSeconds", "prepTime"),
        ("Cook", "performTimeSeconds", "performTime"),
        ("Total", "totalTimeSeconds", "totalTime"),
    ]:
        if value := _minutes(recipe, sec, txt):
            lines.append(f"{label} time: {value}")

    lines.append("\n## Ingredients")
    for ing in recipe.get("recipeIngredient") or []:
        if line := _ingredient_line(ing):
            lines.append(f"- {line}")

    lines.append("\n## Instructions")
    for i, step in enumerate(recipe.get("recipeInstructions") or [], 1):
        title = f" ({step['title']})" if step.get("title") else ""
        lines.append(f"{i}.{title} {step.get('text', '').strip()}")

    if recipe.get("notes"):
        lines.append("\n## Notes")
        for note in recipe["notes"]:
            lines.append(f"- {note.get('title', '')}: {note.get('text', '')}")

    return "\n".join(lines)


def build_messages(recipe: dict, json_mode: str) -> list[dict]:
    user = (
        "Convert this recipe for the Thermomix TM6.\n\n"
        f"{recipe_to_prompt(recipe)}\n\n"
        "Return JSON matching this JSON Schema:\n"
        f"{schema_json()}\n\n"
        "For reference, here is a good conversion of a different recipe (braised beef "
        "sandwiches for 12) in that format. Match its level of detail, not its content:\n"
        f"{EXAMPLE_JSON}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
