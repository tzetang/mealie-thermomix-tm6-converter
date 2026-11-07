"""Structured output the LLM must return, validated with Pydantic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# TM6 speeds: "spoon" (gentle stir, ~40 rpm), 0.5 - 10 in 0.5 steps.
# Turbo is a mode, not a speed.
VALID_SPEEDS = ["spoon"] + [str(x / 2).removesuffix(".0") for x in range(1, 21)]

Mode = Literal[
    "kneading",
    "turbo",
    "browning",
    "slow_cook",
    "sous_vide",
    "fermentation",
    "rice_cooker",
    "kettle",
    "warm_up",
    "thicken",
    "blend",
]
Accessory = Literal["butterfly_whisk", "simmering_basket", "varoma", "spatula"]
MeasuringCup = Literal["on", "off", "simmering_basket_on_lid"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TM6Settings(_Strict):
    time_seconds: int | None = Field(None, ge=1, le=12 * 3600, description="Duration in seconds")
    temperature: int | Literal["varoma"] | None = Field(
        None, description="Degrees C (37-120, up to 160 only in browning mode) or 'varoma'"
    )
    speed: str | None = Field(None, description="'spoon' or 0.5-10 in 0.5 steps, as a string")
    reverse: bool = Field(False, description="Counter-clockwise blade rotation (gentle stirring)")
    mode: Mode | None = None
    accessory: Accessory | None = None
    measuring_cup: MeasuringCup | None = None

    @field_validator("speed", mode="before")
    @classmethod
    def _norm_speed(cls, v):
        if v is None:
            return None
        s = str(v).strip().lower().removeprefix("speed").strip()
        if s in {"soft", "stir", "gentle", "spoon speed", "coccinelle"}:
            s = "spoon"
        try:
            f = float(s)
            s = str(f).removesuffix(".0")
        except ValueError:
            pass
        if s not in VALID_SPEEDS:
            raise ValueError(f"speed must be one of {VALID_SPEEDS}, got {v!r}")
        return s

    @field_validator("accessory", mode="before")
    @classmethod
    def _drop_spatula(cls, v):
        # The spatula is used between settings, never while running: it belongs in the text.
        return None if isinstance(v, str) and v.strip().lower() == "spatula" else v

    @field_validator("temperature", mode="before")
    @classmethod
    def _norm_temp(cls, v):
        if isinstance(v, str):
            s = v.strip().lower().removesuffix("°c").removesuffix("c").strip()
            if s == "varoma":
                return "varoma"
            try:
                return int(float(s))
            except ValueError:
                raise ValueError(f"temperature must be an integer in C or 'varoma', got {v!r}")
        return v

    @model_validator(mode="after")
    def _check(self):
        t = self.temperature
        if isinstance(t, int):
            limit = 160 if self.mode == "browning" else 120
            if not 37 <= t <= limit:
                raise ValueError(
                    f"temperature {t} out of range 37-{limit} C"
                    + ("" if self.mode == "browning" else " (above 120 C needs mode 'browning')")
                )
        if self.accessory == "butterfly_whisk" and self.speed not in (None, "spoon"):
            if float(self.speed) > 4:
                raise ValueError("butterfly whisk must not be used above speed 4")
        if self.mode == "kneading" and (self.speed or self.temperature):
            raise ValueError("kneading mode takes only a time, no speed or temperature")
        if self.mode == "turbo" and (self.time_seconds or 0) > 10:
            raise ValueError("turbo pulses are at most 10 seconds")
        if self.mode is None and not any([self.time_seconds, self.temperature, self.speed]):
            raise ValueError("a Thermomix step needs at least a time, temperature, speed or mode")
        return self


class TM6Ingredient(_Strict):
    quantity: float | None = Field(None, ge=0, description="Numeric amount, grams preferred")
    unit: str | None = Field(None, description="'g', 'ml', or a count unit like 'piece', 'clove', 'pinch'")
    item: str = Field(min_length=1, description="The ingredient itself, e.g. 'onion'")
    note: str | None = Field(None, description="Prep/size hint, e.g. 'halved', 'cold, cubed'")
    section: str | None = Field(None, description="Optional group heading, e.g. 'Sauce'")


class TM6Step(_Strict):
    title: str | None = Field(None, description="Short heading, e.g. 'Chop aromatics'")
    text: str = Field(min_length=1, description="What to do, in plain language")
    ingredients: list[int] = Field(
        default_factory=list, description="0-based indexes into `ingredients` used in this step"
    )
    tm6: TM6Settings | None = Field(None, description="null for steps done outside the Thermomix")


class TM6Recipe(_Strict):
    ingredients: list[TM6Ingredient] = Field(min_length=1)
    steps: list[TM6Step] = Field(min_length=1)
    notes: list[str] = Field(
        default_factory=list, description="Tips, accessories, capacity warnings, assumptions made"
    )
    prep_minutes: int | None = Field(None, ge=0, le=7 * 24 * 60, description="Hands-on time")
    cook_minutes: int | None = Field(None, ge=0, le=7 * 24 * 60, description="Machine/oven time")
    total_minutes: int | None = Field(None, ge=0, le=7 * 24 * 60, description="Everything incl. resting")

    @model_validator(mode="after")
    def _check_refs(self):
        n = len(self.ingredients)
        bad = sorted({i for s in self.steps for i in s.ingredients if not 0 <= i < n})
        if bad:
            raise ValueError(f"steps reference ingredient indexes {bad}, valid range is 0-{n - 1}")
        return self

    def unmentioned_ingredients(self) -> list[tuple[int, str]]:
        """(step number, ingredient) pairs where a linked ingredient isn't named in the step text."""
        out = []
        for n, step in enumerate(self.steps, 1):
            text = step.text.lower()
            for i in step.ingredients:
                item = self.ingredients[i].item
                words = [w.strip(",.()").lower() for w in item.split()]
                stems = [w.rstrip("s") for w in words if len(w) >= 3] or [item.lower()]
                if not any(stem in text for stem in stems):
                    out.append((n, item))
        return out

    def unused_ingredients(self) -> list[int]:
        used = {i for s in self.steps for i in s.ingredients}
        return [i for i in range(len(self.ingredients)) if i not in used]
