# ADR-0009: 업무 종류와 후속 규칙은 워크스페이스가 등록한다 — 흐름을 그리지 않는다

결정일: 2026-09-21. 사용자 확정. 적용 범위는 제품(`src/workflow/`)의 셀프호스트 실사용이며, 공개 데모(VM, [ADR-0008](0008-public-demo-scripted-agents.md))는 심사 기간 동안 건드리지 않는다.

**결정**:

1. 업무 종류는 `KindSpec` 봉투로 정의하고 워크스페이스(세션)별 DB 에 등록한다. 필드는 `kind`(식별자, `^[a-z][a-z0-9_]{1,39}$`) · `label`(화면 표시) · `capability_code`(에이전트 능력 코드, `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$`) · `scope_key`(능력 scope 의 키 하나) · `input_kinds`(시작할 때 받아야 하는 산출물 kind 목록, `ARTIFACT_KINDS` 부분집합, 빈 목록 허용) · `output_kind`(`diagnosis_result` | `code_change_result` | `generic_result`) · `outcomes`(허용 outcome 식별자 목록, 1개 이상, 중복 없음) · `instructions`(에이전트 지시문 — 내장은 빈 문자열) · `builtin` 이다. 내장 종류 2개(`BUILTIN_KINDS`)는 `diagnosis`(`operations.diagnose` · `workflow_id` · input 없음 · `diagnosis_result` · [`ready_for_handoff`, `needs_information`])와 `code_change`(`code.modify` · `repository_id` · input [`diagnosis_result`, `evidence`] · `code_change_result` · [`ready_for_review`, `needs_information`])이며, 검증기·실행 흐름이 코드에 있고 삭제할 수 없다. 사용자 정의 종류는 `output_kind` 가 항상 `generic_result` 이고 완료는 항상 사람 검토이며, 로컬 도구(Codex·Claude)가 `LocalTarget`(`local_registration_id` 하나)으로 **읽기 전용** 수행한다 — worktree·커밋·검증 프로필 없이 인계 디렉터리에서 읽고 `{outcome, summary}` 를 낸다. 세션이 생길 때 내장 종류와 내장 규칙을 seed 한다.
2. 종류 사이 연결은 `SuccessorRule` 행(`from_kind` · `on_outcomes` · `to_kind` · `handoff_kinds`)으로 워크스페이스별로 등록한다. `on_outcomes` 는 `from_kind.outcomes` 의 부분집합이고 `handoff_kinds` 는 `to_kind.input_kinds` 를 모두 포함해야 한다. 내장 규칙 1개(`BUILTIN_RULES`)는 `diagnosis` --[`ready_for_handoff`]--> `code_change`, handoff [`diagnosis_result`, `evidence`] 다. 그래프·DAG·pipeline 개념을 도입하지 않는다 — 순서는 Task 의 `predecessor_task_id` 뿐이고, 규칙은 "이 결과 다음에 무엇을 넘겨 무엇을 시작하는가"만 말한다. 흐름은 규칙 표를 반복 적용한 결과다.
3. 후속 착수 조건은 선행 Task 의 `완료` 가 아니라 **선행 결과 + 판정 통과 + outcome 일치**다. 선행 실행이 `result_ready` 이고 판정(`task_verdicts`)이 `passed` 이며 결과 봉투의 `outcome` 이 규칙 `on_outcomes` 에 있으면 착수한다. 사람 승인은 선행 Task 를 마감할 뿐 후속 착수를 막지 않는다. 사람이 선행을 종료(close)하면 후속을 새로 착수하지 않는다(이미 시작한 것은 계속).
4. 규칙에 없는 결과·outcome 은 착수하지 않고 이유를 남겨 확인 필요로 둔다. 중앙은 여전히 LLM 을 부르지 않는다([ADR-0004](0004-central-service-rule-based-no-llm.md) 유지) — 규칙 일치는 등록된 값의 명시적 비교다.
5. 종류의 내용은 정의하지 않는다 — 봉투(입력 kind·출력 kind·outcome 목록)만 고정하고 내용은 에이전트가 쓴다. 받는 쪽이 LLM 이라 읽으면 된다. 중앙은 사용자 정의 종류의 결과에서 `outcome ∈ KindSpec.outcomes` 만 판정한다.

**이유**: 원래 문제는 착수 대기다 — A 가 끝나 B 를 시작할 수 있어도 사람이 전달·실행할 때까지 후속이 밀린다. 흐름은 진행돼 봐야 아는 경우가 많아 미리 그리는 방식(n8n 식 노드·엣지, LLM 이 흐름 JSON 생성)은 예측이 틀리고 분기가 폭발한다. 입출력 봉투를 정형화하면 매 단계 결정이 "방금 나온 출력이 이 형태면 이 형태를 받는 다음 작업을 만든다" 하나로 줄고, 흐름은 그 반복의 결과가 된다. 규칙이라 같은 출력엔 항상 같은 다음 작업이고, 이유가 남고, 형태가 안 맞으면 멈춘다. 병목은 개인보다 팀에서 크므로 종류·규칙을 코드가 아니라 등록 데이터(DB + 화면)로 둔다. 착수 조건을 결과로 바꾸는 이유: 코드 수정의 `완료` 는 사람 승인이라, 선행 `완료` 를 기다리면 "에이전트 검토가 사람 승인보다 먼저" 장면이 성립하지 않았다.

**트레이드오프**:
- 사용자 정의 종류는 자동 완료 검증기가 없어 항상 사람 검토다. 자동 완료는 내장 `diagnosis` 의 `response_path_changed` 판정만 그대로다.
- API 에이전트(진단 API)는 이번에도 `diagnosis` 만 받는다. 범용 API 계약은 다음 ADR 이다.
- 완료 시 새 업무를 **생성**하는 규칙(대상·범위를 선행 결과에서 파생해야 한다)은 이 phase 밖이다. 미리 등록된 업무 사이를 잇는 것만 한다.
- 사람이 선행을 종료해도 이미 시작한 후속은 계속된다. 후속을 멈추려면 후속 Task 를 따로 종료한다.
- `HandoffBundle` 의 이전 이름 `diagnosis_result_artifact_id` 가 `source_result_artifact_id` 로 바뀌고 `source_kind`·`inputs` 가 추가되므로 계약 v1 안에서 필드명이 바뀐다. 공개 배포 전이라 버전을 올리지 않으며 DB 는 스키마 버전으로 재생성한다.

**참고 — Astera(parsingk/Astera)·n8n 과의 차이**: "A 끝나면 B 자동"만으로는 차이가 없다. 차이는 "안 그려도 흐른다 + 넘어갈 때 검증한다"다. n8n 은 업무가 들어오는 입구·나가는 출구로만 쓴다(다음 phase).
