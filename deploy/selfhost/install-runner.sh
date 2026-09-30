#!/usr/bin/env bash
# 셀프호스트 러너 설정 — 호스트 Mac 네이티브 launchd (ADR-0016 결정 2, ARCHITECTURE "러너 붙이기").
# workflow 패키지를 설치하고 ~/Library/LaunchAgents/com.workflow.selfhost.connector.plist 를 쓴다.
# --server·--code·--repo 를 주면([러너 붙이기] 명령 한 줄, ADR-0018) connector setup(connect + register) 뒤 적재까지 한다.
# 인자가 없으면 connect·register 명령을 출력만 하고, launchd 적재는 연결 토큰 파일이 있을 때만 한다.
# 연결 코드·--env 값은 plist·출력에 쓰지 않는다(setup 인자로만 넘긴다).
set -euo pipefail

LABEL="com.workflow.selfhost.connector"

usage() {
  cat <<EOF
사용법: deploy/selfhost/install-runner.sh [--help]
       deploy/selfhost/install-runner.sh --server URL --code CODE --repo 폴더
           [--tool claude|codex] [--verify NAME=COMMAND]... [--link PATH]... [--copy PATH]... [--env NAME=VALUE]...

호스트 Mac 에 셀프호스트 러너(python3 -m workflow.connector run)를 launchd 로 설치한다.
  1. python3 -m pip install -e <저장소>
  2. (--server·--code·--repo 가 있으면) python3 -m workflow.connector setup … — 연결 + 저장소 등록
  3. ~/Library/LaunchAgents/$LABEL.plist 작성 (홈·python·PATH(claude/codex 위치 포함)를 채움)
  4. 2 를 했거나 연결 토큰 파일이 있으면 launchctl 로 적재, 없으면 connect 명령을 안내
--server·--code·--repo 는 셋 다 주거나 셋 다 뺀다. 연결 코드는 /operator/github 저장소 카드의 [러너 붙이기].
다시 실행해도 된다(plist 를 새로 쓰고 다시 적재).

환경변수:
  WORKFLOW_PORT     중앙 서버 포트 (기본: deploy/selfhost/.env 의 값, 없으면 8000)
  PYTHON            사용할 python3 (기본: PATH 의 python3)
  SKIP_PIP_INSTALL=1  패키지 설치를 건너뜀
  DRY_RUN=1         실행하지 않고 할 일과 plist 내용만 출력
EOF
}

SETUP_SERVER="" SETUP_CODE="" SETUP_REPO=""
SETUP_EXTRA=()   # --tool·--verify·--link·--copy·--env — setup 에 그대로 넘긴다
SHOWN_EXTRA=()   # 출력용 — --env 값은 가린다
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --server|--code|--repo|--tool|--verify|--link|--copy|--env)
      if [[ $# -lt 2 ]]; then
        echo "$1 에 값이 필요합니다. --help 를 보세요." >&2
        exit 2
      fi
      case "$1" in
        --server) SETUP_SERVER="$2" ;;
        --code) SETUP_CODE="$2" ;;
        --repo) SETUP_REPO="$2" ;;
        --env) SETUP_EXTRA+=("$1" "$2"); SHOWN_EXTRA+=("$1" "${2%%=*}=***") ;;
        *) SETUP_EXTRA+=("$1" "$2"); SHOWN_EXTRA+=("$1" "$2") ;;
      esac
      shift 2 ;;
    *)
      echo "알 수 없는 인자입니다(--server·--code·--repo·--tool·--verify·--link·--copy·--env). --help 를 보세요." >&2
      exit 2 ;;
  esac
done
SETUP=0
if [[ -n "$SETUP_SERVER" && -n "$SETUP_CODE" && -n "$SETUP_REPO" ]]; then
  SETUP=1
