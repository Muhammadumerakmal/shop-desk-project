"""ShopContext: the customer lives here, never in the prompt (FR-2).

The first four fields are the ones the brief specifies. The rest is per-session working state
that must survive history trimming (FR-12): the basket, the draft order id and the quotes
issued this run (which the guardrail re-checks, FR-6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from shop_desk.catalogue import CATALOGUE_PATH
from shop_desk.cost import CostLedger

PKT = timezone(timedelta(hours=5), name="PKT")  # Pakistan time, no DST


def new_order_id() -> str:
    return f"ORD-{uuid.uuid4().hex[:6].upper()}"


@dataclass(frozen=True)
class Quote:
    """A figure's derivation, not the figure: the guardrail recomputes it from the file."""

    lines: tuple[tuple[str, int], ...]  # (sku, qty)
    discount_rate: float = 0.0


@dataclass
class ShopContext:
    shop: str
    currency: str
    customer_id: str
    tier: str = "walk_in"  # "walk_in" or "regular"

    basket: dict[str, int] = field(default_factory=dict)
    orders: list[Any] = field(default_factory=list)  # confirmed Order objects
    draft_order_id: str = field(default_factory=new_order_id)
    issued_quotes: list[Quote] = field(default_factory=list)  # reset every run
    fast_path_used: bool = False  # set by lookup_price, read by the cost line
    escalation: Any = None  # EscalationReason, set by the handoff (FR-10)
    handoff_audit: Any = None  # HandoffAudit: before/after of the transferred history
    clock: Callable[[], datetime] | None = None  # simulated time for FR-4
    catalogue_path: Path = CATALOGUE_PATH
    ledger: CostLedger = field(default_factory=CostLedger)  # FR-11

    def now(self) -> datetime:
        return self.clock() if self.clock else datetime.now(PKT)

    def start_run(self) -> None:
        """Per-run state: quotes only count as 'from the catalogue this run' (NFR-3)."""
        self.issued_quotes.clear()
        self.fast_path_used = False


def fixed_clock(hour: int, minute: int = 0) -> Callable[[], datetime]:
    """A simulated clock pinned to today's date at the given PKT hour."""
    today = datetime.now(PKT).date()
    moment = datetime(today.year, today.month, today.day, hour, minute, tzinfo=PKT)
    return lambda: moment
