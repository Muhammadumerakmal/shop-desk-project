# Shop Desk — Specification

## Objective

The Shop Desk answers customers of a small electronics shop (**Al-Noor Electronics**, prices in
PKR): what is in stock, what it costs, and "can I order three of them". It works only from
`catalogue.json`. When the customer confirms, it builds a **typed order**. It hands genuinely
stuck conversations to a **human escalation** agent.

The theme is **not wasting money**. A price question must not cost a reasoning model. The cheap
model is the default and only the agent that needs more overrides it. Escalation transfers a
trimmed conversation, not the whole log. No price reaches a customer unless it is in the
catalogue.

**Users.** Two kinds of customers: `walk_in` (default) and `regular` (loyalty customers). Shop
staff read the escalations and the cost line.

**Success looks like** one long browser conversation (10+ turns) in which price questions are
answered in one model call, an order is confirmed as a validated `Order` object, a bargaining
customer is escalated with a typed reason and a trimmed history, and the whole conversation is
one trace with a per-turn cost line.

## Assumptions

1. The model provider is **OpenAI**, using the Chat Completions API. One `OPENAI_API_KEY` serves
   both the model calls and the trace export to the OpenAI platform.
2. The shop runs on Pakistan time (UTC+5, no DST). It is open **10:00–21:00**, and same-day
   delivery is offered for orders confirmed before **17:00**.
3. Regular customers get a fixed **5 % loyalty discount**, available only through a tool.
4. Orders live in the session (in memory). Payment and delivery addresses are out of scope.
5. Model names are configuration (`FAST_MODEL`, `REASONING_MODEL`) so that newer OpenAI
   releases can be swapped in without code changes. The pair must accept `temperature` and
   `max_tokens`, which every agent here sends.

## Behaviour — functional requirements

### Phase 1 — catalogue and the fast path

**FR-1 — One catalogue, three levels of model configuration.**
Products live in `catalogue.json` and the Desk can reach them only through tools. The process
default model (cheap) is set globally at startup. The Order clerk overrides it on itself (agent
level). The re-quote path overrides it for one run (run level).
*Accept:* the three overrides are at three named places in code. A plain question is answered by
`FAST_MODEL`. Removing the global default changes exactly one thing: which model the
non-overriding agents (Desk and specialists) ask for. The Order clerk and the re-quote path are
unaffected.

**FR-2 — The customer lives in context.**
Every run receives a `ShopContext` (shop, currency, customer id, tier, plus per-session basket
state). Tools read it through `RunContextWrapper`. The prompt never contains it.
*Accept:* `add_to_basket` / `loyalty_discount` read `tier` and the basket through the wrapper, no
tool's JSON schema contains a wrapper parameter, and no resolved prompt contains the customer id.

**FR-3 — The fast path.**
"What does the kettle cost?" is answered **by the `lookup_price` tool's own output**. The Desk
stops at that tool, so the model never sees the result and never writes a sentence around it.
Every other question goes through the normal tool loop.
*Accept:* a price question costs **1 model call** and an order question costs **≥ 2**. The cost of
each path is stated. What we give up: the answer cannot be rephrased, combined with a second fact
or followed up in the same turn. It reads like a template, not like a person.

**FR-4 — Instructions that know the hour.**
The Desk's system prompt is built each turn from the shop facts and the clock. During open hours
before 17:00 it may promise same-day delivery. After 17:00 it promises next-day delivery. Outside
opening hours it promises nothing for today and says when the shop opens.
*Accept:* the same question at two simulated hours yields two different promises, and the resolved
prompt can be printed before any model call (`--show-prompt`).

### Phase 2 — orders, limits and truth

**FR-5 — A confirmed order is a typed object.**
When the customer confirms, the run's final output is an `Order` (`order_id`, `status`, `items`,
`total`), not prose. Python recomputes the total from the line items. If the model's total
disagrees, the mismatch is reported to the customer and in the log, and the recomputed total is the
one used.
*Accept:* a planted mismatch is caught and reported, and a correct order passes unchanged.

**FR-6 — No invented prices.**
An output guardrail on every customer-facing agent reads the finished answer. It refuses the answer
if it quotes an amount, a stock figure or a SKU that the current `catalogue.json` does not back.
Amounts are backed if they are a catalogue price, or a figure recomputed from a quote a tool
issued in this run. The file is re-read at check time. A refusal is caught: the turn is retried once
through the re-quote path. If that also fails, the customer gets a polite "I can't confirm that
price" message.
*Accept:* editing a price in the catalogue makes a previously passing answer fail. An order
containing an out-of-stock item never passes. No traceback reaches the customer.

