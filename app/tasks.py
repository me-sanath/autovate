import json
import os
from celery import shared_task
from pathlib import Path

from . import testgen, self_heal as sh, staging, job_store
from .docgen import generate_documentation, DocOptions
from .log_helper import emit_log, emit_error, get_job_id
from .security import ensure_repo_access

# Import local langraph module
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
import langraph  # type: ignore


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _validated_repo(repo_path: str) -> Path:
    return ensure_repo_access(repo_path)


@shared_task(name="tasks.generate_docs")
def generate_docs(repo_path: str, use_llm: bool = False, template: str | None = None, export_formats: list[str] | None = None, manual_override: bool = False):
    job_id = get_job_id() or "unknown"
    repo = _validated_repo(repo_path)
    emit_log(job_id, f"Starting documentation generation for {repo_path}")
    if not repo.exists():
        emit_error(job_id, f"Repo path not found: {repo}")
        raise FileNotFoundError(f"Repo path not found: {repo}")
    opts = DocOptions(
        template=(template or "api"),
        export_formats=(export_formats or ["md", "html"]),
        use_llm=bool(use_llm),
        manual_override=bool(manual_override),
    )
    emit_log(job_id, f"Using template: {opts.template}, formats: {opts.export_formats}")
    try:
        result = generate_documentation(str(repo), opts)
        emit_log(job_id, f"Documentation generated successfully: {result.get('summary', 'N/A')}")
        for err in result.get("llm_errors", []) or []:
            emit_error(job_id, f"LLM doc generation error: {err}")
        try:
            from celery import current_task
            job_store.publish_update({
                "type": "job_update",
                "job": {"id": current_task.request.id, "state": "SUCCESS", "result": result}
            })
        except Exception:
            pass
        return result
    except Exception as e:
        emit_error(job_id, f"Error generating documentation: {str(e)}")
        raise


@shared_task(name="tasks.generate_and_run_tests")
def generate_and_run_tests(repo_path: str):
    job_id = get_job_id() or "unknown"
    repo = _validated_repo(repo_path)
    emit_log(job_id, f"Starting test generation and execution for {repo_path}")
    if not repo.exists():
        emit_error(job_id, f"Repo path not found: {repo}")
        raise FileNotFoundError(f"Repo path not found: {repo}")
    emit_log(job_id, "Generating pytest test skeletons...")
    gen = testgen.generate_pytest_skeletons(repo, job_id)
    emit_log(job_id, f"Generated {len(gen)} test files")
    emit_log(job_id, "Running pytest...")
    report = testgen.run_pytest(repo, job_id)
    result = {"generated": gen, "report": report}
    if report.get("returncode") == 0:
        emit_log(job_id, "Tests passed successfully")
    else:
        emit_error(job_id, f"Tests failed with return code {report.get('returncode')}")
        if report.get("stderr"):
            emit_error(job_id, f"Error output: {report['stderr'][:500]}")
    try:
        from celery import current_task
        job_store.publish_update({
            "type": "job_update",
            "job": {"id": current_task.request.id, "state": "SUCCESS", "result": result}
        })
    except Exception:
        pass
    return result


@shared_task(name="tasks.self_heal")
def self_heal(repo_path: str, format_only: bool = True, use_ai: bool = True, groq_api_key: str | None = None, model: str = "llama-3.1-8b-instant", auto_apply: bool = False):
    job_id = get_job_id() or "unknown"
    repo = _validated_repo(repo_path)
    emit_log(job_id, f"Starting self-heal for {repo_path} (format_only={format_only}, use_ai={use_ai})")
    if not repo.exists():
        emit_error(job_id, f"Repo path not found: {repo}")
        raise FileNotFoundError(f"Repo path not found: {repo}")
    
    # Use AI-powered healing if enabled, otherwise fall back to basic fixes
    if use_ai:
        res = sh.apply_ai_healing(
            repo,
            job_id=job_id,
            groq_api_key=groq_api_key,
            model=model,
            max_files=50,  # Limit to small projects for now
            use_ai=True,
            auto_apply=auto_apply,
        )
    else:
        res = sh.apply_basic_fixes(repo, format_only=format_only, job_id=job_id)
    
    emit_log(job_id, f"Applied fixes: {', '.join(res.get('applied', []))}")
    try:
        from celery import current_task
        job_store.publish_update({
            "type": "job_update",
            "job": {"id": current_task.request.id, "state": "SUCCESS", "result": res}
        })
    except Exception:
        pass
    return res


@shared_task(name="tasks.stage_validate")
def stage_validate(repo_path: str, compose_path: str | None, service: str | None, health_url: str | None, timeout: int = 120):
    job_id = get_job_id() or "unknown"
    repo = _validated_repo(repo_path)
    emit_log(job_id, f"Starting staging validation for {repo_path}")
    if not repo.exists():
        emit_error(job_id, f"Repo path not found: {repo}")
        raise FileNotFoundError(f"Repo path not found: {repo}")
    result = staging.validate_with_compose(repo, compose_path=compose_path, service=service, health_url=health_url, timeout=timeout, job_id=job_id)
    if result.get("status") == "ok":
        emit_log(job_id, f"Staging validation successful: {result.get('project', 'N/A')}")
    else:
        emit_error(job_id, f"Staging validation failed: {result.get('reason', 'Unknown error')}")
    try:
        from celery import current_task
        job_store.publish_update({
            "type": "job_update",
            "job": {"id": current_task.request.id, "state": "SUCCESS", "result": result}
        })
    except Exception:
        pass
    return result


