# Step 6: web-kinds-page

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`
- `/docs/adr/0009-registered-kinds-and-succession-rules.md`, `/docs/adr/0005-access-model-anonymous-session-operator-token.md`
- `/docs/ARCHITECTURE.md` "업무 종류와 후속 규칙"(화면), `/docs/UI_GUIDE.md` 전부 (앱 셸·컴포넌트·색상·"하지 마라"), `/docs/GLOSSARY.md`
- `/src/workflow/contracts/v1.py` — `KindSpec`·`SuccessorRule`·`ARTIFACT_KINDS`·`KIND_PATTERN`·`IDENTIFIER_PATTERN`
- `/src/workflow/domain/kinds.py` (`validate_rule`, `get_kind`), `/src/workflow/adapters/repo.py` (`list_kinds`·`insert_kind`·`delete_kind`·`list_rules`·`insert_rule`·`delete_rule` 과 예외 `KindProtected`·`KindInUse`·`DuplicateKind`·`DuplicateRule`)
- `/src/workflow/server/web.py` — 라우트 스타일(`router`, `require_session`, `get_conn`, `PageError`, `_render`, `_redirect`, `_base`), `/agents/register` 라우트 두 개(GET/POST)와 `agents_unregister`(409 `agent_in_use`) 를 본보기로
- `/src/workflow/server/templates/_sidebar.html`, `agents_register.html`, `base.html`, `/src/workflow/server/static/style.css`
- `/src/workflow/server/errors.py` (`PageError` 필드·422/409 형식)
- `/tests/workflow/server/test_web.py`(등록 라우트 테스트 스타일), `test_ui.py`(셸·사이드바 검사), `conftest.py`

이전 step 에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 이 phase 의 개념 (모든 step 공통 — 이 절은 step 파일마다 같다)

이 phase 는 "업무 종류 2개(`diagnosis`·`code_change`)와 인계 쌍 1개(진단 → 코드 수정)가 코드에 박힌 상태"를
"종류·후속 규칙을 워크스페이스(세션)가 **등록**하는 상태"로 바꾼다. 흐름을 그리지 않는다 — 규칙 표를 반복 적용한
결과가 흐름이다 (ADR-0009, step 0 이 쓴다). 팀 사용을 전제하므로 종류·규칙은 코드가 아니라 DB + 화면이다.

- `KindSpec` (계약, `contracts/v1.py`): 업무 종류의 **봉투**. 필드:
  `kind`(식별자, `^[a-z][a-z0-9_]{1,39}$`) · `label`(화면 표시) · `capability_code`(에이전트 능력 코드, `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$`) ·
  `scope_key`(능력 scope 의 키 하나, 식별자) · `input_kinds`(시작할 때 받아야 하는 산출물 kind 목록, `ARTIFACT_KINDS` 부분집합, 빈 목록 허용) ·
  `output_kind`(`diagnosis_result` | `code_change_result` | `generic_result`) · `outcomes`(허용 outcome 식별자 목록, 1개 이상, 중복 없음) ·
  `instructions`(에이전트 지시문 — 내장은 빈 문자열) · `builtin`(내장 여부).
  종류의 **내용**(검토 의견이 어떻게 생겼는지 등)은 정의하지 않는다. 중앙은 봉투만 본다.
- 내장 종류 2개 `BUILTIN_KINDS` (계약 상수):
  `diagnosis` = 진단 · `operations.diagnose` · `workflow_id` · input 없음 · `diagnosis_result` · [`ready_for_handoff`, `needs_information`];
  `code_change` = 코드 수정 · `code.modify` · `repository_id` · input [`diagnosis_result`, `evidence`] · `code_change_result` · [`ready_for_review`, `needs_information`].
  내장은 검증기·실행 흐름이 코드에 있고 삭제할 수 없다. 사용자 정의 종류는 `output_kind` 가 항상 `generic_result` 이고 완료는 항상 사람 검토다.
- `SuccessorRule` (계약): `from_kind` · `on_outcomes`(선행 결과의 outcome 이 이 중 하나면 잇는다, `from_kind.outcomes` 부분집합) · `to_kind` ·
  `handoff_kinds`(선행 실행의 산출물 중 넘길 kind 목록; `to_kind.input_kinds` 를 모두 포함해야 한다).
  내장 규칙 1개 `BUILTIN_RULES`: `diagnosis` --[`ready_for_handoff`]--> `code_change`, handoff [`diagnosis_result`, `evidence`].
- `GenericResult` (계약): 내장이 아닌 종류의 결과 봉투 — `contract_version` · `execution_id` · `task_id` · `kind` · `outcome` · `summary` · `artifact_ids`.
  산출물 kind 는 `generic_result` (`ARTIFACT_KINDS` 에 추가). 중앙은 `outcome ∈ KindSpec.outcomes` 만 판정하고 완료는 사람이 한다.
- `LocalTarget` (계약): 내장이 아닌 종류를 로컬 도구(Codex·Claude)가 수행할 때의 target — `local_registration_id` 하나.
  worktree·커밋·검증 프로필 없음 — **읽기 전용 실행**이며 작업 위치는 인계 디렉터리다.
- `InputRef` (계약): 인계 묶음의 입력 항목 — `kind` · `artifact_id` · `sha256` · `content_type`.
- `HandoffBundle` (계약, 일반화): `source_execution_id` · `source_kind` · `source_result_artifact_id`(이전 이름 `diagnosis_result_artifact_id`) ·
  `inputs: list[InputRef]`(규칙 `handoff_kinds` 로 모은 산출물) · `attachments`(근거 원문 `evidence_id@version` — 기존과 같음, 진단 결과에서만 채워진다).
- `ExecutionRequest.kind` 는 식별자 문자열이고 `kind_spec: KindSpec | None` 을 갖는다(서버가 채운다). target 은 `diagnosis` → `DiagnosisTarget`,
  `code_change` → `CodeChangeTarget`, 그 외 → `LocalTarget`(이때 `kind_spec` 필수, `kind_spec.kind == kind`, `builtin=False`).
- 종류·규칙은 **워크스페이스별**: `kinds(session_id, kind)` · `succession_rules(session_id, from_kind, to_kind)`. 세션이 생길 때(`repo.create_session`)
  내장 종류 2개 + 내장 규칙 1개를 seed 한다. `Capability.code` 는 패턴만 계약이 검사하고, "코드가 어느 종류의 `capability_code` 인가 · scope 키가 그 종류의
  `scope_key` 인가"는 서버가 등록부로 검사한다(422).
- **후속 착수 조건이 바뀐다**: 이전엔 선행 Task 가 `완료` 여야 했다. 이제는 선행 실행이 `result_ready` 이고 판정(`task_verdicts`)이 `passed` 이며
  결과 봉투의 `outcome` 이 규칙 `on_outcomes` 에 있으면 착수한다. 사람 승인은 선행 Task 를 마감할 뿐 후속 착수를 막지 않는다
  (그래서 "에이전트 검토가 사람 승인보다 먼저" 가 가능하다). 사람이 선행을 종료(close)하면 후속을 새로 착수하지 않는다(이미 시작한 것은 계속).
  규칙에 없는 결과·outcome 은 착수하지 않고 이유를 남긴다(확인 필요). 중앙은 LLM 을 부르지 않는다(ADR-0004).
- 증명 기준(step 8): e2e 가 화면으로 종류 `review`(diff·code_change_result 를 받아 `approved`/`changes_requested`/`needs_information` 을 냄)와
  규칙 `code_change --[ready_for_review]--> review` 를 등록하면, 진단 → 수정 → 검토가 `composition.py`·`worker.py` 를 고치지 않고 자동 착수한다.

## 작업

웹 계층에 "업무 종류·규칙" 페이지 하나를 추가한다. 업무 등록 폼·가져오기·상세 화면의 종류 일반화는 **step 7** 이다 — 여기서는 새 페이지와 사이드바 링크만.

### 라우트 — `src/workflow/server/web.py`

```python
@router.get("/kinds")                      # 페이지: 종류 목록 + 종류 등록 폼 + 규칙 목록 + 규칙 등록 폼
@router.post("/kinds")                     # 종류 등록 → 302 /kinds
@router.post("/kinds/{kind}/delete")       # 종류 삭제 → 302 /kinds
@router.post("/rules")                     # 규칙 등록 → 302 /kinds
@router.post("/rules/{rule_id}/delete")    # 규칙 삭제 → 302 /kinds
```

모두 `require_session`(익명 세션 = 워크스페이스). 운영자 전용이 아니다.

`POST /kinds` 폼 필드: `kind`, `label`, `capability_code`(비어 있으면 `kind` 와 같게), `scope_key`, `input_kinds`(다중 선택, `ARTIFACT_KINDS` 중 `handoff_bundle` 제외), `outcomes`(쉼표·공백 구분 텍스트 → 목록), `instructions`(textarea). `output_kind="generic_result"`, `builtin=False` 는 서버가 채운다. 검증: `KindSpec.model_validate` 의 `ValidationError` → 422 `invalid_field`(첫 오류의 필드명, 사람이 읽는 문구); `DuplicateKind` → 409 `kind_exists`.

`POST /rules` 폼 필드: `from_kind`, `on_outcomes`(다중 선택 — 화면은 `from_kind` 의 outcomes 를 보여 준다; 서버는 `validate_rule` 로 확인), `to_kind`, `handoff_kinds`(다중 선택). 검증: `SuccessorRule.model_validate` → 422; `domain.kinds.validate_rule(list_kinds(...), rule)` 사유가 있으면 422 `invalid_field` 로 그 문구; `DuplicateRule` → 409 `rule_exists`.

삭제: `KindProtected` → 409 `kind_protected`("내장 종류는 삭제할 수 없습니다"), `KindInUse` → 409 `kind_in_use`(사유 문구에 Task 인지 규칙인지), `NotFound` → 404.

### 템플릿 — `src/workflow/server/templates/kinds.html` (신규)

`agents_register.html` 의 구조(제목·설명 한 줄·카드 목록·폼)를 따른다. 가운데 열 순서:

1. **업무 종류** — 카드 하나에 종류 하나: 라벨(큰 글씨) · `kind` · 능력 코드 · scope 키 · 받는 산출물 칩 · 내는 산출물 · outcome 칩 · 지시문(접힘/요약) · 내장이면 태그 "내장" 과 삭제 버튼 없음, 아니면 삭제 버튼(`form method=post`).
2. **종류 등록** 폼 — 위 필드. `instructions` 에는 placeholder 로 "에이전트가 무엇을 읽고 무엇을 판단해 어떤 근거로 답할지" 한 줄.
3. **후속 규칙** — 행 하나에 규칙 하나: `{from label} --[outcome, …]--> {to label}` · 넘기는 산출물 칩 · 삭제 버튼. 내장 규칙도 삭제 가능(설명 한 줄: "규칙이 없으면 그 결과 뒤 후속은 사람이 시작합니다").
4. **규칙 등록** 폼 — `from_kind`·`to_kind` 는 select(등록된 종류), `on_outcomes`·`handoff_kinds` 는 체크박스. `from_kind` 를 바꾸면 outcome 체크박스가 그 종류 것으로 바뀌는 소량 JS(데이터는 `data-outcomes` 속성으로 서버가 넣는다 — 인라인 스크립트는 base.html 의 기존 방식 따름).

사이드바 `nav` 에 `<a href="/kinds">종류·규칙</a>` 를 "에이전트" 다음에 추가(활성 표시 규칙 동일). `style.css` 에 필요한 최소 클래스만(칩은 기존 `.tag`/`.chip` 재사용).

UI_GUIDE "하지 마라"를 지킨다: 모달 없음, 그래프·화살표 그림 없음(규칙은 **한 줄 텍스트**), 용어는 GLOSSARY 라벨(화면 문구 "업무 종류"·"후속 규칙"·"받는 산출물"·"내는 산출물"·"결과값").

### `views.py`

`kind_public(spec: KindSpec) -> dict` (템플릿용: 라벨·칩 목록·`builtin`), `rule_public(rule_id, rule, kinds) -> dict` (라벨 치환한 한 줄 문구 포함). 산출물 kind 의 한글 라벨은 `filters.KIND_LABELS` 를 쓰고 `generic_result: "결과 봉투"` 를 추가한다.

### 테스트 (먼저 작성) — `tests/workflow/server/test_web.py`, `test_ui.py`, `test_views.py`

- GET `/kinds` 가 내장 2종("내장" 태그, 삭제 버튼 없음)과 내장 규칙 1개를 보여 준다; 다른 세션의 종류는 보이지 않는다.
- POST `/kinds` 정상(`review` 개념 절 값) → 302 후 목록에 나타남, `capability_code` 비우면 `kind` 와 같음; 패턴 위반 422(필드명); 중복 409; 내장 이름으로 등록 시도 409.
- POST `/rules` 정상(`code_change → review`, handoff `diff`·`code_change_result`) → 302 후 한 줄 문구; `on_outcomes` 가 `from_kind` 밖 → 422 문구; `handoff_kinds` 가 `to_kind.input_kinds` 를 못 덮음 → 422 문구; 중복 409; 없는 종류 422/404.
- 삭제: 내장 종류 409 `kind_protected`; Task 가 쓰는 종류 409 `kind_in_use`; 규칙이 참조하는 종류 409; 규칙 삭제 후 종류 삭제 성공; 규칙 삭제 정상·없음 404.
- `test_ui.py`: 사이드바에 "종류·규칙" 링크, 활성 표시, 3열 셸 유지.
- `test_views.py`: `kind_public`·`rule_public` 문구.

## Acceptance Criteria

```bash
python3 -m pytest tests/workflow/server -q
python3 -m pytest -q
python3 -m ruff check .
grep -n '"/kinds"\|"/rules"' src/workflow/server/web.py
grep -n 'href="/kinds"' src/workflow/server/templates/_sidebar.html
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 검증은 계약 모델 + `domain.kinds.validate_rule` 로 하고 웹에 규칙 논리를 새로 쓰지 않았는가?
   - 비밀값·토큰이 템플릿에 없는가?
   - UI_GUIDE 의 셸·컴포넌트·"하지 마라"를 따르는가? (그래프 그림 없음)
   - GLOSSARY 용어·화면 라벨을 그대로 썼는가?
   - `server/` 가 `connector/` 를 import 하지 않는가?
3. 결과에 따라 `phases/6-typed-handoff/index.json` 의 해당 step 을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (라우트·템플릿·뷰 함수·오류 코드)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 업무 등록 폼(`/tasks/new`)·가져오기·상세·운영자 폼을 이 step 에서 바꾸지 마라. 이유: step 7. 여기서는 새 페이지와 사이드바만.
- 종류·규칙을 그래프·다이어그램으로 그리지 마라. 이유: ADR-0009 — 규칙은 한 줄 텍스트다. UI_GUIDE "하지 마라".
- 사용자 정의 종류의 `output_kind` 를 폼에서 받지 마라(항상 `generic_result`). 이유: 내장 결과 봉투는 검증기가 딸려 있다.
- 종류·규칙 등록을 운영자 전용으로 막지 마라. 이유: 팀 전제 — 워크스페이스 구성원이 등록한다(ADR-0005 의 세션 = 워크스페이스).
- 기존 테스트를 깨뜨리지 마라.
