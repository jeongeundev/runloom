#!/usr/bin/env python3
"""Step 17 — 진단 모델 실호출 평가. ARCHITECTURE "진단 모델과 평가 기준"의 5사례 × 3회를 돌리고 결과 표를 쓴다.

    python3 scripts/diag_eval.py --cases all --repeat 3 --out docs/DIAG_EVAL.md

- 각 실행은 진단 서비스의 실제 경로 `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 HTTP 없이
  프로세스 안에서 부른다. 모델은 `OpenAIModelClient(model_id=DIAG_MODEL_ID)` (기본 ADR-0003 의 gpt-4.1-mini).
- 시작 전 확인: `OPENAI_API_KEY`, `DIAG_PRICE_INPUT_PER_M`, `DIAG_PRICE_OUTPUT_PER_M`, `DIAG_EVAL_BUDGET_USD` 가
  없으면 유료 호출 0회로 exit 2. 매 실행 전 누적 추정 비용이 `DIAG_EVAL_BUDGET_USD` 이상이면 멈춘다.
- 사례의 자료 누락·교체는 `FixtureStore(removed=, replaced=)` 로 재현하며 fixture 파일은 고치지 않는다.
  프롬프트·도구 설명은 제품 코드 그대로다 — 사례를 통과시키려고 정답을 넣지 않는다.
- 통과 기준: `normal` 3/3 이 `ready_for_handoff` + 검증기 `passed`, 나머지 12회 중 "잘못된 수정 착수"
  (`ready_for_handoff` 이면서 `passed`) 0회. ADR-0003 확정 여부의 최종 판단은 사용자가 한다.
"""

import argparse
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from diagnostic_demo import db
from diagnostic_demo.artifact_store import ArtifactStore
from diagnostic_demo.pricing import estimate_usd
from diagnostic_demo.settings import Settings, load_settings
from diagnostic_demo.tools.api import TOOL_CONTRACT_VERSION, Tools
from diagnostic_demo.tools.store import FixtureStore
from diagnostic_demo.tools.trace import ToolTraceRecorder
from diagnostic_demo.worker.assemble import ResultSchemaInvalid, assemble_result
from diagnostic_demo.worker.loop import Budget, BudgetExceeded, Usage, run_diagnosis
from diagnostic_demo.worker.model import DraftInvalid, ModelClient
from diagnostic_demo.worker.prompt import PROMPT_VERSION
from workflow.contracts.v1 import DiagnosisResult, ExecutionRequest
from workflow.domain.verification import LoadedEvidence, TraceEntry, verify_diagnosis

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "diagnostic_demo" / "fixtures" / "evidence"
DEFAULT_OUT = ROOT / "docs" / "DIAG_EVAL.md"
DEFAULT_WORKDIR = ROOT / "data" / "diag-eval"  # .gitignore 의 data/. 실행별 DB·산출물·results.json 을 남긴다

REQUIRED_ENV = ("OPENAI_API_KEY", "DIAG_PRICE_INPUT_PER_M", "DIAG_PRICE_OUTPUT_PER_M", "DIAG_EVAL_BUDGET_USD")
KST = timezone(timedelta(hours=9))
_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]+")

# CONTRACT 1절 진단 요청 — execution_id 만 실행마다 바꾼다
_REQUEST_BASE = {
    "contract_version": 1,
    "task_id": "diagnose-daily-0920",
    "kind": "diagnosis",
    "agent_id": "agent-ops-demo",
    "task_revision": 1,
    "request": "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요.",
    "input_artifact_ids": [],
    "target": {"run_id": "daily-0920-0900"},
}


# --- 사례 ------------------------------------------------------------------------------


def _fixture(evidence_id: str, ext: str) -> bytes:
    return (FIXTURES / evidence_id / f"1.{ext}").read_bytes()


def _effective_conflict_document() -> bytes:
    """변경 안내의 적용 시각을 실패 실행(09-20 09:00) 뒤인 09-21 00:00 으로. markdown 과 machine 을 함께 바꾼다."""
    doc = json.loads(_fixture("upstream-response-change", "json"))
    doc["machine"]["effective_at"] = "2026-09-21T00:00:00+09:00"
    if "2026-09-20 00:00" not in doc["markdown"]:
        raise ValueError("upstream-response-change fixture 의 markdown 에 적용 시각 문구가 없습니다")
    doc["markdown"] = doc["markdown"].replace("2026-09-20 00:00", "2026-09-21 00:00")
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")


