"""The agents and how each one is configured (FR-1, FR-3, FR-5, FR-6, FR-8, FR-9).

`build_agents(settings)` is a factory rather than module-level globals so that tests can build
the same graph with scripted models, and so the global default is configured before any agent
exists.

Graph: Desk --tool--> Pricing specialist · Desk --handoff--> Order clerk · Desk --handoff--> Human escalation

Model levels (FR-1):
  global  -> config.configure_global()         Desk, specialist base and its clones (no model=)
  agent   -> Order clerk: model=reasoning_model  (here)
  run     -> DeskSession._requote(): RunConfig(model=reasoning_model)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents import Agent, ModelSettings, RunContextWrapper, RunResult, StopAtTools, handoff

from shop_desk.config import Settings
from shop_desk.context import ShopContext
from shop_desk.cost import PricingHooks
from shop_desk.guardrails import catalogue_guardrail
from shop_desk.handoff_filters import escalation_filter
from shop_desk.instructions import desk_instructions, escalation_instructions
from shop_desk.schemas import EscalationReason, Order
from shop_desk.tools import DESK_TOOLS, FAST_PATH_TOOL, SPECIALIST_TOOLS, view_basket

ORDER_CLERK_INSTRUCTIONS = """You are the order clerk. The customer has confirmed they want to order what is in their basket.
1. Call view_basket exactly once.
2. Return an Order:
   - order_id: the "Draft order id" shown by view_basket
   - status: "confirmed" if the basket has at least one valid line, otherwise "draft"
   - items: one LineItem per basket line, with sku, qty and unit_price exactly as view_basket shows them
   - total: the sum of qty × unit_price over the items
Never add, drop or re-price items. Never invent an order id."""

BASE_INSTRUCTIONS = "You are a Shop Desk specialist. Use only catalogue tools for any figure."

PRICING_INSTRUCTIONS = """You are the pricing specialist. You receive a request such as "3 x KTL-01 and 2 x IRN-05".
Call the quote tool once with those lines (use search_catalogue first only if you were given names, not SKUs).
Reply with ONLY the TOTAL number from the quote tool, digits only: no currency, no words, no commas."""


async def extract_figure(result: RunResult) -> str:
    """FR-8: the specialist answers with a figure, not a paragraph."""
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(result.final_output or ""))
    if not match:
        return "The pricing specialist could not produce a figure; use view_basket instead."
    return match.group().replace(",", "")


@dataclass
class ShopDeskAgents:
    desk: Agent[ShopContext]
    order_clerk: Agent[ShopContext]
    specialist_base: Agent[ShopContext]
    pricing: Agent[ShopContext]
    escalation: Agent[ShopContext]


def build_agents(settings: Settings) -> ShopDeskAgents:
    guardrails = [catalogue_guardrail]  # FR-6: every customer-facing agent

    order_clerk = Agent[ShopContext](
        name="Order clerk",
        instructions=ORDER_CLERK_INSTRUCTIONS,
        model=settings.reasoning_model,  # FR-1 agent level: the one agent that must reason
        model_settings=ModelSettings(temperature=0),
        tools=[view_basket],
        output_type=Order,  # FR-5: a confirmed order is a structure, not prose
        output_guardrails=guardrails,
    )

    # FR-9: one base, two clones that differ only in instructions and model settings.
    specialist_base = Agent[ShopContext](
        name="Specialist base",
        instructions=BASE_INSTRUCTIONS,
        model_settings=ModelSettings(temperature=0.2),
        tools=list(SPECIALIST_TOOLS),
        output_guardrails=guardrails,
    )
    pricing = specialist_base.clone(
        name="Pricing specialist",
        instructions=PRICING_INSTRUCTIONS,
        model_settings=ModelSettings(temperature=0, max_tokens=60),
    )
    escalation = specialist_base.clone(
        name="Human escalation",
        instructions=escalation_instructions,  # FR-10: reads the typed reason from context
        model_settings=ModelSettings(temperature=0.4, max_tokens=350),
    )
    # FR-11: agent-level hooks on the Pricing specialist only. Set after cloning so the base
    # and the escalation clone stay hook-free.
    pricing.hooks = PricingHooks()

    async def on_escalate(ctx: RunContextWrapper[ShopContext], reason: EscalationReason) -> None:
        ctx.context.escalation = reason

    desk = Agent[ShopContext](
        name="Shop Desk",
        instructions=desk_instructions,  # FR-4: rebuilt every turn
        # FR-1: no model here on purpose -> the global default (the cheap model) answers.
        model_settings=ModelSettings(temperature=0.3, max_tokens=400),
        tools=[
            *DESK_TOOLS,
            pricing.as_tool(  # FR-8: agent as tool, returns a number
                tool_name="pricing_specialist",
                tool_description=(
                    "Exact total for several items or quantities, e.g. '3 x KTL-01 and 2 x IRN-05'. "
                    "Returns only the number in PKR; put it in your own sentence."
                ),
                custom_output_extractor=extract_figure,
            ),
        ],
        handoffs=[
            handoff(
                order_clerk,
                tool_description_override=(
                    "Transfer when the customer clearly confirms they want to place the order for "
                    "what is in their basket."
                ),
            ),
            handoff(  # FR-10: typed reason + trimmed history
                escalation,
                tool_name_override="escalate_to_human",
                tool_description_override=(
                    "Hand the customer to a member of staff when you cannot help: bargaining or "
                    "discount demands, complaints, an unavailable item they insist on, a request "
                    "for a human, or when you are stuck. Give the reason and a short note."
                ),
                on_handoff=on_escalate,
                input_type=EscalationReason,
                input_filter=escalation_filter,
            ),
        ],
        output_guardrails=guardrails,
        # FR-3: a price lookup's own output is the final answer; the model never sees it.
        tool_use_behavior=StopAtTools(stop_at_tool_names=[FAST_PATH_TOOL]),
    )
    return ShopDeskAgents(
        desk=desk,
        order_clerk=order_clerk,
        specialist_base=specialist_base,
        pricing=pricing,
        escalation=escalation,
    )
