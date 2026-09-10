from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import scripts.sqlite_maintenance as sqlite_maintenance
from scripts.sqlite_maintenance import (
    MaintenanceError,
    create_backup,
    restore_database,
    verify_backup,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "backups").mkdir()
    return tmp_path


def _create_database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, "
            "parent_id INTEGER NOT NULL REFERENCES parent(id)"
            ")"
        )
        connection.execute("INSERT INTO parent (id, value) VALUES (1, ?)", (value,))
        connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 1)")
        connection.commit()
    finally:
        connection.close()


def _database_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM parent WHERE id = 1").fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


def test_online_backup_writes_verified_manifest_and_hash(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "snapshot")

    live_connection = sqlite3.connect(database_path)
    try:
        live_connection.execute("PRAGMA journal_mode = WAL")
        live_connection.execute("UPDATE parent SET value = 'committed snapshot' WHERE id = 1")
        live_connection.commit()

        backup = create_backup(project_root)
    finally:
        live_connection.close()

    verified = verify_backup(project_root, backup.directory)
    manifest = json.loads((backup.directory / "manifest.json").read_text(encoding="utf-8"))
    hash_line = (backup.directory / "app.db.sha256").read_text(encoding="ascii").strip()

    assert verified.sha256 == manifest["database"]["sha256"]
    assert hash_line == f"{verified.sha256}  app.db"
    assert manifest["checks"] == {
        "foreign_key_check": "ok",
        "integrity_check": "ok",
    }
    assert _database_value(backup.database_path) == "committed snapshot"


def test_restore_validates_backup_and_keeps_a_pre_restore_backup(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE parent SET value = 'before restore' WHERE id = 1")
        connection.commit()
    finally:
        connection.close()

    result = restore_database(project_root, backup.directory, app_stopped=True)

    assert _database_value(database_path) == "from backup"
    assert result.pre_restore_backup is not None
    assert _database_value(result.pre_restore_backup / "app.db") == "before restore"
    verify_backup(project_root, result.pre_restore_backup)


def test_restore_recreates_a_missing_live_database(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    database_path.unlink()

    result = restore_database(project_root, backup.directory, app_stopped=True)

    assert result.pre_restore_backup is None
    assert _database_value(database_path) == "from backup"
    assert not list((project_root / "backups").glob("pre-restore-*"))


def test_failed_restore_returns_to_missing_database_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    database_path.unlink()
    original_check = sqlite_maintenance._check_database

    def fail_post_replace_check(path: Path) -> None:
        original_check(path)
        if path.resolve() == database_path.resolve():
            raise MaintenanceError("forced post-restore verification failure")

    monkeypatch.setattr(sqlite_maintenance, "_check_database", fail_post_replace_check)

    with pytest.raises(MaintenanceError, match="previously missing database"):
        restore_database(project_root, backup.directory, app_stopped=True)

    assert not database_path.exists()
    assert not list((project_root / "backups").glob("pre-restore-*"))


def test_restore_rejects_orphan_sidecar_when_live_database_is_missing(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    database_path.unlink()
    sidecar_path = database_path.with_name("app.db-wal")
    sidecar_bytes = b"orphan sidecar"
    sidecar_path.write_bytes(sidecar_bytes)

    with pytest.raises(MaintenanceError, match="sidecar file exists"):
        restore_database(project_root, backup.directory, app_stopped=True)

    assert not database_path.exists()
    assert sidecar_path.read_bytes() == sidecar_bytes


def test_restore_preserves_invalid_live_database_as_recovery_only_snapshot(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    invalid_database = b"this is not a SQLite database\n"
    database_path.write_bytes(invalid_database)

    result = restore_database(project_root, backup.directory, app_stopped=True)

    assert _database_value(database_path) == "from backup"
    assert result.pre_restore_backup is not None
    assert (result.pre_restore_backup / "app.db").read_bytes() == invalid_database
    manifest = json.loads((result.pre_restore_backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backup_kind"] == "pre-restore-unverified"
    assert manifest["recovery_only"] is True
    assert manifest["verified"] is False
    with pytest.raises(MaintenanceError, match="invalid backup kind"):
        verify_backup(project_root, result.pre_restore_backup)


@pytest.mark.parametrize("sidecar_suffix", ["-wal", "-shm", "-journal"])
def test_restore_rejects_invalid_live_database_with_sqlite_sidecar(
    tmp_path: Path,
    sidecar_suffix: str,
) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    invalid_database = b"this is not a SQLite database\n"
    database_path.write_bytes(invalid_database)
    sidecar_path = database_path.with_name(database_path.name + sidecar_suffix)
    sidecar_bytes = b"unrecovered SQLite sidecar bytes"
    sidecar_path.write_bytes(sidecar_bytes)

    with pytest.raises(MaintenanceError, match="sidecar file exists"):
        restore_database(project_root, backup.directory, app_stopped=True)

    assert database_path.read_bytes() == invalid_database
    assert sidecar_path.read_bytes() == sidecar_bytes
    assert not list((project_root / "backups").glob("pre-restore-*"))


def test_failed_restore_rolls_back_to_invalid_live_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "from backup")
    backup = create_backup(project_root)
    invalid_database = b"this is not a SQLite database\n"
    database_path.write_bytes(invalid_database)
    original_check = sqlite_maintenance._check_database

    def fail_post_replace_check(path: Path) -> None:
        original_check(path)
        if path.resolve() == database_path.resolve():
            database_path.with_name("app.db-wal").write_bytes(b"new restore sidecar")
            raise MaintenanceError("forced post-restore verification failure")

    monkeypatch.setattr(sqlite_maintenance, "_check_database", fail_post_replace_check)

    with pytest.raises(MaintenanceError, match="restored automatically"):
        restore_database(project_root, backup.directory, app_stopped=True)

    assert database_path.read_bytes() == invalid_database
    assert not database_path.with_name("app.db-wal").exists()
    snapshots = list((project_root / "backups").glob("pre-restore-*"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "app.db").read_bytes() == invalid_database
    expected_hash = hashlib.sha256(invalid_database).hexdigest()
    assert (snapshots[0] / "app.db.sha256").read_text(encoding="ascii").strip() == (
        f"{expected_hash}  app.db"
    )


def test_locked_live_database_does_not_use_unverified_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "live value")
    backup = create_backup(project_root)
    monkeypatch.setattr(sqlite_maintenance, "SQLITE_BUSY_TIMEOUT_MS", 10)

    locking_connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        locking_connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(MaintenanceError, match="could not validate"):
            restore_database(project_root, backup.directory, app_stopped=True)
    finally:
        locking_connection.rollback()
        locking_connection.close()

    assert _database_value(database_path) == "live value"
    assert not list((project_root / "backups").glob("pre-restore-*"))


def test_restore_rejects_tampered_backup_before_changing_live_database(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    _create_database(database_path, "live value")
    backup = create_backup(project_root)
    with (backup.directory / "app.db").open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(MaintenanceError, match="size does not match"):
        restore_database(project_root, backup.directory, app_stopped=True)

    assert _database_value(database_path) == "live value"
    assert not list((project_root / "backups").glob("pre-restore-*"))


def test_backup_rejects_foreign_key_violations(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    database_path = project_root / "data" / "app.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child (parent_id) VALUES (999)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(MaintenanceError, match="foreign_key_check"):
        create_backup(project_root)

    assert list((project_root / "backups").iterdir()) == []
