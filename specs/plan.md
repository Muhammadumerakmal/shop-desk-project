# Shop Desk — Plan

## Tech stack

| Piece | Choice |
|---|---|
| Language | Python 3.12 (managed by `uv`) |
| Agent framework | `openai-agents` 0.22.x (OpenAI Agents SDK) |
| Model provider | Gemini, OpenAI-compatible endpoint `https://generativelanguage.googleapis.com/v1beta/openai/`, Chat Completions API |
| Structured data | `pydantic` v2 models, `dataclasses` for context |
| UI | `chainlit` 2.x |
| Config | `python-dotenv` |
| Tests | `pytest` + `pytest-asyncio`, SDK `agents.testing.ScriptedModel` (no network) |

## Commands

```
uv sync                                   # install
cp .env.example .env                      # then add GEMINI_API_KEY, OPENAI_API_KEY
uv run pytest -q                          # all offline checks
uv run python -m shop_desk.cli            # terminal desk (walk_in, live clock)
uv run python -m shop_desk.cli --tier regular --hour 22 --show-prompt
uv run python -m shop_desk.cli --demo     # scripted 12-turn conversation: order + escalation
uv run chainlit run app.py -w             # browser UI (FR-12)
```

## Project structure

```
catalogue.json            the only source of products (FR-1, NFR-3)
app.py                    Chainlit entry point (FR-12)
shop_desk/
  config.py               Settings, StartupError, configure_global()   FR-1 global level, NFR-1, FR-13
  catalogue.py            Product/Catalogue, load_catalogue(), money() FR-1
  context.py              ShopContext, Quote                            FR-2
  schemas.py              LineItem, Order, EscalationReason, QuoteLine, check_order_total()  FR-5, FR-10
  instructions.py         dynamic instructions per turn                 FR-4
  tools.py                function tools                                FR-1, FR-2, FR-3, FR-7
  guardrails.py           catalogue output guardrail                    FR-6
  handoff_filters.py      trimmed escalation history + audit            FR-10
  desk_agents.py          build_agents(settings): Desk, Order clerk, base + clones   FR-1, FR-3, FR-8, FR-9, FR-10
  cost.py                 RunHooks, AgentHooks, LedgerRunner, cost line FR-11
  history.py              trim_history()                                FR-12
  session.py              DeskSession.ask(): one customer turn          FR-1 run level, FR-5, FR-6, FR-8, FR-13
  cli.py                  terminal interface and scripted demo
tests/                    one file per requirement group, offline
specs/                    constitution, spec, plan, tasks
```

## Agents

| Agent | Model | ModelSettings | Why this model |
|---|---|---|---|
| **Shop Desk** (entry) | *none → global default* `FAST_MODEL` | `temperature=0.3`, `max_tokens=400` | Handles most turns: lookups, basket edits, small talk. Cheap is enough. `tool_use_behavior=StopAtTools(["lookup_price"])` gives the fast path. |
| **Order clerk** (handoff target) | **agent level:** `REASONING_MODEL` | `temperature=0` | Must turn a basket into an exact typed `Order` (`output_type=Order`). The only agent that pays for reasoning on every call, and only when a customer confirms. |
| **Specialist base** (never run directly) | *none → global default* | `temperature=0.2` | Template holding the shared tool (`quote`) and shared guardrail. |
| **Pricing specialist** = `base.clone(...)` | *inherits* (not restated) | `temperature=0`, `max_tokens=60` | Arithmetic via the `quote` tool, answers digits only. Exposed to the Desk with `as_tool`. |
| **Human escalation** = `base.clone(...)` | *inherits* (not restated) | `temperature=0.4`, `max_tokens=350` | Writes one empathetic handover message. Cheap model is enough. |
| **Re-quote run** | **run level:** `RunConfig(model=REASONING_MODEL)` | — | Only after the guardrail refused the cheap model's answer. Retries that one turn once with the stronger model. |

