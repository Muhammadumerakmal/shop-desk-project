"""Session behaviour: turn ceiling (FR-8), separate baskets and long conversations (FR-12)."""

from agents.testing import assistant_message, function_call

from shop_desk.session import CEILING, CLOSED, MAX_TURNS

from tests.conftest import FAST


async def test_turn_ceiling_is_caught_and_ends_politely(make_session, provider):
    session = make_session()
    looping = [[function_call("search_catalogue", {"query": ""}, call_id=f"s{i}")] for i in range(MAX_TURNS + 1)]
    provider.script(FAST, looping)
    reply = await session.ask("Show me everything, again and again")
    assert reply.text == CEILING and reply.kind == "ended"
    assert (await session.ask("hello?")).text == CLOSED


async def test_two_sessions_do_not_share_a_basket(make_session, provider):
    first, second = make_session(), make_session()
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 2}, call_id="a1")],
            [assistant_message("Two kettles added.")],
        ],
    )
    await first.ask("Add two kettles")
    assert first.context.basket == {"KTL-01": 2} and second.context.basket == {}
    assert first.context.draft_order_id != second.context.draft_order_id
