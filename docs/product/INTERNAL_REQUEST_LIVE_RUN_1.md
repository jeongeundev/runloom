# 사내 요청 실연동 1회차 — 공개 사례 K1, 우회 설정

작성일: 2026-10-04
상태: 준비 완료. 실행 전.

[공개 사례 K1](INTERNAL_REQUEST_PUBLIC_CASES.md#사례-k1--업그레이드-후-kube-proxy-기동-실패-재전달--우회책--원인-확정)을 셀프호스트(스키마 v23)에서 세 계정이 실제 러너·Claude CLI 로 진행한다. 목적은 **사람 사이 흐름**(요청 → 수락 → 질문 → 조사 → 검토 → 판단 → 반환 → 재개)의 결함과 혼란을 찾는 것이다. 조사 품질은 평가하지 않는다.

## 이번 회차의 우회와 한계

- **능력 우회**: 러너 등록 에이전트의 능력은 `code.fix`·`code.review`·`code.triage` 로 고정이다(`adapters/repo.py` 등록 경로). 조사 종류의 능력 코드를 `code.review` 로 등록해 기존 에이전트를 후보로 만든다. `list_kinds` 는 내장 종류를 먼저 돌려주므로 `code.review` 로 찾는 기존 GitHub 검토 매칭은 내장 `code_review` 를 그대로 쓴다.
- **자료 없음**: 사용자 정의 종류는 빈 인계 디렉터리에서 `Read`·`Glob`·`Grep` 만, 네트워크 없이 돈다. 에이전트는 요청 본문과 답변만 본다. K1 의 원인 확정(코드의 `!=` 비교)은 재현할 수 없다.
- **에이전트**: `runloom-sandbox` (OpenArchive 는 쓰지 않는다).
- **원업무**: runloom-sandbox 에 이슈 1건을 만들어 업무로 가져온다(`runloom` 라벨 없음 — 자동 실행 안 됨, 판단 에이전트 미지정 — 자동 판단 안 됨).

## 사전 점검 (2026-10-04 15:55 KST)

- 원업무: sandbox #5 → `RUN-26`, 새로 들어옴·담당 없음, 자동 실행 없음.
- 소유자 승인: 두 에이전트 정책 `run` — 처리 담당이 맡겨도 승인 대기 없음.
- 멤버 권한: `member` 역할에 `delegate`·`respond` 가 있어 수락·조사 시작·검토·판단 응답 가능. 종류·담당 범위 등록은 관리자.
- 러너: launchd `com.workflow.selfhost.connector` 실행 중, 5초마다 claim. `runloom-sandbox` 등록 도구 `claude`, Claude Code 2.1.289 호출 확인.
- 무관한 잡음: 옛 등록 `local-demo-report`(origin 없음)의 `git fetch` 실패가 매분 stderr 에 남는다(로그 13MB). 이번 실행과 무관.

## 역할과 브라우저

| 역할 | 계정 | 브라우저 |
|---|---|---|
| 요청자 R | 기존 관리자 | 평소 창 |
| 처리 담당 N | 새 멤버 (예: `n@runloom.local`) | 다른 브라우저 또는 프로필 |
| 판단 담당 J | 새 멤버 (예: `j@runloom.local`) | 시크릿 창 |

로그인 쿠키가 브라우저별이라 세 계정을 같은 창에서 동시에 쓸 수 없다.

## 준비 (관리자 R)

1. `/team` — 멤버 초대 2개(N, J). 초대 링크를 각 브라우저에서 열어 비밀번호를 정한다.
2. `/connect?tab=kinds` — 종류 등록:
   - 종류 `incident_investigation`, 이름 `장애 조사`
   - 능력 코드 `code.review` (우회), scope 키 `repository_id`
   - 입력 종류: 선택 안 함
   - outcomes: `cause_found, needs_information, unresolved`
   - 지시: 아래 "조사 지시" 블록
3. `/connect?tab=team` — 담당 범위 추가: 시스템 `kube_proxy`, 요청 유형 `investigation`, 수신자 N, 판단 담당자 J, 에이전트 `runloom-sandbox`.

조사 지시:

```text
요청 본문과 추가 질문·답변만 근거로 원인 후보를 정리한다.
1) 증상 요약 2) 원인 후보와 각각의 근거·반증 3) 요청자가 지금 쓸 수 있는 우회책 4) 원인 확정에 필요한 확인 항목.
코드나 로그를 직접 보지 못했으면 그렇다고 쓰고, 확인하지 못한 것은 '미확인'으로 둔다.
원인이 근거로 좁혀졌으면 cause_found, 정보가 모자라면 needs_information, 그 외는 unresolved.
```

## 진행

| # | 누가 | 어디서 | 할 일 | 기록할 것 |
|---|---|---|---|---|
| 1 | R | 원업무 패널 "다른 담당자에게 요청" | 담당 범위 `kube_proxy · investigation` 선택, 아래 "요청 본문" 붙여 넣고 보내기 | 담당을 찾기 쉬웠나 |
| 2 | N | `/requests` | 받은 요청 수락 | 알림이 왔나, 어디서 알았나 |
| 3 | N | `/requests` | 정보 질문: "업그레이드 전 kube-proxy 1.35 로그의 nf_conntrack_max 값과, 호스트에서 직접 설정한 nf_conntrack_max 값이 있으면 알려주세요." | |
| 4 | R | `/requests` 또는 원업무 패널 | 아래 "답변" 붙여 넣기 | 질문을 어디서 봤나 |
| 5 | N | `/requests` | 조사 종류 `장애 조사 · repository_id=jeongeundev/runloom-sandbox` 고르고 조사 시작 | 실행이 시작됐는지 어디서 보이나 |
| 6 | N | 조사 작업 화면 | 결과 읽고 검토 승인 | 걸린 시간, 결과가 쓸 만한가 |
| 7 | N | `/requests` | 판단 요청 — 쟁점: "우회책(호스트 nf_conntrack_max 를 1048576 으로 낮추기)을 요청자에게 안내해도 되는가" | |
| 8 | J | `/requests` | 판단 승인 + 사유 | 판단 담당이 맥락을 이해할 수 있었나 |
| 9 | N | `/requests` | 검토된 결과 반환 | |
| 10 | R | 원업무 패널 | 반환 결과 확인, 업무 재개 기록: "호스트 값을 낮춰 kube-proxy 기동 확인, 업그레이드 재개" | 결과를 그대로 쓸 수 있었나 |

요청 본문:

```text
클러스터를 1.35 → 1.36 으로 업그레이드한 뒤 LXC 컨테이너 안의 control-plane 노드에서 kube-proxy 가 시작되지 않습니다.
1.36.4 로그: sysctls.go:147] "Setting nf_conntrack_max" nfConntrackMax=1048576 → 설정 실패 후 종료
1.35.8 로그: conntrack.go:57] "Setting nf_conntrack_max" nfConntrackMax=262144 → 정상
워커 노드(LXC 아님)는 정상입니다. 업그레이드가 여기서 멈춰 있습니다. 원인과 지금 쓸 수 있는 우회책이 필요합니다.
```

답변:

```text
1.35 로그의 262144 는 8코어 호스트 값입니다. 40코어 호스트는 부팅 스크립트로 nf_conntrack_max=1310720, hashsize=327680 을 직접 넣습니다(코어당 32768·8192). LXC 안에서는 이 값을 바꿀 수 없습니다.
```

## 결과

(실행 후 기록)