Handoffs: Desk → Order clerk (`transfer_to_order_clerk`), Desk → Human escalation
(`escalate_to_human`, `input_type=EscalationReason`, `input_filter=escalation_filter`).
Agent as tool: Desk → Pricing specialist (`pricing_specialist`).
Agent hooks (`PricingHooks`) are attached to the Pricing clone after cloning. Because the
specialist runs as a nested `Runner.run`, the run-level hooks of the Desk run do not see its model
calls.

## Tools — inputs and outputs

Every tool reads `ShopContext` through `RunContextWrapper` (not in schema). Every tool returns a
`str` and never raises: failures come back as a sentence (`NFR-4`, via the `@never_raises`
decorator plus explicit checks).

| Tool | Takes | Returns | Notes |
|---|---|---|---|
| `lookup_price` | `product: str` (name words or SKU) | `"Electric kettle 1.7L (KTL-01) is PKR 4,200 — 12 in stock."` | **Fast path**: the Desk stops here (FR-3). |
| `search_catalogue` | `query: str` (`""` = everything) | one line per product: SKU, name, price, availability | normal loop |
| `add_to_basket` | `sku: str, qty: int` | confirmation with basket size, or why not (unknown SKU, qty < 1, out of stock, > stock) | mutates `ctx.basket` |
| `remove_from_basket` | `sku: str` | confirmation or "not in basket" | |
| `view_basket` | — | lines, line totals, total, draft order id | issues a `Quote` |
| `loyalty_discount` | `sku: str, qty: int` | discounted total at 5 % | `is_enabled`: `tier == "regular"` (FR-7). Issues a `Quote` |
| `eid_gift_wrap` | `sku: str` | — | `is_enabled=False`, off statically (FR-7) |
| `quote` (specialist) | `lines: list[QuoteLine]` | `"TOTAL=12600"` plus a breakdown | issues a `Quote` |
| `pricing_specialist` | `input: str` | the figure as digits, e.g. `"12600"` | `Pricing.as_tool(custom_output_extractor=…)` (FR-8) |

## Structures crossing a boundary

```python
@dataclass
class ShopContext:                     # every run, every tool (FR-2)
    shop: str
    currency: str
    customer_id: str
    tier: str = "walk_in"                         # "walk_in" | "regular"
    basket: dict[str, int]                        # sku -> qty   (survives history trimming)
    orders: list[Order]                           # confirmed this session
    draft_order_id: str                           # stable until an order is confirmed
    issued_quotes: list[Quote]                    # reset every run; read by the guardrail
    escalation: EscalationReason | None
    handoff_audit: HandoffAudit | None
    clock: Callable[[], datetime] | None          # simulated hour for FR-4
    ledger: CostLedger                            # FR-11

@dataclass(frozen=True)
class Quote:                                      # derivation, not just a number
    lines: tuple[tuple[str, int], ...]            # (sku, qty)
    discount_rate: float = 0.0

class LineItem(BaseModel): sku: str; qty: int; unit_price: float
class Order(BaseModel): order_id: str; status: Literal["draft","confirmed","escalated"]; items: list[LineItem]; total: float
class QuoteLine(BaseModel): sku: str; qty: int
class EscalationReason(BaseModel):
    reason: Literal["bargaining","complaint","unavailable_item","customer_asked_for_human","stuck"]
    note: str                                     # <= 120 chars, for the human

@dataclass
class Reply:                                      # session -> UI
    text: str; kind: Literal["fast-path","reasoning","refused","ended"]
    order: Order | None; order_check: OrderCheck | None
    escalation: EscalationReason | None; handoff_audit: HandoffAudit | None
    cost_line: str
```

## How the guardrail decides (FR-6)

1. Re-read `catalogue.json`.
2. Extract from the answer:
   - amounts next to `PKR`/`Rs`
   - stock claims (`N in stock` / `N left` / `N available`)
   - SKUs (`[A-Z]{3}-\d{2}`)