def _http_error_run_record() -> bytes:
    """실패 실행이 HTTP 503 으로 조회 단계에서 실패한 기록. 응답 본문이 없으므로 response_ref 는 null."""
    record = json.loads(_fixture("run-daily-0920-0900", "json"))
    record["http_status"] = 503
    record["stages"] = [
        {"stage": "fetch", "status": "failed", "error_code": "UPSTREAM_HTTP_503"},
        {"stage": "transform", "status": "skipped"},
        {"stage": "render", "status": "skipped"},
    ]
    record["response_ref"] = None
    record["report_ref"] = None
    return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")


_HTTP_ERROR_LOG = (
    "2026-09-20T09:00:00+09:00 ERROR run_id=daily-0920-0900 stage=fetch event=http_error http_status=503"
    " retry_count=3\n"
    "2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900 stage=transform status=skipped reason=fetch_failed\n"
    "2026-09-20T09:00:00+09:00 INFO  run_id=daily-0920-0900 stage=render status=skipped reason=fetch_failed\n"
    "2026-09-20T09:00:00+09:00 ERROR run_id=daily-0920-0900 event=run_finished status=failed report_created=false\n"
).encode("utf-8")

CASES: dict[str, dict] = {
    "normal": {},
    "missing_response": {"removed": frozenset({("response-after", "1")})},
    "missing_change_doc": {"removed": frozenset({("upstream-response-change", "1")})},
    "effective_conflict": {
        "replaced": {("upstream-response-change", "1"): _effective_conflict_document()},
    },
    "http_error_input": {
        "replaced": {
            ("run-daily-0920-0900", "1"): _http_error_run_record(),
            ("log-daily-0920", "1"): _HTTP_ERROR_LOG,
        },
    },
}

# 표 렌더용 기대 결과 (ARCHITECTURE·PRD "도구·인계 수용 기준")
EXPECTATIONS: dict[str, str] = {
    "normal": "`ready_for_handoff` + 검증기 `passed`",
    "missing_response": "`needs_information` (실패 응답 본문 누락)",
    "missing_change_doc": "`needs_information` (변경 안내 누락)",
    "effective_conflict": "`needs_information`(evidence_conflict) 또는 검증기 `failed` — 인계 없음",
    "http_error_input": "`needs_information`(unsupported_diagnosis) — response_path_changed 재생 없음",
}


# --- 결과 ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    case: str
    attempt: int
    execution_id: str
    outcome: str | None  # ready_for_handoff | needs_information | None(모델·조립 오류)
    verdict: str | None  # passed | failed | undecidable | None(검증기 미도달)
    calls: int
    input_tokens: int
    output_tokens: int
    seconds: float
    estimated_usd: float
    error: str | None
    diagnosis_code: str | None = None
    missing_codes: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()  # "get_run daily-0920-0900 ok" — 조회 이력 순서 (분석용)

    @property
    def handoff_passed(self) -> bool:
        """자동 완료 → B 착수로 이어지는 조합. normal 이 아닌 사례에서 이것이 "잘못된 수정 착수" 다."""
        return self.outcome == "ready_for_handoff" and self.verdict == "passed"


@dataclass
class CostLedger:
    """이 평가의 누적 추정 비용. 한도 이상이면 다음 실행을 시작하지 않는다."""

    limit_usd: float
    spent_usd: float = 0.0

    def exhausted(self) -> bool:
        return self.spent_usd >= self.limit_usd

    def add(self, usd: float) -> None:
        self.spent_usd += usd


@dataclass(frozen=True)
class Evaluation:
    normal_passed: int
    normal_total: int
    false_handoffs: tuple[CaseResult, ...]
    complete: bool  # 모든 사례가 repeat 회 끝났는지 (예산 중단·오류 없이)

    @property
    def passed(self) -> bool:
        return (
            self.complete
            and self.normal_total > 0
            and self.normal_passed == self.normal_total
            and not self.false_handoffs
        )


