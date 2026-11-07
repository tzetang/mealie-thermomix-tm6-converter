import logging
import uuid

from .config import Settings
from .llm import LLMConverter
from .mealie import MealieClient
from .models import TM6Recipe, TM6Settings

log = logging.getLogger(__name__)

COUNT_UNITS = {"piece", "pieces", "pc", "pcs", "whole", "x", "item", "items"}

# Fields copied from the source on every (re)conversion
COPY_FIELDS = [
    "description",
    "recipeYield",
    "recipeYieldQuantity",
    "recipeServings",
    "totalTime",
    "prepTime",
    "cookTime",
    "performTime",
    "totalTimeSeconds",
    "prepTimeSeconds",
    "performTimeSeconds",
    "recipeCategory",
    "tools",
    "nutrition",
    "orgURL",
]

MODE_LABELS = {
    "kneading": "Kneading mode",
    "turbo": "Turbo",
    "browning": "Browning mode",
    "slow_cook": "Slow cook mode",
    "sous_vide": "Sous-vide mode",
    "fermentation": "Fermentation mode",
    "rice_cooker": "Rice cooker mode",
    "kettle": "Kettle mode",
    "warm_up": "Warm up mode",
    "thicken": "Thicken mode",
    "blend": "Blend mode",
}
ACCESSORY_LABELS = {
    "butterfly_whisk": "butterfly whisk",
    "simmering_basket": "simmering basket",
    "varoma": "Varoma",
    "spatula": "spatula",
}
CUP_LABELS = {
    "on": "measuring cup in",
    "off": "measuring cup out",
    "simmering_basket_on_lid": "simmering basket on lid",
}


class Skip(Exception):
    """Recipe should not be converted (already a TM6 version, etc.)."""


def fmt_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} sec"
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m} min {s} sec" if s else f"{m} min"


def fmt_qty(q: float) -> str:
    return f"{q:g}"


def settings_line(t: TM6Settings) -> str:
    parts: list[str] = []
    if t.mode:
        parts.append(MODE_LABELS[t.mode])
    if t.time_seconds:
        parts.append(fmt_time(t.time_seconds))
    if t.temperature is not None:
        parts.append("Varoma" if t.temperature == "varoma" else f"{t.temperature} °C")
    if t.reverse:
        parts.append("↺ reverse")
    if t.speed:
        parts.append("speed spoon" if t.speed == "spoon" else f"speed {t.speed}")
    if t.measuring_cup:
        parts.append(CUP_LABELS[t.measuring_cup])
    basket_on_lid = t.measuring_cup == "simmering_basket_on_lid"
    if t.accessory and not (t.accessory == "simmering_basket" and basket_on_lid):
        parts.append(f"with {ACCESSORY_LABELS[t.accessory]}")
    return " / ".join(parts)


def is_tm6_recipe(recipe: dict, s: Settings) -> bool:
    tag = s.tm6_tag.lower()
    for t in recipe.get("tags") or []:
        if (t.get("slug") or "").lower() == tag or (t.get("name") or "").lower() == tag:
            return True
    if (recipe.get("name") or "").strip().endswith(s.name_suffix):
        return True
    return s.extras_key in (recipe.get("extras") or {})


async def build_ingredients(tm6: TM6Recipe, mealie: MealieClient) -> list[dict]:
    out: list[dict] = []
    last_section: str | None = None
    for ing in tm6.ingredients:
        item = ing.item + (f", {ing.note}" if ing.note else "")
        unit_name = (ing.unit or "").strip()
        unit_obj = None
        if unit_name and unit_name.lower() not in COUNT_UNITS:
            unit_obj = await mealie.find_unit(unit_name)

        entry: dict = {"referenceId": str(uuid.uuid4()), "food": None, "unit": None}
        if ing.quantity is not None and (unit_obj or not unit_name or unit_name.lower() in COUNT_UNITS):
            # Structured: scales correctly in Mealie
            entry.update(quantity=ing.quantity, unit=unit_obj, note=item)
            label = unit_obj.get("abbreviation") or unit_obj.get("name") if unit_obj else ""
            entry["display"] = " ".join(p for p in [fmt_qty(ing.quantity), label, item] if p)
        else:
            # Unit unknown to Mealie (or no quantity): keep it all as text
            text = " ".join(
                p for p in [fmt_qty(ing.quantity) if ing.quantity is not None else "", unit_name, item] if p
            )
            entry.update(quantity=None, note=text, display=text)
        entry["originalText"] = entry["display"]

        if ing.section and ing.section != last_section:
            entry["title"] = ing.section
        last_section = ing.section or last_section
        out.append(entry)
    return out


