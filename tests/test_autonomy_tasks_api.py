from types import SimpleNamespace

from fastapi.testclient import TestClient

import agent.server.mk1_api as mk1_api


class _AutonomyCore:
    def __init__(self):
        self.added = []

    def _load_autonomy_approved_tasks(self):
        return list(self.added)

    def _add_autonomy_approved_task(self, **task):
        entry = {"id": "task-1", "key": "approved:task-1", "status": "pending", **task}
        self.added.append(entry)
        return {"ok": True, "task": entry}


def test_autonomy_task_api_adds_and_lists_structured_task(monkeypatch):
    core = _AutonomyCore()
    monkeypatch.setattr(mk1_api, "core", core)
    client = TestClient(mk1_api.app)
    payload = {
        "title": "Verify status route",
        "proof_target": "GET /status",
        "allowed_action": "read_only",
        "done_when": "The response reports core_online.",
    }

    created = client.post("/autonomy/tasks", json=payload)
    listed = client.get("/autonomy/tasks")

    assert created.status_code == 200
    assert created.json()["task"]["title"] == payload["title"]
    assert listed.json()["tasks"][0]["proof_target"] == "GET /status"