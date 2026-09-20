#!/usr/bin/env bash
set -euo pipefail
# VM 설치 스크립트 — Ubuntu 24.04 기준, root 로 실행. 멱등: 다시 돌려도 이미 있는 것은 건너뛴다.
# ADR-0006: systemd 서비스 4개 + 백업 타이머 + Caddy 뿐이다. 컨테이너·오케스트레이션은 쓰지 않는다.
# 하는 일: 패키지 → workflow 사용자 → 데이터 디렉터리(0700) → /opt/workflow clone + venv + pip install →
#         /etc/workflow/*.env (예시 복사, 0600) → Caddyfile → systemd 유닛 복사·enable.
# 하지 않는 일: 서비스 start. env 파일이 비어 있으면 시작 즉시 실패해 Restart=always 가 반복되므로
#         docs/DEPLOY.md 3 단계에서 값을 채운 뒤 사용자가 start 한다. 도메인·DNS 도 사용자가 한다.
#
# 변수(환경변수로 덮어쓴다):
#   WORKFLOW_REPO_URL  clone 할 저장소 (비공개면 배포용 토큰/키가 필요하다)
#   WORKFLOW_REPO_REF  체크아웃할 브랜치·태그

WORKFLOW_REPO_URL="${WORKFLOW_REPO_URL:-https://github.com/jeongeundev/harness-framework.git}"
WORKFLOW_REPO_REF="${WORKFLOW_REPO_REF:-main}"
APP_DIR=/opt/workflow
DATA_DIR=/var/lib/workflow
ENV_DIR=/etc/workflow
BACKUP_DIR=/var/backups/workflow
PY=python3.13

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행하세요: sudo $0" >&2
  exit 1
fi

echo "== 1. 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git sqlite3 curl ca-certificates software-properties-common
# Ubuntu 24.04 의 기본 python3 은 3.12 다. 프로젝트는 3.13 계열(ADR-0002)이라 deadsnakes 에서 받는다
if ! apt-cache show "$PY" >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -q
fi
apt-get install -y -q "$PY" "$PY-venv"
if ! command -v caddy >/dev/null 2>&1; then
  # universe 의 caddy 로 충분하다 (reverse_proxy + 자동 HTTPS). 없으면 공식 저장소를 쓴다: https://caddyserver.com/docs/install
  apt-get install -y -q caddy
fi

echo "== 2. 사용자와 디렉터리"
if ! id workflow >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin workflow
fi
install -d -m 700 -o workflow -g workflow "$DATA_DIR" "$DATA_DIR/central" "$DATA_DIR/diag" "$BACKUP_DIR"
install -d -m 700 -o root -g root "$ENV_DIR"

echo "== 3. 코드 ($APP_DIR)"
if [ ! -d "$APP_DIR/.git" ]; then
  install -d -m 755 -o workflow -g workflow "$APP_DIR"
  sudo -u workflow -H git clone --branch "$WORKFLOW_REPO_REF" "$WORKFLOW_REPO_URL" "$APP_DIR"
else
  echo "이미 있음: $APP_DIR — 갱신은 'sudo -u workflow -H git -C $APP_DIR pull --ff-only' 로 한 뒤 이 스크립트를 다시 실행"
fi
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  sudo -u workflow -H "$PY" -m venv "$APP_DIR/venv"
fi
# editable 설치: templates·static·fixtures 를 src/ 에서 그대로 읽고, git pull 뒤 재설치가 필요 없다
sudo -u workflow -H "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u workflow -H "$APP_DIR/venv/bin/pip" install -q -e "$APP_DIR"
chmod 755 "$APP_DIR/deploy/backup.sh"

echo "== 4. 환경변수 파일 ($ENV_DIR, 예시 복사 — 값은 사용자가 채운다)"
for name in central diag; do
  if [ ! -f "$ENV_DIR/$name.env" ]; then
    cp "$APP_DIR/deploy/env/$name.env.example" "$ENV_DIR/$name.env"
    echo "생성: $ENV_DIR/$name.env (비밀값 비어 있음)"
  fi
  chown root:root "$ENV_DIR/$name.env"
  chmod 600 "$ENV_DIR/$name.env"
done

echo "== 5. Caddyfile"
# 이미 도메인을 적어 둔 파일은 덮어쓰지 않는다
if ! grep -q 'reverse_proxy 127.0.0.1:8000' /etc/caddy/Caddyfile 2>/dev/null; then
  install -m 644 "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
  echo "복사: /etc/caddy/Caddyfile — {\$WORKFLOW_DOMAIN} 을 실제 도메인으로 바꾼다 (DEPLOY.md 4 단계)"
fi

echo "== 6. systemd 유닛"
install -m 644 "$APP_DIR"/deploy/systemd/workflow-*.service "$APP_DIR"/deploy/systemd/workflow-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable workflow-central.service workflow-worker.service workflow-diag.service workflow-diag-worker.service
systemctl enable --now workflow-backup.timer

cat <<MSG

설치 끝. 다음은 docs/DEPLOY.md 3 단계:
  1) $ENV_DIR/central.env, $ENV_DIR/diag.env 의 비밀값·단가를 채운다 (openssl rand -hex 32)
  2) systemctl start workflow-diag workflow-diag-worker workflow-central workflow-worker
  3) systemctl status 'workflow-*'
MSG
