"""One customer conversation: `DeskSession.ask()` runs one customer turn.

Keeps the conversation history and the ShopContext together, so two sessions never share a
basket (FR-12). Every run-level failure becomes a polite sentence here (NFR-4). This is also where
the run level of model configuration lives: the re-quote path (FR-1). A session owns one trace,
so a whole conversation is one trace (FR-13).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from openai import APIError

from agents import (
    AgentsException,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelProvider,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    RunResult,
    Runner,
    TResponseInputItem,
    custom_span,
    trace,
)
from agents.tracing.scope import Scope

from shop_desk.catalogue import CatalogueError, load_catalogue, money
from shop_desk.config import Settings
from shop_desk.context import ShopContext, new_order_id
from shop_desk.cost import CostHooks, TurnRecord
from shop_desk.desk_agents import ShopDeskAgents
from shop_desk.fastpath import is_price_question
from shop_desk.guardrails import escalation_reason_from_guardrail
from shop_desk.handoff_filters import HandoffAudit
from shop_desk.history import trim_history
from shop_desk.orders import placed_order, record_order
from shop_desk.schemas import EscalationReason, LineItem, Order, OrderCheck, check_order_total

log = logging.getLogger(__name__)

# FR-8: the turn ceiling. A price question uses 1 turn, a basket edit 2, a confirmed order 3
# (Desk -> Order clerk -> view_basket -> Order). 6 leaves room for one retry of each step and
# stops a model that loops on tools before it burns money.
MAX_TURNS = 6

TurnKind = Literal["fast-path", "reasoning", "cached", "refused", "ended"]

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
# XR-2: only used if the escalation agent itself fails; it is the last thing a customer sees.
BARGAIN_REFUSAL = (
    "Prices are fixed at what our catalogue says, so I can't change them. A member of staff will "
    "follow up with you about the offer during opening hours."
)


@dataclass
class Reply:
    text: str
    kind: TurnKind
    model_calls: int = 0
    order: Order | None = None
    order_check: OrderCheck | None = None
    requoted: bool = False
    escalation: EscalationReason | None = None
    handoff_audit: HandoffAudit | None = None
    turn_cost: TurnRecord | None = None
    cost_line: str = ""


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
        self.hooks = CostHooks()
        # FR-13: one trace for the whole conversation, started now, finished in close().
        self.trace = trace(
            workflow_name="Shop Desk conversation",
            group_id=f"desk-{uuid.uuid4().hex[:12]}",
            metadata={"shop": context.shop, "tier": context.tier},
        )
        self.trace.start()

    @property
    def trace_url(self) -> str:
        return f"https://platform.openai.com/traces/trace?trace_id={self.trace.trace_id}"

    def close(self) -> None:
        self.trace.finish()

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
            hooks=self.hooks,
            run_config=run_config,
        )

    async def _requote(self, run_input: list[TResponseInputItem]) -> RunResult:
        """FR-1 run level: retry this one turn with the reasoning model, for this run only."""
        return await self._run(run_input, self._run_config(model=self.settings.reasoning_model))

    async def ask(self, text: str) -> Reply:
        if self.ended:
            return Reply(CLOSED, kind="ended", cost_line=self.context.ledger.cost_line())
        self.turn += 1
        self.context.ledger.start_turn()
        self.context.escalation = self.context.handoff_audit = None
        token = Scope.set_current_trace(self.trace)  # every run of this turn joins the one trace
        try:
            with custom_span(f"turn {self.turn}", {"customer_turn": self.turn}) as span:
                reply = await self._ask(text)
                span.span_data.name = f"turn {self.turn} · {reply.kind}"
                span.span_data.data["kind"] = reply.kind
        finally:
            Scope.reset_current_trace(token)
        reply.turn_cost = self.context.ledger.close_turn(reply.kind)
        reply.cost_line = self.context.ledger.cost_line()
        return reply

    async def _ask(self, text: str) -> Reply:
        cached = self._from_cache(text)  # XR-3: no model call at all
        if cached is not None:
            return cached
        # FR-12: what is sent is trimmed; the basket lives in context, not in history.
        run_input = [*trim_history(self.history), {"role": "user", "content": text}]
        basket_before = dict(self.context.basket)
        requoted = False
        try:
            try:
                result = await self._run(run_input, self._run_config())
            except OutputGuardrailTripwireTriggered as trip:
                log.warning("turn %s: guardrail refused %s", self.turn, trip.guardrail_result.output.output_info)
                self.context.basket = basket_before  # undo tool side effects before the retry
                requoted = True
                self.context.ledger.block(requoted=True)  # XR-4: this is the turn that costs twice
                result = await self._requote(run_input)
        except InputGuardrailTripwireTriggered as trip:  # XR-2, before any Desk model call
            return await self._bargaining(trip, text)
        except OutputGuardrailTripwireTriggered:
            self.context.basket = basket_before
            self.context.ledger.block(requoted=True)
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
        if result.last_agent is self.agents.escalation:
            return self._escalated(str(output), calls, requoted)
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

    def _from_cache(self, text: str) -> Reply | None:
        """XR-3: answer a repeated plain price question without calling the model at all.

        Only reachable when `lookup_price` already answered this exact set of products against
        these exact catalogue bytes, so the text is the tool's own current words, not a copy.
        """
        if not is_price_question(text):
            return None
        try:
            catalogue = load_catalogue(self.context.catalogue_path)
        except CatalogueError:
            return None  # the normal path reports catalogue trouble in its own words
        answer = self.context.price_cache.get(catalogue, text)
        if answer is None:
            return None
        # The exchange still enters the history, so the next turn has the whole conversation.
        self.history = [
            *self.history,
            {"role": "user", "content": text},
            {"role": "assistant", "content": answer},
        ]
        return Reply(answer, kind="cached", model_calls=0)

    async def _bargaining(self, trip: InputGuardrailTripwireTriggered, text: str) -> Reply:
        """XR-2: the Desk model never saw this message. Hand it straight to a person."""
        info = trip.guardrail_result.output.output_info
        self.context.escalation = escalation_reason_from_guardrail(info)
        log.info("turn %s: bargaining refused by the input guardrail (%s)", self.turn, info)
        reply_text = BARGAIN_REFUSAL
        calls = 0
        try:
            result = await Runner.run(
                self.agents.escalation,
                [{"role": "user", "content": text}],
                context=self.context,
                max_turns=self.max_turns,
                hooks=self.hooks,
                run_config=self._run_config(),
            )
            reply_text = str(result.final_output)
            calls = result.context_wrapper.usage.requests
        except (AgentsException, APIError) as exc:
            log.warning("turn %s: escalation failed (%s)", self.turn, type(exc).__name__)
        self.history = [
            *self.history,
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply_text},
        ]
        return Reply(
            reply_text,
            kind="reasoning",
            model_calls=calls,
            escalation=self.context.escalation,
        )

    def _escalated(self, text: str, calls: int, requoted: bool) -> Reply:
        """FR-10: hand staff a typed reason and, if there is a basket, an 'escalated' order."""
        order = None
        if self.context.basket:
            catalogue = load_catalogue(self.context.catalogue_path)
            items = [
                LineItem(sku=sku, qty=qty, unit_price=catalogue.get(sku).price)
                for sku, qty in self.context.basket.items()
                if catalogue.get(sku) is not None
            ]
            order = Order(
                order_id=self.context.draft_order_id,
                status="escalated",
                items=items,
                total=round(sum(i.qty * i.unit_price for i in items), 2),
            )
        return Reply(
            text,
            kind="reasoning",
            model_calls=calls,
            order=order,
            requoted=requoted,
            escalation=self.context.escalation,
            handoff_audit=self.context.handoff_audit,
        )

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
        # XR-1: stored as SKUs and quantities only, so the file can never become a second source
        # of prices. The write is best-effort and never fails the order (NFR-4).
        record_order(placed_order(order, self.context.customer_id, self.context.now()), self.context.orders_path)
        self.context.basket.clear()
        self.context.draft_order_id = new_order_id()
        return Reply("\n".join(lines), kind="reasoning", model_calls=calls, order=order, order_check=check, requoted=requoted)
