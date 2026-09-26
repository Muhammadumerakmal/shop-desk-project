"""Shop Desk in the browser (FR-12): `uv run chainlit run app.py -w`.

One DeskSession per browser session: its own ShopContext (basket, orders), its own history and
its own trace. The agent graph is built once and shared, because agents hold no customer state.
"""

from __future__ import annotations

import chainlit as cl
from chainlit.input_widget import Slider

from shop_desk.catalogue import CatalogueError, load_catalogue, money
from shop_desk.config import StartupError, configure_global, load_settings
from shop_desk.context import ShopContext, fixed_clock
from shop_desk.desk_agents import build_agents
from shop_desk.session import DeskSession, Reply

WALK_IN, REGULAR = "Walk-in customer", "Regular customer"

try:
    SETTINGS = load_settings()
    configure_global(SETTINGS)  # FR-1 global level, before the agents are built
    AGENTS = build_agents(SETTINGS)
    STARTUP_ERROR = None
except (StartupError, CatalogueError) as exc:  # NFR-1: a sentence, not a stack trace
    SETTINGS = AGENTS = None
    STARTUP_ERROR = str(exc)
    print(f"Cannot start: {exc}")

STARTERS = [
    cl.Starter(label="Kettle price", message="What does the kettle cost?"),
    cl.Starter(label="What do you sell?", message="What do you have in stock?"),
    cl.Starter(label="Order three", message="Can I order three kettles?"),
    cl.Starter(label="Delivery today?", message="If I order now, can you deliver today?"),
]


@cl.set_chat_profiles
async def chat_profiles(current_user=None, language=None):
    return [
        cl.ChatProfile(name=WALK_IN, markdown_description="A first-time customer.", default=True, starters=STARTERS),
        cl.ChatProfile(name=REGULAR, markdown_description="A loyalty customer: the 5% loyalty tool is offered.", starters=STARTERS),
    ]


@cl.on_chat_start
async def on_chat_start():
    if STARTUP_ERROR:
        await cl.Message(content=f"Cannot start: {STARTUP_ERROR}").send()
        return
    catalogue = load_catalogue()
    tier = "regular" if cl.user_session.get("chat_profile") == REGULAR else "walk_in"
    context = ShopContext(
        shop=catalogue.shop,
        currency=catalogue.currency,
        customer_id=f"web-{cl.user_session.get('id', 'anon')[:8]}",
        tier=tier,
    )
    cl.user_session.set("desk", DeskSession(AGENTS, context, SETTINGS))
    await cl.ChatSettings(
        [Slider(id="hour", label="Simulated hour, PKT (-1 = live clock)", initial=-1, min=-1, max=23, step=1)]
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    session: DeskSession | None = cl.user_session.get("desk")
    if session:
        hour = int(settings.get("hour", -1))
        session.context.clock = None if hour < 0 else fixed_clock(hour)  # FR-4 demo


def _order_table(reply: Reply, currency: str) -> str:
    order = reply.order
    rows = [
        f"| {i.sku} | {i.qty} | {money(i.unit_price, currency)} | {money(i.qty * i.unit_price, currency)} |"
        for i in order.items
    ]
    return "\n".join(
        [
            f"**Order `{order.order_id}` · status `{order.status}`**",
            "",
            "| SKU | Qty | Unit price | Line total |",
            "|---|---|---|---|",
            *rows,
            f"| **Total** | | | **{money(order.total, currency)}** |",
        ]
    )


@cl.on_message
async def on_message(message: cl.Message):
    session: DeskSession | None = cl.user_session.get("desk")
    if session is None:
        await cl.Message(content=f"Cannot start: {STARTUP_ERROR}").send()
        return

    reply = await session.ask(message.content)

    if reply.handoff_audit or reply.escalation:
        async with cl.Step(name="Escalation handoff", type="tool") as step:
            step.input = f"reason = {reply.escalation.reason!r}" if reply.escalation else ""
            step.output = reply.handoff_audit.describe() if reply.handoff_audit else "no audit"

    content = reply.text
    if reply.order and reply.order.status == "escalated":
        content += "\n\n" + _order_table(reply, session.context.currency)
    await cl.Message(content=content).send()

    if reply.turn_cost:  # FR-11: per-turn line, then the running conversation line
        async with cl.Step(name=f"Cost · turn {reply.turn_cost.turn} · {reply.turn_cost.kind}", type="llm") as step:
            step.output = f"{reply.turn_cost.line()}\n\n{reply.cost_line}\n\nTrace: {session.trace_url}"


@cl.on_chat_end
async def on_chat_end():
    session: DeskSession | None = cl.user_session.get("desk")
    if session:
        session.close()  # FR-13: finish the conversation's one trace
