# Shop Desk — Constitution

The rules this build may not break. If code and this file disagree, the code is wrong.
Every article is checkable: each one names the requirement it protects and how a reviewer
verifies it.

## Article 1 — Models and who may override them (FR-1, NFR-2)

1. The **process default is the cheap model** (`FAST_MODEL`, default `gemini-2.5-flash`). It is
   set **once, globally**, in `shop_desk/config.py::configure_global()`. No other file sets a
   process-wide model.
2. Exactly **two** places may run the reasoning model (`REASONING_MODEL`, default
   `gemini-2.5-pro`):
   - **Agent level:** the *Order clerk* sets `model=` on itself, because turning a basket into a
     typed, priced order is the one job that needs reasoning.
   - **Run level:** the *re-quote path* passes `RunConfig(model=REASONING_MODEL)` for a single
     run, used only after the catalogue guardrail has refused an answer.
3. Every other agent (the Desk, the specialist base and its clones) declares **no model** and
   inherits the global default. A clone never restates the model it inherits.
4. Every agent declares its own `ModelSettings`. Each override is justified in `plan.md`.

## Article 2 — Truth comes from the catalogue (FR-6, NFR-3)

1. `catalogue.json` is the only source of products, prices, stock and SKUs. The model never sees
   it except through tool output.
2. **No price, stock figure or SKU reaches a customer unless it came from the catalogue during
   this run.** An output guardrail re-reads `catalogue.json` at check time and compares every
   quoted amount, stock figure and SKU with it. This is a data check, not an LLM judgement.
3. An out-of-stock item is never sold: tools refuse to basket it and the guardrail refuses any
   `Order` that contains it or exceeds stock.
4. A confirmed order's total is **recomputed in Python** from its line items. The model's total
   is never trusted. A mismatch is reported, not silently corrected.

## Article 3 — Secrets live only in `.env` (NFR-1)

1. `GEMINI_API_KEY` (model calls) and `OPENAI_API_KEY` (trace export) are read from `.env` or the
   environment. `.env` is gitignored and `.env.example` holds placeholders only.
2. Keys are never printed, logged, put in a prompt, or put in a trace. `Settings.__repr__` masks
   them.
3. A missing key stops the program **at startup, with one plain sentence** that says which key is
   missing and where it goes. Never a stack trace.

## Article 4 — No tool raises into the runner (NFR-4)

1. Every function tool catches its own failures (missing SKU, bad quantity, unreadable catalogue)
   and **returns a sentence the model can use**.
2. A tool exception reaching the runner is a defect, even if the SDK would have caught it.
3. Run-level failures (guardrail tripwire, turn ceiling, provider error) are caught by the session
   layer and turned into a polite customer message. The customer never sees a traceback.

## Article 5 — The customer lives in context, not in the prompt (FR-2)

1. `ShopContext` is passed to every run and read by tools through `RunContextWrapper`.
2. No prompt ever contains the customer id or tier. Tool schemas never expose the wrapper.
3. Dynamic instructions may use shop-level facts (shop name, currency, opening hours, the clock),
   never customer-level ones.

## Article 6 — Conversations stay affordable (FR-3, FR-10, FR-12)

1. A plain price question costs **one model call** (the fast path). Nothing may add a second one.
2. Escalation transfers a **trimmed** history: tool noise is removed and only recent turns travel.
3. Chat history is trimmed past a fixed turn count. State that must survive (the basket, orders)
   lives in `ShopContext`, never only in history.
4. Every conversation runs under a turn ceiling (`MAX_TURNS`) that is caught and ends politely.

## Article 7 — Process (NFR-5)

1. **Specification before implementation.** `constitution.md`, `spec.md`, `plan.md` and
   `tasks.md` are committed before the first source file, in a commit with no code.
2. A behaviour change updates the spec first, then the code.
3. Every requirement has an automated check in `tests/` that runs without API keys, plus a live
   demonstration path (`python -m shop_desk.cli`).

## Boundaries

| Always | Ask first | Never |
|---|---|---|
| Run `uv run pytest` before each commit | Adding a dependency | Commit `.env` or any key |
| Name the FR a change serves in the commit | Changing the model defaults | Let a tool raise |
| Keep prices, stock and SKUs flowing from tools | Changing `MAX_TURNS` or history size | Put a price, SKU or customer id in a prompt |
| Update `spec.md` before changing behaviour | Changing the Order schema | Trust a model-computed total |
