"""FastAPI dashboard — single page that shows recent calls + totals."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

# Allow running as a script: `python server/app.py` or `uvicorn server.app:app`
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROTO = os.path.dirname(_HERE)
if _PROTO not in sys.path:
    sys.path.insert(0, _PROTO)

from agentcost import ledger  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

app = FastAPI(title="agentcost dashboard", version="0.1.0")

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATE_DIR)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    # Templates iterate over fields, so materialise dataclasses to dicts.
    recent = [vars(c) for c in ledger.list_recent(limit=20)]
    return templates.TemplateResponse(
        request,  # new starlette API: request first
        "index.html",
        {
            "totals": ledger.totals(),
            "recent": recent,
        },
    )


@app.get("/api/totals", response_class=JSONResponse)
def api_totals() -> Dict[str, Any]:
    return ledger.totals()


@app.get("/api/recent", response_class=JSONResponse)
def api_recent(limit: int = 50) -> Dict[str, Any]:
    return {"recent": [vars(c) for c in ledger.list_recent(limit=limit)]}


@app.get("/api/task/{task_id}", response_class=JSONResponse)
def api_task(task_id: str) -> Dict[str, Any]:
    return ledger.task_summary(task_id)
