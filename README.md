# Mealie → Thermomix TM6 converter

A small FastAPI service. When it gets a Mealie recipe, it asks an OpenAI-compatible LLM for a TM6 manual-mode version as validated JSON. It then writes the result back to Mealie as `<name> (TM6)`.

## Flow

1. The Mealie Recipe Action "Convert to TM6" POSTs the recipe to `/convert/<WEBHOOK_SECRET>`. The service replies `202` straight away and does the work in the background.
2. It re-fetches the recipe from the Mealie API by slug and skips it if any of these hold:
   - it is tagged `tm6`
   - its name ends in `(TM6)`
   - it has `extras.tm6_source_id`
   - a conversion for it is already running
3. LLM call → `TM6Recipe` Pydantic model. Validation covers:
   - speeds: spoon, or 0.5–10
   - temperatures: 37–120 °C, up to 160 °C only in browning mode, or Varoma
   - butterfly whisk at speed 4 or below
   - turbo pulses of 10 s or less
   - step → ingredient indexes that point at real ingredients

   If validation fails, it retries once with the error text. If that fails too, it logs the error and stops.
4. It looks for an existing TM6 version with `extras.tm6_source_id = <source recipe id>`. This is the recipe UUID, so it still matches after you rename the original.
   - **Found:** updates that recipe in place and re-copies the image.
   - **Not found:** uses Mealie's `duplicate` endpoint, which copies the image, assets, categories and tools, then overwrites the content.
5. What gets written:
   - tags: `tm6` plus the original's tags
   - categories, yield and times from the original
   - ingredients in Thermomix order. Grams are mapped to your Mealie `gram` unit, so scaling works.
   - steps with a bold settings line, e.g. **3 min / 120 °C / speed 1 / MC off**. Each step has `durationSeconds` for the timer and links to the ingredients it uses.
   - a "Thermomix conversion" note with the source link and "Converted by LLM, verify settings on first cook"
   - an LLM tips note
   - the original recipe's notes

## Setup

```bash
cp .env.example .env    # fill in WEBHOOK_SECRET, MEALIE_API_TOKEN, LLM_*
docker compose up -d --build
docker logs -f tm6-converter
```

**Mealie needs one env change.** Mealie blocks outbound requests to private addresses. In the Portainer `mealie` stack, add the following to the `mealie` service and redeploy:

```yaml
      HTTP_ALLOW_LIST: tm6-converter
```

**Recipe Action:** go to Mealie → Household settings → Recipe Actions → Create:
- Title: `Convert to TM6`
- Type: `post`
- URL: `http://tm6-converter:8000/convert/<WEBHOOK_SECRET>`

The action then appears in a recipe's ⋮ menu. The TM6 version shows up about a minute later.

**Testing without Mealie's button:**

```bash
docker exec tm6-converter python -c "import urllib.request,os,json; print(urllib.request.urlopen(urllib.request.Request('http://localhost:8000/convert/'+os.environ['WEBHOOK_SECRET'], data=json.dumps({'slug':'YOUR-RECIPE-SLUG'}).encode(), headers={'Content-Type':'application/json'})).read())"
```

### Optional: auto-convert new recipes

1. Set `AUTO_CONVERT_ENABLED=true`. While it's off, `/notify/...` returns 404.
2. Add a notifier: Mealie → Household settings → Notifiers. Use the Apprise URL `json://tm6-converter:8000/notify/<WEBHOOK_SECRET>` and tick only **Recipe Created**.

The `(TM6)` recipe that the converter creates also fires "recipe created". The name and tag guards ignore it.

## LLM settings

| Var | Notes |
|---|---|
| `LLM_BASE_URL` | Any `/v1` OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1`, `http://ollama:11434/v1`, `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | Model name as that endpoint expects it |
| `LLM_JSON_MODE` | `json_object` (default, widely supported), `json_schema` (structured outputs), or `none` (JSON is pulled from the reply text) |

Small local models struggle with this task. A mid-size or larger model gives much better TM6 settings.

## Dev

```bash
docker run --rm -v "$PWD/tests:/srv/tests:ro" -u root mealie-tm6-converter:local \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q tests"
```

## Known limits

- `recipe_scale` from the action is ignored. The base recipe is converted, and you can scale in Mealie.
- Ingredients are linked to Mealie units but not to foods. Text the converter can't map is kept as an ingredient note.
- Each run fully overwrites the TM6 version's content, so hand edits to it are lost when you convert again.
