"""`runloom` 명령 — argparse·명령·고르기·출력·exit code (ARCHITECTURE "CLI — phase 26" main.py·명령 표).

exit code: 0 성공, 1 서버 오류 응답·연결 실패, 2 사용법·설정 오류. 대화형 프롬프트는 없다(`login` 의 표준 입력 제외).
서버 응답의 문자열은 출력만 하고 셸·git·파일 경로로 쓰지 않는다.
"""

import argparse
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, TextIO

import httpx

from workflow.cli import config, render
from workflow.cli.client import ApiClient, ApiError, Unreachable

_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


class _Exit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _Parser(argparse.ArgumentParser):
    """도움말은 주입된 stdout, 사용법 오류는 주입된 stderr 로 쓰고, 끝내지 않고 status 를 넘긴다."""

    def __init__(self, *args, streams: tuple[TextIO, TextIO], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._streams = streams

    def _print_message(self, message: str, file=None) -> None:
        if message:
            out, err = self._streams
            (err if file is None or file is sys.stderr else out).write(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr)
        raise _Exit(status)

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


class _NoOpenRequest(Exception):
    def __init__(self, request_id: str) -> None:
        super().__init__(request_id)
        self.request_id = request_id


@dataclass
class _Context:
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    http: httpx.Client | None

    def client(self) -> ApiClient:
        creds = config.load(self.env)
        return ApiClient(creds.server, creds.token, http=self.http)

    def emit(self, args: argparse.Namespace, body: Any, text: Callable[[], str]) -> int:
        self.stdout.write((json.dumps(body, ensure_ascii=False, indent=2) if args.json else text()) + "\n")
        return 0


# --- 고르기 ---------------------------------------------------------------------------------------------


def pick_next(items: list[dict]) -> tuple[dict | None, int]:
    """내 차례 목록에서 (우선순위 high→low, `updated_at` 오래된 것) 첫 항목과 나머지 건수 — 같으면 API 순서(D14)."""
    if not items:
        return None, 0
    ordered = sorted(items, key=lambda i: (_PRIORITY_RANK.get(i["priority"], len(_PRIORITY_RANK)), i["updated_at"]))
    return ordered[0], len(items) - 1


def pick_reports(reports: dict, *, member_id: str) -> tuple[dict | None, dict | None]:
    """(펼칠 원본, 내 맞춤 풀이) — 최신 원본이 가장 늦게 만들어진 단계, 그 단계 원본 기준의 내 최근 맞춤 풀이(D16·D17)."""
    stage = None
    for candidate in reports["stages"]:
        if candidate["latest"] is not None and (
                stage is None or candidate["latest"]["created_at"] >= stage["latest"]["created_at"]):
            stage = candidate
    if stage is None:
        return None, None
    bases = {stage["latest"]["report_id"], *(view["report_id"] for view in stage["previous"])}
    mine = [view for view in reports["tailored"]
            if view["requested_by_member_id"] == member_id and view["base_report_id"] in bases]
    tailored = None
    for view in mine:
        if tailored is None or view["created_at"] >= tailored["created_at"]:
            tailored = view
    return stage["latest"], tailored


# --- 명령 -----------------------------------------------------------------------------------------------


def _login(args: argparse.Namespace, ctx: _Context) -> int:
    server = config.normalize_server(args.server)
    token = ctx.stdin.readline().strip()
    if not token:
        raise config.ConfigError("토큰을 표준 입력으로 주세요 (예: pbpaste | runloom login --server URL).")
    me = ApiClient(server, token, http=ctx.http).get("/me")
    config.save(ctx.env, config.Credentials(server, token))
    return ctx.emit(args, me, lambda: f"{me['display_name']}({me['role']}, 토큰 {me['token_scope']})로 로그인했습니다.")


def _logout(args: argparse.Namespace, ctx: _Context) -> int:
    path = config.credentials_path(ctx.env)
    deleted = config.delete(ctx.env)
    first = f"로그인 정보를 지웠습니다: {path}" if deleted else "지울 로그인 정보가 없습니다."
    return ctx.emit(args, {"deleted": deleted}, lambda: f"{first}\n서버의 토큰은 웹 /me 에서 폐기하세요.")


def _list(args: argparse.Namespace, ctx: _Context) -> int:
    params = {"filter": args.filter}
    if args.repository is not None:
        params["repository"] = args.repository
    if args.closed is not None:
        params["closed"] = args.closed
    body = ctx.client().get("/work-items", params=params)
    return ctx.emit(args, body, lambda: render.work_list(body))


def _gather(client: ApiClient, key: str) -> dict:
    """`show`·`next` 의 합친 객체(D23) — `/me` 응답은 넣지 않는다."""
    detail = client.get(f"/work-items/{key}")
    reports = client.get(f"/work-items/{key}/reports")
    report, tailored = pick_reports(reports, member_id=client.get("/me")["member_id"])
    return {"detail": detail, "reports": reports, "report": report, "tailored": tailored}


def _show_text(shown: dict) -> str:
    return render.show(shown["detail"], shown["reports"], shown["report"], shown["tailored"])


def _show(args: argparse.Namespace, ctx: _Context) -> int:
    shown = _gather(ctx.client(), args.key)
    return ctx.emit(args, shown, lambda: _show_text(shown))


def _next(args: argparse.Namespace, ctx: _Context) -> int:
    client = ctx.client()
    picked, more = pick_next(client.get("/work-items", params={"filter": "my_turn"})["items"])
    if picked is None:
        empty = {"detail": None, "reports": None, "report": None, "tailored": None, "more_my_turn": 0}
        return ctx.emit(args, empty, lambda: "내 차례 업무가 없습니다.")
    shown = {**_gather(client, picked["key"]), "more_my_turn": more}

    def text() -> str:
        rest = f"\n\n내 차례 {more}건 더 — runloom list --filter my_turn" if more else ""
        return _show_text(shown) + rest

    return ctx.emit(args, shown, text)


def _report(args: argparse.Namespace, ctx: _Context) -> int:
    view = ctx.client().get(f"/reports/{args.report_id}")
    return ctx.emit(args, view, lambda: render.report(view, old_base=bool(view["stale"])))


def _decide(args: argparse.Namespace, ctx: _Context) -> int:
    body = {"answer": "confirmed"} if args.confirm else {"answer": "changed", "note": args.change}
    view = ctx.client().put(f"/reports/{args.report_id}/decisions/{args.item_id}", json=body)
    return ctx.emit(args, view, lambda: "답을 저장했습니다.\n\n" + render.report(view, old_base=bool(view["stale"])))


def _respond(args: argparse.Namespace, ctx: _Context) -> int:
    """`expected_revision` 은 열린 요청 목록의 서버 값(D20). `response_id` 는 명령 1회마다 새 값 — 재시도 없음(D24)."""
    client = ctx.client()
    request = next((r for r in client.get("/human-requests")["requests"] if r["request_id"] == args.request_id), None)
    if request is None:
        raise _NoOpenRequest(args.request_id)
    body = {"response_id": uuid.uuid4().hex, "expected_revision": request["revision"], "action": args.action,
            "text": args.text}
    if args.agent is not None:
        body["agent_id"] = args.agent
    saved = client.post(f"/human-requests/{args.request_id}/responses", json=body)

    def text() -> str:
        if saved["created"]:
            return f"응답을 저장했습니다: {args.request_id} {args.action}"
        return f"같은 응답이 이미 저장되어 있습니다: {args.request_id}"

    return ctx.emit(args, saved, text)


def _start(args: argparse.Namespace, ctx: _Context) -> int:
    """서버가 정한 브랜치 이름을 보이기만 한다 — 로컬 git 은 실행하지 않는다(D21)."""
    detail = ctx.client().put(f"/work-items/{args.key}/direct-work")
    return ctx.emit(args, detail, lambda: f"직접 작업을 시작했습니다.\n브랜치: {detail['direct']['branch']}")


def _stop(args: argparse.Namespace, ctx: _Context) -> int:
    detail = ctx.client().delete(f"/work-items/{args.key}/direct-work")
    return ctx.emit(args, detail, lambda: "직접 작업을 그만두었습니다.")


def _build_parser(stdout: TextIO, stderr: TextIO) -> _Parser:
    streams = (stdout, stderr)
    common = _Parser(add_help=False, streams=streams)
    common.add_argument("--json", action="store_true", help="API 응답을 JSON 으로 낸다")
    parser = _Parser(prog="runloom", streams=streams,
                     description="Runloom 사람용 CLI — 내 차례 업무와 인계 리포트를 본다. 멤버 토큰(wfm_…)으로 /api/v1 을 부른다.")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Callable[[argparse.Namespace, _Context], int], help: str) -> _Parser:
        sub = commands.add_parser(name, parents=[common], help=help, description=help, streams=streams)
        sub.set_defaults(handler=handler)
        return sub

    login = add("login", _login, "표준 입력 첫 줄의 토큰을 서버에서 확인하고 자격 파일에 저장한다")
    login.add_argument("--server", required=True, help="서버 주소 (https://…, http 는 localhost 만)")
    add("logout", _logout, "자격 파일을 지운다 (서버의 토큰은 웹 /me 에서 폐기)")
    listing = add("list", _list, "업무 목록 — 한 줄에 키·우선순위·상태·제목·담당")
    listing.add_argument("--filter", default="all", help="all · my_turn · unassigned · agent_working (기본 all)")
    listing.add_argument("--repository", help="OWNER/NAME 저장소만")
    listing.add_argument("--closed", help="끝난 업무 범위 (서버 기본 recent, all)")
    add("next", _next, "내 차례 업무 하나(우선순위 높은 것 → 오래 기다린 것)와 그 인계 리포트")
    show = add("show", _show, "업무 상세와 최신 인계 리포트(와 내 맞춤 풀이)")
    show.add_argument("key", metavar="KEY", help="업무 키 (예: RUN-12)")
    report = add("report", _report, "인계 리포트 한 건")
    report.add_argument("report_id", metavar="REPORT_ID", help="리포트 id (예: hrp-0a1b2c3d4e5f)")
    decide = add("decide", _decide, "리포트의 결정·확인 항목에 답한다 (받는 사람·관리자)")
    decide.add_argument("report_id", metavar="REPORT_ID", help="리포트 id")
    decide.add_argument("item_id", metavar="ITEM_ID", help="항목 id (예: q1)")
    answer = decide.add_mutually_exclusive_group(required=True)
    answer.add_argument("--confirm", action="store_true", help="그대로 확인한다")
    answer.add_argument("--change", metavar="TEXT", help="다르게 정한 내용 (1~500자)")
    respond = add("respond", _respond, "열린 사람 요청에 응답한다")
    respond.add_argument("request_id", metavar="REQUEST_ID", help="사람 요청 id")
    respond.add_argument("action", metavar="ACTION", help="행동 (resume · close · retry · choose_agent … — 서버가 검사)")
    respond.add_argument("--text", default="", help="응답 글")
    respond.add_argument("--agent", help="choose_agent 로 지정할 에이전트 id")
    start = add("start", _start, "직접 작업을 시작하고 브랜치 이름을 보인다 (로컬 git 은 실행하지 않음)")
    start.add_argument("key", metavar="KEY", help="업무 키 (예: RUN-12)")
    stop = add("stop", _stop, "직접 작업을 그만둔다")
    stop.add_argument("key", metavar="KEY", help="업무 키 (예: RUN-12)")
    return parser


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, stdin: TextIO | None = None,
         stdout: TextIO | None = None, stderr: TextIO | None = None, http: httpx.Client | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = _build_parser(stdout, stderr).parse_args(argv)
    except _Exit as done:
        return done.status
    try:
        return args.handler(args, _Context(env, stdin, stdout, http))
    except config.ConfigError as error:
        stderr.write(f"{error}\n")
        return 2
    except ApiError as error:
        stderr.write(f"오류 {error.status} {error.code}: {error.message}\n")
        if error.status == 401:
            stderr.write("runloom login 으로 다시 로그인하세요.\n")
        return 1
    except _NoOpenRequest as error:
        stderr.write(f"열린 사람 요청 {error.request_id} 가 없습니다.\n")
        return 1
    except Unreachable as error:
        stderr.write(f"서버에 연결할 수 없습니다({error.reason}): {error.server}\n")
        return 1


def main_entry() -> NoReturn:
    sys.exit(main())
