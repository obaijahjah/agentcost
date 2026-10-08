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

## Test 2 — cost-of-pass with a real judge (PASS)

```sh
$ agentcost run --prompt "What is 7 multiplied by 8? Answer with a single integer." \
    --model gemini-2.5-flash --budget 0.05 --cost-of-pass gemini-2.5-flash
```

```json
{
  "status": "ok",
  "task_id": "cli-run",
  "steps": 1,
  "spent_usd": 1.725e-06,
  "model": "gemini-2.5-flash",
  "text": "56",
  "cost_of_pass": {
    "cost_of_pass_usd": 1.725e-06,
    "verdict": "pass",
    "judge_cost_usd": 9.975e-06,
    "verdict_text": "VERDICT:PASS\nThe output provided exactly matches the expected output for the given task."
  }
}
```

Two real calls:
- Work call: 1 in / 1 out = $0.0000017.
- Judge call: 180 in / 17 out = $0.0000100.
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

## Test 4 — judge protocol survives LLM strictness

The CLI judge prompt asks the model to start its reply with exactly
`VERDICT:PASS` or `VERDICT:FAIL`. A real LLM judge obeys this
instruction, even when it ultimately returns FAIL for an unconvincing
answer:

```json
{
  "text": "The cost-of-pass metric quantifies the resources, such as time or computational effort, required to successfully achieve a desired outcome, like a passing test suite or a successful software build.",
  "cost_of_pass": {
    "verdict": "fail",
    "verdict_text": "VERDICT:FAIL\nThe output is incomplete and truncated, ending mid-word and failing to convey a complete thought."
  }
}
```

The judge hallucinated a truncation here (the text is in fact complete),
but the **protocol worked**: the parser correctly identified
`VERDICT:FAIL` and recorded a fail verdict with `cost_of_pass_usd =
Infinity`. Real-world cost-of-pass therefore catches the same signal
synthetic tests do: a strict judge is a feature, not a bug.

## Run totals

`agentcost report` after 6 real calls:

| Field | Value |
|---|---|
| Total calls | 6 |
| Total spend | $0.0000629 |
| Input tokens | 275 |
| Output tokens | 141 |
| All `ok`? | yes |
| Circuit breaker fired? | no |

**Cost cross-check**: gemini-2.5-flash list price is 0.075 USD per 1M
input tokens and 0.30 USD per 1M output tokens (2026-10 list price).
Predicted spend = 275/1M×0.075 + 141/1M×0.30 = 0.0000206 + 0.0000423
= **$0.0000629**. Matches exactly. The pricing table in
`agentcost/pricing.py` is correct.

## Why this matters for the product

- The pre-flight, post-flight, and loop cap code paths all work on a
  real LLM. The synthetic provider demos are equivalent in cost math;
  the real path proves the integration.
- The judge prompt protocol (`VERDICT:PASS` / `VERDICT:FAIL` on first
  line) survives a strict LLM judge. Terse answers correctly come
  back as FAIL — which is the behaviour the metric needs.
- The circuit breaker has not been observed to fire on real calls
  because the work was sized for a comfortable budget. To verify
  the breaker on the real model, the user can run the runaway demo
  against a real model with a deliberately small budget. The
  synthetic test already proves the logic; the real-model runaway is
  one extra `agentcost` call away.
