"""XR-3: a repeated price question costs zero model calls, and an edit retires the entry."""

import json

from agents.testing import assistant_message, function_call

from shop_desk.catalogue import load_catalogue
from shop_desk.fastpath import PriceCache, is_price_question
from shop_desk.tools import lookup_price

from tests.conftest import FAST, invoke

KETTLE = "What does the kettle cost?"


def test_a_repeat_of_a_price_question_is_recognised():
    assert is_price_question("What does the kettle cost?")
    assert is_price_question("how much is the steam iron")
    assert is_price_question("Is the pedestal fan in stock?")


def test_a_question_needing_thought_is_never_a_price_question():
    for text in [
        "Can I order three kettles?",
        "What's in my basket now, and the total?",
        "Give me 30% off or I'm going to the market.",
        "If I order now, can you deliver today?",
        "Your kettles are cheaper at the market.",
        "What did I order last week?",
    ]:
        assert not is_price_question(text), text


async def test_first_question_costs_one_call_and_the_repeat_costs_none(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")]])

    first = await session.ask(KETTLE)
    assert first.kind == "fast-path" and provider.calls(FAST) == 1

    second = await session.ask(KETTLE)
    assert second.kind == "cached"
    assert second.text == first.text  # the tool's own words, not a paraphrase
    assert second.model_calls == 0
    assert provider.calls(FAST) == 1, "the repeat must not call the model at all"


async def test_a_different_product_is_not_served_from_the_cache(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("lookup_price", {"product": "kettle"}, call_id="c1")],
            [function_call("lookup_price", {"product": "iron"}, call_id="c2")],
        ],
    )
    await session.ask(KETTLE)
    other = await session.ask("how much is the steam iron")
    assert other.kind == "fast-path" and "IRN-05" in other.text
    assert provider.calls(FAST) == 2


async def test_a_non_price_question_is_never_served_from_the_cache(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("lookup_price", {"product": "kettle"}, call_id="c1")],
            [function_call("view_basket", {}, call_id="v2")],
            [assistant_message("Your basket is empty.")],
        ],
    )
    await session.ask(KETTLE)
    reply = await session.ask("What's in my basket?")
    assert reply.kind == "reasoning"


async def test_editing_the_catalogue_retires_the_entry(make_session, provider, catalogue_file):
    """The one rule that keeps NFR-3 true: a cached figure describes one version of the file."""
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("lookup_price", {"product": "kettle"}, call_id="c1")],  # first ask
            [function_call("lookup_price", {"product": "kettle"}, call_id="c2")],  # after the edit
        ],
    )
    await session.ask(KETTLE)
    assert (await session.ask(KETTLE)).kind == "cached"

    raw = json.loads(catalogue_file.read_text(encoding="utf-8"))
    for item in raw["products"]:
        if item["sku"] == "KTL-01":
            item["price"] = 4500
    catalogue_file.write_text(json.dumps(raw), encoding="utf-8")

    after = await session.ask(KETTLE)
    assert after.kind == "fast-path", "a stale figure must never be served"
    assert "PKR 4,500" in after.text and "PKR 4,200" not in after.text
    assert provider.calls(FAST) == 2


async def test_the_cache_key_needs_the_fingerprint(make_context, catalogue_file):
    context = make_context()
    catalogue = load_catalogue(catalogue_file)
    cache = PriceCache()
    cache.put(catalogue, "kettle", "PKR 4,200")
    assert cache.get(load_catalogue(catalogue_file), KETTLE) == "PKR 4,200"

    raw = json.loads(catalogue_file.read_text(encoding="utf-8"))
    raw["products"][0]["price"] = 4500  # same products, different bytes
    catalogue_file.write_text(json.dumps(raw), encoding="utf-8")
    assert cache.get(load_catalogue(catalogue_file), KETTLE) is None


async def test_the_tool_is_the_only_writer(make_context):
    context = make_context()
    assert not context.price_cache.entries
    text = await invoke(lookup_price, context, product="kettle")
    catalogue = load_catalogue(context.catalogue_path)
    assert context.price_cache.get(catalogue, "kettle") == text


async def test_the_cost_line_reports_a_cached_turn_separately(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")]])
    await session.ask(KETTLE)
    await session.ask(KETTLE)
    line = session.context.ledger.cost_line()
    assert "1 cached at 0 calls" in line
    assert "1 fast-path" in line
    assert "1 model call(s)" in line, "two turns, one call"
