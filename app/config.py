from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Auth for incoming webhooks: POST /convert/{WEBHOOK_SECRET}
    webhook_secret: str = Field(min_length=16)

    # Mealie API (internal URL, e.g. http://mealie:9000 on the shared docker network)
    mealie_url: str = "http://mealie:9000"
    mealie_api_token: str
    # Public base URL used for the "source recipe" link in the note.
    mealie_public_url: str | None = None

    # OpenAI-compatible LLM endpoint (OpenAI, Ollama, LiteLLM, OpenRouter, vLLM, ...)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "gpt-4.1-mini"
    # json_schema: strict structured output (OpenAI, recent Ollama/vLLM)
    # json_object: "JSON mode", schema given in the prompt only (widest support)
    # none: no response_format at all; JSON is extracted from the reply text
    llm_json_mode: Literal["json_schema", "json_object", "none"] = "json_object"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 300
    llm_max_tokens: int | None = None

    tm6_tag: str = "tm6"
    name_suffix: str = "(TM6)"
    extras_key: str = "tm6_source_id"

    # Optional automatic conversion on recipe creation (Mealie notifier -> /notify/{secret})
    auto_convert_enabled: bool = False

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
