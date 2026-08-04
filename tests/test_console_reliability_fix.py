"""Regression tests for console theme and real service lifecycle reliability."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.service_manager import ServiceDefinition, ServiceManager


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_console_has_early_persistent_theme_bootstrap_and_explicit_control() -> None:
    page = create_console_app().routes
    assert page  # app construction must remain side-effect free
    from llm_budget_gateway.console_api import _render_managed_console

    html = _render_managed_console()
    assert "id='theme'" in html
    assert "gateway-theme" in html
    assert "prefers-color-scheme: dark" in html
    assert "Apply saved theme before first paint" in html
    assert "aria-pressed" in html


def test_default_manager_uses_project_root_not_caller_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_console_app()
    manager = app.state.service_manager
    assert (manager._workdir / "src" / "llm_budget_gateway").is_dir()


@pytest.mark.asyncio
async def test_real_child_service_starts_becomes_reachable_and_stops(
    tmp_path: Path,
) -> None:
    port = _free_port()
    service = ServiceDefinition(
        "probe",
        "Probe",
        "llm_budget_gateway.console_api:create_console_app",
        port,
        "/health",
    )
    project_root = Path(__file__).resolve().parents[1]
    manager = ServiceManager(
        (service,), workdir=project_root, log_dir=tmp_path / "logs", startup_timeout=5.0
    )
    try:
        state = manager.start("probe")
        assert state["running"] is True
        assert state["reachable"] is True
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/health")
        assert response.status_code == 200
    finally:
        stopped = manager.stop("probe")
    assert stopped["running"] is False
    assert stopped["reachable"] is False


def test_failed_start_returns_log_tail_in_error(tmp_path: Path) -> None:
    service = ServiceDefinition(
        "broken", "Broken", "missing.module:create_app", _free_port(), "/"
    )
    manager = ServiceManager(
        (service,),
        workdir=Path(__file__).resolve().parents[1],
        log_dir=tmp_path / "logs",
        startup_timeout=2.0,
    )
    with pytest.raises(RuntimeError, match="Recent log"):
        manager.start("broken")


def test_manager_metadata_environment_and_missing_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ServiceDefinition("demo", "Demo", "pkg.app:create_app", 9191, "/docs")
    manager = ServiceManager((service,), workdir=tmp_path, log_dir=tmp_path / "logs")
    monkeypatch.setenv("PYTHONPATH", "/existing")
    assert manager.definitions()[0]["slug"] == "demo"
    assert manager.statuses()[0]["running"] is False
    assert manager._child_environment()["PYTHONPATH"].startswith(str(tmp_path / "src"))
    assert manager._log_tail("missing") == "log unavailable"


def test_repeat_start_returns_existing_state(tmp_path: Path) -> None:
    from unittest.mock import Mock

    from llm_budget_gateway.service_manager import ManagedProcess

    service = ServiceDefinition("demo", "Demo", "pkg.app:create_app", 9191, "/docs")
    manager = ServiceManager((service,), workdir=tmp_path, log_dir=tmp_path / "logs")
    log_path = tmp_path / "owned.log"
    log = log_path.open("wb")
    process = Mock(pid=42)
    process.poll.return_value = None
    manager._processes["demo"] = ManagedProcess(process, log, 1.0)
    try:
        state = manager.start("demo")
        assert state["running"] is True
        assert state["pid"] == 42
    finally:
        log.close()
