#!/usr/bin/env bash
set -euo pipefail
# VM 코드 갱신 — GitHub main 을 받아 서비스 5개를 재시작한다. root 로 실행 (sudo bash /opt/workflow/deploy/update-vm.sh).
# install-vm.sh 이후에만 쓴다. env 파일·Caddy 설정은 건드리지 않는다. 데이터 디렉터리는 WORKFLOW_RESET_DB=1 일 때만(아래) 옮긴다.
# 재시작 중 몇 초 동안 웹이 502 다. 진행 중인 실행은 워커·연결 프로그램이 재접속 뒤 이어간다.
#
# 변수: WORKFLOW_REPO_REF  체크아웃할 브랜치 (기본 main)
#       WORKFLOW_RESET_DB  "1" 이면 스키마 버전이 바뀐 배포용 DB 초기화 — 서비스를 멈추고 중앙 DB·산출물·진단 데이터를
#                          /var/backups/workflow/reset-{날짜}/ 로 옮긴 뒤 빈 상태로 다시 시작한다 (삭제 아님).
#                          이 값이 없으면 데이터를 절대 건드리지 않는다 — 심사 중 세션 데이터를 실수로 날리지 않기 위해.
#                          초기화 뒤에는 docs/DEPLOY.md 5·6 단계(seed → connect → register)를 다시 한다:
#                          카탈로그·연결 프로그램 등록이 중앙 DB 에 있었기 때문이다. 데모 저장소·연결 프로그램 상태는 남긴다.

WORKFLOW_REPO_REF="${WORKFLOW_REPO_REF:-main}"
APP_DIR=/opt/workflow
DATA_DIR=/var/lib/workflow
BACKUP_DIR=/var/backups/workflow
SERVICES="workflow-diag workflow-diag-worker workflow-central workflow-worker workflow-connector"

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행하세요: sudo $0" >&2
  exit 1
fi

reset_data() {
  # WORKFLOW_RESET_DB=1 일 때만 불린다. 옮기기만 하고 지우지 않는다. 같은 날 두 번 돌려도 이전 백업을 덮어쓰지 않도록 시각까지 붙인다
  RESET_DIR="$BACKUP_DIR/reset-$(date +%Y-%m-%d-%H%M%S)"
  install -d -m 700 -o workflow -g workflow "$RESET_DIR" "$RESET_DIR/central" "$RESET_DIR/diag"
  systemctl stop $SERVICES
  shopt -s nullglob
  for item in "$DATA_DIR"/central/db.sqlite "$DATA_DIR"/central/db.sqlite-wal "$DATA_DIR"/central/db.sqlite-shm \
              "$DATA_DIR"/central/artifacts; do
    [ -e "$item" ] && mv "$item" "$RESET_DIR/central/"
  done
  for item in "$DATA_DIR"/diag/*; do
    mv "$item" "$RESET_DIR/diag/"
  done
  shopt -u nullglob
  echo "DB 초기화됨(백업: $RESET_DIR) — 재시작 뒤 docs/DEPLOY.md 5·6 단계(seed → connect → register)를 다시 한다"
}

echo "== 1. 코드 ($APP_DIR, $WORKFLOW_REPO_REF)"
before=$(sudo -u workflow -H git -C "$APP_DIR" rev-parse --short HEAD)
sudo -u workflow -H git -C "$APP_DIR" fetch -q origin "$WORKFLOW_REPO_REF"
sudo -u workflow -H git -C "$APP_DIR" checkout -q "$WORKFLOW_REPO_REF"
sudo -u workflow -H git -C "$APP_DIR" pull -q --ff-only origin "$WORKFLOW_REPO_REF"
after=$(sudo -u workflow -H git -C "$APP_DIR" rev-parse --short HEAD)
echo "   $before → $after"

echo "== 2. 의존성 (pyproject 가 바뀌었을 때만 실제로 설치된다)"
sudo -u workflow -H "$APP_DIR/venv/bin/pip" install -q -e "$APP_DIR[dev]"

if [ "${WORKFLOW_RESET_DB:-}" = "1" ]; then
  reset_data
fi

echo "== 3. 재시작"
systemctl restart workflow-diag workflow-diag-worker workflow-central workflow-worker workflow-connector
sleep 3
systemctl is-active $SERVICES | tr '\n' ' '; echo
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)
if [ "$code" != "200" ]; then
  echo "중앙 웹 응답 $code — journalctl -u workflow-central -n 30 으로 확인" >&2
  exit 1
fi
echo "갱신 끝: $after, 중앙 웹 200"
