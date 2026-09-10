from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATABASE_FILE_NAME = "app.db"
MANIFEST_FILE_NAME = "manifest.json"
HASH_FILE_NAME = "app.db.sha256"
MANIFEST_SCHEMA_VERSION = 1
BACKUP_TIMEOUT_SECONDS = 120.0
SQLITE_BUSY_TIMEOUT_MS = 30_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MaintenanceError(RuntimeError):
    """Raised when a safe backup or restore invariant is not met."""


@dataclass(frozen=True)
class BackupInfo:
    directory: Path
    database_path: Path
    sha256: str
    size_bytes: int
    verified: bool = True


@dataclass(frozen=True)
class RestoreResult:
    restored_from: Path
    pre_restore_backup: Path | None
    sha256: str


class DatabaseValidationError(MaintenanceError):
    """Raised when SQLite content exists but cannot pass database validation."""


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _project_directories(project_root: Path) -> tuple[Path, Path, Path]:
    try:
        root = project_root.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError("The project root does not exist.") from exc
    if not root.is_dir():
        raise MaintenanceError("The project root is not a directory.")

    data_directory = root / "data"
    if not data_directory.is_dir() or _is_link_or_reparse_point(data_directory):
        raise MaintenanceError("The project data directory is missing or is a reparse point.")
    if data_directory.resolve(strict=True).parent != root:
        raise MaintenanceError("The project data directory is outside the project root.")

    backup_root = root / "backups"
    backup_root.mkdir(exist_ok=True)
    if not backup_root.is_dir() or _is_link_or_reparse_point(backup_root):
        raise MaintenanceError("The project backup directory is not a local directory.")
    if backup_root.resolve(strict=True).parent != root:
        raise MaintenanceError("The project backup directory is outside the project root.")

    return root, data_directory.resolve(strict=True), backup_root.resolve(strict=True)


