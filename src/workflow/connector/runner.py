"""연결 프로그램의 실행 루프 — claim → 접수 → 어댑터 선택 → 인계 자료 → 어댑터 → 산출물 업로드 → result_ready|failed.

규칙 (ARCHITECTURE "상태·재접속·완료", CONTRACT 3절):
- 모든 이벤트는 로컬 `pending_events` 에 먼저 쓰고 보낸다. 중앙에 닿지 않으면(`Unreachable`) 다음 tick 에
  같은 seq 로 다시 보낸다. `sequence_gap` 이면 중앙이 요구한 순번부터 다시 보낸다.
- 재시작 후 `launching`/`running` 인 실행은 프로세스 동일성을 확인할 수 없으므로 아무 이벤트도 보내지 않고
  `unknown_local_at` 만 적어 사람 확인을 기다린다 (중앙은 2분 규칙으로 `unknown`). 재실행하지 않는다.
- 어댑터가 돌려준 산출물은 로컬 DB 에 보존한 뒤 업로드한다. 업로드 중 끊겨도 어댑터를 다시 돌리지 않는다.
- 산출물·진행 메시지·오류 메시지는 `mask_secrets` 를 거친다. 연결 토큰은 이 모듈이 알지 못한다 (client 안).
- 어댑터는 도구 이름 → 어댑터 매핑이며, 실행마다 `target.local_registration_id` 로 찾은 로컬 등록의 `tool` 로
  하나를 고른다 (`select_adapter`). 요청 본문의 값으로 실행 대상을 고르지 않는다.
- 종료 이벤트(result_ready·failed)를 중앙이 받은 뒤 worktree·인계 디렉터리를 지운다 (`_cleanup_workdirs`). 결과는
  `task/{task_id}` 브랜치의 커밋으로 남아 있다. 지우는 경로는 등록의 repo 와 task_id 로 계산한 것뿐이다.
"""

import hashlib
import json
import logging
import re
import shutil
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from workflow.connector import git_ops, state
from workflow.connector.adapter import AdapterOutput, ExecutionAdapter
from workflow.connector.client import (
    CentralClient,
    CentralError,
    EventConflictError,
    SequenceGapError,
    Unauthenticated,
    Unreachable,
)
from workflow.connector.config import ConnectorPaths
from workflow.connector.git_ops import GitError
from workflow.connector.masking import mask_secrets
from workflow.contracts.v1 import (
    CONTRACT_VERSION,
    ArtifactMeta,
    CodeChangeResult,
    CodeChangeTarget,
    ExecutionEvent,
    ExecutionRequest,
    HandoffBundle,
)

log = logging.getLogger(__name__)

