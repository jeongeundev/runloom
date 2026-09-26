# Step 6: 지표 계산 — 순수 도메인

## 읽어야 할 파일

- AGENTS.md
- phases/9-measure/README.md (계획 기본값·지표 정의 — 이 phase 의 기준)
- phases/9-measure/index.json (완료 step 의 summary)
- docs/adr/0015-measurement-events-and-baseline.md (step 0 산출물. step 0 에서는 새로 만든다)
- docs/ARCHITECTURE.md ("측정 — phase 9" 절. step 0 에서는 새로 만든다)
- docs/GLOSSARY.md
- docs/ARCHITECTURE.md ("측정 — phase 9" 지표 정의 표)
- src/workflow/domain/ (기존 도메인 모듈의 값 객체 스타일, 예: task_readiness.py)

먼저 실제 파일과 이전 step 산출물을 읽는다. 이름·칸·함수 시그니처는 step 0 의 ADR-0015·ARCHITECTURE 가 고정한 것을 따르고, 바꿔야 하면 같은 이름의 문서·계약·테스트를 함께 갱신한다. 대화 이력을 전제로 판단하지 않는다.

## 작업

`src/workflow/domain/metrics.py` 신설. DB·HTTP·시계를 보지 않고 값 객체만 받는다. 이름은 step 0 이 ARCHITECTURE 에 고정한 것을 쓴다. 시그니처 수준 예:

```python
@dataclass(frozen=True)
class Stat:
    n: int                 # 계산된 건수
    median_seconds: float | None
    incomplete: int        # 끝나지 않아 제외된 건수

def compute_metrics(facts: MetricsInput, *, window_from: datetime | None, window_to: datetime | None,
                    group_by: Literal["config_revision", "folder_commit"] | None) -> MetricsReport: ...

def summarize_baseline(items: Sequence[BaselineItemFact]) -> BaselineSummary: ...
```

- 입력 값 객체: Task(묶음 루트·선행·종류·생성·이슈 열림·검토 결정·병합 확인), 실행(시도 번호·상태·시각·실패 코드·설정 번호·폴더 커밋·비용·토큰), 판정/검토 결과(outcome·시각), 사람 요청(생성·응답), 업무 이벤트(type·시각·대기 actor), 기준선 항목.
- 지표는 ARCHITECTURE 지표 정의 표 전부: 인계 대기(actor 별 대기 구간 포함), 접수→사람 차례, 접수→완료, 개입 횟수·응답 시간, 1회 통과율·재작업·사람 거부 비율, 실행 시간·비용·토큰(알려진 합계와 모름 건수 분리), 실패율·실패 코드 분포·재실행.
- 비율은 분자·분모를 함께 낸다. 표본이 0 이면 None.
- `group_by` 가 있으면 같은 지표를 그룹마다 낸다. 실행이 없는 NULL 그룹은 "모름" 그룹으로 모은다.

## 테스트 먼저

`tests/workflow/domain/test_metrics.py`: 지표마다 손으로 계산한 작은 사례(짝수·홀수 중앙값, 미완료 제외 건수, 비용 일부 모름, 재작업 2회 묶음, 대기 구간 actor 분리, 기간 필터 경계, group_by 두 종류), 빈 입력, 기준선 요약(n·중앙값). 도메인 import 규칙 위반이 없는지 기존 검사로 확인.

소스 변경 전에 `tests/` 미러 경로에 실패 테스트를 작성하고 실패 원인이 의도한 것인지 확인한다(`tdd-guard.sh` 가 테스트 없는 소스 작성을 막는다). 구현 후 해당 테스트와 전체 회귀를 통과시킨다.

## Acceptance Criteria

```bash
python3 -m pytest -q
python3 -m ruff check .
```

위 커맨드와 이 step 의 테스트 항목을 모두 확인한다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 규칙을 확인한다: `domain/` 은 FastAPI·sqlite3·HTTPX·subprocess·Git 을 import 하지 않는다 / `server/` 와 `connector/` 는 서로 import 하지 않고 `contracts/` 만 공유한다 / 비밀값(`WORKFLOW_GITHUB_TOKEN`, 연결 토큰 등)은 환경변수에서만 읽고 DB·로그·응답·템플릿에 넣지 않는다 / 이름은 GLOSSARY 식별자를 쓴다.
3. 성공이면 `phases/9-measure/index.json` 의 step 6 만 `completed` 로 바꾸고 `summary` 에 생성·수정 파일, 결정, 검증 결과(테스트 수), 다음 step 이 주의할 점을 한 줄로 남긴다.
4. 3회 수정 후에도 실패하면 `error` + `error_message`, 사용자 결정·외부 자격이 필요하면 `blocked` + `blocked_reason` 을 기록한다. 실행하지 않은 검증을 `completed` 로 표시하지 않는다.

## 금지사항

- `main`·공개 데모 VM 을 변경·배포하지 않는다. 이유: 이 phase 는 `service` 기반 실서비스 작업이다.
- 실제 GitHub·유료 모델을 호출하지 않는다. 이유: 모든 step 은 대역(httpx `MockTransport`, fake 도구)으로 검증한다. 실제 기준선 가져오기는 phase 뒤 사용자 지시로 한다.
- 사용자 변경을 삭제하거나 stash·reset 하지 않는다. 커밋은 하네스가 한다.
- 이 step 범위 밖 모듈을 "개선"하지 않는다. 이유: step 마다 한 레이어만 바꿔 회귀 원인을 좁힌다.
- 모르는 비용·시각을 0 이나 현재 시각으로 채우지 않는다. 이유: "모름"과 0 은 다른 사실이다.