def _regular_file_at(path: Path, expected_parent: Path, label: str) -> Path:
    if _is_link_or_reparse_point(path):
        raise MaintenanceError(f"{label} must not be a link or reparse point.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError(f"{label} is missing.") from exc
    if resolved.parent != expected_parent or not resolved.is_file():
        raise MaintenanceError(f"{label} is not at the expected project path.")
    return resolved


def _database_path(project_root: Path) -> tuple[Path, Path, Path, Path]:
    root, data_directory, backup_root = _project_directories(project_root)
    database_path = _regular_file_at(
        data_directory / DATABASE_FILE_NAME,
        data_directory,
        "The application database",
    )
    return root, data_directory, backup_root, database_path


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    return connection


def _check_database(database_path: Path) -> None:
    try:
        connection = _readonly_connection(database_path)
        try:
            integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if integrity_rows != ["ok"]:
                raise DatabaseValidationError("SQLite integrity_check did not return ok.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise DatabaseValidationError(
                    "SQLite foreign_key_check found one or more violations."
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        error_code = getattr(exc, "sqlite_errorcode", None)
        primary_error_code = error_code & 0xFF if isinstance(error_code, int) else None
        if primary_error_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            raise DatabaseValidationError("SQLite could not validate the database.") from exc
        raise MaintenanceError("SQLite could not validate the database.") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flush_file(path: Path) -> None:
    # Windows rejects FlushFileBuffers for read-only handles, so request write access.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _write_text_durable(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _durable_copy_to_new_file(source: Path, destination: Path) -> None:
    with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())


def _online_backup(source_path: Path, destination_path: Path) -> None:
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS

    def stop_if_timed_out(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise MaintenanceError("SQLite online backup timed out.")

    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path)
        destination = sqlite3.connect(destination_path)
        source.backup(
            destination,
            pages=256,
            progress=stop_if_timed_out,
            sleep=0.05,
        )
        destination.commit()
    except sqlite3.Error as exc:
        raise MaintenanceError("SQLite online backup failed.") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
    _flush_file(destination_path)


def _new_backup_directory(backup_root: Path, kind: str) -> Path:
    if kind not in {"manual", "pre-restore"}:
        raise MaintenanceError("Unsupported backup kind.")
    prefix = "" if kind == "manual" else "pre-restore-"
    for _attempt in range(10):
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        candidate = backup_root / f"{prefix}{timestamp}"
        if not candidate.exists():
            return candidate
        time.sleep(0.001)
    raise MaintenanceError("Could not allocate a unique backup directory.")


def _safe_remove_staging_directory(staging: Path, backup_root: Path) -> None:
    if (
        staging.exists()
        and staging.parent == backup_root
        and staging.name.startswith(".backup-in-progress-")
    ):
        shutil.rmtree(staging)


def create_backup(project_root: Path, *, kind: str = "manual") -> BackupInfo:
    """Create and atomically publish a verified online SQLite backup."""
    _root, _data_directory, backup_root, source_path = _database_path(project_root)
    final_directory = _new_backup_directory(backup_root, kind)
    staging = Path(tempfile.mkdtemp(prefix=".backup-in-progress-", dir=backup_root))
    try:
        destination_path = staging / DATABASE_FILE_NAME
        _online_backup(source_path, destination_path)
        _check_database(destination_path)

        database_hash = _sha256(destination_path)
        database_size = destination_path.stat().st_size
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "backup_kind": kind,
            "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source": {"relative_path": "data/app.db"},
            "database": {
                "file": DATABASE_FILE_NAME,
                "bytes": database_size,
                "sha256": database_hash,
            },
            "checks": {
                "integrity_check": "ok",
                "foreign_key_check": "ok",
            },
            "sqlite_version": sqlite3.sqlite_version,
        }
        _write_text_durable(
            staging / MANIFEST_FILE_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        _write_text_durable(
            staging / HASH_FILE_NAME,
            f"{database_hash}  {DATABASE_FILE_NAME}\n",
        )

        os.replace(staging, final_directory)
        return BackupInfo(
            directory=final_directory,
            database_path=final_directory / DATABASE_FILE_NAME,
            sha256=database_hash,
            size_bytes=database_size,
        )
    except Exception:
        _safe_remove_staging_directory(staging, backup_root)
        raise


def _create_unverified_pre_restore_snapshot(
    project_root: Path,
    source_path: Path,
) -> BackupInfo:
    """Preserve invalid SQLite bytes without presenting them as a restorable backup."""
    _root, data_directory, backup_root = _project_directories(project_root)
    source_path = _regular_file_at(
        source_path,
        data_directory,
        "The application database",
    )
    final_directory = _new_backup_directory(backup_root, "pre-restore")
    staging = Path(tempfile.mkdtemp(prefix=".backup-in-progress-", dir=backup_root))
    try:
        destination_path = staging / DATABASE_FILE_NAME
        _durable_copy_to_new_file(source_path, destination_path)
        database_hash = _sha256(destination_path)
        database_size = destination_path.stat().st_size
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "backup_kind": "pre-restore-unverified",
            "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source": {"relative_path": "data/app.db"},
            "database": {
                "file": DATABASE_FILE_NAME,
                "bytes": database_size,
                "sha256": database_hash,
            },
            "checks": {
                "integrity_check": "failed-or-unreadable",
                "foreign_key_check": "failed-or-not-run",
            },
            "recovery_only": True,
            "verified": False,
            "sqlite_version": sqlite3.sqlite_version,
        }
        _write_text_durable(
            staging / MANIFEST_FILE_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        _write_text_durable(
            staging / HASH_FILE_NAME,
            f"{database_hash}  {DATABASE_FILE_NAME}\n",
        )
        os.replace(staging, final_directory)
        return BackupInfo(
            directory=final_directory,
            database_path=final_directory / DATABASE_FILE_NAME,
            sha256=database_hash,
            size_bytes=database_size,
            verified=False,
        )
    except Exception:
        _safe_remove_staging_directory(staging, backup_root)
        raise


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if manifest_path.stat().st_size > 64 * 1024:
        raise MaintenanceError("The backup manifest is unexpectedly large.")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaintenanceError("The backup manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise MaintenanceError("The backup manifest must be a JSON object.")
    return value


def _manifest_database_metadata(manifest: dict[str, Any]) -> tuple[str, int]:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise MaintenanceError("The backup manifest schema version is unsupported.")
    if manifest.get("backup_kind") not in {"manual", "pre-restore"}:
        raise MaintenanceError("The backup manifest has an invalid backup kind.")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("relative_path") != "data/app.db":
        raise MaintenanceError("The backup manifest source path is invalid.")
    checks = manifest.get("checks")
    if not isinstance(checks, dict) or checks != {
        "integrity_check": "ok",
        "foreign_key_check": "ok",
    }:
        raise MaintenanceError("The backup manifest does not record successful checks.")
    database = manifest.get("database")
    if not isinstance(database, dict) or database.get("file") != DATABASE_FILE_NAME:
        raise MaintenanceError("The backup manifest database entry is invalid.")
    expected_hash = database.get("sha256")
    expected_size = database.get("bytes")
    if not isinstance(expected_hash, str) or not _SHA256_PATTERN.fullmatch(expected_hash):
        raise MaintenanceError("The backup manifest SHA-256 is invalid.")
    if type(expected_size) is not int or expected_size < 0:
        raise MaintenanceError("The backup manifest database size is invalid.")
    return expected_hash, expected_size


def _validated_backup_directory(project_root: Path, backup_directory: Path) -> tuple[Path, Path]:
    root, _data_directory, backup_root = _project_directories(project_root)
    if _is_link_or_reparse_point(backup_directory):
        raise MaintenanceError("The backup directory must not be a link or reparse point.")
    try:
        resolved = backup_directory.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError("The backup directory does not exist.") from exc
    if not resolved.is_dir() or resolved.parent != backup_root or resolved == backup_root:
        raise MaintenanceError("The backup must be a direct child of the project backup directory.")
    return root, resolved


def verify_backup(project_root: Path, backup_directory: Path) -> BackupInfo:
    """Validate the paths, manifest, hashes, integrity, and foreign keys of a backup."""
    _root, resolved_directory = _validated_backup_directory(project_root, backup_directory)
    database_path = _regular_file_at(
        resolved_directory / DATABASE_FILE_NAME,
        resolved_directory,
        "The backup database",
    )
    manifest_path = _regular_file_at(
        resolved_directory / MANIFEST_FILE_NAME,
        resolved_directory,
        "The backup manifest",
    )
    hash_path = _regular_file_at(
        resolved_directory / HASH_FILE_NAME,
        resolved_directory,
        "The backup hash file",
    )

    expected_hash, expected_size = _manifest_database_metadata(_load_manifest(manifest_path))
    if hash_path.stat().st_size > 256:
        raise MaintenanceError("The backup hash file is unexpectedly large.")
    hash_file_value = hash_path.read_text(encoding="ascii").strip()
    if hash_file_value != f"{expected_hash}  {DATABASE_FILE_NAME}":
        raise MaintenanceError("The backup hash file does not match the manifest.")
    if database_path.stat().st_size != expected_size:
        raise MaintenanceError("The backup database size does not match the manifest.")
    if _sha256(database_path) != expected_hash:
        raise MaintenanceError("The backup database SHA-256 does not match the manifest.")
    _check_database(database_path)
    return BackupInfo(
        directory=resolved_directory,
        database_path=database_path,
        sha256=expected_hash,
        size_bytes=expected_size,
    )


@contextmanager
def _restore_lock(data_directory: Path) -> Iterator[None]:
    lock_path = data_directory / ".sqlite-restore.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise MaintenanceError(
            "Another restore may be running; remove the restore lock only after "
            "confirming it is stale."
        ) from exc
    try:
        os.write(descriptor, b"SQLite restore in progress\n")
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _sqlite_sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    return tuple(
        database_path.with_name(database_path.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )


def _ensure_no_sqlite_sidecars(database_path: Path) -> None:
    for sidecar_path in _sqlite_sidecar_paths(database_path):
        if sidecar_path.exists() or _is_link_or_reparse_point(sidecar_path):
            raise MaintenanceError(
                "A SQLite sidecar file exists, so automatic recovery is unsafe. Do not delete "
                "it separately; preserve or recover the database and its sidecars together."
            )


def _remove_sqlite_sidecars_before_rollback(
    database_path: Path,
    data_directory: Path,
) -> None:
    for sidecar_path in _sqlite_sidecar_paths(database_path):
        if _is_link_or_reparse_point(sidecar_path):
            raise MaintenanceError(
                "A linked SQLite sidecar appeared during restore; automatic rollback is unsafe."
            )
        if not sidecar_path.exists():
            continue
        resolved_sidecar = _regular_file_at(
            sidecar_path,
            data_directory,
            "The SQLite sidecar",
        )
        resolved_sidecar.unlink()


def _prepare_target_for_replacement(database_path: Path) -> None:
    try:
        connection = sqlite3.connect(database_path, timeout=2, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout = 2000")
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is not None and checkpoint[0] != 0:
                raise MaintenanceError(
                    "The live database could not be checkpointed; stop the application and retry."
                )
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
            connection.execute("COMMIT")
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    except sqlite3.Error as exc:
        raise MaintenanceError(
            "The live database is busy; stop the application and retry the restore."
        ) from exc

    _ensure_no_sqlite_sidecars(database_path)


def _prepare_unverified_target_for_replacement(
    database_path: Path,
    data_directory: Path,
    snapshot: BackupInfo,
) -> None:
    current_path = _regular_file_at(
        database_path,
        data_directory,
        "The application database",
    )
    _ensure_no_sqlite_sidecars(current_path)
    if current_path.stat().st_size != snapshot.size_bytes:
        raise MaintenanceError(
            "The invalid live database changed after its recovery snapshot was created."
        )
    if _sha256(current_path) != snapshot.sha256:
        raise MaintenanceError(
            "The invalid live database changed after its recovery snapshot was created."
        )


def _durable_temporary_copy(source: Path, data_directory: Path, *, prefix: str) -> Path:
    descriptor, destination_name = tempfile.mkstemp(
        prefix=prefix,
        suffix=".tmp",
        dir=data_directory,
    )
    destination = Path(destination_name)
    try:
        with (
            source.open("rb") as source_handle,
            os.fdopen(
                descriptor,
                "wb",
            ) as destination_handle,
        ):
            descriptor = -1
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        _safe_unlink_staging(destination, data_directory)
        raise
    return destination


def _safe_unlink_staging(path: Path, data_directory: Path) -> None:
    if path.parent == data_directory and path.name.startswith(".app.db.restore-"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def restore_database(
    project_root: Path,
    backup_directory: Path,
    *,
    app_stopped: bool,
) -> RestoreResult:
    """Restore a verified backup after preserving the current database bytes."""
    if not app_stopped:
        raise MaintenanceError(
            "Restore requires explicit confirmation that the application is stopped."
        )
    root, data_directory, _backup_root = _project_directories(project_root)
    target_candidate = data_directory / DATABASE_FILE_NAME
    if _is_link_or_reparse_point(target_candidate):
        raise MaintenanceError("The restore target must not be a link or reparse point.")
    target_existed = target_candidate.exists()
    if target_existed:
        target_path = _regular_file_at(
            target_candidate,
            data_directory,
            "The application database",
        )
    else:
        target_path = target_candidate

    with _restore_lock(data_directory):
        backup_info = verify_backup(root, backup_directory)
        pre_restore: BackupInfo | None
        if not target_existed:
            _ensure_no_sqlite_sidecars(target_path)
            pre_restore = None
        else:
            try:
                _check_database(target_path)
            except DatabaseValidationError:
                _ensure_no_sqlite_sidecars(target_path)
                pre_restore = _create_unverified_pre_restore_snapshot(root, target_path)
            except MaintenanceError:
                _ensure_no_sqlite_sidecars(target_path)
                raise
            else:
                pre_restore = create_backup(root, kind="pre-restore")

        # Re-verify after creating the safety backup to close the mutation window.
        backup_info = verify_backup(root, backup_info.directory)
        staging_path = _durable_temporary_copy(
            backup_info.database_path,
            data_directory,
            prefix=".app.db.restore-",
        )
        replaced = False
        try:
            if _sha256(staging_path) != backup_info.sha256:
                raise MaintenanceError("The staged restore file failed its SHA-256 check.")
            _check_database(staging_path)
            if pre_restore is None:
                if target_path.exists() or _is_link_or_reparse_point(target_path):
                    raise MaintenanceError(
                        "The missing live database reappeared while restore was running."
                    )
                _ensure_no_sqlite_sidecars(target_path)
            elif pre_restore.verified:
                _prepare_target_for_replacement(target_path)
            else:
                _prepare_unverified_target_for_replacement(
                    target_path,
                    data_directory,
                    pre_restore,
                )
            try:
                os.replace(staging_path, target_path)
            except OSError as exc:
                raise MaintenanceError(
                    "Atomic database replacement failed; stop the application and retry."
                ) from exc
            replaced = True

            if _sha256(target_path) != backup_info.sha256:
                raise MaintenanceError("The restored database failed its SHA-256 check.")
            _check_database(target_path)
        except Exception as restore_error:
            if replaced:
                if pre_restore is None:
                    try:
                        _remove_sqlite_sidecars_before_rollback(target_path, data_directory)
                        restored_target = _regular_file_at(
                            target_path,
                            data_directory,
                            "The newly restored database",
                        )
                        restored_target.unlink()
                    except Exception as rollback_error:
                        raise MaintenanceError(
                            "Restore verification failed and the newly created database could "
                            "not be removed; the source backup remains available."
                        ) from rollback_error
                    raise MaintenanceError(
                        "Restore verification failed; the previously missing database was "
                        "removed automatically."
                    ) from restore_error
                rollback_path = _durable_temporary_copy(
                    pre_restore.database_path,
                    data_directory,
                    prefix=".app.db.restore-rollback-",
                )
                try:
                    if rollback_path.stat().st_size != pre_restore.size_bytes:
                        raise MaintenanceError(
                            "The staged automatic rollback has an unexpected size."
                        )
                    if _sha256(rollback_path) != pre_restore.sha256:
                        raise MaintenanceError(
                            "The staged automatic rollback failed its SHA-256 check."
                        )
                    _remove_sqlite_sidecars_before_rollback(target_path, data_directory)
                    os.replace(rollback_path, target_path)
                    if _sha256(target_path) != pre_restore.sha256:
                        raise MaintenanceError("Automatic rollback failed its SHA-256 check.")
                    if pre_restore.verified:
                        _check_database(target_path)
                except Exception as rollback_error:
                    raise MaintenanceError(
                        "Restore verification failed and automatic rollback failed; "
                        "the pre-restore backup remains available."
                    ) from rollback_error
                finally:
                    _safe_unlink_staging(rollback_path, data_directory)
                raise MaintenanceError(
                    "Restore verification failed; the pre-restore backup was restored "
                    "automatically."
                ) from restore_error
            raise
        finally:
            _safe_unlink_staging(staging_path, data_directory)

    return RestoreResult(
        restored_from=backup_info.directory,
        pre_restore_backup=pre_restore.directory if pre_restore else None,
        sha256=backup_info.sha256,
    )


def _validated_cli_project_root(value: str) -> Path:
    expected = Path(__file__).resolve().parents[1]
    try:
        candidate = Path(value).resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError("The supplied project root does not exist.") from exc
    if candidate != expected:
        raise MaintenanceError("The supplied project root is not this script's project root.")
    return candidate


def _relative_to_project(path: Path, project_root: Path) -> str:
    return path.relative_to(project_root).as_posix()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe local SQLite backup and restore helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--project-root", required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--project-root", required=True)
    restore_parser.add_argument("--backup-directory", required=True)
    restore_parser.add_argument("--app-stopped", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        project_root = _validated_cli_project_root(arguments.project_root)
        payload: dict[str, str | None]
        if arguments.command == "backup":
            backup = create_backup(project_root)
            payload = {
                "backup_directory": _relative_to_project(backup.directory, project_root),
                "sha256": backup.sha256,
            }
        else:
            result = restore_database(
                project_root,
                Path(arguments.backup_directory),
                app_stopped=arguments.app_stopped,
            )
            payload = {
                "restored_from": _relative_to_project(result.restored_from, project_root),
                "pre_restore_backup": (
                    _relative_to_project(result.pre_restore_backup, project_root)
                    if result.pre_restore_backup
                    else None
                ),
                "sha256": result.sha256,
            }
    except Exception as exc:  # Keep CLI failures concise and avoid dumping environment values.
        if isinstance(exc, MaintenanceError):
            message = str(exc)
        else:
            message = "SQLite maintenance failed unexpectedly."
        print(f"ERROR: {message}", file=sys.stderr)  # noqa: T201
        return 1

    print(json.dumps(payload, sort_keys=True))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
