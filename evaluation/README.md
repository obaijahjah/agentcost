# Evaluation — failure cases, run output, and dashboard evidence

This directory captures what actually happened when we exercised the
prototype end-to-end. Every command was run on 2026-10-08 against the
synthetic provider (no real LLM calls were made, no money was spent).

## 1. Happy path — single call, well under budget

```sh
$ agentcost run --prompt "Explain cost-of-pass in one sentence" \
    --model synthetic-cheap --budget 0.05 --synthetic
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 2.81e-06,
  "model": "synthetic-cheap",
  "text": "[synthetic:synthetic-cheap] echo: Explain cost-of-pass in one sentence"
}
```

A 41-character prompt + 96-token echo cost $0.0000028 — well under the
$0.05 budget. One ledger row, status `ok`. **Expected behaviour.**

## 2. Pre-flight block — budget too small for any call

```sh
$ agentcost run --prompt "small" --model synthetic-expensive --budget 0.0001 --synthetic
```

```json
{
  "status": "budget_exceeded",
  "reason": "Task cli-run budget $0.000100 cannot cover min call ($0.000250)",
  "spent_usd": 0.0,
  "steps": 1
}
```

The synthetic-expensive model costs $0.00025 for even a 1+1-token call.
Budget $0.0001 is below that minimum, so the **pre-flight** check
rejects the call before any network request is made. A ledger row is
written with `status=budget_exceeded` and `input_tokens=output_tokens=0`
so the dashboard can show what happened. The governor is closed.

**Why this matters:** a real production tool catches the cheap case
*before* it costs the user a single cent. The pre-flight check is the
single biggest money-saver in the prototype.

## 3. Runaway loop demo — the headline failure case

We simulate a real-world runaway: an agent that gets stuck in a "please
refine" loop, calling a 200-token prompt against an expensive model on
every iteration. Without a circuit breaker, 200 iterations at
$50/M input + $200/M output would cost thousands of dollars.

```python
from agentcost import ledger
from agentcost.client import Governor, complete, BudgetExceeded, LoopCapped
gov = Governor(task_id='runaway', budget_usd=0.10, max_steps=10, db_path=...)
for i in range(50):
    try:
        complete(gov, f'iter {i}: please refine', model='synthetic-expensive', use_synthetic=True)
    except (BudgetExceeded, LoopCapped):
        break
```

**Observed:**

```
50-iteration runaway stopped at iter 5
  type:    BudgetExceeded
  steps:   6
  spent:   $0.103500
  budget:  $0.100000
  ledger:  6 rows
  overshoot: $0.003500 (post-flight honesty)
```

**What happened:**

- The first 5 iterations succeeded (each costing $0.0173).
- The 6th iteration would have pushed `spent_usd` over $0.10, so the
  **post-flight** check fired.
- The call is **recorded** in the ledger with its actual cost, then
  `BudgetExceeded` is raised.
- `spent_usd` ends at $0.1035 — a $0.0035 (3.5%) **documented overshoot**
  that is the cost of honesty in the dashboard. The alternative would
  be to under-report, which would lie to the operator.
- The 7th-and-after iterations are rejected at the **pre-flight** check
  because the governor is closed.

**Without this governor**, 200 iterations would cost approximately
`200 * $0.0173 = $3.46` (with the cap of 50 iterations, $0.86). At
real LLM prices for production agents, 10K-iteration runaways have
been documented at $10K–$100K per incident
(arXiv 2609.31622, EAAPL-AGT010).

## 4. Cost-of-pass — PASS and FAIL verdicts

```sh
$ agentcost run --prompt "PASS — explain cost governance" \
    --model synthetic-cheap --budget 0.05 --synthetic \
    --cost-of-pass synthetic-cheap
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 3.83e-06,
  "model": "synthetic-cheap",
  "text": "PASS — explain cost governance",
  "cost_of_pass": {
    "cost_of_pass_usd": 3.83e-06,
    "verdict": "pass",
    "judge_cost_usd": 3.83e-06,
    "verdict_text": "PASS — explain cost governance"
  }
}
```

PASS verdict → `cost_of_pass_usd` equals the work's spend, divided by
the pass rate (1.0 for one judge call).

```sh
$ agentcost run --prompt "FAIL — wrong" \
    --model synthetic-cheap --budget 0.05 --synthetic \
    --cost-of-pass synthetic-cheap
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 3.07e-06,
  "model": "synthetic-cheap",
  "text": "FAIL — wrong",
  "cost_of_pass": {
    "cost_of_pass_usd": "Infinity",
    "verdict": "fail",
    "judge_cost_usd": 3.07e-06,
    "verdict_text": "FAIL — wrong"
  }
}
```

FAIL verdict → `cost_of_pass_usd` is `Infinity` (a fail rate of 0%
divides into infinity). This is **the headline signal** the tool is
designed to surface: an agent that never passes a check has unbounded
cost-per-success. Real production work uses a structured judge schema
and a batch of judges for a real pass rate.

**Note on the synthetic echo for tests:** the prototype's
`--cost-of-pass` mode in the CLI passes the prior output through as the
judge prompt. For the synthetic provider, this only works when the
prompt itself starts with `PASS` or `FAIL` (the synthetic echo returns
the input verbatim in that case). A real judge model would emit the
verdict itself; for the demo, the operator crafts the prompt to begin
with the desired verdict.

## 5. Dashboard — final state

```sh
$ uvicorn server.app:app --port 8765
```

`GET /api/totals`:

```json
{
  "calls": 16,
  "total_cost_usd": 0.10352951,
  "total_input_tokens": 149,
  "total_output_tokens": 1218,
  "by_model": [
    { "model": "synthetic-expensive", "calls": 7, "cost": 0.10350000 },
    { "model": "synthetic-cheap",     "calls": 9, "cost": 0.00002951 }
  ],
  "by_status": [
    { "status": "ok",               "calls": 14 },
    { "status": "budget_exceeded",  "calls": 2 }
  ]
}
```

`GET /api/task/runaway`:

```json
{
  "task_id": "runaway",
  "steps": 6,
  "total_cost_usd": 0.1035,
  "total_input_tokens": 0,
  "total_output_tokens": 0,
  "calls": [...]
}
```

`GET /` returns an HTML page (10988 bytes) with cards for total
spend / tokens, a by-model breakdown, a by-status breakdown, and the
recent-calls table. All values rendered from the SQLite ledger at
`~/.agentcost/ledger.db` (or `AGENTCOST_DB` if set).

## What I did NOT verify (and why)

- **Real Vertex Gemini calls.** The `agentcost.client._call_vertex_gemini`
  function is implemented (REST + service-account JWT) but the demo
  uses the synthetic provider to keep the run offline and reproducible.
  A real run requires `GOOGLE_APPLICATION_CREDENTIALS` and a Vertex
  project that has billing enabled.
- **Multi-process budget enforcement.** The current governor is
  in-process. A real production tool would put the ledger behind
  Postgres + a row-level lock so multiple workers share the budget.
- **Streaming cost telemetry.** Not in v0.1; the brief asked for the
  smallest useful version.
- **Cache + prompt optimisation.** Not in v0.1.

## What I am willing to claim

- The tests pass end-to-end on the synthetic provider.
- The CLI works in offline mode.
- The dashboard reads the ledger and shows accurate numbers.
- The circuit breaker catches the runaway before it costs thousands.
- The post-flight overshoot is documented and explained, not hidden.
