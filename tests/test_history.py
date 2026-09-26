"""FR-12: history is trimmed; tool noise goes first, then the oldest turns."""

from shop_desk.history import HISTORY_TURNS, TOOL_DETAIL_TURNS, split_turns, trim_history


def _turn(n: int) -> list[dict]:
    return [
        {"role": "user", "content": f"question {n}"},
        {"type": "function_call", "call_id": f"c{n}", "name": "view_basket", "arguments": "{}"},
        {"type": "function_call_output", "call_id": f"c{n}", "output": "Total: PKR 4,200"},
        {"role": "assistant", "content": f"answer {n}"},
    ]


def test_long_history_keeps_recent_turns_and_drops_old_tool_noise():
    items = [i for n in range(1, 11) for i in _turn(n)]  # 10 turns
    trimmed = trim_history(items)
    turns = split_turns(trimmed)
    assert len(turns) == HISTORY_TURNS
    assert turns[0][0]["content"] == "question 5"  # turns 1-4 dropped whole
    for turn in turns[:-TOOL_DETAIL_TURNS]:
        assert [i.get("role") for i in turn] == ["user", "assistant"]  # tool noise gone first
    for turn in turns[-TOOL_DETAIL_TURNS:]:
        assert len(turn) == 4  # recent turns keep full detail


def test_never_leaves_an_orphan_tool_output():
    trimmed = trim_history([i for n in range(1, 9) for i in _turn(n)])
    calls = {i["call_id"] for i in trimmed if i.get("type") == "function_call"}
    outputs = {i["call_id"] for i in trimmed if i.get("type") == "function_call_output"}
    assert calls == outputs


def test_short_history_is_untouched():
    items = _turn(1)
    assert trim_history(items) == items
    assert trim_history([]) == []
