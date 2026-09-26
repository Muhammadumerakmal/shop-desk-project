"""NFR-4: tools meeting bad data return a sentence; nothing raises into the runner."""

import json

from shop_desk.tools import add_to_basket, lookup_price, remove_from_basket, search_catalogue, view_basket

from tests.conftest import invoke


async def test_lookup_price_sentence(make_context):
    ctx = make_context()
    text = await invoke(lookup_price, ctx, product="kettle")
    assert text == "Electric kettle 1.7L (KTL-01) is PKR 4,200 — 12 in stock."
    assert ctx.fast_path_used


async def test_out_of_stock_and_unknown(make_context):
    ctx = make_context()
    assert "out of stock" in await invoke(lookup_price, ctx, product="pedestal fan")
    assert "couldn't find" in await invoke(lookup_price, ctx, product="playstation")


async def test_basket_rules(make_context):
    ctx = make_context()
    assert "no product" in (await invoke(add_to_basket, ctx, sku="ZZZ-99", qty=1)).lower()
    assert "at least 1" in await invoke(add_to_basket, ctx, sku="KTL-01", qty=0)
    assert "out of stock" in await invoke(add_to_basket, ctx, sku="FAN-22", qty=1)
    assert "Only 3 available" in await invoke(add_to_basket, ctx, sku="HTR-07", qty=4)
    assert ctx.basket == {}
    await invoke(add_to_basket, ctx, sku="ktl-01", qty=3)
    basket = await invoke(view_basket, ctx)
    assert "3 × PKR 4,200 = PKR 12,600" in basket and "Total: PKR 12,600" in basket
    assert ctx.issued_quotes, "view_basket must record its derivation for the guardrail"
    assert "Removed" in await invoke(remove_from_basket, ctx, sku="KTL-01")


async def test_broken_catalogue_is_a_sentence_not_an_exception(make_context, catalogue_file):
    ctx = make_context()
    catalogue_file.write_text("{broken")
    text = await invoke(search_catalogue, ctx, query="")
    assert "unavailable" in text


async def test_bad_catalogue_values_are_a_sentence(make_context, catalogue_file):
    ctx = make_context()
    data = json.loads(catalogue_file.read_text())
    data["products"][0]["price"] = "free"
    catalogue_file.write_text(json.dumps(data))
    assert "unavailable" in await invoke(lookup_price, ctx, product="kettle")
