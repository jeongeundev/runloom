"""진단 서비스의 내용 주소 산출물 저장소 — 중앙 것과 같은 규약 (임시 파일 → rename, root/ab/cd/<sha256>)."""

import hashlib


def test_write_is_content_addressed_and_idempotent(artifact_store):
    data = b'{"a": 1}'
    sha, ref = artifact_store.write(data)
    assert sha == hashlib.sha256(data).hexdigest()
    assert ref == f"{sha[:2]}/{sha[2:4]}/{sha}"
    assert artifact_store.read(ref) == data
    assert artifact_store.exists(sha)

    again = artifact_store.write(data)
    assert again == (sha, ref)
    assert not list(artifact_store.root.rglob(".tmp-*"))


def test_different_bytes_get_different_refs(artifact_store):
    _, ref_a = artifact_store.write(b"a")
    _, ref_b = artifact_store.write(b"b")
    assert ref_a != ref_b
    assert artifact_store.read(ref_a) == b"a" and artifact_store.read(ref_b) == b"b"
