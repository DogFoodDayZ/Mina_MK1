import os
import tempfile
import time

from memory.mk1_memory import MK1Memory


def _build_mem_stub() -> MK1Memory:
    mem = MK1Memory.__new__(MK1Memory)
    mem.backup_keep_hourly_hours = 48
    mem.backup_keep_daily_days = 30
    mem.backup_keep_weekly_weeks = 26
    mem.backup_max_total = 120
    mem.backup_min_interval_seconds = 0
    mem.status = {
        "last_backup": None,
    }
    mem._last_backup_signature = None
    mem._set_error = lambda *_args, **_kwargs: None
    return mem


def test_backup_retention_honors_max_total():
    mem = _build_mem_stub()
    mem.backup_keep_hourly_hours = 1
    mem.backup_keep_daily_days = 2
    mem.backup_keep_weekly_weeks = 2
    mem.backup_max_total = 3

    now = time.time()
    backups = [
        {"path": "b0", "ts": now},
        {"path": "b1", "ts": now - 1800},
        {"path": "b2", "ts": now - 7200},
        {"path": "b3", "ts": now - 86400},
        {"path": "b4", "ts": now - 10 * 86400},
    ]

    keep, delete_paths = mem._plan_backup_retention(backups, now_ts=now)

    assert "b0" in keep
    assert len(keep) <= 3
    assert set(delete_paths) == {b["path"] for b in backups if b["path"] not in keep}


def test_backup_skips_unchanged_snapshot():
    mem = _build_mem_stub()

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "memory.db")
        small = os.path.join(td, "faiss_small.index")
        base = os.path.join(td, "faiss_base.index")
        backup_dir = os.path.join(td, "backups")

        for path, content in [
            (db, b"db-data"),
            (small, b"small-index"),
            (base, b"base-index"),
        ]:
            with open(path, "wb") as f:
                f.write(content)

        os.makedirs(backup_dir, exist_ok=True)

        mem.db_path = db
        mem.faiss_small_path = small
        mem.faiss_base_path = base
        mem.backup_dir = backup_dir

        first = mem.backup(reason="test_first")
        second = mem.backup(reason="test_second")

        assert first is not None
        assert os.path.isdir(first)
        assert second is None
