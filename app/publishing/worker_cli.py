from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.publishing.registry import build_publisher_registry
from app.publishing.worker import PublishingWorker


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publishing worker")
    parser.add_argument("--once", action="store_true", help="Process one due-job batch")
    parser.add_argument(
        "--worker-id",
        default=f"{socket.gethostname()}-{os.getpid()}",
        help="Stable worker identifier",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _arguments()
    settings = get_settings()
    init_db()
    worker = PublishingWorker(
        settings,
        build_publisher_registry(settings),
        SessionLocal,
    )
    if args.once:
        result = await worker.run_once(args.worker_id)
        sys.stdout.write(result.model_dump_json(indent=2) + "\n")
        return
    stop_event = asyncio.Event()
    try:
        await worker.run_forever(args.worker_id, stop_event)
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(_main())
