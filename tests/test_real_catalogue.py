"""XR-4: any catalogue in the same shape works, and refusals are counted."""

import json

from agents.testing import assistant_message, function_call

from shop_desk.catalogue import load_catalogue
from shop_desk.tools import lookup_price

from tests.conftest import FAST, REASONING, invoke

OTHER_SHOP = {
    "shop": "Sana Mobile Corner",
    "currency": "PKR",
    "products": [
        {"sku": "PHN-01", "name": "Smartphone 128GB", "price": 45000, "stock": 3},
        {"sku": "PWR-09", "name": "Power bank 20000mAh", "price": 4500, "stock": 0},
    ],
}


def write_other(tmp_path):
    path = tmp_path / "sana.json"
    path.write_text(json.dumps(OTHER_SHOP), encoding="utf-8")
    return path


def test_another_catalogue_loads_with_no_code_change(tmp_path):
    catalogue = load_catalogue(write_other(tmp_path))
    assert catalogue.shop == "Sana Mobile Corner"
    assert [p.sku for p in catalogue.products.values()] == ["PHN-01", "PWR-09"]
    assert catalogue.fingerprint


async def test_the_desk_answers_only_from_the_other_catalogue(make_context, tmp_path):
    """Same Desk, same tools, same guardrail — a different shop, with no code change."""
    context = make_context("walk_in", catalogue_path=write_other(tmp_path))

    assert await invoke(lookup_price, context, product="smartphone") == (
        "Smartphone 128GB (PHN-01) is PKR 45,000 — 3 in stock."
    )
    assert "out of stock right now" in await invoke(lookup_price, context, product="power bank")
    # a SKU from the old shop simply does not exist here
    assert "couldn't find" in await invoke(lookup_price, context, product="KTL-01")


async def test_the_other_catalogue_is_what_the_guardrail_checks(make_context, tmp_path):
    """A price from the *old* shop must not survive a switch to the new one."""
    from shop_desk.guardrails import check_text

    context = make_context("walk_in", catalogue_path=write_other(tmp_path))
    catalogue = load_catalogue(context.catalogue_path)
    assert check_text("The kettle is PKR 4,200.", catalogue, context), "42,000 is not a phone price"
    assert check_text("The smartphone is PKR 45,000.", catalogue, context) == []


def test_fingerprints_differ_between_catalogues(tmp_path, catalogue_file):
    assert load_catalogue(catalogue_file).fingerprint != load_catalogue(write_other(tmp_path)).fingerprint


async def test_a_guardrail_refusal_is_counted(make_session, provider):
    """A block is the one event that makes a turn cost twice, so it lands on the cost line."""
    session = make_session()
    provider.script(FAST, [[assistant_message("The kettle is PKR 1,000, a special for you.")]])
    provider.script(REASONING, [[function_call("lookup_price", {"product": "kettle"}, call_id="q1")]])

    reply = await session.ask("What does the kettle cost?")

    assert reply.requoted, "the session should have retried once on the reasoning model"
    assert "PKR 4,200" in reply.text, "and the retry answered from the catalogue"
    assert session.context.ledger.guardrail_blocks == 1
    assert session.context.ledger.requotes == 1
    assert "guardrail blocked 1 answer(s), 1 re-quoted" in session.context.ledger.cost_line()


async def test_a_second_refusal_is_a_block_but_not_a_second_requote(make_session, provider):
    """XR-4 counts refusals and re-quotes, and only one re-quote was ever spent here.

    The first answer is blocked and retried once; the retry is blocked too and the customer is
    politely refused. That is two refused answers and one retry, so the two counts must differ.
    """
    from shop_desk.session import REFUSAL

    session = make_session()
    provider.script(FAST, [[assistant_message("The kettle is PKR 3,999.")]])  # invented
    provider.script(REASONING, [[assistant_message("Still PKR 3,500.")]])  # invented again

    reply = await session.ask("How much is the kettle?")

    assert reply.text == REFUSAL and reply.kind == "refused"
    assert session.context.ledger.guardrail_blocks == 2
    assert session.context.ledger.requotes == 1
    assert "guardrail blocked 2 answer(s), 1 re-quoted" in session.context.ledger.cost_line()


async def test_a_clean_conversation_reports_no_blocks(make_session, provider):
    session = make_session()
    provider.script(FAST, [[function_call("lookup_price", {"product": "kettle"}, call_id="c1")]])
    await session.ask("What does the kettle cost?")
    assert session.context.ledger.guardrail_blocks == 0
    assert "guardrail blocked" not in session.context.ledger.cost_line()


def test_the_cli_exposes_the_catalogue_and_the_order_store():
    from shop_desk.cli import parse_args

    args = parse_args(["--catalogue", "mine.json", "--orders", "mine.jsonl"])
    assert args.catalogue == "mine.json" and args.orders == "mine.jsonl"
    assert parse_args([]).catalogue is None and parse_args([]).orders is None
