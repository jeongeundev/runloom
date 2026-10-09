# 현재 인계 — 업무 목록과 결과 기반 자동 실행

갱신일: 2026-10-09. 2026-10-09 공개 데모 종료 — VM 삭제, `service` 를 `main` 으로 합치고 `service` 삭제, 데모 코드는 태그 `contest-demo-2026`([ADR-0029](adr/0029-demo-shutdown-main-single-branch.md)). 아래 기록의 `service` 는 지금의 `main` 이다. 2026-09-26 이전 기록은 [보관 자료](archive/2026-09-27-contest-and-history/CURRENT_HANDOFF-until-2026-09-26.md).

## 다음 작업: 23-setup-ux 완료 — service 병합 → 셀프호스트 v25 → K1 실연동 재개 (새 세션은 여기서 시작)

**23-setup-ux 완료**(2026-10-05, `feat-23-setup-ux`, step 0~10, `service` `058d9bc` 에서 갈라짐, [phase 23 README](../phases/23-setup-ux/README.md), [ADR-0028](adr/0028-setup-ux.md), [ARCHITECTURE](ARCHITECTURE.md) "설정 UX — phase 23", 용어 [GLOSSARY](GLOSSARY.md) "계획 용어 — phase 23", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 23 설정 UX"). 계기: K1 실연동이 준비 1번(팀 초대)에서 멈춤 — [사내 요청 실연동 1회차](product/INTERNAL_REQUEST_LIVE_RUN_1.md) "결과" 의 화면 문제 15건. 사이드바 `연결` 을 `팀`(`/team` — 멤버·초대·에이전트(러너 합침)·담당 범위)·`저장소`(`/repos` — GitHub·Jira 연결과 저장소 카드, 이슈 목록 없음)·`설정`(`/settings?tab=kinds|triage|notify|inbound|advanced`)으로 나눴고 옛 주소는 303 으로 넘어간다(POST 경로 그대로). 초대는 받는 사람 이메일(필수)·이름 + [링크 다시 만들기](옛 링크 무효, 가입은 그 이메일로만 — 스키마 v25). 종류 폼은 화면 이름·지시문·결과값·맡을 에이전트(체크박스 — 그 종류의 능력을 붙임)만 묻고 식별자·능력 코드·scope 키·산출물은 "고급". 팀 화면 에이전트 줄에서 "맡을 수 있는 일" 편집, 담당 범위 요청 유형은 고르는 목록. 저장소 카드 본문에 판단 에이전트(설명 포함)·수정·검토 에이전트(이름만), 고급·카드 도구는 접힘, 담당 연결은 수집한 이슈의 GitHub 사용자에서 고른다. 업무 목록은 끝난 업무 묶음(맨 아래·접힘)·같은 값 칸 숨김·다음 할 일 중복 `—`·도구 막대(보기 옵션 접힘). 내부 ID·능력 코드는 "자세히" 안에만(설정 › 고급은 예외). **러너 프로토콜 변화 없음 — 러너 재설치 불필요.**

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. `feat-23-setup-ux` 를 `service` 에 `--no-ff` 병합.
2. 셀프호스트 v25 재설치 — 백업 먼저(`backup create`), `install.sh` → 스키마 25. 러너는 그대로. 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v25(북마크 주소가 바뀜 — 옛 주소는 넘어감). 남아 있는 옛 초대 `inv-93013b36ea37` 은 팀 화면에 `이메일 없음(옛 초대)` 로 보인다 — 그 줄의 [링크 다시 만들기] 또는 [취소].
3. K1 실연동 재개 — [사내 요청 실연동 1회차](product/INTERNAL_REQUEST_LIVE_RUN_1.md) "준비 (관리자 R)" 가 새 화면 경로로 고쳐져 있다(팀 초대 2 → 설정 › 업무 종류·규칙에서 `장애 조사`(맡을 에이전트 runloom-sandbox — `code.review` 우회 없음) → 팀 담당 범위 → 마지막에 저장소 카드 판단 에이전트). 진행 표는 그대로, 아래 "결과 뒤 판단 실연동 확인 목록" 도 같은 실행에서 본다(준비 줄의 종류 등록만 새 폼으로). 화면만으로 준비가 끝났는지(문서를 보지 않고)를 결과 절에 남긴다.

**23 에서 남긴 것·주의**: 저장소별 판단 기준·"멤버로 보기"·초대 이메일 발송은 다음 phase. 업무 패널·단계 상세·`/requests`·모니터링 화면은 점검 범위 밖(노출 단정 대상이 아니다). 보드(`board_columns`)에는 완료 칸이 남아 있다. 수정 에이전트 후보(저장소 카드)는 저장소별로 거르지 않는다(기존 그대로). 도메인 차단 문구 `수정 Agent <agent_id>`(검증 프로필 불일치)는 저장소 카드에 ID 를 보일 수 있다. 저장소 카드 설정 폼은 JS(`data-json-action`) 가 있어야 저장된다(기존 그대로).

## 이전 다음 작업: 22-next-step 완료 — service 병합 → 셀프호스트 v24 → 결과 뒤 판단 실연동

**22-next-step 완료**(2026-10-05, `feat-22-next-step`, step 0~10, `service` `aae864a` 에서 갈라짐, [ADR-0027](adr/0027-next-step-triage.md), [ARCHITECTURE](ARCHITECTURE.md) "결과 뒤 판단 — phase 22", 용어 [GLOSSARY](GLOSSARY.md) "계획 용어 — phase 22", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 22 결과 뒤 판단"): 결과가 규칙 밖(①)·`needs_information`(②)·사내 요청 반환(③)이면 저장소 카드의 판단 에이전트가 판단 시점에 고정한 후보(다음 단계 종류·멤버·에이전트·담당 범위) 안에서 다음 행동 하나(다음 단계·재작업 / 새 업무 / 사내 요청 / 사람 확인)를 제안하고, 사람이 업무 패널 "다음 단계 제안" 절의 [제안대로] 를 한 번 누른다(판단이 만든 사내 요청의 요청자 = 누른 멤버). 시작할 수 없거나(판단 에이전트 없음·옛 러너·대기 3600초 초과) 실패·[무시]면 지금 동작 — ② 는 원래 사람 요청, ① 은 그대로 `확인 필요`, ③ 은 판단이 만든 요청만 `next_step_human` 사람 요청. 사내 요청이 생기면 받는 사람에게 `요청 받음` 알림, 제안이 나오면 `다음 단계 제안` 알림. 받는 사람의 수락·조사·검토·반환은 그대로 사람이 한다. 결과 뒤 판단은 자동 시작하지 않고, 접수 판단의 자동 시작 자격 건수·모니터링 판단 지표에 섞이지 않는다. 스키마 v24, **러너 프로토콜 변화**(claim `capabilities` 에 `after_result_triage`) — 옛 러너는 결과 뒤 판단만 못 한다.

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. `feat-22-next-step` 를 `service` 에 `--no-ff` 병합.
2. 셀프호스트 v24 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 24 → **러너도 `install-runner.sh` 재실행**(옛 러너는 결과 뒤 판단을 받지 못한다). 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v24. 재설치 뒤 `connectors.capabilities_json` 에 `after_result_triage` 가 있는지 본다.
3. 결과 뒤 판단 실연동 1회 — 아래 목록. 결과는 [VERIFICATION_LOG](VERIFICATION_LOG.md) 에 새 절로.

**결과 뒤 판단 실연동 확인 목록**([사내 요청 실연동 1회차](product/INTERNAL_REQUEST_LIVE_RUN_1.md)의 K1 을 결과 뒤 판단 기준으로 고친 것 — runloom-sandbox, 실제 Claude 구독, 세 계정 R(관리자·요청자)·N(처리 담당)·J(판단 담당)는 브라우저를 나눈다):
- [ ] 준비: N·J 초대, 조사 종류 `incident_investigation`(능력 `code.review` 우회·scope `repository_id`·입력 없음·outcomes `cause_found, needs_information, unresolved`), 담당 범위 `kube_proxy · investigation → N`(판단 담당 J, 에이전트 runloom-sandbox), N 의 개인 웹훅(또는 공용 웹훅) — 1회차 문서 "준비"와 같다.
- [ ] **저장소 카드(runloom-sandbox)의 판단 에이전트 칸을 고른다** — 1회차와 다른 점. 고르면 sandbox 의 `새로 들어옴`·담당 없음 업무에 접수 판단이 바로 걸린다(기존 업무 확인 뒤 고른다). OpenArchive 카드는 고르지 않는다.
- [ ] sandbox 에 K1 이슈 1건(1회차 "요청 본문") → 접수 판단 제안 → R [제안대로 맡기기] → 수정 에이전트가 `needs_information` 을 내는지(LXC 호스트 값이 없으므로 기대). 내지 않고 수정까지 가면 그 결과·판정을 기록하고 ① 경로(규칙 밖 결과)로 본다.
- [ ] ② 사람 요청 대신 결과 뒤 판단이 도는지(업무 이유 `다음 단계 판단 중`), 제안이 `사내 요청 · kube_proxy/investigation → N` 인지, 목적 글·근거·확신도가 쓸 만한지, R 에게 `다음 단계 제안` 알림. 다른 행동을 제안하면 그대로 기록한다(판단 품질 평가가 목적이 아니다 — 흐름 확인).
- [ ] R [제안대로] → `/requests` 에 `판단 제안으로 생성`, 요청자 = R, 업무 이유 `사내 요청 대기 · N`, N 에게 `요청 받음` 알림이 왔는지·어디서 알았는지.
- [ ] N 수락 → (선택) 정보 질문·R 답변 → 조사 시작 → 검토 승인 → (선택) J 판단 → 반환. 1회차 "진행" 2~9 와 같다.
- [ ] 반환 뒤 R 의 수동 재개 없이 원래 업무에서 `request_returned` 판단이 도는지, 제안이 `재작업 → <수정 에이전트>` 인지, R [제안대로] → 반환 요약이 지적으로 들어간 새 수정 실행 → 검토 → 초안 PR. 결과를 그대로 쓸 수 있었는지.
- [ ] 판단 두 번의 시간(원인 → 제안)·구독 사용량(실행 비용·토큰), 후보 밖 값(`판단 실패 · 후보 밖 제안`) 여부, 판단 중 러너 등록 폴더에 변경 없음(`git status`).
- [ ] (선택) [무시] 한 번 → 원래 사람 요청이 열리는지.

**22 에서 남긴 것·주의**: ③ 판단이 실패·무시되면 대체 경로가 없다(반환 조회가 `request_returned` 판단 행이 있는 요청을 빼므로 — ARCHITECTURE 조회 정의 그대로) — 그때 원래 업무는 사람이 패널에서 잇는다. 결과 뒤 판단 품질 지표(모니터링)는 범위 밖. 프로젝트별 판단 기준·한 화면 등록은 phase 23. 사람이 만든 사내 요청의 반환도 원래 업무에서 판단을 건다(판단 Agent 가 있을 때).

## 이전 다음 작업: 사내 요청 실연동 — 공개 사례 K1 을 실제 러너·CLI 로 조사

**21-internal-request 병합·셀프호스트 v23 반영**(2026-10-04): 사내 요청(담당 범위·요청·정보 확인·조사·판단·반환·재개 기록)을 `feat-21-internal-request` 로 커밋해 `service` 에 병합(`332da59`). 하네스 phase 가 아니라 `phases/` 기록은 없다. 문서는 [사내 요청](product/INTERNAL_REQUESTS.md)부터, 합성 검증·기록 양식은 [파일럿 기록](product/INTERNAL_REQUEST_PILOT_RECORD.md). 회사 내부 사례가 없어 공개 Kubernetes 이슈 3건을 기존 방식 사례로 복원했다([공개 사례](product/INTERNAL_REQUEST_PUBLIC_CASES.md)). 셀프호스트: 백업 `20261004T055430Z`, 진행 중 실행 0건에서 v16 → v23, 업무 25·Task 26·멤버 1·실행 5 그대로, 외래키 검사 통과, `/healthz` 200. 러너 코드 변화 없음 — 재설치 안 함, 재기동 뒤에도 연결 이어짐.

**할 일**(사용자 지시 뒤): K1 을 입력으로 혼자 세 역할(요청자·처리 담당·판단 담당)을 나눠 실제 등록 에이전트로 조사 → 반환 → 재개 기록. 손으로 먼저 돌리고 나온 결함을 phase 22 step 으로 만든다. 먼저 확인할 것: 조사용 종류(`generic_result`, 입력 없음) 등록, 러너 claim 의 지원 종류(현재 `bug_fix`·`code_review`·`triage`)가 조사 종류를 받는지, 두 번째·세 번째 멤버 계정.

## 이전 다음 작업: 판단·Jira·17 실연동·브라우저 확인(모니터링 세 탭 포함)

**20-monitor 완료**(2026-10-02, `feat-20-monitor`, step 0~9, [ADR-0026](adr/0026-monitor.md), [ARCHITECTURE](ARCHITECTURE.md) "모니터링 — phase 20", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 20 모니터링"): `/monitor` 탭 셋 — `전후`(기존 화면 + 설정 번호 그룹 머리에 무엇을·누가·언제 바꿨는지, v16 이전 번호는 `기록 없음`)·`판단`(종류별·기준 버전별 제안 n·사람 처리·사람 일치·진행 여부·실제 결과(병합 완료·재작업 없이 병합·진행 중)·실패 코드·판단 시간·비용, 확신도 구간표)·`담당자별`(멤버 완료·진행·내 차례 대기·가장 오래 기다림·응답 시간, 에이전트 실행·실패율·1회 통과·재작업·실행 시간·비용, 담당 없음). 연결 "판단" 탭 자동 시작 행에 `지금 기준값 x 이상 판단 n건 — 사람 일치 a/b · 병합 c/d`. `/metrics.json` 키 `triage`·`assignees`·`config_changes`, `/metrics.csv` 는 열 그대로 새 행. 설정 변경 기록 `config_changes`(스키마 v16). 지표는 기존 기록에서 매번 계산(저장·캐시 없음). **러너 프로토콜 변화 없음 — 러너 재설치 불필요.**

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. ~~`feat-20-monitor` 를 `service` 에 `--no-ff` 병합~~ — 끝남(2026-10-02, `ee4e478`).
2. ~~셀프호스트 v16 재설치~~ — 끝남(2026-10-02, 백업 `20261001T234413Z`, 진행 중 실행 0건에서 v15 → v16, 업무 25·Task 26·멤버 1 그대로, `config_changes` 0행, 외래키 검사 통과, `/healthz` 200, 러너 재설치 안 함 — 재설치 뒤에도 claim 이어짐). 원래 절차: 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 16. 러너는 그대로. 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v16.
3. 판단 실연동(아래 19 확인 목록) → Jira 실연동(18 목록) → 17 실연동(두 번째 멤버·`--name b` 러너) → 브라우저 확인(16·17 목록 + 19 판단 화면 + **모니터링 세 탭**·자동 시작 미리보기 줄).

**19-triage 완료**(2026-10-02, `feat-19-triage`, step 0~10, [ADR-0025](adr/0025-triage.md), [ARCHITECTURE](ARCHITECTURE.md) "판단 — phase 19", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 19 판단"): 담당 없는 새 GitHub·Jira 업무에 내장 종류 `triage` 단계를 붙여, 저장소 카드의 **판단 에이전트**(러너의 로컬 Claude Code/Codex)가 기본 브랜치 끝의 읽기 전용 체크아웃에서 Runloom 이 저장한 판단 기준(버전 v1, v2…)으로 종류·담당·선행·진행 여부(`맡겨도 됨`·`확인 필요`·`부적합`)·확신도·근거를 **제안**한다. 자동 판단은 워크스페이스에 한 번에 1건·러너가 빌 때만, 사용량 한도면 그 에이전트 1시간 쉼. 중앙은 시작 때 고정한 후보 안의 값만 받고(밖이면 `판단 실패 · 후보 밖 제안`), 판단 로그에 남긴다. 패널 판단 절 [제안대로 맡기기]·[무시]·[판단 받기]/[다시 판단], 담당이 정해지면 `accepted`/`changed` 기록, 목록 배지. 연결 화면 "판단" 탭(기준 편집·버전 이력, 종류별 자동 시작 — 사람 처리 20건부터 켬, 기준값 0.50~1.00). 자동 시작은 맡긴 사람 없이 기존 맡기기 경로(소유자 승인·꺼진 러너 대기 그대로), 타임라인 `자동 시작 · 판단 v<n>`. 판단은 업무를 완료·종료하거나 `내 차례` 를 만들지 않는다. 스키마 v15, **러너 프로토콜 변화**(claim `supported_kinds` 에 `triage`) — 옛 러너는 판단만 못 한다(`러너 업데이트 필요 — 판단 미지원`).

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. ~~`feat-19-triage` 를 `service` 에 `--no-ff` 병합~~ — 끝남(2026-10-02, `df82a46`).
2. ~~셀프호스트 v15 재설치~~ — 끝남(2026-10-02, 백업 `20261001T191251Z`, v14 → v15, 업무 25·Task 26·멤버 1 그대로, 외래키 검사 통과, 에이전트 둘에 `code.triage`, 러너 재설치 뒤 `supported_kinds_json` 에 `triage`). **판단 에이전트 칸은 아직 비워 둠** — 고르면 sandbox 의 `새로 들어옴` 업무에 자동 판단이 바로 걸리므로 실연동(3) 때 고른다. 원래 절차: 셀프호스트 v15 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 15 → **러너도 `install-runner.sh` 재실행**(옛 러너는 판단을 받지 못한다). 그 뒤 저장소 카드(runloom-sandbox)의 **판단 에이전트** 칸을 고른다. 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v15. OpenArchive 저장소 카드에는 판단 에이전트를 고르지 않는다(공모전 출품작 — 러너·워커를 멈춰 둔 저장소).
3. 판단 실연동 1회 — 아래 확인 목록. 결과는 [VERIFICATION_LOG](VERIFICATION_LOG.md) 에 새 절로.
4. ~~20-monitor 설계~~ — 끝남, 구현까지 완료(위 20-monitor).

**판단 실연동 확인 목록**(runloom-sandbox, 실제 Claude 구독):
- [ ] 재설치 뒤 러너 claim 의 지원 종류에 `triage`(`connectors.supported_kinds_json`), 에이전트 능력에 `code.triage {repository_id}`.
- [ ] sandbox 이슈 3건 — ① 명확한 버그(재현 절차·기대 동작이 있는 것) ② 양식이 빈 것(제목만) ③ 운영 접근이 필요한 것(예: 운영 DB 확인) → 자동 판단이 한 번에 1건씩 → 제안 진행 여부가 각각 `맡겨도 됨`·`확인 필요`·`부적합` 인지, 근거·모자란 정보가 기준 항목과 맞는지.
- [ ] 판단 시간(판단 단계 시작 → 제안)과 구독 사용량(실행의 비용·토큰 — `total_cost_usd` 는 CLI 계산값), 후보 밖 값(`triage_invalid`)이 나오는지.
- [ ] ① 에 [제안대로 맡기기] → 수정·검토·초안 PR → 판단 로그 `accepted`. ② 는 담당을 다르게 정해 `changed`, ③ 은 [무시] → `dismissed`.
- [ ] 판단 중 원본 폴더(러너 등록 폴더)에 변경이 없는지(`git status`).

**개발 정리(2026-10-02)**: Docker 이미지 태그를 Compose 프로젝트별로 분리해 셀프호스트 E2E 설치·재시작·백업 복원 1건 통과. n8n 문서·예시는 현행 셀프호스트와 `kind:bug_fix`·`kind:code_review` 라벨로 갱신했다. `3-limit-wait`는 계획만 있는 보류 기능으로 유지한다(실사용 한도 빈도를 확인한 뒤 현재 업무·단계 모델에 맞춰 재설계, 옛 step을 그대로 실행하지 않음). 간헐 E2E 실패 조사 결과와 명령은 VERIFICATION_LOG 최신 절을 본다.

**미룬 것**: Jira 실연동(아래 18 확인 목록 — 사용자가 Jira Cloud 사이트·토큰을 만든 뒤), 17 실연동(두 번째 멤버·`--name b` 러너), 브라우저 확인(16·17 목록 + 19 패널 판단 절·연결 "판단" 탭·저장소 카드 판단 에이전트 select). 셀프호스트 Docker E2E는 2026-10-02 프로젝트별 이미지 격리 뒤 통과했다.

**19 에서 남긴 것·주의**: 확신도는 모델이 말한 값이다 — 자동 시작 기준값은 20-monitor 에서 판단 품질을 본 뒤 정한다. 업무 내용이 바뀌어도 자동으로 다시 판단하지 않는다([다시 판단]만). 선행 업무 제안은 `blocks` 링크만 남기고 착수를 막지 않는다. 사용량 한도 쉼은 워커 메모리라 재시작하면 풀린다(3-limit-wait 는 범위 밖). 판단 에이전트 소유자 알림·직접 등록/n8n 업무 판단·판단 에이전트 자동 매칭은 하지 않았다.

**18-jira 완료**(2026-10-01, `feat-18-jira`, step 0~9, [ADR-0024](adr/0024-jira-source.md), [ARCHITECTURE](ARCHITECTURE.md) "Jira 소스 — phase 18", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 18 Jira"): 연결 탭 가져올 곳에 Jira 칸(사이트 주소·이메일·API 토큰 → tenant_info·myself 확인 → 게이트웨이/사이트 기준 주소 저장, 토큰은 비밀 파일 `jira_api_token`), 프로젝트 찾기·추가(연결 저장소 = GitHub 저장소 하나, 지금부터/열린 업무 전부), 프로젝트 설정(이슈 유형·세 상태·후속 이슈 유형·켜짐·목록 새로 고침), 1분 폴링 가져오기(`search/jql`·`nextPageToken`·포함 경계 커서) → 업무 `새로 들어옴`(맡기기 뒤에만 착수), Jira 완료 범주 = 원본 닫힘(다음 단계 대기, 다시 열면 이어감), 연결 저장소에서 수정·검토·초안 PR(`Fixes` 없음, 본문 첫 줄 `원본: SHOP-n — 주소`, 요청문 머리에도 원본 키), 세 순간(작업 시작·`PR · 검토`·`완료`) → outbox `jira_deliveries` → 전환(이미 그 상태면 보내지 않음, 전환 없음·400 은 반영 실패), 후속 새 업무 → 같은 프로젝트에 이슈(라벨 `runloom`·`runloom-RUN-n`, `Relates` 링크, 응답 유실은 라벨로 조정), 스키마 v14. 러너 프로토콜은 바뀌지 않았다.

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. ~~`feat-18-jira` 를 `service` 에 `--no-ff` 병합~~ — 끝남(2026-10-01, `c5f1d31`).
2. ~~셀프호스트 v14 재설치~~ — 끝남(2026-10-01, 백업 `20261001T085957Z`, v13 → v14, 업무 25건 그대로·jira 기본 매핑 1행·외래키 검사 통과, 러너는 17 뒤 이미 새 판이라 그대로). 원래 절차: 셀프호스트 v14 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 14. 러너 재설치는 필요 없다. 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v14. 셀프호스트가 아직 v13 전(v12)이면 v13 항목의 **러너 재설치(`install-runner.sh`)도 함께** 한다 — 러너 프로토콜은 17 에서 바뀌었다.
3. 사용자가 무료 Jira Cloud 사이트와 API 토큰을 만든다 → 아래 실연동 확인 목록 1회.
4. ~~19-triage 설계~~ — 끝남, 구현까지 완료(위 19-triage).

**Jira 실연동 확인 목록**(사용자와 1회 — 결과는 [VERIFICATION_LOG](VERIFICATION_LOG.md) 에 새 절로. 절차는 [SELFHOST](SELFHOST.md) "Jira 연결"):
- [ ] 무료 Jira Cloud 사이트 만들기(`https://<이름>.atlassian.net`), 소프트웨어 프로젝트 하나(키 예: `SHOP`).
- [ ] 이슈 유형·상태를 회사 형식으로 — 유형 `버그`·`작업`, 워크플로 상태 `대기`·`진행 중`·`리뷰중`·`종료`(종료만 완료 범주). 아무 상태에서 세 상태로 옮길 수 있는 전환이 있는지 확인.
- [ ] API 토큰 만들기 — 스코프 토큰이면 `read:jira-work`·`write:jira-work`·`read:jira-user`. 어느 토큰(스코프 있음/없음)인지와 저장된 기준 주소(`jira_connections.api_base` 가 `gateway`/`site`)를 기록.
- [ ] `/connect?tab=sources` 에서 연결 → `연결됨 · 이름 · 사이트`.
- [ ] 프로젝트 추가 — 연결 저장소 `runloom-sandbox`, 시작점 지금부터 → 설정: 세 상태 `진행 중`·`리뷰중`·`종료`, 후속 이슈 유형 `작업`. 상태 후보에 한국어 이름이 그대로 보이는지.
- [ ] Jira 에 sandbox 를 고치는 이슈 1건 → 1분 안에 `새로 들어옴` → [에이전트에게 맡기기] → Jira 가 `진행 중` 으로.
- [ ] 수정·검토 → sandbox 에 초안 PR(본문 첫 줄 `원본: SHOP-n — 주소`) → 업무 `PR · 검토` → Jira `리뷰중`.
- [ ] PR 병합 → 업무 `완료` → Jira `종료`. Jira 상태가 세 번 옮겨졌는지(이슈 기록), 업무 패널 "원본에 남긴 것" 에 `반영됨` 세 줄.
- [ ] 후속 이슈 — 후속 규칙을 새 업무로 둔 상태에서 한 건 → Jira 에 새 이슈(라벨 `runloom`·`runloom-RUN-n`, 원인과 `Relates` 링크) 1건, 다음 가져오기 뒤 업무 수 그대로. 회사 Jira 처럼 생성 화면 필수 칸이 있으면 `반영 실패 · 필수 칸 — …` 가 보이는지.
- [ ] (선택) 진행 중에 Jira 에서 `종료` 로 옮겨 다음 단계 대기 → 다시 열면 이어가는지.

**18 에서 남긴 것·주의**: 후속 새 업무는 이슈를 만들기 전에는 원본 이슈 id 가 없어 그 사이의 세 순간 전환은 쌓이지 않는다(만든 뒤 순간부터). 상태 옮기기 실패(전환 없음·필수 칸)는 재시도하지 않는다 — Jira 를 고친 뒤 사람이 옮긴다. Jira 상태는 완료 판정 근거가 아니다(완료는 PR 병합). Jira 담당자·진행 댓글·PR 원격 링크·웹훅·OAuth·Jira 기준선·매핑 표 편집 화면은 하지 않았다. 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 공유 때문에 여전히 미실행.

**17 반영 이력**: 17 은 `service` 에 병합됐다(`ed64efa`·`3392159`) — 아래 17 할 일 1·5 는 끝남.

**17-team-handoff 완료**(2026-10-01, `feat-17-team-handoff`, step 0~11, [ADR-0023](adr/0023-cross-member-delegation.md), [ARCHITECTURE](ARCHITECTURE.md) "사람 사이 인계 — phase 17", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 17 사람 사이 인계"): 에이전트마다 맡기기 정책(`바로 실행`·`내 승인 뒤 실행` — 연결 탭 팀 목록에서 러너 소유자·관리자가 바꿈), 다른 멤버의 에이전트에게 맡기면 소유자에게 `맡김` 알림, 승인 정책이면 소유자의 `내 차례`에 [승인]·[거절](거절 = 담당 없음 + 맡긴 사람 알림, 후속 검토 단계도 새로 묻는다), 꺼진 러너에게 맡기면 `대기 · <소유자>의 러너 꺼짐 · 켜지면 시작` + 소유자 알림 1회 → 켜지면 시작(모든 종류), 검증 실패 요청에 [검증만 다시](에이전트 없이 같은 결과 커밋 재검증)·[답하고 다시 맡기기](옛 [답하고 다시 판정]), 패널 담당 후보 `이름 · 이OO의 Mac · 켜짐`·지시 메모 → 요청문 머리(`# RUN-n 제목`·양식·지시), 목록 저장소 묶기·필터·키 칸 `RUN-n` 먼저, 러너 상대 `PYTHONPATH` 풀기, 실행 이벤트마다 단계 상태 재계산, `install-runner.sh --name`, 스키마 v13. step 11 e2e 에서 고친 결함 3건: 같은 저장소를 등록한 러너가 둘이면 맡긴 에이전트를 무시하던 매칭, 검토가 다른 러너로 가던 매칭, 실행 `failed` 이벤트가 업무를 `종료` 로 굳히던 것(step 4 회귀).

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. `feat-17-team-handoff` 를 `service` 에 `--no-ff` 병합.
2. 셀프호스트 v13 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤 `install.sh` → 스키마 13 → **러너도 `install-runner.sh` 재실행**(러너 프로토콜 변화 — 옛 러너는 [검증만 다시]를 받지 못한다). 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v13.
3. 실연동 1회 — 두 번째 멤버 계정 + `--name b` 러너(**다른 이름의 sandbox 클론** 폴더 — 같은 폴더면 등록 409, [SELFHOST](SELFHOST.md) "한 Mac 에 러너 두 대")로 sandbox 이슈 하나를 A → B 에이전트에게 맡기기(저장소 카드의 수정·검토 에이전트 칸은 비워 둔다), 승인 정책 1회(승인·거절), [검증만 다시]는 RUN-24 에서(재설치 뒤 러너가 새 판이어야 한다).
4. 브라우저 확인 — 16 에서 남은 것(패널 끼우기·Esc·뒤로 가기·묶음 접기·브랜치 복사·800px 미만 전체 화면 패널) + 17(저장소 묶기·저장소 필터 select, 승인·거절 버튼, 지시 메모 칸, 팀 탭 맡기기 정책 select). 자동 테스트 없음.
5. 18-jira 설계([REDESIGN_PLAN](product/REDESIGN_PLAN.md) 13절).

**17 에서 남긴 것·주의**: 저장소 카드(`match_for_source`)는 단계 없이 계산해 러너가 둘이면 "수정 Agent 2개" 를 그대로 보인다(패널에서 고르면 시작함). 승인 범위는 업무 × 소유자 × 맡긴 사람(2026-10-01 사용자 결정으로 단계에서 넓힘, `fix-approval-per-work`) — 한 번 승인하면 그 업무의 후속 검토·[다시 맡기기]는 다시 묻지 않는다. 거절된 범위는 워커가 다시 묻지 않는다(사람이 다시 맡기면 새 요청). `install-runner.sh` 에 `--id` 가 없어 로컬 등록 이름 = 클론 폴더 이름. 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 공유 때문에 여전히 미실행.

**17 설계 전 기록**(이력):

**2026-09-30 사용자 결정**: 17-jira 앞에 phase 하나를 끼운다(Jira·판단·모니터링은 한 칸씩 밀림 — 18-jira·19-triage·20-monitor, REDESIGN_PLAN 13절은 새 phase step 0 이 고친다). 이름 제안 `17-team-handoff`(설계 세션에서 확정).

**왜**(사용자 정리, 2026-09-30): Orca 로 저장소별 Claude Code 세션을 열어 작업·리뷰를 새 세션에 맡기는 1인 흐름은 자연어가 더 빠르고 성능도 좋을 수 있다. Runloom 의 핵심 가치는 **내 에이전트 사이가 아니라 사람 사이 병목** — 다른 멤버의 에이전트(그 멤버 Mac 의 러너)에게 작업이 바로 가고, 결과가 받아야 할 사람에게만 "내 차례"로 가는 것. Orca 의 Projects = 저장소 축.

**범위 후보**(설계 세션에서 사용자와 확정 — 결정 질문은 장면으로 2~3개씩):
1. **두 사람·러너 두 대 시나리오를 검증 목표로** — 관리자 A(러너 1, 이 Mac) + 멤버 B(러너 2). A 가 업무 담당을 "B 의 에이전트"로 → B 러너가 실행 → 결과·검토·PR → 받는 사람별 내 차례·알림. 지금까지는 러너 하나로만 돌렸다(phase 15 가 멤버 러너·소유자를 만들었지만 실사용 미검증). 담당 선택에 에이전트 소유자(누구의 Mac) 표시, 러너가 꺼진 멤버 에이전트에게 맡겼을 때의 표시, 인계 맥락(업무 양식·앞 단계 결과·응답 메모)이 받는 쪽에 충분한지. 실연동은 같은 Mac 에서 러너 홈을 둘로 나눠 돌리는 것으로 가능한지(`WORKFLOW_CONNECTOR_HOME` 류 설정 유무 확인 필요) — 아니면 대역 e2e + 실연동 1회.
2. **저장소별 업무 보기**(사용자 요청) — 묶기에 저장소, 도구 막대에 저장소 필터(Orca Projects 처럼 기본 축 후보).
3. 목록 키 칸에 `RUN-n` 먼저(원본 키는 옆에 짧게 `sandbox#3`) — 브랜치·PR·패널은 `RUN-n` 인데 목록에서 못 찾았다.
4. 사람 요청 버튼 이름 — `fix_verification_failed` 의 [답하고 다시 판정] 은 판정만이 아니라 에이전트를 다시 돌린다(`worker._resume`, 이전 결과 브랜치에서). "결과는 그대로 두고 검증만 다시" 동작은 없다 — 필요한지.
5. 러너 검증 환경 격리 — 러너를 설치한 Python 의 편집 설치(`pip install -e` 본 저장소)가 대상 저장소 검증 하위 프로세스에 섞인다(sandbox 실패 원인, 아래). 상대 `PYTHONPATH` 가 cwd 바뀌면 깨지는 함정 포함.
6. 실행이 `running` 인데 단계(Task) 상태가 `실행 요청됨 · 접수 대기` 로 남던 것 — 갱신 주기인지 결함인지 먼저 재현.
7. 브라우저 JS 확인 결과(사용자) — 패널 끼우기·Esc·묶음 접기·브랜치 복사.

**지금 살아 있는 것**: RUN-24(sandbox #3) = `내 차례 · 결과 판정 실패`(사용자가 [답하고 다시 판정] 누를 차례, 수정 diff 는 맞음). RUN-25(sandbox #4)·RUN-22(OpenArchive #133) = 테스트 클릭으로 `직접 작업 중`(RUN-22 는 [직접 작업 그만두기] 필요, OpenArchive 쓰기 금지 그대로). 셀프호스트 v12·러너 1대(이 Mac, sandbox 등록 검증 명령 `$PWD/src` 로 재등록) 켜짐.


**2026-09-30 실사용 확인(16 뒤)**: sandbox 이슈 #3(RUN-24, `duration` 표기)·#4(RUN-25, `ago` 날짜) 생성. RUN-24 를 패널에서 에이전트에게 맡김 → 수정은 맞았으나 검증 실패 6건 — sandbox 의 `tests/workflow/scripted` 가 cwd 를 바꿔 `python -m workflow.scripted.*` 를 띄우는데 러너 `--env PYTHONPATH=src` 가 상대 경로라 호스트의 Runloom 편집 설치(`service`, scripted 삭제됨)를 잡음. 러너 등록 검증 명령을 `sh -c 'PYTHONPATH="$PWD/src" python3 -m pytest -q && python3 -m ruff check .'` 로 다시 등록(클론에서 2925 passed 확인). 다음 phase 후보(사용자 요청·관찰):
- **저장소별 업무 보기**(사용자 요청 2026-09-30) — 묶기·필터에 저장소.
- 목록 키 칸에 `RUN-n` 을 먼저(원본 키는 옆에 짧게) — 브랜치·PR 은 `RUN-n` 인데 목록에서 못 찾음.
- 러너 검증 환경 격리 — 호스트 Python 의 편집 설치가 대상 저장소 검증에 섞임(상대 `PYTHONPATH` 함정).
- 실행 중인데 단계 상태가 `실행 요청됨 · 접수 대기` 로 남음(업무 상태는 맞음) — 갱신 주기인지 결함인지 확인.
- RUN-22(OpenArchive #133)·RUN-25 가 테스트 클릭으로 `직접 작업 중`.

**2026-09-30 반영(16)**: `feat-16-work-ui` → `service` 병합(`f53afd6`, 원격 미푸시). 셀프호스트 재설치 — 진행 중 실행 0 확인 → 백업 `20260930T090902Z`(스키마 11) → `install.sh` → 스키마 12(업무 23: 새로 들어옴 21·완료 1·종료 1, Task 24, 멤버 1 보존, 감지 PR 0). 러너는 재기동하지 않음(프로토콜 변화 없음). 남은 확인: 브라우저 JS(패널·Esc·묶음 접기·브랜치 복사), 실제 github.com PR 감지.

**16-work-ui 완료**(2026-09-30, `feat-16-work-ui`, step 0~10, [ADR-0022](adr/0022-work-screen-and-direct-work.md), [ARCHITECTURE](ARCHITECTURE.md) "업무 화면 — phase 16", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 16 업무 화면"): 업무 화면 `/tasks`(한 줄 표·담당자/상태 묶음·빠른 필터 4개·목록/보드 6칸·끝난 업무 14일), 오른쪽 상세 패널 `/tasks?open=RUN-n`(조각 `/work/{key}/panel`, `/work/{key}` 는 넘김, 알림·원본 댓글 링크도 업무 주소), 패널에서 담당(에이전트 = 곧 맡기기)·우선순위, [내 세션에서 작업](브랜치 `RUN-n-<요약>`)·그만두기, PR 신호(소스 저장소 PR 의 head·제목에 든 키 → 감지 PR → `PR · 검토`·병합 `완료`), 연결 화면 `/connect` 탭 5개(옛 GET 7개 넘김), 모니터링 `/monitor`, 시작하기 `/start`, 사이드바 5항목, 스키마 v12.

**할 일**(순서대로, 모두 사용자 지시 뒤):
1. `feat-16-work-ui` 를 `service` 에 `--no-ff` 병합.
2. 셀프호스트 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤, `install.sh` → 스키마 v12. 러너 재기동은 필요 없다(러너 프로토콜 변화 없음). 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v12. 옛 북마크는 새 주소로 넘어간다.
3. 재설치 뒤 브라우저에서 사용자 확인 — 행 클릭 → 패널이 목록 위에 열리고 주소만 바뀌는지, Esc·닫기·뒤로 가기, 묶음 접기가 새로 고침 뒤에도 남는지, 브랜치 `복사`, 800px 미만 전체 화면 패널. 자동 테스트 없음.
4. 실제 sandbox 저장소에서 직접 작업 1회 — [내 세션에서 작업] → 준 브랜치 이름으로 push·PR → 다음 동기화에 `PR · 검토` → 병합 → `완료`. 실제 github.com PR 감지는 아직 미확인.
5. 17-jira 설계([REDESIGN_PLAN](product/REDESIGN_PLAN.md) 13절).

**16 에서 남긴 것·주의**: 처음 PR 커서(NULL)는 저장소마다 최근 PR 100건(50 × 2쪽)까지만 본다. 감지 PR 은 알림을 보내지 않는다. 병합 없이 닫힌 감지 PR 은 무시한다(직접 작업이면 `직접 작업 중` 으로 돌아감). 브랜치 push 만으로는 감지하지 않는다(PR 이 있어야 함). 러너가 붙은 직후 첫 claim 전에 에이전트를 담당으로 고르면 착수가 다음 워커 tick 으로 밀린다(대기 이유 "연결 프로그램 업데이트 필요"). `_base` 가 화면마다 시작하기 사실(쿼리 5개 이하)을 읽는다. 코드 속 오류 문구 몇 개는 옛 주소(`/operator/github`)를 말한다 — 넘어가므로 둠. 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 공유 때문에 여전히 미실행. 범위 밖(다음 phase): 판단 제안·자동 시작(18-triage), Jira(17), 모니터링 확장(19), Claude Code 훅 상태 보고, 보드 끌기, 여러 행 일괄 변경.

**15-team 완료**(2026-09-30, `feat-15-team`, [ADR-0021](adr/0021-team-accounts-and-roles.md), [ARCHITECTURE](ARCHITECTURE.md) "팀 — phase 15", 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 15 팀"): 운영자 토큰은 첫 설정·복구 전용, 이메일·비밀번호 로그인(서버 로그인 세션 `wf_login`), 초대·재설정 링크, 역할 × 동작 표(관리자·멤버), Origin 검사, 사람별 "내 차례"(담당 멤버 → 맡긴 사람 → 관리자 전원)·응답자 기록, 받는 사람별 알림(공용 `→ 이름` + 개인 웹훅), 러너 소유자, 스키마 v11.

**15 할 일**(이력 — 1·2 는 아래 "2026-09-30 반영(15)" 에서 끝남):
1. `feat-15-team` 을 `service` 에 `--no-ff` 병합.
2. 셀프호스트 재설치 — 백업 먼저(`backup create`), 진행 중 실행이 끝난 뒤, `install.sh` → 스키마 v11 → 첫 접속에서 `.env` 의 운영자 토큰으로 관리자 계정 만들기(옛 쿠키 무효라 다시 로그인) → `install-runner.sh`. 절차는 [SELFHOST](SELFHOST.md) "업그레이드" v11·"로그인"·"팀". 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 `workflow-selfhost:local` 공유 때문에 여전히 미실행 — 재설치 때 함께 확인한다.
3. 16-work-ui 설계(업무 목록 담당자별 묶음·필터·보드·상세 패널 — [REDESIGN_PLAN](product/REDESIGN_PLAN.md) 13절).

**2026-09-30 16-work-ui step 설계 완료**: `phases/16-work-ui/`(11 step, README 에 결정 9가지). 사용자 결정: 상세 = 오른쪽 패널(`/tasks?open=RUN-n`), 보드는 보기 전용(끌기 없음), 담당을 에이전트로 고르면 곧 맡기기, 옛 화면 9개는 `/connect` 탭으로 모으고 옛 주소는 넘김, 직접 작업 감지 = [내 세션에서 작업] 버튼 + 키 든 PR(브랜치 push 감지 없음), Claude Code 훅은 다음 phase 로. 스키마 v12. 다음: `python3 scripts/execute.py 16-work-ui --engine claude`(사용자 지시 뒤).

**2026-09-30 반영(15)**: `feat-15-team` → `service` 병합(`0f376a9`, 원격 미푸시). 셀프호스트 재설치 — 백업 `20260930T040417Z`(스키마 10) → `install.sh` → 스키마 11(업무 23·Task 24 보존, 관리자 1명은 아직 이메일·비밀번호 없음 = 첫 설정 대기, 러너 1개 소유자 없음) → `install-runner.sh`(launchd `Bootstrap failed: 5` — bootout 직후 bootstrap 경쟁으로 보임, 수동 `launchctl bootstrap` 재시도로 적재, heartbeat·claim 정상). 관리자 계정 만들기 완료(사용자, 2026-09-30). `install-runner.sh` 의 bootout→bootstrap 경쟁은 수정(서비스가 사라질 때까지 최대 10초 대기 + bootstrap 3회 재시도).

**2026-09-30 15-team 결정(사용자)**: 운영자 토큰은 첫 설정·복구 전용(이후 이메일·비밀번호), "내 차례" 받는 사람 = 담당 멤버 → 맡긴 사람 → 관리자 전원(응답은 누구나, 응답자 기록), `WORKFLOW_PUBLIC_URL` 설정 + 터널은 문서 안내만, 알림 = 공용 웹훅(`→ 이름`) + 멤버 개인 웹훅 선택, 멤버도 자기 러너 붙임(해제는 소유자·관리자). 기본값: scrypt(표준 라이브러리), 서버 로그인 세션 표, CSRF 는 Origin 검사, 초대 1회용 7일, 이메일 발송 없음.

**2026-09-30 반영**: 13·14 모두 `service` 병합(`56a400d`, `7cd95d9`, 원격 미푸시). 셀프호스트 재설치 — 백업 `20260929T235350Z`(스키마 8) → `install.sh` → 스키마 10(업무 23: 새로 들어옴 21·완료 1·종료 1, Task 24·기준선 24 보존, 종류 `bug_fix`·`code_review`, 관리자 1) → `install-runner.sh` 로 러너 재기동(claim 정상). 재기동 직후 워커가 sandbox #1 의 기존 댓글(같은 comment_id)을 업무 키 `RUN-23` 이 든 제목으로 한 번 고쳐 썼다(새 댓글 아님, OpenArchive 쓰기 없음). 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 `workflow-selfhost:local` 을 사용자 설치와 공유해 여전히 미실행 — 태그 격리 수정이 먼저 필요. 당시 남은 문서 정리: n8n 안내·테스트의 삭제된 데모 명령과 등록 경로 — 2026-10-02 현행 셀프호스트 안내로 갱신.

**14-task-model 완료**(2026-09-30, `feat-14-task-model`, [ADR-0020](adr/0020-work-items-and-stages.md), 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "phase 14 업무·단계"): 업무 `work_items`(키 `RUN-n`)·단계 Task 분리, 업무 상태 8개, 실패 → 내 차례·[다시 맡기기]·[닫기], `placement`, 매핑 표·양식 칸, 브랜치 `runloom/<키>`, 지표 묶음 = 업무, 홈 목록 = 업무 한 줄, 스키마 v10. 다음: `feat-14-task-model` 을 `service` 에 `--no-ff` 병합 → 셀프호스트 재설치(백업 먼저, 진행 중 실행이 끝난 뒤, 러너도 함께 — [SELFHOST](SELFHOST.md) 업그레이드 v10, 사용자 지시 뒤) → 15-team 설계(초대·로그인·역할). 셀프호스트 Docker e2e(`WORKFLOW_DOCKER=1`)는 이미지 태그 공유 때문에 미실행 — 재설치 때 함께 확인한다.

**13-selfhost-only 완료**(2026-09-29, `feat-13-selfhost-only`, [ADR-0019](adr/0019-service-selfhost-only.md), 검증은 [VERIFICATION_LOG](VERIFICATION_LOG.md) "2026-09-29 phase 13 셀프호스트 전용"). 다음: `feat-13-selfhost-only` 를 `service` 에 `--no-ff` 병합 → `python3 scripts/execute.py 14-task-model --engine claude`(`phases/14-task-model`). 셀프호스트는 아직 스키마 8 — 재설치(v9 로 올림)는 백업 뒤 사용자 지시로.

**step 설계 완료(2026-09-29)**: 13-task-model 범위를 두 phase 로 나눴다(사용자 결정 — demo 걷어내기를 먼저).
- `phases/13-selfhost-only/`(6 step): `service` 에서 demo 모드·진단 데모·대본 에이전트·카탈로그·fixture 가져오기·VM 배포 파일 삭제, 내장 종류 `bug_fix`·`code_review` 둘, 스키마 v9. README "남기는 것" 참고.
- `phases/14-task-model/`(11 step): 업무 `WorkItem`(`work_items`, 키 `RUN-n`) ↔ 단계 `Task` 분리, 업무 상태 8개(실패 = "내 차례 · 실패" + [다시 맡기기]·[닫기]), 후속 규칙 `placement`(same_work/new_work), `members`(첫 관리자), 매핑 표(`github · kind · * → bug_fix` 기본), 양식 칸 추출, 브랜치 `runloom/RUN-n`, 지표 묶음 = 업무, 홈 목록 = 업무 한 줄, 기존 데이터는 v10 마이그레이션으로 옮김.
- 이후 phase 번호 한 칸씩 밀림: 15-team, 16-work-ui, 17-jira, 18-triage, 19-monitor(REDESIGN_PLAN 13절은 13 의 step 5 가 고침).

**실행**: `service` 에서 `python3 scripts/execute.py 13-selfhost-only --engine claude` → `feat-13-selfhost-only` 를 `service` 에 `--no-ff` 병합 → `python3 scripts/execute.py 14-task-model --engine claude` → 병합. 셀프호스트 재설치는 사용자 지시 뒤.

**2026-09-29 확정 사항**: 목록 한 줄 = 업무(수정·검토·재작업·판단은 단계), `service` 는 셀프호스트만(demo 는 `main` 에만), 일정 무관·완성도 우선, 팀 계정 = 초대 링크 + 이메일·비밀번호, 판단 = 로컬 Claude Code 판단 에이전트가 제안만(자동 시작은 업무 종류별 설정), 직접 작업 상태 추적 = Git·GitHub 신호 + Claude Code 훅, 수신함 = "담당 없음" 묶음. Jira 는 사용자 계정의 새 Jira Cloud 사이트(지인 회사 Jira 는 형식만 참고).

**현재 환경(2026-09-29 저녁)**:
- `service` = `115f529`(실연동 1 결함 수정 병합), 원격 미푸시. 셀프호스트는 이 코드로 재설치(백업 `20260929T093748Z`, 스키마 8), 러너도 새 코드로 재기동.
- 실연동 대상은 비공개 `jeongeundev/runloom-sandbox`(클론 `/Users/kje/demo/runloom-sandbox`, 클론 로컬 `gh auth git-credential` 자격). App `runloom-gwufov` 설치는 sandbox 만 — 권한 Issues RW·Pull requests RW·Contents R·Metadata R. 워커·러너 **켜져 있음**, 알림(Discord) 설정됨.
- [실연동 1](VERIFICATION_LOG.md): sandbox #1 → 수정 → 검토 → 초안 PR #2 → 사람 병합 → 업무 완료. 결함 2건(러너 재시작 뒤 영구 정지, App Contents 권한)은 수정됨. 에이전트의 #1 수정도 `service` 로 가져옴(`d5c2568`).
- OpenArchive: 소스 중지, #112 운영자 종료, App 설치 없음. 쓰기 금지 그대로. 로컬 `/Users/kje/demo/OpenArchive-worktrees/task-e3df709051a2` 에 push 안 된 작업 폴더가 남아 있다(정리는 사용자 결정).

## 방향 전환 기록 (2026-09-29)

2026-09-29 실연동 중단. **OpenArchive 는 오픈소스 공모전 출품작이라 커밋·이슈·PR·댓글 하나하나가 심사 대상 — Runloom·에이전트가 쓰지 않는다**(읽기만). #112 를 맡긴 직후 봇 상태 댓글 1개가 달려 삭제했고, 러너(launchd bootout)·중앙 워커(`docker compose -p runloom … stop worker`)를 멈췄다. push·PR 은 없었다. GitHub App `runloom-gwufov` 의 OpenArchive 설치를 제거했다(App 자체는 남음, 설치 0). Runloom DB 의 OpenArchive 업무 22·기준선 24 는 남아 있다. **워커·러너를 다시 켜기 전에 실연동 대상을 정한다.**

**설계 계획: [재설계 계획](product/REDESIGN_PLAN.md)**(2026-09-29) — 목업 https://claude.ai/artifact/Jx6Pa7PmRZo1hmvFuiH66C, phase 제안 13-task-model → 14-team → 15-work-ui → 16-jira → 17-triage → 18-monitor. 실연동은 새 비공개 저장소(runloom 복사)·새 Jira Cloud 사이트에서.

사용자 판단(2026-09-29): 앱 사용 자체가 불편해 UX·UI 를 처음부터 다시 봐야 한다. 업무 가져오기가 가장 불편 — GitHub·**Jira 가져오기와 Jira 에 업무 등록**이 필수, 가져올 때 소스별 양식을 Runloom 업무 양식으로 맞춰 등록, 이슈 옆에 PR 도 보여야 한다. 같은 서비스는 없지만 비슷한 서비스를 벤치마킹한다. 조사 문서: `docs/research/2026-09-29-*.md`(로컬 에이전트 보드, 이슈→PR 에이전트, 가져오기·필드 매핑, Jira 연동).

에이전트의 ssh(OpenArchive HA 3노드 VM 실측) 질문: 러너가 사용자 계정으로 Claude Code 를 띄우므로 연결 자체는 사용자 터미널과 같은 조건이다. 막는 것은 `connector/claude.py` `ALLOWED_TOOLS` 고정(파일 편집·pytest·git diff/status)과 환경 허용 목록(`SSH_AUTH_SOCK` 없음)이다 — 등록별 허용 명령 선언으로 열 수 있다(미구현).

## 이전 작업: `12-real-repo` 실연동 (중단)

**12-real-repo 구현 완료** (2026-09-28, 브랜치 `feat-12-real-repo`, step 0~10 — `phases/12-real-repo/index.json`). 설계는 [ADR-0018](adr/0018-real-repo-cycle.md)·ARCHITECTURE "실제 저장소 순환 — phase 12"·CONTRACT 14절. 들어간 것: 저장소 카드 [러너 붙이기] → `install-runner.sh --server --code --repo` 한 명령(setup = connect + register, register 가 수정·검토 Agent 를 만듦), 러너가 60초마다 fetch 해 기본 브랜치 최신을 기준 커밋으로 보고, worktree 에 `--link` 심볼릭 링크·`--env` 환경(값은 러너에만), 결과 브랜치 `task/<id>` push, 검토 승인 뒤 App 으로 초안 PR(`Fixes #N`) → 병합 추적 → 완료·지표, 알림 웹훅(사람 차례·PR 확인·실패, `/operator/notifications`, 스키마 v8). 대역 e2e `tests/e2e/test_real_repo.py` 가 이 한 줄기를 가짜 GitHub·bare 저장소·가짜 알림 수신으로 돈다([VERIFICATION_LOG](VERIFICATION_LOG.md) 2026-09-28 절).

[MVP 계획](product/MVP_PLAN.md)의 목표: **2026-10-02 까지 Runloom 단독으로 OpenArchive(`jeongeundev/OpenArchive`) 실제 이슈 3건 이상을 이슈 → 수정 → 검토 → 사람 차례 알림까지 사람의 전달 없이 진행하고, 도입 전후를 지표로 보인다.** 남은 것은 사용자와 함께 하는 실연동이다.

실연동 준비 목록(순서대로 — 사용자 지시·확인 뒤에만 한다):
1. 완료(2026-09-29): `service` 병합(2e06218) → 백업 `20260929T012810Z` → `install.sh` 재실행, 스키마 8, 업무 22·기준선 24 보존.
2. **App 권한 승인** — 기존 App `runloom-gwufov` 의 Pull requests 를 Read and write 로 올리고 설치(jeongeundev)에서 새 권한 승인([SELFHOST "App 권한 올리기"](SELFHOST.md#app-권한-올리기--phase-12-전에-만든-app)).
3. **에이전트 전용 테스트 DB** — 2026-09-29 컨테이너 `runloom-agent-db`(pgvector pg17, vector 0.8.6, `127.0.0.1:5435`, 볼륨 `runloom-agent-db`)로 띄움 — 5433 은 `opensql-db-1`, 5434 는 `application-db-1` 이 사용 중. 러너 `--env DATABASE_URL=postgresql://openarchive:openarchive@localhost:5435/openarchive` 로 넘긴다.
4. 완료(2026-09-29) **OpenArchive 준비**: 원본 폴더 `/Users/kje/demo/OpenArchive` 를 `git pull --ff-only` 로 GitHub `main`(`2383ce9`)에 맞추고 `backend/.venv` 에 `pip install -e '.[dev]'`(pytest·ruff 가 없었다), `frontend` 에 `npm install`. check.sh 는 고치지 않았다 — 대신 러너 `--env PYTHONPATH=.` 로 링크된 편집 설치 venv 가 작업 복사본 코드를 먼저 잡게 한다(확인: 없으면 원본 `app`, 있으면 복사본 `app`). Next 16 Turbopack 이 링크된 `node_modules` 를 "points out of the filesystem root" 로 거부해 러너에 `--copy`(APFS 복제, 4.6초)를 더했다(`781ce2a`). GitHub `main` 클론 + 링크 venv + 복제 node_modules + `PYTHONPATH=.` + 5435 DB 로 `bash scripts/check.sh` 통과(exit 0, 약 3분, backend 880·frontend 242). **main 에 의존성이 늘면 원본 폴더를 다시 pull + pip/npm install 해야 한다**(venv·node_modules 는 원본 것을 쓴다).
5. **러너 붙이기** — 카드 [러너 붙이기] → 나온 명령 끝에 붙여 실행:
   `--repo /Users/kje/demo/OpenArchive --verify "check=bash scripts/check.sh" --link backend/.venv --copy frontend/node_modules --env PYTHONPATH=. --env DATABASE_URL=postgresql://openarchive:openarchive@localhost:5435/openarchive`
   `origin` 은 https(osxkeychain) — launchd 에서 push 가 되는지 첫 업무에서 확인.
6. **알림** — Discord 웹훅 URL 을 `/operator/notifications` 에 저장 → [테스트 보내기].
7. **이슈 3건 이상** — 사용자가 손대지 않은 이슈를 골라 [에이전트에게 맡기기] → PR 병합까지. [VERIFICATION_LOG](VERIFICATION_LOG.md) 의 "실연동 기록 틀" 에 이슈마다 기록(phase 11 미확인 `setup_action`·설치 URL `state` 복귀 포함). 끝나면 `/metrics` 의 기준선(24건, 중앙값 7시간 28분) 대 도입 후.

phase 12 에서 남긴 것: 실제 github.com 의 초안 PR 생성(초안 미지원 422 문구 포함)·권한 올리기 화면·실제 Discord 전송은 미확인. PR 을 못 연 업무는 다시 열지 않는다(사람 요청). 러너는 실행 중에는 fetch 하지 않는다. `--env` 값은 명령행 인자라 실행 중 `ps` 에 보인다. step 10 에서 고친 결함: 러너가 결과 봉투를 가릴 때 `sk-` 패턴이 `task-<12 hex>` 안의 `sk-…` 를 키로 보고 `task_id` 를 `task-***` 로 바꿔 모든 수정 결과가 판정 `result_ids_match` 에서 실패하던 문제(step 4 회귀, `connector/masking.py`), 원본 이슈 댓글·`/operator/github` 의 "자동으로 푸시하지 않음" 문구.

phase 11 에서 남긴 것: 수집 실패(rate limit·권한)는 DB 에 저장하지 않아 카드에 안 보인다(워커 로그만). 설치에서 빠졌다 다시 들어온 저장소의 소스는 멈춘 채다(고급 설정 `수집 켜기`). manifest code 는 callback URL 이라 접근 로그에 남는다(한 번 쓰면 무효). 브라우저 스크립트(자동 제출 폼·`data-json-action`)는 서버 렌더만 확인했다.

phase 9 e2e 에서 발견한 결함 — 수정됨(step 13): 재작업 상한 1 에서 수정 요청 검토가 재작업을 시작시킨 뒤, 재작업 결과가 판정되기 전 tick 이 같은 검토를 다시 평가하면 `domain/task_followup.py` `_after_review` 가 `rounds_used(1) >= 상한(1)` 으로 `rework_limit_reached` 사람 요청을 하나 더 만들던 문제. 이제 그 검토가 이미 재작업(`rework:{검토 실행}`, 워커가 수정 Task 실행의 `start_key` 로 `handled_cause_keys` 에 넣음)을 일으켰으면 상한 판단 전에 `none`("이미 재작업을 시작한 검토") 을 낸다. 회귀: `tests/workflow/domain/test_task_followup.py`·`tests/workflow/server/test_task_cycle.py`, e2e `tests/e2e/test_metrics.py` 는 사람 요청 0·개입 1 로 단정.

공모전 관련 작업은 더 하지 않는다(2026-09-27 사용자 결정). 공개 데모 VM 은 2026-10-09 삭제했다([ADR-0029](adr/0029-demo-shutdown-main-single-branch.md)). 공모전 문서는 [보관 자료](archive/2026-09-27-contest-and-history/)로 옮겼다.

## 지금 상태

| 항목 | 상태 |
|---|---|
| 브랜치 | `main` 하나(2026-10-09 `service` 를 합침). 새 phase 는 `main` 에서 `feat-*` 로 분기하고 끝나면 `--no-ff` 병합. 원격 푸시는 사용자 지시 때만 |
| 완료 phase | 0-mvp, 1-diag-fix, 2-model-compare, 5-scripted-demo(공모전 데모), 6-typed-handoff([ADR-0009](adr/0009-registered-kinds-and-succession-rules.md)), 7-n8n-gateway([ADR-0010](adr/0010-n8n-inbox-and-callback.md)), 8-github-task-cycle([ADR-0014](adr/0014-github-task-cycle.md)), 9-measure([ADR-0015](adr/0015-measurement-events-and-baseline.md)), 10-selfhost([ADR-0016](adr/0016-selfhost-docker-fixed-workspace.md)), 11-github-app([ADR-0017](adr/0017-github-app-connection.md)) — 모두 `service` 병합됨(미푸시). 12-real-repo([ADR-0018](adr/0018-real-repo-cycle.md)) — `service` 병합됨(2e06218). 4-claude-issues 는 step 3 에서 종료 |
| 계획만 | `3-limit-wait`([ADR-0007](adr/0007-usage-limit-wait-policy.md), 사용량 한도 대기). 실사용에서 한도에 걸리는 빈도를 보고 당긴다 |
| 검증 | 2026-09-29 `service` 115f529 기준 `python3 -m pytest -q` 2932 passed/68 skipped, `ruff` 통과, `WORKFLOW_E2E=1` `test_github_app`·`test_real_repo` 13 passed. 이전: 2026-09-28 `feat-12-real-repo` step 10 기준 2918 passed/68 skipped. `WORKFLOW_E2E=1` e2e 67 passed/1 skipped(`test_real_repo.py` 7 포함), `WORKFLOW_DOCKER=1` 셀프호스트 e2e 는 phase 11 브랜치에서 1 passed(phase 12 에서 다시 돌리지 않음) |
| 실연동 | 2026-09-23 실제 GitHub·실제 Claude 로 `bug_fix` → `code_review` 1회 통과([VERIFICATION_LOG](VERIFICATION_LOG.md)). `changes_requested` 재작업은 실연동 미관찰. 2026-09-27 실제 GitHub App 생성·설치·이슈 수집(17건)·기준선(24건) 성공. 2026-09-29 실연동 1(sandbox): 실제 push·App 초안 PR·병합 추적·Discord 알림까지 통과 |
| 사용자 결정 대기 | 실연동 자원 정리(`jeongeundev/runloom-live-test`, `../runloom-live-test`, `../runloom-live-state/`, `~/.runloom-live.env`), 워커 httpx 로그의 callback URL `signature` 노출 처리 |

## 재개 방법 — 하네스

```bash
cd /Users/kje/00_Workspace/01_Coding/project/workflow
git checkout main
python3 scripts/execute.py {task-name} --engine claude   # phases/{task-name}/index.json + step{N}.md
```

- 워크플로우 전체는 `.claude/commands/harness.md`. `--engine claude` 를 쓴다(Codex 사용량 소진).
- 세션 한도(429)로 step 이 3회 실패하면 코드 문제가 아니다. `index.json` 의 그 step 을 `pending` 으로 되돌리고 `error_message` 를 지운 뒤 재개한다.
- 하네스가 도는 동안 같은 작업 트리에서 편집하지 않는다(필요하면 별도 worktree).
- 구현은 사용자가 "진행해" 로 지시했을 때만 한다. 실배포·유료 호출은 별도 지시 없이 시작하지 않는다. 결정 질문은 압축 용어 대신 장면으로 풀어 2~3개씩 묻는다.
