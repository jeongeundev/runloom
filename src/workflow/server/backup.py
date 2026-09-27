"""셀프호스트 백업·복원 CLI (ADR-0016 결정 7, ARCHITECTURE "백업·복원 CLI").

`python3 -m workflow.server.backup create|list|restore`. 경로는 `WORKFLOW_DB_PATH`·`WORKFLOW_ARTIFACT_DIR`·
`WORKFLOW_BACKUP_DIR` 에서 읽고 비밀값은 요구하지 않는다(`load_settings` 를 거치지 않는다).
백업 하나 = `{백업}/{이름}/central.sqlite`(SQLite 온라인 백업 API) + `artifacts.tar.gz`. `.env`·연결 토큰 파일은 담지 않는다.
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workflow.adapters.db import connect, init_schema

DB_FILE = "central.sqlite"
ARTIFACTS_FILE = "artifacts.tar.gz"
PRE_RESTORE_PREFIX = "pre-restore-"
_STAMP = "%Y%m%dT%H%M%SZ"
_NAME = re.compile(r"^(?:pre-restore-)?(\d{8}T\d{6}Z)$")


class BackupError(Exception):
    """종료 코드 1 — 메시지를 stderr 에 낸다."""


class BackupNotFound(BackupError):
    """종료 코드 2 — 없는 백업 이름."""


@dataclass(frozen=True)
class Paths:
    db_path: Path
    artifact_dir: Path
    backup_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Paths":
        return cls(
            db_path=Path(env.get("WORKFLOW_DB_PATH") or "data/central.sqlite"),
            artifact_dir=Path(env.get("WORKFLOW_ARTIFACT_DIR") or "data/artifacts"),
            backup_dir=Path(env.get("WORKFLOW_BACKUP_DIR") or "data/backups"),
        )


@dataclass(frozen=True)
class BackupInfo:
    name: str
    created_at: datetime
    size_bytes: int
    schema_version: int | None


def _check_db(path: Path) -> int | None:
    """복사본 무결성 확인 후 schema_version 을 돌려준다. 손상이면 BackupError."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"백업 DB 가 손상되었습니다: {path}")
            try:
                row = conn.execute("SELECT version FROM schema_version").fetchone()
            except sqlite3.OperationalError:
                return None
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise BackupError(f"백업 DB 가 손상되었습니다: {path}") from None


def _check_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as tar:
            tar.getmembers()
    except (tarfile.TarError, OSError, EOFError):
        raise BackupError(f"산출물 묶음이 손상되었습니다: {path}") from None


