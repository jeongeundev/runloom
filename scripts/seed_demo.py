#!/usr/bin/env python3
"""운영자 데모 에이전트 2개를 중앙 DB 에 등록하고 연결 코드 1개를 발급한다.

    python3 scripts/seed_demo.py --db PATH --artifacts PATH --base-commit SHA [--print-code]

로컬 e2e(`scripts/local_stack.py`)와 배포 런북(Step 16)이 쓴다. 멱등이다 — 다시 실행해도 에이전트는 2개이고,
연결 프로그램이 이미 보고한 연결 정보(connector_id·연결 상태·마지막 확인·discovered)는 지우지 않는다.
비밀값은 넣지 않는다: 진단 API 자격 증명은 참조명 `env:DIAG_API_TOKEN` 뿐이다 (ARCHITECTURE 인증 절).
`DIAG_API_URL` 은 `agent-ops-demo` 의 표시용 주소이며 실제 호출 주소는 중앙 워커의 설정이 정한다.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from workflow.adapters import repo
from workflow.adapters.db import connect, init_schema
from workflow.server.auth import utc_now

DEFAULT_DIAG_API_URL = "http://127.0.0.1:8100"
LOCAL_REGISTRATION_ID = "local-demo-report"
REPOSITORY_ID = "demo-report-repo"
VERIFICATION_PROFILE_IDS = ["vp-pytest", "vp-report"]


def seed(
    db_path: Path,
    artifact_dir: Path,
    *,
    connector_id: str | None = None,
    base_commit: str,
    now: str,
    diag_api_url: str = DEFAULT_DIAG_API_URL,
) -> dict:
    """에이전트 2개 upsert + 연결 코드 1개 발급. 반환 {"connect_code": …, "agents": [agent_id, …]}."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    Path(artifact_dir).mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        init_schema(conn)
        repo.upsert_agent(conn, {
            "agent_id": "agent-ops-demo",
            "name": "운영 진단 데모",
            "owner_scope": "company",
            "connection_type": "api",
            "api_url": diag_api_url,
            "credential_ref": "env:DIAG_API_TOKEN",
            "capabilities": [{"code": "operations.diagnose", "scope": {"workflow_id": "daily-report"}}],
            "connection_state": "online",
            "shared_to_all_sessions": True,
        })
        # 연결 프로그램의 등록 보고(`repo.update_registration`)가 채운 값은 재실행해도 지우지 않는다
        existing = repo.get_agent(conn, "agent-codex-mac")
        reported = {"connector_id": connector_id, "connection_state": "offline"}
        if existing is not None:
            reported = {
                "connector_id": connector_id or existing["connector_id"],
                "connection_state": existing["connection_state"],
                "last_seen_at": existing["last_seen_at"],
                "discovered": json.loads(existing["discovered_json"]),
            }
        repo.upsert_agent(conn, {
            **reported,
            "agent_id": "agent-codex-mac",
            "name": "개인 Codex",
            "owner_scope": "personal",
            "connection_type": "local",
            "local_registration_id": LOCAL_REGISTRATION_ID,
            "repository_id": REPOSITORY_ID,
            "base_commit": base_commit,
            "verification_profile_ids": VERIFICATION_PROFILE_IDS,
            "capabilities": [{"code": "code.modify", "scope": {"repository_id": REPOSITORY_ID}}],
            "shared_to_all_sessions": True,
        })
        code = repo.issue_connect_code(conn, now)
        agents = [a["agent_id"] for a in repo.list_agents(conn)]
    finally:
        conn.close()
    return {"connect_code": code, "agents": agents}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="운영자 데모 에이전트 2개를 등록하고 연결 코드를 발급한다.")
    parser.add_argument("--db", required=True, type=Path, help="중앙 DB 경로 (WORKFLOW_DB_PATH)")
    parser.add_argument("--artifacts", required=True, type=Path, help="산출물 디렉터리 (WORKFLOW_ARTIFACT_DIR)")
    parser.add_argument("--base-commit", required=True, help="데모 저장소 기준 커밋 (scaffold_demo_repo.py 출력)")
    parser.add_argument("--print-code", action="store_true", help="발급한 연결 코드를 출력한다 (1회용, 10분)")
    args = parser.parse_args(argv)
    result = seed(
        args.db, args.artifacts, base_commit=args.base_commit, now=utc_now(),
        diag_api_url=os.environ.get("DIAG_API_URL") or DEFAULT_DIAG_API_URL,
    )
    print(f"agents={','.join(result['agents'])}")
    if args.print_code:
        print(f"connect_code={result['connect_code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
