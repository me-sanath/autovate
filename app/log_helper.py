from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import job_store


def _log_root() -> Path:
    base = os.environ.get("AUTOVATE_LOG_ROOT")
    if base:
        return Path(base).expanduser().resolve()
    workspace = Path(os.environ.get("AUTOVATE_WORKSPACE_ROOT", os.getcwd())).resolve()
    return workspace / ".autovate" / "logs"


def _persist_log(job_id: str, payload: dict) -> None:
    root = _log_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / f"{job_id}.jsonl"
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload) + "\n")
    except Exception:
        # Logging persistence should never crash worker threads.
        pass


def emit_log(job_id: str, message: str, level: str = "INFO") -> None:
    """Emit a log message for a job"""
    record = {
        "type": "log",
        "job_id": job_id,
        "level": level,
        "message": message,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    job_store.publish_update(record)
    _persist_log(job_id, record)


def emit_error(job_id: str, message: str) -> None:
    """Emit an error log"""
    emit_log(job_id, message, level="ERROR")


def get_job_id() -> Optional[str]:
    """Get current Celery task ID"""
    try:
        from celery import current_task
        return current_task.request.id if current_task else None
    except Exception:
        return None

