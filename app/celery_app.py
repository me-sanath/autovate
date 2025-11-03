import os
from celery import Celery


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v else default


BROKER_URL = _env("CELERY_BROKER_URL", "redis://redis:6379/0")
BACKEND_URL = _env("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

app = Celery("autovate", broker=BROKER_URL, backend=BACKEND_URL, include=["app.tasks"])

# Reasonable defaults
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=1800,
)


@app.task(bind=True)
def on_task_failure(self, exc, task_id, args, kwargs, einfo):
    """Emit error log when task fails"""
    try:
        from app.log_helper import emit_error
        error_msg = f"Task failed: {str(exc)}"
        if einfo:
            error_msg += f"\nTraceback: {str(einfo)}"
        emit_error(task_id, error_msg)
    except Exception:
        pass  # Don't fail on log emission failure


