"""Settings, secrets and the *global* level of model configuration.

Serves FR-1 (global default model), NFR-1 (secrets) and FR-13 (tracing under your own key).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
    set_tracing_export_api_key,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_FAST_MODEL = "gemini-2.5-flash"
DEFAULT_REASONING_MODEL = "gemini-2.5-pro"

# The SDK resolves an agent with no model through this variable at run time.
DEFAULT_MODEL_ENV = "OPENAI_DEFAULT_MODEL"


class StartupError(Exception):
    """A configuration problem explained in one sentence, shown instead of a stack trace."""


@dataclass(frozen=True)
class Settings:
    fast_model: str
    reasoning_model: str
    tracing: bool
    base_url: str = GEMINI_BASE_URL
    # repr=False keeps keys out of logs, tracebacks and debug prints (NFR-1).
    gemini_api_key: str = field(default="", repr=False)
    openai_api_key: str | None = field(default=None, repr=False)


def _is_placeholder(value: str | None) -> bool:
    return not value or value.strip() == "" or value.startswith("your-")


def load_settings(env_file: str | None = ".env") -> Settings:
    """Read settings from .env / the environment; raise StartupError on a missing key."""
    if env_file:
        load_dotenv(env_file, override=False)

    gemini_key = os.getenv("GEMINI_API_KEY")
    if _is_placeholder(gemini_key):
        raise StartupError(
            "GEMINI_API_KEY is missing: copy .env.example to .env and put your Gemini key there."
        )

    tracing = os.getenv("SHOP_DESK_TRACING", "on").strip().lower() not in {"off", "0", "false", "no"}
    openai_key = os.getenv("OPENAI_API_KEY")
    if tracing and _is_placeholder(openai_key):
        raise StartupError(
            "OPENAI_API_KEY is missing: add it to .env to export traces, "
            "or set SHOP_DESK_TRACING=off to run without tracing."
        )

    return Settings(
        fast_model=os.getenv("FAST_MODEL", DEFAULT_FAST_MODEL).strip(),
        reasoning_model=os.getenv("REASONING_MODEL", DEFAULT_REASONING_MODEL).strip(),
        tracing=tracing,
        gemini_api_key=gemini_key.strip(),
        openai_api_key=openai_key.strip() if tracing and openai_key else None,
    )


def configure_global(settings: Settings) -> None:
    """FR-1, level 1 of 3: the process-wide client, API and cheap default model.

    Must run before any agent is built: the SDK derives an agent's default ModelSettings
    from the default model name at construction time.
    """
    client = AsyncOpenAI(api_key=settings.gemini_api_key, base_url=settings.base_url)
    # Gemini's key must never be sent to the OpenAI trace exporter.
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    os.environ[DEFAULT_MODEL_ENV] = settings.fast_model  # the global default model

    if settings.tracing and settings.openai_api_key:
        set_tracing_export_api_key(settings.openai_api_key)
        set_tracing_disabled(False)
    else:
        set_tracing_disabled(True)