def build_steps(tm6: TM6Recipe, ingredients: list[dict]) -> list[dict]:
    steps = []
    for step in tm6.steps:
        text = step.text.strip()
        if step.tm6:
            text = f"**{settings_line(step.tm6)}**\n\n{text}"
        steps.append(
            {
                "id": str(uuid.uuid4()),
                "title": step.title or "",
                "summary": "",
                "text": text,
                "ingredientReferences": [
                    {"referenceId": ingredients[i]["referenceId"]} for i in dict.fromkeys(step.ingredients)
                ],
                "durationSeconds": step.tm6.time_seconds if step.tm6 and step.tm6.time_seconds else None,
            }
        )
    return steps


class Converter:
    def __init__(self, settings: Settings, mealie: MealieClient, llm: LLMConverter):
        self.s = settings
        self.mealie = mealie
        self.llm = llm
        self._in_flight: set[str] = set()

    def target_name(self, source: dict) -> str:
        return f"{source.get('name', '').strip()} {self.s.name_suffix}"

    async def source_link(self, source: dict) -> str:
        base = (self.s.mealie_public_url or self.s.mealie_url).rstrip("/")
        group = await self.mealie.group_slug()
        path = f"/g/{group}/r/{source['slug']}" if group else f"/recipe/{source['slug']}"
        return base + path

    async def convert_slug(self, slug: str) -> dict | None:
        """Full pipeline. Returns the written TM6 recipe, or None if skipped."""
        source = await self.mealie.get_recipe(slug)
        if is_tm6_recipe(source, self.s):
            log.info("Skipping %s: it is already a TM6 recipe", slug)
            return None
        source_id = str(source["id"])
        if source_id in self._in_flight:
            log.info("Skipping %s: conversion already running", slug)
            return None
        self._in_flight.add(source_id)
        try:
            return await self._convert(source)
        finally:
            self._in_flight.discard(source_id)

    async def _convert(self, source: dict) -> dict:
        source_id = str(source["id"])
        log.info("Converting %s (%s) with %s", source["slug"], source_id, self.s.llm_model)
        tm6 = await self.llm.convert(source)

        tag = await self.mealie.get_or_create_tag(self.s.tm6_tag)
        existing = await self.mealie.find_tm6_version(source_id, self.s.extras_key, tag["slug"])
        if existing:
            log.info("Updating existing TM6 version %s", existing["slug"])
            await self._copy_image(source, existing)
            target = await self.mealie.get_recipe(existing["slug"])  # fresh image key
        else:
            # duplicate copies the image, assets and organizers for us
            target = await self.mealie.duplicate_recipe(source["slug"], self.target_name(source))
            target = await self.mealie.get_recipe(target["slug"])
            log.info("Created %s", target["slug"])

        ingredients = await build_ingredients(tm6, self.mealie)
        unused = tm6.unused_ingredients()

        for field in COPY_FIELDS:
            if field in source:
                target[field] = source[field]
        target["name"] = self.target_name(source)
        target["tags"] = [t for t in source.get("tags") or [] if t.get("slug") != tag["slug"]] + [tag]
        target["recipeIngredient"] = ingredients
        target["recipeInstructions"] = build_steps(tm6, ingredients)
        target["extras"] = {**(source.get("extras") or {}), self.s.extras_key: source_id}
        for field, minutes in [
            ("prepTimeSeconds", tm6.prep_minutes),
            ("performTimeSeconds", tm6.cook_minutes),
            ("totalTimeSeconds", tm6.total_minutes),
        ]:
            if minutes:
                target[field] = minutes * 60
        target["notes"] = await self._notes(source, tm6, unused) + list(source.get("notes") or [])
        target.pop("comments", None)

        written = await self.mealie.update_recipe(target["slug"], target)
        log.info("Wrote %s (%d ingredients, %d steps)", written.get("slug"), len(ingredients), len(tm6.steps))
        return written

    async def _notes(self, source: dict, tm6: TM6Recipe, unused: list[int]) -> list[dict]:
        link = await self.source_link(source)
        notes = [
            {
                "title": "Thermomix conversion",
                "text": (
                    f"Source recipe: [{source.get('name')}]({link})\n\n"
                    "Converted by LLM, verify settings on first cook."
                ),
            }
        ]
        tips = list(tm6.notes)
        if unused:
            names = ", ".join(tm6.ingredients[i].item for i in unused)
            tips.append(f"Not linked to any step (check where they go): {names}")
        for step_no, item in tm6.unmentioned_ingredients():
            tips.append(f"Step {step_no} uses {item} but its text doesn't say so: check the amount")
        if tips:
            notes.append({"title": "Thermomix notes", "text": "\n".join(f"- {t}" for t in tips)})
        return notes

    async def _copy_image(self, source: dict, target: dict) -> None:
        if not source.get("image"):
            return
        try:
            data = await self.mealie.download_image(str(source["id"]))
            if data:
                await self.mealie.upload_image(target["slug"], data)
        except Exception as e:  # image is a nice-to-have
            log.warning("Could not copy image to %s: %s", target["slug"], e)
