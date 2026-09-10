from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from re import fullmatch

from app.config import PROJECT_ROOT


@dataclass(frozen=True)
class WorkerLaunch:
    pid: int
    log_path: Path


def launch_council_worker(run_id: str) -> WorkerLaunch:
    if fullmatch(r"council-[A-Za-z0-9T-]+", run_id) is None:
        raise ValueError("AI会議の実行IDが正しくありません。")
    log_dir = PROJECT_ROOT / "data" / "ai_council_workers"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"
    command = [
        sys.executable,
        "-m",
        "app.ai_council.worker_cli",
        "--run-id",
        run_id,
    ]
    with log_path.open("a", encoding="utf-8") as log_file:
        if sys.platform == "win32":
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(  # noqa: S603 - fixed module and validated run ID
                command,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                startupinfo=startup_info,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
            )
        else:
            process = subprocess.Popen(  # noqa: S603 - fixed module and validated run ID
                command,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    return WorkerLaunch(pid=process.pid, log_path=log_path)
