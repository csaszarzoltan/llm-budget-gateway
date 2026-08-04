"""Deterministic activation and onboarding controls for gateway operators."""

from __future__ import annotations

import math
import re
from hashlib import sha256
from typing import Any


def _num(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


class SetupProgress:
    """Measure onboarding completion and identify the next required step."""

    STEPS = ("install", "virtual-key", "provider", "budget", "health", "first-request")

    def evaluate(self, completed: list[str]) -> dict[str, object]:
        done = set(completed)
        unknown = sorted(done - set(self.STEPS))
        if unknown:
            raise ValueError(f"unknown setup steps: {', '.join(unknown)}")
        missing = [step for step in self.STEPS if step not in done]
        return {
            "ready": not missing,
            "progress": len(done) / len(self.STEPS),
            "missing": missing,
            "next": missing[0] if missing else None,
        }


class EnvironmentTemplate:
    """Create a redacted environment template for selected services."""

    KEYS = {
        "gateway": ("GATEWAY_VIRTUAL_KEYS",),
        "operations": ("GATEWAY_OPERATIONS_API_KEY",),
        "quality": ("GATEWAY_EVALUATION_API_KEY",),
        "security": ("GATEWAY_SECURITY_API_KEY",),
        "resilience": ("GATEWAY_RESILIENCE_API_KEY",),
        "optimization": ("GATEWAY_OPTIMIZATION_API_KEY",),
        "collaboration": ("GATEWAY_COLLABORATION_API_KEY",),
        "platform": ("GATEWAY_PLATFORM_API_KEY",),
        "agentops": ("GATEWAY_AGENTOPS_API_KEY",),
        "fleet": ("GATEWAY_FLEET_API_KEY",),
        "assurance": ("GATEWAY_ASSURANCE_API_KEY",),
        "delivery": ("GATEWAY_DELIVERY_API_KEY",),
        "scale": ("GATEWAY_SCALE_API_KEY",),
    }

    def build(self, services: list[str]) -> dict[str, object]:
        invalid = sorted(set(services) - set(self.KEYS))
        if invalid:
            raise ValueError(f"unknown services: {', '.join(invalid)}")
        keys = sorted({key for service in services for key in self.KEYS[service]})
        lines = [f"{key}=replace-me" for key in keys]
        return {"keys": keys, "template": "\n".join(lines) + ("\n" if lines else "")}


class ProviderCredentialCheck:
    """Report provider credential presence without returning values."""

    def evaluate(self, required: list[str], configured: list[str]) -> dict[str, object]:
        req = sorted({x for x in required if isinstance(x, str) and x})
        have = set(configured)
        if not req:
            raise ValueError("required credentials must not be empty")
        missing = [x for x in req if x not in have]
        return {"ready": not missing, "missing": missing, "configured_count": len(have)}


class PortPlan:
    """Validate the service port map for duplicates and reserved ports."""

    def validate(
        self, ports: dict[str, int], reserved: list[int] | None = None
    ) -> dict[str, object]:
        used: dict[int, list[str]] = {}
        for name, port in ports.items():
            if (
                isinstance(port, bool)
                or not isinstance(port, int)
                or not 1 <= port <= 65535
            ):
                raise ValueError(f"invalid port for {name}")
            used.setdefault(port, []).append(name)
        conflicts = {
            str(port): sorted(names) for port, names in used.items() if len(names) > 1
        }
        reserved_hits = sorted(
            name for name, port in ports.items() if port in set(reserved or [])
        )
        return {
            "valid": not conflicts and not reserved_hits,
            "conflicts": conflicts,
            "reserved_hits": reserved_hits,
        }


class ConfigurationDoctor:
    """Detect common unsafe or incomplete local and production settings."""

    def inspect(
        self, config: dict[str, Any], production: bool = False
    ) -> dict[str, object]:
        findings = []
        if not config.get("GATEWAY_VIRTUAL_KEYS"):
            findings.append("virtual keys are not configured")
        timeout = config.get("GATEWAY_PROVIDER_TIMEOUT", 60)
        try:
            timeout = _num(timeout, "GATEWAY_PROVIDER_TIMEOUT", 0.1)
        except ValueError:
            findings.append("provider timeout is invalid")
            timeout = 0
        if timeout > 120:
            findings.append("provider timeout exceeds 120 seconds")
        if production and str(config.get("GATEWAY_DATABASE_URL", "")).startswith(
            "sqlite"
        ):
            findings.append("production uses SQLite")
        return {"ready": not findings, "findings": findings}


class FirstRequestBuilder:
    """Build a safe OpenAI-compatible first-request example."""

    def build(
        self, base_url: str, model: str, virtual_key_name: str = "YOUR_GATEWAY_KEY"
    ) -> dict[str, str]:
        if not re.fullmatch(r"https?://[^\s]+", base_url):
            raise ValueError("base_url must be http or https")
        if not model.strip():
            raise ValueError("model is required")
        url = base_url.rstrip("/") + "/v1/chat/completions"
        curl = f'curl -s \'{url}\' -H \'Authorization: Bearer ${virtual_key_name}\' -H \'Content-Type: application/json\' -d \'{{"model":"{model}","messages":[{{"role":"user","content":"Hello"}}]}}\''
        return {"url": url, "curl": curl}


class BudgetStarter:
    """Generate a validated starter budget configuration object."""

    def build(
        self, key_id: str, hard_limit: float, rpm_limit: int, tpm_limit: int
    ) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_id):
            raise ValueError("invalid key_id")
        hard = _num(hard_limit, "hard_limit", 0.01)
        rpm = int(_num(rpm_limit, "rpm_limit", 1))
        tpm = int(_num(tpm_limit, "tpm_limit", 1))
        return {
            "scopes": [
                {
                    "scope": {"kind": "key", "key": key_id},
                    "soft_limit": round(hard * 0.8, 6),
                    "hard_limit": hard,
                    "window": "monthly",
                    "rpm_limit": rpm,
                    "tpm_limit": tpm,
                }
            ]
        }


