from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

from fastapi import Depends, Header, HTTPException, status


class SecurityError(Exception):
    """Raised when a security invariant is violated."""


_RATE_LIMITER_LOCK = threading.Lock()
_RATE_LIMIT_WINDOWS: defaultdict[str, deque[float]] = defaultdict(deque)


def _get_allowed_roots() -> List[Path]:
    """
    Return the configured repository allow-list as resolved Paths.
    Defaults to the current workspace root if AUTOVATE_ALLOWED_REPOS is unset.
    """
    allowlist = os.environ.get("AUTOVATE_ALLOWED_REPOS")
    if allowlist:
        roots = [p.strip() for p in allowlist.split(",") if p.strip()]
    else:
        roots = [os.environ.get("AUTOVATE_WORKSPACE_ROOT", os.getcwd())]
    resolved: List[Path] = []
    for root in roots:
        resolved.append(Path(root).expanduser().resolve())
    return resolved


def _expand_repo_path(repo_path: str) -> Path:
    """
    Expand a user-supplied path. Relative paths are resolved against AUTOVATE_WORKSPACE_ROOT.
    """
    if not repo_path:
        raise SecurityError("Repository path cannot be empty")
    p = Path(repo_path).expanduser()
    if not p.is_absolute():
        base = Path(os.environ.get("AUTOVATE_WORKSPACE_ROOT", os.getcwd())).resolve()
        p = (base / p).resolve()
    else:
        p = p.resolve()
    return p


def resolve_repo_path(repo_path: str) -> Path:
    """
    Resolve and validate a repository path against the configured allow-list.
    """
    candidate = _expand_repo_path(repo_path)
    allowed_roots = _get_allowed_roots()
    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            break
        except ValueError:
            continue
    else:
        raise SecurityError(f"Repo path {candidate} is outside of allowed roots")
    if not candidate.exists():
        raise SecurityError(f"Repo path not found: {candidate}")
    return candidate


def resolve_subpath(repo_root: Path, requested_path: str | None) -> Path:
    """
    Resolve a path located inside repo_root, preventing directory traversal.
    """
    if not requested_path:
        raise SecurityError("Path value is required")
    target = (repo_root / requested_path).resolve()
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise SecurityError(f"Path {requested_path} escapes repository root") from exc
    return target


def _get_api_keys() -> List[str]:
    keys = os.environ.get("AUTOVATE_API_KEYS", "")
    return [k.strip() for k in keys.split(",") if k.strip()]


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """
    Simple API-key validation dependency.
    Returns the validated key so callers can use it for rate limiting.
    """
    keys = _get_api_keys()
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured on this worker",
        )
    token = x_api_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token or token not in keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return token


def _register_hit(bucket: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    with _RATE_LIMITER_LOCK:
        hits = _RATE_LIMIT_WINDOWS[bucket]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        hits.append(now)


def rate_limit_dependency(scope: str, limit: int = 30, window_seconds: int = 60) -> Callable[[str], None]:
    """
    FastAPI dependency factory that enforces a per-key scoped rate limit.
    """

    def _enforce(api_key: str = Depends(verify_api_key)) -> None:  # type: ignore[override]
        bucket_id = f"{api_key}:{scope}"
        _register_hit(bucket_id, limit, window_seconds)

    return _enforce


def ensure_repo_access(repo_path: str) -> Path:
    """
    Non-FastAPI helper for background workers.
    Raises FileNotFoundError when validation fails so Celery surfaces failures.
    """
    try:
        return resolve_repo_path(repo_path)
    except SecurityError as exc:
        raise FileNotFoundError(str(exc)) from exc

