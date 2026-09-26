"""Function tools: the only road to the catalogue (FR-1, FR-2, FR-3, NFR-4).

Every tool takes `RunContextWrapper[ShopContext]` first (hidden from the schema) and returns a
sentence. `@never_raises` is the backstop so that no tool exception reaches the runner.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from agents import AgentBase, RunContextWrapper, function_tool

from shop_desk.catalogue import CatalogueError, load_catalogue
from shop_desk.context import Quote, ShopContext
from shop_desk.orders import recent_orders as recent_orders_for
from shop_desk.pricing import LOYALTY_RATE, quote_figures
from shop_desk.schemas import QuoteLine

log = logging.getLogger(__name__)

FAST_PATH_TOOL = "lookup_price"


def never_raises(fn: Callable[..., str]) -> Callable[..., str]:
    """Turn any failure inside a tool into a sentence the model can use (NFR-4)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except CatalogueError as exc:
            log.warning("catalogue error in %s: %s", fn.__name__, exc)
            return f"The catalogue is unavailable right now ({exc}). Please try again shortly."
        except Exception as exc:  # the backstop: a tool must not raise into the runner
            log.exception("tool %s failed", fn.__name__)
            return f"That request could not be completed ({type(exc).__name__}). Please check the SKU and quantity."

    return wrapper


def _catalogue(ctx: RunContextWrapper[ShopContext]):
    return load_catalogue(ctx.context.catalogue_path)


@function_tool(name_override=FAST_PATH_TOOL)
@never_raises
def lookup_price(ctx: RunContextWrapper[ShopContext], product: str) -> str:
    """Price and stock of ONE product, for a plain question like "what does the kettle cost?".

    Your turn ends with this tool's text as the answer, so use it only when the customer asks
    only for the price or availability of a product. Do not use it for orders or totals.

    Args:
        product: product name words or a SKU, e.g. "kettle" or "KTL-01".
    """
    ctx.context.fast_path_used = True
    catalogue = _catalogue(ctx)
    matches = catalogue.find(product)
    if not matches:
        return (
            f"Sorry, I couldn't find \"{product}\" in the {catalogue.shop} catalogue. "
            "Ask me what we stock and I'll list it."
        )
    lines = []
    for p in matches[:3]:
        if p.in_stock:
            lines.append(f"{p.name} ({p.sku}) is {catalogue.money(p.price)} — {p.stock} in stock.")
        else:
            lines.append(f"{p.name} ({p.sku}) is {catalogue.money(p.price)}, but it is out of stock right now.")
    text = "\n".join(lines)
    # XR-3: the tool is the only writer of the price cache, so an entry can never disagree with
    # what this tool would say now. The key carries the catalogue fingerprint, so an edit to the
    # file retires the entry instead of serving a stale figure (NFR-3).
    ctx.context.price_cache.put(catalogue, product, text)
    return text


@function_tool
@never_raises
def search_catalogue(ctx: RunContextWrapper[ShopContext], query: str) -> str:
    """List catalogue products with SKU, price and availability.

    Args:
        query: words to match in product names, or "" to list everything.
    """
    catalogue = _catalogue(ctx)
    products = catalogue.find(query) if query.strip() else list(catalogue.products.values())
    if not products:
        return f"No products match \"{query}\". Call search_catalogue with \"\" to see everything."
    rows = []
    for p in products:
        availability = f"{p.stock} in stock" if p.in_stock else "out of stock"
        rows.append(f"{p.sku} · {p.name} · {catalogue.money(p.price)} · {availability}")
    return "\n".join(rows)


