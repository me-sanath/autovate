from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import redis

from dotenv import load_dotenv

load_dotenv()


def _redis() -> redis.Redis:
    url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    return redis.from_url(url)


_JOBS_KEY = "autovate:jobs"
_MAX_JOBS = int(os.environ.get("AUTOVATE_MAX_JOBS", "200"))
_UPDATES_CH = os.environ.get("AUTOVATE_UPDATES_CHANNEL", "autovate:updates")


def add_job(job: Dict[str, Any]) -> None:
    r = _redis()
    r.lpush(_JOBS_KEY, json.dumps(job))
    r.ltrim(_JOBS_KEY, 0, _MAX_JOBS - 1)
    publish_update({"type": "job_update", "job": job})


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    r = _redis()
    rows = r.lrange(_JOBS_KEY, 0, max(0, limit - 1))
    out: List[Dict[str, Any]] = []
    for b in rows:
        try:
            out.append(json.loads(b))
        except Exception:
            continue
    return out


def publish_update(msg: Dict[str, Any]) -> None:
    r = _redis()
    r.publish(_UPDATES_CH, json.dumps(msg))


def get_pubsub():
    r = _redis()
    ps = r.pubsub()
    ps.subscribe(_UPDATES_CH)
    return ps

