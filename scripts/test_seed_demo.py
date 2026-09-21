"""seed_demo.py — 운영자 카탈로그 에이전트 3개 등록과 연결 코드 발급. 멱등이며 비밀값을 DB 에 넣지 않는다."""

import json
import sys
from pathlib import Path

import pytest

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


def test_seed_registers_three_catalog_agents_shared_to_all_sessions(tmp_path):
    result = _seed(tmp_path)

    agents = _agents(tmp_path)
    assert sorted(agents) == ["agent-claude-mac", "agent-codex-mac", "agent-ops-demo"]
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

    # 세 번째 — 같은 저장소·기준 커밋·검증 프로필을 맡는 Claude Code. 등록 ID 만 다르다 (connector 가 tool 로 어댑터를 고른다)
    claude = agents["agent-claude-mac"]
    assert claude["name"] == "Claude Code"
    assert (claude["connection_type"], claude["owner_scope"], claude["connection_state"]) == ("local", "personal", "offline")
    assert json.loads(claude["capabilities_json"]) == json.loads(codex["capabilities_json"])
    assert claude["local_registration_id"] == "local-demo-report-claude"
    assert claude["local_registration_id"] != codex["local_registration_id"]
    assert (claude["repository_id"], claude["base_commit"]) == (codex["repository_id"], codex["base_commit"])
    assert claude["verification_profile_ids_json"] == codex["verification_profile_ids_json"]
    assert claude["connector_id"] is None
    assert claude["shared_to_all_sessions"] == 1

    # 기본은 대본 에이전트가 아니다 — 실제 모델·도구를 쓰는 셀프호스트 배포
    assert all(a["demo_scripted"] == 0 for a in agents.values())


def test_local_registration_ids_are_one_per_tool():
    assert seed_demo.LOCAL_REGISTRATION_IDS == {"codex": "local-demo-report", "claude": "local-demo-report-claude"}
    assert seed_demo.REPOSITORY_ID == "demo-report-repo"


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

    assert len(_agents(tmp_path)) == 3
    assert first["connect_code"] != second["connect_code"]


def test_scripted_marks_all_three_agents_and_survives_reseed(tmp_path):
    _seed(tmp_path, scripted=True)
    assert {a: row["demo_scripted"] for a, row in _agents(tmp_path).items()} == {
        "agent-ops-demo": 1, "agent-codex-mac": 1, "agent-claude-mac": 1,
    }

    _seed(tmp_path, scripted=True)  # 멱등 재실행 — 값 유지, 여전히 3개
    agents = _agents(tmp_path)
    assert len(agents) == 3 and all(row["demo_scripted"] == 1 for row in agents.values())

    _seed(tmp_path)  # 플래그는 실행마다 명시한다 — 없으면 대본 표시를 내린다
    assert all(row["demo_scripted"] == 0 for row in _agents(tmp_path).values())


@pytest.mark.parametrize(
    "agent_id, registration_id",
    [("agent-codex-mac", "local-demo-report"), ("agent-claude-mac", "local-demo-report-claude")],
    ids=["codex", "claude"],
)
@pytest.mark.parametrize("scripted", [False, True], ids=["plain", "scripted"])
def test_reseed_keeps_connection_reported_by_connector(tmp_path, agent_id, registration_id, scripted):
    _seed(tmp_path, scripted=scripted)
    conn = connect(tmp_path / "central" / "db.sqlite")
    try:
        repo.update_registration(
            conn, registration_id, connector_id="conn-abcd", repository_id="demo-report-repo",
            base_commit=BASE_COMMIT, verification_profile_ids=["vp-pytest", "vp-report"],
            discovered={"found": {"git": {"head": BASE_COMMIT}}}, now="2026-09-20T00:10:00Z",
        )
    finally:
        conn.close()

    _seed(tmp_path, scripted=scripted)

    agents = _agents(tmp_path)
    row = agents[agent_id]
    assert row["connector_id"] == "conn-abcd"
    assert row["connection_state"] == "online"
    assert row["last_seen_at"] == "2026-09-20T00:10:00Z"
    assert json.loads(row["discovered_json"]) == {"found": {"git": {"head": BASE_COMMIT}}}
    assert row["demo_scripted"] == int(scripted)
    # 보고하지 않은 다른 로컬 Agent 는 그대로 offline
    other = agents["agent-claude-mac" if agent_id == "agent-codex-mac" else "agent-codex-mac"]
    assert other["connector_id"] is None and other["connection_state"] == "offline"


def test_explicit_connector_id_wins_for_both_local_agents(tmp_path):
    _seed(tmp_path, connector_id="conn-explicit")
    agents = _agents(tmp_path)
    assert agents["agent-codex-mac"]["connector_id"] == "conn-explicit"
    assert agents["agent-claude-mac"]["connector_id"] == "conn-explicit"


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
    assert out.startswith("agents=") and set(out.splitlines()[0].removeprefix("agents=").split(",")) == {
        "agent-ops-demo", "agent-codex-mac", "agent-claude-mac",
    }
    assert "connect_code=" not in out

    assert seed_demo.main([*argv, "--print-code"]) == 0
    out = capsys.readouterr().out
    assert "connect_code=" in out
    assert len(_agents(tmp_path)) == 3
    assert all(row["demo_scripted"] == 0 for row in _agents(tmp_path).values())


def test_main_scripted_flag_marks_agents(tmp_path):
    argv = [
        "--db", str(tmp_path / "db.sqlite"), "--artifacts", str(tmp_path / "artifacts"),
        "--base-commit", BASE_COMMIT, "--scripted",
    ]
    assert seed_demo.main(argv) == 0
    conn = connect(tmp_path / "db.sqlite")
    try:
        rows = {a["agent_id"]: a["demo_scripted"] for a in repo.list_agents(conn)}
    finally:
        conn.close()
    assert rows == {"agent-ops-demo": 1, "agent-codex-mac": 1, "agent-claude-mac": 1}


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
