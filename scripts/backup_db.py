#!/usr/bin/env python3
"""Daily SQLite backup for the llm-budget-gateway control plane.

Uses the SQLite Online Backup API (safe while the gateway is running),
keeps the N most recent snapshots, and logs to the gateway log directory.

Cron: 0 3 * * *  (03:00 local) — run via systemd timer or user crontab.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / ".gateway-console"
BACKUP_ROOT = DATA_DIR / "backups"
KEEP = 7  # daily snapshots to retain

DBS = ["product.db", "providers.db", "routing.db", "gateway.db",
       "intelligence.db", "operations.db", "evaluations.db"]


def backup_db(src: Path, dest: Path) -> tuple[int, int]:
    """Hot backup via SQLite Online Backup API. Returns (pages, size_bytes)."""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)
        size = dest.stat().st_size
        return dest_conn.total_changes, size
    finally:
        dest_conn.close()
        src_conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=KEEP, help="snapshots to retain")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = BACKUP_ROOT
    if args.dry_run:
        print(f"[dry-run] would back up {DATA_DIR} dbs to {backup_root}/{stamp}/")
        return 0

    backup_root.mkdir(parents=True, exist_ok=True)
    snapshot_dir = backup_root / stamp
    snapshot_dir.mkdir(exist_ok=True)

    results: list[str] = []
    any_error = False
    for name in DBS:
        src = DATA_DIR / name
        if not src.exists():
            continue
        dest = snapshot_dir / name
        try:
            pages, size = backup_db(src, dest)
            results.append(f"  {name}: {pages} pages, {size/1024:.1f} KiB")
        except Exception as exc:  # noqa: BLE001
            any_error = True
            results.append(f"  {name}: FAILED — {exc}")

    # Rotate: keep the newest N snapshots, remove older ones.
    snapshots = sorted(
        (p for p in backup_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    removed: list[str] = []
    for old in snapshots[:-args.keep]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)

    log = "\n".join(
        [f"[{stamp}] gateway DB backup (keep={args.keep})"] + results
        + ([f"  rotated out: {', '.join(removed)}"] if removed else [])
    )
    log_path = DATA_DIR / "logs" / "backup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fh:
        fh.write(log + "\n")
    print(log)
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
