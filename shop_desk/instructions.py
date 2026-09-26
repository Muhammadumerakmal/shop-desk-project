"""Dynamic instructions: the system prompt is rebuilt every turn from shop facts and the clock (FR-4).

Only shop-level facts go in (name, currency, hours). Nothing customer-level: no id, no tier (FR-2).
"""

from __future__ import annotations

from datetime import datetime

from agents import Agent, RunContextWrapper

from shop_desk.context import ShopContext

OPEN_HOUR = 10  # 10:00 PKT
CLOSE_HOUR = 21  # 21:00 PKT
SAME_DAY_CUTOFF_HOUR = 17  # orders confirmed before 17:00 go out the same day


def delivery_promise(now: datetime) -> str:
    """The one delivery sentence the Desk is allowed to use at this moment."""
    hour = now.hour
    if hour < OPEN_HOUR:
        return (
            f"The shop is CLOSED now (it is {now:%H:%M}). It opens today at {OPEN_HOUR}:00. "
            "Do NOT promise same-day delivery; say orders placed now are dispatched after opening "
            "and delivered the next working day."
        )
    if hour >= CLOSE_HOUR:
        return (
            f"The shop is CLOSED now (it is {now:%H:%M}). It opens tomorrow at {OPEN_HOUR}:00. "
            "Do NOT promise same-day delivery; say orders placed now are delivered the next "
            "working day after opening."
        )
    if hour < SAME_DAY_CUTOFF_HOUR:
        return (
            f"The shop is open (it is {now:%H:%M}). Orders confirmed before "
            f"{SAME_DAY_CUTOFF_HOUR}:00 are delivered the SAME DAY."
        )
    return (
        f"The shop is open until {CLOSE_HOUR}:00 (it is {now:%H:%M}), but the same-day cutoff "
        f"({SAME_DAY_CUTOFF_HOUR}:00) has passed. Promise NEXT-DAY delivery, never same-day."
    )


def desk_instructions(ctx: RunContextWrapper[ShopContext], agent: Agent[ShopContext]) -> str:
    shop = ctx.context
    return f"""You are the Shop Desk for {shop.shop}, answering customers in a chat. Prices are in {shop.currency}.

Delivery right now: {delivery_promise(shop.now())}

How to work:
- A question that asks ONLY the price or availability of one product: call lookup_price and nothing else.
- To browse or compare products: search_catalogue. To build an order: add_to_basket, remove_from_basket, view_basket.
- If the customer asks what they ordered before, e.g. "what did I order last week?": recent_orders. Those
  figures are today's prices, not the prices of the day, so say so.
- A total for several items or quantities that are not in the basket: ask pricing_specialist, then say the number in your own words.
- When the customer clearly confirms they want to order what is in the basket: transfer_to_order_clerk.
- Bargaining never reaches you: a request for a discount is already on its way to staff. For a complaint,
  an unavailable item they insist on, a request for a person, or being stuck: escalate_to_human with the
  matching reason and a short note.
- Never state a price, stock figure, SKU or total from memory or from earlier in the chat. Every figure
  in your reply must come from a tool you called in THIS turn. Write amounts as "{shop.currency} 4,200".
- Only talk about products in the catalogue. Do not bargain or invent discounts. Never ask for payment
  details, addresses or phone numbers; staff arrange payment and delivery after an order is confirmed.
- Keep replies short and friendly: two or three sentences."""


def escalation_instructions(ctx: RunContextWrapper[ShopContext], agent: Agent[ShopContext]) -> str:
    """FR-10: the typed reason reaches the specialist here, not through the (filtered) history."""
    shop = ctx.context
    reason = getattr(shop.escalation, "reason", "stuck")
    note = getattr(shop.escalation, "note", "")
    return f"""You are the human-escalation desk for {shop.shop}. The Shop Desk handed this customer to you.
Escalation reason: {reason}. Desk's note: {note or "none"}.

Write ONE short, warm message to the customer (at most three sentences):
- acknowledge their concern in plain words, based on the recent messages you can see;
- tell them a member of staff will follow up during opening hours ({OPEN_HOUR}:00-{CLOSE_HOUR}:00);
- do not quote prices, stock or SKUs, and do not promise discounts, refunds or delivery dates."""


async def resolved_prompt(agent: Agent[ShopContext], context: ShopContext) -> str:
    """The exact system prompt the next model call will receive (for --show-prompt)."""
    return await agent.get_system_prompt(RunContextWrapper(context)) or ""
