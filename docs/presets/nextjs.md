# 프리셋: Next.js (App Router) + TypeScript

프로젝트 시작 시 아래 값을 `AGENTS.md` 와 `docs/ARCHITECTURE.md` 의 해당 슬롯에 복사해 넣는다.

## AGENTS.md → 기술 스택
- TypeScript strict mode
- Next.js 15 (App Router)
- Tailwind CSS

## AGENTS.md → 아키텍처 규칙
- CRITICAL: 모든 API 로직은 `app/api/` 라우트 핸들러에서만 처리한다
- CRITICAL: 클라이언트 컴포넌트에서 외부 API 를 직접 호출하지 않는다
- 컴포넌트는 `components/`, 타입은 `types/` 로 분리한다

## AGENTS.md → 명령어
```
npm run dev      # 개발 서버
npm run build    # 프로덕션 빌드
npm run lint     # ESLint
npm run test     # 테스트
```

## ARCHITECTURE.md → 디렉토리 구조
```
src/
├── app/               # 진입점 — 페이지 + API 라우트
├── components/        # UI 컴포넌트
├── types/             # TypeScript 타입 정의
├── lib/               # 유틸리티 + 헬퍼
└── services/          # 외부 API 래퍼
```

## ARCHITECTURE.md → 패턴
Server Components 를 기본으로 하고, 인터랙션이 필요한 곳만 Client Component 로 만든다.

## ARCHITECTURE.md → 데이터 흐름
```
사용자 입력 → Client Component → API Route → 외부 API → 응답 → UI 업데이트
```

## ARCHITECTURE.md → 상태·영속성
서버 상태는 Server Components, 클라이언트 상태는 `useState`/`useReducer`.

## step Acceptance Criteria
```bash
npm run build   # 컴파일 에러 없음
npm test        # 테스트 통과
```

## 비고
- `docs/UI_GUIDE.md` 를 사용한다 (UI 있는 프로젝트).
- `verify.sh` 는 `package.json` 의 `lint`/`build`/`test` 스크립트를 자동 감지한다.
