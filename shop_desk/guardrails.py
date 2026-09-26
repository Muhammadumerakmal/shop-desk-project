"""No invented prices: an output guardrail checked against catalogue.json (FR-6, NFR-3).

A data check, not a vibe check. The file is re-read on every check. An amount is allowed only if
it is a catalogue price or a figure recomputed, from the current file, out of a quote a tool issued
during this run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents import Agent, GuardrailFunctionOutput, RunContextWrapper, output_guardrail

from shop_desk.catalogue import Catalogue, CatalogueError, load_catalogue
from shop_desk.context import ShopContext
from shop_desk.pricing import LOYALTY_RATE, quote_figures
from shop_desk.schemas import Order

_NUM = r"(\d[\d,]*(?:\.\d+)?)"
AMOUNT_RE = re.compile(rf"(?:PKR|Rs\.?|₨)\s*{_NUM}|{_NUM}\s*(?:PKR|rupees)\b", re.IGNORECASE)
STOCK_RE = re.compile(r"\b(\d+)\s+(?:units?\s+|pieces?\s+)?(?:in stock|left|available)\b", re.IGNORECASE)
SKU_RE = re.compile(r"\b[A-Z]{3}-\d{2}\b")


@dataclass(frozen=True)
class Violation:
    kind: str  # "price" | "stock" | "sku" | "order" | "catalogue"
    value: str
    detail: str


def _number(text: str) -> float:
    return round(float(text.replace(",", "")), 2)


def allowed_amounts(catalogue: Catalogue, context: ShopContext) -> set[float]:
    allowed = {round(p.price, 2) for p in catalogue.products.values()}
    for quote in context.issued_quotes:
        allowed |= quote_figures(quote, catalogue).all()
    return allowed


def check_text(text: str, catalogue: Catalogue, context: ShopContext) -> list[Violation]:
    violations: list[Violation] = []
    amounts = allowed_amounts(catalogue, context)
    for match in AMOUNT_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if _number(raw) not in amounts:
            violations.append(Violation("price", raw, "amount not backed by the catalogue this run"))
    stocks = {p.stock for p in catalogue.products.values()}
    for match in STOCK_RE.finditer(text):
        if int(match.group(1)) not in stocks:
            violations.append(Violation("stock", match.group(1), "stock figure not in the catalogue"))
    for sku in SKU_RE.findall(text):
        if catalogue.get(sku) is None:
            violations.append(Violation("sku", sku, "SKU not in the catalogue"))
    return violations


def check_order(order: Order, catalogue: Catalogue, context: ShopContext) -> list[Violation]:
    violations: list[Violation] = []
    for item in order.items:
        product = catalogue.get(item.sku)
        if product is None:
            violations.append(Violation("sku", item.sku, "ordered SKU not in the catalogue"))
            continue
        valid_prices = {round(product.price, 2)}
        if context.tier == "regular":
            valid_prices.add(round(product.price * (1 - LOYALTY_RATE), 2))
        if round(item.unit_price, 2) not in valid_prices:
            violations.append(Violation("price", str(item.unit_price), f"unit price for {item.sku} is not the catalogue price"))
        if not product.in_stock:
            violations.append(Violation("order", item.sku, "out-of-stock item cannot be sold"))
        elif not 1 <= item.qty <= product.stock:
            violations.append(Violation("order", item.sku, f"quantity {item.qty} is outside 1..{product.stock}"))
    return violations


def check_output(output: Any, context: ShopContext) -> list[Violation]:
    try:
        catalogue = load_catalogue(context.catalogue_path)
    except CatalogueError as exc:
        return [Violation("catalogue", "", f"cannot verify against the catalogue: {exc}")]
    if isinstance(output, Order):
        return check_order(output, catalogue, context)
    return check_text(str(output), catalogue, context)


@output_guardrail(name="catalogue_truth")
async def catalogue_guardrail(
    ctx: RunContextWrapper[ShopContext], agent: Agent[ShopContext], output: Any
) -> GuardrailFunctionOutput:
    violations = check_output(output, ctx.context)
    return GuardrailFunctionOutput(
        output_info={"agent": agent.name, "violations": [v.__dict__ for v in violations]},
        tripwire_triggered=bool(violations),
    )
