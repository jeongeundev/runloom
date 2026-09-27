# ADR-0017: GitHub 연결 — 사용자 자신의 GitHub App·비밀 저장소·전체 가져오기와 지시 실행

결정일: 2026-09-27 (phase 11 step 0). 기본값은 [phase 11 README](../../phases/11-github-app/README.md) "계획 기본값"이며 이 문서로 구현 기준을 고정한다. 계기: 셀프호스트 설치 뒤 GitHub 연결 화면이 로컬 저장소 ID·RFC 3339 시작 시각·검증 프로필 ID·GitHub 숫자 사용자 ID·`.env` 토큰을 하나하나 입력하게 해, 연결 자체가 병목이었다. 실제 서비스(Linear·Copilot coding agent·Coolify 등)가 쓰는 "버튼 → GitHub 에서 승인 → 저장소 선택" 흐름으로 바꾼다. 이 시점에는 구현이 없다 — 아래 이름·경로는 step 1~9 가 만들고, 바꿀 때는 이 ADR·[ARCHITECTURE](../ARCHITECTURE.md) "GitHub App 연결 — phase 11"·[GLOSSARY](../GLOSSARY.md)·테스트를 같이 고친다.

## 결정

1. **표준 연결 = 사용자 자신의 GitHub App.** `/operator/github` 의 [GitHub 연결] 이 GitHub App manifest 흐름을 시작한다: 브라우저가 `https://github.com/settings/apps/new?state=…`(조직이면 `https://github.com/organizations/{org}/settings/apps/new?state=…`)로 `manifest` 폼을 POST → 사용자가 GitHub 에서 [Create] → GitHub 가 `redirect_url` 로 `code`·`state` 를 돌려줌 → 서버가 `POST /app-manifests/{code}/conversions`(인증 없음, code 는 1시간 안에 교환)로 App ID·slug·client ID·client secret·webhook secret·개인 키(`pem`)를 받는다 → 설치 화면(`https://github.com/apps/{slug}/installations/new?state=…`)으로 보냄 → 사용자가 저장소(전체/선택)를 고르고 [Install] → GitHub 가 `setup_url` 로 `installation_id`(와 `setup_action`)를 돌려줌 → 서버가 설치 저장소마다 소스를 만든다. App 은 비공개(`public: false` — 만든 계정에만 설치 가능), 웹훅 끔(`hook_attributes.active: false`), 권한은 Issues 읽기·쓰기, Pull requests 읽기, Metadata 읽기뿐이다(Contents 없음 — 코드는 로컬 저장소에서 읽고 쓴다).
2. **인증 = App JWT → 설치 토큰.** App JWT 는 RS256(`PyJWT[crypto]`), `iat` = 지금 − 60초, `exp` = 지금 + 9분(상한 10분), `iss` = client ID(공식 권장, App ID 도 허용). 설치 토큰은 `POST /app/installations/{installation_id}/access_tokens` 로 받고(유효 1시간) 프로세스 메모리에만 설치별로 캐시하며, 만료 5분 전이면 새로 받는다. 설치 토큰은 파일·DB 에도 쓰지 않는다. 설치 저장소 목록은 설치 토큰으로 `GET /installation/repositories`(`per_page=100`, Link 페이지).
3. **`setup_url` 의 `installation_id` 는 믿지 않는다.** 공식 문서가 위조 가능하다고 경고한다. 서버는 App JWT 로 `GET /app/installations/{installation_id}` 를 불러 우리 App 의 설치인지 확인한 뒤에만 소스를 만든다(비공개 App 이라 설치는 만든 계정에만 있다). 운영자 로그인은 두 콜백 모두 필수다.
4. **비밀 보관 = 데이터 볼륨의 0600 비밀 파일.** `adapters/secret_store.py` 의 `SecretStore` 가 `WORKFLOW_SECRET_DIR`(셀프호스트 compose `/data/secrets`, demo·개발 기본 `data/secrets`) 아래 파일로 둔다 — 디렉터리 0700, 파일 0600, 임시 파일 + rename 으로 원자 교체, 파일 이름은 코드 상수만(외부 입력으로 경로를 만들지 않는다). App 개인 키·client secret·webhook secret·붙여 넣은 PAT 가 대상이다. DB·백업(`workflow.server.backup` 은 DB·`artifacts` 만 담는다)·로그·응답·템플릿·예외 문구·Codex/Claude 프로세스 환경에 넣지 않는다. 화면·API 는 "연결됨/안 됨"과 비밀이 아닌 값(App slug·설치 계정·저장소 이름)만 보인다. [AGENTS.md](../../AGENTS.md) 비밀값 규칙을 "환경변수 또는 비밀 저장소에서만 읽는다"로 바꾼다.
5. **가져오기 = 설치 저장소의 열린 이슈 전부(`intake: all_open`), 실행 = 지시한 것만.** App 으로 만든 소스는 열린 이슈(PR 제외)를 전부 업무 목록에 `대기`(`not_delegated`, "실행 지시 전")로 가져오고, 새 이슈·변경도 기존 주기 조회로 따라온다. 착수는 (a) 업무 목록의 [에이전트에게 맡기기](`POST /tasks/{task_id}/delegate`) 또는 (b) GitHub 에서 이슈에 트리거 라벨(`trigger_label`, 기본 `runloom`, 대소문자 무시)을 붙인 것만 한다. 지시는 한 번 기록되면 유지된다(라벨을 떼도 되돌리지 않는다). 지시 뒤 수정 → 검토 → 재작업은 기존 phase 8 순환 그대로 자동이다. [ADR-0014](0014-github-task-cycle.md) 결정 2 의 "범위 밖 백로그는 자동 착수하지 않는다"는 "전체를 목록에 가져오되 실행은 지시한 것만"으로 바뀐다 — 지시 없는 자동 착수가 없다는 점은 같다.
6. **자동 매칭 — 내부 ID 입력 없음.** 러너는 등록 폴더의 `origin` 원격이 GitHub 이면 `owner/name` 만 `discovered.found.github_repository` 로 보고한다(URL 원문·인증 정보·다른 원격은 보내지 않음, 러너 계약 v1 그대로). 서버는 소스 저장소와 이 값이 같은(대소문자 무시) 이 워크스페이스의 로컬 Agent 에서 로컬 저장소 ID(`workflow_repository_id`)·검증 프로필·수정 Agent·검토 Agent 를 정한다. 수정 Agent 는 GitHub 담당자 연결(`AssigneeBinding`) → 소스의 기본 담당(`default_fix_agent_id`) → 후보가 하나뿐인 자동 선택 순이다. 후보가 없거나 둘 이상이면 추정하지 않고 대기 코드로 알린다(화면에서 하나를 고르면 그 값이 소스 설정에 저장된다).
7. **웹훅 없음.** 셀프호스트는 127.0.0.1 전용이라 GitHub 가 닿지 않는다. 기존 주기 조회(`GITHUB_SYNC_INTERVAL_SECONDS` 60초)를 유지한다. 설치 저장소가 바뀌면(GitHub 설정에서 저장소 추가·제거) `setup_on_update: true` 로 다시 `setup_url` 에 돌아올 때 소스를 맞춘다.
8. **대체 경로 = 고급 설정의 PAT 붙여 넣기.** App 을 만들 수 없는 경우(조직 정책 등) `/operator/github` 고급 설정에서 fine-grained PAT 를 붙여 넣는다(`POST /operator/github/token`). 같은 비밀 저장소 파일에 둔다. 저장 전 `GET /repos/{owner}/{name}` 로 접근을 확인하고, 값은 다시 보여 주지 않는다.
9. **기존 설정 호환.** 환경변수 `WORKFLOW_GITHUB_TOKEN`·`WORKFLOW_GITHUB_REPOS` 와 phase 8 소스(라벨 필터·고른 이슈 번호·시작 시각·수동 입력한 ID)는 그대로 동작한다. 저장된 `config_json` 에 새 칸이 없으면 `intake: filtered` 로 읽고, `filtered` 소스의 수집·준비 판정(담당자 대기 코드 포함)은 지금 규칙 그대로다. 새 칸은 모두 기본값이 있는 추가형이라 기존 API 요청 본문도 그대로 유효하다. 러너 계약 `contract_version` 은 1 그대로다.

