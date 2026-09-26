"""FR-4: the same question at two simulated hours gets two different promises."""

from agents.testing import assistant_message

from shop_desk.context import fixed_clock
from shop_desk.instructions import resolved_prompt

from tests.conftest import FAST


async def test_two_hours_two_promises(make_session):
    morning = make_session(clock=fixed_clock(11))
    night = make_session(clock=fixed_clock(22))
    p_morning = await resolved_prompt(morning.agents.desk, morning.context)
    p_night = await resolved_prompt(night.agents.desk, night.context)
    assert "SAME DAY" in p_morning
    assert "CLOSED" in p_night and "Do NOT promise same-day" in p_night and "opens tomorrow at 10:00" in p_night
    assert p_morning != p_night


async def test_after_cutoff_promises_next_day(make_session):
    evening = make_session(clock=fixed_clock(18))
    prompt = await resolved_prompt(evening.agents.desk, evening.context)
    assert "NEXT-DAY" in prompt and "SAME DAY" not in prompt


async def test_prompt_sent_to_model_is_the_resolved_one(make_session, provider):
    session = make_session(clock=fixed_clock(22))
    provider.script(FAST, [[assistant_message("We open tomorrow at 10:00.")]])
    await session.ask("Can you deliver today?")
    sent = provider.models[FAST].calls[0].system_instructions
    assert "opens tomorrow at 10:00" in sent


async def test_prompt_says_a_total_needs_view_basket_this_turn(make_session):
    """FR-6 with FR-3: found live. Without this the Desk multiplies add_to_basket's unit price
    itself, and the catalogue guardrail refuses a perfectly good basket total as unbacked."""
    session = make_session(clock=fixed_clock(14))
    prompt = await resolved_prompt(session.agents.desk, session.context)
    assert "call view_basket in this turn" in prompt
    assert "returns no total" in prompt
    assert "refused" in prompt
