import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agent.server.mk1_api as mk1_api


class _NoDueAlarmMemory:
    def get_due_tasks(self, top_k=25, include_tags=None):
        return []


class _DueAlarmMemory:
    def get_due_tasks(self, top_k=25, include_tags=None):
        assert top_k == 25
        assert include_tags == ["scheduled_task"]
        return [{
            "id": 41,
            "text": "Alarm: endpoint verification",
            "due_at": 1.0,
            "tags": ["scheduled_task", "alarm_task", "beep"],
        }]


class _CreateTaskMemory:
    def __init__(self):
        self.calls = []

    def add_task_memory(self, text, due_at=None, tags=None, status="pending"):
        self.calls.append({"text": text, "due_at": due_at, "tags": tags, "status": status})
        return 73


def test_ring_due_alarm_reports_no_due_alarm(monkeypatch):
    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=_NoDueAlarmMemory()))
    client = TestClient(mk1_api.app)

    response = client.post("/tasks/ring_due_alarm")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["played"] is False
    assert response.json()["reason"] == "no_due_alarm"


def test_ring_due_alarm_plays_first_due_alarm(monkeypatch):
    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=_DueAlarmMemory()))
    monkeypatch.setattr(mk1_api, "_play_alarm_beep_async", lambda repeats: {
        "ok": True,
        "started": True,
        "repeats": repeats,
    })
    client = TestClient(mk1_api.app)

    response = client.post("/tasks/ring_due_alarm?repeats=1")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["played"] is True
    assert response.json()["task_id"] == 41
    assert response.json()["text"] == "Alarm: endpoint verification"


def test_create_task_uses_scheduled_task_memory_with_repeat_and_alarm_tags(monkeypatch):
    memory = _CreateTaskMemory()
    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=memory))
    client = TestClient(mk1_api.app)

    response = client.post("/tasks/create", json={
        "text": "Check the build",
        "due_at": 4_102_444_800,
        "repeat_seconds": 600,
        "alarm": True,
    })

    assert response.status_code == 200
    assert response.json()["task"]["id"] == 73
    assert memory.calls[0]["status"] == "pending"
    assert memory.calls[0]["tags"] == ["user_memory", "reminder_task", "recurring_task", "repeat_600s", "alarm_task", "beep"]


def test_create_task_generates_windowed_recurring_series(monkeypatch):
    calls = []

    class Memory:
        def add_task_memory(self, text, due_at=None, tags=None, status="pending"):
            calls.append({"text": text, "due_at": due_at, "tags": tags, "status": status})
            return len(calls)

    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=Memory()))
    client = TestClient(mk1_api.app)

    start_ts = time.time() + 60
    end_ts = start_ts + 50 * 60
    response = client.post("/tasks/create", json={
        "text": "Drink water",
        "due_at": start_ts,
        "window_end": end_ts,
        "repeat_minutes": 25,
        "alarm": True,
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 3
    assert [item["due_at"] for item in calls] == [start_ts, start_ts + 25 * 60, end_ts]
    assert payload["task"]["text"] == "Drink water"


def test_alarm_beep_falls_back_to_system_alert(monkeypatch):
    events = []

    class FakeWinsound:
        MB_ICONEXCLAMATION = 0x30
        SND_ALIAS = 0x00010000
        SND_ASYNC = 0x0001

        @staticmethod
        def Beep(*args, **kwargs):
            raise RuntimeError("tone unavailable")

        @staticmethod
        def MessageBeep(flag):
            events.append(flag)

        @staticmethod
        def PlaySound(*args, **kwargs):
            events.append("playsound")

    monkeypatch.setattr(mk1_api, "winsound", FakeWinsound)
    mk1_api._play_alarm_beep_async(repeats=2)

    assert events and events[0] == FakeWinsound.MB_ICONEXCLAMATION