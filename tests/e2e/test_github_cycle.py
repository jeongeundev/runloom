"""GitHub 업무 순환 e2e — 가짜 GitHub HTTP 서버·임시 Git 저장소·가짜 도구로 전체 순환과 장애 회귀를 본다 (phase 8 step 14).

이 모듈이 직접 띄우는 것:
- **가짜 GitHub** — 127.0.0.1 임시 포트의 `ThreadingHTTPServer`. 이슈 목록(since·페이지·ETag·PR 항목)·이슈·저장소·댓글
  생성/조회/수정만 흉내 내고 요청을 모두 기록한다. 장애(5xx·응답 유실)와 오래된 스냅샷을 한 번씩 끼워 넣을 수 있다.
- **중앙 API** — `python3 -m uvicorn workflow.server.app:app` 하위 프로세스 (운영자 로그인·Agent 등록·소스 설정·사람 응답).
- **연결 프로그램** — `python3 -m workflow.connector connect/register/run` 하위 프로세스. PATH 앞의 가짜 `codex` 가 임시
  Git 저장소 worktree 에 재현 테스트와 수정을 쓰고(커밋은 연결 프로그램), 검토는 결과 커밋 체크아웃의 파일을 읽어
  판정한다. 수정 전 실패·수정 후 통과·깨끗한 체크아웃 검증은 실제 `pytest` 하위 프로세스다.
- **중앙 워커** — 이 테스트 프로세스 안의 `Worker`. `HttpGitHubClient` 는 그대로(`api.github.com` 고정) 두고 transport 만
  가짜 GitHub 로 넘긴다 — 제품에 GitHub 주소를 바꾸는 설정을 두지 않기 위해서다. tick 을 테스트가 돌리므로 재시작
  (새 `Worker`)·장애 주입 시점을 정할 수 있다. 수집 간격은 0 으로 줄인다(매 tick 수집 → 304·경계 재수신을 매번 겪는다).

업무 (PRD phase 8 표): A billing#1(kim) · B shop#1(lee, 다른 저장소 등록) · C = A 의 검토로 미리 웹 등록한 code_review ·
D billing#2(park, Agent 능력 범위 밖) · E billing#3(kim+park 2명) · G shop#2(lee, B 와 같은 등록 → 저장소 잠금, 재작업 상한 0).
A 는 C 의 수정 요청 → 한 번 재작업 → 새 커밋 재검토 → 승인, B 는 새 검토 F 생성 → 승인.

`WORKFLOW_E2E=1` 일 때만 돈다. 실제 GitHub·모델 호출 없음 — 네트워크는 127.0.0.1 뿐이다.
테스트 함수는 번호 순으로 이어지며 앞 단계의 상태(`world`)를 쓴다.
"""

import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from workflow.adapters import repo
from workflow.adapters.artifact_store import ArtifactStore
from workflow.adapters.callback_client import HttpCallbackClient
from workflow.adapters.db import connect
from workflow.adapters.github_client import HttpGitHubClient
from workflow.contracts.v1 import CodeChangeResult, CodeReviewResult, ExecutionRequest
from workflow.server import worker as worker_module
from workflow.server.auth import utc_now
from workflow.server.settings import load_settings
from workflow.server.worker import Worker

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKFLOW_E2E") != "1", reason="WORKFLOW_E2E=1 일 때만"),
]

TOKEN = "ghp_e2eFakeToken" + "Z9" * 12  # 가짜 GitHub 만 받는 값. 어떤 산출물·로그·DB·도구 환경에도 나오면 안 된다
DROP_PREFIXES = ("WORKFLOW_", "DIAG_", "OPENAI_")
START_AT = "2026-09-01T00:00:00Z"
REPO_IDS = {"acme/billing": 5001, "acme/shop": 5002}
KIM, LEE, PARK = (101, "kim-dev"), (102, "lee-dev"), (103, "park-dev")
FIX_KIM, FIX_PARK, FIX_LEE = "agent-fix-kim", "agent-fix-park", "agent-fix-lee"
REVIEW_BILLING, REVIEW_SHOP = "agent-review-billing", "agent-review-shop"
DRIVE_TIMEOUT = 120.0


# --- 임시 Git 저장소 (수정·검토 대상) ---------------------------------------------------------------

_COMMON = {
    ".gitignore": "__pycache__/\n.pytest_cache/\n",
    "pytest.ini": "[pytest]\n",
    "conftest.py": "",
}
BILLING_FILES = {
    **_COMMON,
    "billing/__init__.py": "",
    "billing/coupon.py": (
        "def apply_coupons(total: int, coupons: list[int]) -> int:\n"
        "    for amount in coupons:\n"
        "        total -= amount\n"
        "        total -= amount\n"
        "    return total\n"
    ),
    "billing/invoice.py": 'def invoice_number(year: int, seq: int) -> str:\n    return f"INV-{year}-{seq}"\n',
    "billing/refund.py": "def refund_fee(amount: int) -> int:\n    return amount // 10\n",
    "tests/test_smoke.py": (
        "from billing.coupon import apply_coupons\n\n\n"
        "def test_no_coupons():\n    assert apply_coupons(1000, []) == 1000\n"
    ),
}
SHOP_FILES = {
    **_COMMON,
    "shop/__init__.py": "",
    "shop/rounding.py": "def order_total(prices: list[float]) -> int:\n    return int(sum(prices))\n",
    "shop/discount.py": "def member_discount(price: int) -> int:\n    return price - 100\n",
    "tests/test_smoke.py": (
        "from shop.rounding import order_total\n\n\n"
        "def test_whole_prices():\n    assert order_total([1.0, 2.0]) == 3\n"
    ),
}


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid", "commit", "-q", "-m", "base")
    return _git(path, "rev-parse", "HEAD")


# --- 가짜 codex — 수정은 시나리오 표대로 파일을 쓰고, 검토는 결과 커밋 체크아웃을 읽는다 -----------------------

