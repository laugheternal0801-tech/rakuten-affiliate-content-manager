from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import PROJECT_ROOT, get_settings
from app.models import Base

Path(PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)

engine: Engine = create_engine(
    get_settings().database_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


def _set_sqlite_connection_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    """Apply safety and contention settings to each new SQLite connection."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


def configure_sqlite_engine(database_engine: Engine) -> None:
    """Register SQLite-only connection initialization on an engine."""
    if database_engine.dialect.name != "sqlite":
        return
    if not event.contains(database_engine, "connect", _set_sqlite_connection_pragmas):
        event.listen(database_engine, "connect", _set_sqlite_connection_pragmas)


configure_sqlite_engine(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    # Import additive subsystem models before create_all so their tables are registered.
    from app.ai_council import models as _ai_council_models  # noqa: F401
    from app.creative_production import models as _creative_production_models  # noqa: F401
    from app.market_intelligence import models as _market_intelligence_models  # noqa: F401
    from app.operating_system import models as _operating_system_models  # noqa: F401
    from app.publishing import models as _publishing_models  # noqa: F401
    from app.research_catalog import models as _research_catalog_models  # noqa: F401

    Base.metadata.create_all(engine)
    from app.research_catalog.repositories import ensure_schema_version

    with SessionLocal.begin() as session:
        ensure_schema_version(session)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