@function_tool
@never_raises
def add_to_basket(ctx: RunContextWrapper[ShopContext], sku: str, qty: int) -> str:
    """Add a quantity of one catalogue SKU to the customer's basket.

    Args:
        sku: the catalogue SKU, e.g. "KTL-01".
        qty: how many units to add (at least 1).
    """
    catalogue = _catalogue(ctx)
    product = catalogue.get(sku)
    if product is None:
        return f"There is no product with SKU {sku!r}. Use search_catalogue to find the right SKU."
    if qty < 1:
        return "The quantity must be at least 1."
    if not product.in_stock:
        return f"{product.name} ({product.sku}) is out of stock, so it cannot be added to the basket."
    basket = ctx.context.basket
    wanted = basket.get(product.sku, 0) + qty
    if wanted > product.stock:
        return (
            f"Only {product.stock} available for {product.name} ({product.sku}); "
            f"the basket already has {basket.get(product.sku, 0)}."
        )
    basket[product.sku] = wanted
    return (
        f"Added {qty} × {product.name} ({product.sku}) at {catalogue.money(product.price)} each. "
        f"The basket now has {len(basket)} line(s). Call view_basket for the total."
    )


@function_tool
@never_raises
def remove_from_basket(ctx: RunContextWrapper[ShopContext], sku: str) -> str:
    """Remove one SKU completely from the customer's basket.

    Args:
        sku: the catalogue SKU to remove.
    """
    key = sku.strip().upper()
    if ctx.context.basket.pop(key, None) is None:
        return f"{key} is not in the basket."
    return f"Removed {key} from the basket."


@function_tool
@never_raises
def view_basket(ctx: RunContextWrapper[ShopContext]) -> str:
    """Show the basket with current catalogue prices, line totals, the total and the draft order id."""
    catalogue = _catalogue(ctx)
    basket = ctx.context.basket
    if not basket:
        return "The basket is empty."
    rows, quote_lines = [], []
    for sku, qty in basket.items():
        product = catalogue.get(sku)
        if product is None:
            rows.append(f"{sku}: no longer in the catalogue, remove it before ordering")
            continue
        quote_lines.append((sku, qty))
        stock_note = "" if qty <= product.stock else f" (only {product.stock} in stock now)"
        rows.append(
            f"{sku} · {product.name} · {qty} × {catalogue.money(product.price)} = "
            f"{catalogue.money(product.price * qty)}{stock_note}"
        )
    quote = Quote(lines=tuple(quote_lines))
    ctx.context.issued_quotes.append(quote)
    rows.append(f"Total: {catalogue.money(quote_figures(quote, catalogue).total)}")
    rows.append(f"Draft order id: {ctx.context.draft_order_id}")
    return "\n".join(rows)


# --- XR-1: what this customer ordered before, with every figure re-derived from the file ------------


@function_tool
@never_raises
def recent_orders(ctx: RunContextWrapper[ShopContext], days: int = 7) -> str:
    """This customer's own past orders, most recent first, with the totals they cost today.

    Use it when the customer asks what they ordered before, e.g. "what did I order last week?".

    Args:
        days: how far back to look, in days (default 7).
    """
    window = min(max(days, 1), 365)
    orders = recent_orders_for(ctx.context.customer_id, window, ctx.context.now(), ctx.context.orders_path)
    if not orders:
        return f"You have no orders on record in the last {window} day(s)."

    now = ctx.context.now()
    catalogue = _catalogue(ctx)
    rows = []
    for order in orders:
        quote = Quote(lines=order.items)
        ctx.context.issued_quotes.append(quote)  # so the guardrail backs these figures (NFR-3)
        figures = quote_figures(quote, catalogue)
        gone = set(figures.missing)
        # `quote_figures` drops a missing SKU entirely, so its `unit_prices` is aligned to the
        # *surviving* lines. Pairing it with `order.items` directly would shift a live price onto
        # the line before a removed one, so the surviving lines are zipped instead.
        live = [(sku, qty) for sku, qty in order.items if sku not in gone]
        prices = {sku: price for (sku, _), price in zip(live, figures.unit_prices)}
        detail = "; ".join(
            f"{qty} × {sku} @ {catalogue.money(prices[sku])}"
            for sku, qty in live
        ) or "nothing from this order is in the catalogue any more"
        total = f"total {catalogue.money(figures.total)} at today's prices"
        if gone:
            total += " (of the items still listed)"
        row = f"{order.order_id} · {order.age_days(now)} day(s) ago · {detail} · {total}"
        if gone:
            row += f" · no longer in the catalogue: {', '.join(sku for sku, _ in order.items if sku in gone)}"
        rows.append(row)
    return "\n".join(rows)