_FAKE_CODEX = r'''#!/usr/bin/env python3
"""e2e 가짜 codex. 모델 없이: 수정(workspace-write)은 요청 속 `[scenario:이름]` 과 재작업 여부(프롬프트의
'# 이전 검토 지적' 절)로 재현 테스트·수정 파일을 -C worktree 에 쓴다(커밋하지 않는다). 검토(read-only)는 -C 체크아웃의
.py 파일에서 `TODO(review):` 를 찾아 있으면 changes_requested(차단 지적), 없으면 approved."""
import json
import os
import re
import sys

TOKEN = "__TOKEN__"
FIXES = __FIXES__

args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1]


cwd, last_message, sandbox = opt("-C"), opt("--output-last-message"), opt("--sandbox")
prompt = sys.stdin.read()
leaked = [k for k, v in os.environ.items() if TOKEN in v or k in ("WORKFLOW_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")]
if leaked:
    print(f"GitHub 토큰이 도구 환경에 있다: {leaked}", file=sys.stderr)
    sys.exit(3)
print(json.dumps({"type": "thread.started", "thread_id": "fake-cycle"}), flush=True)

if sandbox == "read-only":
    findings = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                for no, line in enumerate(f, 1):
                    if "TODO(review):" in line:
                        findings.append({"severity": "blocking", "path": os.path.relpath(path, cwd), "line": no,
                                         "message": line.split("TODO(review):", 1)[1].strip()})
    outcome = "changes_requested" if findings else "approved"
    with open(last_message, "w", encoding="utf-8") as f:
        json.dump({"outcome": outcome, "summary": f"결과 커밋 검토: 차단 지적 {len(findings)}건",
                   "findings": findings, "missing_information": []}, f, ensure_ascii=False)
    sys.exit(0)

scenario = re.search(r"\[scenario:([a-z]+)\]", prompt).group(1)
rework = "# 이전 검토 지적" in prompt
files = FIXES[f"{scenario}:{'rework' if rework else 'first'}"]
for rel, text in files.items():
    path = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
with open(last_message, "w", encoding="utf-8") as f:
    json.dump({"summary": f"{scenario} 재현 테스트 추가 후 수정{' (검토 지적 반영)' if rework else ''}",
               "outcome": "ready_for_review", "files_changed": sorted(files), "notes": ""}, f, ensure_ascii=False)
print(json.dumps({"type": "turn.completed"}), flush=True)
'''

FIXES = {
    # A — 첫 수정은 중복 차감만 고치고 음수 합계를 남긴다(검토가 지적) → 재작업이 바닥값과 그 재현 테스트를 더한다
    "coupon:first": {
        "billing/coupon.py": (
            "def apply_coupons(total: int, coupons: list[int]) -> int:\n"
            "    for amount in coupons:\n"
            "        total -= amount\n"
            "    return total  # TODO(review): 할인이 합계보다 크면 음수가 된다\n"
        ),
        "tests/test_coupon_once.py": (
            "from billing.coupon import apply_coupons\n\n\n"
            "def test_coupon_is_applied_once():\n    assert apply_coupons(1000, [100]) == 900\n"
        ),
    },
    "coupon:rework": {
        "billing/coupon.py": (
            "def apply_coupons(total: int, coupons: list[int]) -> int:\n"
            "    for amount in coupons:\n"
            "        total -= amount\n"
            "    return max(total, 0)\n"
        ),
        "tests/test_coupon_floor.py": (
            "from billing.coupon import apply_coupons\n\n\n"
            "def test_total_never_negative():\n    assert apply_coupons(100, [500]) == 0\n"
        ),
    },
    "invoice:first": {
        "billing/invoice.py": 'def invoice_number(year: int, seq: int) -> str:\n    return f"INV-{year}-{seq:05d}"\n',
        "tests/test_invoice_format.py": (
            "from billing.invoice import invoice_number\n\n\n"
            'def test_sequence_is_zero_padded():\n    assert invoice_number(2026, 42) == "INV-2026-00042"\n'
        ),
    },
    "refund:first": {
        "billing/refund.py": "def refund_fee(amount: int) -> int:\n    return amount * 3 // 100\n",
        "tests/test_refund_fee.py": (
            "from billing.refund import refund_fee\n\n\n"
            "def test_fee_is_three_percent():\n    assert refund_fee(10000) == 300\n"
        ),
    },
    "rounding:first": {
        "shop/rounding.py": "def order_total(prices: list[float]) -> int:\n    return int(round(sum(prices)))\n",
        "tests/test_rounding.py": (
            "from shop.rounding import order_total\n\n\n"
            "def test_total_is_rounded():\n    assert order_total([999.6]) == 1000\n"
        ),
    },
    # G — 검토가 늘 지적을 남기는 수정. 재작업 상한 0 이라 바로 사람 요청이 된다
    "stubborn:first": {
        "shop/discount.py": (
            "def member_discount(price: int) -> int:\n"
            "    return max(price - 100, 0)  # TODO(review): 등급별 할인율이 빠졌다\n"
        ),
        "tests/test_discount_floor.py": (
            "from shop.discount import member_discount\n\n\n"
            "def test_discount_never_negative():\n    assert member_discount(50) == 0\n"
        ),
    },
}


def install_fake_codex(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True)
    script = bin_dir / "codex"
    text = _FAKE_CODEX.replace("__TOKEN__", TOKEN).replace("__FIXES__", repr(FIXES))
    script.write_text(f"#!{sys.executable}\n" + text.split("\n", 1)[1])
    script.chmod(0o755)
    return bin_dir


# --- 가짜 GitHub HTTP 서버 ---------------------------------------------------------------------


def gh_issue(repo_name: str, number: int, title: str, *, body: str = "", labels=("bug",), assignees=(),
             state: str = "open", created_at: str = "2026-09-10T00:00:00Z", updated_at: str = "2026-09-10T00:00:00Z",
             pull: bool = False) -> dict:
    item = {
        "id": REPO_IDS[repo_name] * 1000 + number, "number": number, "title": title, "body": body, "state": state,
        "labels": [{"name": name} for name in labels],
        "assignees": [{"id": user_id, "login": login} for user_id, login in assignees],
        "html_url": f"https://github.com/{repo_name}/issues/{number}",
        "created_at": created_at, "updated_at": updated_at,
    }
    if pull:
        item["pull_request"] = {"url": f"https://api.github.com/repos/{repo_name}/pulls/{number}"}
    return item


