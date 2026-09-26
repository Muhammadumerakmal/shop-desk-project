# Shop Desk â€” Tasks

Ordered by dependency. Each task is independently verifiable and names the requirement it serves.
`Verify` commands run offline unless marked **live**.

## Phase 0 â€” specify (0:00â€“0:35)

- [x] **T00** Write and commit `constitution.md`, `spec.md`, `plan.md`, `tasks.md` in their own commit, with no code.
  - Serves: NFR-5 Â· Verify: `git log --stat -1` lists only `specs/` and `.gitignore`

## Phase 1 â€” catalogue and the fast path (0:35â€“1:05)

- [x] **T01** Project skeleton: `pyproject.toml`, `.env.example`, `catalogue.json` (8 products, at least one out of stock).
  - Serves: FR-1, NFR-1 Â· Verify: `uv sync` succeeds
- [x] **T02** `config.py`: `Settings`, `load_settings()` raising `StartupError` with one sentence per missing key, `configure_global()` setting the OpenAI client, the chat-completions API, the default model and the tracing key.
  - Serves: FR-1 (global), NFR-1, FR-13 Â· Verify: `pytest tests/test_config.py`
- [x] **T03** `catalogue.py`: `load_catalogue()` (reads the file every call), `find()`, `money()`.
  - Serves: FR-1 Â· Verify: `pytest tests/test_catalogue.py`
- [x] **T04** `context.py`: `ShopContext` with basket, clock and ledger. `schemas.py`: `QuoteLine`.
  - Serves: FR-2 Â· Verify: imported by tests
- [x] **T05** `tools.py`: `lookup_price`, `search_catalogue`, `add_to_basket`, `remove_from_basket`, `view_basket`, all `@never_raises`.
  - Serves: FR-1, FR-2, NFR-4 Â· Verify: `pytest tests/test_context.py tests/test_tools.py`
- [x] **T06** `instructions.py`: Desk instructions built from shop facts and `ctx.now()`, with 3 delivery promises.
  - Serves: FR-4 Â· Verify: `pytest tests/test_instructions.py`
- [x] **T07** `desk_agents.py` v1: Desk with `StopAtTools(["lookup_price"])`, no model. `session.py` v1: `DeskSession.ask()`. `cli.py` with `--hour`, `--tier`, `--show-prompt`.
  - Serves: FR-1, FR-3, FR-4 Â· Verify: `pytest tests/test_fast_path.py tests/test_model_levels.py`

## Phase 2 â€” orders, limits and truth (1:05â€“1:40)

- [x] **T08** `schemas.py`: `LineItem`, `Order`, `check_order_total()` â†’ `OrderCheck`.
  - Serves: FR-5 Â· Verify: `pytest tests/test_orders.py`
- [x] **T09** Order clerk: `model=REASONING_MODEL` (agent level), `output_type=Order`, Desk handoff. Session renders the order from Python, reports mismatch, clears basket.
  - Serves: FR-1 (agent), FR-5 Â· Verify: `pytest tests/test_orders.py tests/test_model_levels.py`
- [x] **T10** `guardrails.py`: catalogue guardrail for text and `Order`, attached to every customer-facing agent. Session catches the tripwire, runs the re-quote path (run level), then politely refuses.
  - Serves: FR-6, FR-1 (run), NFR-3 Â· Verify: `pytest tests/test_guardrail.py`
- [x] **T11** `loyalty_discount` (`is_enabled` by tier) and `eid_gift_wrap` (`is_enabled=False`).
  - Serves: FR-7 Â· Verify: `pytest tests/test_tiers.py`
- [x] **T12** Specialist base + `quote` tool. Pricing clone as `pricing_specialist` tool with number extractor. `MAX_TURNS=6` caught in session.
  - Serves: FR-8 Â· Verify: `pytest tests/test_specialists.py tests/test_session.py::test_turn_ceiling`
- [x] **T13** Human escalation clone. `is` checks for shared/independent attributes.
  - Serves: FR-9 Â· Verify: `pytest tests/test_specialists.py::test_clones_share_and_differ`