# --- FR-7: a tool that is earned, and a tool that is off -------------------------------------

def is_regular_customer(ctx: RunContextWrapper[ShopContext], agent: AgentBase) -> bool:
    """Evaluated every run: the tool is only offered to regular customers."""
    return ctx.context.tier == "regular"


@function_tool(is_enabled=is_regular_customer)
@never_raises
def loyalty_discount(ctx: RunContextWrapper[ShopContext], sku: str, qty: int) -> str:
    """The regular-customer price (5% loyalty discount) for a quantity of one SKU.

    Args:
        sku: the catalogue SKU.
        qty: number of units, at least 1.
    """
    if ctx.context.tier != "regular":  # defence in depth; the schema already hides this tool
        return "Loyalty pricing is only available to regular customers."
    catalogue = _catalogue(ctx)
    product = catalogue.get(sku)
    if product is None:
        return f"There is no product with SKU {sku!r}."
    if qty < 1:
        return "The quantity must be at least 1."
    quote = Quote(lines=((product.sku, qty),), discount_rate=LOYALTY_RATE)
    ctx.context.issued_quotes.append(quote)
    figures = quote_figures(quote, catalogue)
    return (
        f"Regular-customer price for {qty} × {product.name} ({product.sku}): "
        f"{catalogue.money(figures.total)} (5% off {catalogue.money(figures.subtotal)}, "
        f"saving {catalogue.money(figures.discount)})."
    )


SEASONAL_TOOLS_ENABLED = False  # Eid season is over: switched off statically


@function_tool(is_enabled=SEASONAL_TOOLS_ENABLED)
@never_raises
def eid_gift_wrap(ctx: RunContextWrapper[ShopContext], sku: str) -> str:
    """Add free Eid gift wrapping to a basket item (seasonal service).

    Args:
        sku: the basket SKU to gift wrap.
    """
    return f"{sku} will be gift wrapped for Eid."


# --- FR-8: the specialist's arithmetic tool ---------------------------------------------------

@function_tool
@never_raises
def quote(ctx: RunContextWrapper[ShopContext], lines: list[QuoteLine]) -> str:
    """Exact total for quantities of catalogue SKUs, computed from current catalogue prices.

    Args:
        lines: the SKUs and quantities to price.
    """
    catalogue = _catalogue(ctx)
    bad = [line.sku for line in lines if catalogue.get(line.sku) is None]
    if bad:
        return f"Unknown SKU(s): {', '.join(bad)}. Use search_catalogue to find the right SKU."
    if not lines or any(line.qty < 1 for line in lines):
        return "Every line needs a quantity of at least 1."
    q = Quote(lines=tuple((catalogue.get(line.sku).sku, line.qty) for line in lines))
    ctx.context.issued_quotes.append(q)
    figures = quote_figures(q, catalogue)
    breakdown = "; ".join(
        f"{qty} × {sku} @ {price:g} = {line_total:g}"
        for (sku, qty), price, line_total in zip(q.lines, figures.unit_prices, figures.line_totals)
    )
    return f"TOTAL={figures.total:g}\n{breakdown}"


DESK_TOOLS = [
    lookup_price,
    search_catalogue,
    add_to_basket,
    remove_from_basket,
    view_basket,
    recent_orders,
    loyalty_discount,
    eid_gift_wrap,
]
SPECIALIST_TOOLS = [search_catalogue, quote]
