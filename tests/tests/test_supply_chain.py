"""TDD coverage for the research-ranked supply-chain security center."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.supply_chain import (
    ArtifactDescriptor,
    ProvenanceService,
    SBOMService,
    UpgradeRiskService,
)


def test_sbom_generates_deterministic_cyclonedx_components(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname="demo"\nversion="1.2.3"\ndependencies=["fastapi==0.141.1", "pydantic==2.13.4"]\n'
    )
    package = tmp_path / "package-lock.json"
    package.write_text(
        json.dumps(
            {
                "name": "ui",
                "version": "1.0.0",
                "packages": {"node_modules/react": {"version": "19.1.1"}},
            }
        )
    )
    first = SBOMService().generate(pyproject=pyproject, package_lock=package)
    second = SBOMService().generate(pyproject=pyproject, package_lock=package)
    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert [(x["name"], x["version"]) for x in first["components"]] == [
        ("fastapi", "0.141.1"),
        ("pydantic", "2.13.4"),
        ("react", "19.1.1"),
    ]
    assert len(first["serialNumber"].removeprefix("urn:uuid:")) == 32


def test_sbom_requires_exact_pins_and_valid_files(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname="demo"\nversion="1"\ndependencies=["fastapi>=1"]\n'
    )
    with pytest.raises(ValueError, match="pinned"):
        SBOMService().generate(pyproject=project)
    with pytest.raises(FileNotFoundError):
        SBOMService().generate(pyproject=tmp_path / "missing.toml")


def test_provenance_sign_and_verify_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "release.zip"
    artifact.write_bytes(b"release bytes")
    service = ProvenanceService()
    statement = service.create(
        ArtifactDescriptor("release.zip", artifact),
        builder_id="urn:builder:github-actions",
        source_uri="https://example.invalid/repo",
        source_digest="a" * 40,
    )
    assert (
        statement["subject"][0]["digest"]["sha256"]
        == hashlib.sha256(b"release bytes").hexdigest()
    )
    assert service.verify(statement, artifact) is True
    artifact.write_bytes(b"tampered")
    assert service.verify(statement, artifact) is False


def test_provenance_rejects_unsafe_or_missing_artifacts(tmp_path: Path) -> None:
    service = ProvenanceService()
    with pytest.raises(ValueError, match="name"):
        service.create(
            ArtifactDescriptor("../bad", tmp_path / "x"),
            builder_id="b",
            source_uri="s",
            source_digest="d",
        )
    with pytest.raises(FileNotFoundError):
        service.create(
            ArtifactDescriptor("good.zip", tmp_path / "missing"),
            builder_id="b",
            source_uri="s",
            source_digest="d",
        )


def test_upgrade_risk_flags_major_unpinned_removed_and_security_changes() -> None:
    result = UpgradeRiskService().assess(
        current={"gateway": "1.9.0", "safe": "2.1.0", "removed": "1.0.0"},
        proposed={"gateway": "2.0.0", "safe": "2.2.0", "new": ">=3"},
        security_advisories={"gateway": "CVE-2099-0001"},
    )
    assert result["risk"] == "high"
    assert result["requires_approval"] is True
    changes = {x["package"]: x["kind"] for x in result["changes"]}
    assert changes == {
        "gateway": "security-major",
        "new": "unpinned",
        "removed": "removed",
        "safe": "minor",
    }
    assert result["recommendation"].startswith("Block automatic rollout")


def test_upgrade_risk_low_for_patch_and_validates_versions() -> None:
    service = UpgradeRiskService()
    result = service.assess(
        current={"a": "1.0.0"}, proposed={"a": "1.0.1"}, security_advisories={}
    )
    assert result["risk"] == "low"
    assert result["requires_approval"] is False
    with pytest.raises(ValueError, match="semantic version"):
        service.assess(
            current={"a": "latest"}, proposed={"a": "1.0.0"}, security_advisories={}
        )


@pytest.mark.asyncio
async def test_supply_chain_api_real_http_flow(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname="demo"\nversion="1.0.0"\ndependencies=["fastapi==0.141.1"]\n'
    )
    app = create_console_app(project_root=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        sbom = await client.get("/v1/console/supply-chain/sbom")
        assert sbom.status_code == 200
        assert sbom.json()["components"][0]["name"] == "fastapi"
        risk = await client.post(
            "/v1/console/supply-chain/upgrade-risk",
            json={
                "current": {"a": "1.0.0"},
                "proposed": {"a": "2.0.0"},
                "security_advisories": {},
            },
        )
        assert risk.status_code == 200
        assert risk.json()["risk"] == "high"
        bad = await client.post(
            "/v1/console/supply-chain/upgrade-risk",
            json={"current": {"a": "nope"}, "proposed": {"a": "2.0.0"}},
        )
        assert bad.status_code == 422
