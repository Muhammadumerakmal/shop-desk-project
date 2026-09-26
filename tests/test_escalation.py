"""FR-10: escalation carries a typed reason and a trimmed history."""

from agents import HandoffInputData, RunContextWrapper
from agents.testing import assistant_message, function_call

from shop_desk.handoff_filters import KEEP_MESSAGES, escalation_filter
from shop_desk.schemas import EscalationReason

from tests.conftest import FAST


def _noisy_history(turns: int) -> tuple:
    items = []
    for n in range(turns):
        items += [
            {"role": "user", "content": f"q{n}"},
            {"type": "function_call", "call_id": f"c{n}", "name": "search_catalogue", "arguments": "{}"},
            {"type": "function_call_output", "call_id": f"c{n}", "output": "KTL-01 · Electric kettle"},
            {"role": "assistant", "content": f"a{n}"},
        ]
    return tuple(items)


def test_filter_removes_tool_items_and_old_messages(make_context):
    ctx = make_context()
    data = HandoffInputData(
        input_history=_noisy_history(6), pre_handoff_items=(), new_items=(), run_context=RunContextWrapper(ctx)
    )
    out = escalation_filter(data)
    assert len(out.input_history) == KEEP_MESSAGES
    assert all("role" in item for item in out.input_history)  # messages only
    assert out.input_history[-1] == {"role": "assistant", "content": "a5"}  # most recent kept
    audit = ctx.handoff_audit
    assert audit.before == {"message": 12, "tool": 12}
    assert audit.after == {"message": KEEP_MESSAGES}
    assert "removed 6 message, 12 tool" in audit.describe()


async def test_desk_handoff_escalates_with_a_typed_reason_and_clean_history(make_session, provider):
    """The Desk's own escalate_to_handoff, for a reason the input guardrail leaves alone (XR-2)."""
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a1")],
            [assistant_message("Three kettles are in your basket.")],
            [function_call("escalate_to_human", {"reason": "complaint", "note": "arrived broken"}, call_id="e1")],
            [assistant_message("I'm sorry about that. A member of staff will follow up with you.")],
        ],
    )
    await session.ask("Add three kettles")
    reply = await session.ask("The last one arrived smashed and I want to complain.")

    assert isinstance(reply.escalation, EscalationReason)
    assert reply.escalation.reason == "complaint"  # a value, not a sentence
    assert reply.order.status == "escalated" and reply.order.total == 12600

    escalation_call = provider.models[FAST].calls[-1]
    assert "Escalation reason: complaint" in escalation_call.system_instructions
    sent_types = {item.get("type") for item in escalation_call.input if isinstance(item, dict)}
    assert "function_call" not in sent_types and "function_call_output" not in sent_types
    assert reply.handoff_audit.before.get("tool", 0) >= 2 and "tool" not in reply.handoff_audit.after


async def test_escalation_survives_an_unreadable_catalogue(make_session, provider, catalogue_file):
    """FR-10 + NFR-4: a broken catalogue must not cost the customer their staff handoff.

    The handoff quotes no figure, so there is nothing to verify and the turn must complete. The
    draft order *is* all figures, so it is dropped rather than filled in with an unverified price.
    """
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a1")],
            [assistant_message("Three kettles are in your basket.")],
            [function_call("escalate_to_human", {"reason": "complaint", "note": "arrived broken"}, call_id="e1")],
            [assistant_message("I'm sorry about that. A member of staff will follow up with you.")],
        ],
    )
    await session.ask("Add three kettles")
    catalogue_file.write_text("{broken")  # the shop's catalogue goes unreadable mid-conversation

    reply = await session.ask("The last one arrived smashed and I want to complain.")

    assert isinstance(reply.escalation, EscalationReason)
    assert reply.escalation.reason == "complaint"  # staff still get the typed reason
    assert "member of staff" in reply.text.lower()
    assert reply.handoff_audit is not None  # and the trimmed history still travelled
    assert reply.order is None, "no unverified price reaches staff either"
    assert reply.kind == "reasoning" and not reply.requoted, "no re-quote was needed"
    assert session.context.basket == {"KTL-01": 3}, "the basket survives for staff to read"