## 대안

- **환경변수 토큰만(현재).** 사용자가 GitHub 에서 PAT 권한을 고르고 `.env` 를 고치고 재설치해야 하며, 저장소 목록·숫자 ID·검증 프로필 ID 를 따로 입력한다. 이번 phase 의 계기 자체라 표준에서 기각, 호환 경로로만 남긴다.
- **OAuth App.** 사용자 권한을 그대로 빌려 저장소 단위로 좁힐 수 없고(scope 가 `repo` 전체), 토큰이 사람에 묶인다. 저장소 선택·세분 권한·짧은 토큰을 주는 GitHub App 을 택해 기각.
- **Runloom 이 운영하는 공용 GitHub App.** 설치는 한 번 클릭이지만 공용 App 의 개인 키를 모든 셀프호스트에 배포하거나 중앙 중계 서버가 필요하다. 셀프호스트 1인용 전제와 맞지 않아 기각 — 사용자마다 자기 App 을 만든다(manifest 가 이름·권한을 채우므로 입력은 없다).
- **웹훅.** 즉시 반영되지만 127.0.0.1 서버에 GitHub 가 닿지 않아 터널(smee·Cloudflare Tunnel)과 서명 검증이 필요하다. 60초 주기 조회로 충분해 기각(터널은 후속).
- **가져온 이슈를 전부 자동 실행.** 연결하는 순간 백로그 전체에 구독 한도·비용·커밋이 생긴다. 가져오기와 실행 지시를 나눠 기각.
- **비밀을 DB 에 암호화 저장.** 암호화 키를 다시 어딘가(환경변수·파일)에 둬야 하고, DB 백업·복원과 함께 움직여 유출 범위가 넓어진다. 데이터 볼륨의 0600 파일이 더 단순해 기각.

