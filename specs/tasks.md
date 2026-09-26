# Shop Desk — Tasks

Ordered by dependency. Each task is independently verifiable and names the requirement it serves.
`Verify` commands run offline unless marked **live**.

## Phase 0 — specify (0:00–0:35)

- [x] **T00** Write and commit `constitution.md`, `spec.md`, `plan.md`, `tasks.md` in their own commit, with no code.
  - Serves: NFR-5 · Verify: `git log --stat -1` lists only `specs/` and `.gitignore`

## Phase 1 — catalogue and the fast path (0:35–1:05)

- [ ] **T01** Project skeleton: `pyproject.toml`, `.env.example`, `catalogue.json` (8 products, at least one out of stock).
  - Serves: FR-1, NFR-1 · Verify: `uv sync` succeeds
- [ ] **T02** `config.py`: `Settings`, `load_settings()` raising `StartupError` with one sentence per missing key, `configure_global()` setting the Gemini client, the chat-completions API, the default model and the tracing key.
  - Serves: FR-1 (global), NFR-1, FR-13 · Verify: `pytest tests/test_config.py`
- [ ] **T03** `catalogue.py`: `load_catalogue()` (reads the file every call), `find()`, `money()`.
  - Serves: FR-1 · Verify: `pytest tests/test_catalogue.py`
- [ ] **T04** `context.py`: `ShopContext` with basket, clock and ledger. `schemas.py`: `QuoteLine`.
  - Serves: FR-2 · Verify: imported by tests
- [ ] **T05** `tools.py`: `lookup_price`, `search_catalogue`, `add_to_basket`, `remove_from_basket`, `view_basket`, all `@never_raises`.
  - Serves: FR-1, FR-2, NFR-4 · Verify: `pytest tests/test_context.py tests/test_tools.py`
- [ ] **T06** `instructions.py`: Desk instructions built from shop facts and `ctx.now()`, with 3 delivery promises.
  - Serves: FR-4 · Verify: `pytest tests/test_instructions.py`
- [ ] **T07** `desk_agents.py` v1: Desk with `StopAtTools(["lookup_price"])`, no model. `session.py` v1: `DeskSession.ask()`. `cli.py` with `--hour`, `--tier`, `--show-prompt`.
  - Serves: FR-1, FR-3, FR-4 · Verify: `pytest tests/test_fast_path.py tests/test_model_levels.py`

## Phase 2 — orders, limits and truth (1:05–1:40)

- [ ] **T08** `schemas.py`: `LineItem`, `Order`, `check_order_total()` → `OrderCheck`.
  - Serves: FR-5 · Verify: `pytest tests/test_orders.py`
- [ ] **T09** Order clerk: `model=REASONING_MODEL` (agent level), `output_type=Order`, Desk handoff. Session renders the order from Python, reports mismatch, clears basket.
  - Serves: FR-1 (agent), FR-5 · Verify: `pytest tests/test_orders.py tests/test_model_levels.py`
- [ ] **T10** `guardrails.py`: catalogue guardrail for text and `Order`, attached to every customer-facing agent. Session catches the tripwire, runs the re-quote path (run level), then politely refuses.
  - Serves: FR-6, FR-1 (run), NFR-3 · Verify: `pytest tests/test_guardrail.py`
- [ ] **T11** `loyalty_discount` (`is_enabled` by tier) and `eid_gift_wrap` (`is_enabled=False`).
  - Serves: FR-7 · Verify: `pytest tests/test_tiers.py`
- [ ] **T12** Specialist base + `quote` tool. Pricing clone as `pricing_specialist` tool with number extractor. `MAX_TURNS=6` caught in session.
  - Serves: FR-8 · Verify: `pytest tests/test_specialists.py tests/test_session.py::test_turn_ceiling`
- [ ] **T13** Human escalation clone. `is` checks for shared/independent attributes.
  - Serves: FR-9 · Verify: `pytest tests/test_specialists.py::test_clones_share_and_differ`

## Phase 3 — escalation, lifecycle and interface (1:40–1:55)

- [ ] **T14** `EscalationReason`, `escalate_to_human` handoff with `on_handoff`, `escalation_filter` (`remove_all_tools` + last 6 messages) writing a `HandoffAudit`. Dynamic escalation instructions read the reason from context.
  - Serves: FR-10 · Verify: `pytest tests/test_escalation.py`
- [ ] **T15** `cost.py`: `CostHooks` (run level), `PricingHooks` (agent level, Pricing only), `LedgerRunner` (custom runner) and cost line.
  - Serves: FR-11 · Verify: `pytest tests/test_cost.py`
- [ ] **T16** `history.py`: `trim_history(items, keep_turns=6, keep_tool_turns=2)`.
  - Serves: FR-12 · Verify: `pytest tests/test_history.py`
- [ ] **T17** `app.py`: Chainlit with chat profiles (walk_in / regular), per-session `DeskSession`, order table, escalation step and cost line.
  - Serves: FR-12 · Verify: **live** two browser windows, 11 turns
- [ ] **T18** One trace per conversation: session-owned trace with a span per turn, labelled by kind.
  - Serves: FR-13 · Verify: `pytest tests/test_session.py::test_one_trace_per_conversation`, **live** open the trace
- [ ] **T19** `cli --demo`: scripted 12-turn conversation with one order and one escalation.
  - Serves: Demo · Verify: **live** `uv run python -m shop_desk.cli --demo`

## Cut list (in this order)

FR-11 → FR-9 → the agent-level hooks inside FR-11. Never cut FR-3 or FR-6.
