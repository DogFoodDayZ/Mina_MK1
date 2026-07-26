import json
from pathlib import Path

from agent import core as core_module


class _RuntimeStateCore:
    _save_runtime_state = core_module.MK1Core._save_runtime_state

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self._turn_clock_started_at = "2026-07-25T06:00:00"
        self._turn_clock_tick = 1
        self._turn_clock_last_tick_at = "2026-07-25T06:00:05"
        self._turn_clock_last_elapsed_seconds = 5.0
        self._last_user_activity_at = "2026-07-25T06:00:00"
        self._last_shutdown_at = None
        self._idle_checkin_pending = False
        self._idle_checkin_due_since_at = None
        self._runtime_state_last_written_at = None

    def _runtime_state_path(self) -> str:
        return str(self.state_path)


def test_runtime_state_write_failure_preserves_last_valid_file(tmp_path, monkeypatch):
    state_path = tmp_path / ".mina_runtime_state.json"
    original = {"last_user_activity_at": "2026-07-25T05:00:00"}
    state_path.write_text(json.dumps(original), encoding="utf-8")

    def fail_during_dump(payload, file_obj, **kwargs):
        file_obj.write('{"updated_at":')
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(core_module.json, "dump", fail_during_dump)
    monkeypatch.setattr(core_module.traceback, "print_exc", lambda: None)

    fake = _RuntimeStateCore(state_path)
    fake._save_runtime_state(reason="heartbeat")

    assert json.loads(state_path.read_text(encoding="utf-8")) == original
    assert list(tmp_path.glob("*.tmp")) == []
    assert fake._runtime_state_last_written_at is None