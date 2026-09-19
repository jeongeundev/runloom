"""artifact_store.py — 내용 주소 저장. 같은 바이트는 한 번만 저장한다."""

import hashlib

import pytest

from workflow.adapters.artifact_store import ArtifactStore


def test_write_returns_sha256_and_ref_and_roundtrips(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    data = b'{"report_date": "2026-09-19"}'
    sha, ref = store.write(data)
    assert sha == hashlib.sha256(data).hexdigest()
    assert ref == f"{sha[:2]}/{sha[2:4]}/{sha}"
    assert store.read(ref) == data
    assert store.exists(sha)
    assert not store.exists("0" * 64)


def test_same_bytes_are_stored_once_without_temp_leftovers(tmp_path):
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    data = b"same bytes"
    first = store.write(data)
    second = store.write(data)
    assert first == second
    files = [p for p in root.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert files[0].name == first[0]


def test_read_missing_ref_raises(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(FileNotFoundError):
        store.read("ab/cd/" + "a" * 64)
