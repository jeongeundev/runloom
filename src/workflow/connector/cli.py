"""`python3 -m workflow.connector` — 운영자 Mac 의 연결 프로그램 CLI.

    connect  --server URL --code CONNECT_CODE        연결 코드 교환 → 토큰 파일(0600)
    register --id ID --repo PATH --repository-id RID [--verify NAME=CMD ...]
                                                     로컬 등록 저장 + discovery 결과를 중앙에 보고
    run      [--adapter echo]                        claim 루프

검증 명령은 `--verify` 인자로만 받아 `shlex.split` 한 인자 배열을 로컬에 둔다. 서버·요청·근거에서 명령을 받지 않는다.
"""

import argparse
import logging
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx

from workflow.connector import state
from workflow.connector.adapter import EchoAdapter
from workflow.connector.client import CentralClient, CentralError, Unreachable
from workflow.connector.config import ConnectorPaths, connector_paths, read_token, write_token
from workflow.connector.discovery import discover
from workflow.connector.runner import Runner, utc_now

# Step 12 가 CodexAdapter 를 추가하며 기본값을 바꾼다
ADAPTERS = {"echo": EchoAdapter}


def _verify_arg(value: str) -> tuple[str, list[str]]:
    name, sep, command = value.partition("=")
    if not sep or not name.strip() or not command.strip():
        raise argparse.ArgumentTypeError("형식은 NAME=COMMAND (예: vp-pytest=\"python3 -m pytest -q\")")
    return name.strip(), shlex.split(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m workflow.connector", description="workflow 로컬 연결 프로그램")
    sub = parser.add_subparsers(dest="command", required=True)

    connect = sub.add_parser("connect", help="연결 코드를 교환해 토큰 파일을 만든다")
    connect.add_argument("--server", required=True, help="중앙 서버 URL (https://…)")
    connect.add_argument("--code", required=True, help="운영자 화면에서 발급한 1회용 연결 코드")

    register = sub.add_parser("register", help="폴더 + 도구를 로컬 등록하고 중앙에 보고한다")
    register.add_argument("--id", required=True, dest="local_registration_id")
    register.add_argument("--repo", required=True, type=Path)
    register.add_argument("--repository-id", required=True)
    register.add_argument("--tool", default="codex", choices=["codex"])
    register.add_argument(
        "--verify", action="append", default=[], type=_verify_arg, metavar="NAME=COMMAND",
        help="검증 프로필. 여러 번 지정 가능",
    )

    run = sub.add_parser("run", help="claim 루프를 돈다")
    run.add_argument("--adapter", default="echo", choices=sorted(ADAPTERS))
    run.add_argument("--claim-interval", type=float, default=5.0)
    run.add_argument("--heartbeat-interval", type=float, default=30.0)
    return parser


def _head_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _require_token(paths: ConnectorPaths):
    stored = read_token(paths)
    if stored is None:
        print(f"토큰 파일이 없습니다 ({paths.token_file}). 먼저 `connect --server … --code …` 를 실행하세요.",
              file=sys.stderr)
    return stored


def _connect(args, paths: ConnectorPaths, transport) -> int:
    client = CentralClient(args.server, None, transport=transport)
    try:
        connector_id, token = client.exchange(args.code)
    except (CentralError, Unreachable) as exc:
        print(f"연결 실패: {exc}", file=sys.stderr)
        return 1
    write_token(paths, connector_id, token, args.server)
    print(f"연결됨: connector_id={connector_id} (토큰은 {paths.token_file} 에 0600 으로 저장)")
    return 0


def _register(args, paths: ConnectorPaths, transport) -> int:
    stored = _require_token(paths)
    if stored is None:
        return 2
    repo = args.repo.resolve()
    try:
        base_commit = _head_sha(repo)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"저장소 HEAD 를 읽지 못했습니다 ({repo}): {exc}", file=sys.stderr)
        return 1
    profiles = dict(args.verify)
    registration = {
        "local_registration_id": args.local_registration_id,
        "repo_path": str(repo),
        "tool": args.tool,
        "repository_id": args.repository_id,
        "base_commit": base_commit,
        "verification_profiles": profiles,
    }
    conn = state.connect(paths.state_db)
    try:
        state.init_schema(conn)
        state.save_registration(conn, registration)
    finally:
        conn.close()
    client = CentralClient(stored.server, stored.token, transport=transport)
    try:
        reply = client.report_registration(stored.connector_id, {
            "local_registration_id": args.local_registration_id,
            "tool": args.tool,
            "repository_id": args.repository_id,
            "base_commit": base_commit,
            "verification_profile_ids": list(profiles),
            "discovered": discover(repo),
        })
    except (CentralError, Unreachable) as exc:
        print(f"로컬 등록은 저장했지만 중앙 보고에 실패했습니다: {exc}", file=sys.stderr)
        return 1
    print(
        f"등록됨: {args.local_registration_id} → agent {reply.get('agent_id')} "
        f"(base_commit {base_commit[:12]}, 검증 프로필 {', '.join(profiles) or '없음'})"
    )
    return 0


def _run(args, paths: ConnectorPaths, transport, env: Mapping[str, str]) -> int:
    stored = _require_token(paths)
    if stored is None:
        return 2
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(paths.log_dir / "connector.log")],
    )
    conn = state.connect(paths.state_db)
    state.init_schema(conn)
    runner = Runner(
        client=CentralClient(stored.server, stored.token, transport=transport),
        state_conn=conn,
        paths=paths,
        adapter=ADAPTERS[args.adapter](),
        connector_id=stored.connector_id,
        clock=utc_now,
        handoff_root=paths.home / "handoff",
    )
    logging.getLogger(__name__).info(
        "연결 프로그램 시작: connector_id=%s server=%s adapter=%s", stored.connector_id, stored.server, args.adapter
    )
    runner.run_forever(args.claim_interval, args.heartbeat_interval)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    env = os.environ if env is None else env
    args = build_parser().parse_args(argv)
    paths = connector_paths(env)
    paths.home.mkdir(parents=True, exist_ok=True)
    if args.command == "connect":
        return _connect(args, paths, transport)
    if args.command == "register":
        return _register(args, paths, transport)
    return _run(args, paths, transport, env)
