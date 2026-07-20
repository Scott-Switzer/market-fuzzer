import json
import sqlite3
from pathlib import Path

import pytest

from scripts.operator_backup import backup_database, restore_database


def test_operator_backup_writes_integrity_checked_database_and_manifest(tmp_path) -> None:
    source = tmp_path / "arena.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence(value) VALUES ('verified')")

    destination = tmp_path / "backups" / "arena.sqlite3"
    result = backup_database(source, destination)
    manifest = json.loads(destination.with_suffix(".sqlite3.manifest.json").read_text())

    assert destination.is_file()
    assert manifest["integrity_check"] == "ok"
    assert manifest["backup_sha256"].startswith("sha256:")
    assert result["manifest_path"].endswith("arena.sqlite3.manifest.json")
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("verified",)


def test_operator_restore_verifies_checksum_and_refuses_overwrite(tmp_path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('restored')")
    backup = tmp_path / "backup.sqlite3"
    result = backup_database(source, backup)
    restored = tmp_path / "restored.sqlite3"
    restore_database(backup, Path(result["manifest_path"]), restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("restored",)
    with pytest.raises(FileExistsError):
        restore_database(backup, Path(result["manifest_path"]), restored)
    backup.write_bytes(backup.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        restore_database(backup, Path(result["manifest_path"]), tmp_path / "other.sqlite3")
