from __future__ import annotations

from typing import Optional
from . import job_store


def emit_log(job_id: str, message: str, level: str = "INFO") -> None:
    """Emit a log message for a job"""
    job_store.publish_update({
        "type": "log",
        "job_id": job_id,
        "level": level,
        "message": message,
    })


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

