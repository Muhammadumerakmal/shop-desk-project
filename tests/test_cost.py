"""FR-11: the cost line, built from run-context numbers, tells fast-path from reasoning turns."""

from agents.testing import assistant_message, function_call

from tests.conftest import FAST, REASONING


def _pricing_turn():
    return [
        [function_call("pricing_specialist", {"input": "3 x KTL-01"}, call_id="p1")],
        [function_call("quote", {"lines": [{"sku": "KTL-01", "qty": 3}]}, call_id="q1")],
        [assistant_message("12600")],
        [assistant_message("Three kettles come to PKR 12,600.")],
    ]


async def test_cost_line_distinguishes_fast_path_from_reasoning(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")], *_pricing_turn()])

    fast = await session.ask("What does the kettle cost?")
    slow = await session.ask("How much would three kettles be?")

    assert fast.turn_cost.kind == "fast-path" and fast.turn_cost.calls == 1
    assert fast.turn_cost.input_tokens == 100 and fast.turn_cost.output_tokens == 10
    # 2 Desk calls + 2 nested Pricing calls; nested usage is counted once, not twice
    assert slow.turn_cost.kind == "reasoning" and slow.turn_cost.calls == 4
    assert slow.turn_cost.input_tokens == 400
    assert "2 turn(s): 1 fast-path, 1 reasoning" in slow.cost_line
    assert "5 model call(s)" in slow.cost_line and "500 in / 50 out" in slow.cost_line
    assert "most expensive: turn 2" in slow.cost_line


async def test_agent_hooks_only_on_pricing_and_runner_records_every_run(make_session, provider):
    session = make_session()
    provider.script(FAST, _pricing_turn())
    await session.ask("How much would three kettles be?")
    ledger = session.context.ledger
    by_source = {(c.agent, c.source) for c in ledger.calls}
    assert by_source == {("Shop Desk", "run-hook"), ("Pricing specialist", "agent-hook")}
    runs = [(r.agent, r.depth, r.requests) for r in ledger.runs]
    assert ("Pricing specialist", 1, 2) in runs and ("Shop Desk", 0, 4) in runs
    assert session.agents.specialist_base.hooks is None and session.agents.escalation.hooks is None


async def test_cost_line_names_the_model_of_each_turn(make_session, provider):
    session = make_session()
    provider.script(FAST, [[assistant_message("The kettle is PKR 1.")]])  # refused
    provider.script(REASONING, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")]])
    reply = await session.ask("kettle price?")
    assert reply.requoted
    assert reply.turn_cost.models == (FAST, REASONING)  # the re-quote ran on the run-level override
    assert f"{REASONING} ×1" in reply.cost_line
