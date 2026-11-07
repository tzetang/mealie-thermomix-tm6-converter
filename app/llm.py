import json
import logging
import re

from openai import AsyncOpenAI
from pydantic import ValidationError

from .config import Settings
from .models import TM6Recipe
from .prompt import build_messages

log = logging.getLogger(__name__)


class ConversionError(Exception):
    pass


def extract_json(text: str) -> str:
    """Pull a JSON object out of a reply that may include fences or chatter."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class LLMConverter:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.s = settings
        self.client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )

    def _response_format(self) -> dict | None:
        match self.s.llm_json_mode:
            case "json_schema":
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "tm6_recipe",
                        "schema": TM6Recipe.model_json_schema(),
                        # strict mode forbids optional fields; we validate ourselves instead
                        "strict": False,
                    },
                }
            case "json_object":
                return {"type": "json_object"}
            case _:
                return None

    async def _complete(self, messages: list[dict]) -> str:
        kwargs: dict = {
            "model": self.s.llm_model,
            "messages": messages,
            "temperature": self.s.llm_temperature,
        }
        if (rf := self._response_format()) is not None:
            kwargs["response_format"] = rf
        if self.s.llm_max_tokens:
            kwargs["max_tokens"] = self.s.llm_max_tokens
        resp = await self.client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        if resp.choices[0].finish_reason == "length":
            log.warning("LLM reply was cut off at max tokens; validation will likely fail")
        return content

    async def convert(self, recipe: dict) -> TM6Recipe:
        messages = build_messages(recipe, self.s.llm_json_mode)
        last_error: str | None = None
        for attempt in (1, 2):
            reply = await self._complete(messages)
            try:
                return TM6Recipe.model_validate_json(extract_json(reply))
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_error = str(e)
                log.warning("LLM output invalid (attempt %d): %s", attempt, last_error[:2000])
                messages = messages + [
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            "Your JSON failed validation with these errors:\n"
                            f"{last_error}\n\n"
                            "Fix them and return the complete corrected JSON object only."
                        ),
                    },
                ]
        raise ConversionError(f"LLM output failed validation twice: {last_error}")
