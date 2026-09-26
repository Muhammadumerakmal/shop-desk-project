"""Keeping a long conversation affordable (FR-12).

Every Desk call re-sends the history, so history is what a long conversation costs. Before each
run it is trimmed in this order:

1. Tool calls and tool outputs older than the last TOOL_DETAIL_TURNS turns are dropped first.
   They are the bulkiest items and the least useful: their facts were already turned into the
   Desk's replies, and figures must be re-fetched each turn anyway (NFR-3).
2. Then whole turns older than the last HISTORY_TURNS customer turns are dropped.

What must survive trimming (the basket, the draft order id, confirmed orders) lives in
ShopContext, so turn eleven still knows the basket even though turn one is gone.
"""

from __future__ import annotations

from typing import Any

HISTORY_TURNS = 6
TOOL_DETAIL_TURNS = 2
TOOL_ITEM_TYPES = {
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "reasoning",
}


def _is_user_message(item: Any) -> bool:
    return isinstance(item, dict) and item.get("role") == "user"


def _is_tool_item(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") in TOOL_ITEM_TYPES


def split_turns(items: list[Any]) -> list[list[Any]]:
    """Group items into turns; each turn starts at a customer message."""
    turns: list[list[Any]] = []
    for item in items:
        if _is_user_message(item) or not turns:
            turns.append([])
        turns[-1].append(item)
    return turns


def trim_history(
    items: list[Any], keep_turns: int = HISTORY_TURNS, tool_detail_turns: int = TOOL_DETAIL_TURNS
) -> list[Any]:
    turns = split_turns(items)[-keep_turns:]
    cut = max(len(turns) - tool_detail_turns, 0)
    older = [[i for i in turn if not _is_tool_item(i)] for turn in turns[:cut]]
    return [item for turn in [*older, *turns[cut:]] for item in turn]
