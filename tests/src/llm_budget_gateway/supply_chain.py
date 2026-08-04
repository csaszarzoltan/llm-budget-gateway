"""Deterministic SBOM, provenance and dependency upgrade-risk controls."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?$")


@dataclass(frozen=True)
class ArtifactDescriptor:
    """Local release artifact and its safe display name."""

    name: str
    path: Path


class SBOMService:
    """Generate a deterministic CycloneDX-compatible dependency inventory."""

    def generate(
        self, *, pyproject: Path, package_lock: Path | None = None
    ) -> dict[str, Any]:
        """Read pinned Python and npm dependencies into an auditable SBOM."""
        if not pyproject.is_file():
            raise FileNotFoundError(pyproject)
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        components: list[dict[str, str]] = []
        for dependency in project.get("dependencies", []):
            match = _PIN.fullmatch(dependency)
            if match is None:
                raise ValueError(f"dependency must be exactly pinned: {dependency}")
            components.append(
                {
                    "type": "library",
                    "name": match.group(1),
                    "version": match.group(2),
                    "purl": f"pkg:pypi/{match.group(1)}@{match.group(2)}",
                }
            )
        if package_lock is not None and package_lock.is_file():
            data = json.loads(package_lock.read_text(encoding="utf-8"))
            for key, value in data.get("packages", {}).items():
                if key.startswith("node_modules/") and value.get("version"):
                    name = key.removeprefix("node_modules/")
                    version = str(value["version"])
                    components.append(
                        {
                            "type": "library",
                            "name": name,
                            "version": version,
                            "purl": f"pkg:npm/{name}@{version}",
                        }
                    )
        components.sort(key=lambda item: (item["name"].casefold(), item["version"]))
        identity = json.dumps(components, sort_keys=True, separators=(",", ":"))
        serial = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": f"urn:uuid:{serial}",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": str(project.get("name", "application")),
                    "version": str(project.get("version", "0.0.0")),
                }
            },
            "components": components,
        }


class ProvenanceService:
    """Create and verify deterministic SLSA-style artifact provenance."""

    def create(
        self,
        artifact: ArtifactDescriptor,
        *,
        builder_id: str,
        source_uri: str,
        source_digest: str,
    ) -> dict[str, Any]:
        """Build an in-toto statement bound to one local artifact digest."""
        if Path(artifact.name).name != artifact.name or not artifact.name:
            raise ValueError("artifact name must be a safe basename")
        if not artifact.path.is_file():
            raise FileNotFoundError(artifact.path)
        digest = _sha256(artifact.path)
        return {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": artifact.name, "digest": {"sha256": digest}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "builder": {"id": builder_id},
                "buildType": "https://llm-budget-gateway.dev/build/v1",
                "externalParameters": {
                    "source": {"uri": source_uri, "digest": source_digest}
                },
                "runDetails": {"byproducts": []},
            },
        }

    def verify(self, statement: dict[str, Any], artifact: Path) -> bool:
        """Return whether the artifact still matches its provenance subject."""
        if not artifact.is_file():
            return False
        try:
            expected = statement["subject"][0]["digest"]["sha256"]
        except (KeyError, IndexError, TypeError):
            return False
        return expected == _sha256(artifact)


class UpgradeRiskService:
    """Assess dependency diffs before an automated production rollout."""

    def assess(
        self,
        *,
        current: dict[str, str],
        proposed: dict[str, str],
        security_advisories: dict[str, str],
    ) -> dict[str, Any]:
        """Classify additions, removals and semantic-version changes."""
        changes: list[dict[str, str]] = []
        for package in sorted(set(current) | set(proposed)):
            old = current.get(package)
            new = proposed.get(package)
            advisory = security_advisories.get(package)
            if old is None:
                kind = "added" if new and _SEMVER.fullmatch(new) else "unpinned"
            elif new is None:
                _version(old)
                kind = "removed"
            else:
                old_parts = _version(old)
                if not _SEMVER.fullmatch(new):
                    kind = "unpinned"
                else:
                    new_parts = _version(new)
                    if new_parts[0] != old_parts[0]:
                        kind = "major"
                    elif new_parts[1] != old_parts[1]:
                        kind = "minor"
                    elif new_parts[2] != old_parts[2]:
                        kind = "patch"
                    else:
                        kind = "unchanged"
            if advisory and kind == "major":
                kind = "security-major"
            if kind != "unchanged":
                changes.append(
                    {
                        "package": package,
                        "from": old or "not installed",
                        "to": new or "removed",
                        "kind": kind,
                        "advisory": advisory or "",
                    }
                )
        high = {"major", "security-major", "unpinned", "removed"}
        risk = (
            "high"
            if any(x["kind"] in high for x in changes)
            else "medium"
            if any(x["kind"] == "minor" for x in changes)
            else "low"
        )
        return {
            "risk": risk,
            "requires_approval": risk == "high",
            "changes": changes,
            "recommendation": (
                "Block automatic rollout and require reviewed provenance, tests, and rollback evidence."
                if risk == "high"
                else "Stage the upgrade through a canary and monitor compatibility."
                if risk == "medium"
                else "Patch-only change may proceed through the standard verified pipeline."
            ),
        }


def _version(raw: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(raw)
    if match is None:
        raise ValueError(f"invalid semantic version: {raw}")
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
