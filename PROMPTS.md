# Shop Desk — Prompts for the coding agent

These are the prompts that drive Claude Code or OpenCode through the guide's clock, one prompt
per phase. They follow the guide's rules:

1. **Specification before implementation.** Prompt 0 produces only the four spec artifacts.
   You commit them yourself before Prompt 1.
2. **The agent writes the code, you own it.** Every prompt ends with "explain" and "prove" steps.
   Read the output before you commit.
3. **Requirements are numbered and testable.** Every prompt names its FRs and their "Done when".
4. **The clock is real.** If you fall behind, use the cut-list prompt at the bottom.

Paste each prompt as-is. The text in `<angle brackets>` is yours to fill in.

---

## Prompt 0 — Specify (0:00–0:35) · no code

```text
We are doing a spec-driven build of "Shop Desk" from shop-desk-project-guide.pdf in this folder.
Read the whole PDF first. Do NOT create any source file (.py, pyproject, app) in this step.

Create exactly four files under specs/ and nothing else (plus a .gitignore that ignores .env):

1. specs/constitution.md — rules the build may not break, each one checkable:
   - the process default model is the cheap model (FAST_MODEL, gemini-2.5-flash), set globally;
     only the Order clerk (agent level) and a re-quote path (run level) may use REASONING_MODEL;
   - a quoted price, stock figure or SKU must come from catalogue.json this run;
   - secrets live only in .env, never printed; a missing key fails at startup with one sentence;
   - no tool raises into the runner: tools return sentences;
   - Always / Ask first / Never boundaries.
2. specs/spec.md — behaviour: objective, assumptions (Gemini via OpenAI-compatible endpoint, PKT
   hours 10:00–21:00, same-day cutoff 17:00, 5% loyalty rate for regular tier), FR-1..FR-13 and
   NFR-1..NFR-5 in my own words with an "Accept:" line each, the three things the Desk deliberately
   will NOT do (bargain/invent discounts, take payment or personal details, discuss anything outside
   the catalogue), and a definition-of-done table mapping each check to a test file.
   For FR-10 state exactly what the handoff filter removes and what the specialist still needs.
3. specs/plan.md — architecture: every agent with its model level, ModelSettings and WHY;
   every tool with what it takes and returns; every structure crossing a boundary (ShopContext,
   Quote, LineItem, Order, EscalationReason, Reply); how the guardrail decides; the session turn
   flow (tripwire -> re-quote -> polite refusal; MaxTurnsExceeded -> polite end); commands,
   project structure, testing strategy (offline with agents.testing.ScriptedModel), risks.
4. specs/tasks.md — ordered, independently verifiable tasks T00..T19, each naming the FR it serves,
   the files it touches and a Verify command.

The four files must agree with each other. When done, list any decision you made that the PDF
did not dictate, so I can approve or change it before I commit.
```

**You then:** read all four, fix anything you disagree with, and commit:
`git add specs .gitignore && git commit -m "Phase 0: constitution, spec, plan and tasks"`.

---

## Prompt 1 — Catalogue and the fast path (0:35–1:05) · FR-1 … FR-4

