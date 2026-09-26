"""The escalation handoff transfers a trimmed conversation, not the entire log (FR-10).

Removed: every tool call and tool output (catalogue lookups, basket edits, specialist calls, the
handoff call itself) and all but the last KEEP_MESSAGES items of earlier history.
Kept: the recent customer and Desk messages, in order. The typed reason travels through
ShopContext into the escalation agent's instructions, and the basket stays readable in context.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from agents import HandoffInputData
from agents.extensions import handoff_filters

KEEP_MESSAGES = 6


@dataclass(frozen=True)
class HandoffAudit:
    before: dict[str, int]
    after: dict[str, int]

    @property
    def removed(self) -> dict[str, int]:
        return {k: n - self.after.get(k, 0) for k, n in self.before.items() if n - self.after.get(k, 0) > 0}

    def describe(self) -> str:
        def fmt(counts: dict[str, int]) -> str:
            return ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "nothing"

        return (
            f"before: {sum(self.before.values())} items ({fmt(self.before)}) → "
            f"after: {sum(self.after.values())} items ({fmt(self.after)}); removed {fmt(self.removed)}"
        )


def _kind(item: Any) -> str:
    """Classify an input item or run item as 'message' or 'tool'."""
    if isinstance(item, dict):
        kind = item.get("type") or ("message" if "role" in item else "other")
    else:
        kind = getattr(item, "type", type(item).__name__)
    return "message" if kind in {"message", "message_output_item"} else "tool"


def summarise(data: HandoffInputData) -> dict[str, int]:
    history = data.input_history if isinstance(data.input_history, tuple) else ()
    items = [*history, *data.pre_handoff_items, *data.new_items]
    if isinstance(data.input_history, str):
        items.append({"role": "user"})
    return dict(Counter(_kind(i) for i in items))


def escalation_filter(data: HandoffInputData) -> HandoffInputData:
    """SDK filter first (drop all tool items), then keep only the recent messages."""
    filtered = handoff_filters.remove_all_tools(data)
    history = filtered.input_history
    if isinstance(history, tuple):
        history = history[-KEEP_MESSAGES:]
    trimmed = filtered.clone(input_history=history)
    if data.run_context is not None:
        data.run_context.context.handoff_audit = HandoffAudit(before=summarise(data), after=summarise(trimmed))
    return trimmed
