import json
import logging

import httpx

log = logging.getLogger(__name__)


class MealieClient:
    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            transport=transport,
        )
        self._units: list[dict] | None = None

    async def aclose(self):
        await self.http.aclose()

    async def _req(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = await self.http.request(method, url, **kw)
        except httpx.TransportError as e:
            # Mealie sometimes drops a pooled keep-alive connection; retry once on a fresh one.
            log.warning("Mealie %s %s: %r, retrying once", method, url, e)
            r = await self.http.request(method, url, **kw)
        if r.is_error:
            log.error("Mealie %s %s -> %s: %s", method, url, r.status_code, r.text[:500])
        r.raise_for_status()
        return r

    async def get_recipe(self, slug_or_id: str) -> dict:
        return (await self._req("GET", f"/api/recipes/{slug_or_id}")).json()

    async def update_recipe(self, slug: str, data: dict) -> dict:
        return (await self._req("PUT", f"/api/recipes/{slug}", json=data)).json()

    async def duplicate_recipe(self, slug: str, new_name: str) -> dict:
        """Copies the recipe incl. image, tags, categories, tools, yields and assets."""
        return (await self._req("POST", f"/api/recipes/{slug}/duplicate", json={"name": new_name})).json()

    async def get_or_create_tag(self, name: str) -> dict:
        # Not /tags/slug/{slug}: Mealie v3.30 returns 500 (not 404) for a missing tag there.
        wanted = name.strip().lower()
        r = await self._req("GET", "/api/organizers/tags", params={"perPage": -1})
        for tag in r.json().get("items", []):
            if wanted in ((tag.get("slug") or "").lower(), (tag.get("name") or "").lower()):
                return tag
        return (await self._req("POST", "/api/organizers/tags", json={"name": name})).json()

    async def group_slug(self) -> str | None:
        try:
            return (await self._req("GET", "/api/groups/self")).json().get("slug")
        except httpx.HTTPError:
            return None

    async def units(self) -> list[dict]:
        if self._units is None:
            r = await self._req("GET", "/api/units", params={"perPage": -1})
            self._units = r.json().get("items", [])
        return self._units

    async def find_unit(self, name: str) -> dict | None:
        wanted = _unit_aliases(name)
        for unit in await self.units():
            names = {
                (unit.get(k) or "").strip().lower()
                for k in ("name", "pluralName", "abbreviation", "pluralAbbreviation")
            }
            names |= {(a.get("name") or "").strip().lower() for a in unit.get("aliases") or []}
            names.discard("")
            if names & wanted:
                return unit
        return None

    async def find_tm6_version(self, source_id: str, extras_key: str, tag_slug: str) -> dict | None:
        """Find the TM6 recipe whose extras[extras_key] == source_id."""
        candidates: list[dict] = []
        try:
            r = await self._req(
                "GET",
                "/api/recipes",
                params={"queryFilter": f'extras.value = "{source_id}"', "perPage": 50},
            )
            candidates = r.json().get("items", [])
        except httpx.HTTPStatusError:
            log.info("extras queryFilter not supported, scanning recipes tagged %s", tag_slug)
            r = await self._req("GET", "/api/recipes", params={"tags": tag_slug, "perPage": -1})
            candidates = r.json().get("items", [])

        for summary in candidates:
            full = await self.get_recipe(summary["slug"])
            if (full.get("extras") or {}).get(extras_key) == source_id:
                return full
        return None

    async def download_image(self, recipe_id: str) -> bytes | None:
        r = await self.http.get(f"/api/media/recipes/{recipe_id}/images/original.webp")
        return r.content if r.status_code == 200 and r.content else None

    async def upload_image(self, slug: str, data: bytes, extension: str = "webp") -> None:
        await self._req(
            "PUT",
            f"/api/recipes/{slug}/image",
            files={"image": (f"image.{extension}", data, f"image/{extension}")},
            data={"extension": extension},
        )


_UNIT_SYNONYMS = [
    {"g", "gram", "grams", "gr", "gramm"},
    {"kg", "kilogram", "kilograms"},
    {"ml", "milliliter", "milliliters", "millilitre", "millilitres"},
    {"l", "liter", "liters", "litre", "litres"},
    {"tsp", "teaspoon", "teaspoons"},
    {"tbsp", "tablespoon", "tablespoons"},
    {"pinch", "pinches"},
    {"clove", "cloves"},
    {"piece", "pieces", "pc", "pcs"},
]


def _unit_aliases(name: str) -> set[str]:
    n = name.strip().lower().rstrip(".")
    for group in _UNIT_SYNONYMS:
        if n in group:
            return set(group)
    return {n, n.rstrip("s"), n + "s"}


def parse_notifier_body(body: dict) -> tuple[str | None, str | None]:
    """Mealie's Apprise json:// notifier puts event data into custom keys.

    Returns (event_type, recipe_slug).
    """
    event_type = body.get("event_type")
    doc = body.get("document_data")
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError:
            doc = None
    slug = doc.get("recipe_slug") if isinstance(doc, dict) else None
    return event_type, slug