# G — 첫 수집 뒤에 새로 올라오는 이슈(증분 수집). B 와 같은 담당·등록이라 B 가 쓰는 동안 저장소 잠금으로 기다린다
G_ISSUE = gh_issue("acme/shop", 2, "회원 할인 음수", assignees=[LEE], updated_at="2026-09-10T07:00:00Z",
                   body="50원 상품이 -50원이 됩니다. [scenario:stubborn]")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class FakeGitHub:
    """GitHub REST 의 이 제품이 쓰는 부분만. 목록은 `page_size` 로 잘라 Link rel=next 를 준다(클라이언트 per_page 무시)."""

    token: str
    page_size: int = 2
    issues: dict = field(default_factory=lambda: {name: {} for name in REPO_IDS})
    comments: dict = field(default_factory=dict)  # comment_id → {"repo", "number", "body", "updated_at"}
    requests: list = field(default_factory=list)  # (method, path, authorized)
    faults: list = field(default_factory=list)  # [method, path 정규식, 동작] — 한 번 쓰고 지운다
    stale: dict = field(default_factory=dict)  # repo → 다음 목록 응답에 덧붙일 오래된 스냅샷
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_comment_id: int = 9001

    def add(self, repo_name: str, item: dict) -> None:
        self.issues[repo_name][item["number"]] = item

    def fail_next(self, method: str, pattern: str, action: str) -> None:
        with self.lock:
            self.faults.append([method, re.compile(pattern), action])

    def serve_stale_once(self, repo_name: str, item: dict) -> None:
        with self.lock:
            self.stale.setdefault(repo_name, []).append(item)

    def issue_comments(self, repo_name: str, number: int) -> list[dict]:
        with self.lock:
            return [dict(c, id=i) for i, c in sorted(self.comments.items())
                    if (c["repo"], c["number"]) == (repo_name, number)]

    def writes(self) -> list[tuple[str, str]]:
        with self.lock:
            return [(m, p) for m, p, _ in self.requests if m != "GET"]

    def _fault(self, method: str, path: str) -> str | None:
        for fault in self.faults:
            if fault[0] == method and fault[1].search(path):
                self.faults.remove(fault)
                return fault[2]
        return None

    def handle(self, method: str, path: str, query: dict, headers, body: bytes):
        """→ (status, headers, payload) 또는 None(응답 없이 연결을 끊는다)."""
        authorized = headers.get("Authorization") == f"Bearer {self.token}"
        with self.lock:
            self.requests.append((method, path, authorized))
            if not authorized:
                return 401, {}, {"message": "Bad credentials"}
            fault = self._fault(method, path)
            if fault == "500":
                return 500, {}, {"message": "Server Error"}
            match = re.fullmatch(r"/repos/([^/]+/[^/]+)(/.*)?", path)
            if match is None or match.group(1) not in REPO_IDS:
                return 404, {}, {"message": "Not Found"}
            repo_name, rest = match.group(1), match.group(2) or ""
            if method == "GET" and rest == "":
                return 200, {}, {"id": REPO_IDS[repo_name], "full_name": repo_name}
            if method == "GET" and rest == "/issues":
                return self._list(repo_name, query, headers)
            if (m := re.fullmatch(r"/issues/(\d+)", rest)) and method == "GET":
                item = self.issues[repo_name].get(int(m.group(1)))
                return (200, {}, item) if item else (404, {}, {"message": "Not Found"})
            if (m := re.fullmatch(r"/issues/(\d+)/comments", rest)) and method == "GET":
                number = int(m.group(1))
                items = [{"id": i, "body": c["body"], "user": {"id": 1, "login": "runloom-bot"},
                          "updated_at": c["updated_at"]}
                         for i, c in sorted(self.comments.items()) if (c["repo"], c["number"]) == (repo_name, number)]
                return 200, {}, items
            if (m := re.fullmatch(r"/issues/(\d+)/comments", rest)) and method == "POST":
                comment_id = self.next_comment_id
                self.next_comment_id += 1
                self.comments[comment_id] = {"repo": repo_name, "number": int(m.group(1)),
                                             "body": json.loads(body)["body"], "updated_at": utc_now()}
                if fault == "drop":  # 만들고 나서 응답을 잃는다 — 클라이언트는 닿았는지 모른다
                    return None
                return 201, {}, {"id": comment_id}
            if (m := re.fullmatch(r"/issues/comments/(\d+)", rest)) and method == "PATCH":
                comment = self.comments.get(int(m.group(1)))
                if comment is None:
                    return 404, {}, {"message": "Not Found"}
                comment.update(body=json.loads(body)["body"], updated_at=utc_now())
                return 200, {}, {"id": int(m.group(1))}
            return 404, {}, {"message": "Not Found"}

    def _list(self, repo_name: str, query: dict, headers):
        since = query.get("since", [None])[0]
        page = int(query.get("page", ["1"])[0])
        items = sorted(self.issues[repo_name].values(), key=lambda i: (i["updated_at"], i["number"]))
        if since is not None:
            items = [i for i in items if _ts(i["updated_at"]) >= _ts(since)]
        chunk = items[(page - 1) * self.page_size: page * self.page_size]
        if page == 1:
            chunk = chunk + self.stale.pop(repo_name, [])
        payload = json.dumps(chunk, sort_keys=True).encode()
        etag = '"' + hashlib.sha1(payload).hexdigest() + '"'
        if headers.get("If-None-Match") == etag:
            return 304, {"ETag": etag}, None
        extra = {"ETag": etag}
        if page * self.page_size < len(items):
            extra["Link"] = f'<https://api.github.com/repos/{repo_name}/issues?page={page + 1}>; rel="next"'
        return 200, extra, chunk


def serve(fake: FakeGitHub) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _do(self):
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            reply = fake.handle(self.command, url.path, parse_qs(url.query), self.headers, body)
            if reply is None:
                self.close_connection = True
                return
            status, headers, payload = reply
            data = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = _do

        def log_message(self, *args):  # 테스트 출력 조용히
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ToFakeGitHub(httpx.BaseTransport):
    """`https://api.github.com/…` 요청을 로컬 가짜 서버로 보낸다. 제품 클라이언트의 host 고정은 그대로 둔다."""

    def __init__(self, port: int):
        self._port = port
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com", request.url
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self._port)
        return self._inner.handle_request(request)


