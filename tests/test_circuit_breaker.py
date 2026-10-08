"""Tests for the circuit-breaker / budget logic. Uses synthetic models so
no network is needed."""

import os
import tempfile

import pytest

from agentcost import ledger
from agentcost.client import (
    BudgetExceeded,
    Governor,
    LoopCapped,
    complete,
)
from agentcost import pricing


def _fresh_gov(db_path: str, **kw) -> Governor:
    return Governor(
        task_id=kw.pop("task_id", "t"),
        budget_usd=kw.pop("budget_usd", 0.001),
        max_steps=kw.pop("max_steps", 4),
        db_path=db_path,
    )


def test_happy_path_one_call():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = _fresh_gov(path, budget_usd=0.01)
        text = complete(gov, "hello", model="synthetic-cheap", use_synthetic=True)
        assert "synthetic-cheap" in text
        assert gov.step == 1
        assert gov.spent_usd > 0
        s = ledger.task_summary("t", db_path=path)
        assert s["steps"] == 1
        assert s["calls"][0].status == "ok"


def test_budget_exceeded_preflight():
    """A 1+1 synthetic-cheap call costs ~$0.0000004; a budget of $0 must
    fail in the pre-flight before the call is even attempted."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = _fresh_gov(path, budget_usd=0.0)
        with pytest.raises(BudgetExceeded):
            complete(gov, "x", model="synthetic-cheap", use_synthetic=True)
        # the failed pre-flight is still recorded
        s = ledger.task_summary("t", db_path=path)
        assert s["calls"][0].status == "budget_exceeded"
        assert gov.done is True


def test_budget_exceeded_postflight():
    """Force the post-flight path: a single expensive-model call that
    fits the pre-flight cap but the actual cost pushes us over.

    Synthetic-expensive costs $50/M input + $200/M output. The pre-flight
    minimum is 1+1 token = $0.00025. A budget of $0.0005 lets the
    pre-flight pass; the actual 50+72-token call costs $0.0169 and the
    post-flight check fires.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = _fresh_gov(path, budget_usd=0.0005, max_steps=5)
        with pytest.raises(BudgetExceeded):
            complete(
                gov,
                "x" * 200,
                model="synthetic-expensive",
                use_synthetic=True,
            )
        s = ledger.task_summary("t", db_path=path)
        # The first call is the post-flight rejection; the actual cost
        # is recorded so the ledger tells the truth.
        statuses = [c.status for c in s["calls"]]
        assert statuses[0] == "budget_exceeded"
        assert gov.done is True
        # The post-flight rejection records the actual cost; spent_usd
        # exceeds budget_usd. This is the documented overshoot — by
        # design, for dashboard honesty.
        assert gov.spent_usd > 0.0005
        # And no further calls are allowed.
        with pytest.raises(BudgetExceeded):
            complete(gov, "small", model="synthetic-cheap", use_synthetic=True)


def test_loop_capped():
    """max_steps=2 — the third call must raise LoopCapped."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = _fresh_gov(path, budget_usd=10.0, max_steps=2)
        complete(gov, "a", model="synthetic-cheap", use_synthetic=True)
        complete(gov, "b", model="synthetic-cheap", use_synthetic=True)
        with pytest.raises(LoopCapped):
            complete(gov, "c", model="synthetic-cheap", use_synthetic=True)
        s = ledger.task_summary("t", db_path=path)
        # 2 ok + 1 loop_capped
        statuses = [c.status for c in s["calls"]]
        assert statuses == ["ok", "ok", "loop_capped"]


def test_runaway_loop_demo():
    """A 50-iteration runaway with an expensive model must stop well
    before the budget is gone. This is the headline failure case.

    Honest behaviour: the post-flight check records the actual call cost
    in the ledger before raising. The LAST recorded call may therefore
    push ``spent_usd`` slightly over ``budget_usd`` — that is by design,
    so dashboards show the true spend. The test asserts:
      * the loop was closed (no more than ``max_steps + 1`` rows)
      * no further call is allowed after the breaker fires
      * the ledger shows the truth
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        # Realistic runaway: 200 iterations at $50/M input + $200/M output
        # would cost thousands of dollars. With max_steps=20 and a $0.10
        # budget, the loop MUST stop.
        gov = _fresh_gov(
            path, task_id="runaway", budget_usd=0.10, max_steps=20
        )
        last_error = None
        for i in range(50):
            try:
                complete(
                    gov,
                    f"iter {i}: please refine your answer",
                    model="synthetic-expensive",
                    use_synthetic=True,
                )
            except (BudgetExceeded, LoopCapped) as exc:
                last_error = exc
                break
        assert last_error is not None
        # Loop must have closed; the breaker fired.
        assert gov.done is True
        # No further calls allowed.
        with pytest.raises((BudgetExceeded, LoopCapped)):
            complete(
                gov,
                "iter 100: try again",
                model="synthetic-expensive",
                use_synthetic=True,
            )
        s = ledger.task_summary("runaway", db_path=path)
        # We bounded the runaway at or below max_steps + 1 (the +1 covers
        # the final post-flight rejection row).
        assert gov.step <= 21
        # Spend is bounded by max_steps * max_per_call cost
        # (rough upper bound: 21 * $0.0252 = $0.53 worst case for
        # synthetic-expensive with large prompts).
        assert gov.spent_usd < 1.0  # well below "thousands" of dollars
        # The ledger includes at least one budget_exceeded or loop_capped row
        assert any(
            c.status in ("budget_exceeded", "loop_capped") for c in s["calls"]
        )
