import json
import sqlite3

from scripts.operator_backup import backup_database


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
