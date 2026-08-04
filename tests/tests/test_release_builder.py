"""Release packaging hygiene tests."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


def module():
    spec = importlib.util.spec_from_file_location(
        "build_release", "scripts/build_release.py"
    )
    assert spec and spec.loader
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


def test_release_builder_requires_cockpit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    try:
        module().build_release(root, tmp_path / "x.zip")
    except ValueError as exc:
        assert "ui/dist" in str(exc)
    else:
        raise AssertionError("missing cockpit must fail closed")


def test_release_builder_excludes_runtime_state_and_keeps_cockpit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "ui/dist").mkdir(parents=True)
    (root / "ui/dist/index.html").write_text("ok")
    (root / "app.py").write_text("ok")
    (root / "runtime.db").write_text("secret")
    (root / "master.key").write_text("secret")
    (root / ".venv").mkdir()
    (root / ".venv/x").write_text("x")
    output = module().build_release(root, tmp_path / "release.zip")
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "llm-budget-gateway/app.py" in names
    assert "llm-budget-gateway/ui/dist/index.html" in names
    assert not any(
        name.endswith((".db", ".key")) or "/.venv/" in name for name in names
    )
