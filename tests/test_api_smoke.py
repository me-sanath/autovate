from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(api_client):
    client, _repo, _sent = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_doc_job_requires_auth(api_client):
    client, repo, sent = api_client

    resp = client.post(
        "/jobs/doc",
        json={
            "repo_path": str(repo),
            "use_llm": False,
            "template": "api",
            "export_formats": ["md"],
            "manual_override": False,
        },
    )
    assert resp.status_code == 401

    resp = client.post(
        "/jobs/doc",
        headers={"X-API-Key": "test-key"},
        json={
            "repo_path": str(repo),
            "use_llm": False,
            "template": "api",
            "export_formats": ["md"],
            "manual_override": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"].startswith("task-")
    assert sent[0]["name"] == "tasks.generate_docs"
    assert sent[0]["args"][0] == str(repo)


def test_doc_job_rejects_outside_repo(api_client, tmp_path):
    client, _repo, _sent = api_client
    rogue = tmp_path / "other" / "project"
    rogue.mkdir(parents=True, exist_ok=True)

    resp = client.post(
        "/jobs/doc",
        headers={"X-API-Key": "test-key"},
        json={"repo_path": str(rogue)},
    )
    assert resp.status_code == 400
    assert "outside of allowed roots" in resp.json()["detail"]

