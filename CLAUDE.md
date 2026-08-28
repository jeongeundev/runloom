# 프로젝트: {프로젝트명}

## 기술 스택
- {언어 (예: TypeScript strict / Python 3.12 / Go 1.23)}
- {프레임워크·런타임 (예: Next.js 15 / FastAPI / 없음)}
- {주요 라이브러리 (예: Tailwind CSS / SQLAlchemy / 없음)}

> `docs/presets/` 에 해당 스택 프리셋이 있으면 그 값으로 채운다.

## 아키텍처 규칙
- CRITICAL: {절대 지켜야 할 규칙 1 — 레이어 경계를 넘지 않는 규칙을 적는다.
  예: 모든 외부 API 호출은 서버 계층에서만 수행한다}
- CRITICAL: {절대 지켜야 할 규칙 2 — 데이터 흐름의 단방향성이나 의존 방향을 적는다.
  예: 도메인 로직은 프레임워크 모듈을 import 하지 않는다}
- {일반 규칙 — 배치 규칙을 적는다. 예: 타입 정의와 구현을 같은 파일에 두지 않는다}

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
{개발 서버}   # 예: npm run dev / uvicorn app.main:app --reload / go run ./cmd/server
{빌드}        # 예: npm run build / (없음) / go build ./...
{린트}        # 예: npm run lint / ruff check . / golangci-lint run
{테스트}      # 예: npm test / pytest / go test ./...

> 이 항목들은 `scripts/hooks/verify.sh` 가 검증에 사용하는 것과 같은 커맨드여야 한다.
> 값을 채운 뒤 verify.sh 가 해당 툴체인을 실제로 감지하는지 확인할 것.
