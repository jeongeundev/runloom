# ADR-0029: 공개 데모 종료 — VM 삭제, `main` 단일 브랜치

결정일: 2026-10-09. 사용자 결정 "main 이 공모전용 데모 화면이었는데 이제 필요 없다. 데모용 웹 서비스도 필요 없으면 내린다". 공모전 심사(~2026-10-05)가 끝났다.

## 결정

1. **공개 데모 VM 을 내린다.** AWS Lightsail(ap-northeast-2) 인스턴스 `workflow` 와 고정 IP `worflow-ip`(43.202.200.54, `runloom.duckdns.org`)를 스냅샷 없이 삭제한다. 대본 데모라 보관할 데이터가 없다.
2. **데모 코드는 태그로만 남긴다.** 마지막 `main`(0da3d21)에 태그 `contest-demo-2026` 를 단다. 진단 데모(`src/diagnostic_demo/`)·대본 에이전트(`src/workflow/scripted/`)·VM 배포 파일(`deploy/install-vm.sh`·`deploy/systemd/`·`deploy/Caddyfile` 등)은 그 태그에서만 볼 수 있다.
3. **브랜치는 `main` 하나.** `main` 이 `service` 의 조상이라 `main` 을 `service` 로 fast-forward 하고 `service` 를 지운다(로컬·origin). 이후 phase 는 `main` 에서 `feat-*` 로 갈라져 `main` 에 `--no-ff` 로 병합한다. 배포 형태는 셀프호스트([ADR-0016](0016-selfhost-docker-fixed-workspace.md)·[ADR-0019](0019-service-selfhost-only.md)) 하나다.
4. **문서.** 공개 데모 VM 런북 `docs/DEPLOY.md` 는 [보관 자료](../archive/2026-09-27-contest-and-history/DEPLOY-public-demo-vm.md)로 옮긴다. 현행 문서·코드 주석의 "`main` 전용" 표시는 "공개 데모 전용(종료)" 으로 바꾼다. `phases/`·이전 ADR 본문·VERIFICATION_LOG 는 당시 기록이라 고치지 않는다. `WORKFLOW_MODE=demo` 설정 오류 문구에서 `main` 브랜치 안내를 뺀다.

## 결과

- [ADR-0006](0006-deployment-vm-caddy-mac-connector.md)(VM 배포)·[ADR-0008](0008-public-demo-scripted-agents.md)(대본 데모)은 종료된 기록이다. [ADR-0019](0019-service-selfhost-only.md) 의 `main`·`service` 구분도 이 시점부터 당시 기록이다.
- DuckDNS 의 `runloom` 서브도메인은 이 저장소 밖(duckdns.org 계정)에서 정리한다.
