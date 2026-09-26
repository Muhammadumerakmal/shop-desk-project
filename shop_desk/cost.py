"""What every turn cost (FR-11): run-level hooks, agent-level hooks and a custom runner.

- `CostHooks` (run level) sees every model call of the Desk run: Desk, Order clerk, Escalation.
- `PricingHooks` (agent level) sits on the Pricing specialist only. It runs as a nested
  `Runner.run` inside a tool, where the Desk run's hooks cannot see its calls.
- `LedgerRunner` (custom runner) records each run's model and token count from the run context.

Token numbers come from `RunContextWrapper.usage` and `ModelResponse.usage`, never an estimate.
Nested runs share the parent's Usage object, so turn totals sum only top-level runs.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agents import (
    Agent,
    AgentHooks,
    AgentsException,
    ModelResponse,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    RunResult,
)
from agents.models.default_models import OPENAI_DEFAULT_MODEL_ENV_VARIABLE_NAME, get_default_model
from agents.run import AgentRunner


@dataclass(frozen=True)
class CallRecord:
    turn: int
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    source: str  # "run-hook" | "agent-hook"


@dataclass(frozen=True)
class RunRecord:
    turn: int
    depth: int  # 0 = the customer's turn, 1 = a nested agent-as-tool run
    agent: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    ok: bool


@dataclass(frozen=True)
class TurnRecord:
    turn: int
    kind: str  # "fast-path" | "reasoning" | "refused" | "ended"
    calls: int
    input_tokens: int
    output_tokens: int
    models: tuple[str, ...]

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def line(self) -> str:
        models = ", ".join(f"{m} ×{n}" for m, n in Counter(self.models).items()) or "no model call"
        return (
            f"Turn {self.turn} · {self.kind} · {self.calls} call(s) · "
            f"{self.input_tokens:,} in / {self.output_tokens:,} out · {models}"
        )


@dataclass
class CostLedger:
    turn: int = 0
    run_model_override: str | None = None
    depth: int = 0
    calls: list[CallRecord] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)

    def resolve_model(self, agent: Agent[Any] | None) -> str:
        """The model name that served this agent: run override > agent's own > global default."""
        if self.run_model_override:
            return self.run_model_override
        if agent is not None and isinstance(agent.model, str):
            return agent.model
        if agent is not None and agent.model is not None:
            return type(agent.model).__name__
        return os.environ.get(OPENAI_DEFAULT_MODEL_ENV_VARIABLE_NAME) or get_default_model()

    def start_turn(self) -> None:
        self.turn += 1

    def record_call(self, agent: Agent[Any], response: ModelResponse, source: str) -> None:
        usage = response.usage
        self.calls.append(
            CallRecord(self.turn, agent.name, self.resolve_model(agent), usage.input_tokens, usage.output_tokens, source)
        )

    def close_turn(self, kind: str) -> TurnRecord:
        runs = [r for r in self.runs if r.turn == self.turn and r.depth == 0]
        calls = [c for c in self.calls if c.turn == self.turn]
        record = TurnRecord(
            turn=self.turn,
            kind=kind,
            calls=sum(r.requests for r in runs),
            input_tokens=sum(r.input_tokens for r in runs),
            output_tokens=sum(r.output_tokens for r in runs),
            models=tuple(c.model for c in calls),
        )
        self.turns.append(record)
        return record

    def most_expensive_turn(self) -> TurnRecord | None:
        return max(self.turns, key=lambda t: t.tokens, default=None)

    def cost_line(self) -> str:
        """The per-conversation cost line: turns, fast vs reasoning, calls, tokens, models."""
        kinds = Counter(t.kind for t in self.turns)
        calls = sum(t.calls for t in self.turns)
        tokens_in = sum(t.input_tokens for t in self.turns)
        tokens_out = sum(t.output_tokens for t in self.turns)
        models = Counter(m for t in self.turns for m in t.models)
        parts = [
            f"{len(self.turns)} turn(s): {kinds.get('fast-path', 0)} fast-path, {kinds.get('reasoning', 0)} reasoning"
            + (f", {kinds['refused']} refused" if kinds.get("refused") else ""),
            f"{calls} model call(s)",
            f"{tokens_in:,} in / {tokens_out:,} out tokens",
            "models: " + (", ".join(f"{m} ×{n}" for m, n in models.items()) or "none"),
        ]
        worst = self.most_expensive_turn()
        if worst and worst.tokens:
            parts.append(f"most expensive: turn {worst.turn} ({worst.tokens:,} tokens)")
        return "Cost · " + " · ".join(parts)


def _ledger(context: Any) -> CostLedger | None:
    shop_context = context.context if isinstance(context, RunContextWrapper) else context
    return getattr(shop_context, "ledger", None)


class CostHooks(RunHooks[Any]):
    """Run-level hooks: one record per model call in the customer's run."""

    async def on_llm_end(self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse) -> None:
        ledger = _ledger(context)
        if ledger:
            ledger.record_call(agent, response, source="run-hook")


class PricingHooks(AgentHooks[Any]):
    """Agent-level hooks, attached to the Pricing specialist only."""

    async def on_llm_end(self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse) -> None:
        ledger = _ledger(context)
        if ledger:
            ledger.record_call(agent, response, source="agent-hook")


def _usage_numbers(wrapper: RunContextWrapper[Any] | None) -> tuple[int, int, int]:
    if wrapper is None:
        return 0, 0, 0
    u = wrapper.usage
    return u.requests, u.input_tokens, u.output_tokens


class LedgerRunner(AgentRunner):
    """Custom runner: every Runner.run (including nested agent-as-tool runs) passes through here."""

    async def run(self, starting_agent: Agent[Any], input: Any, **kwargs: Any) -> RunResult:
        context = kwargs.get("context")
        ledger = _ledger(context)
        if ledger is None:
            return await super().run(starting_agent, input, **kwargs)

        run_config = kwargs.get("run_config")
        override = run_config.model if isinstance(run_config, RunConfig) and isinstance(run_config.model, str) else None
        # Nested runs share the parent's Usage object: measure the delta, not the running total.
        before = _usage_numbers(context if isinstance(context, RunContextWrapper) else None)
        previous_override, depth = ledger.run_model_override, ledger.depth
        ledger.run_model_override = override or previous_override
        ledger.depth += 1
        wrapper, ok = None, False
        try:
            result = await super().run(starting_agent, input, **kwargs)
            wrapper, ok = result.context_wrapper, True
            return result
        except AgentsException as exc:
            wrapper = getattr(exc.run_data, "context_wrapper", None) if exc.run_data else None
            raise
        finally:
            after = _usage_numbers(wrapper)
            ledger.runs.append(
                RunRecord(
                    turn=ledger.turn,
                    depth=depth,
                    agent=starting_agent.name,
                    model=ledger.resolve_model(starting_agent),
                    requests=max(after[0] - before[0], 0),
                    input_tokens=max(after[1] - before[1], 0),
                    output_tokens=max(after[2] - before[2], 0),
                    ok=ok,
                )
            )
            ledger.run_model_override, ledger.depth = previous_override, depth
