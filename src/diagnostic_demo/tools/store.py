"""진단 데모의 가상 자료 저장소 — PRD "읽기 전용 조회 도구 4개", ARCHITECTURE "API 에이전트의 자료 범위".

도구는 사실 자료만 돌려준다. 판단은 모델이 한다. 파일 I/O 는 이 모듈에만 있다.

- 범위 밖 `workflow_id`·`run_id` 는 빈 결과가 아니라 `access_denied` 다.
- 개별 자료 조회 실패는 `not_found`·`access_denied`·`unavailable` 로 구분하고 빈 본문으로 바꾸지 않는다.
- `removed`·`replaced`·`unavailable` 은 평가(Step 17)·테스트가 자료 누락·충돌·일시 오류를 재현할 때 쓴다.
  fixture 파일은 고치지 않는다.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from workflow.contracts.v1 import EvidenceVersion

EvidenceKey = tuple[str, str]  # (evidence_id, version)

# 실행 기록이 다른 자료를 가리키는 필드. 이 자료들은 실행의 workflow 범위에 속한다.
_RUN_REF_FIELDS = ("response_ref", "log_ref", "report_ref")
_LIST_RUN_FIELDS = ("run_id", "started_at", "status", "code_version")


class ToolError(str, Enum):
    not_found = "not_found"
    access_denied = "access_denied"
    unavailable = "unavailable"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: Any | None  # JSON 은 dict/list, 텍스트는 str
    content_type: str | None
    error: ToolError | None
    returned: tuple[EvidenceVersion, ...]  # 이 호출이 실제로 반환한 근거. 목록 조회는 빈 튜플


def _failure(error: ToolError) -> ToolResult:
    return ToolResult(ok=False, content=None, content_type=None, error=error, returned=())


def _aware(value: str, what: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{what} 에 시간대가 없습니다: {value!r}")
    return parsed


def _ref_key(value: Any) -> EvidenceKey | None:
    if isinstance(value, dict) and isinstance(value.get("evidence_id"), str) and isinstance(
        value.get("version"), str
    ):
        return (value["evidence_id"], value["version"])
    return None


class FixtureStore:
    def __init__(
        self,
        root: Path,
        allowed_workflow_ids: frozenset[str],
        removed: frozenset[EvidenceKey] = frozenset(),
        replaced: Mapping[EvidenceKey, bytes] | None = None,
        unavailable: frozenset[EvidenceKey] = frozenset(),
    ) -> None:
        self._root = Path(root)
        self._allowed = frozenset(allowed_workflow_ids)
        self._removed = frozenset(removed)
        self._replaced = dict(replaced or {})
        self._unavailable = frozenset(unavailable)

        index = json.loads((self._root / "index.json").read_text(encoding="utf-8"))
        self._content_types: dict[str, str] = dict(index["content_types"])
        self._runs: dict[str, dict[str, Any]] = {run["run_id"]: run for run in index["runs"]}
        self._documents: list[dict[str, Any]] = list(index["documents"])
        self._scope = self._build_scope()

    # --- 범위 -------------------------------------------------------------------

    def _build_scope(self) -> dict[EvidenceKey, str]:
        """(evidence_id, version) → workflow_id. 실행 기록·문서는 index 에서, 응답·로그·보고서는 실행 기록의 참조에서."""
        scope: dict[EvidenceKey, str] = {}
        for doc in self._documents:
            scope[(doc["evidence_id"], doc["version"])] = doc["workflow_id"]
        for run in self._runs.values():
            key = _ref_key(run["evidence"])
            if key is None:
                raise ValueError(f"index.runs[{run['run_id']!r}].evidence 가 evidence 참조가 아닙니다")
            scope[key] = run["workflow_id"]
            # 교체된 기록과 원본 기록이 가리키는 자료를 모두 같은 범위로 둔다 (교체는 범위를 바꾸지 않는다)
            for raw in filter(None, (self._replaced.get(key), self._disk_bytes(key))):
                record = json.loads(raw)
                for field in _RUN_REF_FIELDS:
                    ref = _ref_key(record.get(field)) if isinstance(record, dict) else None
                    if ref is not None:
                        scope.setdefault(ref, run["workflow_id"])
        return scope

    def _allowed_workflow(self, workflow_id: str) -> bool:
        return workflow_id in self._allowed

    # --- 파일 -------------------------------------------------------------------

    def _path_of(self, key: EvidenceKey) -> Path | None:
        matches = sorted((self._root / "evidence" / key[0]).glob(f"{key[1]}.*"))
        return matches[0] if matches else None

    def _disk_bytes(self, key: EvidenceKey) -> bytes | None:
        path = self._path_of(key)
        return path.read_bytes() if path is not None else None

    def _content_type_of(self, key: EvidenceKey) -> str | None:
        path = self._path_of(key)
        if path is None:
            return None
        return self._content_types.get(path.suffix.lstrip("."))

    def raw_bytes(self, evidence_id: str, version: str) -> bytes:
        """저장된 원문 바이트 (교체본이 있으면 교체본). 첨부 조립용이며 범위 검사는 하지 않는다."""
        key = (evidence_id, version)
        if key in self._removed:
            raise FileNotFoundError(f"{evidence_id}@{version} 은 제거된 자료입니다")
        if key in self._replaced:
            return self._replaced[key]
        data = self._disk_bytes(key)
        if data is None:
            raise FileNotFoundError(f"{evidence_id}@{version} 파일이 없습니다")
        return data

    def sha256_of(self, evidence_id: str, version: str) -> str:
        return hashlib.sha256(self.raw_bytes(evidence_id, version)).hexdigest()

    def _read(self, key: EvidenceKey) -> ToolResult:
        """범위·가용성 검사 후 원문을 파싱해 돌려준다. 실패 종류는 구분하고 빈 본문으로 바꾸지 않는다."""
        workflow_id = self._scope.get(key)
        if workflow_id is None or key in self._removed:
            return _failure(ToolError.not_found)
        if not self._allowed_workflow(workflow_id):
            return _failure(ToolError.access_denied)
        if key in self._unavailable:
            return _failure(ToolError.unavailable)
        content_type = self._content_type_of(key)
        if content_type is None:
            return _failure(ToolError.not_found)
        raw = self.raw_bytes(*key)
        content: Any = json.loads(raw) if content_type == "application/json" else raw.decode("utf-8")
        return ToolResult(
            ok=True,
            content=content,
            content_type=content_type,
            error=None,
            returned=(EvidenceVersion(evidence_id=key[0], version=key[1]),),
        )

    # --- 도구 4개 ---------------------------------------------------------------

    def get_run(self, run_id: str) -> ToolResult:
        run = self._runs.get(run_id)
        if run is None:
            return _failure(ToolError.not_found)
        if not self._allowed_workflow(run["workflow_id"]):
            return _failure(ToolError.access_denied)
        return self._read((run["evidence"]["evidence_id"], run["evidence"]["version"]))

    def list_runs(
        self, workflow_id: str, before: str | None, status: str | None, limit: int = 10
    ) -> ToolResult:
        if not self._allowed_workflow(workflow_id):
            return _failure(ToolError.access_denied)
        cutoff = _aware(before, "before") if before is not None else None
        runs = [run for run in self._runs.values() if run["workflow_id"] == workflow_id]
        if cutoff is not None:
            runs = [run for run in runs if _aware(run["started_at"], "started_at") < cutoff]
        if status is not None:
            runs = [run for run in runs if run["status"] == status]
        runs.sort(key=lambda run: _aware(run["started_at"], "started_at"), reverse=True)
        summaries = [{field: run[field] for field in _LIST_RUN_FIELDS} for run in runs[:limit]]
        return ToolResult(
            ok=True, content=summaries, content_type="application/json", error=None, returned=()
        )

    def list_documents(self, workflow_id: str) -> ToolResult:
        if not self._allowed_workflow(workflow_id):
            return _failure(ToolError.access_denied)
        documents = [dict(doc) for doc in self._documents if doc["workflow_id"] == workflow_id]
        return ToolResult(
            ok=True, content=documents, content_type="application/json", error=None, returned=()
        )

    def read_evidence(self, evidence_id: str, version: str) -> ToolResult:
        return self._read((evidence_id, version))
