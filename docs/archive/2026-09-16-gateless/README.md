# 이전 Gateless 문서와 실험 보관

보관일: 2026-09-19. 폴더 날짜는 이전 기준선의 갱신일을 나타낸다.

제품 방향과 문서 구성을 다시 논의하기 위해 이전 자료를 내용 변경 없이 옮겼다. 하위 구조를 유지해 문서 사이의 상대 링크와 실험 자료 관계를 보존했다. 이 폴더의 `확정`, `freeze`, `현재 기준`, `다음 세션` 등의 표현은 당시 기록이며 현행 요구사항·실행 지시가 아니다.

현재 단계는 [현재 논의](../../CURRENT_HANDOFF.md), 문서 분류는 [문서 안내](../../README.md)를 따른다.

| 보관 자료 | 내용과 재사용 시 주의점 |
|---|---|
| [CURRENT_HANDOFF.md](CURRENT_HANDOFF.md) | 이전 세션 인계. 현재 작업 순서를 지시하지 않음 |
| [GATELESS_CONTEXT.md](GATELESS_CONTEXT.md) | 긴 Discovery 이력. 새 제품의 합의로 간주하지 않음 |
| [product/PRD.md](product/PRD.md), [product/MVP_SPEC.md](product/MVP_SPEC.md) | 개발팀·사전 위임·준비된 GitHub 데모 중심의 이전 제품 기준선 |
| [domain/DOMAIN_MODEL.md](domain/DOMAIN_MODEL.md), [domain/WORK_ENGINE.md](domain/WORK_ENGINE.md) | 이전 책임·상태·큐·슬롯·재개 규칙. 필요성 검토 후 재사용 |
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | 이전 기준선에 맞춘 아키텍처 초안. 현재 채택한 스택이 아님 |
| [research/INTEGRATION_FEASIBILITY.md](research/INTEGRATION_FEASIBILITY.md), [research/EXTERNAL_WORK_API_RESEARCH.md](research/EXTERNAL_WORK_API_RESEARCH.md) | 당시 API·실행 방식 조사. 필요할 때 현재 지원 여부를 재확인 |
| [research/PREPARED_ENVIRONMENT_VERIFICATION.md](research/PREPARED_ENVIRONMENT_VERIFICATION.md) | 실제 실험의 관찰과 한계. 제품 전체·보안·수요 검증과 구분 |
| [research/prepared-probe/README.md](research/prepared-probe/README.md) | 실험 코드, 원시 로그, 테스트 출력, 산출물과 해시 목록 |

실험 파일은 원본을 보존했다. 본문·스크립트·로그의 과거 `docs/...` 경로나 임시 절대 경로는 역사적 기록이며 일괄 수정하지 않았다. 재현이 필요하면 현재 보관 위치와 실행 환경에 맞게 경로를 확인한다. 이번 정리에서는 모델 호출·Docker 실험을 다시 실행하지 않았다.
