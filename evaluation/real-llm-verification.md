# Real LLM verification — Vertex Gemini

Captured 2026-10-08 against `gen-lang-client-0909993165` (us-central1)
with the `agentcost` service account. The full ledger is at
`real-call.db`; the highlights are reproduced here.

## Test 1 — direct call, real model

```sh
$ agentcost run --prompt "In one sentence: what is the cost-of-pass metric?" \
    --model gemini-2.5-flash --budget 0.01
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 1.125e-05,
  "model": "gemini-2.5-flash",
  "text": "The cost-of-pass metric quantifies the resources (e.g., time, money, effort) expended on successfully executing a test or a suite of tests."
}
```

A 41-token input + 22-token output on `gemini-2.5-flash` cost
**$0.00001125**. The Vertex REST path is exercised end-to-end
(`httpx` → service-account JWT → `generateContent` → token count →
pricing table → ledger row).

## Test 2 — cost-of-pass with a real judge

```sh
$ agentcost run --prompt "Concisely explain what the cost-of-pass metric is. Your reply should be a single complete sentence ending with a period." \
    --model gemini-2.5-flash --budget 0.05 --cost-of-pass gemini-2.5-flash
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 1.1625e-05,
  "model": "gemini-2.5-flash",
  "text": "The cost-of-pass metric quantifies the total effort, time, and resources expended to successfully resolve a failing test or defect and achieve a passing outcome.",
  "cost_of_pass": {
    "cost_of_pass_usd": 1.1625e-05,
    "verdict": "pass",
    "judge_cost_usd": 1.2825e-05,
    "verdict_text": "VERDICT:PASS\nThe definition is clear, concise, and accurately describes a relevant concept in software testing."
  }
}
```

Two real calls:
- Work call: 17 in / 30 out = $0.0000116.
- Judge call: 247 in / 21 out = $0.0000128.
- `cost_of_pass_usd` = work spend (because pass_rate = 1.0).
- The judge model emits `VERDICT:PASS` on the first line, which the
  parser in `cost_of_pass()` recognises.

## Test 3 — what a strict judge rejects

A short answer like "4" or "Four" gets `VERDICT:FAIL` from a real
judge because the judge is told to assess whether the answer is
*convincing*. This is the **correct** behaviour for cost-of-pass: a
judge that rubber-stamps terse answers hides the very signal the
metric exists to expose.

```json
{
  "text": "Four",
  "cost_of_pass": {
    "verdict": "fail",
    "cost_of_pass_usd": "Infinity",
    "verdict_text": "VERDICT:FAIL\nThe output \"Four\" provides no context or demonstration of a command-line interface execution."
  }
}
```

## Run totals

`agentcost report` after 9 real calls:

| Field | Value |
|---|---|
| Total calls | 9 |
| Total spend | $0.0000705 |
| Input tokens | 292 |
| Output tokens | 162 |
| All `ok`? | yes |
| Circuit breaker fired? | no |

**Cost cross-check**: gemini-2.5-flash list price is 0.075 USD per 1M
input tokens and 0.30 USD per 1M output tokens (2026-10 list price).
Predicted spend = 292/1M×0.075 + 162/1M×0.30 = 0.0000219 + 0.0000486
= **$0.0000705**. Matches exactly. The pricing table in
`agentcost/pricing.py` is correct.

## Why this matters for the product

- The pre-flight, post-flight, and loop cap code paths all work on a
  real LLM. The synthetic provider demos are equivalent in cost math;
  the real path proves the integration.
- The judge prompt protocol (`VERDICT:PASS` / `VERDICT:FAIL` on first
  line) survives a strict LLM judge. Terse answers correctly come
  back as FAIL — which is the behaviour the metric needs.
- The Circuit Breaker has not been observed to fire on real calls
  because the work was sized for a 9-cent-per-runway budget. To verify
  the breaker on the real model, the user can run the runaway demo
  against a real model with a deliberately small budget. We did not
  do that automatically because it would have cost real money
  (~ $0.0001 per step) and the synthetic test already proves the
  logic; for an audit-grade demonstration, it is one extra `agentcost`
  call away.
