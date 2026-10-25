"""Memory tools must use the running core's memory, never a separate default database.

Transcript: "By the way, my sister Dana lives in Tucson and she's a welder." Mina called
memory_write, which opened its own MK1Memory and stored the fact in
E:\\Mina_MK1\\memory\\memory.db instead of the memory the conversation was using.
"""

from pathlib import Path

from tools.tool_loader import ToolLoader


class RecordingMemory:
    def __init__(self):
        self.added = []

    def find_memory_id_by_text(self, *args, **kwargs):
        return None

    def add_memory(self, text, kind="fact", tags=None):
        self.added.append((text, kind, list(tags or [])))
        return 41


def test_memory_write_uses_loader_memory(monkeypatch):
    import memory.mk1_memory as mk1_memory

    def forbidden(*args, **kwargs):
        raise AssertionError("memory_write opened a separate default MK1Memory")

    monkeypatch.setattr(mk1_memory, "MK1Memory", forbidden)
    loader = ToolLoader(str(Path(__file__).resolve().parents[1] / "tools"))
    live = RecordingMemory()
    loader.memory = live

    args = {"text": "Travis's sister Dana lives in Tucson and is a welder", "kind": "fact"}
    result = loader.run("memory_write", args)

    assert result == {"ok": True, "id": 41, "stored": args["text"]}
    assert live.added == [(args["text"], "fact", [])]
    assert "_memory" not in args
