from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List


def _discover_python_modules(repo: Path) -> List[Path]:
    files: List[Path] = []
    for p in repo.rglob("*.py"):
        # skip virtual envs, tests and hidden
        if any(part in {".venv", "venv", "__pycache__"} for part in p.parts):
            continue
        if "/tests/" in ("/" + p.as_posix() + "/"):
            continue
        files.append(p)
    return files


def generate_pytest_skeletons(repo: Path, job_id: str | None = None) -> Dict[str, str]:
    from .log_helper import emit_log
    out_dir = repo / "tests" / "auto"
    out_dir.mkdir(parents=True, exist_ok=True)
    created: Dict[str, str] = {}
    modules = _discover_python_modules(repo)
    if job_id:
        emit_log(job_id, f"Discovering Python modules in {repo}...")
        emit_log(job_id, f"Found {len(modules)} Python modules")
    for module_path in modules:
        rel = module_path.relative_to(repo)
        name = "test_" + rel.name.replace(".py", "") + ".py"
        target = out_dir / name
        if target.exists():
            continue
        # naive import path from relative path
        import_path = (
            rel.with_suffix("").as_posix().replace("/", ".")
        )
        content = (
            f"import importlib\n\n"
            f"def test_import_module():\n"
            f"    m = importlib.import_module('{import_path}')\n"
            f"    assert m is not None\n"
        )
        target.write_text(content, encoding="utf-8")
        created[str(target)] = import_path
        if job_id:
            emit_log(job_id, f"Generated test: {target.name} for {rel}")
    return created


def run_pytest(repo: Path, job_id: str | None = None) -> Dict[str, str | int]:
    from .log_helper import emit_log, emit_error
    reports_dir = repo / ".autovate" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_report = reports_dir / "pytest.json"
    cmd = [
        "pytest",
        "-q",
        "--maxfail=1",
        f"--json-report",
        f"--json-report-file={json_report}",
        "tests/auto",
    ]
    if job_id:
        emit_log(job_id, f"Running pytest: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, check=False)
        stdout_lines = proc.stdout.splitlines()
        stderr_lines = proc.stderr.splitlines()
        if job_id:
            for line in stdout_lines[:50]:  # first 50 lines
                emit_log(job_id, f"[pytest stdout] {line}")
            if proc.stderr:
                for line in stderr_lines[:50]:
                    emit_error(job_id, f"[pytest stderr] {line}")
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-5000:],
            "stderr": proc.stderr[-5000:],
            "json_report": str(json_report),
        }
    except FileNotFoundError:
        if job_id:
            emit_error(job_id, "pytest not installed")
        return {"returncode": -1, "stderr": "pytest not installed", "stdout": "", "json_report": str(json_report)}


