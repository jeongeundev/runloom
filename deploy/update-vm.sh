#!/usr/bin/env bash
set -euo pipefail
# VM 코드 갱신 — GitHub main 을 받아 서비스 4개를 재시작한다. root 로 실행 (sudo bash /opt/workflow/deploy/update-vm.sh).
# install-vm.sh 이후에만 쓴다. env 파일·데이터 디렉터리·Caddy 설정은 건드리지 않는다.
# 재시작 중 몇 초 동안 웹이 502 다. 진행 중인 실행은 워커·연결 프로그램이 재접속 뒤 이어간다.
#
# 변수: WORKFLOW_REPO_REF  체크아웃할 브랜치 (기본 main)

WORKFLOW_REPO_REF="${WORKFLOW_REPO_REF:-main}"
APP_DIR=/opt/workflow

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행하세요: sudo $0" >&2
  exit 1
fi

echo "== 1. 코드 ($APP_DIR, $WORKFLOW_REPO_REF)"
before=$(sudo -u workflow -H git -C "$APP_DIR" rev-parse --short HEAD)
sudo -u workflow -H git -C "$APP_DIR" fetch -q origin "$WORKFLOW_REPO_REF"
sudo -u workflow -H git -C "$APP_DIR" checkout -q "$WORKFLOW_REPO_REF"
sudo -u workflow -H git -C "$APP_DIR" pull -q --ff-only origin "$WORKFLOW_REPO_REF"
after=$(sudo -u workflow -H git -C "$APP_DIR" rev-parse --short HEAD)
echo "   $before → $after"

echo "== 2. 의존성 (pyproject 가 바뀌었을 때만 실제로 설치된다)"
sudo -u workflow -H "$APP_DIR/venv/bin/pip" install -q -e "$APP_DIR"

echo "== 3. 재시작"
systemctl restart workflow-diag workflow-diag-worker workflow-central workflow-worker
sleep 3
systemctl is-active workflow-diag workflow-diag-worker workflow-central workflow-worker | tr '\n' ' '; echo
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)
if [ "$code" != "200" ]; then
  echo "중앙 웹 응답 $code — journalctl -u workflow-central -n 30 으로 확인" >&2
  exit 1
fi
echo "갱신 끝: $after, 중앙 웹 200"
