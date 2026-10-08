"""Tests for cost-of-pass. Synthetic model with deterministic judge."""

import os
import tempfile

from agentcost.client import Governor, complete, cost_of_pass


def test_cost_of_pass_no_spend():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = Governor(task_id="t", budget_usd=1.0, max_steps=3, db_path=path)
        result = cost_of_pass(gov, "synthetic-cheap",
                              lambda tid, last: "PASS", use_synthetic=True)
        assert result["verdict"] == "no_spend"
        assert result["cost_of_pass_usd"] == 0.0


def test_cost_of_pass_pass():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = Governor(task_id="t2", budget_usd=1.0, max_steps=3, db_path=path)
        # One real call (synthetic-expensive so the cost is meaningful)
        complete(gov, "answer the question", model="synthetic-expensive",
                 use_synthetic=True)
        pre_spent = gov.spent_usd
        # Judge prompt is a short token so the synthetic echo's first
        # word is "VERDICT:PASS" — verifying the parser logic end-to-end.
        result = cost_of_pass(
            gov, "synthetic-cheap",
            lambda tid, last: "VERDICT:PASS — looks good",
            use_synthetic=True,
        )
        assert result["verdict"] == "pass"
        # On PASS, cost-of-pass = pre_spent (the work, not the judge).
        assert result["cost_of_pass_usd"] == pre_spent
        # The judge call cost was added on top of the original spend.
        assert gov.spent_usd > pre_spent


def test_cost_of_pass_fail_yields_infinite():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = Governor(task_id="t3", budget_usd=1.0, max_steps=3, db_path=path)
        complete(gov, "x", model="synthetic-expensive", use_synthetic=True)
        result = cost_of_pass(
            gov, "synthetic-cheap",
            lambda tid, last: "VERDICT:FAIL — wrong",
            use_synthetic=True,
        )
        assert result["verdict"] == "fail"
        # fail rate = 0/1 → cost-of-pass is infinite
        assert result["cost_of_pass_usd"] == float("inf")


def test_cost_of_pass_ambiguous_yields_infinite():
    """If the judge model doesn't start with PASS/FAIL, the verdict is
    'ambiguous' and cost-of-pass is infinite — the operator should
    notice and fix the judge."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        gov = Governor(task_id="t4", budget_usd=1.0, max_steps=3, db_path=path)
        complete(gov, "x", model="synthetic-expensive", use_synthetic=True)
        result = cost_of_pass(
            gov, "synthetic-cheap",
            lambda tid, last: "Hmm, not sure about this one",
            use_synthetic=True,
        )
        assert result["verdict"] == "ambiguous"
        assert result["cost_of_pass_usd"] == float("inf")
