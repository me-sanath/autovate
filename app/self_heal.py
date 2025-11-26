from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

# Import langraph module
import sys
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))
import langraph  # type: ignore


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


def _ensure_git_clean(repo: Path) -> None:
    git_dir = repo / ".git"
    if not git_dir.exists():
        return
    status = _run(["git", "status", "--porcelain"], cwd=repo)
    if status["returncode"] != 0:
        raise RuntimeError(f"git status failed: {status.get('stderr')}")
    if status.get("stdout", "").strip():
        raise RuntimeError("Working tree has uncommitted changes; commit or stash before running self-heal")


def _persist_patches(repo: Path, fixes: List[Dict[str, object]], job_id: str | None) -> Optional[Path]:
    if not fixes:
        return None
    batch_id = job_id or f"self-heal-{int(time.time())}"
    patch_root = repo / ".autovate" / "patches" / batch_id
    patch_root.mkdir(parents=True, exist_ok=True)
    for fix in fixes:
        before = fix.get("file_before")
        after = fix.get("file_after")
        rel_path = Path(fix.get("file", "unknown"))
        if not before or not after:
            continue
        diff = difflib.unified_diff(
            str(before).splitlines(keepends=True),
            str(after).splitlines(keepends=True),
            fromfile=f"{rel_path} (before)",
            tofile=f"{rel_path} (after)",
            lineterm="",
        )
        patch_file = patch_root / rel_path
        patch_file = patch_file.with_suffix(patch_file.suffix + ".patch")
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text("".join(diff), encoding="utf-8")
    return patch_root


def _run_sanity_checks(repo: Path) -> Dict[str, Dict[str, object]]:
    checks: Dict[str, Dict[str, object]] = {}
    compile_res = _run(["python", "-m", "compileall", "-q", "."], cwd=repo)
    checks["compileall"] = compile_res
    if shutil.which("pytest"):
        checks["pytest"] = _run(["pytest", "-q"], cwd=repo)
    else:
        checks["pytest"] = {"returncode": -1, "stdout": "", "stderr": "pytest not installed"}
    return checks


def apply_ai_healing(
    repo: Path,
    job_id: str | None = None,
    groq_api_key: str | None = None,
    model: str = "llama-3.1-8b-instant",
    max_files: int = 50,
    use_ai: bool = True,
    auto_apply: bool = False,
) -> Dict[str, object]:
    """
    Apply AI-powered self-healing using LangGraph workflow.
    
    This function:
    1. Analyzes the codebase for issues
    2. Chunks code with context
    3. Uses Groq LLM to generate fixes
    4. Applies fixes to files
    5. Runs basic formatters as fallback
    """
    from .log_helper import emit_log, emit_error
    
    repo = repo.resolve()
    autovate_dir = repo / ".autovate"
    autovate_dir.mkdir(parents=True, exist_ok=True)
    
    report: Dict[str, object] = {
        "applied": [],
        "steps": [],
        "ai_healing": None,
    }
    
    # Get API key from parameter or environment
    api_key = groq_api_key or os.environ.get("GROQ_API_KEY")
    
    if use_ai and api_key:
        if job_id:
            emit_log(job_id, "Starting AI-powered self-healing workflow...")
        try:
            _ensure_git_clean(repo)
        except RuntimeError as exc:
            if job_id:
                emit_error(job_id, str(exc))
            raise
        
        try:
            # Create workflow instance
            workflow = langraph.SelfHealWorkflow(
                api_key=api_key,
                model=model,
            )
            
            # Execute workflow with logging callback
            def log_callback(jid: str, message: str, level: str = "INFO"):
                if level == "ERROR":
                    emit_error(jid, message)
                else:
                    emit_log(jid, message, level)
            
            max_bytes_env = os.environ.get("AUTOVATE_HEAL_MAX_BYTES", "200000")
            try:
                max_bytes = int(max_bytes_env)
            except ValueError:
                max_bytes = 200000

            ai_result = workflow.execute(
                root_path=repo,
                max_files=max_files,
                job_id=job_id,
                log_callback=log_callback,
                apply_changes=auto_apply,
                max_total_bytes=max_bytes,
            )
            
            report["ai_healing"] = ai_result
            report["applied"].append("ai_healing")

            patch_dir = _persist_patches(repo, ai_result.get("fixes", []), job_id)
            if patch_dir:
                report["patch_dir"] = str(patch_dir)
                if job_id:
                    emit_log(job_id, f"Captured {len(ai_result.get('fixes', []))} patches to {patch_dir}")

            if not auto_apply:
                if ai_result.get("fixes"):
                    emit_log(job_id or "self-heal", "Auto-apply disabled. Review patches before applying.")
            elif ai_result.get("applied_changes", 0) > 0:
                checks = _run_sanity_checks(repo)
                report["sanity_checks"] = checks
                if job_id:
                    emit_log(job_id, "Ran compileall/pytest sanity checks after auto-apply")
            
            if job_id:
                emit_log(
                    job_id,
                    f"AI healing completed: {ai_result.get('fixes_applied', 0)} fixes applied, "
                    f"{len(ai_result.get('errors', []))} errors"
                )
        
        except Exception as e:
            error_msg = f"AI healing failed: {str(e)}"
            if job_id:
                emit_error(job_id, error_msg)
            report["ai_healing"] = {"status": "error", "error": error_msg}
    
    elif use_ai and not api_key:
        if job_id:
            emit_log(job_id, "Groq API key not found, skipping AI healing")
        report["ai_healing"] = {"status": "skipped", "reason": "No API key"}
    
    # Always run basic fixes as fallback/complement
    if job_id:
        emit_log(job_id, "Running basic formatters (black, ruff)...")
    
    basic_result = apply_basic_fixes(repo, format_only=True, job_id=job_id)
    report["basic_fixes"] = basic_result
    report["applied"].extend(basic_result.get("applied", []))
    report["steps"].extend(basic_result.get("steps", []))
    
    # Save comprehensive report
    (autovate_dir / "self_heal_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    
    return report


