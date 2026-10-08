"""Tests for the ledger."""

import os
import tempfile

from agentcost import ledger


def test_init_idempotent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        ledger.init(path)
        ledger.init(path)  # second call must not raise
        with ledger.get_conn(path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='calls'"
            ).fetchone()[0]
        assert n == 1


def test_record_and_list():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        rid = ledger.record(
            db_path=path,
            task_id="t1",
            step=1,
            model="synthetic-cheap",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.0001,
            latency_ms=12,
            status="ok",
            note="",
            meta={"k": "v"},
        )
        assert len(rid) == 12
        recent = ledger.list_recent(db_path=path, limit=10)
        assert len(recent) == 1
        c = recent[0]
        assert c.task_id == "t1"
        assert c.input_tokens == 10
        assert c.output_tokens == 20
        assert c.cost_usd == 0.0001
        assert c.status == "ok"
        assert c.meta == {"k": "v"}


def test_task_summary_aggregates():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        for i in range(3):
            ledger.record(
                db_path=path,
                task_id="tA",
                step=i + 1,
                model="synthetic-cheap",
                input_tokens=10 * (i + 1),
                output_tokens=5,
                cost_usd=0.0001 * (i + 1),
                latency_ms=10,
                status="ok",
            )
        s = ledger.task_summary("tA", db_path=path)
        assert s["steps"] == 3
        assert s["total_input_tokens"] == 60  # 10 + 20 + 30
        assert abs(s["total_cost_usd"] - 0.0006) < 1e-9


def test_totals_breakdown():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        for model, n in [("synthetic-cheap", 2), ("synthetic-expensive", 1)]:
            for i in range(n):
                ledger.record(
                    db_path=path,
                    task_id="tx",
                    step=i + 1,
                    model=model,
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.01,
                    latency_ms=5,
                    status="ok",
                )
        t = ledger.totals(db_path=path)
        assert t["calls"] == 3
        assert abs(t["total_cost_usd"] - 0.03) < 1e-9
        by_model = {r["model"]: r["calls"] for r in t["by_model"]}
        assert by_model == {"synthetic-cheap": 2, "synthetic-expensive": 1}