3. Allowed amounts are every catalogue unit price, plus every figure **recomputed from the
   current file** for each `Quote` issued in this run: unit prices, line totals, subtotal,
   discount and total. Allowed stock figures are the catalogue's stock values. Allowed SKUs are
   the catalogue's SKUs.
4. For an `Order`, every item's SKU must exist. `unit_price` must equal the catalogue price, or
   the 5 %-discounted price for a regular customer. `qty` must be ≥ 1 and ≤ stock, and stock
   must be > 0.
5. Anything else trips the wire. `DeskSession` catches `OutputGuardrailTripwireTriggered`, retries
   once with the run-level reasoning model, then falls back to a polite refusal.

Because quotes store their **derivation**, editing the catalogue mid-conversation makes any stale
figure fail on the next check. That is the intended answer to "what happens if the catalogue is
edited".

## Session turn (FR-8, FR-12, FR-13)

```
ask(text):
  if ended -> polite "conversation closed"
  ctx.issued_quotes.clear()
  input = trim_history(history) + [user text]
  with conversation trace set current, custom_span("turn N"):
      try  run(Desk, max_turns=6, hooks=CostHooks)
      except OutputGuardrailTripwireTriggered: run again with RunConfig(model=REASONING_MODEL)
          except tripwire again: polite refusal
      except MaxTurnsExceeded: polite goodbye, ended = True
      except AgentsException/APIError: polite "system trouble" sentence
  if final_output is Order: check_order_total -> report mismatch, store, clear basket
  history = result.to_input_list(); ledger closes the turn -> cost line
```

The trace is created once per session (`trace(...).start()`) and set as current for each turn,
so every run of the conversation attaches to one trace. It is finished when the session closes.

## Model-configuration levels at a glance (FR-1)

| Level | Where | What |
|---|---|---|
| Global | `config.configure_global()` | `set_default_openai_client(gemini, use_for_tracing=False)`, `set_default_openai_api("chat_completions")`, `OPENAI_DEFAULT_MODEL=FAST_MODEL` |
| Agent | `desk_agents.build_agents()` | `Order clerk: model=settings.reasoning_model` |
| Run | `session.DeskSession._requote()` | `RunConfig(model=settings.reasoning_model)` |

Deleting the global default makes `Desk` and the clones resolve the SDK's built-in default name,
which the Gemini endpoint does not serve. That is the only behaviour that changes.

## Testing strategy

- Offline and deterministic: a `ScriptedProvider` returns `agents.testing.ScriptedModel`s by model
  name and records which name each agent asked for. No API key is needed.
- One test file per requirement group, named in the success-criteria table of `spec.md`.
- Pure functions (catalogue, guardrail extraction, totals, trimming, filters) are tested directly.
  Agent wiring is tested through `DeskSession` with scripted models.
- A live run (`cli --demo`) is the manual acceptance for traces and real model behaviour.

## Code style

```python
@function_tool
@never_raises
def add_to_basket(ctx: RunContextWrapper[ShopContext], sku: str, qty: int) -> str:
    """Add a quantity of one catalogue SKU to the customer's basket."""
    product = load_catalogue().get(sku)
    if product is None:
        return f"There is no product with SKU {sku!r}. Use search_catalogue to find the right SKU."
    ...
```

snake_case, type hints everywhere, docstrings on every tool (they become tool descriptions),
module docstring naming the FRs a file serves, and no print outside `cli.py` and `app.py`.

## Risks

| Risk | Mitigation |
|---|---|
| Gemini compat endpoint rejects a parameter (strict schema, `parallel_tool_calls`) | Only use temperature/max_tokens; keep `Order` schema flat |
| Model repeats an old total from history, so the guardrail trips | Desk instructions: always re-fetch figures; re-quote path retries |
| Model calls `lookup_price` for a non-price question | Tool description scoped to "only the price/stock of one product" |
| Trace export fails without an OpenAI key | Missing `OPENAI_API_KEY` fails at startup unless `SHOP_DESK_TRACING=off` |
