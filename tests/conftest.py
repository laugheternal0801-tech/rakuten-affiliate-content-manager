from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from tests.publishing_support import PublishingFactory


@pytest.fixture
def publishing_factory(tmp_path: Path) -> Iterator[PublishingFactory]:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with Session(database) as session:
        yield PublishingFactory(session, tmp_path)
