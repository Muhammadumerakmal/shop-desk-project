"""Orders survive the conversation, so the Desk can answer "what did I order last week?" (XR-1).

A JSONL file, one JSON object per confirmed order, appended when the customer confirms and read
back by the `recent_orders` tool. The file is gitignored and, crucially, **holds no prices**:

    {"order_id": "ORD-1A2B3C", "customer_id": "CUST-7781", "placed_at": "2026-09-26T14:03:00+05:00",
     "items": [{"sku": "KTL-01", "qty": 3}]}

Only the order id, who it was for, when, and the SKUs and quantities. Every figure the customer is
shown about a past order is re-derived from the *current* `catalogue.json` through
`pricing.quote_figures`, the same call the tools and the guardrail already use. Two consequences,
both wanted:

- the store can never become a second source of prices, so NFR-3 and Article 2 still hold;
- editing a price changes what last week's order is said to have cost, instead of the Desk
  replaying a stale number it once believed.

Failures are sentences, never exceptions (NFR-4): a missing file is an empty history, and an
unwritable one logs and carries on rather than breaking the order that was just placed.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORDERS_PATH = Path(os.getenv("SHOP_DESK_ORDERS", PROJECT_ROOT / "orders.jsonl"))

# The shop's timezone lives here because this module needs it to compare stored timestamps, and
# `context.py` imports from this module. One definition, one direction of dependency.
SHOP_TZ = timezone(timedelta(hours=5), name="PKT")  # Pakistan time, no DST


def as_shop_time(moment: datetime) -> datetime:
    """Make a datetime comparable with a stored one.

    Stored timestamps are written with an offset, but a hand-built or test one may be naive, and
    comparing the two raises. A naive value is read as shop local time, which is what it means
    everywhere in this project.
    """
    return moment.replace(tzinfo=SHOP_TZ) if moment.tzinfo is None else moment


@dataclass(frozen=True)
class PlacedOrder:
    """What is stored: identity, time and lines. No price ever reaches this dataclass."""

    order_id: str
    customer_id: str
    placed_at: datetime
    items: tuple[tuple[str, int], ...]  # (sku, qty)

    def age_days(self, now: datetime) -> int:
        return max((as_shop_time(now) - as_shop_time(self.placed_at)).days, 0)

    def to_json(self) -> str:
        return json.dumps(
            {
                "order_id": self.order_id,
                "customer_id": self.customer_id,
                "placed_at": self.placed_at.isoformat(),
                "items": [{"sku": sku, "qty": qty} for sku, qty in self.items],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, line: str) -> "PlacedOrder | None":
        """Parse one stored line. A damaged line is skipped, never raised (NFR-4).

        `placed_at` is normalised to an aware datetime here, so every `PlacedOrder` that exists has
        an aware `placed_at` and no comparison downstream can hit a naive/aware mismatch.
        """
        try:
            raw = json.loads(line)
            return cls(
                order_id=str(raw["order_id"]),
                customer_id=str(raw["customer_id"]),
                placed_at=as_shop_time(datetime.fromisoformat(str(raw["placed_at"]))),
                items=tuple((str(i["sku"]), int(i["qty"])) for i in raw["items"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("skipping a damaged order line (%s)", exc)
            return None


def placed_order(order, customer_id: str, placed_at: datetime) -> PlacedOrder:
    """The storable form of a confirmed `Order`: SKUs and quantities only."""
    return PlacedOrder(
        order_id=order.order_id,
        customer_id=customer_id,
        placed_at=placed_at,
        items=tuple((item.sku, item.qty) for item in order.items),
    )


def record_order(order: PlacedOrder, path: Path | None = None) -> bool:
    """Append one confirmed order. Returns whether it was stored; never raises (NFR-4)."""
    try:
        target = Path(path) if path else ORDERS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(order.to_json() + "\n")
        return True
    except OSError as exc:
        log.warning("could not store order %s: %s", order.order_id, exc)
        return False


def recent_orders(
    customer_id: str, days: int = 7, now: datetime | None = None, path: Path | None = None
) -> list[PlacedOrder]:
    """This customer's orders from the last `days` days, newest first.

    A missing or unreadable file is an empty history, not an error: the Desk can say "I have no
    record of an order" whether or not the shop has ever taken one.
    """
    target = Path(path) if path else ORDERS_PATH
    moment = as_shop_time(now) if now else datetime.now(SHOP_TZ)
    cutoff = moment - timedelta(days=max(days, 0))
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.warning("could not read the order store %s: %s", target.name, exc)
        return []

    found = []
    for line in lines:
        order = PlacedOrder.from_json(line)
        if order is None or order.customer_id != customer_id:
            continue
        if order.placed_at >= cutoff:
            found.append(order)
    return sorted(found, key=lambda o: o.placed_at, reverse=True)
