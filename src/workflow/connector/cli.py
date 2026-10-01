"""`python3 -m workflow.connector` — 운영자 Mac 의 연결 프로그램 CLI.

    connect   --server URL --code CONNECT_CODE       연결 코드 교환 → 토큰 파일(0600)
    register  --repo PATH [--id ID] [--repository-id RID] [--tool codex|claude] [--verify NAME=CMD ...]
              [--link PATH ...] [--env NAME=VALUE ...]
                                                     로컬 등록 저장 + discovery 결과를 중앙에 보고
                                                     예: --verify "vp-pytest=python3 -m pytest -q"
                                                         --verify "vp-lint=python3 -m ruff check ."
                                                     --id 기본 = 폴더 이름, --repository-id 기본 = GitHub owner/name
                                                     (없으면 폴더 이름). --link·--env 는 로컬에만 둔다 (ADR-0018)
    setup     --server URL [--code CODE] --repo PATH [register 인자 ...]
                                                     connect + register 를 한 번에. 같은 서버의 토큰이 있으면
                                                     connect 를 건너뛴다. --tool 기본 = PATH 의 claude, 없으면 codex
    run       [--adapter auto|codex|claude|echo]     claim 루프. 기본 auto: codex·claude 어댑터를 둘 다 만들고
              [--keep-workdirs]                      실행마다 등록의 tool 로 고른다 (runner.select_adapter).
                                                     --keep-workdirs 는 결과 업로드 뒤에도 worktree·인계 디렉터리를
                                                     남긴다 (디버깅용, 기본은 정리)
    run-local --request FILE --handoff-dir DIR --out DIR [--adapter auto|codex|claude|echo]
                                                     중앙 없이 어댑터 한 번 실행, 산출물을 --out 에 파일로 (Step 15 실연동 확인용)

검증 명령은 `--verify` 인자로만 받아 `shlex.split` 한 인자 배열을 로컬에 둔다. 서버·요청·근거에서 명령을 받지 않는다.
"""

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import httpx
from pydantic import ValidationError

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, EchoAdapter, ExecutionAdapter
from workflow.connector.claude import ClaudeAdapter
from workflow.connector.client import CentralClient, CentralError, Unreachable
from workflow.connector.codex import CodexAdapter
from workflow.connector.config import ConnectorPaths, connector_paths, read_token, write_token
from workflow.connector.discovery import discover
from workflow.connector.git_ops import GitError
from workflow.connector.masking import ENV_NAME, RESERVED_ENV_NAMES
from workflow.connector.runner import AdapterNotSelected, Runner, select_adapter, utc_now
from workflow.contracts.v1 import ExecutionRequest

# 이름 → (state_conn, env) 로 어댑터를 만드는 factory. codex·claude 는 로컬 등록(검증 프로필·저장소 경로)을 읽는다.
# 실행마다 어느 것을 쓸지는 등록의 `tool` 값으로 고른다 (`runner.select_adapter`)
ADAPTERS = {
    "codex": lambda conn, env: CodexAdapter(conn, env_base=env),
    "claude": lambda conn, env: ClaudeAdapter(conn, env_base=env),
    "echo": lambda conn, env: EchoAdapter(),
}
AUTO_ADAPTERS = ("codex", "claude")  # `--adapter auto` 가 만드는 것. 실제 도구 둘 — echo 는 명시할 때만

# `--env` 로 받지 않는 이름 (AGENTS.md 비밀값 환경변수 + 러너가 정하는 것). 접두사 `WORKFLOW_` 도 거부


def _build_adapters(name: str, conn, env: Mapping[str, str]) -> dict[str, ExecutionAdapter]:
    names = AUTO_ADAPTERS if name == "auto" else (name,)
    return {n: ADAPTERS[n](conn, env) for n in names}


def _verify_arg(value: str) -> tuple[str, list[str]]:
    name, sep, command = value.partition("=")
    if not sep or not name.strip() or not command.strip():
        raise argparse.ArgumentTypeError("형식은 NAME=COMMAND (예: vp-pytest=\"python3 -m pytest -q\")")
    return name.strip(), shlex.split(command)


