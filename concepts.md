# Shop Desk — Concepts

This file maps every OpenAI Agents SDK concept the project uses (Parts 0–20 in the guide's
concept-coverage table) to the exact place it lives in this codebase, and explains *why* it is
there. Read it top to bottom once. After that, use it to answer "where is X and why?" during a
defence.

> SDK version: `openai-agents` 0.22.x · Model provider: Gemini via its OpenAI-compatible endpoint
> · UI: Chainlit 2.x. Spec artifacts live in [`specs/`](specs/).

---

## 1. The whole system on one page

```
                 customer (Chainlit tab / CLI)
                          │  text
                          ▼
              ┌───────────────────────┐   one per browser session
              │  DeskSession.ask()    │   owns: ShopContext, history, ONE trace
              │  shop_desk/session.py │   catches: guardrail trip, MaxTurnsExceeded, API errors
              └──────────┬────────────┘
      trim_history()     │ Runner.run(Desk, max_turns=6, hooks=CostHooks)   ← LedgerRunner (custom runner)
                         ▼
   ┌────────────────────────────────────────────┐  model: none → GLOBAL default (FAST_MODEL)
   │ Shop Desk  (dynamic instructions, FR-4)     │  tool_use_behavior = StopAtTools(["lookup_price"])
   │ output_guardrails = [catalogue_guardrail]   │
   └──┬─────────────┬──────────────┬─────────────┘
      │ tools        │ agent-as-tool │ handoffs
      │              ▼               ├──────────────► Order clerk      model = REASONING (AGENT level)
      │   Pricing specialist (clone) │                 output_type = Order
      │   hooks = PricingHooks        └──────────────► Human escalation (clone)
      │                                                input_type = EscalationReason
      ▼                                                input_filter = escalation_filter
 lookup_price · search_catalogue · add_to_basket · remove_from_basket · view_basket
 loyalty_discount (regular only) · eid_gift_wrap (off) · quote (specialists) · recent_orders
      │
      ▼
 catalogue.json  ← re-read on every call; the ONLY source of prices, stock and SKUs

 Re-quote path: if the guardrail trips → the same turn runs again with RunConfig(model=REASONING)  (RUN level)
```

### Three typical turns and what they cost

| Customer says | Path | Model calls | Why |
|---|---|---|---|
| "What does the kettle cost?" | Desk → `lookup_price` → **stop** | **1** | `StopAtTools`: the tool's text *is* the answer |
| "Can I order three kettles?" | Desk → `add_to_basket` → Desk writes reply | 2 | normal tool loop: the model must see the result |
| "Three kettles and two irons, how much?" | Desk → `pricing_specialist` (Pricing → `quote` → digits) → Desk | 4 | nested run; the Desk rewords the number |
| "Yes, place the order" | Desk → handoff → Order clerk → `view_basket` → `Order` | 3 (2 on the reasoning model) | the only place reasoning is paid for |
| "Give me 30% off or I'm leaving" | `bargaining_guardrail` **stops the run** → Human escalation | 1 | the Desk model is never called; only the escalation agent is |
| "What did I order last week?" | `recent_orders` → Desk rewords | 1 | figures re-derived from today's catalogue |
| "...and the kettle again?" (same session) | **cache** | **0** | same SKU, same catalogue fingerprint |

---

## 2. Concept by concept

Each entry follows the same shape: **what it is** → **where in this project** → **why here** →
**gotcha**.

### Parts 0–2 · Setup, keys, Gemini — FR-1, NFR-1

- **What:** the SDK talks to any OpenAI-compatible endpoint. Gemini exposes one at
  `https://generativelanguage.googleapis.com/v1beta/openai/` and speaks the *Chat Completions* API,
  not the Responses API.
- **Where:** `shop_desk/config.py`: `load_settings()`, `configure_global()`, `StartupError`.
  Keys come from `.env` (template: `.env.example`, gitignored).
