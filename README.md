# Shop Desk

A spec-driven customer desk for a small shop (**Al-Noor Electronics**, prices in PKR), built on the
**OpenAI Agents SDK** with **Gemini** models and a **Chainlit** UI. It answers what's in stock and what it
costs, builds a **typed order** when the customer confirms, and hands genuinely stuck conversations to a
human, **without wasting money**:

- a price question costs **one model call** (fast path);
- the cheap model is the process default; only the Order clerk and a one-run re-quote use the reasoning model;
- escalation transfers a **trimmed** history with a **typed** reason;
- **no price, stock figure or SKU** reaches a customer unless `catalogue.json` backs it this run.

| Read | For |
|---|---|
| [`specs/`](specs/) | constitution, spec, plan, tasks (Phase 0, committed before any code) |
| [`concepts.md`](concepts.md) | every SDK concept → where it lives here and why, plus defence answers |
| [`PROMPTS.md`](PROMPTS.md) | the prompts that drive a coding agent through each phase |

## Quick start

```bash
uv sync
cp .env.example .env          # add GEMINI_API_KEY and OPENAI_API_KEY (for traces)
uv run pytest -q              # 61 offline checks, no keys needed
uv run chainlit run app.py -w # browser UI
```

Terminal:

```bash
uv run python -m shop_desk.cli                           # chat (walk-in, live clock)
uv run python -m shop_desk.cli --tier regular --hour 22 --show-prompt
uv run python -m shop_desk.cli --demo                    # 13 turns: fast path, order, escalation
```

REPL commands: `/basket`, `/cost`, `/prompt`, `/quit`.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | required | model calls (Gemini OpenAI-compatible endpoint) |
| `OPENAI_API_KEY` | required unless tracing is off | trace export to platform.openai.com |
| `SHOP_DESK_TRACING` | `on` | `off` runs without tracing |
| `FAST_MODEL` | `gemini-2.5-flash` | process default (global level) |
| `REASONING_MODEL` | `gemini-2.5-pro` | Order clerk (agent level) and re-quote (run level) |

## Layout

```
catalogue.json        the only source of products
app.py                Chainlit UI (FR-12)
shop_desk/
  config.py           settings, secrets, global model default, custom runner install
  catalogue.py        load / find / money
  context.py          ShopContext (the customer lives here, never in a prompt)
  tools.py            function tools (fast path, basket, tier-gated, seasonal, quote)
  instructions.py     dynamic Desk + escalation instructions
  pricing.py          the one derivation of figures from the catalogue
  guardrails.py       catalogue output guardrail
  schemas.py          Order, LineItem, EscalationReason, order total check
  desk_agents.py      Desk, Order clerk, specialist base + clones, handoffs
  handoff_filters.py  trimmed escalation history + audit
  history.py          conversation trimming
  cost.py             run hooks, agent hooks, custom runner, cost line
  session.py          one customer turn; re-quote path; one trace per conversation
  cli.py              terminal UI and scripted demo
tests/                one file per requirement group, offline
```
