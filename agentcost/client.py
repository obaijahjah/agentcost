"""Vertex Gemini call wrapper with budget + circuit breaker.

This is the smallest useful version of the "agent cost governor". A real
production version would:

  * cache pricing per model
  * support streaming cost telemetry
  * run in a shared Redis-backed ledger so multiple processes share the
    budget
  * support more providers behind a unified interface

The prototype uses Vertex Gemini because that is what is available on this
machine. The pricing layer is decoupled, so a different provider is one
`_call_provider` swap away.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from . import ledger, pricing


@dataclass
class Governor:
    """Per-task budget + step cap + cumulative cost tracker.

    Pass one instance per task. Each call to ``client.complete`` consumes
    one step and decrements the budget by the cost of the call. When the
    budget is exhausted or the step cap is hit, ``complete`` raises
    ``BudgetExceeded`` or ``LoopCapped`` instead of calling the model.
    """

    task_id: str
    budget_usd: float
    max_steps: int
    db_path: str = ledger.DEFAULT_DB
    spent_usd: float = 0.0
    step: int = 0
    done: bool = False
    _stop_reason: Optional[str] = None

    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    def checkpoint(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        status: str,
        note: str = "",
        meta: Optional[Dict[str, Any]] = None,
        latency_ms: int = 0,
    ) -> ledger.CallRecord:
        cost = pricing.cost_usd(model, input_tokens, output_tokens)
        self.spent_usd += cost
        self.step += 1
        if self.spent_usd >= self.budget_usd and not self.done:
            self._stop_reason = "budget_exceeded"
        rid = ledger.record(
            db_path=self.db_path,
            task_id=self.task_id,
            step=self.step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            status=status,
            note=note,
            meta=meta,
        )
        return ledger.CallRecord(
            id=rid,
            ts=time.time(),
            task_id=self.task_id,
            step=self.step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            status=status,
            note=note,
            meta=meta or {},
        )


class BudgetExceeded(RuntimeError):
    """Raised when a task exceeds its declared monetary budget."""


class LoopCapped(RuntimeError):
    """Raised when a task exceeds its declared max step count."""


# --------------------------- Provider calls ---------------------------


def _vertex_access_token() -> str:
    """Exchange the GCP service-account JSON for a short-lived access token.

    Falls back to ``gcloud auth print-access-token`` if the standard google
    auth lib is not available, which keeps the prototype dependency-light.
    """
    import json
    import subprocess

    creds_path = (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("VERTEX_CREDENTIALS_PATH")
    )
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError(
            "Set GOOGLE_APPLICATION_CREDENTIALS to your service-account JSON. "
            "See README for setup."
        )
    # Prefer the google-auth library if installed; fall back to gcloud.
    try:
        from google.oauth2 import service_account  # type: ignore
        from google.auth.transport.requests import Request as GARequest  # type: ignore

        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(GARequest())
        return creds.token  # type: ignore[return-value]
    except Exception:
        out = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        )
        return out.strip()


def _call_vertex_gemini(
    *,
    model: str,
    prompt: str,
    project: str,
    location: str,
    max_output_tokens: int = 512,
    timeout_s: float = 30.0,
) -> Dict[str, Any]:
    """One Vertex Gemini call. Returns dict with text, input_tokens,
    output_tokens."""
    token = _vertex_access_token()
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/publishers/google/"
        f"models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_output_tokens},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    started = time.time()
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    latency_ms = int((time.time() - started) * 1000)
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    usage = data.get("usageMetadata", {}) or {}
    return {
        "text": text,
        "input_tokens": int(usage.get("promptTokenCount", 0)),
        "output_tokens": int(usage.get("candidatesTokenCount", 0)),
        "latency_ms": latency_ms,
        "raw": data,
    }


def _call_synthetic(
    *,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
) -> Dict[str, Any]:
    """Deterministic, offline call used for tests and the failure demo so
    the prototype runs without any network or credentials.

    If the prompt itself starts with the literal token ``PASS`` or
    ``FAIL`` (after stripping), echo it back as the verdict text. This
    lets ``cost_of_pass`` tests exercise the parser without standing up
    a real judge model.
    """
    in_tok = max(1, len(prompt) // 4)
    out_tok = min(max_output_tokens, 64 + (len(prompt) % 32))
    stripped = prompt.strip()
    if stripped.upper().startswith("PASS") or stripped.upper().startswith("FAIL"):
        text = stripped[:200]
    else:
        text = f"[synthetic:{model}] echo: {prompt[:60]}"
    return {
        "text": text,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "latency_ms": 5,
    }


# --------------------------- Public API ---------------------------


def complete(
    gov: Governor,
    prompt: str,
    *,
    model: str,
    project: Optional[str] = None,
    location: Optional[str] = None,
    use_synthetic: bool = False,
) -> str:
    """Run one LLM call under the governor. Returns the model text.

    Raises ``BudgetExceeded`` if the next call would push the task over
    budget, or ``LoopCapped`` if max_steps is hit. The ledger row is
    recorded either way with the appropriate status.
    """
    if gov.done:
        raise BudgetExceeded(f"Governor {gov.task_id} is closed: {gov._stop_reason}")

    if gov.step >= gov.max_steps:
        gov.done = True
        gov._stop_reason = "loop_capped"
        gov.checkpoint(
            model=model,
            input_tokens=0,
            output_tokens=0,
            status="loop_capped",
            note="max_steps reached before call",
        )
        raise LoopCapped(
            f"Task {gov.task_id} hit max_steps={gov.max_steps} before next call"
        )

    # Cheap pre-flight: if even one minimum-cost call cannot fit, fail closed.
    min_cost = pricing.cost_usd(model, 1, 1)
    if gov.remaining() < min_cost:
        gov.done = True
        gov._stop_reason = "budget_exceeded"
        gov.checkpoint(
            model=model,
            input_tokens=0,
            output_tokens=0,
            status="budget_exceeded",
            note="pre-flight: budget cannot cover even a 1+1 call",
        )
        raise BudgetExceeded(
            f"Task {gov.task_id} budget ${gov.budget_usd:.6f} cannot cover "
            f"min call (${min_cost:.6f})"
        )

    started = time.time()
    try:
        if use_synthetic or model.startswith("synthetic-"):
            data = _call_synthetic(model=model, prompt=prompt)
        else:
            data = _call_vertex_gemini(
                model=model,
                prompt=prompt,
                project=project or os.environ.get("VERTEX_PROJECT_ID", ""),
                location=location or os.environ.get("VERTEX_LIVE_LOCATION", "us-central1"),
            )
    except Exception as exc:
        # Record the error so the dashboard can show it. Re-raise so the
        # caller can decide whether to retry, escalate, or stop.
        gov.checkpoint(
            model=model,
            input_tokens=0,
            output_tokens=0,
            status="error",
            note=f"provider error: {type(exc).__name__}: {exc}",
            meta={"exception": type(exc).__name__},
            latency_ms=int((time.time() - started) * 1000),
        )
        raise

    cost = pricing.cost_usd(model, data["input_tokens"], data["output_tokens"])

    # Post-flight budget check (a single call can be bigger than expected).
    would_overspend = (gov.spent_usd + cost) > gov.budget_usd
    status = "budget_exceeded" if would_overspend else "ok"
    if would_overspend:
        gov.done = True
        gov._stop_reason = "budget_exceeded"

    gov.checkpoint(
        model=model,
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        status=status,
        note="" if status == "ok" else "post-flight: call would overspend budget",
        meta={"text_preview": data["text"][:120]},
        latency_ms=data.get("latency_ms", int((time.time() - started) * 1000)),
    )

    if would_overspend:
        raise BudgetExceeded(
            f"Task {gov.task_id} hit budget ${gov.budget_usd:.6f} after "
            f"step {gov.step} (spent ${gov.spent_usd:.6f}, this call would "
            f"add ${cost:.6f})"
        )

    return data["text"]


def cost_of_pass(
    gov: Governor,
    judge_model: str,
    judge_prompt_builder: Callable[[str, str], str],
    *,
    project: Optional[str] = None,
    location: Optional[str] = None,
    use_synthetic: bool = False,
) -> Dict[str, Any]:
    """Compute cost-of-pass for the task so far.

    cost_of_pass = total_spent / success_rate.
    success_rate = pass_count / judge_count. A single judge call returns
    pass=1 or pass=0; the caller can call this multiple times to estimate
    success on a batch.

    For a real product, judge output should be a structured verdict, not a
    free-form response. The prototype parses a simple 'PASS' or 'FAIL'
    prefix.
    """
    if gov.spent_usd <= 0:
        return {"cost_of_pass_usd": 0.0, "verdict": "no_spend", "judge_cost_usd": 0.0}

    last_text = ""
    if gov.step > 0:
        # Cheap: read last text preview back from the ledger.
        recent = ledger.list_recent(limit=1, db_path=gov.db_path)
        if recent and recent[0].task_id == gov.task_id:
            last_text = (recent[0].meta or {}).get("text_preview", "")

    pre_spent = gov.spent_usd
    judge_prompt = judge_prompt_builder(gov.task_id, last_text)
    try:
        verdict_text = complete(
            gov,
            judge_prompt,
            model=judge_model,
            project=project,
            location=location,
            use_synthetic=use_synthetic,
        )
    except (BudgetExceeded, LoopCapped) as exc:
        return {
            "cost_of_pass_usd": float("inf"),
            "verdict": "judge_blocked",
            "judge_cost_usd": 0.0,
            "reason": str(exc),
        }

    judge_cost = gov.spent_usd - pre_spent
    verdict_text_stripped = verdict_text.strip().upper()
    first_line = verdict_text_stripped.splitlines()[0] if verdict_text_stripped else ""
    if "VERDICT:PASS" in first_line:
        verdict = "pass"
        cost = pre_spent
    elif "VERDICT:FAIL" in first_line:
        verdict = "fail"
        cost = float("inf")
    else:
        # Fall back to the older PASS/FAIL first-word heuristic so
        # tests that pass "PASS" or "FAIL" directly still work.
        first_word = first_line.split(maxsplit=1)[0] if first_line else ""
        if first_word.startswith("PASS"):
            verdict = "pass"
            cost = pre_spent
        elif first_word.startswith("FAIL"):
            verdict = "fail"
            cost = float("inf")
        else:
            # Ambiguous response — treat as a fail (infinite cost-of-pass)
            # so the operator notices and adjusts the judge.
            verdict = "ambiguous"
            cost = float("inf")
    return {
        "cost_of_pass_usd": cost,
        "verdict": verdict,
        "judge_cost_usd": judge_cost,
        "verdict_text": verdict_text,
    }