elif [[ -n "$SETUP_SERVER$SETUP_CODE$SETUP_REPO" || ${#SETUP_EXTRA[@]} -gt 0 ]]; then
  echo "--server·--code·--repo 는 함께 줘야 합니다(또는 모두 빼고 설치만). --help 를 보세요." >&2
  exit 2
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
  LABEL="$LABEL" PY="$PYTHON" WORKDIR="$REPO_ROOT" LOG_DIR="$LOG_DIR" LAUNCH_PATH="$LAUNCH_PATH" LAUNCH_HOME="$HOME" \
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
    # HOME: git push·fetch 가 ~/.gitconfig·자격 도우미(osxkeychain)·~/.ssh 를 찾는 위치 (ADR-0018)
    "EnvironmentVariables": {"PATH": e["LAUNCH_PATH"], "HOME": e["LAUNCH_HOME"], "LANG": "ko_KR.UTF-8"},
}
sys.stdout.write(plistlib.dumps(plist).decode())
PYEOF
}

load_plist() {
  local domain="gui/$(id -u)" i
  launchctl bootout "$domain/$LABEL" >/dev/null 2>&1 || true
  # bootout 은 서비스가 다 내려가기 전에 돌아온다 — 곧바로 bootstrap 하면 "5: Input/output error"
  for i in $(seq 1 10); do
    launchctl print "$domain/$LABEL" >/dev/null 2>&1 || break
    sleep 1
  done
  for i in 1 2 3; do
    if launchctl bootstrap "$domain" "$PLIST_PATH"; then
      echo "러너 적재: $LABEL (로그 $LOG_DIR)"
      return 0
    fi
    [[ "$i" == 3 ]] || sleep 1
  done
  echo "러너 적재에 실패했습니다. 잠시 뒤 직접 실행하세요: launchctl bootstrap $domain $PLIST_PATH" >&2
  return 1
}

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[DRY_RUN] $PYTHON -m pip install -e $REPO_ROOT"
  if [[ "$SETUP" == 1 ]]; then
    echo "[DRY_RUN] $PYTHON -m workflow.connector setup --server $SETUP_SERVER --code *** --repo $SETUP_REPO ${SHOWN_EXTRA[*]:-}"
  fi
  echo "[DRY_RUN] $PLIST_PATH 작성:"
  render_plist
  if [[ "$SETUP" == 1 ]]; then
    echo "[DRY_RUN] launchctl bootout gui/$(id -u)/$LABEL; launchctl bootstrap gui/$(id -u) $PLIST_PATH"
  else
    echo "[DRY_RUN] 연결 토큰 파일이 있으면 launchctl bootstrap gui/$(id -u) $PLIST_PATH"
  fi
else
  if [[ "${SKIP_PIP_INSTALL:-0}" != 1 ]]; then
    if ! "$PYTHON" -m pip install -e "$REPO_ROOT"; then
      echo "패키지 설치에 실패했습니다. 직접 실행한 뒤 SKIP_PIP_INSTALL=1 로 다시 실행하세요:" >&2
      echo "  $PYTHON -m pip install -e $REPO_ROOT" >&2
      exit 1
    fi
  fi
  if [[ "$SETUP" == 1 ]]; then
    # 코드는 인자로만 넘긴다 — 실패하면 적재하지 않는다(코드가 만료됐으면 [러너 붙이기]에서 다시 발급)
    if ! "$PYTHON" -m workflow.connector setup --server "$SETUP_SERVER" --code "$SETUP_CODE" --repo "$SETUP_REPO" \
        ${SETUP_EXTRA[@]+"${SETUP_EXTRA[@]}"}; then
      echo "러너 연결·등록(setup)에 실패했습니다 — launchd 에 적재하지 않았습니다. 코드가 만료됐으면 저장소 카드에서 [다시 발급] 하세요." >&2
      exit 1
    fi
  fi
  mkdir -p "$(dirname "$PLIST_PATH")" "$LOG_DIR"
  render_plist > "$PLIST_PATH"
  echo "launchd 설정 작성: $PLIST_PATH"
  if [[ "$SETUP" == 1 || -f "$CONNECTOR_HOME/token.json" ]]; then
    load_plist
  else
    echo "연결 토큰이 아직 없어 launchd 에 적재하지 않았습니다 — 아래 1·2 뒤 이 스크립트를 다시 실행하세요."
  fi
fi

if [[ "$SETUP" == 1 ]]; then
  echo
  echo "러너를 붙였습니다 — $SETUP_SERVER/operator/github 저장소 카드에 매칭 값이 보이면 끝입니다."
  exit 0
fi
cat <<EOF

사용자가 할 명령 (저장소 폴더에서):
  1. 연결 — $SERVER/operator 에서 발급한 연결 코드로:
     $PYTHON -m workflow.connector connect --server $SERVER --code <연결 코드>
  2. 저장소 등록 — 작업 폴더와 도구:
     $PYTHON -m workflow.connector register --id <등록 id> --repo <작업 폴더> --repository-id <저장소 id> --tool claude
  3. 러너 적재 — 연결 뒤 이 스크립트를 다시 실행: $SELFHOST_DIR/install-runner.sh
EOF
