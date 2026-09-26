"""Offline test harness: scripted models by name, a private catalogue copy, no API keys."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agents import Model, ModelProvider, Usage, set_tracing_disabled
from agents.testing import ScriptedModel

from shop_desk.catalogue import CATALOGUE_PATH, load_catalogue
from shop_desk.config import DEFAULT_MODEL_ENV, Settings, configure_global
from shop_desk.context import ShopContext
from shop_desk.desk_agents import build_agents
from shop_desk.session import DeskSession

FAST = "fast-model"
REASONING = "reasoning-model"


class ScriptedProvider(ModelProvider):
    """Returns one ScriptedModel per resolved model name and records who asked for what."""

    def __init__(self) -> None:
        self.models: dict[str, ScriptedModel] = {}
        self.requested: list[str | None] = []
        # every scripted call reports real-looking usage, so cost numbers are testable
        self.usage = Usage(requests=1, input_tokens=100, output_tokens=10, total_tokens=110)

    def _model(self, name: str) -> ScriptedModel:
        if name not in self.models:
            self.models[name] = ScriptedModel()
            self.models[name].set_default_usage(self.usage)
        return self.models[name]

    def script(self, name: str, steps: list) -> ScriptedModel:
        model = self._model(name)
        model.extend(steps)
        return model

    def get_model(self, model_name: str | None) -> Model:
        self.requested.append(model_name)
        resolved = model_name or os.environ[DEFAULT_MODEL_ENV]  # same rule the SDK uses
        return self._model(resolved)

    def calls(self, name: str) -> int:
        return len(self.models[name].calls) if name in self.models else 0


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv(DEFAULT_MODEL_ENV, "placeholder")  # restored after the test
    s = Settings(fast_model=FAST, reasoning_model=REASONING, tracing=False, gemini_api_key="test-key")
    configure_global(s)  # the real global level, pointed at the fake names
    set_tracing_disabled(True)
    return s


@pytest.fixture
def catalogue_file(tmp_path) -> Path:
    path = tmp_path / "catalogue.json"
    shutil.copy(CATALOGUE_PATH, path)
    return path


@pytest.fixture
def make_context(catalogue_file):
    def _make(tier: str = "walk_in", customer_id: str = "CUST-7781", **kwargs) -> ShopContext:
        # a caller may point the context at another catalogue (XR-4); everything else about the
        # shop is read from whichever file is in force.
        path = kwargs.pop("catalogue_path", catalogue_file)
        catalogue = load_catalogue(path)
        return ShopContext(
            shop=catalogue.shop,
            currency=catalogue.currency,
            customer_id=customer_id,
            tier=tier,
            catalogue_path=path,
            **kwargs,
        )

    return _make


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def make_session(settings, make_context, provider):
    def _make(tier: str = "walk_in", **kwargs) -> DeskSession:
        return DeskSession(build_agents(settings), make_context(tier, **kwargs), settings, model_provider=provider)

    return _make


async def invoke(tool, context: ShopContext, **arguments) -> str:
    """Call a FunctionTool exactly as the runner would, without a model."""
    import json

    from agents.tool_context import ToolContext

    args = json.dumps(arguments)
    tool_ctx = ToolContext(context=context, tool_name=tool.name, tool_call_id="test-call", tool_arguments=args)
    return await tool.on_invoke_tool(tool_ctx, args)
