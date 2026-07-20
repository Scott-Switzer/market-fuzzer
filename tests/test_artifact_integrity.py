import pytest

from app.execution_store import ArenaStore, ArtifactIntegrityError


def test_artifact_read_fails_closed_after_content_or_manifest_corruption(tmp_path) -> None:
    store = ArenaStore(tmp_path / "integrity.sqlite3")
    store.create_experiment_job("job-1", {"scope": "integrity-test"}, "test")
    store.save_experiment_artifact("artifact-1", "job-1", "result", {"value": 1}, {"source": "test"})
    assert store.experiment_artifact("job-1", "result")["content"] == {"value": 1}
    with store.connection() as connection:
        connection.execute(
            "UPDATE experiment_artifacts SET content_json = ? WHERE artifact_id = ?",
            ('{"value":2}', "artifact-1"),
        )
    with pytest.raises(ArtifactIntegrityError, match="content"):
        store.experiment_artifact("job-1", "result")
