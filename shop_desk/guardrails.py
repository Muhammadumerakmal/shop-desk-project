"""No invented prices: an output guardrail checked against catalogue.json (FR-6, NFR-3).

A data check, not a vibe check. The file is re-read on every check. An amount is allowed only if
it is a catalogue price or a figure recomputed, from the current file, out of a quote a tool issued
during this run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    input_guardrail,
    output_guardrail,
)

from shop_desk.catalogue import Catalogue, CatalogueError, load_catalogue
from shop_desk.context import ShopContext
from shop_desk.pricing import LOYALTY_RATE, quote_figures
from shop_desk.schemas import EscalationReason, Order

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


# --- XR-2: an input guardrail, so a haggler never reaches the model ------------------------------
#
# The Desk already knows to escalate a bargainer, and it will do so — but only after paying for the
# call that produced the decision. This guardrail runs first, on the customer's own words, and hands
# the turn straight to the escalation agent. A haggler therefore costs one escalation call and no
# Desk call at all, which is the cheapest outcome available and the reason it is worth a guardrail.

# Deliberately narrow, because a false positive sends an innocent customer to a member of staff.
# "off" on its own is not a trigger ("turn the wrapping off"), and asking about a discount *policy*
# is a question, not a demand, so it reaches the Desk.
BARGAIN_PATTERNS = (
    re.compile(r"\d{1,2}\s*%\s*(?:off|discount|less)", re.IGNORECASE),
    re.compile(r"\b(?:haggl\w*|bargain\w*|sharpen the price)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:cheaper|less expensive|lower price|lower the price|best price|better price|"
        r"price match|match (?:the|your|that) price|beat (?:the|your|that))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:give|take|knock|throw)\s+(?:me\s+)?(?:a\s+|an\s+)?\d{0,2}\s*%?\s*(?:off|discount)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfree\s+(?:gift|delivery|shipping|extra|unit|product)\b", re.IGNORECASE),
    re.compile(r"\b(?:come down|bring (?:it|that|them) down|reduce the price)\b", re.IGNORECASE),
)
DISCOUNT_WORD_RE = re.compile(r"\b(?:discount|discounts|rebate|rebates|coupon|voucher|promo code)\b", re.IGNORECASE)
POLICY_QUESTION_RE = re.compile(r"\b(?:policy|policies|do you (?:have|offer)|is there|are there)\b", re.IGNORECASE)


def detect_bargaining(text: str) -> str | None:
    """The bargaining phrase found in the customer's words, or None.

    Pure and separately unit-tested: this is the only part of the guardrail that decides anything,
    and it decides before the model does.
    """
    for pattern in BARGAIN_PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group().strip()
    word = DISCOUNT_WORD_RE.search(text)
    if word and not (text.rstrip().endswith("?") and POLICY_QUESTION_RE.search(text)):
        return word.group()
    return None


def _last_user_text(input: Any) -> str:
    """The customer's own words: the last user message in a run input.

    The SDK hands the guardrail whatever the run was given — a string, one item, or a list of items
    — so all three shapes are unwrapped here rather than assumed.
    """
    if isinstance(input, str):
        return input
    if isinstance(input, dict):
        items: list[Any] = [input]
    elif isinstance(input, (list, tuple)):
        items = list(input)
    else:
        return ""
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):  # content parts
            return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


@input_guardrail(name="no_bargaining")
async def bargaining_guardrail(
    ctx: RunContextWrapper[ShopContext], agent: Agent[ShopContext], input: Any
) -> GuardrailFunctionOutput:
    """XR-2: refuse a bargain before it is answered, and route it to a human instead."""
    found = detect_bargaining(_last_user_text(input))
    return GuardrailFunctionOutput(
        output_info={
            "agent": agent.name,
            "phrase": found,
            "reason": "bargaining",
            "violations": [Violation("bargaining", found or "", "discount demanded by the customer").__dict__],
        },
        tripwire_triggered=found is not None,
    )


def escalation_reason_from_guardrail(info: Any) -> EscalationReason:
    """Turn a tripped input-guardrail result into the typed reason the specialist will read."""
    phrase = ""
    if isinstance(info, dict):
        phrase = str(info.get("phrase") or "")
    note = f"Customer demanded a discount ({phrase})." if phrase else "Customer demanded a discount."
    return EscalationReason(reason="bargaining", note=note[:120])
