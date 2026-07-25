"""Tests for the ArtifactStore abstraction (reset integrity gate 22: no FS path leaks)."""

from __future__ import annotations

import pytest

from app.evidence.artifact_store import (
    ArtifactRef,
    FilesystemArtifactStore,
    InMemoryArtifactStore,
)


@pytest.fixture(params=["memory", "filesystem"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryArtifactStore()
    return FilesystemArtifactStore(tmp_path / "artifacts")


def test_put_get_roundtrip(store):
    ref = store.put("run/abc/historical/metrics.json", b'{"sharpe": 1.2}', content_type="application/json")
    assert store.get(ref.key) == b'{"sharpe": 1.2}'
    assert ref.size == len(b'{"sharpe": 1.2}')
    assert len(ref.sha256) == 64
    assert ref.content_type == "application/json"


def test_put_text(store):
    ref = store.put_text("run/abc/strategy/hash.txt", "deadbeef")
    assert store.get(ref.key).decode() == "deadbeef"


def test_exists(store):
    assert not store.exists("nope/x")
    store.put("nope/x", b"1")
    assert store.exists("nope/x")


def test_list_prefix(store):
    store.put("run/a/1.txt", b"1")
    store.put("run/a/2.txt", b"2")
    store.put("run/b/3.txt", b"3")
    assert store.list("run/a") == ["run/a/1.txt", "run/a/2.txt"]
    assert len(store.list("run")) == 3


def test_public_dict_has_no_filesystem_path(store):
    ref = store.put("run/abc/x.json", b"{}")
    pub = ref.public_dict()
    # Integrity gate 22: no absolute path, no store root, ever.
    serialized = str(pub)
    assert "/" not in pub["store"]
    assert not serialized.startswith("/")
    assert "tmp" not in str(pub.get("path", ""))
    assert "path" not in pub
    assert set(pub.keys()) == {"store", "key", "sha256", "size", "content_type"}


def test_public_url_is_not_a_disk_path(store):
    store.put("run/abc/x.json", b"{}")
    url = store.public_url("run/abc/x.json")
    assert url.startswith("artifact://")
    assert not url.startswith("/")


def test_public_url_missing_raises(store):
    with pytest.raises(KeyError):
        store.public_url("does/not/exist")


def test_verify_detects_match(store):
    ref = store.put("run/abc/x.json", b'{"a":1}')
    assert store.verify(ref) is True


def test_verify_detects_tamper(store):
    ref = store.put("run/abc/x.json", b'{"a":1}')
    # overwrite with different content -> stored ref no longer matches
    store.put("run/abc/x.json", b'{"a":2}')
    tampered = ArtifactRef(store=ref.store, key=ref.key, sha256=ref.sha256, size=ref.size)
    assert store.verify(tampered) is False


def test_key_normalization_strips_leading_slash(store):
    ref = store.put("/run/abc/x.json", b"{}")
    assert ref.key == "run/abc/x.json"
    assert store.exists("run/abc/x.json")


def test_traversal_key_rejected(store):
    with pytest.raises(ValueError):
        store.put("run/../../etc/passwd", b"x")


def test_filesystem_store_confines_to_root(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "root")
    with pytest.raises(ValueError):
        store.get("../outside.txt")


def test_filesystem_atomic_write_no_tmp_leftover(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "root")
    store.put("a/b.txt", b"hello")
    # No .tmp files should remain and list() must exclude them.
    assert store.list() == ["a/b.txt"]


def test_filesystem_clear(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "root")
    store.put("a/b.txt", b"hello")
    store.clear()
    assert store.list() == []