def create_backup(paths: Paths, now: datetime, prefix: str = "") -> str:
    """DB 를 온라인 백업 API 로 복사하고(서버·워커가 돌아도 안전) 산출물을 tar.gz 로 묶는다. 이름을 돌려준다."""
    if not paths.db_path.is_file():
        raise BackupError(f"DB 가 없습니다: {paths.db_path}")
    name = prefix + now.astimezone(UTC).strftime(_STAMP)
    final = paths.backup_dir / name
    if final.exists():
        raise BackupError(f"같은 이름의 백업이 이미 있습니다: {name}")
    paths.backup_dir.mkdir(parents=True, exist_ok=True)
    paths.backup_dir.chmod(0o700)
    # 다 만든 뒤에만 이름을 붙인다 — 중간에 실패한 백업이 목록·복원에 나오지 않게
    partial = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=paths.backup_dir))
    try:
        src = sqlite3.connect(paths.db_path)
        dst = sqlite3.connect(partial / DB_FILE)
        try:
            src.backup(dst)
            # 복사본을 단일 파일로 둔다(-wal·-shm 없이 읽기 전용으로 열 수 있게)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
            src.close()
        _check_db(partial / DB_FILE)
        with tarfile.open(partial / ARTIFACTS_FILE, "w:gz") as tar:
            if paths.artifact_dir.is_dir():
                tar.add(paths.artifact_dir, arcname="artifacts")
            else:
                tar.addfile(_dir_entry("artifacts"))
        partial.rename(final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return name


def _dir_entry(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    """새것부터. 이름 규칙에 맞고 DB 파일이 있는 디렉터리만."""
    infos: list[BackupInfo] = []
    if not backup_dir.is_dir():
        return infos
    for entry in backup_dir.iterdir():
        match = _NAME.match(entry.name)
        if not match or not (entry / DB_FILE).is_file():
            continue
        try:
            version = _check_db(entry / DB_FILE)
        except BackupError:
            version = None
        infos.append(
            BackupInfo(
                name=entry.name,
                created_at=datetime.strptime(match.group(1), _STAMP).replace(tzinfo=UTC),
                size_bytes=sum(p.stat().st_size for p in entry.iterdir() if p.is_file()),
                schema_version=version,
            )
        )
    infos.sort(key=lambda i: (i.created_at, i.name), reverse=True)
    return infos


def prune(backup_dir: Path, keep: int) -> list[str]:
    """최근 `keep` 개만 남기고 오래된 것부터 지운다. 지운 이름을 돌려준다."""
    removed = [info.name for info in list_backups(backup_dir)[keep:]]
    for name in removed:
        shutil.rmtree(backup_dir / name)
    return removed


def restore_backup(paths: Paths, name: str, force: bool, now: datetime) -> str | None:
    """central·worker 를 멈춘 뒤 쓴다. 백업을 검사하고, 현재 DB 가 있으면 `force` 일 때만 복원 전 백업을 만든 뒤 바꾼다.
    복원 전 백업 이름(없으면 None)을 돌려준다."""
    source = paths.backup_dir / name
    if not _NAME.match(name) or not (source / DB_FILE).is_file():
        raise BackupNotFound(f"백업이 없습니다: {name}")
    _check_db(source / DB_FILE)
    _check_archive(source / ARTIFACTS_FILE)
    pre = None
    if paths.db_path.exists():
        if not force:
            raise BackupError(f"대상 DB 가 이미 있습니다: {paths.db_path} — 덮어쓰려면 --force")
        pre = create_backup(paths, now, prefix=PRE_RESTORE_PREFIX)

    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    staged_db = paths.db_path.with_name(paths.db_path.name + ".restoring")
    shutil.copyfile(source / DB_FILE, staged_db)
    # 남은 -wal·-shm 이 새 DB 에 적용되면 안 된다
    for suffix in ("-wal", "-shm"):
        Path(str(paths.db_path) + suffix).unlink(missing_ok=True)
    os.replace(staged_db, paths.db_path)

    paths.artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=paths.artifact_dir.parent))
    try:
        with tarfile.open(source / ARTIFACTS_FILE, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        extracted = staging / "artifacts"
        extracted.mkdir(exist_ok=True)
        if paths.artifact_dir.exists():
            shutil.rmtree(paths.artifact_dir)
        extracted.rename(paths.artifact_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    conn = connect(paths.db_path)
    try:
        init_schema(conn)
    except Exception as exc:
        raise BackupError(f"복원한 DB 의 스키마 확인 실패: {exc}") from None
    finally:
        conn.close()
    return pre


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m workflow.server.backup",
        description="중앙 DB·산출물 백업·복원. 비밀값(.env·연결 토큰)은 담지 않는다.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="온라인 백업 (서버·워커가 돌아도 된다)")
    create.add_argument("--dest", help="백업 디렉터리 (기본 WORKFLOW_BACKUP_DIR)")
    create.add_argument("--keep", type=int, help="최근 N 개만 남기고 오래된 백업을 지운다")
    sub.add_parser("list", help="백업 목록 (새것부터: 이름·시각·크기·스키마 버전)")
    restore = sub.add_parser(
        "restore",
        help="백업으로 되돌린다 — central·worker 를 멈춘 뒤 실행",
        description="central·worker 를 멈춘 뒤 실행한다. 현재 DB 를 pre-restore-{시각} 으로 먼저 백업하고 바꾼다.",
    )
    restore.add_argument("name", help="백업 이름 (list 참고)")
    restore.add_argument("--force", action="store_true", help="대상 DB 가 있어도 덮어쓴다")
    return parser


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] = os.environ,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    paths = Paths.from_env(env)
    try:
        if args.command == "create":
            if args.dest:
                paths = Paths(paths.db_path, paths.artifact_dir, Path(args.dest))
            name = create_backup(paths, now())
            if args.keep is not None:
                for removed in prune(paths.backup_dir, args.keep):
                    print(f"지움: {removed}")
            print(name)
        elif args.command == "list":
            for info in list_backups(paths.backup_dir):
                version = info.schema_version if info.schema_version is not None else "?"
                stamp = info.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                print(f"{info.name}\t{stamp}\t{info.size_bytes}\tschema {version}")
        else:
            pre = restore_backup(paths, args.name, args.force, now())
            if pre:
                print(f"복원 전 백업: {pre}")
            print(f"복원: {args.name}")
    except BackupNotFound as exc:
        print(exc, file=sys.stderr)
        return 2
    except BackupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
