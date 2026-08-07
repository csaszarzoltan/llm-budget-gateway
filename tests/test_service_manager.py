"""Process lifecycle tests for the local console service manager."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from llm_budget_gateway.service_manager import ServiceDefinition, ServiceManager


@pytest.fixture
def one_service() -> tuple[ServiceDefinition, ...]:
    return (ServiceDefinition("demo", "Demo", "pkg.app:create_app", 9191, "/docs"),)


def test_unknown_service_is_rejected(one_service, tmp_path):
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with pytest.raises(ValueError, match="unknown service"):
        manager.start("missing")


def test_start_uses_current_python_without_shell(one_service, tmp_path):
    process = Mock(pid=1234)
    process.poll.return_value = None
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.object(manager, "_is_port_open", side_effect=[False, True, True]),
        patch(
            "llm_budget_gateway.service_manager.subprocess.Popen", return_value=process
        ) as popen,
        patch("llm_budget_gateway.service_manager.time.sleep"),
    ):
        result = manager.start("demo")
    command = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert command[1:4] == ["-m", "uvicorn", "pkg.app:create_app"]
    assert "--factory" in command and "9191" in command
    assert kwargs["shell"] is False
    assert result["running"] is True
    manager._processes["demo"].log.close()


def test_occupied_unmanaged_port_is_not_started(one_service, tmp_path):
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.object(manager, "_is_port_open", return_value=True),
        pytest.raises(RuntimeError, match="unmanaged process"),
    ):
        manager.start("demo")


def test_stop_only_terminates_process_owned_by_manager(one_service, tmp_path):
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with patch.object(manager, "_is_port_open", return_value=True):
        state = manager.stop("demo")
    assert state["managed"] is False
    assert state["reachable"] is True


def test_start_all_reports_failure_per_service(one_service, tmp_path):
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.object(manager, "start", side_effect=RuntimeError("boom")),
        patch.object(
            manager, "status", return_value={"slug": "demo", "running": False}
        ),
    ):
        result = manager.start_all()
    assert result == [{"slug": "demo", "running": False, "error": "boom"}]


def test_workers_flag_from_env(one_service, tmp_path):
    """GATEWAY_WORKERS env must translate into uvicorn --workers N."""
    process = Mock(pid=1234)
    process.poll.return_value = None
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.dict("os.environ", {"GATEWAY_WORKERS": "4"}, clear=False),
        patch.object(manager, "_is_port_open", side_effect=[False, True, True]),
        patch(
            "llm_budget_gateway.service_manager.subprocess.Popen", return_value=process
        ) as popen,
        patch("llm_budget_gateway.service_manager.time.sleep"),
    ):
        manager.start("demo")
    command = popen.call_args.args[0]
    assert "--workers" in command
    assert command[command.index("--workers") + 1] == "4"
    manager._processes["demo"].log.close()


def test_no_workers_flag_when_env_unset(one_service, tmp_path):
    """Without GATEWAY_WORKERS the command must not contain --workers."""
    process = Mock(pid=1234)
    process.poll.return_value = None
    manager = ServiceManager(one_service, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(manager, "_is_port_open", side_effect=[False, True, True]),
        patch(
            "llm_budget_gateway.service_manager.subprocess.Popen", return_value=process
        ) as popen,
        patch("llm_budget_gateway.service_manager.time.sleep"),
    ):
        manager.start("demo")
    command = popen.call_args.args[0]
    assert "--workers" not in command
    manager._processes["demo"].log.close()


def test_per_service_workers_override(one_service, tmp_path):
    """A ServiceDefinition.workers value must override the env."""
    services = (ServiceDefinition("demo", "Demo", "pkg.app:create_app", 9191, "/docs", workers=2),)
    process = Mock(pid=1234)
    process.poll.return_value = None
    manager = ServiceManager(services, workdir=tmp_path, log_dir=tmp_path / "logs")
    with (
        patch.dict("os.environ", {"GATEWAY_WORKERS": "8"}, clear=False),
        patch.object(manager, "_is_port_open", side_effect=[False, True, True]),
        patch(
            "llm_budget_gateway.service_manager.subprocess.Popen", return_value=process
        ) as popen,
        patch("llm_budget_gateway.service_manager.time.sleep"),
    ):
        manager.start("demo")
    command = popen.call_args.args[0]
    assert command[command.index("--workers") + 1] == "2"
    manager._processes["demo"].log.close()
