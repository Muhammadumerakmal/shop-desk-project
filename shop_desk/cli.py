"""Terminal Shop Desk: `python -m shop_desk.cli [--tier regular] [--hour 22] [--show-prompt]`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from shop_desk.catalogue import CatalogueError, load_catalogue
from shop_desk.config import StartupError, configure_global, load_settings
from shop_desk.context import ShopContext, fixed_clock
from shop_desk.desk_agents import build_agents
from shop_desk.instructions import resolved_prompt
from shop_desk.session import DeskSession


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talk to the Shop Desk in the terminal.")
    parser.add_argument("--tier", choices=["walk_in", "regular"], default="walk_in")
    parser.add_argument("--customer", default="cli-customer")
    parser.add_argument("--hour", type=int, help="simulate this hour of the day (0-23, PKT)")
    parser.add_argument("--show-prompt", action="store_true", help="print the resolved system prompt first")
    return parser.parse_args(argv)


async def repl(session: DeskSession, show_prompt: bool) -> None:
    if show_prompt:
        print("----- resolved system prompt -----")
        print(await resolved_prompt(session.agents.desk, session.context))
        print("----------------------------------")
    print(f"{session.context.shop} desk. Type /quit to leave.\n")
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
        reply = await session.ask(text)
        print(f"desk> {reply.text}\n       [{reply.kind}, {reply.model_calls} model call(s)]\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings()
        configure_global(settings)  # FR-1 global level, before any agent is built
        catalogue = load_catalogue()
    except (StartupError, CatalogueError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    context = ShopContext(
        shop=catalogue.shop,
        currency=catalogue.currency,
        customer_id=args.customer,
        tier=args.tier,
        clock=fixed_clock(args.hour) if args.hour is not None else None,
    )
    session = DeskSession(build_agents(settings), context, settings)
    asyncio.run(repl(session, args.show_prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
