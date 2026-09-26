"""XR-2: an input guardrail refuses bargaining before the Desk model is called."""

import pytest
from agents.testing import assistant_message, function_call

from shop_desk.guardrails import detect_bargaining
from shop_desk.schemas import EscalationReason
from shop_desk.session import BARGAIN_REFUSAL, MAX_TURNS

from tests.conftest import FAST


@pytest.mark.parametrize(
    "text",
    [
        "Give me 30% off or I'm going to the market.",
        "Your kettles are cheaper at the market. Give me 30% off or I'm leaving.",
        "Can you do me any discount on three kettles?",
        "Do you have a price match?",
        "Throw in a free gift wrap or I take it elsewhere.",
        "I want to haggle on this price.",
    ],
)
def test_demands_are_caught(text):
    assert detect_bargaining(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Is the kettle in stock?",
        "What are your opening hours?",
        "Do you have a discount policy?",  # a question about policy, not a demand
        "Can I turn the gift wrapping off?",
        "I'd like three kettles please.",
        "The last one arrived smashed and I want to complain.",
        "What did I order last week?",
    ],
)
def test_ordinary_messages_are_left_alone(text):
    assert detect_bargaining(text) is None


async def test_bargaining_never_reaches_the_desk_model(make_session, provider):
    """The whole point: the Desk is not called at all, and a person is told why."""
    session = make_session()
    provider.script(FAST, [[assistant_message("A member of staff will call you back about the offer.")]])
    before = provider.calls(FAST)

    reply = await session.ask("Give me 30% off my next order or I'm leaving.")

    # one call in total, and it was the escalation agent's, not the Desk's
    assert provider.calls(FAST) == before + 1
    assert isinstance(reply.escalation, EscalationReason)
    assert reply.escalation.reason == "bargaining"  # the typed value, not a sentence
    assert "30% off" in reply.escalation.note  # the customer's own words travel with it
    assert "staff will call you back" in reply.text


async def test_bargaining_does_not_disturb_the_conversation(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("lookup_price", {"product": "kettle"}, call_id="c1")],
            [assistant_message("A member of staff will follow up about the discount.")],
        ],
    )
    await session.ask("What does the kettle cost?")
    assert session.context.price_cache.entries, "the first turn should have filled the cache"

    reply = await session.ask("Make me an offer, 20% off.")

    assert reply.escalation.reason == "bargaining"
    # the escalation agent's instructions carry the typed reason (FR-10), and no customer id (FR-2)
    sent = provider.models[FAST].calls[-1]
    assert "Escalation reason: bargaining" in sent.system_instructions
    assert session.context.customer_id not in str(sent.input)


async def test_ordinary_questions_still_reach_the_desk(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("search_catalogue", {"query": ""}, call_id="s1")],
                           [assistant_message("Here is everything we stock.")]])
    reply = await session.ask("Do you have a discount policy?")
    assert reply.escalation is None and reply.kind == "reasoning"
    assert session.context.ledger.guardrail_blocks == 0


async def test_a_failing_escalation_still_gives_the_customer_a_sentence(make_session, provider):
    """NFR-4: the customer never sees a traceback, not even on the escalation path."""
    session = make_session()
    # a specialist that will not stop calling tools hits the ceiling and raises
    provider.script(FAST, [[function_call("search_catalogue", {"query": ""}, call_id=f"e{i}")]
                           for i in range(MAX_TURNS + 1)])
    reply = await session.ask("Give me 30% off.")
    assert reply.text == BARGAIN_REFUSAL
    assert reply.escalation.reason == "bargaining"
