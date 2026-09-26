"""Function tools: the only road to the catalogue (FR-1, FR-2, FR-3, NFR-4).

Every tool takes `RunContextWrapper[ShopContext]` first (hidden from the schema) and returns a
sentence. `@never_raises` is the backstop so that no tool exception reaches the runner.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from agents import RunContextWrapper, function_tool

from shop_desk.catalogue import CatalogueError, load_catalogue
from shop_desk.context import Quote, ShopContext

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
    return "\n".join(lines)


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
    rows, total, quote_lines = [], 0.0, []
    for sku, qty in basket.items():
        product = catalogue.get(sku)
        if product is None:
            rows.append(f"{sku}: no longer in the catalogue, remove it before ordering")
            continue
        line_total = product.price * qty
        total += line_total
        quote_lines.append((sku, qty))
        stock_note = "" if qty <= product.stock else f" (only {product.stock} in stock now)"
        rows.append(
            f"{sku} · {product.name} · {qty} × {catalogue.money(product.price)} = "
            f"{catalogue.money(line_total)}{stock_note}"
        )
    ctx.context.issued_quotes.append(Quote(lines=tuple(quote_lines)))
    rows.append(f"Total: {catalogue.money(total)}")
    rows.append(f"Draft order id: {ctx.context.draft_order_id}")
    return "\n".join(rows)


DESK_TOOLS = [lookup_price, search_catalogue, add_to_basket, remove_from_basket, view_basket]
