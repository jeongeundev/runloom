#!/usr/bin/env bash
set -euo pipefail
# 일일 백업 — ARCHITECTURE 재시작·백업·보존 절.
# SQLite DB 세 개(중앙·진단·연결 프로그램 상태)를 `sqlite3 .backup` 으로(온라인 안전 복사), 산출물 디렉터리 두 개를 tar 로
# /var/backups/workflow/{YYYY-MM-DD}/ 에 두고 7일 지난 날짜 디렉터리를 지운다.
# workflow-backup.timer 가 03:00 에 workflow 사용자로 실행한다. 비밀값이 든 env 파일은 읽지 않는다 —
# 경로가 기본값과 다르면 유닛의 Environment= 로 아래 변수를 넘긴다.
# 진단 fixture 는 저장소에 있으므로 대상이 아니다. 연결 프로그램 디렉터리(ADR-0008: 같은 VM)에서는 상태 DB 만 백업한다 —
# 연결 토큰 파일은 비밀값이라 복사하지 않고(재연결 가능), 로그·인계 디렉터리는 일시 자료다.

CENTRAL_DB="${WORKFLOW_DB_PATH:-/var/lib/workflow/central/db.sqlite}"
CENTRAL_ARTIFACTS="${WORKFLOW_ARTIFACT_DIR:-/var/lib/workflow/central/artifacts}"
DIAG_DB="${DIAG_DB_PATH:-/var/lib/workflow/diag/db.sqlite}"
DIAG_ARTIFACTS="${DIAG_ARTIFACT_DIR:-/var/lib/workflow/diag/artifacts}"
CONNECTOR_DB="${WORKFLOW_CONNECTOR_HOME:-/var/lib/workflow/connector}/state.sqlite"
BACKUP_ROOT="${WORKFLOW_BACKUP_DIR:-/var/backups/workflow}"

DEST="$BACKUP_ROOT/$(date +%Y-%m-%d)"
mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

backup_db() { # <db 경로> <대상 파일>
  if [ -f "$1" ]; then
    sqlite3 "$1" ".backup '$2'"
    echo "db: $1 -> $2"
  else
    echo "건너뜀: DB 없음 $1"
  fi
}

backup_dir() { # <디렉터리> <대상 tar.gz>
  if [ -d "$1" ]; then
    tar -czf "$2" -C "$(dirname "$1")" "$(basename "$1")"
    echo "dir: $1 -> $2"
  else
    echo "건너뜀: 디렉터리 없음 $1"
  fi
}

backup_db "$CENTRAL_DB" "$DEST/central.sqlite"
backup_db "$DIAG_DB" "$DEST/diag.sqlite"
backup_db "$CONNECTOR_DB" "$DEST/connector-state.sqlite"
backup_dir "$CENTRAL_ARTIFACTS" "$DEST/central-artifacts.tar.gz"
backup_dir "$DIAG_ARTIFACTS" "$DEST/diag-artifacts.tar.gz"

# 보존 7일: 날짜 디렉터리만 지운다 (BACKUP_ROOT 바로 아래 1단계)
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
echo "완료: $DEST"
