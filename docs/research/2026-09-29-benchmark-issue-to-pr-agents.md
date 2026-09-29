# 벤치마크: 이슈/티켓 → 코딩 에이전트 → PR 서비스

조사일: 2026-09-29. 1차 출처(공식 문서·공식 블로그)만 인용했다. 문서에서 확인하지 못한 항목은 "확인 못 함"으로 적었다. 공식 문서는 자주 바뀌므로 링크를 다시 확인할 것.

## 요약

7개 제품 모두 입구는 **"티켓에 에이전트를 사람처럼 배정(assign)하거나 @멘션"** 으로 거의 같다. Linear·Jira 쪽에는 트래커가 제공하는 "에이전트 세션" 패널이 있어 진행 상황이 티켓 안에 실시간으로 뜬다. 에이전트에게 넘기는 지시는 따로 템플릿을 쓰지 않고 **티켓 제목·본문·댓글(+라벨·커스텀 필드)을 그대로 넘긴다.** 여기에 배정할 때 "추가 프롬프트" 한 칸을 받는 방식이 일반적이다. 인수 조건(acceptance criteria)은 필수가 아니다. 대신 Factory·OpenHands 문서는 인수 조건을 쓰라고 권하고, Rovo Dev는 리뷰 단계에서 인수 조건 충족 여부를 판정한다. 실행 위치는 대부분 벤더 클라우드 VM/컨테이너다. 예외는 사용자 러너에서 도는 GitHub Copilot(Actions, self-hosted 가능), Factory droid-action, OpenHands resolver(모두 GitHub Actions)다. 산출물은 **작업당 PR 1개(대개 draft)** 이고 사람은 **PR 댓글 @멘션으로 수정을 요청**한다. 후속 작업을 자동으로 만드는 일은 드물다. 대신 **리뷰 에이전트(Bugbot, Devin Review, Codex review, Factory review, Rovo Dev review)가 PR을 검사하고 수정 에이전트를 다시 띄우는 루프**가 대세다. Runloom처럼 "로컬 구독 에이전트 + 규칙 기반 후속 등록(수정→리뷰→재작업→사람 차례)"을 한 서비스에서 명시적으로 보여 주는 제품은 없다. 다만 UX 언어(배정·세션 로그·draft PR·@멘션 재작업)는 이미 업계 표준이 있으므로 그것을 따르는 편이 낫다.

## 비교표

| 제품 | 입구 (트래커 / 방식) | 지시 구성 | 실행 위치 | 산출물 | 트래커로 보고 | 사람 반복 | 후속/리뷰 |
|---|---|---|---|---|---|---|---|
| GitHub Copilot cloud agent | GitHub 이슈 assign, 에이전트 패널, PR `@copilot`, Jira·Linear·Slack·Teams·Azure Boards, 자동화 | 제목·본문·기존 댓글 + 선택 프롬프트, 베이스 브랜치, 커스텀 에이전트, 모델 | GitHub Actions (self-hosted ARC 가능, Ubuntu x64/Windows) | `copilot/` 브랜치, 작업당 PR 1개, 스스로 ready 전환 불가 | Jira 채팅 패널 실시간 스트림, Linear Activity 알림 | PR `@copilot` 댓글 또는 직접 push | 이슈 생성: 문서에 없음 |
| OpenAI Codex (cloud) | Linear assign·`@Codex`·triage rule, GitHub `@codex`, Slack, 웹 | 이슈 내용 (템플릿 확인 못 함) | OpenAI 컨테이너 (setup script, 인터넷 기본 차단) | 요약+diff → 사용자가 PR 생성 | Linear Activity + 채팅 링크 | 후속 프롬프트 | `@codex review` / 자동 리뷰 (P0/P1만) |
| Cursor cloud agents + Bugbot | Linear assign·`@Cursor`, Slack, GitHub PR `@cursor`, 웹, API | 이슈 내용, `[repo=…]`·repo 라벨로 저장소 지정 | Cursor 클라우드 VM (`.cursor/environment.json`) | PR + 스크린샷·영상·로그 | Linear 실시간 상태 | Linear에서 `@Cursor` 다시 멘션 | Bugbot 자동 리뷰 → Autofix 에이전트 |
| Devin | Jira·Linear assign, `devin` 라벨, `!plan`/`!implement` 플레이북 라벨, `@Devin`, Slack | 플레이북 + 티켓. 범위 산정 모드에서 요약·계획·신뢰도 댓글 | Devin VM 스냅샷 (blueprint YAML) | PR, Jira remote link | 댓글·세션 링크·Linear 계획 UI | PR 댓글에 자동 응답, `@Devin` | Devin Review, 이슈 생성 권한, 예약 작업으로 티켓 생성 사례 |
| Factory (Droids) | Linear assign·`@Factory`, Jira "Open in Factory" 링크, GitHub `@droid` (Action) | 제목·본문·라벨·댓글. 인수 조건 권장 | Factory 원격 컴퓨터 또는 사용자 GitHub Actions 러너 | PR URL 반환, 이슈에 첨부 | Linear 에이전트 세션 | 같은 이슈에 다시 `@Factory` | 코드 리뷰 (P0–P3) |
| Atlassian Rovo Dev (Jira Coding Agent) | Jira "Start work" 버튼, 에이전트에 assign, Jira 자동화 액션 | 작업 항목 + 사용자 프롬프트·저장 프롬프트 | Atlassian 클라우드 샌드박스 (setup script, secrets) | draft PR (옵션), 병합하지 않음 | 작업 항목의 Agent sessions 섹션 | 채팅 패널 (코드 줄 선택 참조) | 코드 리뷰에서 인수 조건 충족 판정 |
| OpenHands | GitHub `openhands` 라벨·`@openhands`, Jira·Linear·Slack. 셀프 resolver는 `fix-me` 라벨 | 이슈/티켓 내용 (resolver 라벨은 전체 댓글, 멘션은 그 댓글만) | OpenHands Cloud 또는 사용자 GitHub Actions | 성공하면 draft PR, 실패하면 브랜치만 push | 이슈에 "작업 중" → 요약+PR 링크 댓글 | PR `@openhands` | SDK로 리뷰 워크플로우 |

