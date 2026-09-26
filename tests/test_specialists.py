"""FR-8 (a specialist that returns a number) and FR-9 (clones of one base)."""

from agents.testing import assistant_message, function_call

from tests.conftest import FAST


async def test_desk_uses_the_specialists_number_in_its_own_words(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            # Desk -> pricing_specialist tool
            [function_call("pricing_specialist", {"input": "3 x KTL-01 and 2 x IRN-05"}, call_id="p1")],
            # nested Pricing specialist run: quote tool, then digits only
            [function_call("quote", {"lines": [{"sku": "KTL-01", "qty": 3}, {"sku": "IRN-05", "qty": 2}]}, call_id="q1")],
            [assistant_message("25600")],
            # Desk writes its own sentence around the number
            [assistant_message("Three kettles and two steam irons come to PKR 25,600 altogether.")],
        ],
    )
    reply = await session.ask("How much for three kettles and two irons?")
    assert "PKR 25,600" in reply.text and reply.kind == "reasoning"
    tool_output = provider.models[FAST].calls[-1].input[-1]
    assert tool_output["output"] == "25600"  # the specialist returned a figure, not a paragraph


def test_clones_share_and_differ(make_session):
    a = make_session().agents
    base, pricing, escalation = a.specialist_base, a.pricing, a.escalation
    # shared (same objects): the tools list, the guardrail list, the inherited model
    assert pricing.tools is base.tools and escalation.tools is base.tools
    assert pricing.output_guardrails is base.output_guardrails
    assert pricing.model is base.model is None  # neither clone restates the model
    # independent
    assert pricing.instructions is not base.instructions
    assert pricing.model_settings is not base.model_settings
    assert pricing.model_settings.temperature == 0 and escalation.model_settings.temperature == 0.4


def test_shared_tools_list_is_the_trap(make_session):
    a = make_session().agents
    a.pricing.tools.append("oops")  # mutating one clone's list...
    assert a.escalation.tools[-1] == "oops"  # ...changes its sibling and the base too
    a.pricing.tools.pop()
