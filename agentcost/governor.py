"""@govern decorator — wraps a multi-step agent function in a budget.

Usage:

    from agentcost import Governor, govern
    from agentcost.client import complete

    @govern(budget_usd=0.20, max_steps=10, model="synthetic-cheap")
    def research(question: str, g: Governor) -> str:
        out = complete(g, f"Outline: {question}", model="synthetic-cheap")
        # ... arbitrary steps ...
        return out
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional, TypeVar

from .client import (
    BudgetExceeded,
    Governor,
    LoopCapped,
    complete as _complete,
)

F = TypeVar("F", bound=Callable[..., str])


def govern(
    *,
    budget_usd: float,
    max_steps: int,
    model: str,
    task_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Callable[[F], F]:
    """Wrap a function so each call gets a fresh Governor with the given
    budget and step cap. The wrapped function must take a Governor as the
    last argument and return the final string answer."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            tid = task_id or fn.__name__
            gov = Governor(
                task_id=tid,
                budget_usd=budget_usd,
                max_steps=max_steps,
                **({"db_path": db_path} if db_path else {}),
            )
            return fn(*args, gov=gov, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["govern", "Governor", "BudgetExceeded", "LoopCapped", "_complete"]
