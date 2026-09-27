#!/usr/bin/env bash
# 셀프호스트 러너 설정 — 호스트 Mac 네이티브 launchd (ADR-0016 결정 2, ARCHITECTURE "러너 붙이기").
# workflow 패키지를 설치하고 ~/Library/LaunchAgents/com.workflow.selfhost.connector.plist 를 쓴다.
# 연결 코드 교환(connect)·저장소 등록(register)은 사용자가 할 명령으로 출력만 한다 — 이 스크립트는 토큰을 다루지 않는다.
# launchd 적재는 연결 토큰 파일이 있을 때만 한다(없으면 connect 뒤 다시 실행).
set -euo pipefail

LABEL="com.workflow.selfhost.connector"

usage() {
  cat <<EOF
사용법: deploy/selfhost/install-runner.sh [--help]

호스트 Mac 에 셀프호스트 러너(python3 -m workflow.connector run)를 launchd 로 설치한다.
  1. python3 -m pip install -e <저장소>
  2. ~/Library/LaunchAgents/$LABEL.plist 작성 (홈·python·PATH(claude/codex 위치 포함)를 채움)
  3. 연결 토큰 파일이 있으면 launchctl 로 적재, 없으면 connect 명령을 안내
다시 실행해도 된다(plist 를 새로 쓰고 다시 적재).

환경변수:
  WORKFLOW_PORT     중앙 서버 포트 (기본: deploy/selfhost/.env 의 값, 없으면 8000)
  PYTHON            사용할 python3 (기본: PATH 의 python3)
  SKIP_PIP_INSTALL=1  패키지 설치를 건너뜀
  DRY_RUN=1         실행하지 않고 할 일과 plist 내용만 출력
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

DRY_RUN="${DRY_RUN:-0}"
SELFHOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELFHOST_DIR/../.." && pwd)"

if [[ "$(uname -s)" != Darwin && "$DRY_RUN" != 1 ]]; then
  echo "install-runner.sh 는 macOS(launchd) 전용입니다." >&2
  exit 1
fi

PYTHON="${PYTHON:-$(command -v python3 || true)}"
if [[ -z "$PYTHON" ]]; then
  echo "python3 가 필요합니다 (3.13 계열)." >&2
  exit 1
fi
# pyenv shim 같은 래퍼가 아닌 실제 인터프리터 — launchd 는 사용자 셸 설정을 읽지 않는다
PYTHON="$("$PYTHON" -c 'import sys; print(sys.executable)')"

env_port=""
if [[ -f "$SELFHOST_DIR/.env" ]]; then
  env_port="$(sed -n 's/^WORKFLOW_PORT=//p' "$SELFHOST_DIR/.env" | tail -n 1)"
fi
SERVER="http://127.0.0.1:${WORKFLOW_PORT:-${env_port:-8000}}"

# launchd 에 줄 PATH: python·claude·codex 위치 + 기본 경로 (중복 제거)
LAUNCH_PATH=""
add_path() {
  case ":$LAUNCH_PATH:" in
    *":$1:"*) ;;
    *) LAUNCH_PATH="${LAUNCH_PATH:+$LAUNCH_PATH:}$1" ;;
  esac
}
add_path "$(dirname "$PYTHON")"
for tool in claude codex; do
  if tool_path="$(command -v "$tool")"; then
    add_path "$(dirname "$tool_path")"
  else
    echo "참고: $tool 를 PATH 에서 찾지 못했습니다 — 그 도구로 등록한 저장소는 실행되지 않습니다." >&2
  fi
done
for dir in /opt/homebrew/bin /usr/local/bin /usr/bin /bin; do
  add_path "$dir"
done

PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/workflow-connector-selfhost"
CONNECTOR_HOME="${WORKFLOW_CONNECTOR_HOME:-$HOME/Library/Application Support/workflow-connector}"

render_plist() {
  LABEL="$LABEL" PY="$PYTHON" WORKDIR="$REPO_ROOT" LOG_DIR="$LOG_DIR" LAUNCH_PATH="$LAUNCH_PATH" \
    "$PYTHON" - <<'PYEOF'
import os
import plistlib
import sys

e = os.environ
plist = {
    "Label": e["LABEL"],
    "ProgramArguments": [e["PY"], "-m", "workflow.connector", "run"],
    "KeepAlive": True,
    "RunAtLoad": True,
    "WorkingDirectory": e["WORKDIR"],
    "StandardOutPath": os.path.join(e["LOG_DIR"], "stdout.log"),
    "StandardErrorPath": os.path.join(e["LOG_DIR"], "stderr.log"),
    "EnvironmentVariables": {"PATH": e["LAUNCH_PATH"], "LANG": "ko_KR.UTF-8"},
}
sys.stdout.write(plistlib.dumps(plist).decode())
PYEOF
}

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[DRY_RUN] $PYTHON -m pip install -e $REPO_ROOT"
  echo "[DRY_RUN] $PLIST_PATH 작성:"
  render_plist
  echo "[DRY_RUN] 연결 토큰 파일이 있으면 launchctl bootstrap gui/$(id -u) $PLIST_PATH"
else
  if [[ "${SKIP_PIP_INSTALL:-0}" != 1 ]]; then
    if ! "$PYTHON" -m pip install -e "$REPO_ROOT"; then
      echo "패키지 설치에 실패했습니다. 직접 실행한 뒤 SKIP_PIP_INSTALL=1 로 다시 실행하세요:" >&2
      echo "  $PYTHON -m pip install -e $REPO_ROOT" >&2
      exit 1
    fi
  fi
  mkdir -p "$(dirname "$PLIST_PATH")" "$LOG_DIR"
  render_plist > "$PLIST_PATH"
  echo "launchd 설정 작성: $PLIST_PATH"
  if [[ -f "$CONNECTOR_HOME/token.json" ]]; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
    echo "러너 적재: $LABEL (로그 $LOG_DIR)"
  else
    echo "연결 토큰이 아직 없어 launchd 에 적재하지 않았습니다 — 아래 1·2 뒤 이 스크립트를 다시 실행하세요."
  fi
fi

cat <<EOF

사용자가 할 명령 (저장소 폴더에서):
  1. 연결 — $SERVER/operator 에서 발급한 연결 코드로:
     $PYTHON -m workflow.connector connect --server $SERVER --code <연결 코드>
  2. 저장소 등록 — 작업 폴더와 도구:
     $PYTHON -m workflow.connector register --id <등록 id> --repo <작업 폴더> --repository-id <저장소 id> --tool claude
  3. 러너 적재 — 연결 뒤 이 스크립트를 다시 실행: $SELFHOST_DIR/install-runner.sh
EOF
