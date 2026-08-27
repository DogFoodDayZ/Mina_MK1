import json
import sqlite3
import time

import numpy as np

from memory.mk1_memory import MK1Memory


def _make_memory(tmp_path, rows, kinds=None):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT, tags TEXT, "
        "timestamp REAL, due_at REAL, task_status TEXT, "
        "emb_small BLOB, dim_small INTEGER, emb_base BLOB, dim_base INTEGER)"
    )
    kinds = kinds or ["fact"] * len(rows)
    insert_rows = [(kind,) + row for kind, row in zip(kinds, rows)]
    conn.executemany(
        "INSERT INTO memories(kind, text, tags, timestamp, emb_small, dim_small, emb_base, dim_base) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )
    conn.commit()
    conn.close()

    mem = MK1Memory.__new__(MK1Memory)
    mem.db_path = str(db_path)
    mem.faiss_small_path = str(tmp_path / "small.index")
    mem.faiss_base_path = str(tmp_path / "base.index")
    mem.small_index = None
    mem.base_index = None
    mem.status = {}
    mem._errors = []
    mem._set_error = lambda message: mem._errors.append(message)
    return mem


def _vec_blob(dim, fill=0.1):
    return np.full((dim,), fill, dtype=np.float32).tobytes()


def test_rebuild_indexes_skips_corrupt_zero_dim_rows_without_crashing(tmp_path):
    rows = [
        ("good one", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
        ("corrupt zero-dim", json.dumps(["user_memory"]), time.time(), b"", 0, b"", 0),
        ("good two", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
    ]
    mem = _make_memory(tmp_path, rows)

    small, base = mem._rebuild_indexes()

    assert small is not None
    assert base is not None
    assert small.ntotal == 2
    assert base.ntotal == 2
    assert mem._errors == []


def test_delete_memory_ids_reports_true_count_even_if_rebuild_fails(tmp_path, monkeypatch):
    rows = [
        ("keep me", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
        ("delete me", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
    ]
    mem = _make_memory(tmp_path, rows)

    def boom():
        raise RuntimeError("simulated faiss failure")

    monkeypatch.setattr(mem, "_rebuild_indexes", boom)

    conn = sqlite3.connect(mem.db_path)
    target_id = conn.execute("SELECT id FROM memories WHERE text = 'delete me'").fetchone()[0]
    conn.close()

    deleted = mem.delete_memory_ids([target_id])

    assert deleted == 1
    assert any("rebuild_error" in message for message in mem._errors)

    conn = sqlite3.connect(mem.db_path)
    remaining = conn.execute("SELECT id FROM memories").fetchall()
    conn.close()
    assert (target_id,) not in remaining


def test_trim_by_age_reports_true_count_even_if_rebuild_fails(tmp_path, monkeypatch):
    old_ts = time.time() - 1000
    rows = [
        ("old one", json.dumps(["user_memory"]), old_ts, _vec_blob(4), 4, _vec_blob(8), 8),
        ("recent one", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
    ]
    mem = _make_memory(tmp_path, rows)
    monkeypatch.setattr(mem, "_rebuild_indexes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    deleted = mem.trim_by_age(max_age_seconds=500)

    assert deleted == 1
    assert any("trim_by_age_rebuild_error" in message for message in mem._errors)


def test_trim_by_size_reports_true_count_even_if_rebuild_fails(tmp_path, monkeypatch):
    rows = [
        (f"row {i}", json.dumps(["user_memory"]), time.time() + i, _vec_blob(4), 4, _vec_blob(8), 8)
        for i in range(5)
    ]
    mem = _make_memory(tmp_path, rows)
    monkeypatch.setattr(mem, "_rebuild_indexes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    deleted = mem.trim_by_size(max_rows=3)

    assert deleted == 2
    assert any("trim_by_size_rebuild_error" in message for message in mem._errors)


def test_memory_hygiene_reports_true_result_even_if_rebuild_fails(tmp_path, monkeypatch):
    rows = [
        ("what is my name?", json.dumps([]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
        ("kept fact", json.dumps(["user_memory"]), time.time(), _vec_blob(4), 4, _vec_blob(8), 8),
    ]
    mem = _make_memory(tmp_path, rows, kinds=["interaction", "fact"])
    mem.backup = lambda reason="hygiene", force=False: "backup-path"
    monkeypatch.setattr(mem, "_rebuild_indexes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    result = mem.memory_hygiene(dry_run=False, max_delete=500, authorized_apply=True)

    assert result["ok"] is True
    assert result["delete_count"] == 1
    assert any("memory_hygiene_rebuild_error" in message for message in mem._errors)