# --- 세계 (프로세스·저장소·워커) --------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class World:
    workdir: Path
    central_url: str
    central_env: dict
    connector_env: dict
    fake: FakeGitHub
    fake_port: int
    billing: Path
    shop: Path
    base: dict  # repo 경로 이름 → 기준 커밋
    procs: dict = field(default_factory=dict)
    worker: Worker | None = None
    http: httpx.Client | None = None
    session_id: str = ""
    sources: dict = field(default_factory=dict)  # "acme/billing" → source_id
    tasks: dict = field(default_factory=dict)  # 업무 문자 → task_id
    ctx: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return Path(self.central_env["WORKFLOW_DB_PATH"])

    @property
    def store(self) -> ArtifactStore:
        return ArtifactStore(Path(self.central_env["WORKFLOW_ARTIFACT_DIR"]))

    def conn(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def make_worker(self) -> Worker:
        """워커 프로세스 한 번의 시작과 같다 — 메모리 상태 없이 DB 만 보고 이어 간다."""
        settings = load_settings(self.central_env)
        github = HttpGitHubClient(settings.github_token, settings.github_repos, transport=ToFakeGitHub(self.fake_port))
        return Worker(lambda: connect(settings.db_path), self.store, HttpCallbackClient(), settings, utc_now,
                      github=github)

    def spawn(self, name: str, argv: list[str], env: dict) -> None:
        log = (self.workdir / "logs" / f"{name}.log").open("ab")
        self.procs[name] = (subprocess.Popen(argv, env=env, cwd=self.workdir, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT), log)

    def once(self, name: str, argv: list[str]) -> str:
        done = subprocess.run(argv, env=self.connector_env, cwd=self.workdir, capture_output=True, text=True)
        (self.workdir / "logs" / f"{name}.log").write_text(done.stdout + done.stderr)
        assert done.returncode == 0, f"{name}: {done.stdout}{done.stderr}"
        return done.stdout

    def stop(self) -> None:
        for proc, log in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            log.close()

    def log_tails(self) -> str:
        tails = []
        for path in sorted((self.workdir / "logs").glob("*.log")):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            tails.append(f"--- {path.name}\n" + "\n".join(lines))
        return "\n".join(tails)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("github-cycle")
    (workdir / "logs").mkdir()
    billing, shop = workdir / "repos" / "billing", workdir / "repos" / "shop"
    base = {"billing": make_repo(billing, BILLING_FILES), "shop": make_repo(shop, SHOP_FILES)}

    fake = FakeGitHub(TOKEN)
    b, s = "acme/billing", "acme/shop"
    fake.add(b, gh_issue(b, 1, "쿠폰이 두 번 차감됩니다", assignees=[KIM], updated_at="2026-09-10T01:00:00Z",
                         body="쿠폰 1장이 두 번 빠집니다. [scenario:coupon]"))
    fake.add(b, gh_issue(b, 2, "청구서 번호 자릿수", assignees=[PARK], updated_at="2026-09-10T02:00:00Z",
                         body="번호가 5자리로 채워지지 않습니다. [scenario:invoice]"))
    fake.add(b, gh_issue(b, 3, "환불 수수료 계산 오류", assignees=[KIM, PARK], updated_at="2026-09-10T03:00:00Z",
                         body="수수료는 3% 입니다. [scenario:refund]"))
    # 시작 시각 전에 만든 백로그(최근 갱신) · PR 항목 · 라벨이 다른 이슈는 업무가 되지 않는다
    fake.add(b, gh_issue(b, 4, "오래된 백로그", assignees=[KIM], created_at="2026-08-01T00:00:00Z",
                         updated_at="2026-09-10T04:00:00Z", body="[scenario:coupon]"))
    fake.add(b, gh_issue(b, 5, "fix: 쿠폰 PR", assignees=[KIM], updated_at="2026-09-10T05:00:00Z", pull=True))
    fake.add(b, gh_issue(b, 6, "질문", labels=("question",), assignees=[KIM], updated_at="2026-09-10T06:00:00Z"))
    fake.add(s, gh_issue(s, 1, "주문 합계 반올림 오류", assignees=[LEE], updated_at="2026-09-10T01:00:00Z",
                         body="999.6원이 999원이 됩니다. [scenario:rounding]"))
    server = serve(fake)

    port = _free_port()
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    central_env = {
        **inherited,
        "WORKFLOW_DB_PATH": str(workdir / "central" / "db.sqlite"),
        "WORKFLOW_ARTIFACT_DIR": str(workdir / "central" / "artifacts"),
        "SESSION_SECRET": "e2e-session-secret-" + "s" * 20,
        "OPERATOR_TOKEN": "e2e-operator-token-" + "o" * 20,
        "WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "WORKFLOW_GITHUB_TOKEN": TOKEN,
        "WORKFLOW_GITHUB_REPOS": "acme/billing,acme/shop",
        "WORKFLOW_LIMIT_ACTIVE_TASKS_PER_SESSION": "20",
    }
    fake_bin = install_fake_codex(workdir / "bin")
    connector_env = {
        **inherited,
        "WORKFLOW_CONNECTOR_HOME": str(workdir / "connector"),
        "PATH": f"{fake_bin}{os.pathsep}{inherited.get('PATH', '')}",
    }
    world = World(workdir=workdir, central_url=f"http://127.0.0.1:{port}", central_env=central_env,
                  connector_env=connector_env, fake=fake, fake_port=server.server_address[1],
                  billing=billing, shop=shop, base=base)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker_module, "GITHUB_SYNC_INTERVAL_SECONDS", 0)
        try:
            world.spawn("central_api", [sys.executable, "-m", "uvicorn", "workflow.server.app:app",
                                        "--host", "127.0.0.1", "--port", str(port)], central_env)
            _wait_http(world, f"{world.central_url}/healthz")
            world.http = httpx.Client(base_url=world.central_url, follow_redirects=False, timeout=10.0)
            yield world
        finally:
            if world.http is not None:
                world.http.close()
            world.stop()
            server.shutdown()