## Phase 3 â€” escalation, lifecycle and interface (1:40â€“1:55)

- [x] **T14** `EscalationReason`, `escalate_to_human` handoff with `on_handoff`, `escalation_filter` (`remove_all_tools` + last 6 messages) writing a `HandoffAudit`. Dynamic escalation instructions read the reason from context.
  - Serves: FR-10 Â· Verify: `pytest tests/test_escalation.py`
- [x] **T15** `cost.py`: `CostHooks` (run level), `PricingHooks` (agent level, Pricing only), `LedgerRunner` (custom runner) and cost line.
  - Serves: FR-11 Â· Verify: `pytest tests/test_cost.py`
- [x] **T16** `history.py`: `trim_history(items, keep_turns=6, keep_tool_turns=2)`.
  - Serves: FR-12 Â· Verify: `pytest tests/test_history.py`
- [x] **T17** `app.py`: Chainlit with chat profiles (walk_in / regular), per-session `DeskSession`, order table, escalation step and cost line.
  - Serves: FR-12 Â· Verify: **live** two browser windows, 11 turns
- [x] **T18** One trace per conversation: session-owned trace with a span per turn, labelled by kind.
  - Serves: FR-13 Â· Verify: `pytest tests/test_session.py::test_one_trace_per_conversation`, **live** open the trace
- [x] **T19** `cli --demo`: scripted conversation (13 turns to cover the core, 15 with Phase 4) with
  one order, one escalation, one cache hit, one recall and one refused discount.
  - Serves: Demo Â· Verify: **live** `uv run python -m shop_desk.cli --demo`

## Phase 4 â€” beyond the brief (page 8, "If you finish early")

- [x] **T20** `orders.py`: JSONL store (`PlacedOrder`, `record_order`, `recent_orders`), storing SKUs
  and quantities only. `recent_orders` tool re-derives every figure and issues a `Quote` per order.
  Session records each confirmed order. Desk instructions gain the tool. `as_shop_time()` keeps
  every stored timestamp aware, so a naive clock and an aware one still compare.
  - Serves: XR-1 Â· Verify: `pytest tests/test_orders_store.py` (12 passed)
- [x] **T21** `detect_bargaining()` + `bargaining_guardrail` as an **input** guardrail on the Desk.
  Session catches `InputGuardrailTripwireTriggered`, sets a typed `bargaining` reason and runs the
  escalation agent directly. `test_escalation.py` switches its handoff turn to a complaint, since
  bargaining no longer reaches the Desk model.
  - Serves: XR-2 Â· Verify: `pytest tests/test_bargaining.py` (17 passed)
- [x] **T22** `fastpath.py`: `price_question()`, `PriceCache` keyed by resolved SKUs + catalogue
  fingerprint. `lookup_price` writes; `DeskSession` reads and answers at 0 model calls with
  `kind="cached"`. `Catalogue.fingerprint` added; `TurnKind` and the cost line learn "cached".
  - Serves: XR-3 Â· Verify: `pytest tests/test_price_cache.py` (9 passed)
- [x] **T23** `--catalogue PATH` / `SHOP_DESK_CATALOGUE` in the CLI, and `guardrail_blocks` +
  `requotes` on the ledger, reported by `cost_line()` and printed at the end of every CLI run.
  - Serves: XR-4 Â· Verify: `pytest tests/test_real_catalogue.py` (7 passed), **live**
    `cli --demo --catalogue mine.json`

## Cut list (in this order)

FR-11 â†’ FR-9 â†’ the agent-level hooks inside FR-11. Never cut FR-3 or FR-6.

## Status

Phases 0â€“3 are implemented and their offline checks pass (`uv run pytest -q`). The
**live** checks (T17 two browser windows over 11 turns, T18 opening the trace on the platform, T19
the demo against real OpenAI models) need a real `OPENAI_API_KEY` in `.env`. T19 has been run
against `gpt-4.1-mini` / `gpt-4.1` and passes; T17 and T18 are still to be run by the
developer before the demo.

Phase 4 (T20â€“T23) takes the brief's four "if you finish early" suggestions as requirements; the
spec was amended and committed before any of that code was written.
