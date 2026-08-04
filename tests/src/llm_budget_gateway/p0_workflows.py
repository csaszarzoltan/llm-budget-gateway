"""Research-ranked provider compatibility and incident explanation workflows."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CompatibilityProbe:
    """One bounded provider capability check."""

    capability: str
    passed: bool
    latency_ms: int
    detail: str = ""


@dataclass(frozen=True)
class RepairAction:
    """A concrete repair for one failed provider capability."""

    capability: str
    action: str
    evidence: str


@dataclass(frozen=True)
class CompatibilityResult:
    """Provider readiness score with ordered repair actions."""

    provider_id: str
    status: str
    score: int
    passed: int
    total: int
    probes: tuple[CompatibilityProbe, ...]
    repairs: tuple[RepairAction, ...]


class ProviderCompatibilityLab:
    """Score provider capabilities and turn failures into repair instructions."""

    def evaluate(
        self, *, provider_id: str, probes: list[CompatibilityProbe]
    ) -> CompatibilityResult:
        """Validate and summarize a complete provider probe run."""
        if not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not probes:
            raise ValueError("at least one compatibility probe is required")
        capabilities = [probe.capability.strip() for probe in probes]
        if any(not capability for capability in capabilities):
            raise ValueError("probe capability must be non-empty")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("duplicate probe capability")
        if any(probe.latency_ms < 0 for probe in probes):
            raise ValueError("probe latency must be non-negative")
        passed = sum(probe.passed for probe in probes)
        score = round(passed * 100 / len(probes))
        failed = [probe for probe in probes if not probe.passed]
        status = (
            "ready"
            if not failed
            else "blocked"
            if any(
                probe.capability == "authentication" and not probe.passed
                for probe in probes
            )
            else "degraded"
        )
        repairs = tuple(self._repair(probe) for probe in failed)
        return CompatibilityResult(
            provider_id.strip(),
            status,
            score,
            passed,
            len(probes),
            tuple(probes),
            repairs,
        )

    def _repair(self, probe: CompatibilityProbe) -> RepairAction:
        capability = probe.capability.lower()
        actions = {
            "authentication": "Verify the stored credential, account permissions, and provider authentication header.",
            "model_discovery": "Verify the model-list path and that the credential can list models.",
            "streaming": "Verify the base URL includes the provider's /v1 path and that chat streaming is enabled.",
            "tools": "Select a tool-capable model and verify tool_choice and tool schema support.",
            "structured_output": "Select a model supporting JSON schema and verify response_format compatibility.",
            "embeddings": "Select an embedding model and verify the embeddings endpoint path.",
            "vision": "Select a vision-capable model and verify image input format and size.",
        }
        return RepairAction(
            probe.capability,
            actions.get(
                capability,
                "Review the provider response and update this capability's connection settings.",
            ),
            probe.detail or "Probe did not pass.",
        )


@dataclass(frozen=True)
class IncidentEvidence:
    """One privacy-safe fact in an incident decision timeline."""

    incident_id: str
    timestamp: int
    kind: str
    outcome: str
    summary: str
    severity: str
    details: dict[str, Any]


class IncidentTimelineStore:
    """Persist, redact, and explain chronological incident evidence."""

    _SEVERITIES = {"info", "warning", "critical"}
    _SECRET_KEYS = {
        "authorization",
        "api_key",
        "apikey",
        "x-api-key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
    }

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Initialize the SQLite incident evidence store."""
        self._connection = connection
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS incident_evidence (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL,
            summary TEXT NOT NULL, severity TEXT NOT NULL, details_json TEXT NOT NULL)"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_incident_evidence ON incident_evidence(incident_id,timestamp,event_id)"
        )
        self._connection.commit()

    def append(self, evidence: IncidentEvidence) -> IncidentEvidence:
        """Validate, redact, and append one evidence event."""
        if not evidence.incident_id.strip() or not evidence.kind.strip():
            raise ValueError("incident_id and kind must be non-empty")
        if evidence.timestamp < 0:
            raise ValueError("timestamp must be non-negative")
        if evidence.severity not in self._SEVERITIES:
            raise ValueError("severity must be info, warning, or critical")
        safe = IncidentEvidence(
            evidence.incident_id.strip(),
            evidence.timestamp,
            evidence.kind.strip(),
            evidence.outcome.strip(),
            self._redact_text(evidence.summary),
            evidence.severity,
            self._redact_details(evidence.details),
        )
        self._connection.execute(
            "INSERT INTO incident_evidence(incident_id,timestamp,kind,outcome,summary,severity,details_json) VALUES(?,?,?,?,?,?,?)",
            (
                safe.incident_id,
                safe.timestamp,
                safe.kind,
                safe.outcome,
                safe.summary,
                safe.severity,
                json.dumps(safe.details, sort_keys=True),
            ),
        )
        self._connection.commit()
        return safe

    def explain(self, incident_id: str) -> dict[str, Any]:
        """Return ordered evidence with the incident impact and concrete repair."""
        rows = self._connection.execute(
            "SELECT incident_id,timestamp,kind,outcome,summary,severity,details_json FROM incident_evidence WHERE incident_id=? ORDER BY timestamp,event_id",
            (incident_id,),
        ).fetchall()
        if not rows:
            raise KeyError(incident_id)
        events = [
            IncidentEvidence(
                str(r[0]),
                int(r[1]),
                str(r[2]),
                str(r[3]),
                str(r[4]),
                str(r[5]),
                json.loads(r[6]),
            )
            for r in rows
        ]
        critical = next(
            (event for event in events if event.severity == "critical"), events[-1]
        )
        status = (
            "recovered"
            if any(event.outcome == "recovered" for event in events)
            else "active"
        )
        return {
            "incident_id": incident_id,
            "status": status,
            "impact": critical.summary,
            "why": f"{critical.kind.title()} evidence: {critical.summary}",
            "fix": self._fix(events),
            "timeline": [asdict(event) for event in events],
        }

    def _fix(self, events: list[IncidentEvidence]) -> str:
        text = " ".join(f"{event.kind} {event.summary}" for event in events).lower()
        if "429" in text or "rate" in text:
            return "Review provider rate limits, reduce concurrency, and keep the healthy fallback route enabled."
        if "budget" in text or "cost" in text or "412" in text:
            return "Review the application budget, stop runaway work, then raise the budget only with an approved justification."
        if "auth" in text or "401" in text or "403" in text:
            return "Rotate or verify the provider credential and retest the connection before replaying traffic."
        if "timeout" in text or "5" in text:
            return "Check provider health and latency, then route to a healthy fallback or adjust the bounded timeout."
        return "Inspect the linked route, provider, policy, and trace evidence before retrying the request."

    def _redact_details(self, details: dict[str, Any]) -> dict[str, Any]:
        return {
            key: "[REDACTED]"
            if key.lower() in self._SECRET_KEYS
            else self._redact_value(value)
            for key, value in details.items()
        }

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, dict):
            return self._redact_details(value)
        return value

    @staticmethod
    def _redact_text(value: str) -> str:
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
        value = re.sub(r"\bAKIA[A-Z0-9]{16}\b", "[REDACTED]", value)
        value = re.sub(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
            "[REDACTED]",
            value,
        )
        return re.sub(r"\bBearer\s+\S+", "[REDACTED]", value, flags=re.IGNORECASE)