```text
Implement Phase 1 of specs/tasks.md (T01–T07), following specs/plan.md exactly. Use uv, Python 3.12,
openai-agents, pydantic, python-dotenv, pytest + pytest-asyncio.

- T01 pyproject.toml, .env.example (placeholders only), catalogue.json (8 products, KTL-01 kettle
  4200 stock 12 and FAN-22 pedestal fan 9800 stock 0 included).
- T02 shop_desk/config.py: Settings (keys repr=False), load_settings() raising StartupError with
  ONE sentence per missing key, configure_global(): AsyncOpenAI pointed at Gemini,
  set_default_openai_client(client, use_for_tracing=False), set_default_openai_api("chat_completions"),
  OPENAI_DEFAULT_MODEL=FAST_MODEL (this is the GLOBAL level of FR-1), tracing key.
- T03 shop_desk/catalogue.py: load_catalogue() re-reads the file on every call; find(); money().
- T04 shop_desk/context.py: ShopContext dataclass (shop, currency, customer_id, tier + basket,
  draft_order_id, issued_quotes, fast_path_used, clock, catalogue_path).
- T05 shop_desk/tools.py: lookup_price, search_catalogue, add_to_basket, remove_from_basket,
  view_basket. Each takes RunContextWrapper[ShopContext] first and returns a sentence. Add a
  @never_raises decorator (under @function_tool) so no exception reaches the runner.
- T06 shop_desk/instructions.py: desk_instructions(ctx, agent) built per turn from shop facts and
  ctx.context.now(); closed / same-day / next-day promise. NEVER include customer_id or tier.
- T07 desk_agents.build_agents(settings): the Desk with NO model= and
  tool_use_behavior=StopAtTools(stop_at_tool_names=["lookup_price"]); session.DeskSession.ask();
  cli.py with --tier, --hour, --show-prompt.

Tests (offline, no API key): a ScriptedProvider in tests/conftest.py that returns an
agents.testing.ScriptedModel per model name and records requested names. Prove:
FR-1 a plain question is answered by the global default; FR-2 no tool schema contains the wrapper
and the resolved prompt has no customer id; FR-3 a price question is exactly 1 model call and its
answer is the tool's exact text, while an order question is >= 2 calls; FR-4 two simulated hours
give two different promises; NFR-1 a missing key is one sentence.

Run `uv run pytest -q` and show me the result. Then explain in 5 bullets: where each FR lives,
how many model calls each path costs, and what we lose with the fast path. Do not commit.
```

---

## Prompt 2 — Orders, limits and truth (1:05–1:40) · FR-5 … FR-9

```text
Implement Phase 2 of specs/tasks.md (T08–T13), following specs/plan.md.

- T08 shop_desk/schemas.py: LineItem, Order (status Literal draft|confirmed|escalated), QuoteLine,
  check_order_total() -> OrderCheck that recomputes the total in Python and reports a mismatch.
- T09 Order clerk agent: model=settings.reasoning_model (the AGENT level of FR-1),
  ModelSettings(temperature=0), tools=[view_basket], output_type=Order; Desk hands off to it when
  the customer confirms. The session renders the order text in Python (not the model), reports
  any total mismatch, stores the order and clears the basket.
- T10 shop_desk/pricing.py (the one place figures are derived: Quote -> unit prices, line totals,
  subtotal, discount, total) and shop_desk/guardrails.py: an @output_guardrail that RE-READS
  catalogue.json and refuses any PKR amount, "N in stock" figure or SKU the file does not back.
  Amounts are allowed only if they are a catalogue price or recomputed from a Quote a tool issued
  THIS run. For an Order: SKU exists, unit_price is the catalogue price (or 5%-off for regular),
  1 <= qty <= stock, stock > 0. Attach it to Desk, Order clerk and the specialist base.
  In DeskSession: catch OutputGuardrailTripwireTriggered, restore the basket, retry ONCE with
  RunConfig(model=settings.reasoning_model) (the RUN level of FR-1), then reply with a polite
  refusal. No traceback ever reaches the customer.
- T11 loyalty_discount with is_enabled=lambda ctx, agent: ctx.context.tier == "regular";
  eid_gift_wrap with is_enabled=False.
- T12 Specialist base (tools: search_catalogue, quote; the guardrail) and Pricing specialist =
  base.clone(name, instructions, model_settings) exposed as pricing.as_tool("pricing_specialist",
  custom_output_extractor=<returns digits only>). MAX_TURNS=6 on every run; catch MaxTurnsExceeded
  and end the conversation politely.
- T13 Human escalation = base.clone(...) as well. Neither clone passes model=.

Tests: planted order-total mismatch is caught; editing a price in a temp copy of catalogue.json makes
a previously passing answer fail; an out-of-stock Order never passes; the re-quote runs on the
reasoning model and the basket is not doubled; walk_in vs regular get different tool lists and
eid_gift_wrap is in neither; the Desk's reply contains the specialist's number in its own words;
`is` checks show clones share tools/guardrails/model and not instructions/model_settings; the
turn ceiling ends politely.

Run `uv run pytest -q` and show me the result. Then answer: which sharing between the clones
would bite us later, and why is the seasonal tool invisible rather than refused? Do not commit.
```

---

## Prompt 3 — Escalation, lifecycle and interface (1:40–1:55) · FR-10 … FR-13

