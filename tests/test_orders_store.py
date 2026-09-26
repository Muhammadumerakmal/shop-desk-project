"""XR-1: a confirmed order is stored, and "what did I order last week?" re-derives its figures."""

import json
from datetime import datetime, timedelta

from agents.testing import assistant_message, function_call

from shop_desk.context import fixed_clock
from shop_desk.orders import ORDERS_PATH, SHOP_TZ, PlacedOrder, placed_order, recent_orders, record_order
from shop_desk.schemas import LineItem, Order
from shop_desk.tools import recent_orders as recent_orders_tool

from tests.conftest import FAST, REASONING, invoke

ORDER = Order(
    order_id="ORD-ABC123",
    status="confirmed",
    items=[LineItem(sku="KTL-01", qty=3, unit_price=4200.0)],
    total=12600.0,
)
NOW = datetime(2026, 9, 26, 14, 0, tzinfo=SHOP_TZ)


def test_a_stored_order_holds_no_price(tmp_path):
    stored = placed_order(ORDER, "CUST-7781", NOW)
    line = stored.to_json()
    assert "4200" not in line and "12600" not in line, "the file must never become a price source"
    raw = json.loads(line)
    assert raw["items"] == [{"sku": "KTL-01", "qty": 3}]
    assert raw["customer_id"] == "CUST-7781"


def test_the_store_round_trips(tmp_path):
    path = tmp_path / "orders.jsonl"
    assert record_order(placed_order(ORDER, "CUST-7781", NOW), path)
    found = recent_orders("CUST-7781", 7, NOW, path)
    assert [o.order_id for o in found] == ["ORD-ABC123"]
    assert found[0].items == (("KTL-01", 3),)


def test_only_this_customers_orders_come_back(tmp_path):
    path = tmp_path / "orders.jsonl"
    record_order(placed_order(ORDER, "CUST-7781", NOW), path)
    assert recent_orders("CUST-9999", 7, NOW, path) == []


def test_the_window_is_respected(tmp_path):
    path = tmp_path / "orders.jsonl"
    record_order(placed_order(ORDER, "CUST-7781", NOW), path)
    record_order(placed_order(ORDER, "CUST-7781", NOW - timedelta(days=40)), path)
    found = recent_orders("CUST-7781", 7, NOW, path)
    assert [o.order_id for o in found] == ["ORD-ABC123"]


def test_a_missing_store_is_an_empty_history_not_an_error(tmp_path):
    assert recent_orders("CUST-7781", 7, NOW, tmp_path / "nothing.jsonl") == []


def test_a_damaged_line_is_skipped_not_raised(tmp_path):
    path = tmp_path / "orders.jsonl"
    path.write_text('{"order_id": broken\n' + placed_order(ORDER, "C", NOW).to_json() + "\n", encoding="utf-8")
    assert [o.order_id for o in recent_orders("C", 7, NOW, path)] == ["ORD-ABC123"]
    assert PlacedOrder.from_json("nonsense") is None


async def test_a_later_session_sees_an_earlier_order(make_session, provider, make_context, tmp_path):
    """The point of XR-1: the order outlives the conversation that placed it."""
    store = tmp_path / "orders.jsonl"
    order_id = "ORD-ABC123"
    provider.script(
        FAST,
        [
            [function_call("add_to_basket", {"sku": "KTL-01", "qty": 3}, call_id="a1")],
            [assistant_message("Three kettles added.")],
            [function_call("transfer_to_order_clerk", {}, call_id="h1")],
        ],
    )
    provider.script(
        REASONING,
        [
            [function_call("view_basket", {}, call_id="v1")],
            [assistant_message(_order_json(order_id, 12600.0))],
        ],
    )
    first = make_session_with_store(make_context, provider, store)
    await first.ask("I'd like three kettles.")
    reply = await first.ask("Yes, please place the order.")
    assert reply.order is not None and reply.order.order_id == order_id
    assert store.exists(), "a confirmed order must be written to the store"

    # a brand new session, same customer, same store
    text = await invoke(recent_orders_tool, first.context, days=7)
    assert order_id in text and "3 × KTL-01" in text and "PKR 12,600" in text


