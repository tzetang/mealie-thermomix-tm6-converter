"""Few-shot example for the LLM.

English adaptation of recipes/example-carne-louca.json from
github.com/guimatheus92/cookidoo-recipe-creator (MIT), rewritten into the TM6Recipe schema.
It is validated at import so it can never drift from the schema.
"""

import json

from .models import TM6Recipe

EXAMPLE = {
    "ingredients": [
        {"quantity": 1300, "unit": "g", "item": "beef chuck", "note": "cut into 3 cm cubes", "section": "Beef"},
        {"quantity": None, "unit": None, "item": "salt", "note": "to taste", "section": "Beef"},
        {"quantity": None, "unit": None, "item": "smoked paprika", "note": "to taste", "section": "Beef"},
        {"quantity": None, "unit": None, "item": "black pepper", "note": "to taste", "section": "Beef"},
        {"quantity": 60, "unit": "g", "item": "vegetable oil", "note": "divided", "section": "Beef"},
        {"quantity": 2, "unit": None, "item": "large onions", "note": "halved", "section": "Braise"},
        {"quantity": 8, "unit": "clove", "item": "garlic", "note": "peeled", "section": "Braise"},
        {"quantity": 2, "unit": None, "item": "red bell peppers", "note": "quartered", "section": "Braise"},
        {"quantity": 2, "unit": None, "item": "green bell peppers", "note": "quartered", "section": "Braise"},
        {"quantity": 400, "unit": "g", "item": "tomatoes", "note": "about 4, quartered", "section": "Braise"},
        {"quantity": 450, "unit": "g", "item": "tomato passata", "note": None, "section": "Braise"},
        {"quantity": 400, "unit": "g", "item": "dark beer", "note": None, "section": "Braise"},
        {"quantity": 15, "unit": "g", "item": "soy sauce", "note": None, "section": "Braise"},
        {"quantity": 6, "unit": None, "item": "bay leaves", "note": None, "section": "Braise"},
        {"quantity": 150, "unit": "g", "item": "water", "note": "optional", "section": "Braise"},
        {"quantity": None, "unit": None, "item": "fresh chives", "note": "chopped, to taste", "section": "To serve"},
        {"quantity": None, "unit": None, "item": "fresh parsley", "note": "chopped, to taste", "section": "To serve"},
        {"quantity": 12, "unit": None, "item": "bread rolls", "note": None, "section": "To serve"},
        {"quantity": 500, "unit": "g", "item": "mozzarella", "note": None, "section": "To serve"},
    ],
    "steps": [
        {
            "title": "Season",
            "text": "Season 1300 g beef chuck with salt, smoked paprika and black pepper to taste.",
            "ingredients": [0, 1, 2, 3],
            "tm6": None,
        },
        {
            "title": "Sear",
            "text": (
                "Sear the beef in 3 batches in a very hot skillet with a little of the 60 g vegetable "
                "oil until a crust forms. Don't overcrowd the pan or it steams instead of browning. "
                "Set aside. (In-bowl alternative: browning mode, 160 °C, reverse, measuring cup out.)"
            ),
            "ingredients": [0, 4],
            "tm6": None,
        },
        {
            "title": "Chop aromatics",
            "text": "Place 2 large onions and 8 garlic cloves in the mixing bowl and chop. Scrape down the sides with the spatula.",
            "ingredients": [5, 6],
            "tm6": {"time_seconds": 5, "speed": "5", "measuring_cup": "on"},
        },
        {
            "title": "Saute",
            "text": "Add the rest of the vegetable oil and saute until soft.",
            "ingredients": [4],
            "tm6": {"time_seconds": 300, "temperature": 120, "speed": "spoon", "reverse": True, "measuring_cup": "off"},
        },
        {
            "title": "Chop peppers",
            "text": "Add 2 red bell peppers, 2 green bell peppers and 400 g tomatoes, then chop.",
            "ingredients": [7, 8, 9],
            "tm6": {"time_seconds": 3, "speed": "4", "measuring_cup": "on"},
        },
        {
            "title": "Saute",
            "text": "Saute the vegetables.",
            "ingredients": [],
            "tm6": {"time_seconds": 300, "temperature": 120, "speed": "spoon", "reverse": True, "measuring_cup": "off"},
        },
        {
            "title": "Braise",
            "text": (
                "Return the seared beef to the bowl and add 450 g tomato passata, 400 g dark beer, "
                "15 g soy sauce and 6 bay leaves; add up to 150 g water only if it looks dry. If the "
                "bowl is above the MAX mark, braise in 2 batches with half of everything. Braise until "
                "the beef pulls apart; if not, add 15-20 min. (Alternative: slow cook mode for 3-4 h.)"
            ),
            "ingredients": [10, 11, 12, 13, 14],
            "tm6": {"time_seconds": 5400, "temperature": 100, "speed": "spoon", "reverse": True, "measuring_cup": "on"},
        },
        {
            "title": "Reduce",
            "text": "Remove the bay leaves. If the sauce is watery, reduce until thick.",
            "ingredients": [],
            "tm6": {"time_seconds": 600, "temperature": "varoma", "speed": "1", "reverse": True, "measuring_cup": "off"},
        },
        {
            "title": "Shred",
            "text": "Shred the beef in portions with short bursts; stop before it turns to paste.",
            "ingredients": [],
            "tm6": {"time_seconds": 10, "speed": "4", "reverse": True},
        },
        {
            "title": "Herbs",
            "text": "Add fresh chives and fresh parsley to taste and stir through.",
            "ingredients": [15, 16],
            "tm6": {"time_seconds": 20, "speed": "spoon", "reverse": True},
        },
        {
            "title": "Serve",
            "text": "Hollow out 12 bread rolls, fill with 500 g mozzarella and the hot beef. Serve.",
            "ingredients": [17, 18],
            "tm6": None,
        },
    ],
    "notes": [
        "Searing is done in a skillet because the Thermomix bowl steams rather than browns.",
        "1.3 kg beef plus liquids exceeds the 2.2 L bowl: braise in 2 batches.",
        "Water is optional; add it only if the sauce gets too thick during braising.",
    ],
    "prep_minutes": 40,
    "cook_minutes": 120,
    "total_minutes": 170,
}

EXAMPLE_JSON = json.dumps(TM6Recipe.model_validate(EXAMPLE).model_dump(exclude_none=True), ensure_ascii=False)
