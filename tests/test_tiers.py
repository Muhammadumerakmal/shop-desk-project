"""FR-7: a tool that is off for everyone, and a tool that only regular customers earn."""

from agents import RunContextWrapper

from shop_desk.tools import loyalty_discount

from tests.conftest import invoke


async def _tool_names(session) -> set[str]:
    tools = await session.agents.desk.get_all_tools(RunContextWrapper(session.context))
    return {t.name for t in tools}


async def test_same_question_two_tiers_two_tool_sets(make_session):
    walk_in = await _tool_names(make_session(tier="walk_in"))
    regular = await _tool_names(make_session(tier="regular"))
    assert "loyalty_discount" in regular and "loyalty_discount" not in walk_in
    assert regular - walk_in == {"loyalty_discount"}


async def test_seasonal_tool_is_in_no_schema(make_session):
    for tier in ("walk_in", "regular"):
        assert "eid_gift_wrap" not in await _tool_names(make_session(tier=tier))


async def test_what_the_model_is_sent_differs_by_tier(make_session, provider):
    from agents.testing import assistant_message

    from tests.conftest import FAST

    sent = {}
    for tier in ("walk_in", "regular"):
        session = make_session(tier=tier)
        provider.script(FAST, [[assistant_message("Hello!")]])
        await session.ask("Any deals for me?")
        sent[tier] = {t.name for t in provider.models[FAST].calls[-1].tools}
    assert sent["regular"] - sent["walk_in"] == {"loyalty_discount"}
    assert "eid_gift_wrap" not in sent["regular"] | sent["walk_in"]


async def test_loyalty_tool_reads_tier_through_the_wrapper(make_context):
    regular = make_context(tier="regular")
    text = await invoke(loyalty_discount, regular, sku="KTL-01", qty=3)
    assert "PKR 11,970" in text and "saving PKR 630" in text
    walk_in = make_context(tier="walk_in")
    assert "only available to regular" in await invoke(loyalty_discount, walk_in, sku="KTL-01", qty=3)
