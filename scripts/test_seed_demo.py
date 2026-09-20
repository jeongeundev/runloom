"""seed_demo.py — 운영자 데모 에이전트 2개 등록과 연결 코드 발급. 멱등이며 비밀값을 DB 에 넣지 않는다."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import seed_demo

from workflow.adapters import repo
from workflow.adapters.db import connect
from workflow.adapters.errors import NotFound

BASE_COMMIT = "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e"
NOW = "2026-09-20T00:00:00Z"


def _seed(tmp_path, **kwargs):
    return seed_demo.seed(
        tmp_path / "central" / "db.sqlite", tmp_path / "central" / "artifacts",
        base_commit=BASE_COMMIT, now=NOW, **kwargs,
    )


def _agents(tmp_path) -> dict:
    conn = connect(tmp_path / "central" / "db.sqlite")
    try:
        return {a["agent_id"]: dict(a) for a in repo.list_agents(conn)}
    finally:
        conn.close()


def test_seed_registers_two_operator_agents_shared_to_all_sessions(tmp_path):
    result = _seed(tmp_path)

    agents = _agents(tmp_path)
    assert sorted(agents) == ["agent-codex-mac", "agent-ops-demo"]
    assert sorted(result["agents"]) == sorted(agents)
    assert (tmp_path / "central" / "artifacts").is_dir()

    ops = agents["agent-ops-demo"]
    assert (ops["connection_type"], ops["owner_scope"], ops["connection_state"]) == ("api", "company", "online")
    assert json.loads(ops["capabilities_json"]) == [
        {"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}
    ]
    assert ops["credential_ref"] == "env:DIAG_API_TOKEN"
    assert ops["api_url"] == "http://127.0.0.1:8100"
    assert ops["shared_to_all_sessions"] == 1

    codex = agents["agent-codex-mac"]
    assert (codex["connection_type"], codex["owner_scope"], codex["connection_state"]) == ("local", "personal", "offline")
    assert json.loads(codex["capabilities_json"]) == [
        {"code": "code.modify", "scope": {"repository_id": "demo-report-repo"}}
    ]
    assert codex["local_registration_id"] == "local-demo-report"
    assert codex["repository_id"] == "demo-report-repo"
    assert codex["base_commit"] == BASE_COMMIT
    assert json.loads(codex["verification_profile_ids_json"]) == ["vp-pytest", "vp-report"]
    assert codex["connector_id"] is None
    assert codex["shared_to_all_sessions"] == 1


def test_diag_api_url_is_a_parameter(tmp_path):
    _seed(tmp_path, diag_api_url="http://127.0.0.1:18100")
    assert _agents(tmp_path)["agent-ops-demo"]["api_url"] == "http://127.0.0.1:18100"


def test_connect_code_can_be_exchanged_once(tmp_path):
    result = _seed(tmp_path)
    code = result["connect_code"]
    assert code

    conn = connect(tmp_path / "central" / "db.sqlite")
    try:
        connector_id, token = repo.exchange_connect_code(conn, code, "2026-09-20T00:05:00Z")
        assert connector_id.startswith("conn-") and token.startswith("wfc_")
        try:
            repo.exchange_connect_code(conn, code, "2026-09-20T00:06:00Z")
            raise AssertionError("1회용 코드가 두 번 교환됐다")
        except NotFound:
            pass
    finally:
        conn.close()


def test_seed_is_idempotent_and_issues_a_new_code_each_time(tmp_path):
    first = _seed(tmp_path)
    second = _seed(tmp_path)

    assert len(_agents(tmp_path)) == 2
    assert first["connect_code"] != second["connect_code"]


def test_reseed_keeps_connection_reported_by_connector(tmp_path):
    _seed(tmp_path)
    conn = connect(tmp_path / "central" / "db.sqlite")
    try:
        repo.update_registration(
            conn, "local-demo-report", connector_id="conn-abcd", repository_id="demo-report-repo",
            base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest", "vp-report"],
            discovered={"found": {}}, now="2026-09-20T00:10:00Z",
        )
    finally:
        conn.close()

    _seed(tmp_path)

    codex = _agents(tmp_path)["agent-codex-mac"]
    assert codex["connector_id"] == "conn-abcd"
    assert codex["connection_state"] == "online"
    assert codex["last_seen_at"] == "2026-09-20T00:10:00Z"
    assert json.loads(codex["discovered_json"]) == {"found": {}}


def test_explicit_connector_id_wins(tmp_path):
    _seed(tmp_path, connector_id="conn-explicit")
    assert _agents(tmp_path)["agent-codex-mac"]["connector_id"] == "conn-explicit"


def test_no_secret_values_stored(tmp_path):
    _seed(tmp_path)
    raw = (tmp_path / "central" / "db.sqlite").read_bytes()
    assert b"wfc_" not in raw and b"sk-" not in raw


def test_main_prints_agents_and_code_only_with_flag(tmp_path, capsys):
    db = tmp_path / "central" / "db.sqlite"
    artifacts = tmp_path / "central" / "artifacts"
    argv = ["--db", str(db), "--artifacts", str(artifacts), "--base-commit", BASE_COMMIT]

    assert seed_demo.main(argv) == 0
    out = capsys.readouterr().out
    assert "agent-ops-demo" in out and "agent-codex-mac" in out
    assert "connect_code=" not in out

    assert seed_demo.main([*argv, "--print-code"]) == 0
    out = capsys.readouterr().out
    assert "connect_code=" in out
    assert len(_agents(tmp_path)) == 2


def test_main_reads_diag_api_url_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAG_API_URL", "http://127.0.0.1:18100")
    argv = [
        "--db", str(tmp_path / "db.sqlite"), "--artifacts", str(tmp_path / "artifacts"),
        "--base-commit", BASE_COMMIT,
    ]
    assert seed_demo.main(argv) == 0
    conn = connect(tmp_path / "db.sqlite")
    try:
        assert repo.get_agent(conn, "agent-ops-demo")["api_url"] == "http://127.0.0.1:18100"
    finally:
        conn.close()
