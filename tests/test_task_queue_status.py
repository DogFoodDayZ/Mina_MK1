import sys
from types import SimpleNamespace

sys.path.insert(0, 'e:/Mina_MK1')

import mk1_api


class _EmptyQueueMemory:
    def get_task_memories(self, top_k=10, include_done=False):
        return []


class _BrokenMemory:
    def get_task_memories(self, top_k=10, include_done=False):
        raise RuntimeError("db unavailable")


def test_task_queue_status_available_when_queue_is_empty(monkeypatch):
    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=_EmptyQueueMemory()))

    result = mk1_api._task_queue_status(limit=5)

    assert result["available"] is True
    assert result["queued_count"] == 0
    assert result["error"] is None


def test_task_queue_status_unavailable_on_lookup_error(monkeypatch):
    monkeypatch.setattr(mk1_api, "core", SimpleNamespace(memory=_BrokenMemory()))

    result = mk1_api._task_queue_status(limit=5)

    assert result["available"] is False
    assert result["error"]
