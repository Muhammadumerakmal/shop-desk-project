"""FR-3: a price answer costs one model call; an order question costs more."""

from agents.testing import assistant_message, function_call

from tests.conftest import FAST


async def test_price_question_is_one_model_call(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")]])
    reply = await session.ask("What does the kettle cost?")
    assert reply.text == "Electric kettle 1.7L (KTL-01) is PKR 4,200 — 12 in stock."  # the tool's own words
    assert reply.kind == "fast-path"
    assert provider.calls(FAST) == 1 and reply.model_calls == 1


async def test_order_question_takes_the_normal_loop(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="c1")],
            [assistant_message("Done: three kettles are in your basket.")],
        ],
    )
    reply = await session.ask("Can I order three kettles?")
    assert reply.kind == "reasoning"
    assert reply.model_calls == 2
    assert session.context.basket == {"KTL-01": 3}
