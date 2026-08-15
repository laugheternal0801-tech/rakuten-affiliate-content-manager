from __future__ import annotations

import argparse
import logging
from pathlib import Path

from app.services.note_format_optimizer import update_note_format_data


def main() -> None:
    parser = argparse.ArgumentParser(description="note記事フォーマットの公開反応分析を更新")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--playbook", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    playbook = update_note_format_data(
        config_path=args.config,
        snapshots_path=args.snapshots,
        playbook_path=args.playbook,
    )
    logging.info(
        "noteフォーマット分析を更新しました: status=%s sample_size=%s",
        playbook.get("status"),
        playbook.get("sample_size"),
    )


if __name__ == "__main__":
    main()
