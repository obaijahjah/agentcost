"""Entry point so ``hermes verify`` (and ``uvicorn main:app``) can boot
the dashboard without knowing the package layout."""

import os
import sys

# Allow ``python main.py`` from this directory without a package install.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from server.app import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
