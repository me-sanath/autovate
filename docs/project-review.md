# Autovate Project Review (Nov 26, 2025)

## Basic Fixes Applied
- Refactored `git_module.py` into a reusable CLI/utility so it no longer hard-codes `demo-repo` or executes on import. The tool now accepts `--repo/--commit/--parent` arguments and can emit JSON for downstream pipelines.

## Attention Areas & Implementation Needs

### 1. Backend API Trust Boundaries
```53:113:app/main.py
@app.post("/jobs/doc")
def job_doc(payload: DocJob):
    task = celery_app.send_task(
        "tasks.generate_docs",
        args=[
            payload.repo_path,
            bool(payload.use_llm),
            payload.template,
            payload.export_formats or None,
            bool(payload.manual_override),
        ],
    )
```
- Any caller can queue jobs that operate on arbitrary paths on the worker host. There is no allow-listing, sandboxing, or path normalization, so a malicious user could point to `/` or sensitive repos. Implement repo validation plus an execution jail (e.g., workspace root mapping, per-tenant credentials).
- None of the POST endpoints require authentication or rate limiting. If this API is exposed beyond localhost, add auth middleware and basic abuse protection.

### 2. Doc Generation LLM Flow
```452:472:app/docgen.py
        llm_res = langraph.call_groq_api(
            prompt,
            os.environ.get("GROQ_API_KEY"),
            model=os.environ.get("GROQ_MODEL", "groq-1"),
            endpoint=os.environ.get("GROQ_ENDPOINT"),
        )
        
        if llm_res.get("status") == "ok":
            try:
                response_text = json.dumps(llm_res.get("response", {}))
                json_match = re.search(r"\{[\s\S]*\}", response_text)
                if json_match:
                    parsed = json.loads(json_match.group(0))
```
- The response string is wrapped in `json.dumps` and then parsed via regex, which corrupts valid JSON (quotes are escaped) and quietly drops structured outputs. Replace this with direct parsing plus schema validation, and plumb LLM errors back to the caller so failed generations are visible.
- `_generate_llm_docs_for_missing` fires one request per file with no batching/quotas and no retries/backoff. Add rate limiting, streaming logs, and guardrails so cancellations propagate to the Celery task.

### 3. Staging Validation UX
```23:33:app/staging.py
    cpath = Path(compose_path) if compose_path else (repo / "docker-compose.yml")
    if not cpath.exists():
        if (repo / "Dockerfile").exists():
            ...
        return {"status": "skipped", "reason": "No docker-compose.yml or Dockerfile found"}
```
- When the dashboard supplies `compose_path`, it is interpreted relative to the worker’s CWD instead of the repository. This breaks for repo-relative paths and risks reading host files. Resolve against `repo` and explicitly forbid paths escaping the repo root.
- Staging always shells out to `docker compose` with host Docker access, yet the worker container does not mount the Docker socket by default (commented out in `docker-compose.yml`). Document the requirement or add pre-flight checks with actionable errors.

### 4. AI Self-Heal Safety Net
```647:665:app/self_heal.py
                file_path = root / fix['file']
                if file_path.exists():
                    content = read_text_file(file_path)
                    ...
                    file_path.write_text(new_content, encoding='utf-8')
```
- AI replacements are written directly to disk without diff reviews, backups, or formatting verification. Capture patches to `.autovate/patches`, require user approval for destructive writes, and run quick syntax/pytest checks before marking tasks successful.
- There is no cap on cumulative changes per run beyond `max_files`. Add guardrails (max byte diff, skip binary files, enforce git-clean working directory) so automatic heals do not corrupt repositories.

### 5. Testing & Observability Gaps
```65:68:Makefile
.PHONY: test
test:
	@command -v pytest >/dev/null 2>&1 && pytest -q || echo "pytest not installed; skipping tests"
```
- There are zero committed tests for the API, Celery tasks, or langraph helpers; the Makefile target simply runs pytest if available. Add smoke tests for `/health` and job lifecycle plus unit tests for generators (docgen/testgen) so regressions are caught outside production runs.
- Job logs are only ephemeral WebSocket pushes backed by Redis pub/sub. Persist structured job/task logs (job id, step, severity) to disk or an observability backend so operators can audit historical runs.

## Suggested Next Steps
1. **Lock down job inputs** with repo allow-lists and authentication before exposing the API.
2. **Stabilize docgen + self-heal** by fixing JSON parsing, adding retries/backoffs, and creating a transparent approval flow for AI edits.
3. **Improve operational ergonomics**: mount Docker in the worker when staging is enabled, persist job logs/results, and add a small pytest suite to guard critical flows.