def _wait_http(world: World, url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError(f"{url} 응답 없음\n{world.log_tails()}")


# --- 도우미 --------------------------------------------------------------------------------


def drive(world: World, until, what: str, timeout: float = DRIVE_TIMEOUT):
    """워커 tick 을 돌리며 `until()` 이 참 값이 될 때까지 기다린다. 연결 프로그램은 별도 프로세스로 claim 한다."""
    deadline = time.monotonic() + timeout
    while True:
        world.worker.tick()
        result = until()
        if result:
            return result
        assert time.monotonic() < deadline, f"{timeout}초 안에 {what} 에 도달하지 못함\n{world.log_tails()}"
        time.sleep(0.3)


def q(world: World, sql: str, *params) -> list[sqlite3.Row]:
    conn = world.conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def result_branch_of(world: World, task_id: str) -> str:
    """러너가 만든 결과 브랜치 — 업무 키 `runloom/RUN-<n>` (phase 14 step 7, 첫 수정 단계라 순번 없음)."""
    (row,) = q(world, "SELECT w.key_number FROM work_items w JOIN tasks t ON t.work_item_id = w.work_item_id"
                      " WHERE t.task_id = ?", task_id)
    return f"runloom/RUN-{row['key_number']}"


def task(world: World, task_id: str) -> sqlite3.Row:
    return q(world, "SELECT * FROM tasks WHERE task_id = ?", task_id)[0]


def execs(world: World, task_id: str) -> list[sqlite3.Row]:
    return q(world, "SELECT * FROM executions WHERE task_id = ? ORDER BY attempt_no, created_at", task_id)


def request_of(row) -> ExecutionRequest:
    return ExecutionRequest.model_validate_json(row["request_json"])


def verdict_checks(world: World, execution_id: str) -> dict[str, bool] | None:
    rows = q(world, "SELECT verdict_json FROM task_verdicts WHERE execution_id = ? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
             execution_id)
    if not rows:
        return None
    return {c["code"]: c["passed"] for c in json.loads(rows[0]["verdict_json"])["checks"]}


def artifact(world: World, execution_id: str, kind: str) -> bytes:
    conn = world.conn()
    try:
        (row,) = conn.execute("SELECT artifact_id FROM artifacts WHERE execution_id = ? AND kind = ?",
                              (execution_id, kind)).fetchall()
        return repo.read_artifact(conn, world.store, row["artifact_id"])
    finally:
        conn.close()


def fix_result(world: World, execution) -> CodeChangeResult:
    return CodeChangeResult.model_validate_json(artifact(world, execution["execution_id"], "code_change_result"))


def review_result(world: World, execution) -> CodeReviewResult:
    return CodeReviewResult.model_validate_json(artifact(world, execution["execution_id"], "code_review_result"))


def reviews_of(world: World, fix_task_id: str) -> list[sqlite3.Row]:
    return q(world, "SELECT * FROM tasks WHERE predecessor_task_id = ? AND kind = 'code_review' ORDER BY created_at",
             fix_task_id)


def open_requests(world: World, task_id: str) -> list[dict]:
    response = world.http.get("/human-requests")
    assert response.status_code == 200, response.text
    return [r for r in response.json()["requests"] if r["task_id"] == task_id]


def answer(world: World, request: dict, response_id: str, **body) -> httpx.Response:
    payload = {"response_id": response_id, "expected_revision": request["revision"], "action": "resume", "text": "",
               **body}
    return world.http.post(f"/human-requests/{request['request_id']}/responses", json=payload)


def settled(world: World, execution_id: str) -> bool:
    """연결 프로그램이 결과를 냈고 워커가 판정까지 했는가."""
    return verdict_checks(world, execution_id) is not None


def operator_agent(world: World, agent_id: str, code: str, scope: str, registration: str) -> None:
    response = world.http.post("/operator/agents", data={
        "agent_id": agent_id, "name": agent_id, "owner_scope": "personal", "connection_type": "local",
        "capability_code": code, "scope_key": "", "scope_value": scope, "local_registration_id": registration,
    })
    assert response.status_code == 303, response.text[:500]


# --- 시나리오 ------------------------------------------------------------------------------


REGISTRATIONS = (  # (agent, 능력, 저장소 ID, 로컬 등록, 폴더 이름, 검증 프로필 여부)
    (FIX_KIM, "code.fix", "billing", "local-billing-kim", "billing", True),
    (FIX_PARK, "code.fix", "billing", "local-billing-park", "billing", True),
    (REVIEW_BILLING, "code.review", "billing", "local-billing-review", "billing", False),
    (FIX_LEE, "code.fix", "shop", "local-shop-lee", "shop", True),
    (REVIEW_SHOP, "code.review", "shop", "local-shop-review", "shop", False),
)


def test_01_operator_registers_agents_and_connects_the_local_connector(world):
    http = world.http
    login = http.post("/login", data={"token": world.central_env["OPERATOR_TOKEN"]})  # 워크스페이스 = 운영자
    assert login.status_code == 303, login.text[:300]
    world.session_id = q(world, "SELECT session_id FROM sessions WHERE is_operator = 1")[0]["session_id"]

    for agent_id, code, scope, registration, _, _ in REGISTRATIONS:
        operator_agent(world, agent_id, code, scope, registration)
    issued = http.post("/operator/connect-codes")
    connect_code = re.search(r'<code id="issued-code">([^<]+)</code>', issued.text).group(1)

    py = sys.executable
    world.once("connector-connect", [py, "-m", "workflow.connector", "connect", "--server", world.central_url,
                                     "--code", connect_code])
    for agent_id, _, scope, registration, folder, verify in REGISTRATIONS:
        argv = [py, "-m", "workflow.connector", "register", "--id", registration, "--tool", "codex",
                "--repo", str(world.workdir / "repos" / folder), "--repository-id", scope]
        if verify:
            argv += ["--verify", f"vp-pytest={py} -m pytest -q -p no:cacheprovider"]
        assert f"agent {agent_id}" in world.once(f"connector-register-{registration}", argv)
    world.spawn("connector", [py, "-m", "workflow.connector", "run", "--adapter", "codex",
                              "--claim-interval", "0.5", "--heartbeat-interval", "1"], world.connector_env)

    # 첫 claim 이 새 종류 지원을 선언해야 업무 순환 Task 가 착수할 수 있다
    deadline = time.monotonic() + 30
    while not q(world, "SELECT 1 FROM connectors WHERE supported_kinds_json LIKE '%bug_fix%'"):
        assert time.monotonic() < deadline, world.log_tails()
        time.sleep(0.3)


def test_02_operator_connects_two_repositories_and_binds_assignees(world):
    http = world.http
    for full_name, scope, reviewer, rounds in (("acme/billing", "billing", REVIEW_BILLING, 1),
                                                ("acme/shop", "shop", REVIEW_SHOP, 0)):
        response = http.post("/github/sources", json={
            "repository_full_name": full_name, "workflow_repository_id": scope, "label_filter": ["bug"],
            "selected_issue_numbers": [], "start_at": START_AT, "fix_verification_profile_id": "vp-pytest",
            "review_agent_id": reviewer, "run_mode": "auto", "max_rework_rounds": rounds, "enabled": True,
        })
        assert response.status_code == 201, response.text
        assert TOKEN not in response.text and response.json()["token_configured"] is True
        world.sources[full_name] = response.json()["source"]["source_id"]
    for full_name, (user_id, login), agent_id in (("acme/billing", KIM, FIX_KIM), ("acme/billing", PARK, FIX_PARK),
                                                  ("acme/shop", LEE, FIX_LEE)):
        response = http.put(f"/github/sources/{world.sources[full_name]}/assignees/{user_id}",
                            json={"github_login": login, "agent_id": agent_id})
        assert response.status_code == 200, response.text

    # D 의 조건 — park 의 Agent 능력을 운영자가 다른 저장소로 좁혀 둔다(담당 연결은 그대로). 위임 범위 밖
    operator_agent(world, FIX_PARK, "code.fix", "payments", "local-billing-park")


def test_03_first_tick_imports_scoped_issues_and_starts_independent_a_and_b(world):
    world.fake.fail_next("POST", r"^/repos/acme/shop/issues/1/comments$", "drop")  # B 의 첫 댓글 응답 유실
    world.worker = world.make_worker()

    report = world.worker.tick()

    assert report.sync_errors == 0 and report.issues_created == 4
    numbers = {(r["source_id"], r["issue_number"]): r["task_id"] for r in q(world, "SELECT * FROM source_issues")}
    billing, shop = world.sources["acme/billing"], world.sources["acme/shop"]
    # 백로그(#4)·PR(#5)·다른 라벨(#6)은 받지 않는다 — 세 페이지(page_size 2)를 끝까지 읽었다
    assert sorted(numbers) == sorted([(billing, 1), (billing, 2), (billing, 3), (shop, 1)])
    world.tasks.update(A=numbers[(billing, 1)], D=numbers[(billing, 2)], E=numbers[(billing, 3)],
                       B=numbers[(shop, 1)])
    t = world.tasks

    # A·B — 서로 다른 등록이라 같은 tick 에 함께 착수. 대상은 등록값 + 소스의 검증 프로필(이슈 본문이 아님)
    assert report.tasks_started == 2
    (a,) = execs(world, t["A"])
    (b,) = execs(world, t["B"])
    assert (a["agent_id"], a["start_key"], b["agent_id"], b["start_key"]) == (
        FIX_KIM, f"auto:{t['A']}:r1", FIX_LEE, f"auto:{t['B']}:r1")
    target = request_of(a).target
    assert (target.local_registration_id, target.base_commit, target.verification_profile_id) == (
        "local-billing-kim", world.base["billing"], "vp-pytest")
    # D — 위임 밖 / E — 담당 2명: 착수 없이 운영자 요청 한 건씩
    assert execs(world, t["D"]) == [] and execs(world, t["E"]) == []
    assert [r["code"] for r in open_requests(world, t["D"])] == ["delegation_denied"]
    assert [r["code"] for r in open_requests(world, t["E"])] == ["assignee_multiple"]

    # 원본 반영 — 실행·사람 요청이 생긴 이슈만 댓글. B 는 응답을 잃어 '반영 불확실' 로 남는다(재POST 하지 않음)
    conn = world.conn()
    try:
        assert repo.list_source_deliveries(conn, t["B"])[-1].state == "unknown"
    finally:
        conn.close()
    assert len(world.fake.issue_comments("acme/shop", 1)) == 1  # 실제로는 만들어졌다

    # G — 다음 수집에서 새로 들어온다. B 가 아직 결과 전이면 같은 등록이라 착수하지 않는다(저장소 잠금)
    world.fake.add("acme/shop", G_ISSUE)
    report = world.worker.tick()
    assert report.issues_created == 1
    t["G"] = q(world, "SELECT task_id FROM source_issues WHERE source_id = ? AND issue_number = 2", shop)[0][0]
    if execs(world, t["B"])[0]["status"] != "result_ready":
        assert execs(world, t["G"]) == []
        assert task(world, t["G"])["status_reason"] == "같은 저장소에서 다른 수정 실행 중"


def test_04_existing_review_c_is_registered_for_a_and_waits_for_its_result(world):
    response = world.http.post("/tasks", data={
        "title": "쿠폰 수정 검토", "request": "결제 경로 위주로 결과 커밋을 검토해 주세요.",
        "capability_code": "code.review", "scope_value": "billing", "selection_mode": "manual",
        "chosen_agent_id": REVIEW_BILLING, "run_mode": "auto", "completion_mode": "review",
        "predecessor_task_id": world.tasks["A"],
    })
    assert response.status_code == 303, response.text[:500]
    world.tasks["C"] = response.headers["location"].rsplit("/", 1)[1]
    world.worker.tick()
    assert execs(world, world.tasks["C"]) == []  # 검토는 A 의 판정 통과 결과가 있어야 시작한다


def test_05_uncertain_comment_is_reconciled_and_github_outage_does_not_stop_the_cycle(world):
    t = world.tasks
    world.fake.fail_next("GET", r"^/repos/acme/billing/issues$", "500")
    report = world.worker.tick()
    assert report.sync_errors == 1  # 수집 실패는 다음 tick 에 같은 커서로 — Task·실행은 그대로
    conn = world.conn()
    try:
        # 응답을 잃은 POST 는 marker 로 찾아 확정한다 — 두 번째 댓글 없음
        assert repo.list_source_deliveries(conn, t["B"])[-1].state == "delivered"
    finally:
        conn.close()
    (comment,) = world.fake.issue_comments("acme/shop", 1)
    assert comment["body"].splitlines()[0] == f"<!-- runloom:task={t['B']} -->"

    # 오래된 스냅샷(이른 updated_at, 다른 제목)이 늦게 와도 반영하지 않는다
    old = gh_issue("acme/billing", 1, "옛 제목", assignees=[KIM], updated_at="2026-09-09T00:00:00Z",
                   body="[scenario:coupon]")
    world.fake.serve_stale_once("acme/billing", old)
    world.worker.tick()
    assert task(world, t["A"])["title"] == "쿠폰이 두 번 차감됩니다"
    assert q(world, "SELECT source_revision FROM source_issues WHERE task_id = ?", t["A"])[0][0] == 1
    assert q(world, "SELECT COUNT(*) FROM tasks WHERE session_id = ?", world.session_id)[0][0] == 6  # 5 + C


def test_06_real_fix_is_verified_and_restarted_workers_link_existing_c_once(world):
    t = world.tasks
    (a1,) = execs(world, t["A"])
    drive(world, lambda: q(world, "SELECT 1 FROM executions WHERE execution_id = ? AND status = 'result_ready'",
                           a1["execution_id"]) or settled(world, a1["execution_id"]), "A 첫 결과")
    world.worker = world.make_worker()  # 워커 재시작 — 판정·연결은 DB 에서 다시 계산한다
    drive(world, lambda: execs(world, t["C"]), "C 착수")
    for _ in range(3):
        world.make_worker().tick()

    assert verdict_checks(world, a1["execution_id"]) == {
        "result_parsed": True, "result_ids_match": True, "commit_matches": True, "required_artifacts": True,
        "test_before_failed": True, "verification_passed": True,
    }
    before = artifact(world, a1["execution_id"], "test_log_before").decode()
    after = artifact(world, a1["execution_id"], "test_log_after").decode()
    verification = artifact(world, a1["execution_id"], "verification_log").decode()
    assert before.startswith("exit_code=1") and "test_coupon_is_applied_once" in before  # 기준 커밋에서 실제로 실패
    assert after.startswith("exit_code=0") and verification.startswith("exit_code=0")
    result = fix_result(world, execs(world, t["A"])[0])
    assert result.base_commit == world.base["billing"]
    assert _git(world.billing, "rev-parse", result_branch_of(world, t["A"])) == result.result_commit  # 실제 커밋
    assert _git(world.billing, "rev-parse", "main") == world.base["billing"]  # 기준 브랜치는 그대로

    # 기존 C 에 연결 — 새 검토 Task·후속 링크 없음, 실행 하나
    assert [r["task_id"] for r in reviews_of(world, t["A"])] == [t["C"]]
    assert q(world, "SELECT COUNT(*) FROM followup_links WHERE cause_execution_id = ?", a1["execution_id"])[0][0] == 0
    (c1,) = execs(world, t["C"])
    assert (c1["start_key"], c1["agent_id"]) == (f"review:{a1['execution_id']}", REVIEW_BILLING)
    assert request_of(c1).target.result_commit == result.result_commit


def test_07_changes_requested_reworks_a_once_and_the_new_commit_is_reviewed(world):
    t = world.tasks
    a1 = execs(world, t["A"])[0]
    c1 = execs(world, t["C"])[0]
    drive(world, lambda: len(execs(world, t["A"])) == 2, "A 재작업 착수")
    first_review = review_result(world, c1)
    assert first_review.outcome == "changes_requested"
    assert first_review.reviewed_commit == fix_result(world, a1).result_commit  # 실제 결과 커밋을 읽었다
    assert [(f.path, f.severity) for f in first_review.findings] == [("billing/coupon.py", "blocking")]

    a2 = execs(world, t["A"])[1]
    assert (a2["attempt_no"], a2["start_key"]) == (2, f"rework:{c1['execution_id']}")
    assert request_of(a2).target.base_commit == fix_result(world, a1).result_commit  # 이전 결과에서 잇는다

    drive(world, lambda: task(world, t["C"])["finished_at"], "C 재검토 승인")
    a2 = execs(world, t["A"])[1]
    second = fix_result(world, a2)
    assert verdict_checks(world, a2["execution_id"])["test_before_failed"] is True  # 새 재현 테스트가 이전 커밋에서 실패
    c_runs = execs(world, t["C"])
    assert [e["start_key"] for e in c_runs] == [f"review:{a1['execution_id']}", f"review:{a2['execution_id']}"]
    final = review_result(world, c_runs[-1])
    assert (final.outcome, final.reviewed_commit) == ("approved", second.result_commit)
    assert (task(world, t["C"])["status"], task(world, t["C"])["status_reason"]) == ("완료", "검토 승인")
    a = task(world, t["A"])
    assert (a["status"], a["status_reason"], a["finished_at"]) == (
        "확인 필요", "검토 승인 — 병합·이슈 종료는 사람", None)
    assert _git(world.billing, "rev-parse", result_branch_of(world, t["A"])) == second.result_commit  # 재작업은 같은 브랜치
    assert _git(world.billing, "rev-parse", "main") == world.base["billing"]  # 승인은 병합이 아니다


def test_08_b_creates_one_new_review_f_and_g_waits_for_the_same_registration(world):
    t = world.tasks
    b1 = execs(world, t["B"])[0]
    drive(world, lambda: reviews_of(world, t["B"]) and task(world, reviews_of(world, t["B"])[0]["task_id"])["finished_at"],
          "F 승인")
    (f,) = reviews_of(world, t["B"])
    world.tasks["F"] = f["task_id"]
    assert f["chosen_agent_id"] == REVIEW_SHOP
    links = q(world, "SELECT * FROM followup_links WHERE cause_execution_id = ?", b1["execution_id"])
    assert [(r["to_kind"], r["task_id"]) for r in links] == [("code_review", f["task_id"])]
    (f1,) = execs(world, f["task_id"])
    assert f1["start_key"] == f"review:{b1['execution_id']}"
    assert review_result(world, f1).outcome == "approved"
    assert task(world, t["B"])["status_reason"] == "검토 승인 — 병합·이슈 종료는 사람"

    # G 는 B 가 결과를 낸 뒤에야 같은 등록에서 착수했다(저장소 잠금), 동시에 돈 적 없다
    drive(world, lambda: execs(world, t["G"]), "G 착수")
    g1 = execs(world, t["G"])[0]
    b_ready = q(world, "SELECT received_at FROM execution_events WHERE execution_id = ? AND type = 'result_ready'",
                b1["execution_id"])[0][0]
    assert g1["created_at"] >= b_ready


def test_09_rework_limit_zero_turns_changes_requested_into_a_human_request(world):
    t = world.tasks
    requests = drive(world, lambda: [r for r in open_requests(world, t["G"]) if r["code"] == "rework_limit_reached"],
                     "G 재작업 상한")
    assert len(requests) == 1
    assert len(execs(world, t["G"])) == 1  # 자동 재작업 없음
    for _ in range(3):
        world.worker.tick()
    assert len(execs(world, t["G"])) == 1 and len(open_requests(world, t["G"])) == 1


def test_10_e_starts_only_after_the_operator_chooses_an_agent(world):
    t = world.tasks
    (request,) = open_requests(world, t["E"])
    assert execs(world, t["E"]) == []

    response = answer(world, request, "resp-e-1", action="choose_agent", agent_id=FIX_KIM)
    assert response.status_code == 200 and response.json()["created"] is True
    again = answer(world, request, "resp-e-1", action="choose_agent", agent_id=FIX_KIM)  # 재전송
    assert again.status_code == 200 and again.json()["created"] is False
    assert answer(world, request, "resp-e-1", action="close").json()["code"] == "response_conflict"
    stale = answer(world, request, "resp-e-2", action="choose_agent", agent_id=FIX_PARK)  # 이미 응답한 revision
    assert stale.status_code == 409 and stale.json()["code"] == "stale_request"

    (e1,) = drive(world, lambda: execs(world, t["E"]), "E 착수")
    assert (e1["agent_id"], e1["start_key"]) == (FIX_KIM, f"auto:{t['E']}:r2")
    assert q(world, "SELECT COUNT(*) FROM source_issues")[0][0] == 5  # 이슈 재등록 없음


def test_11_d_waits_until_the_operator_changes_delegation_not_just_answers(world):
    t = world.tasks
    (request,) = open_requests(world, t["D"])
    assert request["code"] == "delegation_denied"
    assert answer(world, request, "resp-d-1", text="진행해 주세요").status_code == 200
    drive(world, lambda: [r for r in open_requests(world, t["D"]) if r["revision"] == 1
                          and r["request_id"] != request["request_id"]], "D 재요청")
    assert execs(world, t["D"]) == []  # 응답은 위임 권한이 아니다

    operator_agent(world, FIX_PARK, "code.fix", "billing", "local-billing-park")  # 설정 변경(운영자 권한)
    (d1,) = drive(world, lambda: execs(world, t["D"]), "D 착수")
    assert d1["agent_id"] == FIX_PARK and d1["start_key"].startswith(f"auto:{t['D']}:r")


def test_12_everything_settles_without_duplicates_and_github_sees_one_comment_per_issue(world):
    t = world.tasks

    def done() -> bool:
        return all(reviews_of(world, t[key]) and task(world, reviews_of(world, t[key])[0]["task_id"])["finished_at"]
                   for key in ("D", "E"))

    drive(world, done, "D·E 검토 승인")
    world.worker = world.make_worker()
    for _ in range(3):
        world.worker.tick()

    # Task·Execution 수 — 재시작·재수신·재전송에도 늘지 않았다
    counts = {key: len(execs(world, t[key])) for key in ("A", "B", "C", "D", "E", "F", "G")}
    assert counts == {"A": 2, "B": 1, "C": 2, "D": 1, "E": 1, "F": 1, "G": 1}
    assert [len(reviews_of(world, t[key])) for key in ("A", "B", "D", "E", "G")] == [1, 1, 1, 1, 1]
    assert q(world, "SELECT COUNT(*) FROM tasks WHERE session_id = ?", world.session_id)[0][0] == 10
    for key in ("B", "D", "E"):
        assert task(world, t[key])["status_reason"] == "검토 승인 — 병합·이슈 종료는 사람"

    # 원본 이슈 — 업무마다 댓글 하나(marker 첫 줄), 범위 밖 이슈는 없음. 쓰기는 댓글 생성·수정뿐
    for full_name, number, key in (("acme/billing", 1, "A"), ("acme/billing", 2, "D"), ("acme/billing", 3, "E"),
                                   ("acme/shop", 1, "B"), ("acme/shop", 2, "G")):
        (comment,) = world.fake.issue_comments(full_name, number)
        assert comment["body"].splitlines()[0] == f"<!-- runloom:task={t[key]} -->"
    for number in (4, 5, 6):
        assert world.fake.issue_comments("acme/billing", number) == []
    posts = [p for m, p in world.fake.writes() if m == "POST"]
    assert sorted(posts) == sorted(f"/repos/{n}/issues/{i}/comments" for n, i in (
        ("acme/billing", 1), ("acme/billing", 2), ("acme/billing", 3), ("acme/shop", 1), ("acme/shop", 2)))
    assert all(re.fullmatch(r"/repos/acme/(billing|shop)/issues/comments/\d+", p)
               for m, p in world.fake.writes() if m == "PATCH")
    assert {m for m, _ in world.fake.writes()} <= {"POST", "PATCH"}
    assert all(authorized for _, _, authorized in world.fake.requests)

    a_comment = world.fake.issue_comments("acme/billing", 1)[0]["body"]
    first, final = (fix_result(world, e) for e in execs(world, t["A"]))
    # 최신 수정(재작업)의 기준 → 결과 커밋 전체 SHA — 재작업의 기준은 첫 결과 커밋이다
    assert final.base_commit == first.result_commit
    assert f"`{final.base_commit}` → 결과 커밋 `{final.result_commit}`" in a_comment
    assert "`approved`" in a_comment
    (g_request,) = open_requests(world, t["G"])
    assert g_request["question"] in world.fake.issue_comments("acme/shop", 2)[0]["body"]

    # 기준 브랜치는 어느 저장소에서도 움직이지 않았다(자동 병합·푸시 없음)
    assert _git(world.billing, "rev-parse", "main") == world.base["billing"]
    assert _git(world.shop, "rev-parse", "main") == world.base["shop"]

    # 업무 기준 (phase 14) — 이슈 하나 = 업무 하나. B 의 검토 F 는 같은 업무의 두 번째 단계, 미리 등록한 C 는 A 와
    # 다른 업무(blocks 연결). 승인 뒤 병합·이슈 종료는 사람이라 B 업무는 내 차례
    assert q(world, "SELECT COUNT(*) FROM work_items WHERE session_id = ?", world.session_id)[0][0] == 6  # 이슈 5 + C
    b_work = q(world, "SELECT w.* FROM work_items w JOIN tasks t ON t.work_item_id = w.work_item_id"
                      " WHERE t.task_id = ?", t["B"])[0]
    stages = q(world, "SELECT task_id FROM tasks WHERE work_item_id = ? ORDER BY created_at, task_id",
               b_work["work_item_id"])
    assert [r["task_id"] for r in stages] == [t["B"], t["F"]]
    assert (b_work["status"], b_work["source_key"]) == ("내 차례", "acme/shop#1")
    flow = [json.loads(r["data_json"])["to"] for r in q(
        world, "SELECT data_json FROM work_item_events WHERE work_item_id = ? AND type = 'status_changed' ORDER BY id",
        b_work["work_item_id"])]
    assert "에이전트 작업 중" in flow and flow[-1] == "내 차례"
    assert result_branch_of(world, t["B"]) == f"runloom/RUN-{b_work['key_number']}"
    assert _git(world.shop, "rev-parse", result_branch_of(world, t["B"])) == fix_result(world, execs(world, t["B"])[0]).result_commit
    (link,) = q(world, "SELECT l.type FROM work_item_links l JOIN tasks a ON a.work_item_id = l.from_work_item_id"
                       " JOIN tasks c ON c.work_item_id = l.to_work_item_id WHERE a.task_id = ? AND c.task_id = ?",
                t["A"], t["C"])
    assert link["type"] == "blocks"


def test_13_github_token_never_leaves_the_central_environment(world):
    """토큰은 가짜 GitHub 요청 헤더에만 — DB·산출물·로그·연결 프로그램 상태·저장소·댓글에 없다(도구 환경은 가짜 codex 가 검사)."""
    needle = TOKEN.encode()
    leaked = [str(path) for path in world.workdir.rglob("*")
              if path.is_file() and path.name != "codex" and needle in path.read_bytes()]
    assert leaked == []
    assert all(TOKEN not in c["body"] for c in world.fake.comments.values())
    assert q(world, "SELECT COUNT(*) FROM executions WHERE failed_code IS NOT NULL")[0][0] == 0
