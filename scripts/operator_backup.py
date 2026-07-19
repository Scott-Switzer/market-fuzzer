"""Create and verify an online SQLite backup for the research appliance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def backup_database(source: Path, destination: Path) -> dict[str, Any]:
    """Back up a live SQLite database and write a checksum manifest beside it."""

    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with sqlite3.connect(source) as source_connection, sqlite3.connect(temporary) as backup_connection:
            source_connection.backup(backup_connection)
            integrity = str(backup_connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            user_version = int(backup_connection.execute("PRAGMA user_version").fetchone()[0])
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    try:
        manifest = {
            "schema_version": "operator_backup_v1",
            "created_at": datetime.now(UTC).isoformat(),
            "source_database": source.name,
            "backup_database": destination.name,
            "backup_sha256": _sha256(destination),
            "backup_bytes": destination.stat().st_size,
            "integrity_check": "ok",
            "sqlite_user_version": user_version,
            "claim_boundary": "durability evidence for the supported single-database appliance; not disaster recovery or HA proof",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except Exception:
        if destination.exists():
            destination.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
        raise
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.getenv("ARENA_DB_PATH", "artifacts/arena.sqlite3"),
        help="SQLite database path; defaults to ARENA_DB_PATH",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("ARENA_BACKUP_DIR", "/data/backups"),
        help="Directory for the backup and manifest; defaults to ARENA_BACKUP_DIR or /data/backups",
    )
    parser.add_argument("--label", default="arena", help="Backup filename label")
    args = parser.parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(args.output_dir) / f"{args.label}-{timestamp}.sqlite3"
    print(json.dumps(backup_database(Path(args.database), destination), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
