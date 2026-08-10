"""Entry point used by the packaged ATCConfMaker desktop engine."""

from __future__ import annotations

import os

import uvicorn

from app.main import app


def main() -> None:
    host = os.environ.get("KONFMAKER_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("KONFMAKER_BACKEND_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
