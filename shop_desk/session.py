"""One customer conversation: `DeskSession.ask()` runs one customer turn.

Keeps the conversation history and the ShopContext together, so two sessions never share a
basket. Every run-level failure becomes a polite sentence here (NFR-4). This is also where the
run level of model configuration lives: the re-quote path (FR-1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from openai import APIError

from agents import (
    AgentsException,
    MaxTurnsExceeded,
    ModelProvider,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    RunResult,
    Runner,
    TResponseInputItem,
)

from shop_desk.catalogue import money
from shop_desk.config import Settings
from shop_desk.context import ShopContext, new_order_id
from shop_desk.desk_agents import ShopDeskAgents
from shop_desk.schemas import Order, OrderCheck, check_order_total

log = logging.getLogger(__name__)

# FR-8: the turn ceiling. A price question uses 1 turn, a basket edit 2, a confirmed order 3
# (Desk -> Order clerk -> view_basket -> Order). 6 leaves room for one retry of each step and
# stops a model that loops on tools before it burns money.
MAX_TURNS = 6

TurnKind = Literal["fast-path", "reasoning", "refused", "ended"]

REFUSAL = (
    "I'm sorry, I couldn't confirm that figure against our current catalogue, so I won't quote it. "
    "Could you ask again, naming the product? I'll look it up fresh."
)
CEILING = (
    "I'm sorry, this is taking me too many steps, so I'll stop here rather than keep you waiting. "
    "Your basket is saved with the shop; please start a new chat and ask again, or ask for a member of staff."
)
CLOSED = "This conversation has ended. Please start a new chat to continue."
TROUBLE = "Sorry, I'm having trouble reaching our system right now. Please try again in a moment."


@dataclass
class Reply:
    text: str
    kind: TurnKind
    model_calls: int = 0
    order: Order | None = None
    order_check: OrderCheck | None = None
    requoted: bool = False


class DeskSession:
    def __init__(
        self,
        agents: ShopDeskAgents,
        context: ShopContext,
        settings: Settings,
        *,
        model_provider: ModelProvider | None = None,
        max_turns: int = MAX_TURNS,
    ) -> None:
        self.agents = agents
        self.context = context
        self.settings = settings
        self.model_provider = model_provider  # tests inject scripted models here
        self.max_turns = max_turns
        self.history: list[TResponseInputItem] = []
        self.turn = 0
        self.ended = False

    def _run_config(self, **overrides) -> RunConfig:
        if self.model_provider is not None:
            overrides.setdefault("model_provider", self.model_provider)
        return RunConfig(workflow_name="Shop Desk conversation", **overrides)

    async def _run(self, run_input: list[TResponseInputItem], run_config: RunConfig) -> RunResult:
        self.context.start_run()
        return await Runner.run(
            self.agents.desk,
            run_input,
            context=self.context,
            max_turns=self.max_turns,
            run_config=run_config,
        )

    async def _requote(self, run_input: list[TResponseInputItem]) -> RunResult:
        """FR-1 run level: retry this one turn with the reasoning model, for this run only."""
        return await self._run(run_input, self._run_config(model=self.settings.reasoning_model))

    async def ask(self, text: str) -> Reply:
        if self.ended:
            return Reply(CLOSED, kind="ended")
        self.turn += 1
        run_input = [*self.history, {"role": "user", "content": text}]
        basket_before = dict(self.context.basket)
        requoted = False
        try:
            try:
                result = await self._run(run_input, self._run_config())
            except OutputGuardrailTripwireTriggered as trip:
                log.warning("turn %s: guardrail refused %s", self.turn, trip.guardrail_result.output.output_info)
                self.context.basket = basket_before  # undo tool side effects before the retry
                requoted = True
                result = await self._requote(run_input)
        except OutputGuardrailTripwireTriggered:
            self.context.basket = basket_before
            return self._finish_without_result(text, REFUSAL, "refused")
        except MaxTurnsExceeded:
            self.ended = True
            return self._finish_without_result(text, CEILING, "ended")
        except (AgentsException, APIError) as exc:
            log.warning("turn %s failed: %s", self.turn, type(exc).__name__)
            return self._finish_without_result(text, TROUBLE, "refused")

        self.history = result.to_input_list()
        calls = result.context_wrapper.usage.requests
        output = result.final_output
        if isinstance(output, Order):
            return self._confirm_order(output, calls, requoted)
        kind: TurnKind = "fast-path" if self.context.fast_path_used and not requoted else "reasoning"
        return Reply(str(output), kind=kind, model_calls=calls, requoted=requoted)

    def _finish_without_result(self, text: str, message: str, kind: TurnKind) -> Reply:
        """Keep the history coherent when a run produced no usable result."""
        self.history = [
            *self.history,
            {"role": "user", "content": text},
            {"role": "assistant", "content": message},
        ]
        return Reply(message, kind=kind)

    def _confirm_order(self, order: Order, calls: int, requoted: bool) -> Reply:
        """FR-5: the customer sees an order rendered by Python from the validated structure."""
        currency = self.context.currency
        check = check_order_total(order)
        if check.mismatch:
            log.warning(
                "order %s: model total %s != recomputed %s", order.order_id, check.model_total, check.recomputed_total
            )
            order = order.model_copy(update={"total": check.recomputed_total})
        if order.status != "confirmed" or not order.items:
            return Reply(
                "Your basket is empty, so there is nothing to confirm yet. What would you like to order?",
                kind="reasoning", model_calls=calls, order=order, order_check=check, requoted=requoted,
            )
        lines = [f"Order {order.order_id} confirmed:"]
        for item in order.items:
            lines.append(
                f"- {item.qty} × {item.sku} @ {money(item.unit_price, currency)} = "
                f"{money(item.qty * item.unit_price, currency)}"
            )
        lines.append(f"Total: {money(order.total, currency)}")
        if check.mismatch:
            lines.append(check.note(currency))
        lines.append("A member of staff will contact you to arrange payment and delivery.")
        self.context.orders.append(order)
        self.context.basket.clear()
        self.context.draft_order_id = new_order_id()
        return Reply("\n".join(lines), kind="reasoning", model_calls=calls, order=order, order_check=check, requoted=requoted)
