"""One customer conversation: `DeskSession.ask()` runs one customer turn.

Keeps the conversation history and the ShopContext together, so two sessions never share a
basket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from openai import APIError

from agents import AgentsException, ModelProvider, RunConfig, Runner, TResponseInputItem

from shop_desk.config import Settings
from shop_desk.context import ShopContext
from shop_desk.desk_agents import ShopDeskAgents

log = logging.getLogger(__name__)

TurnKind = Literal["fast-path", "reasoning", "refused", "ended"]


@dataclass
class Reply:
    text: str
    kind: TurnKind
    model_calls: int = 0


class DeskSession:
    def __init__(
        self,
        agents: ShopDeskAgents,
        context: ShopContext,
        settings: Settings,
        *,
        model_provider: ModelProvider | None = None,
    ) -> None:
        self.agents = agents
        self.context = context
        self.settings = settings
        self.model_provider = model_provider  # tests inject scripted models here
        self.history: list[TResponseInputItem] = []
        self.turn = 0

    def _run_config(self, **overrides) -> RunConfig:
        if self.model_provider is not None:
            overrides.setdefault("model_provider", self.model_provider)
        return RunConfig(workflow_name="Shop Desk conversation", **overrides)

    async def ask(self, text: str) -> Reply:
        self.turn += 1
        self.context.start_run()
        run_input = [*self.history, {"role": "user", "content": text}]
        try:
            result = await Runner.run(
                self.agents.desk, run_input, context=self.context, run_config=self._run_config()
            )
        except (AgentsException, APIError) as exc:
            log.warning("turn %s failed: %s", self.turn, type(exc).__name__)
            return Reply(
                "Sorry, I'm having trouble reaching our system right now. Please try again in a moment.",
                kind="refused",
            )
        self.history = result.to_input_list()
        kind: TurnKind = "fast-path" if self.context.fast_path_used else "reasoning"
        return Reply(str(result.final_output), kind=kind, model_calls=result.context_wrapper.usage.requests)