_EXTENSIONS = {"application/json": "json", "text/plain": "txt", "text/markdown": "md"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
MESSAGE_MAX = 500


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _extension(content_type: str) -> str:
    return _EXTENSIONS.get(content_type.split(";")[0].strip().lower(), "bin")


def _safe(name: str) -> str:
    # 근거 ID·버전은 불투명 문자열이다. 파일 이름으로만 쓰고 경로로 해석하지 않는다.
    return _SAFE_NAME.sub("_", name) or "_"


def _masked_text(text: str) -> str:
    return mask_secrets(text)[0][:MESSAGE_MAX]


def _process_stopped(row: dict) -> bool:
    """실패 기록의 process_stopped. 실패가 아니거나 프로세스를 띄우기 전의 실패(failed_json 없음)는 True."""
    if not row["failed_json"]:
        return True
    return bool(json.loads(row["failed_json"])[2])


class HandoffHashMismatch(Exception):
    pass


class AdapterNotSelected(Exception):
    """요청을 실행할 어댑터를 로컬 등록에서 고르지 못했다. `failed` 이벤트의 code·message 가 된다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def select_adapter(
    conn, adapters: Mapping[str, ExecutionAdapter], request: ExecutionRequest
) -> ExecutionAdapter:
    """`target.local_registration_id` 의 로컬 등록이 가진 `tool` 로 어댑터를 고른다.
    등록 없음 → `registration_missing`, 그 도구의 어댑터 없음 → `adapter_missing`.
    로컬 등록이 없는 종류(진단)는 지금처럼 첫 어댑터에 넘긴다 — 어댑터가 `unsupported_kind` 로 답한다."""
    target = request.target
    if not isinstance(target, CodeChangeTarget):
        return next(iter(adapters.values()))
    registration = state.get_registration(conn, target.local_registration_id)
    if registration is None:
        raise AdapterNotSelected("registration_missing", f"로컬 등록 {target.local_registration_id} 이 없다")
    adapter = adapters.get(registration["tool"])
    if adapter is None:
        raise AdapterNotSelected(
            "adapter_missing",
            f"등록 {target.local_registration_id} 의 도구 {registration['tool']} 어댑터가 없다 "
            f"(있는 것: {', '.join(adapters) or '없음'})",
        )
    return adapter


class Runner:
    def __init__(
        self,
        client: CentralClient,
        state_conn,
        paths: ConnectorPaths,
        adapters: Mapping[str, ExecutionAdapter],
        connector_id: str,
        clock: Callable[[], str],
        handoff_root: Path,
        keep_workdirs: bool = False,
    ):
        self._client = client
        self._conn = state_conn
        self._paths = paths
        self._adapters = adapters
        self._connector_id = connector_id
        self._clock = clock
        self._handoff_root = handoff_root
        self._keep_workdirs = keep_workdirs  # 디버깅용 — worktree·인계 디렉터리를 남긴다
        self._heartbeat_interval = 30.0
        self._last_heartbeat: float | None = None

    # --- 루프 ------------------------------------------------------------------------------

    def tick(self) -> None:
        try:
            self._flush_all()
            self._heartbeat_if_due()
            active = state.active_execution(self._conn)
            if active is None:
                self._claim_and_start()
            else:
                self._continue(active)
        except Unreachable as exc:
            log.warning("중앙에 닿지 않음 — 다음 tick 에 재시도: %s", exc)
        except Unauthenticated:
            log.error("연결 토큰이 거부됐다 (401). 운영자가 연결을 취소했을 수 있다 — 다시 connect 필요")

    def run_forever(self, claim_interval: float = 5.0, heartbeat_interval: float = 30.0) -> None:
        self._heartbeat_interval = heartbeat_interval
        while True:
            try:
                self.tick()
            except Exception:  # 한 바퀴의 예외로 프로그램을 죽이지 않는다 (launchd 재시작보다 다음 바퀴가 빠르다)
                log.exception("tick 실패 — 다음 바퀴에 재시도")
            time.sleep(claim_interval)

    def _heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if self._last_heartbeat is not None and now - self._last_heartbeat < self._heartbeat_interval:
            return
        active = state.active_execution(self._conn)
        current = active["execution_id"] if active and active["finished_at"] is None else None
        self._client.heartbeat(self._connector_id, current)
        self._last_heartbeat = now

    # --- 이벤트 -------------------------------------------------------------------------------

    def _emit(self, execution_id: str, type_: str, data: dict, **fields) -> None:
        """seq 발급·큐 저장·실행 필드 갱신을 한 트랜잭션에서. 전송은 곧바로 시도하되 끊기면 보류한다."""
        with state.transaction(self._conn):
            seq = state.next_seq(self._conn, execution_id)
            event = ExecutionEvent.model_validate({
                "contract_version": CONTRACT_VERSION, "execution_id": execution_id, "seq": seq,
                "occurred_at": self._clock(), "type": type_, "data": data,
            })
            state.queue_event(self._conn, event)
            if fields:
                state.update_execution(self._conn, execution_id, **fields)
        try:
            self._flush(execution_id)
        except Unreachable as exc:
            log.warning("이벤트 보류 (%s seq %s): %s", type_, seq, exc)

    def _flush_all(self) -> None:
        for execution_id in state.pending_execution_ids(self._conn):
            self._flush(execution_id)

    def _flush(self, execution_id: str) -> None:
        """미전송 이벤트를 seq 순으로 보낸다. 같은 seq 200 은 ack. gap 이면 요구 순번부터 다시.
        gap 외 4xx 는 중앙이 이미 판단한 것이라 재전송해도 같으므로 기록만 남기고 ack 한다."""
        resent_from: int | None = None
        while pending := state.pop_pending(self._conn, execution_id):
            event = pending[0]
            try:
                self._client.post_event(event)
            except SequenceGapError as gap:
                expected = gap.expected_seq
                if expected >= event.seq or expected == resent_from:
                    raise
                if state.unack_from(self._conn, execution_id, expected) == 0:
                    raise
                log.warning("중앙이 seq %s 부터 요구 — 재전송 (보내던 seq %s)", expected, event.seq)
                resent_from = expected
                continue
            except EventConflictError as exc:
                log.error("seq %s 충돌 — 중앙에 다른 내용이 있다. 이 이벤트는 폐기: %s", event.seq, exc)
            except Unauthenticated:
                raise
            except CentralError as exc:
                log.error("이벤트 %s seq %s 거부 — 폐기: %s", event.type, event.seq, exc)
            state.ack_event(self._conn, execution_id, event.seq, self._clock())
        self._cleanup_if_delivered(execution_id)

    # --- 실행 ---------------------------------------------------------------------------------

    def _claim_and_start(self) -> None:
        request = self._client.claim(self._connector_id)
        if request is None:
            return
        state.record_claim(self._conn, request, self._clock())  # 로컬 접수 기록이 먼저
        self._start(state.get_execution(self._conn, request.execution_id))

    def _continue(self, active: dict) -> None:
        phase = active["phase"]
        if phase == "finished":
            if active["finished_at"] is None:
                self._finalize(active)
            return  # 미전송만 남은 경우는 `_flush_all` 이 처리했다
        if phase == "accepted":
            self._start(active)
            return
        if active["unknown_local_at"] is None:  # launching / running — 재시작 뒤 프로세스 동일성 확인 불가
            state.update_execution(self._conn, active["execution_id"], unknown_local_at=self._clock())
            log.error(
                "%s: 이전 실행이 %s 단계에서 끊겼다. 시작 여부 불명 — 재실행하지 않는다. 사람 확인 필요",
                active["execution_id"], phase,
            )

    def _start(self, row: dict) -> None:
        request = ExecutionRequest.model_validate_json(row["request_json"])
        execution_id = request.execution_id
        if row["next_seq"] == 1:
            self._emit(execution_id, "accepted", {})

        try:
            adapter = select_adapter(self._conn, self._adapters, request)
        except AdapterNotSelected as exc:  # 시작 전 실패 — 프로세스를 띄우지 않았으므로 process_stopped=True
            self._finish_failed(execution_id, (exc.code, exc.message, True))
            return

        handoff_dir = self._handoff_dir(request)
        try:
            self._download_handoff(request, handoff_dir)
        except HandoffHashMismatch as exc:
            self._finish_failed(execution_id, ("handoff_hash_mismatch", str(exc), True))
            return

        state.set_phase(self._conn, execution_id, "launching", handoff_dir=str(handoff_dir))
        started = row["runtime_ref"] is not None

        def progress(message: str, *, runtime_ref: str | None = None) -> None:
            nonlocal started
            if runtime_ref is not None and not started:
                started = True
                self._emit(execution_id, "started", {"runtime_ref": runtime_ref}, runtime_ref=runtime_ref)
                state.set_phase(self._conn, execution_id, "running")
                log.info("%s 시작 확인 %s: %s", execution_id, runtime_ref, _masked_text(message))
                return  # 시작 알림은 started 이벤트가 그 기록이다
            if not started:
                log.info("%s (시작 전 메시지, 중앙에 보내지 않음): %s", execution_id, _masked_text(message))
                return
            if message:
                self._emit(execution_id, "progress", {"message": _masked_text(message)})

        try:
            output = adapter.run(request, handoff_dir, progress)
        except Exception as exc:  # 어댑터 예외 — 프로세스 종료를 확인하지 못했으므로 process_stopped=False
            log.exception("%s: 어댑터 예외", execution_id)
            output = AdapterOutput(
                result=None, failed=("adapter_error", _masked_text(f"{type(exc).__name__}: {exc}"), False),
            )

        if output.failed is None and output.result is not None and not started:
            # 어댑터가 runtime_ref 를 콜백으로 주지 않았다 — 반환값으로 started 를 보낸다
            self._emit(execution_id, "started", {"runtime_ref": output.runtime_ref or "unknown"},
                       runtime_ref=output.runtime_ref or "unknown")
        with state.transaction(self._conn):
            state.save_outputs(self._conn, execution_id, output.artifacts)
            state.set_phase(
                self._conn, execution_id, "finished",
                result_json=output.result.model_dump_json() if output.result else None,
                failed_json=json.dumps(output.failed) if output.failed else None,
            )
        self._finalize(state.get_execution(self._conn, execution_id))

    def _finalize(self, row: dict) -> None:
        """어댑터가 끝난 실행의 업로드와 종료 이벤트. 끊기면(`Unreachable`) 다음 tick 에 남은 업로드부터 잇는다."""
        execution_id = row["execution_id"]
        if row["failed_json"]:
            self._upload_outputs(row)  # 실패 산출물(stderr 등)도 보존한다
            self._finish_failed(execution_id, tuple(json.loads(row["failed_json"])))
            return

        uploaded = self._upload_outputs(row)
        by_kind: dict[str, str] = {}
        for output in uploaded:
            by_kind.setdefault(output["meta"].kind, output["artifact_id"])
        result = CodeChangeResult.model_validate_json(row["result_json"])
        verification = result.verification
        if verification is not None and "verification_log" in by_kind:
            verification = verification.model_copy(update={"log_artifact_id": by_kind["verification_log"]})
        result = CodeChangeResult.model_validate({
            **result.model_dump(),
            "artifact_ids": [o["artifact_id"] for o in uploaded],
            "verification": None if verification is None else verification.model_dump(),
        })
        data = result.model_dump_json(indent=2).encode()
        meta = ArtifactMeta(
            contract_version=CONTRACT_VERSION, kind="code_change_result", name="code_change_result.json",
            content_type="application/json", sha256=hashlib.sha256(data).hexdigest(), size=len(data),
        )
        created = self._client.upload_artifact(execution_id, meta, data)
        self._emit(  # 중앙이 받으면 `_flush` 끝의 `_cleanup_if_delivered` 가 작업 디렉터리를 지운다
            execution_id, "result_ready", {"result_artifact_id": created.artifact_id},
            finished_at=self._clock(),
        )

    def _finish_failed(self, execution_id: str, failed: tuple[str, str, bool]) -> None:
        code, message, stopped = failed
        self._emit(  # 중앙이 받으면 정리 — 단 process_stopped=False 면 `_cleanup_workdirs` 가 건너뛴다
            execution_id, "failed",
            {"code": code, "message": _masked_text(message), "process_stopped": bool(stopped)},
            finished_at=self._clock(),
        )
        state.set_phase(self._conn, execution_id, "finished")

    def _upload_outputs(self, row: dict) -> list[dict]:
        """보존된 산출물 중 아직 업로드하지 않은 것을 마스킹해 올린다. 재업로드는 서버가 같은 ID 를 돌려준다."""
        execution_id = row["execution_id"]
        outputs = state.list_outputs(self._conn, execution_id)
        for output in outputs:
            if output["artifact_id"] is not None:
                continue
            meta, data = self._masked(execution_id, output["meta"], output["data"], row["runtime_ref"] is not None)
            created = self._client.upload_artifact(execution_id, meta, data)
            state.set_output_artifact(self._conn, execution_id, output["idx"], created.artifact_id)
            output["artifact_id"] = created.artifact_id
        return outputs

    def _masked(self, execution_id: str, meta: ArtifactMeta, data: bytes, running: bool) -> tuple[ArtifactMeta, bytes]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return meta, data  # 이진 산출물은 그대로
        masked, count = mask_secrets(text)
        if count == 0:
            return meta, data
        data = masked.encode("utf-8")
        log.warning("%s: 산출물 %s 에서 비밀값 %d건 마스킹", execution_id, meta.name, count)
        if running:
            self._emit(execution_id, "progress", {"message": f"비밀값 마스킹: {meta.name} {count}건"})
        meta = meta.model_copy(update={"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        return meta, data

    # --- 작업 디렉터리 정리 ----------------------------------------------------------------------

    def _cleanup_if_delivered(self, execution_id: str) -> None:
        """미전송 이벤트가 없고 종료 이벤트를 큐에 넣은 실행이면 한 번만 정리한다 (`_flush` 가 다 보낸 직후)."""
        row = state.get_execution(self._conn, execution_id)
        if row is None or row["finished_at"] is None or row["cleaned_at"] is not None:
            return
        self._cleanup_workdirs(row)

    def _cleanup_workdirs(self, row: dict) -> None:
        """결과가 중앙에 닿은 뒤 worktree·인계 디렉터리를 지운다. 브랜치 `task/{task_id}` 와 결과 커밋은 남긴다.
        지우는 경로는 등록의 repo 와 task_id 로 계산한 것뿐이다. 프로세스 종료를 확인하지 못한 실패(process_stopped
        False)는 살아 있는 프로세스의 cwd 일 수 있어 지우지 않는다. 실패는 경고만 남기고 실행 결과에 영향을 주지 않는다.
        이미 없는 디렉터리는 조용히 지나가며, 다 지웠을 때만 `cleaned_at` 을 적는다."""
        if self._keep_workdirs or not _process_stopped(row):
            return
        execution_id = row["execution_id"]
        request = ExecutionRequest.model_validate_json(row["request_json"])
        repo = self._registered_repo(request)
        cleaned = True
        if repo is not None:
            worktree = git_ops.worktree_path(repo, request.task_id)
            if worktree.exists():
                try:
                    git_ops.remove_worktree(repo, worktree)
                except GitError as exc:
                    cleaned = False
                    log.warning("%s: worktree 정리 실패 (%s): %s", execution_id, worktree, exc)
        handoff_dir = self._handoff_dir(request)
        if handoff_dir.exists():
            try:
                shutil.rmtree(handoff_dir)
            except OSError as exc:
                cleaned = False
                log.warning("%s: 인계 디렉터리 정리 실패 (%s): %s", execution_id, handoff_dir, exc)
        if repo is not None and repo.is_dir():
            try:
                git_ops.prune_worktrees(repo)
            except GitError as exc:
                log.warning("%s: worktree prune 실패 (%s): %s", execution_id, repo, exc)
        if cleaned:
            state.update_execution(self._conn, execution_id, cleaned_at=self._clock())

    # --- 인계 자료 -----------------------------------------------------------------------------

    def _registered_repo(self, request: ExecutionRequest) -> Path | None:
        """`target.local_registration_id` 의 로컬 등록이 가리키는 저장소 경로. 등록이 없거나 코드 수정이 아니면 None."""
        if not isinstance(request.target, CodeChangeTarget):
            return None
        registration = state.get_registration(self._conn, request.target.local_registration_id)
        return None if registration is None else Path(registration["repo_path"])

    def _handoff_dir(self, request: ExecutionRequest) -> Path:
        """`<repo>-worktrees/<task_id>.handoff/` (등록이 있으면). 없으면 handoff_root 아래. worktree 밖이다."""
        root = self._handoff_root
        repo = self._registered_repo(request)
        if repo is not None:
            root = repo.parent / f"{repo.name}-worktrees"
        return root / f"{_safe(request.task_id)}.handoff"

    def _download_handoff(self, request: ExecutionRequest, handoff_dir: Path) -> None:
        """입력 산출물 중 handoff_bundle manifest 와 그 attachments 만 내려받아 해시를 확인하고 저장한다.
        파일명 `{evidence_id}@{version}.{ext}`, manifest 는 `manifest.json`. 다른 입력은 `input-{artifact_id}.{ext}`."""
        handoff_dir.mkdir(parents=True, exist_ok=True)
        for artifact_id in request.input_artifact_ids:
            data, content_type = self._client.download_artifact(request.execution_id, artifact_id)
            try:
                bundle = HandoffBundle.model_validate_json(data)
            except ValidationError:
                (handoff_dir / f"input-{_safe(artifact_id)}.{_extension(content_type)}").write_bytes(data)
                continue
            (handoff_dir / "manifest.json").write_bytes(data)
            for attachment in bundle.attachments:
                blob, _ = self._client.download_artifact(request.execution_id, attachment.artifact_id)
                digest = hashlib.sha256(blob).hexdigest()
                if digest != attachment.sha256:
                    raise HandoffHashMismatch(
                        f"{attachment.evidence_id}@{attachment.version}: manifest 해시 {attachment.sha256[:12]}… "
                        f"실제 {digest[:12]}…"
                    )
                name = f"{_safe(attachment.evidence_id)}@{_safe(attachment.version)}.{_extension(attachment.content_type)}"
                (handoff_dir / name).write_bytes(blob)
