"""SQLite ledger — every LLM call lands here, queryable for the dashboard.

Schema is portable to Postgres: use TIMESTAMP WITH TIME ZONE, NUMERIC(12,6)
for cost_usd, JSONB for the meta column. SQLite is fine for the prototype.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DB = os.environ.get(
    "AGENTCOST_DB",
    os.path.expanduser("~/.agentcost/ledger.db"),
)


@dataclass
class CallRecord:
    id: str
    ts: float
    task_id: str
    step: int
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: str  # ok | budget_exceeded | loop_capped | error
    note: str
    meta: Dict[str, Any]


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_conn(db_path: str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: str = DEFAULT_DB) -> None:
    """Create the calls table if missing. Idempotent."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id           TEXT PRIMARY KEY,
                ts           REAL NOT NULL,
                task_id      TEXT NOT NULL,
                step         INTEGER NOT NULL,
                model        TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd     REAL NOT NULL,
                latency_ms   INTEGER NOT NULL,
                status       TEXT NOT NULL,
                note         TEXT NOT NULL,
                meta         TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_task ON calls(task_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts)"
        )


def record(
    *,
    db_path: str = DEFAULT_DB,
    task_id: str,
    step: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    status: str,
    note: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Append one row to the ledger. Returns the new id."""
    init(db_path)  # idempotent; cheap
    rid = uuid.uuid4().hex[:12]
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO calls
                (id, ts, task_id, step, model, input_tokens, output_tokens,
                 cost_usd, latency_ms, status, note, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                time.time(),
                task_id,
                step,
                model,
                int(input_tokens),
                int(output_tokens),
                float(cost_usd),
                int(latency_ms),
                status,
                note,
                json.dumps(meta or {}),
            ),
        )
    return rid


def list_recent(limit: int = 20, db_path: str = DEFAULT_DB) -> List[CallRecord]:
    init(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM calls ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def task_summary(task_id: str, db_path: str = DEFAULT_DB) -> Dict[str, Any]:
    init(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM calls WHERE task_id = ? ORDER BY step ASC",
            (task_id,),
        ).fetchall()
    calls = [_row_to_record(r) for r in rows]
    total_cost = sum(c.cost_usd for c in calls)
    total_in = sum(c.input_tokens for c in calls)
    total_out = sum(c.output_tokens for c in calls)
    return {
        "task_id": task_id,
        "steps": len(calls),
        "total_cost_usd": total_cost,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "calls": calls,
    }


def totals(db_path: str = DEFAULT_DB) -> Dict[str, Any]:
    init(db_path)
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS calls,
                COALESCE(SUM(cost_usd), 0.0) AS cost,
                COALESCE(SUM(input_tokens), 0) AS in_tok,
                COALESCE(SUM(output_tokens), 0) AS out_tok
            FROM calls
            """
        ).fetchone()
        by_model = conn.execute(
            """
            SELECT model, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0.0) AS cost
            FROM calls GROUP BY model ORDER BY cost DESC
            """
        ).fetchall()
        by_status = conn.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM calls GROUP BY status ORDER BY n DESC
            """
        ).fetchall()
    return {
        "calls": int(row["calls"]),
        "total_cost_usd": float(row["cost"]),
        "total_input_tokens": int(row["in_tok"]),
        "total_output_tokens": int(row["out_tok"]),
        "by_model": [
            {"model": r["model"], "calls": int(r["n"]), "cost": float(r["cost"])}
            for r in by_model
        ],
        "by_status": [
            {"status": r["status"], "calls": int(r["n"])} for r in by_status
        ],
    }


def _row_to_record(r: sqlite3.Row) -> CallRecord:
    return CallRecord(
        id=r["id"],
        ts=r["ts"],
        task_id=r["task_id"],
        step=r["step"],
        model=r["model"],
        input_tokens=r["input_tokens"],
        output_tokens=r["output_tokens"],
        cost_usd=r["cost_usd"],
        latency_ms=r["latency_ms"],
        status=r["status"],
        note=r["note"],
        meta=json.loads(r["meta"] or "{}"),
    )
