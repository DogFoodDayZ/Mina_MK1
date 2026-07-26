import threading
import time

from agent.core import MK1Core
from memory.mk1_memory import MK1Memory


def make_memory() -> MK1Memory:
    memory = MK1Memory.__new__(MK1Memory)
    memory._init_operation_tracking()
    return memory


def test_quiesce_waits_for_active_operation_and_rejects_new_work():
    memory = make_memory()
    started = threading.Event()
    release = threading.Event()

    def hold_operation():
        assert memory._begin_memory_operation("held_write") is True
        started.set()
        release.wait(timeout=2)
        memory._end_memory_operation("held_write")

    worker = threading.Thread(target=hold_operation)
    worker.start()
    assert started.wait(timeout=1)

    status = memory.begin_shutdown_quiesce()
    assert status["active_operations"] == 1
    assert memory._begin_memory_operation("late_write") is False
    assert memory.wait_for_memory_idle(timeout_seconds=0.01) is False

    release.set()
    assert memory.wait_for_memory_idle(timeout_seconds=1) is True
    worker.join(timeout=1)
    assert memory.memory_activity_status()["active_operations"] == 0


def test_shutdown_gate_waits_until_memory_is_idle():
    memory = make_memory()
    core = MK1Core.__new__(MK1Core)
    core.memory = memory
    started = threading.Event()
    release = threading.Event()

    def hold_operation():
        assert memory._begin_memory_operation("retention_lifecycle") is True
        started.set()
        release.wait(timeout=2)
        memory._end_memory_operation("retention_lifecycle")

    worker = threading.Thread(target=hold_operation)
    worker.start()
    assert started.wait(timeout=1)

    releaser = threading.Thread(target=lambda: (time.sleep(0.05), release.set()))
    releaser.start()
    result = core._prepare_safe_shutdown(max_wait_seconds=2, poll_seconds=1)

    worker.join(timeout=1)
    releaser.join(timeout=1)
    assert result["ok"] is True
    assert result["result"]["memory_idle"] is True
    assert result["result"]["active_operations"] == 0
    assert result["result"]["quiescing"] is True


def test_force_cannot_interrupt_active_memory_operation():
    memory = make_memory()
    core = MK1Core.__new__(MK1Core)
    core.memory = memory
    assert memory._begin_memory_operation("faiss_rebuild") is True

    try:
        result = core._prepare_safe_shutdown(force=True, max_wait_seconds=0)
    finally:
        memory._end_memory_operation("faiss_rebuild")

    assert result["ok"] is False
    assert result["error"] == "active_memory_operations_not_complete"
    assert result["result"]["forced"] is False
    assert memory.memory_activity_status()["quiescing"] is False