**FR-7 — Tools that are off, and tools that are earned.**
`eid_gift_wrap` (a seasonal service) is switched off statically, so it appears in no schema.
`loyalty_discount` is offered only when `tier == "regular"`.
*Accept:* the same question as `walk_in` and as `regular` gives the model different tool lists, and
the seasonal tool is in neither.

**FR-8 — A pricing specialist that returns a number, and a turn budget.**
A Pricing specialist is exposed to the Desk as the tool `pricing_specialist`. It answers with a
figure only, which the Desk puts into its own sentence. Every conversation turn runs under a
ceiling of **6 agent turns** (`MAX_TURNS`). Hitting it is caught, the customer gets a polite
closing message, and the session ends.
*Accept:* the Desk's reply contains the specialist's number in the Desk's wording, the ceiling is 6,
and the customer's message on hitting it is documented.

**FR-9 — Specialists cloned from one base.**
The Pricing specialist and the Human escalation agent are `clone()`s of one *Specialist base*.
They differ only in name, instructions and model settings. Neither restates the model.
*Accept:* `is` comparisons show shared `tools`, `output_guardrails` and `model` and independent
`instructions` and `model_settings`.

### Phase 3 — escalation, lifecycle and interface

**FR-10 — Escalation carries a typed reason and a trimmed history.**
When the Desk gives up, it hands off to *Human escalation* with an `EscalationReason`
(`reason` is a `Literal`: `bargaining`, `complaint`, `unavailable_item`,
`customer_asked_for_human`, `stuck`). The transferred history is filtered.
**Removed:** every tool call and tool output (catalogue lookups, basket edits, specialist calls,
the handoff call itself) and all but the last 6 conversation messages.
**Kept:** the recent customer and Desk messages in order. The reason reaches the specialist
through its dynamic instructions, and the basket is still readable through context. That is all a
human handover needs: who is upset, about what, and what they were buying.
*Accept:* the escalation agent replies sensibly without tool noise, the reason is an enum value,
and the before/after item counts of the transferred history are shown.

**FR-11 — What every turn cost.**
Run-level hooks record every model call (agent, model, tokens). Agent-level hooks on the Pricing
specialist only record its nested calls. A custom runner records each run's model and token count
from the run context. After every turn a cost line is printed: turns, fast-path vs reasoning turns,
calls, input/output tokens and which model served each turn.
*Accept:* the cost line tells fast-path turns from reasoning turns, and the token numbers come from
`RunContextWrapper.usage`, not an estimate.

**FR-12 — A long conversation in a browser.**
A Chainlit app holds conversations of 10+ turns. History is kept per browser session. Before each
run it is trimmed: first, tool calls and outputs older than the last 2 turns are dropped. Then
turns beyond the last **6** customer turns are dropped. The basket and orders live in
`ShopContext`, so trimming never forgets them.
*Accept:* turn 11 still knows the basket or order, two browser windows have separate baskets, and the
rule for what is thrown away first (old tool noise) is documented.

**FR-13 — One conversation, one trace.**
Tracing is exported under the developer's own OpenAI key. A whole customer conversation is one
trace, with one span per customer turn labelled `fast-path` or `reasoning`.
*Accept:* the trace opens on the platform, and fast-path turns, reasoning turns and the most
expensive turn (the cost line names it too) can be pointed to.

### Phase 4 — beyond the brief (page 8, "If you finish early")

The brief's four closing suggestions, taken as requirements. Same rules as the rest: numbered,
testable, and checked offline.

**XR-1 — Persist orders, and answer "what did I order last week?".**
A confirmed order is appended to a JSONL store keyed by `customer_id`, with the timestamp and the
line items as **SKUs and quantities, never prices**. A `recent_orders(days)` tool re-derives every
figure from the *current* `catalogue.json` and issues a `Quote` per order, so the guardrail backs
the answer with no new rule (NFR-3 holds: a stored price is never trusted, a re-derived one is).
*Accept:* an order placed in session one is visible in a later session for the same `customer_id`,
the tool shows the age in days, and a price edited in the catalogue changes what "last week" costs
rather than replaying the old number. A store that cannot be read or written returns a sentence.