def _link_arg(value: str) -> str:
    """등록 폴더 기준 상대 경로만. 절대 경로·`..`·`.git` 구성 요소는 거부하고 정규화한 문자열을 돌려준다."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts or str(path) in ("", "."):
        raise argparse.ArgumentTypeError("등록 폴더 기준 상대 경로여야 합니다 (절대 경로·'..'·'.git' 불가)")
    return str(path)


def _env_arg(value: str) -> tuple[str, str]:
    """NAME=VALUE. 오류 문구에 값을 넣지 않는다 — 비밀일 수 있다."""
    name, sep, env_value = value.partition("=")
    if not sep or not ENV_NAME.match(name):
        raise argparse.ArgumentTypeError("형식은 NAME=VALUE, 이름은 영문자·숫자·밑줄 (숫자로 시작 불가)")
    if name in RESERVED_ENV_NAMES or name.startswith("WORKFLOW_"):
        raise argparse.ArgumentTypeError(f"{name} 은 --env 로 넘길 수 없습니다 (비밀값·러너 예약 이름)")
    return name, env_value


def default_registration_id(repo: Path) -> str:
    """폴더 이름을 소문자로, 파일 이름 안전 문자(`a-z0-9._-`) 밖은 `-` 로."""
    return re.sub(r"[^a-z0-9._-]", "-", repo.name.lower()).strip("-.") or "repo"


def default_repository_id(repo: Path, discovered: dict) -> str:
    """discovery 가 찾은 GitHub `owner/name`, 없으면 폴더 이름."""
    return discovered.get("found", {}).get("github_repository") or repo.name


def _default_tool(env: Mapping[str, str]) -> str:
    """setup 의 `--tool` 기본 — PATH 에 있는 claude 우선, 그다음 codex. 둘 다 없으면 claude (ADR-0018)."""
    for tool in ("claude", "codex"):
        if shutil.which(tool, path=env.get("PATH")):
            return tool
    return "claude"


def _add_registration_args(parser: argparse.ArgumentParser, *, tool_default: str | None) -> None:
    parser.add_argument("--id", dest="local_registration_id", help="로컬 등록 이름. 기본 = 폴더 이름")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--repository-id", help="기본 = GitHub owner/name, 없으면 폴더 이름")
    parser.add_argument("--tool", default=tool_default, choices=["codex", "claude"])
    parser.add_argument(
        "--verify", action="append", default=[], type=_verify_arg, metavar="NAME=COMMAND",
        help="검증 프로필. 여러 번 지정 가능",
    )
    parser.add_argument(
        "--link", action="append", default=[], type=_link_arg, metavar="PATH",
        help="worktree 에 원본 폴더로 심볼릭 링크할 상대 경로 (예: backend/.venv). 여러 번 지정 가능",
    )
    parser.add_argument(
        "--copy", action="append", default=[], type=_link_arg, metavar="PATH",
        help="worktree 에 원본 폴더에서 복사할 상대 경로 — 링크를 거부하는 도구용 (예: frontend/node_modules). 여러 번 지정 가능",
    )
    parser.add_argument(
        "--env", action="append", default=[], type=_env_arg, metavar="NAME=VALUE",
        help="검증·도구 프로세스 환경에 더할 값. 러너 로컬에만 저장. 여러 번 지정 가능. "
             "상대 PYTHONPATH 는 실행 폴더 기준으로 풀린다 (예: PYTHONPATH=src)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m workflow.connector", description="workflow 로컬 연결 프로그램")
    sub = parser.add_subparsers(dest="command", required=True)

    connect = sub.add_parser("connect", help="연결 코드를 교환해 토큰 파일을 만든다")
    connect.add_argument("--server", required=True, help="중앙 서버 URL (https://…)")
    connect.add_argument("--code", required=True, help="운영자 화면에서 발급한 1회용 연결 코드")

    register = sub.add_parser("register", help="폴더 + 도구를 로컬 등록하고 중앙에 보고한다")
    _add_registration_args(register, tool_default="codex")

    setup = sub.add_parser("setup", help="connect + register 를 한 번에")
    setup.add_argument("--server", required=True, help="중앙 서버 URL (https://…)")
    setup.add_argument("--code", help="1회용 연결 코드. 같은 서버의 토큰이 이미 있으면 생략 가능")
    _add_registration_args(setup, tool_default=None)

    run = sub.add_parser("run", help="claim 루프를 돈다")
    run.add_argument("--adapter", default="auto", choices=["auto", *sorted(ADAPTERS)])
    run.add_argument("--claim-interval", type=float, default=5.0)
    run.add_argument("--heartbeat-interval", type=float, default=30.0)
    run.add_argument(
        "--keep-workdirs", action="store_true",
        help="결과 업로드 뒤에도 worktree·인계 디렉터리를 남긴다 (디버깅용). 기본은 정리",
    )

    local = sub.add_parser("run-local", help="중앙 없이 어댑터를 한 번 실행하고 산출물을 파일로 남긴다")
    local.add_argument("--request", required=True, type=Path, help="ExecutionRequest JSON 파일")
    local.add_argument("--handoff-dir", required=True, type=Path, help="인계 자료 디렉터리 (읽기 전용)")
    local.add_argument("--out", required=True, type=Path, help="산출물을 쓸 디렉터리")
    local.add_argument("--adapter", default="auto", choices=["auto", *sorted(ADAPTERS)])
    return parser


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
        base_commit = git_ops.head_sha(repo)
    except GitError as exc:
        print(f"저장소 HEAD 를 읽지 못했습니다 ({repo}): {exc}", file=sys.stderr)
        return 1
    discovered = discover(repo)
    local_registration_id = args.local_registration_id or default_registration_id(repo)
    repository_id = args.repository_id or default_repository_id(repo, discovered)
    profiles = dict(args.verify)
    links = list(dict.fromkeys(args.link))
    copies = list(dict.fromkeys(args.copy))
    env = dict(args.env)
    registration = {
        "local_registration_id": local_registration_id,
        "repo_path": str(repo),
        "tool": args.tool,
        "repository_id": repository_id,
        "base_commit": base_commit,
        "verification_profiles": profiles,
        "links": links,
        "copies": copies,
        "env": env,
    }
    conn = state.connect(paths.state_db)
    try:
        state.init_schema(conn)
        state.save_registration(conn, registration)
    finally:
        conn.close()
    client = CentralClient(stored.server, stored.token, transport=transport)
    try:
        reply = client.report_registration(stored.connector_id, {  # links·copies·env 는 이름도 보내지 않는다
            "local_registration_id": local_registration_id,
            "tool": args.tool,
            "repository_id": repository_id,
            "base_commit": base_commit,
            "verification_profile_ids": list(profiles),
            "discovered": discovered,
            "agent_name": repo.name[:100],
        })
    except (CentralError, Unreachable) as exc:
        print(f"로컬 등록은 저장했지만 중앙 보고에 실패했습니다: {exc}", file=sys.stderr)
        return 1
    github = discovered.get("found", {}).get("github_repository") or "없음"
    print(
        f"등록됨: {local_registration_id} → agent {reply.get('agent_id')} · GitHub {github} "
        f"· 도구 {args.tool} · base_commit {base_commit[:12]} · 검증 프로필 {', '.join(profiles) or '없음'} "
        f"· 링크 {len(links)}개 · 복사 {len(copies)}개 · 환경변수 {', '.join(env) or '없음'} "
        "· 다음: `python3 -m workflow.connector run` 또는 deploy/selfhost/install-runner.sh"
    )
    return 0


def _setup(args, paths: ConnectorPaths, transport, env: Mapping[str, str]) -> int:
    """같은 서버의 토큰이 있으면 connect 를 건너뛴다 — 코드는 1회용이고, 새 연결은 옛 등록과 409 로 부딪힌다."""
    stored = read_token(paths)
    if stored is not None and stored.server.rstrip("/") == args.server.rstrip("/"):
        print(f"저장된 토큰을 씁니다: connector_id={stored.connector_id} (connect 건너뜀)")
    elif not args.code:
        print(f"{args.server} 의 토큰이 없습니다. 운영자 화면에서 발급한 --code 를 함께 주세요.", file=sys.stderr)
        return 2
    else:
        code = _connect(args, paths, transport)
        if code != 0:
            return code
    if args.tool is None:
        args.tool = _default_tool(env)
    return _register(args, paths, transport)


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
    adapters = _build_adapters(args.adapter, conn, env)
    runner = Runner(
        client=CentralClient(stored.server, stored.token, transport=transport),
        state_conn=conn,
        paths=paths,
        adapters=adapters,
        connector_id=stored.connector_id,
        clock=utc_now,
        handoff_root=paths.home / "handoff",
        keep_workdirs=args.keep_workdirs,
    )
    logging.getLogger(__name__).info(
        "연결 프로그램 시작: connector_id=%s server=%s adapter=%s",
        stored.connector_id, stored.server, ",".join(adapters),
    )
    runner.run_forever(args.claim_interval, args.heartbeat_interval)
    return 0


def _run_local(args, paths: ConnectorPaths, env: Mapping[str, str]) -> int:
    """어댑터 한 번. 이벤트·업로드 없이 산출물을 `--out/{kind}.{ext}` 로 쓴다. 실패면 `failed.json` 과 exit 1."""
    try:
        request = ExecutionRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"요청 파일을 읽지 못했습니다 ({args.request}): {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = state.connect(paths.state_db)
    state.init_schema(conn)
    try:
        adapters = _build_adapters(args.adapter, conn, env)

        def progress(message: str, *, runtime_ref: str | None = None) -> None:
            print(f"[{request.execution_id}] {message}" + (f" ({runtime_ref})" if runtime_ref else ""),
                  file=sys.stderr)

        try:
            output = select_adapter(conn, adapters, request).run(request, args.handoff_dir, progress)
        except AdapterNotSelected as exc:
            output = AdapterOutput(result=None, failed=(exc.code, exc.message, True))
    finally:
        conn.close()
    args.out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for meta, data in output.artifacts:
        name = f"{meta.kind}{Path(meta.name).suffix or '.bin'}"
        (args.out / name).write_bytes(data)
        written.append(name)
    if output.failed is not None:
        code, message, stopped = output.failed
        (args.out / "failed.json").write_text(
            json.dumps({"code": code, "message": message, "process_stopped": stopped}, ensure_ascii=False, indent=2)
        )
        print(f"실패: {code} — {message} (process_stopped={stopped}). 산출물 {len(written)}개 → {args.out}",
              file=sys.stderr)
        return 1
    result = output.result.model_copy(update={"artifact_ids": written})
    if result.verification is not None:
        result = result.model_copy(update={
            "verification": result.verification.model_copy(update={"log_artifact_id": "verification_log.txt"}),
        })
    (args.out / "code_change_result.json").write_text(result.model_dump_json(indent=2))
    print(f"{result.outcome} · result_commit={result.result_commit} · 산출물 {len(written) + 1}개 → {args.out}")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    env = os.environ if env is None else env
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in ("register", "setup"):
        names = [name for name, _ in args.env]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            parser.error(f"--env 이름이 두 번 나왔습니다: {', '.join(duplicated)}")
    paths = connector_paths(env)
    paths.home.mkdir(parents=True, exist_ok=True)
    if args.command == "connect":
        return _connect(args, paths, transport)
    if args.command == "register":
        return _register(args, paths, transport)
    if args.command == "setup":
        return _setup(args, paths, transport, env)
    if args.command == "run-local":
        return _run_local(args, paths, env)
    return _run(args, paths, transport, env)
