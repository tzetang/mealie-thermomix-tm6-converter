import json
import os
import uuid

os.environ.setdefault("WEBHOOK_SECRET", "s" * 32)
os.environ.setdefault("MEALIE_API_TOKEN", "tok")

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.converter import Converter, build_steps, is_tm6_recipe, settings_line
from app.llm import ConversionError, LLMConverter, extract_json
from app.mealie import MealieClient, parse_notifier_body
from app.models import TM6Recipe, TM6Settings

S = Settings(webhook_secret="s" * 32, mealie_api_token="tok", mealie_public_url="https://m.example")

GOOD = {
    "ingredients": [
        {"quantity": 1, "unit": None, "item": "onion", "note": "halved"},
        {"quantity": 2, "unit": "clove", "item": "garlic"},
        {"quantity": 20, "unit": "g", "item": "olive oil"},
        {"quantity": 500, "unit": "g", "item": "passata", "section": "Sauce"},
    ],
    "steps": [
        {"title": "Chop", "text": "Chop 1 onion and 2 garlic cloves, scrape down.", "ingredients": [0, 1], "tm6": {"time_seconds": 5, "speed": 5}},
        {"text": "Saute in 20 g olive oil.", "ingredients": [2], "tm6": {"time_seconds": 180, "temperature": 120, "speed": "1", "measuring_cup": "off"}},
        {"text": "Add 500 g passata and simmer.", "ingredients": [3], "tm6": {"time_seconds": 900, "temperature": 100, "speed": "spoon", "reverse": True}},
        {"text": "Serve.", "tm6": None},
    ],
    "notes": ["Halve for small bowls"],
    "prep_minutes": 10,
    "total_minutes": 30,
}


# ---------- models ----------

def test_good_recipe_validates():
    r = TM6Recipe.model_validate(GOOD)
    assert r.steps[0].tm6.speed == "5"
    assert r.unused_ingredients() == []


@pytest.mark.parametrize(
    "tm6,msg",
    [
        ({"speed": "11"}, "speed must be"),
        ({"temperature": 140, "speed": "1"}, "browning"),
        ({"speed": "6", "accessory": "butterfly_whisk"}, "butterfly"),
        ({"mode": "kneading", "time_seconds": 60, "speed": "4"}, "kneading"),
        ({"mode": "turbo", "time_seconds": 30}, "turbo"),
        ({}, "needs at least"),
    ],
)
def test_bad_settings(tm6, msg):
    with pytest.raises(ValidationError, match=msg):
        TM6Settings.model_validate(tm6)


def test_settings_normalisation():
    t = TM6Settings.model_validate({"speed": "Speed 3.0", "temperature": "Varoma"})
    assert (t.speed, t.temperature) == ("3", "varoma")
    assert TM6Settings.model_validate({"mode": "browning", "temperature": 160, "time_seconds": 60}).temperature == 160


def test_bad_ingredient_index():
    bad = json.loads(json.dumps(GOOD))
    bad["steps"][0]["ingredients"] = [0, 9]
    with pytest.raises(ValidationError, match=r"indexes \[9\]"):
        TM6Recipe.model_validate(bad)


def test_settings_line():
    t = TM6Settings(time_seconds=900, temperature=100, speed="spoon", reverse=True, measuring_cup="off")
    assert settings_line(t) == "15 min / 100 °C / ↺ reverse / speed spoon / measuring cup out"


def test_settings_line_no_duplicate_basket():
    t = TM6Settings(time_seconds=60, temperature=120, speed="1", measuring_cup="simmering_basket_on_lid", accessory="simmering_basket")
    assert settings_line(t) == "1 min / 120 °C / speed 1 / simmering basket on lid"


def test_spatula_is_not_a_setting():
    assert TM6Settings.model_validate({"time_seconds": 5, "speed": "5", "accessory": "spatula"}).accessory is None


def test_example_is_valid_and_clean():
    from app.example import EXAMPLE
    from app.prompt import build_messages

    r = TM6Recipe.model_validate(EXAMPLE)
    assert r.unmentioned_ingredients() == [] and r.unused_ingredients() == []
    assert "braised beef" in build_messages(SOURCE, "json_object")[1]["content"]


def test_unmentioned_ingredient_detected():
    bad = json.loads(json.dumps(GOOD))
    bad["steps"][2]["text"] = "Add the sauce and simmer."
    assert TM6Recipe.model_validate(bad).unmentioned_ingredients() == [(3, "passata")]