class CompatibilityRunStore:
    """Persist bounded provider compatibility history for trend inspection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Initialize the SQLite compatibility-run store."""
        self._connection = connection
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS compatibility_runs (
            run_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, checked_at INTEGER NOT NULL,
            status TEXT NOT NULL, score INTEGER NOT NULL, passed INTEGER NOT NULL,
            total INTEGER NOT NULL, result_json TEXT NOT NULL)"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_compatibility_history ON compatibility_runs(provider_id,checked_at DESC,run_id DESC)"
        )
        self._connection.commit()

    def save(self, result: CompatibilityResult, *, checked_at: int) -> dict[str, Any]:
        """Save one compatibility result and return its public history record."""
        import secrets

        if checked_at < 0:
            raise ValueError("checked_at must be non-negative")
        run_id = "compat_" + secrets.token_hex(8)
        record = {
            "run_id": run_id,
            "provider_id": result.provider_id,
            "checked_at": checked_at,
            "status": result.status,
            "score": result.score,
            "passed": result.passed,
            "total": result.total,
            "repairs": [asdict(item) for item in result.repairs],
        }
        self._connection.execute(
            "INSERT INTO compatibility_runs VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id,
                result.provider_id,
                checked_at,
                result.status,
                result.score,
                result.passed,
                result.total,
                json.dumps(record, sort_keys=True),
            ),
        )
        self._connection.commit()
        return record

    def list(self, provider_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """List newest compatibility runs for one provider."""
        if not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self._connection.execute(
            "SELECT result_json FROM compatibility_runs WHERE provider_id=? ORDER BY checked_at DESC,run_id DESC LIMIT ?",
            (provider_id, limit),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


class ProviderCompatibilityRunner:
    """Execute provider capability probes against a stored provider connection."""

    def __init__(self, store: object, transport: object | None = None) -> None:
        """Create a runner over a provider store and optional HTTP transport."""
        self._store = store
        self._transport = transport

    async def run(self, provider_id: str) -> CompatibilityResult:
        """Run live, non-destructive provider checks and return measured evidence."""
        import time

        import httpx

        from .provider_connections import _discovery_request, _parse_models

        provider = self._store.get(provider_id)
        config = self._store.connection_secret(provider_id)
        provider_type = str(provider["provider_type"])
        discovery = _discovery_request(provider_type, config)
        probes: list[CompatibilityProbe] = []
        model_id = ""

        async with httpx.AsyncClient(transport=self._transport, timeout=15.0) as client:
            for capability in ("authentication", "model_discovery"):
                started = time.perf_counter()
                try:
                    response = await client.request(**discovery)
                    response.raise_for_status()
                    parsed = _parse_models(provider_type, response.json(), config)
                    if parsed:
                        model_id = str(parsed[0]["id"])
                    passed = bool(parsed) if capability == "model_discovery" else True
                    detail = (
                        f"Discovered {len(parsed)} model(s)."
                        if capability == "model_discovery"
                        else "Stored credentials were accepted by the provider."
                    )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    passed, detail = False, self._safe_error(exc)
                probes.append(
                    CompatibilityProbe(
                        capability,
                        passed,
                        int((time.perf_counter() - started) * 1000),
                        detail,
                    )
                )

            if not model_id:
                models = self._store.models(provider_id)
                model_id = str(models[0]["id"]) if models else "compatibility-probe"

            for capability, request in self._capability_requests(
                provider_type, config, model_id
            ):
                started = time.perf_counter()
                try:
                    response = await client.request(**request)
                    response.raise_for_status()
                    passed, detail = self._validate_response(capability, response)
                except (httpx.HTTPError, ValueError) as exc:
                    passed, detail = False, self._safe_error(exc)
                probes.append(
                    CompatibilityProbe(
                        capability,
                        passed,
                        int((time.perf_counter() - started) * 1000),
                        detail,
                    )
                )
        return ProviderCompatibilityLab().evaluate(
            provider_id=provider_id, probes=probes
        )

    def _capability_requests(
        self, provider_type: str, config: dict[str, Any], model_id: str
    ) -> list[tuple[str, dict[str, Any]]]:
        base = str(config.get("base_url", "")).rstrip("/")
        headers: dict[str, str] = {"content-type": "application/json"}
        params: dict[str, str] = {}
        if provider_type in {"openai", "openai_compatible", "custom"}:
            key = str(config.get("api_key", ""))
            header = str(config.get("auth_header", "Authorization"))
            prefix = str(config.get("auth_prefix", "Bearer "))
            if key:
                headers[header] = prefix + key
            chat_url = base + "/chat/completions"
            embeddings_url = base + "/embeddings"
        elif provider_type == "azure_openai":
            headers["api-key"] = str(config.get("api_key", ""))
            params["api-version"] = str(config.get("api_version", ""))
            chat_url = base + f"/openai/deployments/{model_id}/chat/completions"
            embeddings_url = base + f"/openai/deployments/{model_id}/embeddings"
        elif provider_type == "anthropic":
            headers.update(
                {
                    "x-api-key": str(config.get("api_key", "")),
                    "anthropic-version": "2023-06-01",
                }
            )
            chat_url = base + "/messages"
            embeddings_url = base + "/embeddings"
        elif provider_type == "gemini":
            params["key"] = str(config.get("api_key", ""))
            chat_url = base + f"/models/{model_id}:generateContent"
            embeddings_url = base + f"/models/{model_id}:embedContent"
        else:
            return []

        if provider_type == "anthropic":
            basic = {
                "model": model_id,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Return OK"}],
            }
            tools = {
                **basic,
                "tools": [
                    {
                        "name": "ping",
                        "description": "Compatibility probe",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            }
        elif provider_type == "gemini":
            basic = {"contents": [{"parts": [{"text": "Return OK"}]}]}
            tools = basic
        else:
            basic = {
                "model": model_id,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Return OK"}],
            }
            tools = {
                **basic,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "Compatibility probe",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": "auto",
            }

        structured = (
            {**basic, "response_format": {"type": "json_object"}}
            if provider_type not in {"anthropic", "gemini"}
            else basic
        )
        streaming = {**basic, "stream": True}
        embedding_body = (
            {"model": model_id, "input": "compatibility probe"}
            if provider_type != "gemini"
            else {"content": {"parts": [{"text": "compatibility probe"}]}}
        )
        return [
            (
                "chat",
                {
                    "method": "POST",
                    "url": chat_url,
                    "headers": headers,
                    "params": params,
                    "json": basic,
                },
            ),
            (
                "streaming",
                {
                    "method": "POST",
                    "url": chat_url,
                    "headers": headers,
                    "params": params,
                    "json": streaming,
                },
            ),
            (
                "tools",
                {
                    "method": "POST",
                    "url": chat_url,
                    "headers": headers,
                    "params": params,
                    "json": tools,
                },
            ),
            (
                "structured_output",
                {
                    "method": "POST",
                    "url": chat_url,
                    "headers": headers,
                    "params": params,
                    "json": structured,
                },
            ),
            (
                "embeddings",
                {
                    "method": "POST",
                    "url": embeddings_url,
                    "headers": headers,
                    "params": params,
                    "json": embedding_body,
                },
            ),
        ]

    @staticmethod
    def _validate_response(capability: str, response: object) -> tuple[bool, str]:
        content_type = str(response.headers.get("content-type", ""))
        if capability == "streaming":
            text = response.text
            passed = "text/event-stream" in content_type or "data:" in text
            return (
                passed,
                "Streaming response received."
                if passed
                else "Provider returned a non-streaming response.",
            )
        try:
            payload = response.json()
        except ValueError:
            return False, "Provider returned a non-JSON response."
        return isinstance(payload, dict), "Provider returned a valid response object."

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = re.sub(r"(?i)bearer\s+\S+", "[REDACTED]", str(exc))
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
        return text[:300]
