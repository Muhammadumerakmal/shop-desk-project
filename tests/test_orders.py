"""FR-5: a confirmed order is a typed object and its total is recomputed in Python."""

import json

from agents.testing import assistant_message, function_call

from shop_desk.schemas import LineItem, Order, check_order_total

from tests.conftest import FAST, REASONING


def test_correct_total_passes():
    order = Order(order_id="ORD-1", status="confirmed", items=[LineItem(sku="KTL-01", qty=3, unit_price=4200)], total=12600)
    assert not check_order_total(order).mismatch


def test_planted_mismatch_is_caught():
    order = Order(
        order_id="ORD-1",
        status="confirmed",
        items=[LineItem(sku="KTL-01", qty=3, unit_price=4200), LineItem(sku="IRN-05", qty=1, unit_price=6500)],
        total=18000,  # planted: the real total is 19,100
    )
    check = check_order_total(order)
    assert check.mismatch and check.recomputed_total == 19100
    assert "corrected from PKR 18,000 to PKR 19,100" in check.note("PKR")


def _order_json(order_id: str, total: float) -> str:
    return json.dumps(
        {"order_id": order_id, "status": "confirmed", "items": [{"sku": "KTL-01", "qty": 3, "unit_price": 4200}], "total": total}
    )


async def _confirm(session, provider, total: float):
    session.context.basket["KTL-01"] = 3
    order_id = session.context.draft_order_id
    provider.script(FAST, [[function_call("transfer_to_order_clerk", {}, call_id="h1")]])
    provider.script(
        REASONING,
        [
            [function_call("view_basket", {}, call_id="v1")],
            [assistant_message(_order_json(order_id, total))],
        ],
    )
    return order_id, await session.ask("Yes, please place the order.")


async def test_confirmation_produces_a_typed_order(make_session, provider):
    session = make_session()
    order_id, reply = await _confirm(session, provider, 12600)
    assert isinstance(reply.order, Order) and reply.order.order_id == order_id
    assert reply.order.status == "confirmed" and not reply.order_check.mismatch
    assert "Total: PKR 12,600" in reply.text
    assert session.context.basket == {} and session.context.orders == [reply.order]
    assert provider.calls(REASONING) == 2  # the Order clerk ran on the reasoning model


async def test_model_total_mismatch_is_reported_not_accepted(make_session, provider):
    session = make_session()
    _, reply = await _confirm(session, provider, 12000)
    assert reply.order_check.mismatch
    assert reply.order.total == 12600
    assert "corrected from PKR 12,000 to PKR 12,600" in reply.text
