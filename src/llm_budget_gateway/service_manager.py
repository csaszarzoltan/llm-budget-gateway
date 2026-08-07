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

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceDefinition:
    """Static launch metadata for one independently hosted FastAPI service."""

    slug: str
    name: str
    factory: str
    port: int
    home_path: str
    workers: int | None = None  # None → GATEWAY_WORKERS env or 1 (uvicorn default)


SERVICES = (
    # Only the proxy is auto-started by default. The satellite services
    # that shipped with early demo versions are intentionally NOT started:
    # their useful capabilities (intelligence, prompts, quality) have been
    # folded into the cockpit product API and the rest were placeholder
    # dashboards with no real implementation. Set
    # GATEWAY_ENABLE_SATELLITES=1 to bring back the legacy services.
    ServiceDefinition(
        "gateway", "Gateway", "llm_budget_gateway.main:create_app", 8000, "/docs"
    ),
) if os.environ.get("GATEWAY_ENABLE_SATELLITES", "") != "1" else (
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
        startup_timeout: float = 25.0,
    ) -> None:
        self._services = {service.slug: service for service in services}
        self._host = host
        self._workdir = Path(workdir or Path.cwd()).resolve()
        self._log_dir = Path(log_dir or self._workdir / ".gateway-console" / "logs")
        self._processes: dict[str, ManagedProcess] = {}
        self._startup_timeout = max(0.2, float(startup_timeout))
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
            # The previous process may still be draining (TIME_WAIT / slow
            # shutdown) right after a restart — wait briefly for the port to
            # free up instead of failing the whole startup silently.
            if self._is_port_open(service.port):
                deadline = time.monotonic() + self._startup_timeout
                while self._is_port_open(service.port) and time.monotonic() < deadline:
                    time.sleep(0.25)
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
            # Worker count: per-service override, else GATEWAY_WORKERS env,
            # else uvicorn's default (1). Multiple workers give real
            # CPU-parallelism; the shared SQLite stores are WAL + lock-serialized
            # and the in-memory caches are per-worker (acceptable trade-off).
            workers = service.workers
            if workers is None:
                raw = os.environ.get("GATEWAY_WORKERS", "")
                try:
                    workers = int(raw) if raw.strip() else None
                except ValueError:
                    workers = None
            if workers is not None and workers > 1:
                command += ["--workers", str(workers)]
            kwargs: dict[str, object] = {
                "cwd": str(self._workdir),
                "stdin": subprocess.DEVNULL,
                "stdout": log,
                "stderr": subprocess.STDOUT,
                "env": self._child_environment(),
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
            deadline = time.monotonic() + self._startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self._is_port_open(service.port):
                    return self.status(slug)
                time.sleep(0.05)
            alive = process.poll() is None
            if alive:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
            self._reap(slug)
            reason = "did not become reachable" if alive else "exited during startup"
            raise RuntimeError(
                f"{service.name} {reason} on port {service.port}. "
                f"Recent log: {self._log_tail(slug)}"
            )

    def _child_environment(self) -> dict[str, str]:
        """Return an import-safe environment for source and installed checkouts."""
        environment = os.environ.copy()
        source = str(self._workdir / "src")
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            source if not existing else source + os.pathsep + existing
        )
        environment.setdefault("PYTHONUNBUFFERED", "1")
        return environment

    def _log_tail(self, slug: str, limit: int = 1800) -> str:
        """Return bounded startup diagnostics from a child log."""
        try:
            text = (
                (self._log_dir / f"{slug}.log")
                .read_text(encoding="utf-8", errors="replace")[-limit:]
                .strip()
            )
        except OSError:
            return "log unavailable"
        return " | ".join(text.splitlines()[-8:]) or "log is empty"

    def start_all(self) -> list[dict[str, object]]:
        """Start every service and report per-service failures without aborting the batch."""
        results: list[dict[str, object]] = []
        for slug in self._services:
            try:
                results.append(self.start(slug))
            except RuntimeError as exc:
                logger.error("service %s failed to start: %s", slug, exc)
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
