"""Structures that cross a boundary: tool arguments, the typed order and the order check (FR-5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class QuoteLine(BaseModel):
    sku: str = Field(description="catalogue SKU, e.g. KTL-01")
    qty: int = Field(description="number of units, at least 1")


class LineItem(BaseModel):
    sku: str
    qty: int
    unit_price: float


class Order(BaseModel):
    order_id: str
    status: Literal["draft", "confirmed", "escalated"]
    items: list[LineItem]
    total: float


class EscalationReason(BaseModel):
    """FR-10: why the Desk gave up, as a typed value rather than a sentence."""

    reason: Literal["bargaining", "complaint", "unavailable_item", "customer_asked_for_human", "stuck"]
    note: str = Field(default="", max_length=120, description="one short line for the member of staff")


@dataclass(frozen=True)
class OrderCheck:
    model_total: float
    recomputed_total: float

    @property
    def mismatch(self) -> bool:
        return abs(self.model_total - self.recomputed_total) > 0.005

    def note(self, currency: str) -> str:
        if not self.mismatch:
            return ""
        from shop_desk.catalogue import money

        return (
            f"Note: the total was corrected from {money(self.model_total, currency)} to "
            f"{money(self.recomputed_total, currency)}, recomputed from the line items."
        )


def check_order_total(order: Order) -> OrderCheck:
    """FR-5: recompute the total in Python; never trust the model's arithmetic."""
    recomputed = round(sum(item.qty * item.unit_price for item in order.items), 2)
    return OrderCheck(model_total=round(order.total, 2), recomputed_total=recomputed)