**XR-2 — An input guardrail that refuses bargaining and routes it to escalation.**
An `input_guardrail` on the Desk reads the customer's own words before the model is called. On
bargaining language (`\d+% off`, discount/rebate/coupon, "cheaper", "match the price", haggling) it
trips the wire with a typed `EscalationReason(reason="bargaining")`. The session catches the
tripwire and runs the **Human escalation** agent directly, so a haggler costs zero Desk calls.
*Accept:* a bargaining message never reaches the Desk model; the reply is the escalation agent's,
the reason is the enum value `bargaining`, and ordinary messages ("is it in stock?", "what are your
opening hours?") pass through untouched. The Desk keeps `escalate_to_human` for the other four
reasons.

**XR-3 — A cache so a repeated price question costs nothing at all.**
`lookup_price` stores its own output under a key of the **resolved SKUs plus a fingerprint of the
catalogue file's bytes**. `DeskSession` recognises a plain price question by pattern, resolves it
against the catalogue and, on a hit, answers from the cache **without running the model at all**.
Editing the catalogue changes the fingerprint and invalidates every entry.
*Accept:* the first kettle question costs 1 call, the repeat costs **0**, the cost line reports the
cached turn separately, and editing a price in `catalogue.json` makes the next repeat cost 1 call
again with the new figure. A question that is not a plain price question is never served from cache.

**XR-4 — A real catalogue, and a count of what the guardrail blocks.**
`--catalogue PATH` (or `SHOP_DESK_CATALOGUE`) points the whole Desk at another shop's
`catalogue.json` in the same shape. The ledger counts every guardrail refusal and every re-quote,
and the cost line reports them, because a blocked answer is the one that costs **twice**.
*Accept:* the Desk runs against a second catalogue file with no code change, and after any
conversation the CLI prints how many answers the guardrail blocked and how many were re-quoted.

## Non-functional requirements

- **NFR-1 Secrets.** Keys in `.env`, gitignored, never printed. A missing key fails at startup
  with a sentence.
- **NFR-2 Cost.** Every agent declares its own settings. The cheap model is the default and every
  override is justified in `plan.md`.
- **NFR-3 Truth.** No price, stock figure or SKU reaches a customer unless it came from the
  catalogue this run.
- **NFR-4 Failure.** A tool meeting bad data returns a sentence. A tool that raises into the
  runner is a defect.
- **NFR-5 Provenance.** `git log` shows the four Phase 0 artifacts before the first code commit.

## What the Desk deliberately will not do

1. **Bargain or invent discounts.** Prices are the catalogue's. The only discount is the fixed
   5 % loyalty rate, and it exists only as a tool for regular customers. Haggling is escalated.
2. **Take payment or personal details.** No card numbers, addresses or phone numbers. A
   confirmed order is handed to staff, who arrange payment and delivery.
3. **Talk about anything outside the catalogue.** No products the shop does not list, no repair
   advice, no competitor prices. It says so and offers what the catalogue has.

## Success criteria (definition of done)

| Requirement | How it is checked |
|---|---|
| Spec preceded code | `git log --reverse` shows the Phase 0 commit first, with no `.py` files |
| Three config levels present | `tests/test_model_levels.py`, plus pointing at `config.py`, `desk_agents.py`, `session.py` |
| Context never in the prompt | `tests/test_context.py` (schema has no wrapper, prompt has no customer id) |
| Fast path costs one call | `tests/test_fast_path.py` (1 call vs ≥ 2), plus two live traces |
| Order total recomputed | `tests/test_orders.py` (planted mismatch caught) |
| Prices come from the file | `tests/test_guardrail.py` (edit a price, answer fails) |
| Tools differ by tier | `tests/test_tiers.py` |
| Escalation carries a typed reason | `tests/test_escalation.py` |
| History is trimmed on transfer | `tests/test_escalation.py` (before/after counts) |
| Ten turns and still coherent | `tests/test_session.py` (turn 11 remembers the basket) |
| Orders persist and can be recalled | `tests/test_orders_store.py` (placed in one session, seen in the next) |
| Bargaining never reaches the Desk model | `tests/test_bargaining.py` (Desk call count unchanged) |
| A repeated price question costs 0 calls | `tests/test_price_cache.py` (1 then 0, then 1 after a price edit) |
| Any catalogue can be used | `tests/test_real_catalogue.py` (second file, no code change) + `--catalogue` |

## Open questions

- Which OpenAI model names are current and cheap enough for the grader's account. Both are
  env-configurable. `gpt-5.x`-style reasoning models are ruled out: they reject `temperature`
  values other than 1 and require `max_completion_tokens` instead of `max_tokens`.
- Whether the order store should grow beyond one JSONL file. Fine as a file for one shop.
