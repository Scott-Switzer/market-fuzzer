import json
from pathlib import Path

from app.export_world import export_world


def test_native_export_is_deterministic_and_sealed(tmp_path: Path):
    first = export_world(tmp_path / "a", "test-world-001", 42)
    second = export_world(tmp_path / "b", "test-world-001", 42)
    for relative in ("public/entities.json", "public/financials.json", "public/prices.json", "public/events.json"):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
    public = json.dumps(json.loads((first / "public/financials.json").read_text()))
    assert "hidden_truth" not in public
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["producer"]["git_sha"]
    assert manifest["artifact_hashes"]["public/financials.json"]["sha256"]