- **Why:** NFR-1 says a missing key fails **at startup with one sentence**. `load_settings()`
  raises `StartupError("GEMINI_API_KEY is missing: copy .env.example to .env …")`. `cli.py` and
  `app.py` print it and stop. `Settings` marks keys `repr=False`, so they never appear in logs.
- **Gotcha:** `set_default_openai_client(client, use_for_tracing=False)`. Without `False`, the SDK
  would send your **Gemini** key to the **OpenAI** trace exporter.

### Part 3 · Runner and asyncio — FR-1, FR-12

- **What:** `Runner.run(agent, input, context=…, max_turns=…, hooks=…, run_config=…)` is the
  async loop: call model → run tools/handoffs → repeat until a final output.
- **Where:** `DeskSession._run()` in `shop_desk/session.py`. The CLI wraps it in `asyncio.run`.
  Chainlit handlers are already async.
- **Why:** one `Runner.run` = one customer turn. The session feeds it the trimmed history plus the
  new message, then stores `result.to_input_list()` as the new history.

### Part 4 · Model configuration at three levels — FR-1

| Level | Where | Code |
|---|---|---|
| **Global** | `config.configure_global()` | `set_default_openai_client(...)`, `set_default_openai_api("chat_completions")`, `os.environ["OPENAI_DEFAULT_MODEL"] = FAST_MODEL` |
| **Agent** | `desk_agents.build_agents()` | `Agent(name="Order clerk", model=settings.reasoning_model, …)` |
| **Run** | `DeskSession._requote()` | `RunConfig(model=settings.reasoning_model)` |

- **Why:** the theme is *not wasting money*. The cheap model answers by default. Only the Order
  clerk (turning a basket into an exact typed order) pays for reasoning on every call. The
  re-quote path pays for it **once**, only after the guardrail refused a cheaper answer.
- **Precedence** (SDK `get_model()`): `RunConfig.model` > `agent.model` > the global default. So the
  re-quote run overrides even the Order clerk for that single run.
- **Delete-the-global-default experiment:** agents with no `model=` (Desk, specialists) fall back
  to the SDK's built-in default name, which the Gemini endpoint does not serve. That is the one
  behaviour that changes. The Order clerk and the re-quote path are unaffected.
  (`tests/test_model_levels.py::test_deleting_global_default_changes_only_the_default_resolution`)
- **Gotcha:** set the global default **before** building agents. `Agent.model_settings` is
  derived from the default model name at construction time.

### Part 5 · Tools — FR-1, FR-2

- **What:** `@function_tool` turns a typed, documented Python function into a JSON-schema tool.
  The docstring becomes the description and `Args:` become parameter descriptions.
- **Where:** `shop_desk/tools.py`. `DESK_TOOLS` (Desk) and `SPECIALIST_TOOLS` (clones).
- **Why:** FR-1 says products are reachable **only through tools**, so the model never sees
  `catalogue.json` directly.
- **NFR-4 in practice:** `@never_raises` wraps every tool and turns any exception into a sentence
  (`"The catalogue is unavailable right now (…)"`). Tools also validate explicitly: unknown SKU,
  `qty < 1`, out of stock, over stock.
- **Gotcha:** decorator order matters. `@function_tool` goes on the **outside**.
  `functools.wraps` keeps `__wrapped__` and `__annotations__`, so the SDK still sees
  `ctx: RunContextWrapper[ShopContext]` and hides it from the schema.

### Part 6 · Model settings — FR-8, FR-9, NFR-2

- **What:** `ModelSettings(temperature=…, max_tokens=…)` tunes a model per agent. `RunConfig` can
  override it per run.
- **Where:** every agent in `desk_agents.py` declares its own (NFR-2):

| Agent | Settings | Reason |
|---|---|---|
| Shop Desk | `temperature=0.3, max_tokens=400` | friendly but short |
| Order clerk | `temperature=0` | exact copying of basket lines |
| Specialist base | `temperature=0.2` | template |
| Pricing (clone) | `temperature=0, max_tokens=60` | digits only, capped output = capped cost |
| Human escalation (clone) | `temperature=0.4, max_tokens=350` | warm, one short message |

