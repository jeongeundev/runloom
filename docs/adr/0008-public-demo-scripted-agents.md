# ADR-0008: 공개 데모 — 심사 기간에는 대본 에이전트를 VM 한 대에서만 돌린다

결정일: 2026-09-21. 사용자 확정. 적용 범위는 공개 데모(심사 기간 2026-09-21 ~ 10-05, 무로그인 웹)이며 셀프호스트 실사용은 아니다.

**결정**: 공개 데모는 실제 에이전트를 돌리지 않는다. 진단(A)은 진단 서비스의 `DIAG_MODEL=fake`(fixture 대본, OpenAI 호출 없음), 코드 수정(B)은 `workflow.scripted.codex`·`workflow.scripted.claude`(`src/workflow/scripted/`)가 맡는다. 연결 프로그램은 운영자 Mac 이 아니라 **같은 VM** 에서 systemd 유닛(`workflow-connector`)으로 돌고, 유닛의 PATH 앞에 둔 `deploy/bin/{codex,claude}` 래퍼가 실제 도구 이름을 가로채 대본 에이전트를 띄운다. VM 에는 실제 `codex`·`claude` 바이너리도, `OPENAI_API_KEY` 도 두지 않는다. 카탈로그 세 Agent 는 `seed_demo.py --scripted` 로 `demo_scripted=1` 이며 화면에 `시연용 · 대본 재생` 을 표시한다.

**이유**: 공개 데모는 심사위원 외에 투표하는 불특정 방문자도 쓴다. 실제 OpenAI 호출·개인 구독형 Codex/Claude 를 그 트래픽에 노출하면 비용과 사용량 한도가 통제되지 않고([ADR-0003](0003-diagnosis-model-openai-gpt41-mini.md) 총액 US$30, [ADR-0007](0007-usage-limit-wait-policy.md) 구독 창 한도), 운영자 Mac 이 꺼지면 B 가 멈춘다([ADR-0006](0006-deployment-vm-caddy-mac-connector.md) 트레이드오프). 심사자가 확인해야 할 것은 등록 → 가져오기 → 구성 → 자동 실행 → 검토라는 흐름과 상태 규칙이지 특정 모델의 출력이 아니다.

**유지되는 것 — 대본이어도 실제와 같은 부분**: 실행 계약 v1(`contracts/`)·상태 전환·중복 방지·완료 판정(`domain/`)·진단 결과 검증기(`verify_diagnosis`)·worktree 와 결과 커밋(`git_ops`)·수정 전 실패/수정 후 통과의 **실제 pytest** 실행·검증 프로필·산출물 종류는 실제 어댑터와 동일하다. 대본 에이전트는 실제 어댑터(`connector/codex.py`·`connector/claude.py`)의 argv 형식을 그대로 받으며 어댑터·워커·서버 코드는 바뀌지 않는다. 모델 호출만 없다. 결과·로그의 모델 식별자는 `scripted-demo-agent`/`fake-fixture-script` 이며 실제 모델이 돈 것처럼 적지 않는다.

**트레이드오프**:
- 진단 내용과 수정 diff 가 매번 같다. 실제 모델의 오류·보류(`needs_information`)·검증기 차단 사례를 공개 데모에서 보여 주지 못한다 — 그 기록은 [DIAG_EVAL](../DIAG_EVAL.md)(gpt-4.1 5사례 × 3회)에 있다.
- 실제 연동 증거는 공개 데모가 아니라 기록으로 제시한다: 2026-09-20 실제 Codex CLI 로 B 를 끝까지 실행한 기록([VERIFICATION_LOG](../VERIFICATION_LOG.md) Step 15 — worktree·결과 커밋·3 failed → 17 passed)과 같은 날 실제 `gpt-4.1` 로 진단을 평가한 기록([DIAG_EVAL](../DIAG_EVAL.md)). 두 기록은 따로 남았고, 실제 모델과 실제 Codex 로 A → B 를 한 번에 완료한 기록은 없다. 대본 경로의 통합 기록은 VERIFICATION_LOG 2026-09-21 절이다.
- [ADR-0000](0000-principles.md) 의 "고정 답변 재생으로 진단·수정을 대체하지 않는다" 와 긴장이 있다. 이 ADR 은 공개 데모에 한해 그 원칙을 유예하되, 화면에 대본임을 표시하고 검증·worktree·pytest 는 실제로 돌리는 조건으로 한다. 셀프호스트 실사용에는 적용하지 않는다.

**ADR-0006 과의 관계**: ADR-0006 의 "운영자 Mac 연결 프로그램 + launchd" 구성은 셀프호스트 실사용용으로 그대로 유지한다(`deploy/launchd/`, `connector/codex.py`·`claude.py`). 공개 데모에서는 쓰지 않는다. 실제 모델·실제 Codex 로 되돌리려면 `diag.env` 의 `DIAG_MODEL=openai`·`OPENAI_API_KEY`·단가, `central.env` 의 한도(10/36), Mac 연결(ADR-0006) 순이며 절차는 [DEPLOY](../DEPLOY.md) 10절이다.
