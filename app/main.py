from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os
from celery.result import AsyncResult
from . import job_store
import asyncio

# Lazy import to avoid Celery initialization on import in non-worker contexts
from .celery_app import app as celery_app


class DocJob(BaseModel):
    repo_path: str
    use_llm: bool | None = False
    template: str | None = None
    export_formats: list[str] | None = None
    manual_override: bool | None = False


class TestJob(BaseModel):
    repo_path: str


class HealJob(BaseModel):
    repo_path: str
    format_only: bool | None = True


class StageJob(BaseModel):
    repo_path: str
    compose_path: str | None = None
    service: str | None = None
    health_url: str | None = None
    timeout: int | None = 120


app = FastAPI(title="Autovate Agentic DevOps API")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


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
    job_store.add_job({
        "id": task.id,
        "type": "doc",
        "repo_path": payload.repo_path,
        "ts": __import__("time").time(),
        "state": "PENDING",
    })
    from .log_helper import emit_log
    emit_log(task.id, "Documentation generation job created", "INFO")
    return {"job_id": task.id}


@app.post("/jobs/tests/generate-run")
def job_tests(payload: TestJob):
    task = celery_app.send_task("tasks.generate_and_run_tests", args=[payload.repo_path])
    job_store.add_job({
        "id": task.id,
        "type": "tests",
        "repo_path": payload.repo_path,
        "ts": __import__("time").time(),
        "state": "PENDING",
    })
    from .log_helper import emit_log
    emit_log(task.id, "Test generation job created", "INFO")
    return {"job_id": task.id}


@app.post("/jobs/self-heal")
def job_self_heal(payload: HealJob):
    task = celery_app.send_task(
        "tasks.self_heal", args=[payload.repo_path, bool(payload.format_only)]
    )
    job_store.add_job({
        "id": task.id,
        "type": "self_heal",
        "repo_path": payload.repo_path,
        "ts": __import__("time").time(),
        "state": "PENDING",
    })
    from .log_helper import emit_log
    emit_log(task.id, "Self-heal job created", "INFO")
    return {"job_id": task.id}


@app.post("/jobs/stage/validate")
def job_stage(payload: StageJob):
    task = celery_app.send_task(
        "tasks.stage_validate",
        args=[
            payload.repo_path,
            payload.compose_path,
            payload.service,
            payload.health_url,
            int(payload.timeout or 120),
        ],
    )
    job_store.add_job({
        "id": task.id,
        "type": "stage",
        "repo_path": payload.repo_path,
        "ts": __import__("time").time(),
        "state": "PENDING",
    })
    from .log_helper import emit_log
    emit_log(task.id, "Staging validation job created", "INFO")
    return {"job_id": task.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    res = AsyncResult(job_id, app=celery_app)
    data = {
        "id": job_id,
        "state": res.state,
        "successful": res.successful() if res.state == "SUCCESS" else False,
    }
    if res.state == "FAILURE":
        data["error"] = str(res.result)
    elif res.state == "SUCCESS":
        data["result"] = res.result
    return data


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/jobs")
def list_jobs(limit: int = 50):
    jobs = job_store.list_jobs(limit=limit)
    # hydrate current states
    out = []
    for j in jobs:
        res = AsyncResult(j["id"], app=celery_app)
        out.append({
            **j,
            "state": res.state,
            "successful": res.successful() if res.state == "SUCCESS" else False,
        })
    return {"items": out}


@app.websocket("/ws")
async def ws_updates(ws: WebSocket):
    await ws.accept()
    # send initial snapshot
    initial = job_store.list_jobs(limit=50)
    await ws.send_json({"type": "jobs", "items": initial})

    ps = job_store.get_pubsub()
    try:
        while True:
            msg = ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("type") == "message":
                data = msg.get("data")
                try:
                    import json as _json
                    payload = _json.loads(data)
                except Exception:
                    payload = {"type": "unknown"}
                await ws.send_json(payload)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        try:
            ps.close()
        except Exception:
            pass
        return