---

## 1. GitHub Copilot cloud agent (구 coding agent)

1. **입구**: 이슈 Assignees에서 Copilot을 선택한다. 그 밖에 에이전트 패널, PR 댓글의 `@copilot`, 외부 도구(Microsoft Teams, Slack, Azure Boards, Jira, Linear), 일정·이벤트 기반 자동화에서도 시작할 수 있다 ([about](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent), [use on GitHub](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)). Jira에서는 Assignee 필드 지정, `@GitHub Copilot` 댓글, Jira 자동화 규칙의 "Use GitHub Copilot" 액션으로 시작한다. Jira는 AI 기능이 켜져 있고 Rovo가 활성화되어 있어야 한다 ([Jira](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/integrate-coding-agent-with-jira)). Linear에서는 Assign 드롭다운으로 지정한다 ([Linear](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/integrate-coding-agent-with-linear)).
2. **지시 구성**: 이슈 제목, 설명, 배정 시점까지 달린 댓글, 추가 지시를 받는다. **배정 이후에 달린 이슈 댓글은 인식하지 못한다.** 배정할 때 베이스 브랜치, 대상 저장소, "Optional prompt", 커스텀 에이전트, 모델을 고를 수 있다 ([use on GitHub](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)). Jira에서는 제목·설명·라벨·댓글과 인수 조건 같은 Atlassian 커스텀 필드를 넘긴다. 저장소 이름은 사용자가 적어 줘야 한다 ([Jira](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/integrate-coding-agent-with-jira)). Linear에서는 설명 전체와 댓글을 넘긴다 ([Linear](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/integrate-coding-agent-with-linear)). 필수 필드나 템플릿은 없다.
3. **실행 환경**: GitHub Actions 기반의 임시 개발 환경에서 테스트와 린터를 돌린다 ([about](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)). 환경은 `.github/workflows/copilot-setup-steps.yml`로 설정한다. job 이름은 반드시 `copilot-setup-steps`여야 하고, `steps`·`permissions`·`runs-on`·`services`·`snapshot`·`timeout-minutes`만 바꿀 수 있다. timeout은 최대 59분이고, 이 파일은 기본 브랜치에 있어야 동작한다. **Self-hosted 러너를 지원한다**: ARC 또는 scale set으로 운영하는 일회용(ephemeral) 러너를 권장하고, Ubuntu x64와 Windows 64-bit만 되며, 이 경우 내장 방화벽을 꺼야 한다 ([customize env](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-environment)). 인터넷 접근은 방화벽으로 제한한다. 에이전트는 git 명령을 직접 실행하지 못하고 단순 push만 할 수 있다 ([risks](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations)). 저장소의 커스텀 지시 파일, MCP 서버, Copilot Memory, 커스텀 에이전트를 쓸 수 있다 ([about](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)).
4. **산출물·보고·반복**: 작업 하나에 PR은 정확히 하나다 ([about](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)). 커밋 작성자는 Copilot이고, 배정한 사람이 co-author로 들어간다 ([risks](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations)). Jira에서는 채팅 패널에 활동이 실시간으로 흐르고 GitHub 세션 링크가 붙는다. Linear에서는 작업이 끝나면 Activity에 알림이 뜬다 (위 Jira/Linear 문서). 수정은 PR에 `@copilot` 댓글을 달거나 브랜치에 직접 push해서 요청한다 ([review PRs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/review-copilot-prs)).
5. **후속**: 이슈를 스스로 만든다는 내용은 문서에 없다 ([use on GitHub](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)). 리뷰 에이전트와의 자동 연결은 확인 못 함.
6. **UI**: 에이전트 패널과 에이전트 페이지에서 세션 목록을 본다. 세션 로그에는 내부 추론, 사용한 도구, 토큰 사용량, 소요 시간이 나온다. "Stop session"을 누르면 Actions run이 끝나고 이미 push한 커밋은 남는다. 세션은 저장소 안에서 기본으로 공유된다 ([track sessions](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/track-copilot-sessions)).
7. **제약**: 저장소 쓰기 권한이 있는 사용자만 트리거할 수 있다. push는 `copilot/` 브랜치 하나에만 할 수 있다. 사람이 "Approve and run workflows"를 누르기 전에는 CI가 돌지 않는다. 에이전트는 Ready for review 전환, 승인, 병합을 하지 못한다 ([risks](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations)). PR을 요청한 사람의 승인은 필수 승인 수에 포함되지 않는다 ([review PRs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/review-copilot-prs)). 세션은 최대 59분이다.

