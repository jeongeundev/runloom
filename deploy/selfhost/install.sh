#!/usr/bin/env bash
# 셀프호스트 중앙 설치·업그레이드 (ADR-0016 결정 6, ARCHITECTURE "셀프호스트 — phase 10").
# 멱등: .env 가 없을 때만 만들고(비밀값 생성, 0600), 매번 이미지 재빌드·재기동 후 /healthz 를 기다린다.
# 데이터(named volume workflow-data)는 지우지 않는다. 비밀값은 출력하지 않는다.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: deploy/selfhost/install.sh [--help]

셀프호스트 중앙(central·worker)을 Docker compose 로 설치하거나 업그레이드한다.
  1. deploy/selfhost/.env 가 없으면 .env.example 에서 만들고 SESSION_SECRET·OPERATOR_TOKEN 을 생성(0600).
     이미 있으면 건드리지 않는다.
  2. docker compose -p <프로젝트> -f deploy/selfhost/compose.yaml up -d --build
  3. http://127.0.0.1:<WORKFLOW_PORT>/healthz 가 ok 가 될 때까지 기다린다.
다시 실행하면 이미지를 다시 빌드하고 재기동한다(업그레이드). 데이터 볼륨은 유지된다.

환경변수:
  RUNLOOM_PROJECT  compose 프로젝트 이름 (기본 runloom)
  WORKFLOW_PORT    호스트 포트 (기본: .env 의 값, 없으면 8000)
  HEALTH_TIMEOUT   /healthz 대기 초 (기본 120)
  DRY_RUN=1        실행하지 않고 할 일만 출력
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SELFHOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SELFHOST_DIR/.env"
ENV_EXAMPLE="$SELFHOST_DIR/.env.example"
COMPOSE_FILE="$SELFHOST_DIR/compose.yaml"
PROJECT="${RUNLOOM_PROJECT:-runloom}"
DRY_RUN="${DRY_RUN:-0}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE")

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker 와 docker compose 가 필요합니다. Docker Desktop(https://docs.docker.com/desktop/)을 설치하고 실행한 뒤 다시 실행하세요." >&2
  exit 1
fi

# .env — 없을 때만 만든다. 비밀값은 16진수라 sed 치환에 안전하다
random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

if [[ -f "$ENV_FILE" ]]; then
  echo "기존 설정 유지: $ENV_FILE"
elif [[ "$DRY_RUN" == 1 ]]; then
  echo "[DRY_RUN] $ENV_EXAMPLE → $ENV_FILE (0600, SESSION_SECRET·OPERATOR_TOKEN 생성)"
else
  tmp="$(umask 077 && mktemp "$SELFHOST_DIR/.env.tmp.XXXXXX")"
  sed -e "s/^SESSION_SECRET=.*/SESSION_SECRET=$(random_hex)/" \
      -e "s/^OPERATOR_TOKEN=.*/OPERATOR_TOKEN=$(random_hex)/" \
      "$ENV_EXAMPLE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
  echo "설정 생성: $ENV_FILE (0600)"
fi

# 포트: 환경변수 > .env > 8000 (compose 치환 규칙과 같다)
env_port=""
if [[ -f "$ENV_FILE" ]]; then
  env_port="$(sed -n 's/^WORKFLOW_PORT=//p' "$ENV_FILE" | tail -n 1)"
fi
PORT="${WORKFLOW_PORT:-${env_port:-8000}}"
BASE_URL="http://127.0.0.1:$PORT"

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[DRY_RUN] ${COMPOSE[*]} up -d --build"
  echo "[DRY_RUN] $BASE_URL/healthz 가 ok 가 될 때까지 최대 ${HEALTH_TIMEOUT}초 대기"
  exit 0
fi

"${COMPOSE[@]}" up -d --build

echo "$BASE_URL/healthz 확인 중 (최대 ${HEALTH_TIMEOUT}초)…"
healthy=0
for ((i = 0; i < HEALTH_TIMEOUT; i++)); do
  if body="$(curl -fsS --max-time 3 "$BASE_URL/healthz" 2>/dev/null)" && [[ "$body" == *'"status":"ok"'* ]]; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" != 1 ]]; then
  echo "중앙 서버가 ${HEALTH_TIMEOUT}초 안에 정상(/healthz ok)이 되지 않았습니다. 로그를 확인하세요:" >&2
  echo "  ${COMPOSE[*]} logs central worker" >&2
  exit 1
fi

cat <<EOF

설치 완료 — 프로젝트 $PROJECT
  접속 주소   : $BASE_URL/login
  로그인 토큰 : $ENV_FILE 의 OPERATOR_TOKEN 값 (이 파일은 0600, 백업·공유하지 않는다)

다음 할 일:
  1. 브라우저에서 $BASE_URL/login 에 로그인
  2. /operator 에서 러너 연결 코드 발급
  3. 러너 설정: $SELFHOST_DIR/install-runner.sh
EOF