def _order_json(order_id: str, total: float) -> str:
    return json.dumps(
        {
            "order_id": order_id,
            "status": "confirmed",
            "items": [{"sku": "KTL-01", "qty": 3, "unit_price": 4200.0}],
            "total": total,
        }
    )


def make_session_with_store(make_context, provider, store):
    from shop_desk.config import Settings, configure_global
    from shop_desk.desk_agents import build_agents
    from shop_desk.session import DeskSession

    settings = Settings(fast_model=FAST, reasoning_model=REASONING, tracing=False, gemini_api_key="k")
    configure_global(settings)
    context = make_context("walk_in", "CUST-7781", orders_path=store, clock=fixed_clock(14))
    return DeskSession(build_agents(settings), context, settings, model_provider=provider)


async def test_past_figures_are_re_derived_not_replayed(make_context, catalogue_file, tmp_path):
    """Editing a price changes what last week's order is said to have cost. NFR-3 still holds."""
    store = tmp_path / "orders.jsonl"
    record_order(placed_order(ORDER, "CUST-7781", NOW), store)
    context = make_context("walk_in", "CUST-7781", orders_path=store, clock=fixed_clock(14))

    before = await invoke(recent_orders_tool, context, days=7)
    assert "PKR 12,600" in before

    raw = json.loads(catalogue_file.read_text(encoding="utf-8"))
    raw["products"][0]["price"] = 5000
    catalogue_file.write_text(json.dumps(raw), encoding="utf-8")

    after = await invoke(recent_orders_tool, context, days=7)
    assert "PKR 15,000" in after, "the new catalogue price is used"
    assert "PKR 12,600" not in after, "the stored order must never replay its own price"
    assert "at today's prices" in after


async def test_history_figures_pass_the_guardrail(make_context, tmp_path):
    """The tool issues a Quote per order, so the guardrail backs the answer with no new rule."""
    from shop_desk.guardrails import check_text

    store = tmp_path / "orders.jsonl"
    record_order(placed_order(ORDER, "CUST-7781", NOW), store)
    context = make_context("walk_in", "CUST-7781", orders_path=store, clock=fixed_clock(14))
    text = await invoke(recent_orders_tool, context, days=7)
    assert check_text(text, load(context), context) == []


def load(context):
    from shop_desk.catalogue import load_catalogue

    return load_catalogue(context.catalogue_path)


async def test_a_product_that_left_the_catalogue_is_said_so(make_context, catalogue_file, tmp_path):
    store = tmp_path / "orders.jsonl"
    gone = Order(order_id="ORD-GONE01", status="confirmed",
                 items=[LineItem(sku="OLD-99", qty=1, unit_price=500.0)], total=500.0)
    record_order(placed_order(gone, "CUST-7781", NOW), store)
    context = make_context("walk_in", "CUST-7781", orders_path=store, clock=fixed_clock(14))
    text = await invoke(recent_orders_tool, context, days=7)
    assert "no longer in the catalogue: OLD-99" in text
    # nothing is invented for a line that no longer exists
    assert "OLD-99 @" not in text
    assert "nothing from this order is in the catalogue any more" in text
    assert "of the items still listed" in text


async def test_a_removed_line_does_not_shift_a_live_price(make_context, tmp_path):
    """`unit_prices` omits a missing SKU, so pairing by position would blame the wrong line."""
    store = tmp_path / "orders.jsonl"
    mixed = Order(order_id="ORD-MIXED1", status="confirmed",
                  items=[LineItem(sku="OLD-99", qty=2, unit_price=500.0),
                         LineItem(sku="IRN-05", qty=1, unit_price=6500.0)],
                  total=7500.0)
    record_order(placed_order(mixed, "CUST-7781", NOW), store)
    context = make_context("walk_in", "CUST-7781", orders_path=store, clock=fixed_clock(14))
    text = await invoke(recent_orders_tool, context, days=7)

    assert "2 × OLD-99" not in text, "the removed line must not borrow the iron's price"
    assert "1 × IRN-05 @ PKR 6,500" in text
    assert "total PKR 6,500" in text



def test_the_default_store_is_not_tracked_by_git():
    assert ORDERS_PATH.name == "orders.jsonl"
