"""Build a clean release ZIP and reject runtime state or missing cockpit assets."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

EXCLUDED_NAMES = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".db-wal",
    ".db-shm",
    ".key",
    ".pyc",
    ".log",
    ".tsbuildinfo",
}


def include(path: Path, root: Path) -> bool:
    """Return whether a repository path is safe to publish."""
    relative = path.relative_to(root)
    return not any(part in EXCLUDED_NAMES for part in relative.parts) and not any(
        path.name.endswith(x) for x in EXCLUDED_SUFFIXES
    )


def build_release(root: Path, output: Path) -> Path:
    """Create a clean ZIP containing source and the prebuilt cockpit."""
    root = root.resolve()
    if not (root / "ui/dist/index.html").is_file():
        raise ValueError("ui/dist is missing; run npm ci && npm run build")
    with tempfile.TemporaryDirectory(prefix="gateway-release-") as temp:
        staged = Path(temp) / "llm-budget-gateway"
        staged.mkdir()
        for source in root.rglob("*"):
            if not source.is_file() or not include(source, root):
                continue
            target = staged / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(staged.rglob("*")):
                if source.is_file():
                    archive.write(source, source.relative_to(staged.parent))
    return output


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(build_release(args.root, args.output))


if __name__ == "__main__":
    main()
