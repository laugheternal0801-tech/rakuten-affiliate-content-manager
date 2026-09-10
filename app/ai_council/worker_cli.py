from __future__ import annotations

import argparse

from app.ai_council.repositories import fail_council_job
from app.ai_council.worker import AICouncilJobWorker
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.operating_system.providers import build_provider_registry


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI council background worker")
    parser.add_argument("--run-id", required=True, help="Persistent AI council run ID")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    settings = get_settings()
    init_db()
    registry, _ = build_provider_registry(
        settings,
        include_demo=False,
        maximum_quality=True,
    )
    if registry is None:
        with SessionLocal() as session:
            fail_council_job(session, args.run_id, "利用可能なAI APIが設定されていません。")
            session.commit()
        return 2
    return 0 if AICouncilJobWorker(registry, SessionLocal).run(args.run_id) else 1


if __name__ == "__main__":
    raise SystemExit(main())
