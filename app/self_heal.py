# from __future__ import annotations

# import json
# import shutil
# import subprocess
# from pathlib import Path
# from typing import Dict, List


# def _run(cmd: List[str], cwd: Path) -> Dict[str, str | int]:
#     try:
#         proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
#         return {
#             "returncode": proc.returncode,
#             "stdout": proc.stdout[-5000:],
#             "stderr": proc.stderr[-5000:],
#         }
#     except FileNotFoundError:
#         return {"returncode": -1, "stdout": "", "stderr": f"command not found: {cmd[0]}"}


# def apply_basic_fixes(repo: Path, format_only: bool = True, job_id: str | None = None) -> Dict[str, object]:
#     from .log_helper import emit_log, emit_error
#     repo = repo.resolve()
#     autovate_dir = repo / ".autovate"
#     autovate_dir.mkdir(parents=True, exist_ok=True)

#     steps: List[Dict[str, object]] = []

#     # black format
#     if job_id:
#         emit_log(job_id, "Running black formatter...")
#     black_res = _run(["black", "."], cwd=repo)
#     steps.append({"name": "black", **black_res})
#     if job_id:
#         if black_res.get("returncode") == 0:
#             emit_log(job_id, "Black formatting completed successfully")
#             if black_res.get("stdout"):
#                 for line in black_res["stdout"].splitlines()[:20]:
#                     emit_log(job_id, f"[black] {line}")
#         else:
#             emit_error(job_id, f"Black formatting failed: {black_res.get('stderr', '')[:200]}")

#     # ruff fix (if available)
#     if shutil.which("ruff"):
#         if job_id:
#             emit_log(job_id, "Running ruff linter/fixer...")
#         ruff_res = _run(["ruff", "check", "--fix", "."], cwd=repo)
#         steps.append({"name": "ruff", **ruff_res})
#         if job_id:
#             if ruff_res.get("returncode") == 0:
#                 emit_log(job_id, "Ruff linting completed successfully")
#             else:
#                 emit_log(job_id, f"Ruff found issues: {ruff_res.get('stderr', '')[:200]}")
#     else:
#         if job_id:
#             emit_log(job_id, "Ruff not available, skipping")

#     pylint_report = None
#     if not format_only:
#         if job_id:
#             emit_log(job_id, "Running pylint...")
#         pylint_report = _run(["pylint", repo.name], cwd=repo.parent)
#         if job_id:
#             if pylint_report.get("returncode") == 0:
#                 emit_log(job_id, "Pylint check passed")
#             else:
#                 emit_error(job_id, f"Pylint found issues: {pylint_report.get('stdout', '')[:200]}")

#     report = {
#         "applied": [s["name"] for s in steps],
#         "steps": steps,
#         "pylint": pylint_report,
#     }
#     (autovate_dir / "self_heal_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
#     return report

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List


def _run(cmd: List[str], cwd: Path) -> Dict[str, str | int]:
    """
    Run a shell command in the given directory and capture its output.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-5000:],  # Keep only the last 5000 chars for readability
            "stderr": proc.stderr[-5000:],
        }
    except FileNotFoundError:
        return {"returncode": -1, "stdout": "", "stderr": f"command not found: {cmd[0]}"}


def apply_basic_fixes(repo: Path, format_only: bool = True, job_id: str | None = None) -> Dict[str, object]:
    """
    Apply automated code formatting and linting fixes to a repository.
    - Runs Black formatter
    - Optionally runs Ruff linter (if installed)
    - Optionally runs Pylint if format_only=False
    """

    from app.log_helper import emit_log, emit_error  # imported here to avoid circular imports

    repo = repo.resolve()
    autovate_dir = repo / ".autovate"
    autovate_dir.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, object]] = []

    # Step 1: Run Black Formatter
    if job_id:
        emit_log(job_id, "Running Black formatter...")

    black_res = _run(["black", "."], cwd=repo)
    steps.append({"name": "black", **black_res})

    if job_id:
        if black_res.get("returncode") == 0:
            emit_log(job_id, "✅ Black formatting completed successfully.")
            if black_res.get("stdout"):
                for line in black_res["stdout"].splitlines()[:10]:
                    emit_log(job_id, f"[black] {line}")
        else:
            emit_error(job_id, f"❌ Black formatting failed: {black_res.get('stderr', '')[:200]}")

    # Step 2: Run Ruff Linter/Fixer (if available)
    if shutil.which("ruff"):
        if job_id:
            emit_log(job_id, "Running Ruff linter/fixer...")
        ruff_res = _run(["ruff", "check", "--fix", "."], cwd=repo)
        steps.append({"name": "ruff", **ruff_res})
        if job_id:
            if ruff_res.get("returncode") == 0:
                emit_log(job_id, "✅ Ruff linting completed successfully.")
            else:
                emit_error(job_id, f"⚠️ Ruff found issues: {ruff_res.get('stderr', '')[:200]}")
    else:
        if job_id:
            emit_log(job_id, "ℹ️ Ruff not installed, skipping step.")

    # Step 3: Run Pylint (only if format_only=False)
    pylint_report = None
    if not format_only:
        if job_id:
            emit_log(job_id, "Running Pylint analysis...")
        pylint_report = _run(["pylint", repo.name], cwd=repo.parent)
        steps.append({"name": "pylint", **pylint_report})

        if job_id:
            if pylint_report.get("returncode") == 0:
                emit_log(job_id, "✅ Pylint check passed.")
            else:
                emit_error(job_id, f"⚠️ Pylint found issues: {pylint_report.get('stdout', '')[:200]}")

    # Step 4: Write summary report
    report = {
        "applied": [s["name"] for s in steps],
        "steps": steps,
        "pylint": pylint_report,
    }

    (autovate_dir / "self_heal_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    if job_id:
        emit_log(job_id, "🟢 Self Heal process completed. Report saved to .autovate/self_heal_report.json")

    return report

