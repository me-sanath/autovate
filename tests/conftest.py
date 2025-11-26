from __future__ import annotations

import importlib
import types
from pathlib import Path
from typing import Iterator, List, Tuple

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Tuple[TestClient, Path, List[dict]]]:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setenv("AUTOVATE_ALLOWED_REPOS", str(tmp_path))
    monkeypatch.setenv("AUTOVATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOVATE_API_KEYS", "test-key")

    module = importlib.import_module("app.main")
    importlib.reload(module)

    sent: List[dict] = []

    def fake_send_task(name, args=None, kwargs=None):
        task_id = f"task-{len(sent)}"
        sent.append({"name": name, "args": args, "kwargs": kwargs})
        return types.SimpleNamespace(id=task_id)

    monkeypatch.setattr(module.celery_app, "send_task", fake_send_task)
    monkeypatch.setattr(module.job_store, "add_job", lambda job: None)

    client = TestClient(module.app)
    yield client, repo, sent

