"""The agents and how each one is configured (FR-1, FR-3).

`build_agents(settings)` is a factory rather than module-level globals so that tests can build
the same graph with scripted models, and so the global default is configured before any agent
exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents import Agent, ModelSettings, StopAtTools

from shop_desk.config import Settings
from shop_desk.context import ShopContext
from shop_desk.instructions import desk_instructions
from shop_desk.tools import DESK_TOOLS, FAST_PATH_TOOL


@dataclass
class ShopDeskAgents:
    desk: Agent[ShopContext]


def build_agents(settings: Settings) -> ShopDeskAgents:
    desk = Agent[ShopContext](
        name="Shop Desk",
        instructions=desk_instructions,  # FR-4: rebuilt every turn
        # FR-1: no model here on purpose -> the global default (the cheap model) answers.
        model_settings=ModelSettings(temperature=0.3, max_tokens=400),
        tools=list(DESK_TOOLS),
        # FR-3: a price lookup's own output is the final answer; the model never sees it.
        tool_use_behavior=StopAtTools(stop_at_tool_names=[FAST_PATH_TOOL]),
    )
    return ShopDeskAgents(desk=desk)
