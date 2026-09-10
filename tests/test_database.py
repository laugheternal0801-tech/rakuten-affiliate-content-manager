from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from app.database import configure_sqlite_engine


def test_sqlite_connections_enable_foreign_keys_and_wait_for_busy_database() -> None:
    database_engine = create_engine("sqlite://")
    configure_sqlite_engine(database_engine)

    try:
        with database_engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    finally:
        database_engine.dispose()


def test_connection_listener_is_not_registered_for_non_sqlite_engines() -> None:
    database_engine = MagicMock()
    database_engine.dialect.name = "postgresql"

    with patch("app.database.event.listen") as listen:
        configure_sqlite_engine(database_engine)

    listen.assert_not_called()
