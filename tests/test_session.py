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


async def test_turn_eleven_still_remembers_the_basket(make_session, provider):
    session = make_session()
    steps = [
        [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a1")],
        [assistant_message("Three kettles added.")],
    ]
    steps += [[assistant_message(f"Happy to help ({n}).")] for n in range(2, 11)]  # turns 2-10: small talk
    steps += [
        [function_call("view_basket", {}, call_id="v11")],
        [assistant_message("You have 3 × Electric kettle 1.7L, PKR 12,600 in total.")],
    ]
    provider.script(FAST, steps)
    await session.ask("I'd like three kettles.")
    for n in range(2, 11):
        await session.ask(f"small talk {n}")
    reply = await session.ask("Remind me what I'm ordering?")

    sent = provider.models[FAST].calls[-2].input
    assert not any(isinstance(i, dict) and i.get("content") == "I'd like three kettles." for i in sent)  # turn 1 trimmed
    assert "PKR 12,600" in reply.text and session.context.basket == {"KTL-01": 3}


async def test_one_trace_per_conversation(make_session, provider):
    from agents import set_trace_processors, set_tracing_disabled
    from agents.tracing import TracingProcessor

    class Collector(TracingProcessor):
        def __init__(self):
            self.traces, self.spans = [], []

        def on_trace_start(self, trace): self.traces.append(trace.trace_id)
        def on_trace_end(self, trace): pass
        def on_span_start(self, span): pass
        def on_span_end(self, span): self.spans.append(span)
        def shutdown(self): pass
        def force_flush(self): pass

    collector = Collector()
    set_trace_processors([collector])
    set_tracing_disabled(False)
    try:
        session = make_session()
        provider.script(
            FAST,
            [
                [function_call("lookup_price", {"product": "kettle"}, call_id="c1")],
                [assistant_message("Hello!")],
            ],
        )
        await session.ask("kettle price?")
        await session.ask("hi")
        session.close()
    finally:
        set_tracing_disabled(True)
    assert collector.traces == [session.trace.trace_id]
    assert {s.trace_id for s in collector.spans} == {session.trace.trace_id}
    turn_spans = [s.span_data.name for s in collector.spans if s.span_data.type == "custom"]
    assert turn_spans == ["turn 1 · fast-path", "turn 2 · reasoning"]


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