class ServiceProfile:
    """Select coherent service bundles for common user personas."""

    PROFILES = {
        "developer": ("gateway", "control", "quality"),
        "finops": ("gateway", "control", "optimization", "platform"),
        "sre": ("gateway", "operations", "resilience", "delivery", "scale"),
        "security": ("gateway", "security", "assurance", "fleet"),
        "all": (
            "gateway",
            "control",
            "intelligence",
            "operations",
            "quality",
            "security",
            "resilience",
            "optimization",
            "collaboration",
            "platform",
            "agentops",
            "fleet",
            "assurance",
            "delivery",
            "scale",
        ),
    }

    def resolve(self, profile: str) -> dict[str, object]:
        if profile not in self.PROFILES:
            raise ValueError("unknown profile")
        return {
            "profile": profile,
            "services": list(self.PROFILES[profile]),
            "count": len(self.PROFILES[profile]),
        }


class DiagnosticBundle:
    """Build a privacy-safe diagnostic manifest from operator evidence."""

    def build(
        self, version: str, services: list[dict[str, Any]], findings: list[str]
    ) -> dict[str, object]:
        safe_services = [
            {
                "name": str(x.get("name", "")),
                "reachable": bool(x.get("reachable")),
                "port": int(x.get("port", 0)),
            }
            for x in services
        ]
        safe = {
            "version": version,
            "services": safe_services,
            "findings": [str(x) for x in findings],
        }
        digest = sha256(repr(safe).encode()).hexdigest()
        return {**safe, "sha256": digest}


class ActivationGate:
    """Fail closed until setup, configuration, health and request checks pass."""

    REQUIRED = ("setup", "configuration", "ports", "services", "first_request")

    def decide(self, checks: dict[str, bool]) -> dict[str, object]:
        if any(not isinstance(v, bool) for v in checks.values()):
            raise ValueError("checks must be booleans")
        failed = [name for name in self.REQUIRED if checks.get(name) is not True]
        return {
            "activated": not failed,
            "failed": failed,
            "required": list(self.REQUIRED),
        }
