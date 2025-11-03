from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List


def _run(cmd: List[str], cwd: Path) -> Dict[str, str | int]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-5000:],
            "stderr": proc.stderr[-5000:],
        }
    except FileNotFoundError:
        return {"returncode": -1, "stdout": "", "stderr": f"command not found: {cmd[0]}"}


def apply_basic_fixes(repo: Path, format_only: bool = True, job_id: str | None = None) -> Dict[str, object]:
    from .log_helper import emit_log, emit_error
    repo = repo.resolve()
    autovate_dir = repo / ".autovate"
    autovate_dir.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, object]] = []

    # black format
    if job_id:
        emit_log(job_id, "Running black formatter...")
    black_res = _run(["black", "."], cwd=repo)
    steps.append({"name": "black", **black_res})
    if job_id:
        if black_res.get("returncode") == 0:
            emit_log(job_id, "Black formatting completed successfully")
            if black_res.get("stdout"):
                for line in black_res["stdout"].splitlines()[:20]:
                    emit_log(job_id, f"[black] {line}")
        else:
            emit_error(job_id, f"Black formatting failed: {black_res.get('stderr', '')[:200]}")

    # ruff fix (if available)
    if shutil.which("ruff"):
        if job_id:
            emit_log(job_id, "Running ruff linter/fixer...")
        ruff_res = _run(["ruff", "check", "--fix", "."], cwd=repo)
        steps.append({"name": "ruff", **ruff_res})
        if job_id:
            if ruff_res.get("returncode") == 0:
                emit_log(job_id, "Ruff linting completed successfully")
            else:
                emit_log(job_id, f"Ruff found issues: {ruff_res.get('stderr', '')[:200]}")
    else:
        if job_id:
            emit_log(job_id, "Ruff not available, skipping")

    pylint_report = None
    if not format_only:
        if job_id:
            emit_log(job_id, "Running pylint...")
        pylint_report = _run(["pylint", repo.name], cwd=repo.parent)
        if job_id:
            if pylint_report.get("returncode") == 0:
                emit_log(job_id, "Pylint check passed")
            else:
                emit_error(job_id, f"Pylint found issues: {pylint_report.get('stdout', '')[:200]}")

    report = {
        "applied": [s["name"] for s in steps],
        "steps": steps,
        "pylint": pylint_report,
    }
    (autovate_dir / "self_heal_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


