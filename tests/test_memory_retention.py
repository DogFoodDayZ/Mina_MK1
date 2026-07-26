import json
import sqlite3
import time

from memory.mk1_memory import MK1Memory
from memory.retention import plan_conversation_retention


DAY = 86400.0
NOW = 200 * DAY


def row(memory_id, age_days, tags=None, text="turn"):
    return {
        "id": memory_id,
        "text": text,
        "kind": "interaction",
        "tags": tags or ["short_term"],
        "timestamp": NOW - age_days * DAY,
    }


def test_retention_preserves_recent_and_durable_interactions():
    plan = plan_conversation_retention(
        [
            row(1, 10),
            row(2, 120, ["short_term", "routing_correction"]),
            row(3, 120, ["short_term", "user_memory"]),
        ],
        now_ts=NOW,
        interaction_retention_days=90,
        failed_decision_retention_days=14,
    )

    assert plan["delete_ids"] == []
    assert plan["recent_ids"] == [1]
    assert plan["preserved_ids"] == [2, 3]


def test_retention_groups_old_conversation_by_day_for_digest():
    plan = plan_conversation_retention(
        [row(10, 120, text="user turn"), row(11, 120, text="assistant turn")],
        now_ts=NOW,
        interaction_retention_days=90,
        failed_decision_retention_days=14,
    )

    assert plan["regular_ids"] == [10, 11]
    assert len(plan["regular_by_day"]) == 1


def test_failed_decisions_use_shorter_retention():
    plan = plan_conversation_retention(
        [row(20, 15, ["short_term", "failed_decision"]), row(21, 13, ["short_term", "failed_decision"])],
        now_ts=NOW,
        interaction_retention_days=90,
        failed_decision_retention_days=14,
    )

    assert plan["failed_ids"] == [20]
    assert plan["recent_ids"] == [21]


def test_legacy_untagged_refusal_uses_failed_decision_retention():
    plan = plan_conversation_retention(
        [row(30, 15, ["short_term", "assistant_turn"], "I cannot send texts from this setup yet.")],
        now_ts=NOW,
        interaction_retention_days=90,
        failed_decision_retention_days=14,
    )

    assert plan["failed_ids"] == [30]


def test_retention_apply_creates_digest_before_deleting(tmp_path):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT, tags TEXT, timestamp REAL)"
    )
    now = time.time()
    rows = [
        ("old user turn", "interaction", json.dumps(["short_term", "user_turn"]), now - 120 * DAY),
        ("old assistant turn", "interaction", json.dumps(["short_term", "assistant_turn"]), now - 120 * DAY),
        ("old bad route", "interaction", json.dumps(["short_term", "assistant_turn", "failed_decision"]), now - 20 * DAY),
        ("recent user turn", "interaction", json.dumps(["short_term", "user_turn"]), now - 2 * DAY),
    ]
    conn.executemany("INSERT INTO memories(text, kind, tags, timestamp) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    mem = MK1Memory.__new__(MK1Memory)
    mem.db_path = str(db_path)
    mem.interaction_retention_days = 90
    mem.failed_decision_retention_days = 14
    mem.retention_max_delete = 1000
    mem.status = {}
    mem._set_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    backups = []
    mem.backup = lambda reason, force=False: backups.append((reason, force)) or "backup-path"
    mem._rebuild_indexes = lambda: (None, None)

    def add_memory(text, kind="fact", tags=None, **_kwargs):
        write_conn = sqlite3.connect(db_path)
        cur = write_conn.cursor()
        cur.execute(
            "INSERT INTO memories(text, kind, tags, timestamp) VALUES (?, ?, ?, ?)",
            (text, kind, json.dumps(tags or []), now),
        )
        memory_id = cur.lastrowid
        write_conn.commit()
        write_conn.close()
        return memory_id

    mem.add_memory = add_memory

    report = mem.run_retention_lifecycle(dry_run=False, now_ts=now)

    assert report["ok"] is True
    assert report["deleted"] == 3
    assert report["digests_created"]
    assert backups == [("retention_lifecycle", True)]

    check_conn = sqlite3.connect(db_path)
    remaining = check_conn.execute("SELECT text, kind, tags FROM memories ORDER BY id").fetchall()
    check_conn.close()
    assert any(text == "recent user turn" for text, _kind, _tags in remaining)
    assert any("Conversation digest" in text and kind == "procedure" for text, kind, _tags in remaining)
    assert not any(text == "old bad route" for text, _kind, _tags in remaining)