def safe_message(message: str) -> str:
    return _SECRET.sub("sk-***", message)[:500]


def _trace_label(entry) -> str:
    args = entry.input
    if entry.tool == "read_evidence":
        target = f"{args.get('evidence_id')}@{args.get('version')}"
    elif entry.tool == "get_run":
        target = str(args.get("run_id"))
    else:
        target = str(args.get("workflow_id"))
    return f"{entry.tool} {target} {'ok' if entry.ok else entry.error}"


def _preserve_invalid_draft(settings: Settings, execution_id: str, exc: DraftInvalid) -> str:
    """strict 스키마는 통과했지만 계약 검증기(Location 문법 등)가 거부한 초안. 원문·오류 위치를 workdir 에 남기고
    첫 오류 위치를 요약해 돌려준다 — 도구 반환·계약·프롬프트 중 어디가 문제인지 가르는 데 쓴다."""
    cause = exc.__cause__
    errors = (
        [{"loc": list(e["loc"]), "msg": e["msg"], "input": e.get("input")} for e in cause.errors()]
        if isinstance(cause, ValidationError) else []
    )
    out_dir = settings.artifact_dir.parent / "invalid-drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{execution_id}.json").write_text(
        json.dumps({"raw": exc.raw, "errors": errors}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if not errors:
        return ""
    return " — " + "; ".join(
        ".".join(str(part) for part in e["loc"]) + f"={e['input']!r}" for e in errors[:4]
    )


def _verifier_inputs(conn, artifact_store: ArtifactStore, result: DiagnosisResult, recorder: ToolTraceRecorder):
    """중앙 워커(Step 8)가 하는 일: 첨부를 산출물 저장소에서 내려받아 LoadedEvidence 로, 조회 이력을 TraceEntry 로."""
    loaded: dict[tuple[str, str], LoadedEvidence] = {}
    for ref in result.attachments:
        row = db.get_artifact(conn, result.execution_id, ref.artifact_id)
        loaded[(ref.evidence_id, ref.version)] = LoadedEvidence(
            evidence_id=ref.evidence_id, version=ref.version, content_type=row["content_type"],
            sha256=row["sha256"], content=artifact_store.read(row["store_ref"]),
        )
    trace = [
        TraceEntry(
            call_id=e.call_id, tool=e.tool, input=e.input, ok=e.ok,
            returned=tuple((r["evidence_id"], r["version"]) for r in e.returned),
        )
        for e in recorder.entries()
    ]
    return loaded, trace


def _run_one(
    name: str,
    attempt: int,
    *,
    model: Callable[[], ModelClient],
    settings: Settings,
    conn,
    artifact_store: ArtifactStore,
) -> CaseResult:
    execution_id = f"eval-{name}-{attempt}-{secrets.token_hex(4)}"
    request = ExecutionRequest.model_validate({**_REQUEST_BASE, "execution_id": execution_id})
    now = db.utc_now()
    db.insert_run(conn, request, now)

    store = FixtureStore(settings.fixtures_dir, settings.allowed_workflow_ids, **CASES[name])
    recorder = ToolTraceRecorder()
    budget = Budget(
        max_calls=settings.max_calls,
        max_input_tokens=settings.max_input_tokens,
        max_output_tokens=settings.max_output_tokens,
        timeout_seconds=settings.timeout_seconds,
    )
    provenance = {
        "model_id": settings.model_id,
        "prompt_version": PROMPT_VERSION,
        "tool_contract_version": TOOL_CONTRACT_VERSION,
    }
    usage = Usage()
    outcome = verdict = error = diagnosis_code = None
    missing_codes: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()

    started = time.monotonic()
    try:
        draft, _ = run_diagnosis(
            execution_id, request, Tools(store, recorder), model(), budget, time.monotonic,
            lambda *_: None, usage,
        )
        outcome = draft.outcome
        missing_codes = tuple(m.code for m in draft.missing_information)
        diagnosis_code = draft.diagnosis.code if draft.diagnosis is not None else None
        result, _ = assemble_result(
            draft, request, recorder, store, artifact_store, conn, provenance, now=db.utc_now()
        )
        verdict_obj = verify_diagnosis(result, *_verifier_inputs(conn, artifact_store, result, recorder))
        verdict = verdict_obj.outcome
        failed_checks = tuple(c.code for c in verdict_obj.checks if not c.passed)
    except BudgetExceeded as exc:
        error = f"{exc.code}: {safe_message(str(exc))}"
    except ResultSchemaInvalid as exc:
        error = f"result_schema_invalid: {safe_message(str(exc))}"
    except DraftInvalid as exc:
        error = f"model_output_invalid: {safe_message(str(exc))}{_preserve_invalid_draft(settings, execution_id, exc)}"
    except Exception as exc:  # SDK·네트워크·저장 오류 — 기록하고 다음 실행으로
        error = f"internal_error: {type(exc).__name__}: {safe_message(str(exc))}"
    seconds = time.monotonic() - started

    estimated = estimate_usd(
        usage.input_tokens, usage.output_tokens, settings.price_input_per_m, settings.price_output_per_m
    )
    db.record_usage(
        conn, execution_id, settings.model_id, usage.input_tokens, usage.output_tokens, usage.calls,
        estimated if settings.pricing_configured else None, db.utc_now(),
    )
    return CaseResult(
        case=name, attempt=attempt, execution_id=execution_id, outcome=outcome, verdict=verdict,
        calls=usage.calls, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        seconds=round(seconds, 1), estimated_usd=estimated, error=error, diagnosis_code=diagnosis_code,
        missing_codes=missing_codes, failed_checks=failed_checks,
        tool_calls=tuple(_trace_label(e) for e in recorder.entries()),
    )


def run_case(
    name: str,
    repeat: int = 3,
    *,
    model: Callable[[], ModelClient],
    settings: Settings,
    ledger: CostLedger | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> list[CaseResult]:
    """사례 하나를 repeat 회 실행한다. `model` 은 실행마다 새 클라이언트를 주는 factory 다.

    `ledger` 가 있으면 매 실행 전 한도를 확인하고, 이상이면 남은 회차를 시작하지 않는다.
    """
    if name not in CASES:
        raise KeyError(name)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    artifact_store = ArtifactStore(settings.artifact_dir)
    results: list[CaseResult] = []
    try:
        for attempt in range(1, repeat + 1):
            if ledger is not None and ledger.exhausted():
                log(f"예산 도달: 누적 US${ledger.spent_usd:.4f} ≥ 한도 US${ledger.limit_usd:g} — {name} {attempt}회차부터 중단")
                break
            result = _run_one(
                name, attempt, model=model, settings=settings, conn=conn, artifact_store=artifact_store
            )
            if ledger is not None:
                ledger.add(result.estimated_usd)
            results.append(result)
            log(
                f"{name} {attempt}/{repeat}: outcome={result.outcome} verdict={result.verdict}"
                f" calls={result.calls} tokens={result.input_tokens}/{result.output_tokens}"
                f" {result.seconds}s US${result.estimated_usd:.4f}"
                + (f" error={result.error}" if result.error else "")
            )
    finally:
        conn.close()
    return results


# --- 판정·렌더 ----------------------------------------------------------------------------


def evaluate(results: Sequence[CaseResult], *, repeat: int) -> Evaluation:
    normal = [r for r in results if r.case == "normal"]
    false_handoffs = tuple(r for r in results if r.case != "normal" and r.handoff_passed)
    counts = {case: sum(1 for r in results if r.case == case) for case in CASES}
    return Evaluation(
        normal_passed=sum(1 for r in normal if r.handoff_passed),
        normal_total=len(normal),
        false_handoffs=false_handoffs,
        complete=all(count == repeat for count in counts.values()),
    )


def _cell(value) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(
    results: Sequence[CaseResult],
    *,
    settings: Settings,
    ledger: CostLedger,
    repeat: int,
    started_at: str,
    finished_at: str,
) -> str:
    evaluation = evaluate(results, repeat=repeat)
    other_total = sum(1 for r in results if r.case != "normal")
    lines = [
        "# 진단 모델 실호출 평가",
        "",
        f"갱신일: {started_at[:10]}",
        "상태: Step 17 `scripts/diag_eval.py` 가 생성한 기록. ARCHITECTURE \"진단 모델과 평가 기준\"의 5사례 × 3회를"
        " 실제 모델로 돌린 결과다. 결과가 좋게 보이도록 편집하지 않는다. ADR-0003 파일 자체는 이 평가가 고치지 않는다"
        " (사용자 확정).",
        "",
        "## 조건",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 모델 | `{settings.model_id}` (Responses API, function calling + structured outputs strict) |",
        f"| 프롬프트·도구 계약 | `{PROMPT_VERSION}` · `{TOOL_CONTRACT_VERSION}` |",
        f"| 단가 (US$ / 1M 토큰) | 입력 {settings.price_input_per_m:g} · 출력 {settings.price_output_per_m:g}"
        " (`DIAG_PRICE_*`, 사용자가 공식 가격 페이지에서 확인) |",
        f"| 진단 1회 상한 | 호출 {settings.max_calls}회 · 입력 {settings.max_input_tokens:,} · 출력"
        f" {settings.max_output_tokens:,} 토큰 · {settings.timeout_seconds:g}초 |",
        f"| 평가 예산 | US${ledger.limit_usd:g} (`DIAG_EVAL_BUDGET_USD`) — 매 실행 전 누적 비용 확인 |",
        f"| 실행 | {started_at} ~ {finished_at} (KST) · 사례당 {repeat}회 · 총 {len(results)}회 |",
        f"| 총 추정 비용 | US${ledger.spent_usd:.4f} |",
        "| 경로 | `run_diagnosis` → `assemble_result` → 중앙 `verify_diagnosis` 를 프로세스 안에서 직접 호출 (HTTP 없음)."
        " 자료 누락·교체는 `FixtureStore(removed=, replaced=)`, fixture 파일 불변 |",
        "",
        "## 통과 기준과 판정",
        "",
        f"- normal {evaluation.normal_passed}/{evaluation.normal_total}: `ready_for_handoff` 이고 검증기 `passed`"
        f" — 기준 {repeat}/{repeat}",
        f"- 잘못된 수정 착수 {len(evaluation.false_handoffs)}/{other_total}: 나머지 4사례에서 `ready_for_handoff`"
        " 이면서 `passed` 인 실행 — 기준 0",
        f"- 전 사례 {repeat}회 완료: {'예' if evaluation.complete else '아니오 (예산 중단 또는 누락)'}",
        "",
    ]
    if evaluation.passed:
        lines += [
            "**판정: 확정 조건 충족 — ADR-0003 을 확정으로 갱신할 것을 제안한다.** 키·계정 API 사용 가능·단가·예산을"
            " 사용자가 확인했고, 위 기준을 실제 모델로 통과했다. ADR 파일 갱신은 사용자가 한다.",
        ]
    else:
        lines += ["**판정: 확정 보류.** 기준 미충족 실행:", ""]
        for r in results:
            if r.case == "normal" and not r.handoff_passed:
                lines.append(f"- normal {r.attempt}회차: outcome={_cell(r.outcome)} 검증기={_cell(r.verdict)}"
                             + (f" 오류 `{_cell(r.error)}`" if r.error else "")
                             + (f" 실패 check {', '.join(f'`{c}`' for c in r.failed_checks)}" if r.failed_checks else ""))
        for r in evaluation.false_handoffs:
            lines.append(f"- {r.case} {r.attempt}회차: 잘못된 수정 착수 — `ready_for_handoff` 이면서 검증기 `passed`")
        if not evaluation.complete:
            lines.append("- 일부 사례가 3회를 채우지 못했다 (예산 중단 또는 오류)")
        lines += ["", "도구 반환·계약·프롬프트 중 어디가 문제인지의 분리 분석과 모델 교체 필요 여부는 아래 절에 적는다."]
    lines += [
        "",
        "## 사례별 결과",
        "",
        "| 사례 | 회차 | outcome | 검증기 | diagnosis / missing | 호출 | 입력 토큰 | 출력 토큰 | 초 | US$ | 오류 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        detail = r.diagnosis_code or ", ".join(r.missing_codes) or None
        lines.append(
            f"| `{r.case}` | {r.attempt} | {_cell(r.outcome)} | {_cell(r.verdict)} | {_cell(detail)} | {r.calls}"
            f" | {r.input_tokens:,} | {r.output_tokens:,} | {r.seconds} | {r.estimated_usd:.4f} | {_cell(r.error)} |"
        )
    lines += ["", "기대 결과 (ARCHITECTURE·PRD 수용 기준):", ""]
    lines += [f"- `{case}`: {expected}" for case, expected in EXPECTATIONS.items()]

    failed = [r for r in results if r.failed_checks]
    lines += ["", "## 검증기가 실패로 표시한 check", ""]
    if failed:
        lines += ["| 사례 | 회차 | 검증기 | 실패 check |", "|---|---|---|---|"]
        lines += [
            f"| `{r.case}` | {r.attempt} | {_cell(r.verdict)} | {', '.join(f'`{c}`' for c in r.failed_checks)} |"
            for r in failed
        ]
    else:
        lines.append("없음.")

    lines += ["", "## 도구 호출 순서 (조회 이력)", ""]
    for r in results:
        seq = " → ".join(f"`{c}`" for c in r.tool_calls) if r.tool_calls else "(호출 없음)"
        lines.append(f"- `{r.case}` {r.attempt}회차 (`{r.execution_id}`): {seq}")
    lines.append("")
    return "\n".join(lines)


# --- main ------------------------------------------------------------------------------


def _now_kst() -> str:
    return datetime.now(UTC).astimezone(KST).isoformat(timespec="seconds")


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default="all", help="all 또는 쉼표로 구분한 사례 이름")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR, help="실행별 DB·산출물·results.json 위치")
    args = parser.parse_args(argv)

    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        print(
            f"실호출을 시작하지 않습니다 (유료 호출 0회): 환경변수 없음 {', '.join(missing)}. "
            "ADR-0003 확정 조건 — 키·계정 API 사용 가능 여부·단가·평가 예산을 사용자가 확인해 넣은 뒤 재실행.",
            file=sys.stderr,
        )
        return 2
    names = list(CASES) if args.cases == "all" else [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = [c for c in names if c not in CASES]
    if unknown or not names:
        print(f"알 수 없는 사례: {', '.join(unknown) or '(없음)'} — 가능: {', '.join(CASES)}", file=sys.stderr)
        return 2

    # 평가는 항상 실제 모델이다. env 의 DIAG_MODEL=fake 가 있어도 따르지 않는다 (금지: fake 로 대신 돌려 통과라고 쓰지 마라)
    settings = load_settings({
        **env, "DIAG_MODEL": "openai", "DIAG_API_TOKEN": env.get("DIAG_API_TOKEN") or "unused-in-process",
    })
    started_at = _now_kst()
    workdir = args.workdir / started_at.replace(":", "").replace("+09:00", "")
    settings = replace(settings, db_path=workdir / "diag.sqlite", artifact_dir=workdir / "diag-artifacts")
    ledger = CostLedger(limit_usd=float(env["DIAG_EVAL_BUDGET_USD"]))

    import openai

    def model() -> ModelClient:
        from diagnostic_demo.worker.model import OpenAIModelClient

        # 키는 SDK 객체에만 넘긴다. 설정·로그·문서에 넣지 않는다
        return OpenAIModelClient(openai.OpenAI(api_key=settings.openai_api_key), settings.model_id)

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    log(f"평가 시작: model_id={settings.model_id} 사례={','.join(names)} × {args.repeat} 예산=US${ledger.limit_usd:g}"
        f" workdir={workdir}")
    results: list[CaseResult] = []
    for name in names:
        results += run_case(name, args.repeat, model=model, settings=settings, ledger=ledger, log=log)
    finished_at = _now_kst()

    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "results.json").write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render_markdown(results, settings=settings, ledger=ledger, repeat=args.repeat,
                        started_at=started_at, finished_at=finished_at),
        encoding="utf-8",
    )
    evaluation = evaluate(results, repeat=args.repeat)
    log(
        f"평가 끝: normal {evaluation.normal_passed}/{evaluation.normal_total} · 잘못된 수정 착수"
        f" {len(evaluation.false_handoffs)} · 총 US${ledger.spent_usd:.4f} · {'통과' if evaluation.passed else '미충족'}"
        f" → {args.out}"
    )
    return 0 if evaluation.passed else 1


if __name__ == "__main__":
    sys.exit(main())