def test_prompt_uses_seconds_fields():
    from app.prompt import recipe_to_prompt

    assert "Total time: 25 min" in recipe_to_prompt({**SOURCE, "totalTimeSeconds": 1500})


def test_extract_json():
    assert json.loads(extract_json('Sure!\n```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(extract_json('blah {"a": {"b": 2}} blah')) == {"a": {"b": 2}}


def test_notifier_body():
    body = {"title": "x", "event_type": "recipe_created", "document_data": json.dumps({"recipe_slug": "soup"})}
    assert parse_notifier_body(body) == ("recipe_created", "soup")


def test_guards():
    assert is_tm6_recipe({"name": "Soup (TM6)"}, S)
    assert is_tm6_recipe({"name": "Soup", "tags": [{"name": "TM6", "slug": "tm6"}]}, S)
    assert is_tm6_recipe({"name": "Soup", "extras": {"tm6_source_id": "x"}}, S)
    assert not is_tm6_recipe({"name": "Soup", "tags": [{"name": "Dinner", "slug": "dinner"}]}, S)


# ---------- LLM retry ----------

class FakeLLMClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = self
        self.completions = self

    async def create(self, **kw):
        self.calls.append(kw)
        content = self.replies.pop(0)
        msg = type("M", (), {"content": content})
        choice = type("C", (), {"message": msg, "finish_reason": "stop"})
        return type("R", (), {"choices": [choice]})


SOURCE = {
    "id": str(uuid.uuid4()),
    "slug": "tomato-sauce",
    "name": "Tomato Sauce",
    "image": "abc",
    "description": "Nice",
    "recipeYield": "4 servings",
    "tags": [{"id": "t1", "name": "Dinner", "slug": "dinner"}],
    "recipeCategory": [{"id": "c1", "name": "Sauces", "slug": "sauces"}],
    "recipeIngredient": [{"display": "1 onion"}, {"display": "2 cloves garlic"}, {"display": "1.5 tbsp olive oil"}, {"display": "2 cups passata"}],
    "recipeInstructions": [{"text": "Chop onion and garlic, fry in oil, add passata and simmer."}],
    "notes": [{"title": "Orig", "text": "from grandma"}],
    "extras": {},
}


@pytest.mark.asyncio
async def test_llm_retries_once_with_error():
    bad = json.loads(json.dumps(GOOD))
    bad["steps"][0]["tm6"]["speed"] = "12"
    fake = FakeLLMClient([json.dumps(bad), json.dumps(GOOD)])
    r = await LLMConverter(S, client=fake).convert(SOURCE)
    assert len(r.steps) == 4
    assert len(fake.calls) == 2
    assert "failed validation" in fake.calls[1]["messages"][-1]["content"]
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_llm_gives_up_after_two():
    fake = FakeLLMClient(["not json", "still not"])
    with pytest.raises(ConversionError):
        await LLMConverter(S, client=fake).convert(SOURCE)


# ---------- end to end against a fake Mealie ----------

class FakeMealie:
    def __init__(self):
        self.recipes = {SOURCE["slug"]: json.loads(json.dumps(SOURCE))}
        self.tags = {}
        self.puts = []
        self.images = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        if p == "/api/groups/self":
            return httpx.Response(200, json={"slug": "home"})
        if p == "/api/units":
            return httpx.Response(200, json={"items": [{"id": "u1", "name": "gram", "abbreviation": "g"}, {"id": "u2", "name": "clove", "pluralName": "cloves"}]})
        if p.startswith("/api/organizers/tags/slug/"):
            slug = p.rsplit("/", 1)[1]
            return httpx.Response(200, json=self.tags[slug]) if slug in self.tags else httpx.Response(404)
        if p == "/api/organizers/tags" and m == "GET":
            return httpx.Response(200, json={"items": list(self.tags.values())})
        if p == "/api/organizers/tags" and m == "POST":
            name = json.loads(req.content)["name"]
            self.tags[name] = {"id": "tag-tm6", "name": name, "slug": name}
            return httpx.Response(201, json=self.tags[name])
        if p == "/api/recipes" and m == "GET":
            val = req.url.params.get("queryFilter", "").split('"')[1]
            items = [r for r in self.recipes.values() if val in (r.get("extras") or {}).values()]
            return httpx.Response(200, json={"items": items})
        if p.endswith("/duplicate"):
            src = self.recipes[p.split("/")[3]]
            name = json.loads(req.content)["name"]
            new = {**json.loads(json.dumps(src)), "id": str(uuid.uuid4()), "name": name, "slug": "tomato-sauce-tm6"}
            self.recipes[new["slug"]] = new
            return httpx.Response(201, json=new)
        if p.startswith("/api/media/"):
            return httpx.Response(200, content=b"img")
        if p.endswith("/image") and m == "PUT":
            self.images.append(p)
            return httpx.Response(200, json={})
        if p.startswith("/api/recipes/"):
            slug = p.split("/")[3]
            if m == "GET":
                return httpx.Response(200, json=self.recipes[slug])
            if m == "PUT":
                data = json.loads(req.content)
                self.puts.append(data)
                self.recipes[slug] = data
                return httpx.Response(200, json=data)
        return httpx.Response(500, json={"unhandled": p})


@pytest.mark.asyncio
async def test_end_to_end_create_then_update():
    fake = FakeMealie()
    mealie = MealieClient("http://mealie", "tok", transport=httpx.MockTransport(fake.handler))
    llm = LLMConverter(S, client=FakeLLMClient([json.dumps(GOOD), json.dumps(GOOD)]))
    conv = Converter(S, mealie, llm)

    out = await conv.convert_slug("tomato-sauce")
    assert out["name"] == "Tomato Sauce (TM6)"
    assert [t["slug"] for t in out["tags"]] == ["dinner", "tm6"]
    assert out["recipeCategory"][0]["slug"] == "sauces"
    assert out["extras"]["tm6_source_id"] == SOURCE["id"]

    ings = out["recipeIngredient"]
    assert ings[0] == {**ings[0], "quantity": 1, "unit": None, "note": "onion, halved"}
    assert ings[1]["unit"]["id"] == "u2"
    assert ings[2]["unit"]["id"] == "u1" and ings[2]["display"] == "20 g olive oil"
    assert ings[3]["title"] == "Sauce"

    steps = out["recipeInstructions"]
    assert steps[0]["text"].startswith("**5 sec / speed 5**")
    assert [r["referenceId"] for r in steps[0]["ingredientReferences"]] == [ings[0]["referenceId"], ings[1]["referenceId"]]
    assert steps[2]["durationSeconds"] == 900
    assert steps[3]["text"] == "Serve." and steps[3]["durationSeconds"] is None

    assert out["notes"][0]["title"] == "Thermomix conversion"
    assert "https://m.example/g/home/r/tomato-sauce" in out["notes"][0]["text"]
    assert "verify settings on first cook" in out["notes"][0]["text"]
    assert out["notes"][-1] == {"title": "Orig", "text": "from grandma"}
    assert out["prepTimeSeconds"] == 600 and out["totalTimeSeconds"] == 1800
    assert not any("doesn't say so" in n["text"] for n in out["notes"])

    # second run updates the same recipe instead of duplicating
    n = len(fake.recipes)
    out2 = await conv.convert_slug("tomato-sauce")
    assert len(fake.recipes) == n
    assert out2["slug"] == "tomato-sauce-tm6"
    assert fake.images == ["/api/recipes/tomato-sauce-tm6/image"]

    # converting the TM6 version itself is a no-op
    assert await conv.convert_slug("tomato-sauce-tm6") is None
    await mealie.aclose()


# ---------- HTTP layer ----------

def test_http_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    queued = []
    monkeypatch.setattr(main, "schedule", lambda req, slug: queued.append(slug))
    with TestClient(main.app) as c:
        assert c.post("/convert/wrong", json={"content": {"slug": "x"}}).status_code == 404
        r = c.post(f"/convert/{'s' * 32}", json={"action": {}, "content": {"slug": "soup"}, "recipe_scale": 1})
        assert r.status_code == 202 and queued == ["soup"]
        assert c.post(f"/convert/{'s' * 32}", json={"content": {}}).status_code == 422
        # notifier is off by default
        assert c.post(f"/notify/{'s' * 32}", json={}).status_code == 404
        monkeypatch.setattr(main.settings, "auto_convert_enabled", True)
        body = {"event_type": "recipe_created", "document_data": json.dumps({"recipe_slug": "pie"})}
        assert c.post(f"/notify/{'s' * 32}", json=body).status_code == 202
        assert queued == ["soup", "pie"]
