"""One-command, cockpit-first launcher for the complete local gateway system."""

from __future__ import annotations

import os
import sqlite3
import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from .console_api import create_console_app
from .replay_execution import LocalReplayExecutor
from .service_manager import ServiceManager

HOST = "127.0.0.1"
PORT = 8013
COCKPIT_URL = f"http://{HOST}:{PORT}/cockpit"


def startup_plan(project_root: Path) -> dict[str, Any]:
    """Describe the reproducible, cockpit-first local startup plan."""
    return {
        "project_root": str(project_root.resolve()),
        "landing_url": COCKPIT_URL,
        "console_url": f"http://{HOST}:{PORT}/console",
        "auto_start_services": True,
        "command": [
            "uv",
            "run",
            "uvicorn",
            "llm_budget_gateway.system_launcher:create_system_app",
            "--factory",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
    }


def create_system_app(
    manager: ServiceManager | None = None,
    *,
    open_browser: bool = False,
) -> FastAPI:
    """Create the cockpit-first app and automatically start required services."""
    root = Path(__file__).resolve().parents[2]
    data_dir = root / ".gateway-console"
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_console_app(
        manager=manager or ServiceManager(workdir=root),
        project_root=root,
        product_connection=sqlite3.connect(
            data_dir / "product.db", check_same_thread=False
        ),
        provider_connection=sqlite3.connect(
            data_dir / "providers.db", check_same_thread=False
        ),
        credential_key_path=data_dir / "provider-master.key",
        auto_start_services=True,
        cockpit_first=True,
        replay_executor=LocalReplayExecutor(
            api_key=os.getenv("GATEWAY_REPLAY_API_KEY", "")
        ),
    )
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(COCKPIT_URL)).start()
    return app


def main() -> None:
    """Start the whole local system and open the cockpit in the browser."""
    threading.Timer(1.2, lambda: webbrowser.open(COCKPIT_URL)).start()
    uvicorn.run(
        "llm_budget_gateway.system_launcher:create_system_app",
        factory=True,
        host=HOST,
        port=PORT,
    )
