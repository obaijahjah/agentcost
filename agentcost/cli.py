"""CLI: ``agentcost run`` and ``agentcost report``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import ledger
from .client import (
    BudgetExceeded,
    Governor,
    LoopCapped,
    complete,
    cost_of_pass,
)


def _run_one(args: argparse.Namespace) -> int:
    gov = Governor(
        task_id=args.task_id,
        budget_usd=args.budget,
        max_steps=args.max_steps,
    )
    try:
        text = complete(
            gov,
            args.prompt,
            model=args.model,
            use_synthetic=args.synthetic,
        )
    except BudgetExceeded as exc:
        print(json.dumps({"status": "budget_exceeded", "reason": str(exc),
                          "spent_usd": gov.spent_usd, "steps": gov.step},
                         indent=2), file=sys.stderr)
        return 2
    except LoopCapped as exc:
        print(json.dumps({"status": "loop_capped", "reason": str(exc),
                          "spent_usd": gov.spent_usd, "steps": gov.step},
                         indent=2), file=sys.stderr)
        return 3

    out: dict = {
        "status": "ok",
        "task_id": args.task_id,
        "steps": gov.step,
        "spent_usd": gov.spent_usd,
        "model": args.model,
        "text": text,
    }

    if args.cost_of_pass:
        def judge_prompt(tid: str, last: str) -> str:
            # Ask the judge to emit a structured verdict on the first
            # line. The parser in cost_of_pass() looks for VERDICT:PASS
            # or VERDICT:FAIL after stripping. A real LLM (or a synthetic
            # provider whose prompt starts with that token) returns it.
            return (
                f"You are a strict reviewer. On the very first line of "
                f"your reply, write exactly one of:\n"
                f"  VERDICT:PASS\n"
                f"  VERDICT:FAIL\n"
                f"followed by one sentence of justification. Nothing else "
                f"on the first line.\n\n"
                f"Task: {tid}\n\n"
                f"Output to judge:\n{last}"
            )

        result = cost_of_pass(
            gov,
            args.cost_of_pass,
            judge_prompt,
            use_synthetic=args.synthetic,
        )
        out["cost_of_pass"] = result

    print(json.dumps(out, indent=2))
    return 0


def _report(args: argparse.Namespace) -> int:
    if args.task_id:
        s = ledger.task_summary(args.task_id)
        print(json.dumps(s, indent=2, default=str))
    else:
        totals = ledger.totals()
        recent = ledger.list_recent(limit=args.last)
        print(json.dumps(
            {"totals": totals, "recent": [vars(r) for r in recent]},
            indent=2,
            default=str,
        ))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcost")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run one LLM call under a budget")
    run.add_argument("--prompt", required=True)
    run.add_argument("--model", default="synthetic-cheap")
    run.add_argument("--budget", type=float, default=0.05)
    run.add_argument("--max-steps", type=int, default=8)
    run.add_argument("--task-id", default="cli-run")
    run.add_argument(
        "--synthetic", action="store_true",
        help="Use the offline synthetic provider (no credentials needed)",
    )
    run.add_argument(
        "--cost-of-pass", default=None,
        help="If set, also run a judge call to compute cost-of-pass using this model",
    )
    run.set_defaults(fn=_run_one)

    rep = sub.add_parser("report", help="Show ledger summary")
    rep.add_argument("--task-id", default=None)
    rep.add_argument("--last", type=int, default=20)
    rep.set_defaults(fn=_report)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
