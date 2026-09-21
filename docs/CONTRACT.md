# 계약 v1 예시집

갱신일: 2026-09-21
상태: [ARCHITECTURE](ARCHITECTURE.md) 계약 v1의 필드 규칙을 완전한 예시로 옮긴 것. 구현 시 이 예시를 계약 테스트의 fixture로 그대로 사용한다. 식별자·해시·시각은 데모용 가상 값이며, 해시는 형식(SHA-256 소문자 64자리)만 맞춘 예시다. 규칙이 바뀌면 ARCHITECTURE와 이 파일을 함께 고친다.

공통: 모든 본문은 `contract_version: 1`. 알 수 없는 필드는 422. 시각은 시간대 있는 RFC 3339. 오류 본문은 `code`, `message`, `field`(없으면 null), `details`(없으면 null)를 가진다. HTTP 상태: 401 인증, 403 권한, 404 없음, 409 충돌·불가능한 전환, 422 필드 오류, 429 상한 도달.

## 1. 진단 실행 요청 — 중앙 워커 → 진단 API

`POST /runs`, `Authorization: Bearer <DIAG_API_TOKEN>`

```json
{
  "contract_version": 1,
  "execution_id": "exec-diagnose-001",
  "task_id": "diagnose-daily-0920",
  "kind": "diagnosis",
  "agent_id": "agent-ops-demo",
  "task_revision": 1,
  "request": "일일 보고서 생성 실패를 조사하고, 로컬 개발 에이전트가 재현·수정할 수 있도록 근거와 기대 동작을 정리해 주세요.",
  "input_artifact_ids": [],
  "target": { "run_id": "daily-0920-0900" }
}
```

새 접수 `202`:

```json
{
  "execution_id": "exec-diagnose-001",
  "status": "accepted",
  "last_event_seq": 1,
  "result_artifact_id": null,
  "error": null
}
```

같은 ID·같은 내용 재전송은 `200`과 현재 상태. 같은 ID·다른 내용은 `409`:

```json
{
  "code": "execution_conflict",
  "message": "execution_id exec-diagnose-001은 다른 내용으로 이미 접수되었습니다.",
  "field": "target.run_id",
  "details": null
}
```

상태 조회 `GET /runs/exec-diagnose-001?after_seq=1` → `200`. `events`는 `after_seq` 이후의 이벤트를 seq 순으로 담는다.

```json
{
  "execution_id": "exec-diagnose-001",
  "status": "running",
  "last_event_seq": 3,
  "result_artifact_id": null,
  "error": null,
  "events": [
    {
      "contract_version": 1,
      "execution_id": "exec-diagnose-001",
      "seq": 2,
      "occurred_at": "2026-09-20T00:10:01Z",
      "type": "started",
      "data": { "runtime_ref": "diag-run-7f3a" }
    },
    {
      "contract_version": 1,
      "execution_id": "exec-diagnose-001",
      "seq": 3,
      "occurred_at": "2026-09-20T00:10:04Z",
      "type": "progress",
      "data": { "message": "get_run daily-0920-0900 조회 완료" }
    }
  ]
}
```

## 2. 코드 수정 실행 — 연결 프로그램 claim

등록 보고 `POST /connector/registrations`, `Authorization: Bearer wfc_…`. 운영자가 미리 등록한 Agent 의 `local_registration_id` 와 맞춰 연결 정보를 채운다. `tool` 은 `codex` 또는 `claude` 이며 중앙은 값에 따라 분기하지 않는다 — 어댑터 선택은 연결 프로그램의 로컬 등록이 정한다. `discovered` 는 설정 존재 여부와 짧은 요약뿐이며 파일 전체·인증 파일·원격 URL 은 넣지 않는다.

```json
{
  "contract_version": 1,
  "connector_id": "conn-mac-01",
  "local_registration_id": "local-demo-report",
  "tool": "codex",
  "repository_id": "demo-report-repo",
  "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
  "verification_profile_ids": ["vp-pytest", "vp-report"],
  "discovered": { "found": { "AGENTS.md": "# demo-report-repo …", "codex_config": false, "tests_dir": true }, "not_read": ["CLAUDE.md"], "verification_level": "설정 발견" }
}
```

```json
{
  "contract_version": 1,
  "connector_id": "conn-mac-01",
  "local_registration_id": "local-demo-report-claude",
  "tool": "claude",
  "repository_id": "demo-report-repo",
  "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
  "verification_profile_ids": ["vp-pytest", "vp-report"],
  "discovered": { "found": { "AGENTS.md": "# demo-report-repo …", "codex_config": false, "tests_dir": true }, "not_read": ["CLAUDE.md"], "verification_level": "설정 발견" }
}
```