```text
Implement Phase 3 of specs/tasks.md (T14–T19), following specs/plan.md.

- T14 EscalationReason(BaseModel): reason: Literal["bargaining","complaint","unavailable_item",
  "customer_asked_for_human","stuck"], note: str (<=120 chars). Desk handoff:
  handoff(escalation, tool_name_override="escalate_to_human", input_type=EscalationReason,
  on_handoff=<store it in ctx.context.escalation>, input_filter=escalation_filter).
  shop_desk/handoff_filters.py: escalation_filter = agents.extensions.handoff_filters.remove_all_tools
  then keep the last 6 history items, and write a HandoffAudit (before/after item counts by
  message/tool) into the context. The escalation clone gets dynamic instructions that read the
  typed reason from context. If the basket is non-empty, the session builds an Order with
  status="escalated" for staff.
- T15 shop_desk/cost.py: CostLedger; CostHooks(RunHooks).on_llm_end records each call (agent,
  resolved model, tokens from response.usage); PricingHooks(AgentHooks) attached to the Pricing
  clone ONLY; LedgerRunner(AgentRunner) installed with set_default_agent_runner() in
  configure_global(), recording each run's model and token DELTA (nested as_tool runs share the
  parent's Usage object, so only depth-0 runs count toward turn totals). A cost line per turn and
  per conversation: turns (fast-path vs reasoning), calls, tokens in/out, models, most expensive turn.
- T16 shop_desk/history.py: trim_history(): first drop tool calls/outputs older than the last 2
  turns, then drop turns beyond the last 6 customer turns. The basket stays in context.
- T17 app.py (Chainlit): chat profiles Walk-in / Regular (sets tier), a simulated-hour slider,
  one DeskSession per browser session, an escalation step showing the handoff audit, and a cost
  step with the trace link. Close the session on chat end.
- T18 One trace per conversation: DeskSession creates trace(...).start() once, makes it current
  for each turn with agents.tracing.scope.Scope.set_current_trace, wraps each turn in
  custom_span("turn N · fast-path|reasoning"), and finishes it in close().
- T19 cli.py --demo: a scripted conversation of 12+ turns with one order and one escalation.

Tests: the escalation reason arrives as an EscalationReason value; the escalation model call
contains no function_call items; before/after audit counts; the cost line distinguishes fast-path
from reasoning and nested tokens are not double counted; agent hooks fire only for Pricing;
turn 11 still knows the basket after trimming; two sessions have separate baskets; one
conversation produces exactly one trace (collecting TracingProcessor).

Run `uv run pytest -q`, then `uv run chainlit run app.py` and tell me what to click to see each
FR. Do not commit.
```

---

## Prompt 4 — Demo and live verification (1:55–2:00)

```text
With my real keys in .env, run `uv run python -m shop_desk.cli --demo` and then
`uv run python -m shop_desk.cli --hour 22 --show-prompt`. From the output and the trace:
1. Name the fast-path turns and the reasoning turns, and their call counts.
2. Name the most expensive turn and why it was expensive.
3. Show the escalation's typed reason and the handoff before/after counts.
4. Show that turn 11 still knew the basket.
If any live behaviour differs from the offline tests (for example a Gemini parameter rejected),
fix it with the smallest change, add a test for it, and update specs/ first if behaviour changes.
```

---

## Prompt 5 — Rehearse the defence

```text
Quiz me on the eight "Defending your work" questions from the PDF, one at a time. After each of my
answers, point to the exact file and symbol in this repo that proves or disproves it, and tell
me what I missed. Do not give me the answer before I try.
```

---

## Cut-list prompt (use only if behind)

```text
We are behind the clock. Apply the guide's cut list in order and stop as soon as we are back on time:
1. Cut FR-11 (cost hooks, LedgerRunner, cost line), leaving the session code working.
2. Then cut FR-9 (make Pricing and Escalation plain agents instead of clones).
3. Then cut the agent-level hooks inside FR-11 if any remain.
NEVER cut FR-3 (fast path) or FR-6 (catalogue guardrail). Update specs/spec.md and specs/tasks.md
to mark what was cut and why, in the same commit as the code change.
```

---

## Commit messages used in this repo

```
Phase 0: constitution, spec, plan and tasks
Phase 1: catalogue, context, global model default and the fast path (FR-1..FR-4)
Phase 2: typed orders, catalogue guardrail, tier tools, specialists (FR-5..FR-9)
Phase 3: escalation, cost hooks, Chainlit UI and one trace (FR-10..FR-13)
```
