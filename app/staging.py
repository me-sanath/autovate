from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict


def validate_with_compose(repo: Path, compose_path: str | None, service: str | None, health_url: str | None, timeout: int = 120, job_id: str | None = None) -> Dict[str, object]:
    from .log_helper import emit_log, emit_error
    repo = repo.resolve()

    # Verify docker compose availability
    compose_bin = shutil.which("docker")
    if not compose_bin:
        if job_id:
            emit_error(job_id, "docker not installed in worker")
        return {"status": "skipped", "reason": "docker not installed in worker"}

    # Determine compose file
    cpath = Path(compose_path) if compose_path else (repo / "docker-compose.yml")
    if not cpath.exists():
        # fallback to Dockerfile presence check only
        if (repo / "Dockerfile").exists():
            if job_id:
                emit_log(job_id, "Dockerfile found but compose file missing")
            return {"status": "detected", "reason": "Dockerfile found; compose missing", "path": str(repo / "Dockerfile")}
        if job_id:
            emit_error(job_id, "No docker-compose.yml or Dockerfile found")
        return {"status": "skipped", "reason": "No docker-compose.yml or Dockerfile found"}

    project_name = f"autovate_stage_{int(time.time())}"
    logs_dir = repo / ".autovate" / "staging"
    logs_dir.mkdir(parents=True, exist_ok=True)
    compose_env = os.environ.copy()

    if job_id:
        emit_log(job_id, f"Starting docker compose project: {project_name}")
        emit_log(job_id, f"Using compose file: {cpath}")

    up_cmd = [
        "docker",
        "compose",
        "-f",
        str(cpath),
        "-p",
        project_name,
        "up",
        "-d",
    ]
    down_cmd = ["docker", "compose", "-f", str(cpath), "-p", project_name, "down"]

    def run(cmd):
        return subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, check=False)

    if job_id:
        emit_log(job_id, f"Running: {' '.join(up_cmd)}")
    up = run(up_cmd)
    (logs_dir / "compose_up_stdout.txt").write_text(up.stdout, encoding="utf-8")
    (logs_dir / "compose_up_stderr.txt").write_text(up.stderr, encoding="utf-8")
    if job_id:
        for line in up.stdout.splitlines()[:30]:
            emit_log(job_id, f"[compose up] {line}")
        if up.stderr:
            for line in up.stderr.splitlines()[:30]:
                emit_error(job_id, f"[compose up stderr] {line}")
    if up.returncode != 0:
        if job_id:
            emit_error(job_id, f"Docker compose up failed with return code {up.returncode}")
        return {"status": "error", "stage": "compose_up", "returncode": up.returncode}

    try:
        # Basic wait loop
        if job_id:
            emit_log(job_id, f"Waiting {timeout}s for services to be ready...")
        end = time.time() + timeout
        time.sleep(3)
        # Optionally, try a simple health check command
        health_ok = True
        if health_url:
            if job_id:
                emit_log(job_id, f"Checking health endpoint: {health_url}")
            try:
                import urllib.request

                with urllib.request.urlopen(health_url, timeout=10) as resp:  # noqa: S310
                    health_ok = 200 <= resp.status < 300
                if job_id:
                    emit_log(job_id, f"Health check returned status {resp.status}")
            except Exception as e:
                health_ok = False
                if job_id:
                    emit_error(job_id, f"Health check failed: {str(e)}")
        result = {"status": "ok" if health_ok else "degraded", "project": project_name}
        if job_id:
            emit_log(job_id, f"Staging validation completed: {result.get('status')}")
        return result
    finally:
        if job_id:
            emit_log(job_id, "Tearing down docker compose...")
        down = run(down_cmd)
        (logs_dir / "compose_down_stdout.txt").write_text(down.stdout, encoding="utf-8")
        (logs_dir / "compose_down_stderr.txt").write_text(down.stderr, encoding="utf-8")
        if job_id:
            for line in down.stdout.splitlines()[:20]:
                emit_log(job_id, f"[compose down] {line}")


