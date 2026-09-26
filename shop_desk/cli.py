"""Terminal Shop Desk.

    python -m shop_desk.cli [--tier regular] [--hour 22] [--show-prompt]
    python -m shop_desk.cli --demo      # one long conversation, one order, one escalation

REPL commands: /basket  /cost  /prompt  /quit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agents.tracing import get_trace_provider

from shop_desk.catalogue import CATALOGUE_PATH, CatalogueError, load_catalogue
from shop_desk.config import StartupError, configure_global, load_settings
from shop_desk.context import ShopContext, fixed_clock
from shop_desk.desk_agents import build_agents
from shop_desk.instructions import resolved_prompt
from shop_desk.orders import ORDERS_PATH
from shop_desk.session import DeskSession, Reply

DEMO_SCRIPT = [
    "Hi! What do you sell?",
    "What does the kettle cost?",  # fast path
    "Is the pedestal fan available?",  # fast path, out of stock
    "I'd like three kettles please.",
    "And one steam iron.",
    "How much would five LED bulbs and two extension boards cost?",  # pricing specialist
    "What's in my basket now, and the total?",
    "If I order now, can you deliver today?",  # FR-4
    "What was the price of the iron again?",  # fast path
    "Actually, remove the iron.",
    "OK, what am I ordering now?",  # turn 11: remembers the basket
    "Yes, please place the order.",  # Order clerk -> typed Order, stored (XR-1)
    "And the kettle price, same as before?",  # XR-3: the cache answers this at 0 model calls
    "What did I order last week?",  # XR-1: re-derived from the catalogue, never a stored price
    "Your kettles are cheaper at the market. Give me 30% off my next order or I'm leaving.",  # XR-2
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talk to the Shop Desk in the terminal.")
    parser.add_argument("--tier", choices=["walk_in", "regular"], default="walk_in")
    parser.add_argument("--customer", default="cli-customer")
    parser.add_argument("--hour", type=int, help="simulate this hour of the day (0-23, PKT)")
    parser.add_argument("--show-prompt", action="store_true", help="print the resolved system prompt first")
    parser.add_argument("--demo", action="store_true", help="run the scripted demo conversation")
    # XR-4: point the whole Desk at another shop's catalogue. Same JSON shape, no code change.
    parser.add_argument("--catalogue", help="path to another catalogue.json (default: SHOP_DESK_CATALOGUE or ./catalogue.json)")
    parser.add_argument("--orders", help="path to the order store (default: SHOP_DESK_ORDERS or ./orders.jsonl)")
    return parser.parse_args(argv)


def print_reply(reply: Reply) -> None:
    print(f"desk> {reply.text}")
    if reply.escalation:
        print(f"       [escalated: reason={reply.escalation.reason!r}, note={reply.escalation.note!r}]")
    if reply.handoff_audit:
        print(f"       [handoff history {reply.handoff_audit.describe()}]")
    if reply.order and reply.order.status == "escalated":
        print(f"       [draft order for staff: {reply.order.model_dump_json()}]")
    if reply.requoted:
        print("       [re-quoted on the reasoning model after the guardrail refused the first answer]")
    if reply.turn_cost:
        print(f"       [{reply.turn_cost.line()}]")
    print()


async def show_prompt(session: DeskSession) -> None:
    print("----- resolved system prompt -----")
    print(await resolved_prompt(session.agents.desk, session.context))
    print("----------------------------------\n")


async def repl(session: DeskSession) -> None:
    print(f"{session.context.shop} desk ({session.context.tier}). /basket /cost /prompt /quit\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            break
        if text == "/basket":
            print(f"       {session.context.basket or 'empty'} · draft {session.context.draft_order_id}\n")
            continue
        if text == "/cost":
            print(f"       {session.context.ledger.cost_line()}\n")
            continue
        if text == "/prompt":
            await show_prompt(session)
            continue
        print_reply(await session.ask(text))


async def demo(session: DeskSession) -> None:
    for text in DEMO_SCRIPT:
        print(f"you> {text}")
        print_reply(await session.ask(text))


async def run(session: DeskSession, args: argparse.Namespace) -> None:
    if args.show_prompt:
        await show_prompt(session)
    try:
        await (demo(session) if args.demo else repl(session))
    finally:
        session.close()
        get_trace_provider().force_flush()
        ledger = session.context.ledger
        print(ledger.cost_line())
        # XR-3 and XR-4: what the cache saved, and what the guardrail caught.
        print(f"Cache (XR-3): {session.context.price_cache.stats()}")
        print(
            f"Guardrail (XR-4): blocked {ledger.guardrail_blocks} answer(s), "
            f"{ledger.requotes} re-quoted on the reasoning model"
        )
        if session.settings.tracing:
            print(f"Trace: {session.trace_url}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalogue_path = Path(args.catalogue) if args.catalogue else CATALOGUE_PATH
    orders_path = Path(args.orders) if args.orders else ORDERS_PATH
    try:
        settings = load_settings()
        configure_global(settings)  # FR-1 global level, before the agents are built
        catalogue = load_catalogue(catalogue_path)  # XR-4: any catalogue in the same shape
    except (StartupError, CatalogueError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    context = ShopContext(
        shop=catalogue.shop,
        currency=catalogue.currency,
        customer_id=args.customer,
        tier=args.tier,
        clock=fixed_clock(args.hour) if args.hour is not None else None,
        catalogue_path=catalogue_path,
        orders_path=orders_path,
    )
    print(f"{catalogue.shop} · {len(catalogue.products)} products · {catalogue_path}")
    session = DeskSession(build_agents(settings), context, settings)
    asyncio.run(run(session, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
