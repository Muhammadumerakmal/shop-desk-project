"""The one place figures are derived from the catalogue (FR-6, NFR-3).

Tools use it to write figures; the guardrail uses it to recompute them from the current file.
Keeping one derivation means a tool and the guardrail can never disagree about arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

from shop_desk.catalogue import Catalogue
from shop_desk.context import Quote

LOYALTY_RATE = 0.05  # regular customers only, via the loyalty_discount tool (FR-7)


def _r(value: float) -> float:
    return round(value, 2)


@dataclass(frozen=True)
class QuoteFigures:
    unit_prices: tuple[float, ...]
    line_totals: tuple[float, ...]
    subtotal: float
    discount: float
    total: float
    missing: tuple[str, ...]  # SKUs no longer in the catalogue

    def all(self) -> set[float]:
        discounted_units = {
            _r(u * (1 - self.discount / self.subtotal)) for u in self.unit_prices
        } if self.subtotal and self.discount else set()
        return {*self.unit_prices, *self.line_totals, self.subtotal, self.discount, self.total, *discounted_units}


def quote_figures(quote: Quote, catalogue: Catalogue) -> QuoteFigures:
    units, lines, missing = [], [], []
    for sku, qty in quote.lines:
        product = catalogue.get(sku)
        if product is None:
            missing.append(sku)
            continue
        units.append(_r(product.price))
        lines.append(_r(product.price * qty))
    subtotal = _r(sum(lines))
    discount = _r(subtotal * quote.discount_rate)
    return QuoteFigures(tuple(units), tuple(lines), subtotal, discount, _r(subtotal - discount), tuple(missing))
