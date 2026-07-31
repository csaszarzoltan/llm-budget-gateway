"""Local development process manager for gateway FastAPI services."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO


@dataclass(frozen=True)
class ServiceDefinition:
    """Static launch metadata for one independently hosted FastAPI service."""

    slug: str
    name: str
    factory: str
    port: int
    home_path: str


SERVICES = (
    ServiceDefinition(
        "gateway", "Gateway", "llm_budget_gateway.main:create_app", 8000, "/docs"
    ),
    ServiceDefinition(
        "control",
        "Control Center",
        "llm_budget_gateway.control_api:create_control_app",
        8001,
        "/control",
    ),
    ServiceDefinition(
        "intelligence",
        "Intelligence",
        "llm_budget_gateway.market_api:create_market_app",
        8002,
        "/docs",
    ),
    ServiceDefinition(
        "operations",
        "Operations",
        "llm_budget_gateway.operations_api:create_operations_app",
        8003,
        "/docs",
    ),
    ServiceDefinition(
        "quality",
        "Quality",
        "llm_budget_gateway.evaluation_api:create_evaluation_app",
        8004,
        "/docs",
    ),
    ServiceDefinition(
        "security",
        "Security",
        "llm_budget_gateway.security_api:create_security_app",
        8005,
        "/docs",
    ),
    ServiceDefinition(
        "resilience",
        "Resilience",
        "llm_budget_gateway.resilience_api:create_resilience_app",
        8006,
        "/docs",
    ),
    ServiceDefinition(
        "optimization",
        "Optimization",
        "llm_budget_gateway.optimization_api:create_optimization_app",
        8007,
        "/docs",
    ),
    ServiceDefinition(
        "collaboration",
        "Collaboration",
        "llm_budget_gateway.collaboration_api:create_collaboration_app",
        8008,
        "/docs",
    ),
    ServiceDefinition(
        "platform",
        "Platform",
        "llm_budget_gateway.platform_api:create_platform_app",
        8009,
        "/docs",
    ),
    ServiceDefinition(
        "agentops",
        "AgentOps",
        "llm_budget_gateway.agentops_api:create_agentops_app",
        8010,
        "/docs",
    ),
    ServiceDefinition(
        "fleet",
        "Fleet Governance",
        "llm_budget_gateway.fleet_api:create_fleet_app",
        8011,
        "/docs",
    ),
    ServiceDefinition(
        "assurance",
        "Assurance",
        "llm_budget_gateway.assurance_api:create_assurance_app",
        8012,
        "/assurance",
    ),
    ServiceDefinition(
        "delivery",
        "Delivery",
        "llm_budget_gateway.delivery_api:create_delivery_app",
        8014,
        "/docs",
    ),
    ServiceDefinition(
        "scale", "Scale", "llm_budget_gateway.scale_api:create_scale_app", 8015, "/docs"
    ),
)


@dataclass
class ManagedProcess:
    """Runtime handles for a child process and its log stream."""

    process: subprocess.Popen[bytes]
    log: IO[bytes]
    started_at: float


class ServiceManager:
    """Start, inspect and stop local uvicorn services without using a shell."""

    def __init__(
        self,
        services: tuple[ServiceDefinition, ...] = SERVICES,
        host: str = "127.0.0.1",
        workdir: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._services = {service.slug: service for service in services}
        self._host = host
        self._workdir = Path(workdir or Path.cwd()).resolve()
        self._log_dir = Path(log_dir or self._workdir / ".gateway-console" / "logs")
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()

    def definitions(self) -> list[dict[str, object]]:
        """Return launch metadata without process handles or secrets."""
        return [asdict(service) for service in self._services.values()]

    def _definition(self, slug: str) -> ServiceDefinition:
        try:
            return self._services[slug]
        except KeyError as exc:
            raise ValueError(f"unknown service: {slug}") from exc

    def _is_port_open(self, port: int) -> bool:
        try:
            with socket.create_connection((self._host, port), timeout=0.12):
                return True
        except OSError:
            return False

    def _reap(self, slug: str) -> None:
        managed = self._processes.get(slug)
        if managed is not None and managed.process.poll() is not None:
            managed.log.close()
            self._processes.pop(slug, None)

    def status(self, slug: str) -> dict[str, object]:
        """Return process and port state for one service."""
        service = self._definition(slug)
        with self._lock:
            self._reap(slug)
            managed = self._processes.get(slug)
            running = managed is not None and managed.process.poll() is None
            return {
                **asdict(service),
                "running": running,
                "reachable": self._is_port_open(service.port),
                "pid": managed.process.pid if running else None,
                "started_at": managed.started_at if running else None,
                "managed": running,
                "url": f"http://{self._host}:{service.port}{service.home_path}",
                "log_path": str(self._log_dir / f"{slug}.log"),
            }

    def statuses(self) -> list[dict[str, object]]:
        """Return state for every configured service."""
        return [self.status(slug) for slug in self._services]

    def start(self, slug: str) -> dict[str, object]:
        """Start one uvicorn child process, or return its current state."""
        service = self._definition(slug)
        with self._lock:
            self._reap(slug)
            if slug in self._processes:
                return self.status(slug)
            if self._is_port_open(service.port):
                raise RuntimeError(
                    f"port {service.port} is already in use by an unmanaged process"
                )

            self._log_dir.mkdir(parents=True, exist_ok=True)
            log = (self._log_dir / f"{slug}.log").open("ab", buffering=0)
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                service.factory,
                "--factory",
                "--host",
                self._host,
                "--port",
                str(service.port),
            ]
            kwargs: dict[str, object] = {
                "cwd": str(self._workdir),
                "stdin": subprocess.DEVNULL,
                "stdout": log,
                "stderr": subprocess.STDOUT,
                "env": os.environ.copy(),
                "shell": False,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
            except Exception:
                log.close()
                raise
            self._processes[slug] = ManagedProcess(process, log, time.time())
            time.sleep(0.08)
            if process.poll() is not None:
                self._reap(slug)
                raise RuntimeError(
                    f"{service.name} exited during startup; inspect {self._log_dir / f'{slug}.log'}"
                )
            return self.status(slug)

    def start_all(self) -> list[dict[str, object]]:
        """Start every service and report per-service failures without aborting the batch."""
        results: list[dict[str, object]] = []
        for slug in self._services:
            try:
                results.append(self.start(slug))
            except RuntimeError as exc:
                results.append({**self.status(slug), "error": str(exc)})
        return results

    def stop(self, slug: str, timeout: float = 4.0) -> dict[str, object]:
        """Stop one process started by this manager; never kill unmanaged processes."""
        self._definition(slug)
        with self._lock:
            self._reap(slug)
            managed = self._processes.get(slug)
            if managed is None:
                return self.status(slug)
            managed.process.terminate()
            try:
                managed.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                managed.process.kill()
                managed.process.wait(timeout=timeout)
            managed.log.close()
            self._processes.pop(slug, None)
            return self.status(slug)

    def stop_all(self) -> list[dict[str, object]]:
        """Stop every process owned by this manager."""
        return [self.stop(slug) for slug in reversed(tuple(self._services))]