### Part 7 · Local context — FR-2

- **What:** any Python object passed as `Runner.run(context=…)`. Tools and hooks get it as
  `RunContextWrapper[T].context`. **It is never sent to the model.**
- **Where:** `shop_desk/context.py::ShopContext`. The four fields from the brief (`shop`,
  `currency`, `customer_id`, `tier`) plus the per-session state that must survive history trimming:
  `basket`, `orders`, `draft_order_id`, `issued_quotes`, `escalation`, `handoff_audit`, `clock`,
  `ledger`.
- **Proof (FR-2 done-when):** `tests/test_context.py` checks three things. No tool schema contains
  the wrapper. The resolved prompt has no customer id or tier. `instructions.py` never mentions
  `customer_id`/`.tier`. `loyalty_discount` reads `tier` through the wrapper.

### Part 8 · Dynamic instructions — FR-4

- **What:** `instructions=` can be a function `(ctx, agent) -> str`. It is called before **every**
  model call.
- **Where:** `instructions.py::desk_instructions` and `delivery_promise(now)`.
  `ShopContext.clock` lets you simulate the hour (`--hour 22` in the CLI, a slider in Chainlit).
- **Behaviour:** before 10:00 or after 21:00 it says CLOSED, promises nothing for today and says
  when it opens. From 10:00 to 17:00 it promises same-day delivery. From 17:00 to 21:00 it promises
  next-day delivery.
- **Print it before any model call:** `python -m shop_desk.cli --hour 22 --show-prompt`, or `/prompt`
  in the REPL (`resolved_prompt()` calls `agent.get_system_prompt()`).
- `escalation_instructions` is dynamic too. It is how the **typed escalation reason** reaches the
  specialist even though the handoff filter removed the handoff call.

### Part 9 · Cloning — FR-9

- **What:** `agent.clone(**changes)` is a **shallow** `dataclasses.replace`.
- **Where:** `desk_agents.py`: `specialist_base.clone(name=…, instructions=…, model_settings=…)`,
  twice. Neither clone passes `model=`.
- **Shared (`is`):** `pricing.tools is base.tools is escalation.tools`,
  `pricing.output_guardrails is base.output_guardrails`, `pricing.model is base.model` (both
  `None` → global default).
- **Independent:** `instructions`, `model_settings`, `name`, and `hooks` (set on Pricing *after*
  cloning, so the base and escalation stay hook-free).
- **The sharing that bites later:** `pricing.tools.append(x)` silently adds `x` to the base
  **and** the escalation agent (`tests/test_specialists.py::test_shared_tools_list_is_the_trap`).
  To give a clone its own list: `base.clone(tools=[*base.tools, extra])`.

### Part 10 · Tracing — FR-13

- **What:** every run creates spans (agent, generation, tool, handoff, guardrail) inside a trace.
  They are exported to platform.openai.com with `set_tracing_export_api_key(OPENAI_API_KEY)`.
- **One conversation = one trace:** `DeskSession.__init__` creates `trace(...)` and `.start()`s it
  once. Each `ask()` makes it current with `Scope.set_current_trace(self.trace)`, so the runner
  **attaches** to it instead of creating a new trace per turn. `close()` finishes it (CLI exit /
  Chainlit `on_chat_end`).
- **Finding turns in the trace:** each turn is wrapped in `custom_span("turn N · fast-path|reasoning")`.
  The cost line names the most expensive turn.
- **Check:** `tests/test_session.py::test_one_trace_per_conversation` uses a collecting trace
  processor.

### Part 11 · Agents as tools — FR-8

- **What:** `agent.as_tool(tool_name, tool_description, custom_output_extractor=…)` runs the
  agent as a nested `Runner.run` and returns its output to the caller. **Control stays with the
  caller** (unlike a handoff).