## 2. OpenAI Codex (cloud) — Linear·GitHub 연동

1. **입구**: Linear에서는 이슈를 Codex에 배정하거나 `@Codex`로 멘션한다. `@Codex fix this in openai/codex`처럼 저장소를 적을 수 있고, Linear triage rule로 자동 배정할 수도 있다. 자동 배정이면 이슈 작성자의 계정으로 실행된다 ([Linear](https://learn.chatgpt.com/docs/third-party/linear)). GitHub에서는 PR 댓글 `@codex review`, `@codex security review`로 리뷰를 요청하고, 그 밖의 `@codex` 요청은 PR 맥락을 가진 클라우드 채팅을 띄운다 ([GitHub](https://learn.chatgpt.com/docs/third-party/github)). 웹·GitHub·GitLab·Linear·Slack에서 시작할 수 있다 ([cloud](https://learn.chatgpt.com/docs/cloud)).
2. **지시 구성**: 이슈 내용을 받는다. 환경은 Linear가 추천한 저장소와 가장 잘 맞는 것을 고르고, 애매하면 최근에 쓴 환경을 쓴다 ([Linear](https://learn.chatgpt.com/docs/third-party/linear)). 템플릿이나 필수 필드는 확인 못 함.
3. **실행 환경**: 범용 컨테이너 이미지에서 돈다. 순서는 저장소 checkout → setup script(+캐시 재사용 시 maintenance script) → 인터넷 설정 적용 → 에이전트 루프다. **secrets는 setup 단계에서만 보이고 에이전트 단계 전에 제거된다.** 에이전트 단계의 인터넷은 기본 차단이고 제한적·전면 허용을 고를 수 있다. 컨테이너는 최대 12시간 캐시한다 ([environments](https://learn.chatgpt.com/docs/environments/cloud-environment)). 리뷰 규칙은 `AGENTS.md`의 `## Code Review Rules` 절에 쓴다 ([GitHub](https://learn.chatgpt.com/docs/third-party/github)).
4. **산출물·보고·반복**: Linear에서는 Activity에 업데이트가 올라오고 채팅 링크가 붙는다. 끝나면 요약과 채팅 링크를 남기고, **사용자가 거기서 PR을 만든다** ([Linear](https://learn.chatgpt.com/docs/third-party/linear)). 요약과 diff를 확인한 뒤 후속 프롬프트로 수정하거나 PR을 연다 ([cloud](https://learn.chatgpt.com/docs/cloud)). draft 여부와 브랜치 규칙은 확인 못 함.
5. **후속**: 자동 리뷰를 켜면 모든 새 PR을 검토하고 P0/P1만 GitHub 리뷰로 남긴다 ([GitHub](https://learn.chatgpt.com/docs/third-party/github)). 이슈를 생성하는지는 확인 못 함.
6. **UI**: 환경별 작업 이력, 상태, 시각, 작업 로그, 요약+diff 화면이 있다 ([cloud](https://learn.chatgpt.com/docs/cloud)).
7. **제약**: 확인 못 함 (PR 개수와 브랜치 명명 규칙은 문서에 없음).

## 3. Cursor cloud agents (구 background agents) + Bugbot

1. **입구**: Linear에서 assignee를 Cursor로 지정하거나 `@Cursor`를 멘션한다 ([Linear](https://cursor.com/docs/integrations/linear)). 데스크톱, 웹, iOS, Slack `@cursor`, GitHub·Bitbucket PR 댓글 `@cursor`, API에서도 시작한다 ([cloud agent](https://cursor.com/docs/cloud-agent)).
2. **지시 구성**: 이슈 내용을 자동으로 분석하고 개발과 관계없는 작업은 걸러낸다. 저장소를 고르는 우선순위는 본문·댓글의 `[repo=owner/repo]` → 이슈 라벨(부모 "repo", 자식이 저장소) → 프로젝트 라벨 → 대시보드 기본값이다 ([Linear](https://cursor.com/docs/integrations/linear)).
3. **실행 환경**: 격리된 클라우드 VM에서 돈다. `.cursor/environment.json`, 스냅샷, Dockerfile로 환경을 만들고, "Builds"가 백그라운드에서 환경을 미리 준비한다. secrets는 대시보드에서 팀 단위로 관리한다. 외부 도메인 제한과 Tailscale 사설망 연결을 지원한다 ([cloud agent](https://cursor.com/docs/cloud-agent)). GitHub 앱 권한에는 issues(리뷰 중 발견한 버그·작업 추적), checks, actions(CI 재실행), 브랜치 보호 규칙 읽기가 들어 있다 ([GitHub](https://cursor.com/docs/integrations/github)).
4. **산출물·보고·반복**: PR과 함께 스크린샷, 영상, 로그 같은 아티팩트를 남기고, 원격 데스크톱으로 직접 테스트할 수 있다 ([cloud agent](https://cursor.com/docs/cloud-agent)). Linear에 실시간 상태가 뜨고, 다시 `@Cursor`를 멘션하면 **재배정 없이 실행 중인 에이전트에게 전달된다** ([Linear](https://cursor.com/docs/integrations/linear)). draft 여부와 브랜치 명명 규칙은 확인 못 함.
5. **후속·리뷰**: Bugbot은 PR이 갱신될 때마다 자동으로, 또는 `cursor review`·`bugbot run` 댓글로 리뷰한다. "Fix in Cursor"/"Fix in Web" 링크를 제공하고, **Autofix를 켜면 발견한 버그를 고칠 클라우드 에이전트를 자동으로 띄워 새 브랜치나 기존 PR 브랜치에 push한다.** 리뷰 규칙은 `.cursor/BUGBOT.md`에 쓴다 ([Bugbot](https://cursor.com/docs/bugbot)). 이슈를 생성하는지는 문서에 명시되어 있지 않다.
6. **UI**: Cursor 대시보드(cursor.com/agents)에서 세션 상세를 본다. URL로 공유한 실행 기록은 읽기 전용이고, 관리자가 "team follow-ups"를 켜면 동료도 이어서 지시할 수 있다 ([cloud agent](https://cursor.com/docs/cloud-agent)).
7. **제약**: 실행 기록을 보려면 저장소 접근 권한이 따로 필요하다 ([cloud agent](https://cursor.com/docs/cloud-agent)). 그 밖의 제약은 확인 못 함.

## 4. Devin (Cognition)

1. **입구**: Jira에서는 Devin 서비스 계정에 배정, `!plan`·`!implement` 같은 플레이북 라벨, `Devin` 라벨(기본 플레이북), `@Devin` 댓글로 시작한다 ([Jira](https://docs.devin.ai/integrations/jira)). Linear에서는 배정(기본 플레이북), `!plan`·`!implement`·`!triage`·`!review` 라벨, `@Devin` 멘션으로 시작하고, 멘션하면 그 댓글이 지시가 된다 ([Linear](https://docs.devin.ai/integrations/linear)). Slack에서는 채널에서 `@Devin`을 부른다 ([Cognition 블로그](https://cognition.com/blog/how-cognition-uses-devin-to-build-devin)).
2. **지시 구성**: **플레이북(재사용 절차) + 티켓 내용**으로 지시를 만든다. 범위 산정(scoping) 전용 모드에서는 요약, 구현 계획, 신뢰도 추정을 댓글로 먼저 올린다 ([Jira](https://docs.devin.ai/integrations/jira)). Cognition은 "Bug 라벨이 붙으면 `!triage-bug` 플레이북이 돌아 원인과 수정안을 Linear에 올린다"는 내부 사례를 공개했다 ([블로그](https://cognition.com/blog/how-cognition-uses-devin-to-build-devin)).
3. **실행 환경**: 저장소와 도구를 미리 설치한 Linux VM 스냅샷에서 매 세션을 부팅한다. 설정은 대화형으로 하거나 YAML blueprint로 선언한다. secrets는 blueprint 편집기의 Secrets 탭에 두고 `$VAR`로 참조한다 ([environment](https://docs.devin.ai/onboard-devin/environment)). GitHub 권한은 사용자 개인 권한이 아니라 조직 단위로 받는다 ([GitHub](https://docs.devin.ai/integrations/gh)).
4. **산출물·보고·반복**: PR을 만들면 PR URL이 Jira 이슈의 remote link로 자동으로 붙는다. 세션 링크와 진행 댓글도 남긴다 ([Jira](https://docs.devin.ai/integrations/jira)). Linear에서는 명령·파일 편집·진행 요약이 실시간 피드로 올라오고, **Devin의 할 일 목록이 Linear 계획(plan) UI와 동기화**된다. 중지 신호를 보낼 수 있다 ([Linear](https://docs.devin.ai/integrations/linear)). 세션이 archive되지 않은 동안에는 PR 댓글에 자동으로 응답한다 ([GitHub](https://docs.devin.ai/integrations/gh)).
5. **후속·리뷰**: Devin Review는 PR이 열리거나(draft 제외), 커밋이 push되거나, ready로 바뀔 때 자동으로 돈다. `/devin review`로 직접 부를 수도 있다. Bug Catcher가 심각도별로 분류하고, 자동 수정안을 내고, 리뷰 화면에서 바로 커밋할 수 있다 ([Devin Review](https://docs.devin.ai/work-with-devin/devin-review)). "Devin Review나 GitHub 봇이 버그를 지적하면 Devin이 자동으로 PR을 고치고 CI가 통과할 때까지 반복한다"는 사례가 있고, **매일 아침 병합된 PR을 훑어 위반 사항마다 Linear 티켓을 만드는** 예약 작업 사례도 있다 ([블로그](https://cognition.com/blog/how-cognition-uses-devin-to-build-devin)). GitHub issues 쓰기 권한으로 이슈를 열 수 있다 ([GitHub](https://docs.devin.ai/integrations/gh)).
6. **UI**: Devin 웹앱의 세션 페이지(티켓에서 링크)와 Devin Review의 정리된 diff + Analysis 사이드바가 있다 (위 문서).
7. **제약**: draft와 브랜치 명명 규칙은 확인 못 함. Enterprise는 프로젝트와 조직을 매핑해야 한다 ([Jira](https://docs.devin.ai/integrations/jira)).

## 5. Factory (Droids)

1. **입구**: Linear에서 Factory에 배정하거나 `@Factory`를 멘션한다 ([Linear](https://docs.factory.ai/integrations/linear)). Jira에서는 "Droids" 서비스 계정으로 연동하고, 티켓의 "Factory - Open in Factory" 링크로 티켓 맥락을 담은 세션을 연다 ([Jira](https://docs.factory.ai/onboarding/integrating-with-your-engineering-system/jira), 검색 요약 기준). Jira에서 배정이나 멘션으로 시작하는 방식은 확인 못 함. GitHub에서는 `Factory-AI/droid-action`에서 `@droid` 명령을 쓴다 ([GitHub 보안](https://docs.factory.ai/enterprise/github-integration-security)).
2. **지시 구성**: 세션을 시작할 때 이슈 제목, 설명, 라벨, 댓글을 받는다. 문서는 **이슈가 그 자체로 완결되도록, 인수 조건과 검증 단계를 담아 쓰라**고 권한다 ([Linear](https://docs.factory.ai/integrations/linear)).
3. **실행 환경**: 조직 공용 실행 설정의 원격 컴퓨터에서 돈다. 요청자 개인 연결이 아니라 서비스 계정의 신원, Git 자격, 커넥터를 쓴다 ([Linear](https://docs.factory.ai/integrations/linear)). droid-action은 **사용자가 관리하는 GitHub Actions 러너에서만** 돈다. 짧게 사는 GitHub App 토큰을 쓰고, `FACTORY_API_KEY`는 Actions secret에 둔다. 트리거한 사용자에게 쓰기 권한이 있는지 확인하고, 봇은 거부한다 ([GitHub 보안](https://docs.factory.ai/enterprise/github-integration-security)).
4. **산출물·보고·반복**: "PR을 열고 URL을 반환하라"는 지시를 받고, PR 링크를 이슈에 자동으로 붙이려 한다. 진행 상황은 Linear 에이전트 세션에 올라온다. 같은 이슈에서 다시 `@Factory`를 부르면 추가 작업을 맡는다 ([Linear](https://docs.factory.ai/integrations/linear)).
5. **후속·리뷰**: 코드 리뷰는 P0–P3 심각도, 인라인 제안, 깨끗하면 승인하는 방식이다 ([Code Review](https://factory.ai/product/code-review)). 이슈를 자동 생성하는지는 확인 못 함.
6. **UI**: Linear 에이전트 세션과 Factory 앱이 있다. 세부 화면은 확인 못 함.
7. **제약**: 확인 못 함.

## 6. Atlassian Rovo Dev (Jira Coding Agent)

1. **입구**: 작업 항목의 **Agent sessions** 섹션에서 "Start work" → Jira Coding Agent를 고르거나, 작업 항목을 에이전트에 배정한다 ([generate code](https://support.atlassian.com/rovo/docs/generate-code-from-a-work-item-in-jira/)). Jira 자동화의 "Use Jira Coding Agent" 액션으로도 시작하며, 결과로 `{{jiracodingagent.codeGeneration.jobId}}`와 `repoUrl` 스마트 값이 나온다 ([automations](https://support.atlassian.com/rovo/docs/work-with-rovo-dev-in-automations/)).
2. **지시 구성**: 작업 항목 내용에 사용자 프롬프트, **저장소에 저장한 프롬프트**, 코드 표준, 파일 경로, 예시를 더할 수 있다. 저장소를 여러 개 고르면 저장소마다 세션이 따로 생긴다 ([generate code](https://support.atlassian.com/rovo/docs/generate-code-from-a-work-item-in-jira/)). Confluence 문서도 맥락으로 쓴다 ([overview](https://support.atlassian.com/rovo/docs/work-with-rovo-dev-in-jira/)).
3. **실행 환경**: Atlassian 클라우드 샌드박스에서 돈다. 세션 설정은 브랜치 이름(작업 항목 키를 넣어 Jira에 연결되도록 권장), 대상 브랜치, push·draft PR 여부, CI 자동 수정(Bitbucket만), 환경 변수, secrets, setup script이고, **사용자·저장소별로 저장된다** ([configure](https://support.atlassian.com/rovo/docs/configure-a-rovo-dev-in-jira-session/)). 지원 저장소는 Bitbucket Cloud와 GitHub Cloud, 크기는 20GB 미만이다 ([supported repos](https://support.atlassian.com/rovo/docs/supported-repositories-for-rovo-dev-in-jira/)).
4. **산출물·보고·반복**: "Generate code in draft pull request"를 켜면 항상 draft PR을 만들고, 병합은 하지 않는다. 채팅 패널에서 **코드 줄을 선택해 참조하며** 수정을 요청하고, 채팅 패널에서 커밋 메시지와 함께 PR을 만든다 ([generate code](https://support.atlassian.com/rovo/docs/generate-code-from-a-work-item-in-jira/)). Jira 상태를 자동으로 전환하는지는 확인 못 함.
5. **후속·리뷰**: Rovo Dev 코드 리뷰는 PR에 연결된 Jira 작업 항목의 요약·설명·커스텀 필드에서 **인수 조건을 찾아 "충족 / 누락 / 수동 확인 필요"로 판정**한다. 작업 항목 키를 브랜치 이름에 넣어 연결하라고 권한다 ([AC check](https://support.atlassian.com/rovo/docs/check-acceptance-criteria-in-a-code-review/)). 이슈를 생성하는지는 확인 못 함.
6. **UI**: 작업 항목 안의 Agent sessions 섹션과 채팅 패널(diff 보기 포함)이 있다 ([generate code](https://support.atlassian.com/rovo/docs/generate-code-from-a-work-item-in-jira/)).
7. **제약**: Rovo 크레딧을 쓰고, 자동화로 실행하면 자동화 한도에 포함된다 ([automations](https://support.atlassian.com/rovo/docs/work-with-rovo-dev-in-automations/)). 저장소는 20GB 미만이다.

## 7. OpenHands

1. **입구**: Cloud에서는 GitHub 이슈의 `openhands` 라벨이나 이슈·PR 댓글의 `@openhands`로 시작한다 ([GitHub install](https://docs.openhands.dev/usage/cloud/github-installation)). Jira에서는 `openhands` 라벨이나 `@openhands` 댓글로 시작하고, 저장소 위치는 본문이나 댓글에 적는다 ([Jira](https://docs.openhands.dev/openhands/usage/cloud/project-management/jira-integration)). Linear와 Slack 연동도 있다 ([openhands.dev](https://www.openhands.dev/)). 셀프 호스팅 resolver(GitHub Actions)는 `fix-me` 라벨이나 `@openhands-agent` 댓글로 시작한다 ([openhands-resolver PyPI](https://pypi.org/project/openhands-resolver/)).
2. **지시 구성**: resolver에서 라벨로 시작하면 **이슈의 모든 댓글**을, 멘션으로 시작하면 **그 댓글만** 지시로 쓴다 ([PyPI](https://pypi.org/project/openhands-resolver/)). Jira 문서는 명확한 요구사항과 인수 조건을 쓰라고 권하고, 에이전트가 계획을 세운 뒤 구현한다 ([Jira](https://docs.openhands.dev/openhands/usage/cloud/project-management/jira-integration)).
3. **실행 환경**: OpenHands Cloud 또는 사용자 GitHub Actions에서 돈다. resolver는 contents·issues·pull requests·workflows 권한을 가진 PAT와 LLM API 키를 요구한다 ([PyPI](https://pypi.org/project/openhands-resolver/)). 저장소 설정 파일은 `.openhands/setup.sh`(의존성·환경), `.openhands/hooks.json`(위험 명령 차단, **Stop hook이 품질 검사를 통과해야 완료 처리**), Skills(구 microagents)다 ([repository](https://docs.openhands.dev/openhands/usage/customization/repository)).
4. **산출물·보고·반복**: 이슈에 "작업 중" 댓글을 남기고, **해결됐다고 판단할 때만 PR을 열며**, 끝나면 요약과 PR 링크를 댓글로 단다 ([GitHub install](https://docs.openhands.dev/usage/cloud/github-installation)). resolver는 성공하면 draft PR을 만들고, 실패하면 브랜치만 push한 뒤 결과를 댓글로 남기고 `fix-me` 라벨을 뗀다 ([PyPI](https://pypi.org/project/openhands-resolver/)). PR에서는 `@openhands`로 수정을 요청하는데, PR의 양쪽 저장소가 모두 등록되어 있어야 한다 ([GitHub install](https://docs.openhands.dev/usage/cloud/github-installation)).
5. **후속·리뷰**: Agent SDK로 PR 리뷰, 리뷰어 배정, TODO 관리 워크플로우를 만든다 ([PR review](https://docs.openhands.dev/sdk/guides/github-workflows/pr-review), [TODO](https://docs.openhands.dev/sdk/guides/github-workflows/todo-management)). 이슈를 자동 생성하는지는 확인 못 함.
6. **UI**: OpenHands Cloud의 대화(conversation) 화면이 있다. 세부는 확인 못 함.
7. **제약**: resolver는 "한 번에 이슈 하나를 높은 품질로" 처리하도록 설계됐다 ([PyPI](https://pypi.org/project/openhands-resolver/)).

---

## Runloom에 가져올 점

1. **입구는 "배정(assign)" 하나로 통일하고 라벨·멘션은 보조로 둔다** (Copilot, Codex, Cursor, Devin, Factory, Rovo Dev 공통). 사용자가 Runloom에서 "업무 가져오기 → 등록"을 따로 하는 대신, GitHub 이슈의 assignee나 `runloom` 라벨을 붙이면 등록되도록 한다. Devin의 `!plan`/`!implement`처럼 **라벨 이름을 업무 종류에 매핑**하면 ADR-0009의 "종류 = 등록 데이터"와 그대로 맞아떨어진다.
2. **진행 상황을 티켓 안에 보여 준다** (Copilot의 Jira 채팅 스트림, Devin의 Linear plan 동기화, OpenHands의 "작업 중 → 요약+PR 링크" 댓글). Runloom 웹에 오지 않아도 GitHub 이슈 댓글 하나(시작 시 생성하고 이후 **같은 댓글을 수정**)로 "현재 단계: 리뷰 중 / 다음: 사람 차례" 같은 후속 체인 상태와 세션 링크를 보여 주면, "지금 뭐가 어디서 돌고 있나" 헷갈리는 문제가 줄어든다.
3. **메인 화면은 "세션 로그 페이지" 하나를 중심에 둔다** (Copilot 에이전트 패널 + 세션 로그, Cursor cursor.com/agents). 목록에는 세션(업무 1건 = 세션 1개)과 상태를 두고, 상세에는 에이전트가 한 일, 사용한 도구, 소요 시간, 토큰, "Stop" 버튼을 둔다. Runloom의 `Execution`·후속 규칙은 상세 안에 "이 세션이 만든 다음 업무" 타임라인으로 넣는다.
4. **사람이 반복할 때는 PR 댓글 `@멘션`을 쓴다** (Copilot `@copilot`, Devin 자동 응답, OpenHands `@openhands`). 지금의 "재작업/사람 차례" 전환을 Runloom 버튼이 아니라 **draft PR의 리뷰 댓글에서 트리거**하면 사용자가 새 UI를 배우지 않아도 된다. Copilot처럼 트리거는 쓰기 권한이 있는 사람만, 봇 댓글은 거부(Factory)하는 규칙도 같이 가져온다.
5. **리뷰 에이전트는 판정을 "심각도 + 인수 조건 표"로 내게 한다** (Codex P0/P1만 보고, Factory P0–P3, Rovo Dev의 "충족/누락/수동 확인"). Runloom의 결과 판정·후속 규칙이 이 구조화된 출력을 받으면 "P0/누락이 있으면 재작업, 없으면 사람 차례"를 규칙 행으로 표현할 수 있다. 이슈 템플릿에 인수 조건 칸을 권장 필드로 둔다 (Factory·OpenHands 권고).
6. **안전장치는 Copilot 기본값을 따른다**: 전용 브랜치 접두사(`copilot/` → 예: `runloom/<이슈번호>`), 이슈당 PR 1개, 에이전트는 draft만 만들고 ready 전환·병합은 하지 않음, 사람 co-author 표기. Rovo Dev처럼 **브랜치 이름에 이슈 키를 넣어** Jira 연동 때도 자동으로 연결되게 한다.
7. **러너 환경 설정은 저장소 파일 하나로 선언한다** (`copilot-setup-steps.yml`, `.cursor/environment.json`, `.openhands/setup.sh` + Stop hook). Runloom 러너가 저장소의 `AGENTS.md`와 예컨대 `.runloom/setup.sh`만 읽게 하고, Codex처럼 **secrets는 setup 단계에서만 쓰고 에이전트 단계에는 넘기지 않는** 규칙을 명시한다 (현재 AGENTS.md의 "비밀값을 Codex 프로세스 환경에 넣지 않는다"와 같은 방향). "자기 머신에서 도는 러너"는 Copilot의 self-hosted 러너 지원, Factory droid-action, OpenHands resolver와 같은 부류이므로 이들을 비교 대상으로 언급하면 포지셔닝이 쉬워진다.
