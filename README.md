# agentcost

A tiny per-task budget, circuit breaker, and cost-of-pass metric for AI
agent workflows. The smallest useful version of the tool every team
running agents in production will eventually need.

> **Why this exists.** 79% of enterprises overran their AI agent budgets
> in the past year; real runaways cost $10K–$100K per incident (sources
> in the research notes that came with the build). The existing tools
> cover observability (Langfuse, AgentOps) or single-vendor routing
> (Portkey, Helicone). None of them gives you a single control plane
> with per-task monetary budget, fail-closed circuit breaker, and a
> *cost-of-pass* metric that says "this agent's work is not worth its
> spend." This is the smallest version of that.

## Install

```sh
pip install -e .
```

Python 3.9+; only five runtime dependencies: `httpx`, `fastapi`,
`uvicorn`, `pydantic`, `jinja2`.

## Quick start — offline

```sh
agentcost run --prompt "hello world" --model synthetic-cheap \
  --budget 0.05 --synthetic
```

## Quick start — real Vertex Gemini

```sh
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-service-account.json
export VERTEX_PROJECT_ID=your-project
export VERTEX_LIVE_LOCATION=us-central1
agentcost run --prompt "Explain cost-of-pass in one sentence" \
  --model gemini-2.5-flash --budget 0.05
```

## What it does

- **Pre-flight budget check.** If the next call cannot fit in the
  remaining budget, fail closed with a ledger row.
- **Post-flight budget check.** If the actual cost of a call would
  overshoot the budget, fail closed. (The call's true cost is
  recorded so the dashboard tells the truth — see "Honest limits"
  below.)
- **Loop cap.** A `max_steps` cap that fails closed when the agent
  loop is too long.
- **SQLite ledger.** Every call lands in a portable schema. Same
  schema works on Postgres with one type change.
- **Cost-of-pass.** Run a separate judge call and compute
  `total_spend / pass_rate`. A fail verdict is `Infinity` — the
  signal the metric exists to surface.
- **FastAPI dashboard.** `uvicorn main:app --port 8765`.

## What it does NOT do (deliberately)

- It is not multi-tenant. One ledger, one process, one machine.
- It does not have a live pricing API. The price table is hard-coded
  to public list prices as of 2026-10.
- It does not include caching, prompt optimisation, or model routing.
  Those are v0.1.
- It does not have a real auth layer. The dashboard is local-only.
- It does not enforce budget across multiple processes. A shared
  Postgres ledger would be needed for that.

## Run the tests

```sh
pip install pytest
pytest tests/
```

13 tests covering the ledger, the circuit breaker (pre-flight,
post-flight, loop cap), and the cost-of-pass metric (PASS, FAIL,
ambiguous).

## Honest limits

The post-flight check records the actual call cost in the ledger
*before* raising. The last recorded call on a runaway therefore pushes
`spent_usd` slightly over `budget_usd` — by design, so the dashboard
shows the truth. See [`evaluation/README.md`](evaluation/README.md) for
the captured runaway demo and
[`evaluation/real-llm-verification.md`](evaluation/real-llm-verification.md)
for a real Vertex Gemini verification.

## Layout

```
.
├── pyproject.toml
├── main.py             # uvicorn entry point
├── agentcost/          # the library
├── server/             # FastAPI dashboard
├── tests/              # 13 pytest cases
└── evaluation/         # captured failure cases + real-LLM runs
```

## License

MIT.