성공은 `200`과 `{ "agent_id": "agent-codex-mac" }`. `local_registration_id` 에 해당하는 Agent 가 없으면 `404 not_found`, `tool` 이 위 둘이 아니면 `422`.

`POST /connector/claim`, `Authorization: Bearer wfc_…`

```json
{ "contract_version": 1, "connector_id": "conn-mac-01" }
```

배정이 있으면 `200`. 없으면 `204`(본문 없음). 접수 확인 전의 같은 배정은 반복해서 같은 본문을 돌려준다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-fix-001",
  "task_id": "fix-daily-0920",
  "kind": "code_change",
  "agent_id": "agent-codex-mac",
  "task_revision": 1,
  "request": "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, 실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요.",
  "input_artifact_ids": ["art-handoff-001"],
  "target": {
    "local_registration_id": "local-demo-report",
    "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
    "verification_profile_id": "vp-pytest"
  }
}
```

`art-handoff-001`(kind `handoff_bundle`)은 다음 manifest다. `inputs` 는 내장 규칙 `handoff_kinds` [`diagnosis_result`, `evidence`] 로 모은 선행 실행의 산출물이며 `attachments` 에 이미 있는 근거 원문은 다시 넣지 않는다(11절). 연결 프로그램은 여기 나열된 산출물만 내려받을 수 있고, 저장소 옆 `<repo>-worktrees/fix-daily-0920.handoff/`에 풀어 경로를 Codex 프롬프트에 넣는다. worktree 안에는 쓰지 않는다.

```json
{
  "contract_version": 1,
  "source_execution_id": "exec-diagnose-001",
  "source_kind": "diagnosis",
  "source_result_artifact_id": "art-diag-result-001",
  "inputs": [
    { "kind": "diagnosis_result", "artifact_id": "art-diag-result-001", "sha256": "4a7d1e9b2c8f0e3a6b5d9c2f1e8a7b0d3c6f9e2a5b8d1c4f7e0a3b6d9c2f5e8a", "content_type": "application/json" }
  ],
  "attachments": [
    { "evidence_id": "response-before", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-003", "sha256": "5c1f0b4e9d2a7c6b8e3f1a0d4c9b2e7f6a1d8c3b0e5f2a9d7c4b1e8f3a6d0c5b" },
    { "evidence_id": "response-after", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-004", "sha256": "9e2b7d1c4f8a3e6b0d5c2f7a1e4b9c8d3f6a0b5e2d7c1f4a8b3e6d9c0f5a2b7e" },
    { "evidence_id": "log-daily-0920", "version": "1", "content_type": "text/plain", "artifact_id": "art-ev-005", "sha256": "2d8f4a1c7e0b3d6f9a2c5e8b1d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6c9e2b5d8f" }
  ]
}
```

## 3. 실행 이벤트 5종 — 연결 프로그램 → 중앙

`POST /executions/exec-fix-001/events`. 성공은 `200`과 `{ "execution_id", "last_event_seq", "status" }`.

```json
{ "contract_version": 1, "execution_id": "exec-fix-001", "seq": 1, "occurred_at": "2026-09-20T01:00:00+09:00", "type": "accepted", "data": {} }
```

```json
{ "contract_version": 1, "execution_id": "exec-fix-001", "seq": 2, "occurred_at": "2026-09-20T01:00:03+09:00", "type": "started", "data": { "runtime_ref": "pid:48213;start:2026-09-20T01:00:03+09:00" } }
```

```json
{ "contract_version": 1, "execution_id": "exec-fix-001", "seq": 3, "occurred_at": "2026-09-20T01:03:10+09:00", "type": "progress", "data": { "message": "재현 테스트 작성, 수정 전 실행 실패 확인" } }
```

```json
{ "contract_version": 1, "execution_id": "exec-fix-001", "seq": 4, "occurred_at": "2026-09-20T01:09:41+09:00", "type": "result_ready", "data": { "result_artifact_id": "art-fix-result-001" } }
```

실패 예 — 시간 초과 후 프로세스 종료를 확인한 경우:

```json
{ "contract_version": 1, "execution_id": "exec-fix-001", "seq": 4, "occurred_at": "2026-09-20T01:20:05+09:00", "type": "failed", "data": { "code": "timeout", "message": "Codex 실행이 20분을 초과해 종료했습니다.", "process_stopped": true } }
```

이벤트 오류:

| 상황 | HTTP | 본문 |
|---|---|---|
| 같은 seq·같은 내용 | 200 | 현재 상태. 다시 적용하지 않음 |
| 같은 seq·다른 내용 | 409 | `{ "code": "event_conflict", "message": "seq 3은 다른 내용으로 이미 저장되었습니다.", "field": "seq", "details": null }` |
| 순번 누락 (마지막 3, 받은 5) | 409 | `{ "code": "sequence_gap", "message": "seq 4가 먼저 필요합니다.", "field": "seq", "details": { "expected_seq": 4 } }` |
| 최종 상태 뒤 새 started | 409 | `{ "code": "invalid_transition", "message": "result_ready 상태에서는 started를 받을 수 없습니다.", "field": "type", "details": { "current_status": "result_ready" } }` |
| running 아닌데 progress | 409 | `invalid_transition`, `details.current_status` |
| 토큰 없음·만료·취소 | 401 | `{ "code": "unauthenticated", "message": "유효한 연결 토큰이 필요합니다.", "field": null, "details": null }` |
| 다른 프로그램에 배정된 실행 | 403 | `{ "code": "forbidden", "message": "exec-fix-001은 conn-mac-01에 배정되지 않았습니다.", "field": null, "details": null }` |
| 없는 실행 ID | 404 | `{ "code": "not_found", "message": "execution exec-none을 찾을 수 없습니다.", "field": "execution_id", "details": null }` |
| 지원하지 않는 계약 버전 | 422 | `{ "code": "unsupported_contract_version", "message": "contract_version 2는 지원하지 않습니다.", "field": "contract_version", "details": null }` |
| 알 수 없는 필드 | 422 | `{ "code": "unknown_field", "message": "필드 extra는 허용되지 않습니다.", "field": "extra", "details": null }` |

`result_ready`는 `result_artifact_id`가 이미 업로드·해시 확인된 뒤에만 200이다. 아직 없으면 `409 invalid_transition`, `details: { "reason": "result_artifact_missing" }`.

## 4. 산출물 업로드·다운로드

`POST /executions/{execution_id}/artifacts`, multipart: `meta`(JSON)와 `file`(바이트). 같은 실행에 같은 `kind`·`sha256`이 이미 있으면 기존 산출물을 `200`으로 돌려준다(재업로드 안전). 새 저장은 `201`.

```json
{ "contract_version": 1, "kind": "test_log_before", "name": "pytest-before.txt", "content_type": "text/plain", "sha256": "7b3e9a1d5c8f2b6e0a4d7c1f9e3b6a8d2c5f0e7b1a4d8c3f6e9b2a5d0c7f1e4b", "size": 2184 }
```

응답:

```json
{ "artifact_id": "art-test-before-001", "kind": "test_log_before", "sha256": "7b3e9a1d5c8f2b6e0a4d7c1f9e3b6a8d2c5f0e7b1a4d8c3f6e9b2a5d0c7f1e4b", "size": 2184 }
```

본문 해시가 `meta.sha256`과 다르면 `422 hash_mismatch`. 다운로드는 `GET /executions/{execution_id}/artifacts/{artifact_id}`이며, 해당 실행의 `input_artifact_ids`와 manifest에 나열된 것만 허용하고 나머지는 `403`.

산출물 `kind` 목록: `handoff_bundle`, `diagnosis_result`, `tool_trace`, `evidence`, `codex_jsonl`, `codex_stderr`, `diff`, `test_log_before`, `test_log_after`, `verification_log`, `report_output`, `code_change_result`, `review_comment`, `claude_jsonl`, `claude_stderr`, `generic_result`.

로컬 도구의 원시 로그 산출물 — 생산자와 내용:

| kind | 생산자 | 내용 |
|---|---|---|
| `codex_jsonl` / `codex_stderr` | 연결 프로그램 Codex 어댑터 | `codex exec --json` 의 원문 stdout(JSONL) / stderr. 업로드 전 `wfc_`·`sk-` 마스킹 적용 |
| `claude_jsonl` / `claude_stderr` | 연결 프로그램 Claude 어댑터 | `claude -p --output-format json` 의 원문 stdout / stderr. 같은 마스킹 적용 |
| `generic_result` | 연결 프로그램 | 사용자 정의 종류의 결과 봉투(`GenericResult`, 11절). 내장 종류의 `diagnosis_result`·`code_change_result` 와 구분 |

## 5. 진단 결과 — `ready_for_handoff` 전체

`art-diag-result-001`(kind `diagnosis_result`). 근거 문서는 `{ "markdown": …, "machine": … }` 형태의 JSON이며 참조 위치는 `$.machine.*`를 가리킨다. 문서의 Markdown 본문은 v1에서 줄 단위로 인용하지 않는다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-diagnose-001",
  "task_id": "diagnose-daily-0920",
  "run_id": "daily-0920-0900",
  "outcome": "ready_for_handoff",
  "summary": "조회는 성공했으나, 제공자가 목록 위치를 items에서 data.records로 옮긴 뒤 기존 변환부가 items만 읽어 보고서 생성이 중단되었습니다.",
  "findings": [
    {
      "claim": "실패 실행과 직전 정상 실행은 같은 코드 버전 report-base에서 실행되었습니다.",
      "evidence_refs": [
        { "evidence_id": "run-daily-0919-0900", "version": "1", "location": "$.code_version" },
        { "evidence_id": "run-daily-0920-0900", "version": "1", "location": "$.code_version" }
      ]
    },
    {
      "claim": "정상 응답의 목록은 items에 있고, 실패 응답의 목록은 data.records에 있으며 items는 없습니다.",
      "evidence_refs": [
        { "evidence_id": "response-before", "version": "1", "location": "$.items" },
        { "evidence_id": "response-after", "version": "1", "location": "$.data.records" }
      ]
    },
    {
      "claim": "실패 실행은 HTTP 200으로 응답을 받은 뒤 변환 단계에서 MISSING_RECORDS_FIELD로 실패했고 보고서를 만들지 않았습니다.",
      "evidence_refs": [
        { "evidence_id": "log-daily-0920", "version": "1", "location": "lines:1-2" },
        { "evidence_id": "log-daily-0920", "version": "1", "location": "lines:4-4" }
      ]
    },
    {
      "claim": "제공자 안내는 2026-09-20 00:00+09:00부터 목록 위치를 items에서 data.records로 옮기고 행 필드는 유지한다고 명시합니다.",
      "evidence_refs": [
        { "evidence_id": "upstream-response-change", "version": "1", "location": "$.machine.effective_at" },
        { "evidence_id": "upstream-response-change", "version": "1", "location": "$.machine.new_path" }
      ]
    },
    {
      "claim": "보고서 계약은 두 경로를 모두 지원하고 목록 누락은 오류로 처리하도록 요구합니다.",
      "evidence_refs": [
        { "evidence_id": "daily-report-contract", "version": "1", "location": "$.machine.supported_paths" },
        { "evidence_id": "daily-report-contract", "version": "1", "location": "$.machine.empty_list_policy" }
      ]
    }
  ],
  "diagnosis": {
    "code": "response_path_changed",
    "baseline_run_id": "daily-0919-0900",
    "failed_run_id": "daily-0920-0900",
    "old_path": "$.items",
    "new_path": "$.data.records",
    "change_document": { "evidence_id": "upstream-response-change", "version": "1" },
    "report_contract": { "evidence_id": "daily-report-contract", "version": "1" }
  },
  "repair_request": {
    "target_component": "report_transformer",
    "change": "items와 data.records 중 정확히 하나에 있는 목록을 처리합니다.",
    "preserve": "날짜·행 순서·건수·합계 계산과 과거 응답(items) 재처리를 유지합니다.",
    "checks": [
      "변경 응답으로 수정 전 실패를 재현하는 테스트가 먼저 실패함",
      "수정 후 변경 응답에서 2026-09-19 보고서가 생성되고 합계가 입력 행에서 계산한 값과 일치함",
      "구형 응답과 빈 배열·누락·두 경로 동시 존재 사례가 계약대로 처리됨"
    ]
  },
  "missing_information": [],
  "attachments": [
    { "evidence_id": "run-daily-0919-0900", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-001", "sha256": "1a4d7c0f3e6b9a2d5c8f1e4b7a0d3c6f9e2b5a8d1c4f7e0b3a6d9c2f5e8b1a4d" },
    { "evidence_id": "run-daily-0920-0900", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-002", "sha256": "8c2f5b9e1d4a7c0f3e6b9d2a5c8f1e4b7a0d3c6f9e2b5a8d1c4f7e0b3a6d9c2f" },
    { "evidence_id": "response-before", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-003", "sha256": "5c1f0b4e9d2a7c6b8e3f1a0d4c9b2e7f6a1d8c3b0e5f2a9d7c4b1e8f3a6d0c5b" },
    { "evidence_id": "response-after", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-004", "sha256": "9e2b7d1c4f8a3e6b0d5c2f7a1e4b9c8d3f6a0b5e2d7c1f4a8b3e6d9c0f5a2b7e" },
    { "evidence_id": "log-daily-0920", "version": "1", "content_type": "text/plain", "artifact_id": "art-ev-005", "sha256": "2d8f4a1c7e0b3d6f9a2c5e8b1d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6c9e2b5d8f" },
    { "evidence_id": "upstream-response-change", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-006", "sha256": "6f0a3d7c1e4b8f2a5d9c0e3b6f1a4d7c2e5b8f0a3d6c9e1b4f7a0d3c6e9b2f5a" },
    { "evidence_id": "daily-report-contract", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-007", "sha256": "3b6e9c2f5a8d1e4b7c0f3a6d9e2b5c8f1a4d7e0b3c6f9a2d5e8b1c4f7a0d3e6b" },
    { "evidence_id": "daily-report-runbook", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-008", "sha256": "0d3a6c9f2e5b8a1d4c7f0e3b6a9d2c5f8e1b4a7d0c3f6e9b2a5d8c1f4e7b0a3d" }
  ],
  "provenance": {
    "model_id": "gpt-4.1-mini-2025-04-14",
    "prompt_version": "diag-prompt-v3",
    "tool_contract_version": "tools-v1",
    "tool_trace_artifact_id": "art-trace-001"
  }
}
```

검증기가 확인하는 것: `attachments`의 모든 항목이 `art-trace-001`의 조회 이력에 있고, `evidence_refs`의 모든 `evidence_id@version`이 `attachments`에 있으며, 각 `location`이 해당 첨부 원문에 실제로 존재하고, `diagnosis`의 경로·실행·문서가 첨부 내용과 일치한다.

## 6. 진단 결과 — `needs_information` 전체

변경 안내 문서 조회가 `access_denied`였던 경우. 읽지 않은 문서는 `findings`·`attachments`에 없다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-diagnose-002",
  "task_id": "diagnose-daily-0920",
  "run_id": "daily-0920-0900",
  "outcome": "needs_information",
  "summary": "응답의 목록 위치 차이(items → data.records)는 확인했으나, 제공자 변경 안내를 읽지 못해 새 경로의 의미와 적용 시각을 확인할 수 없습니다. 형식 변경 가능성이 있으나 새 입력 계약 확인이 필요합니다.",
  "findings": [
    {
      "claim": "실패 실행과 직전 정상 실행은 같은 코드 버전 report-base에서 실행되었습니다.",
      "evidence_refs": [
        { "evidence_id": "run-daily-0919-0900", "version": "1", "location": "$.code_version" },
        { "evidence_id": "run-daily-0920-0900", "version": "1", "location": "$.code_version" }
      ]
    },
    {
      "claim": "정상 응답의 목록은 items에 있고, 실패 응답의 목록은 data.records에 있으며 items는 없습니다.",
      "evidence_refs": [
        { "evidence_id": "response-before", "version": "1", "location": "$.items" },
        { "evidence_id": "response-after", "version": "1", "location": "$.data.records" }
      ]
    },
    {
      "claim": "실패 실행은 HTTP 200으로 응답을 받은 뒤 변환 단계에서 MISSING_RECORDS_FIELD로 실패했습니다.",
      "evidence_refs": [
        { "evidence_id": "log-daily-0920", "version": "1", "location": "lines:1-2" }
      ]
    }
  ],
  "diagnosis": null,
  "repair_request": null,
  "missing_information": [
    {
      "code": "evidence_unavailable",
      "description": "제공자 변경 안내(upstream-response-change) 조회가 access_denied로 실패해 새 경로의 의미·적용 시각·유지 필드를 확인할 수 없습니다.",
      "evidence_id": "upstream-response-change"
    }
  ],
  "attachments": [
    { "evidence_id": "run-daily-0919-0900", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-011", "sha256": "1a4d7c0f3e6b9a2d5c8f1e4b7a0d3c6f9e2b5a8d1c4f7e0b3a6d9c2f5e8b1a4d" },
    { "evidence_id": "run-daily-0920-0900", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-012", "sha256": "8c2f5b9e1d4a7c0f3e6b9d2a5c8f1e4b7a0d3c6f9e2b5a8d1c4f7e0b3a6d9c2f" },
    { "evidence_id": "response-before", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-013", "sha256": "5c1f0b4e9d2a7c6b8e3f1a0d4c9b2e7f6a1d8c3b0e5f2a9d7c4b1e8f3a6d0c5b" },
    { "evidence_id": "response-after", "version": "1", "content_type": "application/json", "artifact_id": "art-ev-014", "sha256": "9e2b7d1c4f8a3e6b0d5c2f7a1e4b9c8d3f6a0b5e2d7c1f4a8b3e6d9c0f5a2b7e" },
    { "evidence_id": "log-daily-0920", "version": "1", "content_type": "text/plain", "artifact_id": "art-ev-015", "sha256": "2d8f4a1c7e0b3d6f9a2c5e8b1d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6c9e2b5d8f" }
  ],
  "provenance": {
    "model_id": "gpt-4.1-mini-2025-04-14",
    "prompt_version": "diag-prompt-v3",
    "tool_contract_version": "tools-v1",
    "tool_trace_artifact_id": "art-trace-002"
  }
}
```

`missing_information.code`: `evidence_unavailable`(누락·접근 거절·일시 오류), `evidence_conflict`(예: 변경 안내의 적용 시각이 실패 실행보다 뒤), `unsupported_diagnosis`(`response_path_changed`가 아닌 원인).

## 7. 코드 수정 결과 — `ready_for_review`

`art-fix-result-001`(kind `code_change_result`). `verification`은 연결 프로그램이 검증 프로필을 별도로 실행한 뒤 채운다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-fix-001",
  "task_id": "fix-daily-0920",
  "outcome": "ready_for_review",
  "summary": "report_transformer가 items 또는 data.records 중 정확히 하나의 목록을 읽도록 수정했습니다. 변경 응답 재현 테스트 1건을 추가해 수정 전 실패를 확인했고, 수정 후 전체 테스트가 통과합니다.",
  "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
  "result_commit": "9b7e4d2c1a0f8e6d5c3b2a1f0e9d8c7b6a5f4e3d",
  "artifact_ids": ["art-diff-001", "art-test-before-001", "art-test-after-001", "art-report-001", "art-codex-jsonl-001", "art-codex-stderr-001"],
  "verification": {
    "profile_id": "vp-pytest",
    "result_commit": "9b7e4d2c1a0f8e6d5c3b2a1f0e9d8c7b6a5f4e3d",
    "exit_code": 0,
    "log_artifact_id": "art-verify-001"
  }
}
```

정상 제출의 필수 산출물: `diff`, `test_log_before`(종료 코드 0이 아님), `test_log_after`, `report_output`, `verification_log`. 하나라도 없으면 중앙은 결과를 채택하지 않고 확인 필요로 둔다.

보류 제출 예 — 기준 커밋에서 대상을 찾지 못한 경우:

```json
{
  "contract_version": 1,
  "execution_id": "exec-fix-001",
  "task_id": "fix-daily-0920",
  "outcome": "needs_information",
  "summary": "기준 커밋 3f9c2e1a에서 응답 변환부를 찾지 못했습니다. 인계 자료의 report_transformer에 해당하는 모듈 경로 확인이 필요합니다.",
  "base_commit": "3f9c2e1a7b0d4c6e8f1a2b3c4d5e6f7a8b9c0d1e",
  "result_commit": null,
  "artifact_ids": ["art-codex-jsonl-002", "art-codex-stderr-002"],
  "verification": null
}
```

## 8. 검토 수정 요청 — 새 시도

검토자가 수정 요청을 하면 중앙은 `attempt_no 2`의 실행을 만든다. 입력에 이전 결과와 검토 의견이 추가되고 `base_commit`은 이전 시도의 `result_commit`이다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-fix-002",
  "task_id": "fix-daily-0920",
  "kind": "code_change",
  "agent_id": "agent-codex-mac",
  "task_revision": 1,
  "request": "인계된 진단 근거로 보고서 변환 실패를 재현하는 테스트를 먼저 작성하고, 실패를 확인한 뒤 두 응답 형식을 모두 처리하도록 최소 수정하세요.",
  "input_artifact_ids": ["art-handoff-001", "art-fix-result-001", "art-review-001"],
  "target": {
    "local_registration_id": "local-demo-report",
    "base_commit": "9b7e4d2c1a0f8e6d5c3b2a1f0e9d8c7b6a5f4e3d",
    "verification_profile_id": "vp-pytest"
  }
}
```

`art-review-001`(kind `review_comment`):

```json
{ "contract_version": 1, "task_id": "fix-daily-0920", "reviewed_execution_id": "exec-fix-001", "decision": "request_changes", "comment": "두 경로가 동시에 있는 응답을 오류로 처리하는 테스트가 없습니다. 추가해 주세요.", "created_at": "2026-09-20T02:15:00+09:00" }
```

## 9. 자동 선택 기록

중앙이 Task마다 저장하고 화면 이유 표시에 그대로 쓴다.

```json
{
  "task_id": "diagnose-daily-0920",
  "mode": "auto",
  "required_capability": { "code": "operations.diagnose", "scope": { "workflow_id": "daily-report" } },
  "candidate_count": 1,
  "selected_agent_id": "agent-ops-demo",
  "matched": { "code": "operations.diagnose", "scope": { "workflow_id": "daily-report" } },
  "status": "selected",
  "reason": "operations.diagnose · workflow_id=daily-report 일치 후보 1개"
}
```

후보가 없거나 여럿이면 `selected_agent_id: null`, `status: "needs_selection"`, `reason`에 "후보 없음" 또는 "후보 2개 — 선택 필요"를 적는다.

## 10. 상한 도달

`POST /tasks/{id}/run` 등 진단을 새로 만드는 요청이 세션·일일·총액 상한에 걸리면 `429`:

```json
{ "code": "daily_limit_reached", "message": "오늘 이 세션의 진단 실행 한도(10회)에 도달했습니다.", "field": null, "details": { "limit": 10, "resets_at": "2026-09-22T00:00:00+09:00" } }
```

총액 상한은 `code: "budget_exhausted"`, `details: { "estimated_usd": 27.3, "limit_usd": 30 }`.

## 11. 업무 종류·후속 규칙·범용 결과

[ARCHITECTURE](ARCHITECTURE.md) "업무 종류와 후속 규칙", 결정은 [ADR-0009](adr/0009-registered-kinds-and-succession-rules.md). 종류·규칙은 워크스페이스별 등록이며 내장(`builtin: true`)은 세션 생성 시 seed 된다.

### 11.1 `KindSpec`

내장 `code_change`:

```json
{
  "kind": "code_change",
  "label": "코드 수정",
  "capability_code": "code.modify",
  "scope_key": "repository_id",
  "input_kinds": ["diagnosis_result", "evidence"],
  "output_kind": "code_change_result",
  "outcomes": ["ready_for_review", "needs_information"],
  "instructions": "",
  "builtin": true
}
```

사용자 정의 `review` — diff 와 코드 수정 결과를 받아 검토 의견을 낸다. `output_kind` 는 항상 `generic_result`, 완료는 사람 검토:

```json
{
  "kind": "review",
  "label": "검토",
  "capability_code": "review",
  "scope_key": "repository_id",
  "input_kinds": ["diff", "code_change_result"],
  "output_kind": "generic_result",
  "outcomes": ["approved", "changes_requested", "needs_information"],
  "instructions": "인계 디렉터리의 diff 와 code_change_result 를 읽고 변경이 진단의 repair_request 를 충족하는지, 테스트가 무력화되지 않았는지 검토하세요. 저장소를 수정하지 마세요. 마지막 메시지는 {\"outcome\": \"approved\" | \"changes_requested\" | \"needs_information\", \"summary\": \"…\"} JSON 하나입니다.",
  "builtin": false
}
```

`kind` 는 `^[a-z][a-z0-9_]{1,39}$`, `capability_code` 는 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$`. 이미 있는 `kind` 재등록·내장 삭제는 `409`, `input_kinds` 에 4절 목록 밖의 값·`outcomes` 중복·사용자 정의인데 `output_kind` 가 `generic_result` 가 아니면 `422`.

### 11.2 `SuccessorRule`

내장 규칙(`BUILTIN_RULES`):

```json
{ "from_kind": "diagnosis", "on_outcomes": ["ready_for_handoff"], "to_kind": "code_change", "handoff_kinds": ["diagnosis_result", "evidence"] }
```

`code_change → review` 규칙. `handoff_kinds` 는 `review.input_kinds` [`diff`, `code_change_result`] 를 모두 포함하고 `test_log_after` 를 더 넘긴다:

```json
{ "from_kind": "code_change", "on_outcomes": ["ready_for_review"], "to_kind": "review", "handoff_kinds": ["diff", "code_change_result", "test_log_after"] }
```

`on_outcomes` 가 `from_kind.outcomes` 밖이거나 `handoff_kinds` 가 `to_kind.input_kinds` 를 빠뜨리면 `422`. `from_kind`·`to_kind` 가 이 워크스페이스에 없으면 `422`.

### 11.3 `GenericResult` — `review` 의 `approved`

`art-review-result-001`(kind `generic_result`). 중앙은 `outcome ∈ KindSpec.outcomes` 만 판정한다. `artifact_ids` 는 도구의 원시 로그다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-review-001",
  "task_id": "review-daily-0920",
  "kind": "review",
  "outcome": "approved",
  "summary": "diff 는 report_transformer 가 items 또는 data.records 중 하나를 읽도록 하는 최소 변경이며 repair_request 의 preserve 항목을 유지합니다. 추가된 재현 테스트는 수정 전 실패·수정 후 통과가 test_log_after 에서 확인됩니다.",
  "artifact_ids": ["art-claude-jsonl-003", "art-claude-stderr-003"]
}
```

`outcome` 이 `outcomes` 에 없으면 결과를 채택하지 않고 확인 필요, 이유 "허용되지 않은 outcome".

### 11.4 일반화된 `HandoffBundle` — `code_change → review`

`art-handoff-002`(kind `handoff_bundle`). `inputs` 는 규칙 `handoff_kinds` 로 모은 선행 실행(`exec-fix-001`)의 산출물이고, 근거 원문 `attachments` 는 진단 결과에서만 채워지므로 빈 배열이다. 2절의 진단 인계 예시와 같은 구조다.

```json
{
  "contract_version": 1,
  "source_execution_id": "exec-fix-001",
  "source_kind": "code_change",
  "source_result_artifact_id": "art-fix-result-001",
  "inputs": [
    { "kind": "diff", "artifact_id": "art-diff-001", "sha256": "c3e8a1f6d9b2e5c0a7d4f1b8e3c6a9d2f5b0e7c4a1d8f3b6e9c2a5d0f7b4e1c8", "content_type": "text/x-diff" },
    { "kind": "code_change_result", "artifact_id": "art-fix-result-001", "sha256": "e1b4c7d0a3f6e9b2c5d8a1f4e7b0c3d6a9f2e5b8c1d4a7f0e3b6c9d2a5f8e1b4", "content_type": "application/json" },
    { "kind": "test_log_after", "artifact_id": "art-test-after-001", "sha256": "a9d2f5b8e1c4a7d0f3b6e9c2a5d8f1b4e7c0a3d6f9b2e5c8a1d4f7b0e3c6a9d2", "content_type": "text/plain" }
  ],
  "attachments": []
}
```

### 11.5 `ExecutionRequest` — `kind: "review"`

내장이 아닌 종류의 target 은 `LocalTarget`(`local_registration_id` 하나)이고 `kind_spec` 은 서버가 등록부에서 채운다(`kind_spec.kind == kind`, `builtin: false`). worktree·`base_commit`·`verification_profile_id` 가 없다 — 읽기 전용 실행이며 작업 위치는 인계 디렉터리다.

```json
{
  "contract_version": 1,
  "execution_id": "exec-review-001",
  "task_id": "review-daily-0920",
  "kind": "review",
  "agent_id": "agent-claude-mac",
  "task_revision": 1,
  "request": "인계된 diff 와 코드 수정 결과를 검토하고 승인 여부를 판단해 주세요.",
  "input_artifact_ids": ["art-handoff-002"],
  "target": { "local_registration_id": "local-demo-report-claude" },
  "kind_spec": {
    "kind": "review",
    "label": "검토",
    "capability_code": "review",
    "scope_key": "repository_id",
    "input_kinds": ["diff", "code_change_result"],
    "output_kind": "generic_result",
    "outcomes": ["approved", "changes_requested", "needs_information"],
    "instructions": "인계 디렉터리의 diff 와 code_change_result 를 읽고 변경이 진단의 repair_request 를 충족하는지, 테스트가 무력화되지 않았는지 검토하세요. 저장소를 수정하지 마세요. 마지막 메시지는 {\"outcome\": \"approved\" | \"changes_requested\" | \"needs_information\", \"summary\": \"…\"} JSON 하나입니다.",
    "builtin": false
  }
}
```

`kind` 가 `diagnosis`·`code_change` 인 요청은 1·2절과 같고 `kind_spec` 은 `null` 이어도 된다. 그 외 `kind` 인데 `kind_spec` 이 없거나 `kind_spec.kind != kind` 이거나 `builtin: true` 이면 `422`.