## 결과

- 새 의존성 `PyJWT[crypto]`(step 2). `WORKFLOW_SECRET_DIR` 환경변수와 compose 고정값 `/data/secrets`(step 1).
- 백업은 비밀 파일을 담지 않는다. 다른 볼륨에 복원하면 GitHub 를 다시 연결해야 한다(같은 볼륨이면 비밀 파일이 남아 그대로 동작). App 이 GitHub 에 남으므로 다시 연결은 설치 화면부터다.
- 설치 토큰은 프로세스마다(central·worker) 따로 받는다. 한 시간에 몇 번의 추가 호출이다.
- 모든 step 은 httpx `MockTransport`·가짜 GitHub 로 검증한다. 실제 App 생성·설치([Create]·[Install])는 phase 뒤 사용자 브라우저에서 한다.
- 확인하지 못한 것(2026-09-27 공식 문서 기준): 127.0.0.1 주소를 `hook_attributes.url` 로 받아 주는지(`active: false` 여도 `url` 은 필수 항목), 설치 URL 의 `state` 가 `setup_url` 로 그대로 돌아오는지(문서는 "설치 뒤 상태 복원" 용도로 설명) — 결정 3 은 `state` 가 없어도 JWT 확인으로 안전하게 설계했다. 실제 App 생성 때 확인해 [VERIFICATION_LOG](../VERIFICATION_LOG.md)에 남긴다.

## 출처 (2026-09-27 확인, REST API 버전 2022-11-28)

- [Registering a GitHub App from a manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) — POST URL(개인·조직), `manifest`·`state`, manifest 필드, `code` 1시간
- [REST: Apps](https://docs.github.com/en/rest/apps/apps?apiVersion=2022-11-28) — conversion 응답(`id`·`slug`·`client_id`·`client_secret`·`webhook_secret`·`pem`), `GET /app/installations/{id}`, `POST …/access_tokens`(`token`·`expires_at`·`repository_selection`)
- [Generating a JWT](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app) — RS256, `iat` 60초 이전, `exp` 10분 이하, `iss` client ID 권장
- [Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app) — 1시간 만료
- [REST: Installations](https://docs.github.com/en/rest/apps/installations?apiVersion=2022-11-28) — `GET /installation/repositories`(`per_page` 최대 100, `total_count`·`repositories`)
- [About the setup URL](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url) — `installation_id` 위조 경고
- [Sharing your GitHub App](https://docs.github.com/en/apps/sharing-github-apps/sharing-your-github-app) — `https://github.com/apps/{slug}/installations/new?state=…`
