"""FR-6 / NFR-3: no price, stock figure or SKU reaches a customer unless the catalogue backs it."""

import json

from agents.testing import assistant_message, function_call

from shop_desk.context import Quote
from shop_desk.guardrails import check_output
from shop_desk.schemas import LineItem, Order
from shop_desk.session import REFUSAL

from tests.conftest import FAST, REASONING

ANSWER = "The Electric kettle 1.7L (KTL-01) is PKR 4,200 and we have 12 in stock."


def _edit_price(catalogue_file, sku: str, price: float) -> None:
    data = json.loads(catalogue_file.read_text())
    for product in data["products"]:
        if product["sku"] == sku:
            product["price"] = price
    catalogue_file.write_text(json.dumps(data))


def test_true_answer_passes(make_context):
    assert check_output(ANSWER, make_context()) == []


def test_editing_the_catalogue_makes_the_same_answer_fail(make_context, catalogue_file):
    ctx = make_context()
    assert check_output(ANSWER, ctx) == []
    _edit_price(catalogue_file, "KTL-01", 4500)
    violations = check_output(ANSWER, ctx)
    assert [v.kind for v in violations] == ["price"]


def test_invented_amount_stock_and_sku_fail(make_context):
    ctx = make_context()
    kinds = {v.kind for v in check_output("Kettle KTL-99 is Rs 3,999 with 40000 in stock.", ctx)}
    assert kinds == {"price", "stock", "sku"}


def test_only_a_standalone_currency_word_counts_as_an_amount(make_context):
    """A word that merely ends in `Rs` is not a price: "hours 10" is not "Rs 10".

    Plausible shop sentences end in `rs` and are followed by a number — "opening hours 10 to 10",
    "3 orders 2 days ago". Reading the tail of those words as a currency token refused the
    customer's own wording and then paid for a re-quote to say the same thing again.
    """
    ctx = make_context()
    for innocent in (
        "Opening hours 10 to 10, every day.",
        "We have served 2 orders 3 days ago.",
        "You have 3 orders 2 days ago and 1 last week.",
    ):
        assert check_output(innocent, ctx) == [], innocent


def test_every_real_currency_form_is_still_caught(make_context):
    """The lookbehind must not weaken FR-6: a real amount is still refused, in every written form."""
    ctx = make_context()
    for text in (
        "The kettle is Rs 5,000 today.",
        "Our customers Rs 5,000 orders are rare.",
        "That is PKR 5,000 for the kettle.",
        "Only Rs. 999 for the lot.",
        "The kettle is \u20a8 5,000 exactly.",
    ):
        assert check_output(text, ctx), text
    # and a catalogue-backed figure in each form still passes
    for text in ("The kettle is PKR 4,200.", "The kettle is Rs 4,200.", "The kettle is \u20a8 4,200."):
        assert check_output(text, ctx) == [], text


def test_an_unreadable_catalogue_refuses_figures_but_not_a_handoff(make_context, catalogue_file):
    """FR-6 with nothing to check against: refuse a figure, do not strand a customer.

    An unreadable catalogue cannot back an amount, so an answer quoting one is still refused, and a
    typed order — which is nothing but figures — is refused outright. But a staff handoff quotes no
    figure at all, so refusing it would cost the customer their escalation for no truth lost.
    """
    ctx = make_context()
    catalogue_file.write_text("{broken")

    handoff = "I'm sorry about that. A member of staff will follow up with you."
    assert check_output(handoff, ctx) == [], "nothing here needs the catalogue to be verified"

    assert [v.kind for v in check_output("The kettle is PKR 4,200.", ctx)] == ["catalogue"]
    order = Order(order_id="O", status="confirmed", items=[LineItem(sku="KTL-01", qty=1, unit_price=4200)], total=4200)
    assert [v.kind for v in check_output(order, ctx)] == ["catalogue"]


def test_tool_issued_totals_pass_and_are_recomputed_from_the_file(make_context, catalogue_file):
    ctx = make_context()
    text = "Three kettles come to PKR 12,600."
    assert check_output(text, ctx)  # not issued this run -> refused
    ctx.issued_quotes.append(Quote(lines=(("KTL-01", 3),)))
    assert check_output(text, ctx) == []
    _edit_price(catalogue_file, "KTL-01", 4300)  # catalogue edited mid-conversation
    assert check_output(text, ctx)  # the stale total no longer recomputes


def test_out_of_stock_item_is_never_sold(make_context):
    order = Order(order_id="ORD-1", status="confirmed", items=[LineItem(sku="FAN-22", qty=1, unit_price=9800)], total=9800)
    assert any(v.kind == "order" for v in check_output(order, make_context()))


def test_order_over_stock_or_wrong_price_fails(make_context):
    ctx = make_context()
    too_many = Order(order_id="O", status="confirmed", items=[LineItem(sku="HTR-07", qty=4, unit_price=11500)], total=46000)
    cheap = Order(order_id="O", status="confirmed", items=[LineItem(sku="KTL-01", qty=1, unit_price=3000)], total=3000)
    assert check_output(too_many, ctx) and check_output(cheap, ctx)


def test_regular_customer_may_have_the_loyalty_unit_price(make_context):
    order = Order(order_id="O", status="confirmed", items=[LineItem(sku="KTL-01", qty=1, unit_price=3990)], total=3990)
    assert check_output(order, make_context(tier="regular")) == []
    assert check_output(order, make_context(tier="walk_in"))


async def test_refusal_is_retried_on_the_reasoning_model_then_polite(make_session, provider):
    session = make_session()
    provider.script(FAST, [[assistant_message("The kettle is PKR 3,999.")]])  # invented
    provider.script(REASONING, [[assistant_message("The kettle is PKR 3,500.")]])  # still invented
    reply = await session.ask("How much is the kettle?")
    assert reply.text == REFUSAL and reply.kind == "refused"
    assert "reasoning-model" in provider.requested  # the run-level re-quote happened


async def test_requote_can_recover_with_a_true_answer(make_session, provider):
    session = make_session()
    provider.script(FAST, [[assistant_message("Three kettles are PKR 12,000.")]])
    provider.script(
        REASONING,
        [
            [function_call("view_basket", {}, call_id="v1")],
            [assistant_message("Your basket is empty. A kettle is PKR 4,200.")],
        ],
    )
    reply = await session.ask("How much are three kettles?")
    assert reply.requoted and reply.kind == "reasoning"
    assert "PKR 4,200" in reply.text


async def test_basket_side_effects_are_undone_before_the_requote(make_session, provider):
    session = make_session()
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a1")],
            [assistant_message("Added, total PKR 1.")],
        ],
    )
    provider.script(
        REASONING,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a2")],
            [assistant_message("Added three kettles to your basket.")],
        ],
    )
    await session.ask("Add three kettles")
    assert session.context.basket == {"KTL-01": 3}  # not 6
