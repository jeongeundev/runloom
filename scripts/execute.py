#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--push | --pr] [--engine codex|claude]
"""

import argparse
import contextlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    STEP_TIMEOUT = 1800  # 초. 한 step 의 에이전트 세션 상한
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    PR_TEMPLATE = Path(".github") / "pull_request_template.md"
    PR_TITLE_RE = re.compile(r"^(feat|fix|docs|refactor|chore|test)(\([^)]+\))?: \S")
    TZ = timezone(timedelta(hours=9))

    # step 을 위임할 에이전트. codex 가 기본이고, 사용량 한도에 걸리면 claude 로 넘어간다.
    # claude: --strict-mcp-config 로 전역 MCP(~/.claude.json, claude.ai 커넥터)를 물지 않는다.
    # step 세션은 파일·셸 내장 도구만 쓰고, MCP 도구 스키마는 시작 컨텍스트만 키운다
    # (2026-09-21 실측: 서버 12개·도구 174개 → 0개·29개). --mcp-config 는 가변 인자라
    # 뒤따르는 prompt 를 설정으로 삼키므로 쓰지 않는다.
    ENGINES = {
        "codex": ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox"],
        "claude": ["claude", "-p", "--dangerously-skip-permissions", "--strict-mcp-config",
                   "--output-format", "json"],
    }
    # codex 가 한도 초과로 실패할 때 출력에 남는 문구 (usage_limit_reached / "You've hit your usage limit")
    QUOTA_RE = re.compile(r"usage[ _]limit", re.IGNORECASE)

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False, open_pr: bool = False,
                 engine: str = "codex"):
        if engine not in self.ENGINES:
            raise ValueError(f"unknown engine: {engine}")
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push
        self._open_pr_after = open_pr
        self._engine = engine

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._check_blockers()
        self._checkout_branch()
        guardrails = self._load_guardrails()
        self._ensure_created_at()
        self._execute_all_steps(guardrails)
        self._finalize()

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _run_gh(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *args], cwd=self._root, capture_output=True, text=True)

    def _push_branch(self):
        branch = f"feat-{self._phase_name}"
        r = self._run_git("push", "-u", "origin", branch)
        if r.returncode != 0:
            print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
            sys.exit(1)
        print(f"  ✓ Pushed to origin/{branch}")

    def _open_pr(self):
        """phase 결과를 main 으로 가는 PR 로 올린다. 병합은 CI·리뷰 뒤 사람이 GitHub 에서 한다."""
        branch = f"feat-{self._phase_name}"
        self._push_branch()

        if self._run_gh("pr", "view", branch, "--json", "url", "-q", ".url").returncode == 0:
            print(f"  ✓ PR 이미 있음 — push 로 갱신됨 ({branch})")
            return

        written = self._write_pr_description(branch)
        if written:
            title, body = written
        else:
            print("  WARN: PR 본문 작성 세션이 형식에 맞는 결과를 남기지 않음 — step summary 로 조립")
            title, body = f"feat: {self._phase_name}", self._fallback_pr_body()
        r = self._run_gh("pr", "create", "--base", "main", "--head", branch,
                         "--title", title, "--body", body)
        if r.returncode != 0:
            print(f"\n  ERROR: PR 생성 실패: {r.stderr.strip()}")
            sys.exit(1)
        print(f"  ✓ PR: {r.stdout.strip()}")

    def _write_pr_description(self, branch: str) -> Optional[tuple[str, str]]:
        """세션 하나가 phase 전체를 보고 PR 템플릿 칸을 채운다. 형식이 틀리면 None."""
        template_file = Path(self._root) / self.PR_TEMPLATE
        if not template_file.exists():
            return None
        template = template_file.read_text()
        sections = [line for line in template.splitlines() if line.startswith("## ")]
        with tempfile.TemporaryDirectory() as tmp:
            title_file, body_file = Path(tmp) / "title.txt", Path(tmp) / "body.md"
            self._run_engine(self._pr_prompt(branch, template, title_file, body_file))
            if not (title_file.exists() and body_file.exists()):
                return None
            title = title_file.read_text().strip()
            body = body_file.read_text().strip()
        if not self.PR_TITLE_RE.match(title) or "\n" in title:
            return None
        if any(h not in body.splitlines() for h in sections):
            return None
        return title, body

    def _pr_prompt(self, branch: str, template: str, title_file: Path, body_file: Path) -> str:
        phase = f"phases/{self._phase_dir_name}/"
        return (
            f"{self._project} 프로젝트 phase `{self._phase_dir_name}` 의 하네스 실행이 끝났다. "
            f"`{branch}` → `main` PR 의 제목과 본문을 써라.\n\n"
            f"## 읽을 것\n\n"
            f"- `{phase}README.md` — 왜·범위·결정\n"
            f"- `{phase}index.json` — step 별 summary·concerns (step 세션이 다음 세션에게 남긴 메모다. "
            f"그대로 옮기지 말고 사람이 읽을 문장으로 다시 써라)\n"
            f"- `git diff --stat main...{branch}` 와 필요한 부분의 diff\n\n"
            f"## 쓸 것\n\n"
            f"제목 파일: {title_file}\n"
            f"본문 파일: {body_file}\n\n"
            f"- 제목: 한 줄, conventional commits 형식 + 영어 한 줄 설명 "
            f"(예: `feat: add handoff reports with evidence before next stage starts`). "
            f"squash 병합 때 main 커밋 첫 줄이 된다.\n"
            f"- 본문: 아래 템플릿의 `## ` 칸을 순서·이름 그대로 두고 `<!-- -->` 안내를 따라 채운 뒤 안내 주석은 지운다. "
            f"내용은 한국어. 빈 칸은 `None`.\n"
            f"- 읽는 사람: 병합 전의 운영자, 나중에 맥락 없이 보는 다른 사람, 리뷰 봇. "
            f"step 별 상세·파일·함수 목록은 넣지 않는다 (phases/ 와 diff 에 있다).\n"
            f"- Needs decision 에는 사람이 판단해야 할 것만 `- [ ]` 로 적는다 — 설계(README 결정)에서 벗어난 것, "
            f"정하지 않고 넘어간 것. 실제 환경에서 확인하지 못한 것은 Not verified 로.\n"
            f"- 저장소 파일을 고치거나 커밋·push·PR 생성을 하지 마라. 위 두 파일만 쓴다.\n\n"
            f"## 템플릿\n\n{template}"
        )

    def _fallback_pr_body(self) -> str:
        index = self._read_json(self._index_file)
        lines = [f"- step {s['step']} `{s['name']}` — {s.get('summary', '')}" for s in index["steps"]]
        body = f"phase `{self._phase_dir_name}` 하네스 실행 결과.\n\n" + "\n".join(lines)
        concerns = [f"- step {s['step']} `{s['name']}` — {c}" for s in index["steps"]
                    for c in self._as_list(s.get("concerns", []))]
        if concerns:
            body += "\n\n## 걸리는 점 (step 세션이 남김)\n\n" + "\n".join(concerns)
        return body

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print("  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print("  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- guardrails & context ---

    def _load_guardrails(self) -> str:
        # 프로젝트 규칙 문서만 넣는다. codex 는 AGENTS.md 를 읽는다. 없으면 CLAUDE.md 로 폴백.
        # docs/ 는 주입하지 않는다 — 전부 넣으면 step 프롬프트가 12만~16만 토큰으로 출발하고
        # 매 턴 다시 읽힌다 (2026-09-21 실측). 필요한 문서·절은 step.md 의
        # "읽어야 할 파일" 절이 가리키고, 세션이 그때 읽는다.
        guide = next((p for p in (ROOT / "AGENTS.md", ROOT / "CLAUDE.md") if p.exists()), None)
        if guide is None:
            return ""
        return f"## 프로젝트 규칙 ({guide.name})\n\n{guide.read_text()}"

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    def _build_preamble(self, guardrails: str, step_context: str,
                        prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            f"5. /phases/{self._phase_dir_name}/index.json의 해당 step status를 업데이트하라:\n"
            f"   - AC 통과 → \"completed\" + \"summary\" 필드에 이 step의 산출물을 한 줄로 요약\n"
            f"     + \"verification\": 실제로 실행한 AC 명령과 종료 코드 목록 "
            f"(예: [{{\"command\": \"python3 -m pytest -q tests/...\", \"exit_code\": 0}}]). "
            f"이 목록이 없거나 0 이 아닌 종료 코드가 있으면 완료로 인정하지 않는다\n"
            f"     + 끝냈지만 걸리는 점(정하지 않고 넘어간 것, 확인하지 못한 것)이 있으면 "
            f"\"concerns\": [\"...\"] 에 적어라. 마지막에 PR 본문을 쓰는 세션이 읽는다\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 → \"error\" + \"error_message\" 기록\n"
            f"   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) → \"blocked\" + \"blocked_reason\" 기록 후 즉시 중단\n"
            f"6. 모든 변경사항을 커밋하라:\n"
            f"   {commit_example}\n\n---\n\n"
        )

    # --- 에이전트 호출 ---

    def _run_engine(self, prompt: str) -> subprocess.CompletedProcess:
        # stdin 이 파이프면 codex exec 가 EOF 까지 읽으므로 DEVNULL 로 막는다.
        cmd = self.ENGINES[self._engine] + [prompt]
        try:
            return subprocess.run(
                cmd, cwd=self._root, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=self.STEP_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            # 시간 초과를 실패한 실행으로 돌려 재시도 루프에 맡긴다. 예외를 그대로 두면
            # 실행 전체가 죽고 index.json 에 아무 기록도 남지 않는다.
            out = e.output if isinstance(e.output, str) else (e.output or b"").decode(errors="replace")
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode(errors="replace")
            return subprocess.CompletedProcess(
                cmd, 124, out, f"{err}\n[timeout] step 이 {self.STEP_TIMEOUT}초를 초과했습니다.")

    def _invoke_agent(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        result = self._run_engine(prompt)

        # codex 한도 초과 → 같은 프롬프트를 claude 로 재실행하고, 이번 실행의 남은 step 도 claude 로.
        # exit 0 이면 문구가 있어도 폴백하지 않는다 — 완료된 step 을 두 번 실행하게 된다.
        if (self._engine == "codex" and result.returncode != 0
                and self.QUOTA_RE.search(result.stdout + result.stderr)):
            print("\n  ⚠ Codex 사용량 한도 도달 — 이후 step은 Claude로 실행")
            self._engine = "claude"
            result = self._run_engine(prompt)

        if result.returncode != 0:
            print(f"\n  WARN: {self._engine} 비정상 종료 (code {result.returncode})")
            if result.stderr:
                print(f"  stderr: {result.stderr[:500]}")

        output = {
            "step": step_num, "name": step_name, "engine": self._engine,
            "exitCode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        }
        out_path = self._phase_dir / f"step{step_num}-output.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return output

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print("  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print("  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print("  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print("  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True, 실패/차단이면 False."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(guardrails, step_context, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                self._invoke_agent(step, preamble)
                elapsed = int(pi.elapsed)

            index = self._read_json(self._index_file)
            status = next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")
            ts = self._stamp()

            missing = self._missing_evidence(index, step_num) if status == "completed" else None
            if status == "completed" and missing is None:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((s.get("blocked_reason", "") for s in index["steps"] if s["step"] == step_num), "")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._update_top_index("blocked")
                sys.exit(2)

            err_msg = missing or next(
                (s.get("error_message", "Step did not update status") for s in index["steps"] if s["step"] == step_num),
                "Step did not update status",
            )

            if attempt < self.MAX_RETRIES:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "pending"
                        s.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "error"
                        s["error_message"] = f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}"
                        s["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._update_top_index("error")
                sys.exit(1)

        return False  # unreachable

    @staticmethod
    def _as_list(value) -> list:
        """세션이 목록 대신 문자열 하나를 적어도 한 항목으로 다룬다."""
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _missing_evidence(index: dict, step_num: int) -> Optional[str]:
        """completed 로 표시한 step 에 완료 증거가 없으면 그 이유를 돌려준다. 증거가 있으면 None."""
        step = next(s for s in index["steps"] if s["step"] == step_num)
        evidence = step.get("verification")
        if not isinstance(evidence, list) or not evidence:
            return "completed 로 표시했지만 verification(실행한 AC 명령과 종료 코드)이 없다"
        # bool 은 int 의 하위형이라 False == 0 이 통과한다 — 정수 0 만 인정한다
        failed = [e for e in evidence
                  if not isinstance(e, dict) or not e.get("command")
                  or type(e.get("exit_code")) is not int or e["exit_code"] != 0]
        if failed:
            return f"verification 에 command 가 없거나 exit_code 가 0 이 아닌 항목이 있다: {failed}"
        return None

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break

            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._open_pr_after:
            self._open_pr()
        elif self._auto_push:
            self._push_branch()

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    parser.add_argument("--pr", action="store_true",
                        help="완료 후 push 하고 main 으로 가는 PR 을 연다 (이미 있으면 재사용)")
    parser.add_argument("--engine", choices=sorted(StepExecutor.ENGINES), default="codex",
                        help="step 을 위임할 에이전트 (기본 codex). claude 를 주면 처음부터 claude 로 실행")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push, open_pr=args.pr, engine=args.engine).run()


if __name__ == "__main__":
    main()
