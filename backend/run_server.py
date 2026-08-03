"""PyInstaller entry point for the packaged backend.

The launcher (launcher.ps1 / start_app.ps1) starts backend.exe with
``--host 127.0.0.1 --port 8000``. This wrapper parses those args and runs
uvicorn with the FastAPI app object, mirroring ``run_backend.ps1`` which uses
``python -m uvicorn app.main:app --host ... --port ...``.
"""
from __future__ import annotations

import argparse

import uvicorn

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Long-Form Novel AI backend server")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8000, help="bind port")
    parser.add_argument("--reload", action="store_true", help="auto-reload (dev only)")
    args, _ = parser.parse_known_args()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()