- **Where:** `pricing.as_tool("pricing_specialist", …, custom_output_extractor=extract_figure)`.
- **Why:** the Pricing specialist answers with **a number, not a paragraph**. `extract_figure()`
  pulls the first number out of its output. The Desk then writes its own sentence ("Three kettles
  and two irons come to PKR 25,600").
- **Gotcha:** the nested run inherits the parent's `RunConfig` (via `ToolContext.run_config`)
  and shares its `Usage` object. See Part 18 for why that matters for counting tokens.

### Part 12 · Handoffs — FR-10

- **What:** `handoff(agent, input_type=…, on_handoff=…, input_filter=…)` exposes a
  `transfer_to_…` tool. When it is called, the target agent **takes over** the conversation.
- **Order clerk:** plain handoff (`transfer_to_order_clerk`).
- **Escalation:** `handoff(escalation, tool_name_override="escalate_to_human",
  input_type=EscalationReason, on_handoff=on_escalate, input_filter=escalation_filter)`.
  - **Typed reason:** `EscalationReason.reason` is a `Literal["bargaining", "complaint",
    "unavailable_item", "customer_asked_for_human", "stuck"]`. The model must fill it as JSON, and
    `on_escalate` stores the validated object in context.
  - **Filtered history:** `handoff_filters.escalation_filter` first applies the SDK's
    `agents.extensions.handoff_filters.remove_all_tools` (drops every tool call/output, including
    the handoff call). Then it keeps only the last 6 history items and records a `HandoffAudit`
    (before/after counts shown in the CLI and in Chainlit).
  - **What the specialist still needs:** the recent customer and Desk messages (the complaint in
    their words). The reason is in its dynamic instructions. The basket is in context, and the
    session turns it into an `Order(status="escalated")` for staff.

### Part 13 · Advanced tool control — FR-3, FR-7, FR-8

- **Fast path (FR-3):** `tool_use_behavior=StopAtTools(stop_at_tool_names=["lookup_price"])`.
  When the Desk calls `lookup_price`, that tool's output becomes the final output. **The model
  never sees the result and never writes a sentence around it: one model call.**
  - *What you give up:* the answer can't be rephrased, combined with a second fact, translated or
    followed up in the same turn. It reads like a template.
  - The output guardrail still runs on it (the SDK guardrails tool-terminal outputs too).
- **Off vs earned (FR-7):**
  - `eid_gift_wrap`: `@function_tool(is_enabled=False)`, switched off statically.
  - `loyalty_discount`: `is_enabled=is_regular_customer`, evaluated per run from `ctx.context.tier`.
  - *Why "off" and "not offered" look the same to the model:* the model only knows the tools in the
    request's `tools` array. Both are simply absent, so it can't call or even mention them. The
    difference is only on our side (static vs evaluated per run).
- **Turn ceiling (FR-8):** `Runner.run(max_turns=6)`. `MaxTurnsExceeded` is caught in `DeskSession._ask`.
  The customer gets `CEILING` ("…this is taking me too many steps, so I'll stop here…"), and the
  session is marked ended.
  Why 6: price = 1 turn, basket edit = 2, confirmed order = 3, so there is room for one retry of
  each step.

### Part 14 · Structured output — FR-5

- **What:** `output_type=Order` makes the agent's final output a validated Pydantic object.
- **Where:** `schemas.py::Order/LineItem` and the Order clerk.
- **Never trust the model's arithmetic:** `check_order_total()` recomputes
  `sum(qty × unit_price)`. On a mismatch, the session **reports** it ("Note: the total was
  corrected from PKR 12,000 to PKR 12,600…") and logs it. It does not silently accept either number.
- The customer-facing order text is rendered **by Python** from the validated object
  (`DeskSession._confirm_order`), not written by the model.

### Part 15 · Guardrails — FR-6, NFR-3

- **What:** `@output_guardrail` runs on the final output. If it returns `tripwire_triggered=True`,
  the runner raises `OutputGuardrailTripwireTriggered`.
- **Where:** `guardrails.py::catalogue_guardrail`, attached to the Desk, the Order clerk and the
  specialist base (so both clones share it).
- **A data check, not a vibe check.** It re-reads `catalogue.json`, then extracts:
  - amounts next to `PKR`/`Rs`. Allowed amounts are every catalogue price, plus figures
    **recomputed from the current file** for each `Quote` that a tool issued this run
    (`pricing.py::quote_figures`)
  - stock claims ("12 in stock")
  - SKUs (`ABC-12`)

  For an `Order` it checks every SKU, unit price, `qty ≤ stock` and `stock > 0`.
- **Why quotes store derivations, not numbers:** if the catalogue is edited mid-conversation, a
  stale figure no longer recomputes, so it is refused.
- **What happens on refusal:** `DeskSession._ask` restores the basket (undoing tool side effects),
  retries **once** on the reasoning model (run level), and otherwise answers with the polite
  `REFUSAL`. No traceback.
- **XR-2 adds the other direction: `@input_guardrail`.** `guardrails.py::bargaining_guardrail` runs
  on the customer's message *before the first model call*, so a demand for a discount is triaged
  without paying for an answer that is not allowed to exist.
  - It reads only the **newest** user message, so a discount mentioned five turns ago cannot
    re-trigger on an unrelated question.
  - `InputGuardrailTripwireTriggered` carries the same `output_info` shape, so
    `escalation_reason_from_guardrail()` turns it into the same typed `EscalationReason`.
  - `DeskSession._bargaining()` then runs the **escalation agent directly** — the Desk agent is
    never invoked, which `tests/test_bargaining.py` proves by giving the Desk model no script at all.
  - Two rates, not one: "make it 20% cheaper" trips; "what is the kettle price, and is there a
    discount policy?" does not. The policy sentence asks *about* discounts rather than demanding one.
- **A block is the only event that makes a turn cost twice**, so `CostLedger.block(requoted=True)`
  counts it and the cost line says so: `guardrail blocked 1 answer(s), 1 re-quoted`.

### Page 8 · Beyond the brief — the three additions worth defending

- **XR-3, the price cache (`fastpath.py`).** A second "what does the kettle cost?" in the same
  session should not cost a second model call.
  - The key is the **resolved SKU** plus a **fingerprint of the catalogue** (SHA-256 of the file).
    Keying on the wording would miss paraphrases; keying on the SKU alone would serve a stale price
    after the file is edited. The fingerprint makes an edit invalidate the whole cache for free.
  - Hit → `Reply(kind="cached", model_calls=0)`. The turn is still counted, and the cost line
    reports it, so the saving is visible rather than hidden.
  - It is a **session** cache, not a global one: a new customer must not inherit prices, and a
    second Chainlit session must not leak into the first.
- **XR-1, order history (`orders.py`).** Each confirmed order is appended to `orders.jsonl` as one
  JSON object per line: `order_id`, `customer_id`, `placed_at`, and `items` as SKU and quantity
  **only**. No price is ever written, because a stored price would outlive the catalogue it came from.
  - `recent_orders` re-derives every figure from the catalogue in force and labels the result
    "at today's prices" — the same rule as `Quote`, applied to history.
  - A SKU that has since left the catalogue is **said so** rather than guessed at.
  - A damaged line is skipped, never raised (NFR-4): one bad line must not lose the whole history.
  - `as_shop_time()` normalises every stored timestamp to an aware datetime. A naive clock and an
    aware one cannot be compared, and the failure is a `TypeError` in the middle of a customer turn.
- **XR-4, any real shop.** The catalogue is a JSON file with one shape, and everything downstream
  reads it: `--catalogue` / `SHOP_DESK_CATALOGUE` for the file, guardrail prices, the pricing
  specialist's derivations, the cache fingerprint and the recalled order figures. `tests/
  test_real_catalogue.py` points the whole Desk at a *different* shop's catalogue and needs no
  code change — which is the point: a shop is a data change, not a rewrite.

### Parts 16–17 · Lifecycle hooks (agent level) and run hooks — FR-11

- **Run hooks:** `cost.py::CostHooks(RunHooks)`. `on_llm_end` records every model call in the
  Desk's run (agent, resolved model, tokens from `response.usage`).
- **Agent hooks:** `cost.py::PricingHooks(AgentHooks)`, set on the Pricing specialist **only**.
  It runs as a nested `Runner.run` inside a tool call. The Desk run's hooks do not see those calls,
  so the agent hooks record them.

### Part 18 · Custom runners — FR-11

- **What:** `AgentRunner` is the class behind `Runner.run`. `set_default_agent_runner()` swaps in a
  subclass for the whole process.
- **Where:** `cost.py::LedgerRunner`, installed in `configure_global()`. Every run passes through
  it, nested ones included, and it records a `RunRecord(turn, depth, agent, model, requests,
  input_tokens, output_tokens)`.
- **Numbers from the run context, not estimates:** tokens come from
  `result.context_wrapper.usage`, or from `exc.run_data.context_wrapper.usage` when a run raised.
- **Gotcha that shaped the design:** a nested agent-as-tool run **shares the parent's `Usage`
  object**. The runner records each run's *delta*, and turn totals sum only depth-0 runs. Otherwise
  the Pricing specialist's tokens would be counted twice.
- **Cost line** (format; the numbers here are illustrative): `Cost · 5 turn(s): 2 fast-path, 3 reasoning · 11 model call(s) · 9,812 in / 604 out
  tokens · models: gemini-2.5-flash ×9, gemini-2.5-pro ×2 · most expensive: turn 4 (3,120 tokens)`.

### Part 19 · Chainlit — FR-12

- **Where:** `app.py`. Chat profiles give **Walk-in / Regular** (sets `tier`). A settings slider
  sets the simulated hour. `@cl.on_chat_start` creates **one `DeskSession` per browser session**,
  so two windows never share a basket. `@cl.on_message` calls `session.ask()` and shows the reply,
  an escalation step with the handoff audit, and a cost step with the trace link.
  `@cl.on_chat_end` closes the trace.
- **Long conversations (history.py):** before each run, tool calls and outputs older than the last
  2 turns are dropped first, then turns beyond the last 6 customer turns. The basket, the draft
  order id and orders live in `ShopContext`, so **turn eleven still knows the basket**
  (`tests/test_session.py::test_turn_eleven_still_remembers_the_basket`).
- **Why tool noise goes first:** it is the bulkiest part of the history and the least useful.
  Its facts were already turned into the Desk's replies, and NFR-3 forces figures to be re-fetched
  each turn anyway.

### Part 20 · Practice with an agent CLI

The whole project is built by driving Claude Code / OpenCode from a spec. See
[`PROMPTS.md`](PROMPTS.md) for the prompt used at each phase. [`specs/`](specs/) holds the four
Phase 0 artifacts, and `git log --reverse` shows they came first.

---

## 3. Defending your work: answers

1. **Which model answered the last question, and which level decided that?**
   Check the cost line (`models: …`) or the trace's generation span. A plain question is answered
   by `FAST_MODEL`, decided by the **global** level, because the Desk declares no model. An order
   confirmation is answered by `REASONING_MODEL` at the **agent** level (Order clerk). A turn
   marked "re-quoted" used `REASONING_MODEL` at the **run** level.
2. **A price question costs one call. What did you give up?** The model never sees the tool
   result. So there is no rephrasing, no combining it with another fact ("…and yes, we deliver
   today"), no translation and no clarifying question in the same turn. The wording is a fixed
   template from `lookup_price`.
3. **The catalogue is edited mid-conversation?** The guardrail re-reads the file on every check
   and **recomputes** every issued quote from its stored derivation (SKU, qty, discount). A figure
   built on the old price no longer matches. The answer is refused, retried once on the reasoning
   model (which fetches fresh figures), or politely declined. Proof:
   `test_guardrail.py::test_tool_issued_totals_pass_and_are_recomputed_from_the_file`.
4. **What did the handoff filter remove, and how do you know the specialist didn't need it?**
   It removed every tool call and output (lookups, basket edits, specialist calls, the handoff call)
   and all but the last 6 history items. The audit prints the counts (in the unit test: "before: 24 items
   (12 message, 12 tool) → after: 6 items (6 message)"). The specialist's job is one empathetic
   handover message that must not quote figures. It needs what the customer said and why they were
   escalated, and those arrive via the recent messages and the typed reason in its instructions.
   `test_escalation.py` shows it answering from a history with no tool items.
5. **Why is the seasonal tool invisible rather than refused?** A refused tool still costs a model
   call, adds schema tokens to every request, and invites the model to try it and apologise.
   `is_enabled=False` removes it from the request entirely: zero tokens, zero temptation, nothing to
   explain.
6. **Where does the token count come from?** From the SDK's own `Usage`: `ModelResponse.usage` per
   call (hooks) and `RunContextWrapper.usage` per run (`LedgerRunner`). Nothing is estimated from
   string length.
7. **What does trimming drop first, and why is that safest?** Old tool calls and outputs (older than
   2 turns). Their content was already summarised in the Desk's replies, figures must be fetched
   fresh anyway (NFR-3), and removing a call/output *pair* together can't leave an orphan. The basket
   and orders live in context, so nothing the customer is buying is ever lost.
8. **Clones with `is`, and which sharing bites later?**
   `pricing.tools is base.tools is escalation.tools` → `True`.
   `pricing.output_guardrails is base.output_guardrails` → `True`.
   `pricing.instructions is base.instructions` → `False`.
   `pricing.model_settings is base.model_settings` → `False`.
   The shared `tools` **list** bites: appending a tool to one clone silently gives it to all three.

---

## 4. Definition of done → evidence

| Requirement | Evidence |
|---|---|
| Spec preceded code | `git log --reverse --stat`: the Phase 0 commit touches only `specs/` and `.gitignore` |
| Three config levels | `config.configure_global`, Order clerk `model=`, `DeskSession._requote`; `tests/test_model_levels.py` |
| Context never in the prompt | `tests/test_context.py` |
| Fast path costs one call | `tests/test_fast_path.py` (1 vs 2 calls); live: two traces |
| Order total recomputed | `tests/test_orders.py::test_planted_mismatch_is_caught` |
| Prices come from the file | `tests/test_guardrail.py::test_editing_the_catalogue_makes_the_same_answer_fail` |
| Tools differ by tier | `tests/test_tiers.py` |
| Escalation carries a typed reason | `tests/test_escalation.py` |
| History trimmed on transfer | `HandoffAudit.describe()`; `tests/test_escalation.py` |
| Ten turns and still coherent | `tests/test_session.py::test_turn_eleven_still_remembers_the_basket` |
| XR-1 order history outlives the session | `tests/test_orders_store.py::test_a_later_session_sees_an_earlier_order` |
| XR-2 the Desk model never sees a discount demand | `tests/test_bargaining.py` (Desk given no script) |
| XR-3 a repeat price question costs nothing | `tests/test_price_cache.py` (0 model calls, `kind="cached"`) |
| XR-3 a catalogue edit invalidates the cache | `tests/test_price_cache.py::test_editing_the_catalogue_costs_a_call_again` |
| XR-4 another shop needs no code change | `tests/test_real_catalogue.py` |
| XR-4 a blocked answer is counted | `tests/test_real_catalogue.py::test_a_guardrail_refusal_is_counted` |

Run everything offline with `uv run pytest -q` — 106 checks, no API keys needed
(`agents.testing.ScriptedModel`).
