import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .converter import Converter
from .llm import LLMConverter
from .mealie import MealieClient, parse_notifier_body

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tm6")


@asynccontextmanager
async def lifespan(app: FastAPI):
    mealie = MealieClient(settings.mealie_url, settings.mealie_api_token)
    app.state.converter = Converter(settings, mealie, LLMConverter(settings))
    app.state.tasks = set()
    yield
    await mealie.aclose()


app = FastAPI(title="Mealie TM6 converter", lifespan=lifespan, docs_url=None, redoc_url=None)


def check_secret(secret: str) -> None:
    if not hmac.compare_digest(secret.encode(), settings.webhook_secret.encode()):
        raise HTTPException(status_code=404)


def schedule(request: Request, slug: str) -> None:
    """Run the conversion detached from the request so the webhook returns at once."""

    async def run():
        try:
            await request.app.state.converter.convert_slug(slug)
        except Exception:
            log.exception("Conversion of %s failed", slug)

    task = asyncio.create_task(run())
    request.app.state.tasks.add(task)  # keep a reference so it isn't garbage collected
    task.add_done_callback(request.app.state.tasks.discard)


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/convert/{secret}", status_code=202)
async def convert(secret: str, request: Request):
    """Mealie Recipe Action (type 'post'). Body: {action, content: <recipe>, recipe_scale}.

    Also accepts {"slug": "..."} for manual testing with curl.
    """
    check_secret(secret)
    body = await json_body(request)
    content = body.get("content") if isinstance(body.get("content"), dict) else {}
    slug = content.get("slug") or body.get("slug")
    if not slug:
        raise HTTPException(status_code=422, detail="No recipe slug in payload")
    schedule(request, slug)
    log.info("Queued conversion of %s", slug)
    return {"status": "queued", "slug": slug}


@app.post("/notify/{secret}", status_code=202)
async def notify(secret: str, request: Request):
    """Mealie notifier (Apprise json://) target. Converts on recipe creation if enabled."""
    check_secret(secret)
    if not settings.auto_convert_enabled:
        raise HTTPException(status_code=404)
    body = await json_body(request)
    event_type, slug = parse_notifier_body(body)
    if event_type != "recipe_created" or not slug:
        return JSONResponse({"status": "ignored", "event_type": event_type}, status_code=200)
    schedule(request, slug)
    log.info("Queued auto-conversion of %s", slug)
    return {"status": "queued", "slug": slug}
