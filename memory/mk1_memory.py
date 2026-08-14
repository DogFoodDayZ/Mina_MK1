import os
import sqlite3
import time
import json
import shutil
import hashlib
import re
import threading
from functools import wraps
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import faiss
import requests

from memory.retention import plan_conversation_retention


ROOT = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = ROOT

DB_PATH_DEFAULT = os.path.join(MEMORY_DIR, "memory.db")
FAISS_SMALL_PATH_DEFAULT = os.path.join(MEMORY_DIR, "faiss_small.index")
FAISS_BASE_PATH_DEFAULT = os.path.join(MEMORY_DIR, "faiss_base.index")
BACKUP_DIR_DEFAULT = os.path.join(MEMORY_DIR, "backups")


def _tracked_memory_operation(name: str, allow_while_quiescing: bool = False, rejected: Any = None):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self._begin_memory_operation(name, allow_while_quiescing=allow_while_quiescing):
                return rejected(self, *args, **kwargs) if callable(rejected) else rejected

            self._memory_operation_serial_lock.acquire()
            try:
                return func(self, *args, **kwargs)
            finally:
                self._memory_operation_serial_lock.release()
                self._end_memory_operation(name)

        return wrapper

    return decorator


class MK1Memory:
    """
    Hybrid MK1 memory engine:
    - Dual index (small + base)
    - SQLite + FAISS
    - Trimming, backups, maintenance, verification
    """

    def __init__(
        self,
        db_path: str = DB_PATH_DEFAULT,
        faiss_small_path: str = FAISS_SMALL_PATH_DEFAULT,
        faiss_base_path: str = FAISS_BASE_PATH_DEFAULT,
        backup_dir: str = BACKUP_DIR_DEFAULT,
        embed_small_url: str = "http://127.0.0.1:8084/embed_small",
        embed_base_url: str = "http://127.0.0.1:8084/embed_base",
        obsidian_vault: str = "",
        # maintenance / backup knobs
        auto_backup_every_writes: int = 100,
        auto_backup_every_seconds: int = 3600,
        backup_min_interval_seconds: int = 300,
        backup_keep_hourly_hours: int = 48,
        backup_keep_daily_days: int = 30,
        backup_keep_weekly_weeks: int = 26,
        backup_max_total: int = 120,
        trim_max_rows: Optional[int] = None,
        trim_max_age_seconds: Optional[int] = None,
        retention_enabled: bool = True,
        interaction_retention_days: int = 90,
        failed_decision_retention_days: int = 14,
        retention_interval_seconds: int = 86400,
        retention_max_delete: int = 1000,
    ):
        self.db_path = db_path
        self.faiss_small_path = faiss_small_path
        self.faiss_base_path = faiss_base_path
        self.backup_dir = backup_dir

        self.embed_small_url = embed_small_url
        self.embed_base_url = embed_base_url
        self.embed_max_chars = max(256, int(os.getenv("MK1_EMBED_MAX_CHARS", "4000") or 4000))
        self.buffer_warn_threshold = max(1, int(os.getenv("MK1_BUFFER_WARN_THRESHOLD", "25") or 25))
        require_embed_env = os.getenv("MK1_REQUIRE_EMBED", "1").strip().lower()
        self.require_embed_startup = require_embed_env not in {"0", "false", "no", "off"}

        self.status: Dict[str, Any] = {
            "ok": True,
            "db_ok": True,
            "faiss_small_ok": True,
            "faiss_base_ok": True,
            "embed_small_ok": True,
            "embed_base_ok": True,
            "last_error": None,
            "last_error_time": None,
            "last_backup": None,
            "last_rebuild": None,
            "last_maintenance": None,
            "buffer_pending_count": 0,
            "buffer_last_enqueue": None,
            "buffer_last_flush": None,
            "buffer_last_flush_result": None,
            "buffer_warning": False,
        }

        # maintenance / backup config
        self.auto_backup_every_writes = auto_backup_every_writes
        self.auto_backup_every_seconds = auto_backup_every_seconds
        self.backup_min_interval_seconds = backup_min_interval_seconds
        self.backup_keep_hourly_hours = backup_keep_hourly_hours
        self.backup_keep_daily_days = backup_keep_daily_days
        self.backup_keep_weekly_weeks = backup_keep_weekly_weeks
        self.backup_max_total = backup_max_total
        self.trim_max_rows = trim_max_rows
        self.trim_max_age_seconds = trim_max_age_seconds
        self.retention_enabled = bool(retention_enabled)
        self.interaction_retention_days = max(1, int(interaction_retention_days))
        self.failed_decision_retention_days = max(1, int(failed_decision_retention_days))
        self.retention_interval_seconds = max(3600, int(retention_interval_seconds))
        self.retention_max_delete = max(1, int(retention_max_delete))
        self._last_retention_maintenance = 0.0

        self._write_counter = 0
        self._init_operation_tracking()

        # Obsidian vault export path — writes a .md copy of every memory
        obsidian_env = os.getenv("MK1_OBSIDIAN_VAULT", "").strip()
        self.obsidian_vault: Optional[str] = (obsidian_vault.strip() or obsidian_env) or None

        for path in [
            os.path.dirname(self.db_path),
            os.path.dirname(self.faiss_small_path),
            os.path.dirname(self.faiss_base_path),
            self.backup_dir,
        ]:
            if path and not os.path.exists(path):
                os.makedirs(path, exist_ok=True)

        self._last_backup_signature: Optional[str] = None
        self._load_last_backup_signature()

        self.small_index: Optional[faiss.IndexIDMap2] = None
        self.base_index: Optional[faiss.IndexIDMap2] = None
        self._init_indexes()

    def _obsidian_export(self, mem_id: int, text: str, kind: str, tags: List[str], timestamp: float) -> None:
        """Write memory to Obsidian vault as a markdown file. Fire-and-forget."""
        vault = self.obsidian_vault
        if not vault:
            return
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            month_dir = os.path.join(vault, "mina-memory", dt.strftime("%Y-%m"))
            os.makedirs(month_dir, exist_ok=True)
            filename = f"memory-{mem_id}.md"
            filepath = os.path.join(month_dir, filename)
            tag_str = " ".join(f"#{t}" for t in (tags or []))
            frontmatter = (
                f"---\n"
                f"id: {mem_id}\n"
                f"kind: {kind}\n"
                f"tags: [{', '.join(tags or [])}]\n"
                f"timestamp: {dt.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                f"---\n\n"
            )
            body = f"{frontmatter}{text}\n"
            if tag_str:
                body += f"\n{tag_str}\n"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(body)
        except Exception:
            pass  # never let vault export break memory writes

    def _sqlite_keyword_search(
        self,
        query: str,
        top_k: int,
        include_kinds: Optional[List[str]],
        include_tags: Optional[List[str]],
        since_ts: Optional[float],
    ) -> List[Dict[str, Any]]:
        """Keyword fallback when FAISS/embed unavailable. Ranks by token overlap."""
        stop = {"a","an","and","are","as","at","be","by","do","for","from",
                "how","i","in","is","it","me","my","of","on","or","so",
                "that","the","to","was","what","when","who","you","your"}
        tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if t not in stop and len(t) > 1]
        if not tokens:
            tokens = [query.lower().strip()]

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            sql = "SELECT id, text, kind, tags, timestamp FROM memories WHERE 1=1"
            params: List[Any] = []
            if since_ts is not None:
                sql += " AND timestamp >= ?"
                params.append(since_ts)
            sql += " ORDER BY timestamp DESC LIMIT 500"
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return []

        scored = []
        for row in rows:
            rid, text, kind, tags_raw, ts = row
            row_tags = json.loads(tags_raw) if tags_raw else []
            if not self._matches_filters(kind, row_tags, ts, include_kinds, include_tags, since_ts):
                continue
            text_lower = (text or "").lower()
            score = sum(1 for t in tokens if t in text_lower)
            if score > 0:
                scored.append((score, {"id": rid, "text": text, "kind": kind, "tags": row_tags, "timestamp": ts}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def _init_indexes(self) -> None:
        try:
            self._validate_embed_startup()
            self._init_db()
            self.small_index, self.base_index = self._load_or_rebuild_indexes()
        except Exception as e:
            self._set_error(f"init_error: {e}")
            self.small_index = None
            self.base_index = None
            if self.require_embed_startup:
                raise

        print(">>> MK1Memory online")

    # ================= STATUS / ERROR =================

    def _init_operation_tracking(self) -> None:
        self._memory_operation_condition = threading.Condition(threading.RLock())
        self._memory_operation_serial_lock = threading.RLock()
        self._memory_operation_local = threading.local()
        self._memory_active_operations = 0
        self._memory_active_by_name: Dict[str, int] = {}
        self._memory_quiescing = False
        self._memory_closing = False
        self._memory_closed = False

    def _ensure_operation_tracking(self) -> None:
        if not hasattr(self, "_memory_operation_condition"):
            self._init_operation_tracking()

    def _begin_memory_operation(
        self,
        name: str,
        allow_while_quiescing: bool = False,
        internal_close: bool = False,
    ) -> bool:
        self._ensure_operation_tracking()
        depth = int(getattr(self._memory_operation_local, "depth", 0) or 0)
        with self._memory_operation_condition:
            if depth <= 0:
                if self._memory_closed:
                    return False
                if self._memory_closing and not internal_close:
                    return False
                if self._memory_quiescing and not allow_while_quiescing and not internal_close:
                    return False

            self._memory_active_operations += 1
            self._memory_active_by_name[name] = self._memory_active_by_name.get(name, 0) + 1
            self._memory_operation_local.depth = depth + 1
            return True

    def _end_memory_operation(self, name: str) -> None:
        self._ensure_operation_tracking()
        with self._memory_operation_condition:
            depth = int(getattr(self._memory_operation_local, "depth", 1) or 1)
            self._memory_operation_local.depth = max(0, depth - 1)
            self._memory_active_operations = max(0, self._memory_active_operations - 1)
            remaining = max(0, self._memory_active_by_name.get(name, 1) - 1)
            if remaining:
                self._memory_active_by_name[name] = remaining
            else:
                self._memory_active_by_name.pop(name, None)
            self._memory_operation_condition.notify_all()

    def begin_shutdown_quiesce(self) -> Dict[str, Any]:
        self._ensure_operation_tracking()
        with self._memory_operation_condition:
            self._memory_quiescing = True
            return self.memory_activity_status()

    def resume_memory_operations(self) -> Dict[str, Any]:
        self._ensure_operation_tracking()
        with self._memory_operation_condition:
            if not self._memory_closing and not self._memory_closed:
                self._memory_quiescing = False
            self._memory_operation_condition.notify_all()
            return self.memory_activity_status()

    def wait_for_memory_idle(self, timeout_seconds: float) -> bool:
        self._ensure_operation_tracking()
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._memory_operation_condition:
            while self._memory_active_operations > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._memory_operation_condition.wait(timeout=remaining)
            return True

    def memory_activity_status(self) -> Dict[str, Any]:
        self._ensure_operation_tracking()
        with self._memory_operation_condition:
            return {
                "active_operations": int(self._memory_active_operations),
                "active_by_name": dict(self._memory_active_by_name),
                "quiescing": bool(self._memory_quiescing),
                "closing": bool(self._memory_closing),
                "closed": bool(self._memory_closed),
            }

    def _set_error(self, msg: str):
        print(">>> MK1Memory ERROR:", msg)
        self.status["ok"] = False
        self.status["last_error"] = msg
        self.status["last_error_time"] = time.time()
        # error‑based backup (best effort, non‑fatal)
        self._safe_error_backup()

    def _set_ok_flag(self, key: str, value: bool):
        self.status[key] = value
        ok_flags = [
            self.status.get("db_ok", True),
            self.status.get("faiss_small_ok", True),
            self.status.get("faiss_base_ok", True),
            self.status.get("embed_small_ok", True),
            self.status.get("embed_base_ok", True),
        ]
        self.status["ok"] = all(ok_flags)

    def get_status(self) -> Dict[str, Any]:
        return dict(self.status)

    def verify_integrity(self) -> Dict[str, Any]:
        """
        Lightweight verification hook for core.py.
        """
        report: Dict[str, Any] = {
            "db_exists": os.path.exists(self.db_path),
            "faiss_small_exists": os.path.exists(self.faiss_small_path),
            "faiss_base_exists": os.path.exists(self.faiss_base_path),
            "small_index_loaded": self.small_index is not None,
            "base_index_loaded": self.base_index is not None,
            "small_index_ntotal": int(self.small_index.ntotal) if self.small_index else 0,
            "base_index_ntotal": int(self.base_index.ntotal) if self.base_index else 0,
            "status": dict(self.status),
        }
        return report

    # ================= EMBEDDING =================

    def _derive_health_url(self, endpoint_url: str) -> str:
        parts = endpoint_url.rsplit("/", 1)
        if len(parts) == 2:
            return f"{parts[0]}/health"
        return endpoint_url

    def _validate_embed_startup(self) -> None:
        """
        Ensure embed service is reachable during startup so failures are surfaced early.
        """
        health_url = self._derive_health_url(self.embed_small_url)

        try:
            health_resp = requests.get(health_url, timeout=5)
            if health_resp.status_code < 400:
                payload = health_resp.json() if health_resp.content else {}
                if isinstance(payload, dict) and payload.get("ok"):
                    self._set_ok_flag("embed_small_ok", True)
                    self._set_ok_flag("embed_base_ok", True)
                    return
        except Exception:
            # Fall back to endpoint probes below.
            pass

        failures: List[str] = []
        probes = [
            (self.embed_small_url, "embed_small_ok"),
            (self.embed_base_url, "embed_base_ok"),
        ]

        for url, status_key in probes:
            try:
                resp = requests.post(url, json={"text": "startup probe"}, timeout=10)
                if resp.status_code >= 400:
                    self._set_ok_flag(status_key, False)
                    failures.append(f"{url} -> http_{resp.status_code}")
                    continue

                data = resp.json()
                vec = data.get("embedding") if isinstance(data, dict) else None
                if not vec:
                    self._set_ok_flag(status_key, False)
                    failures.append(f"{url} -> invalid_response")
                    continue

                self._set_ok_flag(status_key, True)
            except Exception as e:
                self._set_ok_flag(status_key, False)
                failures.append(f"{url} -> {e}")

        if failures:
            raise RuntimeError("embed_startup_check_failed: " + "; ".join(failures))

    def _embed(self, text: str, use_base: bool = False) -> List[float]:
        url = self.embed_base_url if use_base else self.embed_small_url
        status_key = "embed_base_ok" if use_base else "embed_small_ok"

        raw = str(text or "")
        # Protect embed server from very large payloads (e.g., long tool/list outputs).
        # Keep the head/tail to preserve most identifying context.
        if len(raw) > self.embed_max_chars:
            head = max(64, self.embed_max_chars // 2)
            tail = max(64, self.embed_max_chars - head - 32)
            raw = raw[:head] + "\n...[truncated for embedding]...\n" + raw[-tail:]

        payload_text = raw.strip()
        if not payload_text:
            msg = "embed_empty_text"
            self._set_error(msg)
            self._set_ok_flag(status_key, False)
            return []

        try:
            resp = requests.post(url, json={"text": payload_text}, timeout=30)
            if resp.status_code >= 400:
                msg = f"embed_http_{resp.status_code}"
                self._set_error(msg)
                self._set_ok_flag(status_key, False)
                return []

            data = resp.json()
            vec = data.get("embedding")
            if not vec:
                msg = "embed_invalid_response"
                self._set_error(msg)
                self._set_ok_flag(status_key, False)
                return []

            self._set_ok_flag(status_key, True)
            return vec

        except Exception as e:
            msg = f"embed_exception: {e}"
            self._set_error(msg)
            self._set_ok_flag(status_key, False)
            return []

    # ================= SQLITE INIT =================

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            kind TEXT NOT NULL,
            tags TEXT,
            timestamp REAL NOT NULL,
            due_at REAL,
            task_status TEXT NOT NULL DEFAULT 'stored',
            emb_small BLOB NOT NULL,
            dim_small INTEGER NOT NULL,
            emb_base BLOB NOT NULL,
            dim_base INTEGER NOT NULL
        );
        """)

        # Lightweight migration for older DBs.
        cur.execute("PRAGMA table_info(memories)")
        columns = {str(row[1]) for row in cur.fetchall()}
        if "due_at" not in columns:
            cur.execute("ALTER TABLE memories ADD COLUMN due_at REAL")
        if "task_status" not in columns:
            cur.execute("ALTER TABLE memories ADD COLUMN task_status TEXT NOT NULL DEFAULT 'stored'")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            kind TEXT NOT NULL,
            tags TEXT,
            due_at REAL,
            task_status TEXT NOT NULL DEFAULT 'stored',
            created_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        """)

        conn.commit()
        conn.close()
        self._set_ok_flag("db_ok", True)
        self.status["buffer_pending_count"] = self._pending_buffer_count()

    def _pending_buffer_count(self) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM pending_memories")
            row = cur.fetchone()
            conn.close()
            return int((row or [0])[0] or 0)
        except Exception:
            return 0

    def _refresh_buffer_health_flags(self) -> None:
        pending = self._pending_buffer_count()
        self.status["buffer_pending_count"] = pending
        self.status["buffer_warning"] = pending >= int(self.buffer_warn_threshold)

    def _enqueue_pending_memory(
        self,
        text: str,
        kind: str,
        tags: List[str],
        due_at: Optional[float],
        task_status: str,
        last_error: str,
    ) -> Optional[int]:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO pending_memories (text, kind, tags, due_at, task_status, created_at, attempts, last_error)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    text,
                    kind,
                    json.dumps(tags or []),
                    due_at,
                    str(task_status or "stored"),
                    time.time(),
                    str(last_error or "embed_failed"),
                ),
            )
            conn.commit()
            pending_id = cur.lastrowid
            conn.close()
            self._refresh_buffer_health_flags()
            self.status["buffer_last_enqueue"] = time.time()
            if self.status.get("buffer_warning"):
                print(
                    f">>> MK1Memory WARNING: pending buffer high "
                    f"({self.status.get('buffer_pending_count')} >= {self.buffer_warn_threshold})"
                )
            return int(pending_id) if pending_id is not None else None
        except Exception as e:
            self._set_error(f"pending_enqueue_error: {e}")
            return None

    def _insert_memory_with_vectors(
        self,
        text: str,
        kind: str,
        tags: List[str],
        due_at: Optional[float],
        task_status: str,
        vec_small: List[float],
        vec_base: List[float],
    ) -> Optional[int]:
        vs = np.array(vec_small, dtype=np.float32)
        vb = np.array(vec_base, dtype=np.float32)

        dim_small = len(vs)
        dim_base = len(vb)

        if self.small_index is None or self.small_index.d != dim_small:
            self.small_index = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_small))

        if self.base_index is None or self.base_index.d != dim_base:
            self.base_index = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_base))

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO memories (text, kind, tags, timestamp, due_at, task_status, emb_small, dim_small, emb_base, dim_base)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            text,
            kind,
            json.dumps(tags or []),
            time.time(),
            due_at,
            str(task_status or "stored"),
            vs.tobytes(),
            len(vs),
            vb.tobytes(),
            len(vb),
        ))

        conn.commit()
        mem_id = cur.lastrowid
        conn.close()

        self.small_index.add_with_ids(np.array([vs]), np.array([mem_id], dtype=np.int64))
        self.base_index.add_with_ids(np.array([vb]), np.array([mem_id], dtype=np.int64))

        faiss.write_index(self.small_index, self.faiss_small_path)
        faiss.write_index(self.base_index, self.faiss_base_path)

        self._write_counter += 1
        if (
            self.auto_backup_every_writes is not None
            and self._write_counter >= self.auto_backup_every_writes
        ):
            self.backup(reason="scheduled_writes")
            self._write_counter = 0

        # Export to Obsidian vault if configured
        self._obsidian_export(
            mem_id=int(mem_id),
            text=text,
            kind=kind,
            tags=tags,
            timestamp=time.time(),
        )

        return int(mem_id) if mem_id is not None else None

    def _flush_pending_memories(self, max_items: int = 20) -> Dict[str, int]:
        flushed = 0
        failed = 0

        try:
            read_conn = sqlite3.connect(self.db_path)
            read_cur = read_conn.cursor()
            read_cur.execute(
                """
                SELECT id, text, kind, tags, due_at, task_status, attempts
                FROM pending_memories
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max(1, int(max_items)),),
            )
            rows = read_cur.fetchall()
            read_conn.close()

            if not rows:
                self._refresh_buffer_health_flags()
                self.status["buffer_last_flush_result"] = {"flushed": 0, "failed": 0}
                return {"flushed": 0, "failed": 0}

            for row in rows:
                pid = int(row[0])
                text = str(row[1] or "")
                kind = str(row[2] or "fact")
                tags = json.loads(row[3]) if row[3] else []
                due_at = row[4]
                task_status = str(row[5] or "stored")
                attempts = int(row[6] or 0)

                vec_small = self._embed(text, use_base=False)
                vec_base = self._embed(text, use_base=True)

                if not vec_small or not vec_base:
                    # Permanently discard items that will never embed (e.g. empty text)
                    last_error = str(self.status.get("last_error") or "embed_failed")
                    if last_error == "embed_empty_text" or not str(text).strip():
                        write_conn = sqlite3.connect(self.db_path)
                        write_cur = write_conn.cursor()
                        write_cur.execute("DELETE FROM pending_memories WHERE id = ?", (pid,))
                        write_conn.commit()
                        write_conn.close()
                        continue
                    failed += 1
                    write_conn = sqlite3.connect(self.db_path)
                    write_cur = write_conn.cursor()
                    write_cur.execute(
                        "UPDATE pending_memories SET attempts = ?, last_error = ? WHERE id = ?",
                        (attempts + 1, str(self.status.get("last_error") or "embed_failed"), pid),
                    )
                    write_conn.commit()
                    write_conn.close()
                    # If embedding service is currently down, stop early and retry later.
                    if not (self.status.get("embed_small_ok", True) and self.status.get("embed_base_ok", True)):
                        break
                    continue

                mem_id = self._insert_memory_with_vectors(
                    text=text,
                    kind=kind,
                    tags=tags if isinstance(tags, list) else [],
                    due_at=due_at,
                    task_status=task_status,
                    vec_small=vec_small,
                    vec_base=vec_base,
                )

                if mem_id is None:
                    failed += 1
                    write_conn = sqlite3.connect(self.db_path)
                    write_cur = write_conn.cursor()
                    write_cur.execute(
                        "UPDATE pending_memories SET attempts = ?, last_error = ? WHERE id = ?",
                        (attempts + 1, str(self.status.get("last_error") or "insert_failed"), pid),
                    )
                    write_conn.commit()
                    write_conn.close()
                    continue

                write_conn = sqlite3.connect(self.db_path)
                write_cur = write_conn.cursor()
                write_cur.execute("DELETE FROM pending_memories WHERE id = ?", (pid,))
                write_conn.commit()
                write_conn.close()
                flushed += 1

            self._refresh_buffer_health_flags()
            self.status["buffer_last_flush"] = time.time()
            self.status["buffer_last_flush_result"] = {"flushed": int(flushed), "failed": int(failed)}
            if flushed > 0 or failed > 0:
                print(f">>> MK1Memory buffer flush: flushed={flushed} failed={failed} pending={self.status.get('buffer_pending_count')}")
            return {"flushed": flushed, "failed": failed}
        except Exception as e:
            self._set_error(f"pending_flush_error: {e}")
            self._refresh_buffer_health_flags()
            self.status["buffer_last_flush_result"] = {"flushed": int(flushed), "failed": int(failed + 1)}
            return {"flushed": flushed, "failed": failed + 1}

    @_tracked_memory_operation(
        "buffer_flush",
        allow_while_quiescing=True,
        rejected=lambda self, *args, **kwargs: {
            "ok": False,
            "result": None,
            "error": "memory_closing",
        },
    )
    def flush_pending_memories(self, max_items: int = 200) -> Dict[str, Any]:
        result = self._flush_pending_memories(max_items=max_items)
        return {
            "ok": True,
            "result": {
                "flushed": int(result.get("flushed") or 0),
                "failed": int(result.get("failed") or 0),
                "pending": int(self.status.get("buffer_pending_count") or 0),
                "warning": bool(self.status.get("buffer_warning", False)),
            },
            "error": None,
        }

    def buffer_status(self) -> Dict[str, Any]:
        self._refresh_buffer_health_flags()
        return {
            "ok": True,
            "result": {
                "pending": int(self.status.get("buffer_pending_count") or 0),
                "warning": bool(self.status.get("buffer_warning", False)),
                "warn_threshold": int(self.buffer_warn_threshold),
                "last_enqueue": self.status.get("buffer_last_enqueue"),
                "last_flush": self.status.get("buffer_last_flush"),
                "last_flush_result": self.status.get("buffer_last_flush_result"),
            },
            "error": None,
        }

    # ================= FAISS LOAD / REBUILD =================

    def _load_or_rebuild_indexes(self) -> Tuple[Optional[faiss.IndexIDMap2], Optional[faiss.IndexIDMap2]]:
        small: Optional[faiss.Index] = None
        base: Optional[faiss.Index] = None

        if os.path.exists(self.faiss_small_path):
            try:
                small = faiss.read_index(self.faiss_small_path)
                if not isinstance(small, faiss.IndexIDMap2):
                    small = faiss.IndexIDMap2(small)
                self._set_ok_flag("faiss_small_ok", True)
            except Exception as e:
                self._set_error(f"faiss_small_load_error: {e}")

        if os.path.exists(self.faiss_base_path):
            try:
                base = faiss.read_index(self.faiss_base_path)
                if not isinstance(base, faiss.IndexIDMap2):
                    base = faiss.IndexIDMap2(base)
                self._set_ok_flag("faiss_base_ok", True)
            except Exception as e:
                self._set_error(f"faiss_base_load_error: {e}")

        if small is None or base is None:
            small, base = self._rebuild_indexes()

        return small, base  # type: ignore[return-value]

    @_tracked_memory_operation(
        "faiss_rebuild",
        rejected=lambda self, *args, **kwargs: (self.small_index, self.base_index),
    )
    def _rebuild_indexes(self) -> Tuple[Optional[faiss.IndexIDMap2], Optional[faiss.IndexIDMap2]]:
        print(">>> Rebuilding FAISS indexes…")

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, emb_small, dim_small, emb_base, dim_base FROM memories")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            # no data yet
            self.status["last_rebuild"] = time.time()
            return None, None

        # Why: a handful of rows can carry corrupt/zero-length embeddings (e.g. inserted
        # by an older seed path that bypassed the embed pipeline). Blindly trusting the
        # first row's declared dimension crashed the whole rebuild on any such row with a
        # near-empty FAISS error, which silently swallowed unrelated delete/retention
        # results that call this function afterward.
        valid_rows = [row for row in rows if int(row[2] or 0) > 0 and int(row[4] or 0) > 0]
        skipped_ids = [row[0] for row in rows if row not in valid_rows]

        if not valid_rows:
            self.status["last_rebuild"] = time.time()
            if skipped_ids:
                self._set_error(f"rebuild_indexes_skipped_all_rows_no_valid_embeddings:{skipped_ids[:20]}")
            return None, None

        dim_small = valid_rows[0][2]
        dim_base = valid_rows[0][4]

        small = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_small))
        base = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_base))

        for mem_id, emb_s, row_dim_small, emb_b, row_dim_base in valid_rows:
            vs = np.frombuffer(emb_s, dtype=np.float32)
            vb = np.frombuffer(emb_b, dtype=np.float32)
            if len(vs) != dim_small or len(vb) != dim_base:
                skipped_ids.append(mem_id)
                continue
            small.add_with_ids(np.array([vs]), np.array([mem_id], dtype=np.int64))
            base.add_with_ids(np.array([vb]), np.array([mem_id], dtype=np.int64))

        if skipped_ids:
            print(f">>> Rebuild skipped {len(skipped_ids)} row(s) with corrupt/mismatched embeddings: {skipped_ids[:20]}")

        faiss.write_index(small, self.faiss_small_path)
        faiss.write_index(base, self.faiss_base_path)

        self.status["last_rebuild"] = time.time()
        self._set_ok_flag("faiss_small_ok", True)
        self._set_ok_flag("faiss_base_ok", True)
        return small, base

    # ================= BACKUP =================

    def _backup_sources(self) -> List[str]:
        return [self.db_path, self.faiss_small_path, self.faiss_base_path]

    def _backup_name_to_ts(self, name: str) -> Optional[float]:
        if not name.startswith("backup_"):
            return None
        stamp = name[len("backup_"):]
        try:
            dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
            return dt.timestamp()
        except Exception:
            return None

    def _list_backup_dirs(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(self.backup_dir):
            return out

        for name in os.listdir(self.backup_dir):
            full = os.path.join(self.backup_dir, name)
            if not os.path.isdir(full):
                continue

            ts = self._backup_name_to_ts(name)
            if ts is None:
                try:
                    ts = os.path.getmtime(full)
                except Exception:
                    ts = 0.0

            out.append({
                "name": name,
                "path": full,
                "ts": float(ts or 0.0),
            })

        out.sort(key=lambda x: x["ts"], reverse=True)
        return out

    def _current_backup_signature(self) -> Tuple[str, Dict[str, Any]]:
        files_meta: List[Dict[str, Any]] = []

        for src in self._backup_sources():
            if not os.path.exists(src):
                continue

            try:
                st = os.stat(src)
                files_meta.append({
                    "name": os.path.basename(src),
                    "path": src,
                    "size": int(st.st_size),
                    "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
                })
            except Exception:
                continue

        files_meta.sort(key=lambda x: x["name"])
        canonical = json.dumps(files_meta, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
        return digest, {"files": files_meta}

    def _read_backup_manifest(self, backup_path: str) -> Dict[str, Any]:
        path = os.path.join(backup_path, "manifest.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _load_last_backup_signature(self) -> None:
        try:
            backups = self._list_backup_dirs()
            if not backups:
                self._last_backup_signature = None
                return

            latest = backups[0]["path"]
            manifest = self._read_backup_manifest(latest)
            sig = manifest.get("signature")
            if isinstance(sig, str) and sig:
                self._last_backup_signature = sig
                return

            self._last_backup_signature = None
        except Exception:
            self._last_backup_signature = None

    def _plan_backup_retention(
        self,
        backups: List[Dict[str, Any]],
        now_ts: Optional[float] = None,
    ) -> Tuple[set, List[str]]:
        if now_ts is None:
            now_ts = time.time()

        keep = set()
        seen_hour = set()
        seen_day = set()
        seen_week = set()

        for idx, b in enumerate(backups):
            path = b["path"]
            bts = float(b.get("ts") or 0.0)

            if idx == 0:
                keep.add(path)
                continue

            age_hours = max(0.0, (now_ts - bts) / 3600.0)
            dt = datetime.fromtimestamp(bts) if bts > 0 else datetime.fromtimestamp(0)

            if age_hours <= float(self.backup_keep_hourly_hours):
                key = dt.strftime("%Y%m%d%H")
                if key not in seen_hour:
                    seen_hour.add(key)
                    keep.add(path)
                continue

            if age_hours <= float(self.backup_keep_daily_days * 24):
                key = dt.strftime("%Y%m%d")
                if key not in seen_day:
                    seen_day.add(key)
                    keep.add(path)
                continue

            week = dt.isocalendar()
            key = f"{week[0]}-{week[1]}"
            if key not in seen_week and len(seen_week) < int(self.backup_keep_weekly_weeks):
                seen_week.add(key)
                keep.add(path)

        max_total = max(1, int(self.backup_max_total))
        if len(keep) > max_total:
            keep_sorted = sorted(keep, key=lambda p: next((x["ts"] for x in backups if x["path"] == p), 0.0), reverse=True)
            keep = set(keep_sorted[:max_total])

        delete_paths = [b["path"] for b in backups if b["path"] not in keep]
        return keep, delete_paths

    def _prune_backups(self) -> int:
        backups = self._list_backup_dirs()
        if not backups:
            return 0

        _, delete_paths = self._plan_backup_retention(backups)
        deleted = 0
        for path in delete_paths:
            try:
                shutil.rmtree(path)
                deleted += 1
            except Exception as e:
                self._set_error(f"backup_prune_error: {e}")
        return deleted

    @_tracked_memory_operation("backup", allow_while_quiescing=True)
    def backup(self, reason: str = "manual", force: bool = False) -> Optional[str]:
        now = time.time()

        if not force:
            last = self.status.get("last_backup")
            if (
                self.backup_min_interval_seconds is not None
                and last is not None
                and (now - float(last)) < float(self.backup_min_interval_seconds)
            ):
                return None

        signature, signature_meta = self._current_backup_signature()
        if not force and self._last_backup_signature and signature == self._last_backup_signature:
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"backup_{ts}")
        try:
            os.makedirs(backup_path, exist_ok=True)
            for src in [self.db_path, self.faiss_small_path, self.faiss_base_path]:
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(backup_path, os.path.basename(src)))

            manifest = {
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "reason": reason,
                "signature": signature,
                "sources": signature_meta.get("files", []),
            }
            with open(os.path.join(backup_path, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=True)

            self.status["last_backup"] = time.time()
            self._last_backup_signature = signature
            self._prune_backups()
            print(f">>> MK1Memory backup created at {backup_path}")
            return backup_path
        except Exception as e:
            self._set_error(f"backup_error: {e}")
            return None

    def _safe_error_backup(self):
        """
        Best-effort backup on serious errors; never raises.
        """
        try:
            # throttle: don't spam backups
            last = self.status.get("last_backup")
            if last and (time.time() - last) < 300:
                return
            self.backup(reason="error")
        except Exception:
            pass

    # ================= TRIMMING =================

    @_tracked_memory_operation("trim_by_age", rejected=0)
    def trim_by_age(self, max_age_seconds: float) -> int:
        cutoff = time.time() - max_age_seconds
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM memories WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            # rebuild indexes after trim
            self.small_index, self.base_index = self._rebuild_indexes()
            return deleted
        except Exception as e:
            self._set_error(f"trim_by_age_error: {e}")
            return 0

    @_tracked_memory_operation("trim_by_size", rejected=0)
    def trim_by_size(self, max_rows: int) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM memories")
            total = cur.fetchone()[0]
            if total <= max_rows:
                conn.close()
                return 0

            to_delete = total - max_rows
            cur.execute("""
                DELETE FROM memories
                WHERE id IN (
                    SELECT id FROM memories
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
            """, (to_delete,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            self.small_index, self.base_index = self._rebuild_indexes()
            return deleted
        except Exception as e:
            self._set_error(f"trim_by_size_error: {e}")
            return 0

    @_tracked_memory_operation(
        "memory_hygiene",
        rejected=lambda self, *args, **kwargs: {"ok": False, "error": "memory_quiescing"},
    )
    def memory_hygiene(self, dry_run: bool = False, max_delete: int = 500) -> Dict[str, Any]:
        """
        Conservative memory cleanup pass:
        - keeps user-authored long-term facts/preferences/procedures
        - removes noisy interaction artifacts
        - deduplicates repeated interaction lines (keeps latest 2 copies)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC")
            rows = cur.fetchall()

            if not rows:
                conn.close()
                return {
                    "ok": True,
                    "dry_run": dry_run,
                    "total_rows": 0,
                    "delete_count": 0,
                    "delete_ids": [],
                    "samples": [],
                }

            def _norm_text(s: str) -> str:
                return " ".join((s or "").strip().lower().split())

            def _is_noise_interaction(text: str, kind: str, tags: List[str]) -> bool:
                t = (text or "").strip()
                low = t.lower()
                tag_set = {x.lower() for x in tags}

                if kind != "interaction":
                    return False

                # Keep explicit user-memory and durable fact/procedure lines.
                if "user_memory" in tag_set:
                    return False
                if "authoritative_identity" in tag_set or "authoritative_profile" in tag_set:
                    return False

                if not t:
                    return True

                noise_patterns = [
                    r"^mina-style output\s*:",
                    r"^mina-sign-off\s*:",
                    r"^verified output\s*:",
                    r"^stored memory\s*:",
                    r"^memory write (?:ok|failed)\s*:",
                    r"^```text$",
                    r"^```$",
                    r"^gremlin memory ping:\s*i do not have that in memory yet\.?$",
                    r"^gremlin memory check, incoming\.?",
                    r"^what is my name\??$",
                    r"^who am i\??$",
                    r"^what color are my eyes\??$",
                    r"^tell me about my eyes\.?$",
                    r"^can you relate my eye color to something else\.?$",
                ]

                return any(re.search(p, low) is not None for p in noise_patterns)

            delete_ids: List[int] = []
            samples: List[str] = []
            seen_interaction: Dict[str, int] = {}

            for row in rows:
                mem_id = int(row[0])
                text = row[1] or ""
                kind = row[2] or ""
                tags = json.loads(row[3]) if row[3] else []

                if _is_noise_interaction(text, kind, tags):
                    delete_ids.append(mem_id)
                    if len(samples) < 12:
                        samples.append(text[:180])
                    continue

                # Deduplicate interaction spam: keep latest 2 copies.
                if kind == "interaction":
                    key = _norm_text(text)
                    if key:
                        count = seen_interaction.get(key, 0)
                        if count >= 2:
                            delete_ids.append(mem_id)
                            if len(samples) < 12:
                                samples.append(text[:180])
                            continue
                        seen_interaction[key] = count + 1

                if len(delete_ids) >= max(1, int(max_delete)):
                    break

            delete_ids = delete_ids[:max(1, int(max_delete))]

            if not dry_run and delete_ids:
                # Best-effort backup before destructive maintenance.
                self.backup(reason="memory_hygiene", force=False)

                placeholders = ",".join(["?"] * len(delete_ids))
                cur.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", tuple(delete_ids))
                conn.commit()
                self.small_index, self.base_index = self._rebuild_indexes()

            conn.close()

            return {
                "ok": True,
                "dry_run": dry_run,
                "total_rows": len(rows),
                "delete_count": len(delete_ids),
                "delete_ids": delete_ids,
                "samples": samples,
            }

        except Exception as e:
            self._set_error(f"memory_hygiene_error: {e}")
            return {
                "ok": False,
                "dry_run": dry_run,
                "total_rows": 0,
                "delete_count": 0,
                "delete_ids": [],
                "samples": [],
                "error": str(e),
            }

    # ================= MAINTENANCE CYCLE =================

    def _build_conversation_digest(self, day: str, rows: List[Dict[str, Any]]) -> str:
        stop_words = {
            "about", "after", "again", "also", "because", "before", "could", "from",
            "have", "mina", "should", "that", "their", "there", "these", "they", "this",
            "through", "what", "when", "where", "which", "with", "would", "your",
        }
        counts: Dict[str, int] = {}
        highlights: List[str] = []

        for row in rows or []:
            text = " ".join(str(row.get("text") or "").split())
            if not text:
                continue
            tags = {str(tag or "").strip().lower() for tag in (row.get("tags") or [])}
            role = "User" if "user_turn" in tags else "Mina" if "assistant_turn" in tags else "Turn"
            if len(highlights) < 12:
                highlights.append(f"{role}: {text[:180]}")
            for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", text.lower()):
                token = token.strip("_-")
                if len(token) < 4 or token in stop_words or token.isdigit():
                    continue
                counts[token] = counts.get(token, 0) + 1

        topics = [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]]
        topic_text = ", ".join(topics) if topics else "general conversation"
        highlight_text = " | ".join(highlights)[:1500]
        return f"Conversation digest {day}. Topics: {topic_text}. Highlights: {highlight_text}".strip()

    @_tracked_memory_operation(
        "retention_lifecycle",
        rejected=lambda self, *args, **kwargs: {"ok": False, "error": "memory_quiescing"},
    )
    def run_retention_lifecycle(
        self,
        dry_run: bool = True,
        now_ts: Optional[float] = None,
        max_delete: Optional[int] = None,
    ) -> Dict[str, Any]:
        now_value = float(now_ts if now_ts is not None else time.time())
        delete_limit = max(1, int(max_delete if max_delete is not None else self.retention_max_delete))
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, text, kind, tags, timestamp FROM memories "
                "WHERE kind = 'interaction' ORDER BY timestamp ASC"
            )
            rows = []
            for memory_id, text, kind, tags_json, timestamp in cur.fetchall():
                try:
                    tags = json.loads(tags_json) if tags_json else []
                except Exception:
                    tags = []
                rows.append({
                    "id": int(memory_id),
                    "text": str(text or ""),
                    "kind": str(kind or "interaction"),
                    "tags": tags if isinstance(tags, list) else [],
                    "timestamp": float(timestamp or 0.0),
                })

            cur.execute("SELECT tags FROM memories WHERE tags LIKE '%conversation_digest%'")
            existing_digest_days = set()
            for (tags_json,) in cur.fetchall():
                try:
                    tags = json.loads(tags_json) if tags_json else []
                except Exception:
                    tags = []
                for tag in tags if isinstance(tags, list) else []:
                    tag_text = str(tag or "").strip().lower()
                    if tag_text.startswith("digest_day_"):
                        existing_digest_days.add(tag_text[len("digest_day_"):])
            conn.close()
            conn = None

            plan = plan_conversation_retention(
                rows,
                now_ts=now_value,
                interaction_retention_days=self.interaction_retention_days,
                failed_decision_retention_days=self.failed_decision_retention_days,
            )

            failed_ids = list(plan.get("failed_ids", []))[:delete_limit]
            remaining = max(0, delete_limit - len(failed_ids))
            selected_by_day: Dict[str, List[Dict[str, Any]]] = {}
            regular_ids: List[int] = []
            for day in sorted(plan.get("regular_by_day", {})):
                if remaining <= 0:
                    break
                day_rows = list(plan["regular_by_day"].get(day, []))
                selected_rows = day_rows[:remaining]
                if not selected_rows:
                    continue
                selected_by_day[day] = selected_rows
                ids = [int(row.get("id") or 0) for row in selected_rows if int(row.get("id") or 0) > 0]
                regular_ids.extend(ids)
                remaining -= len(ids)

            report = {
                "ok": True,
                "dry_run": bool(dry_run),
                "interaction_retention_days": self.interaction_retention_days,
                "failed_decision_retention_days": self.failed_decision_retention_days,
                "total_interactions": len(rows),
                "failed_decision_delete_count": len(failed_ids),
                "regular_delete_count": len(regular_ids),
                "delete_count": len(failed_ids) + len(regular_ids),
                "digest_days_needed": [day for day in selected_by_day if day not in existing_digest_days],
                "digest_days_existing": sorted(day for day in selected_by_day if day in existing_digest_days),
                "preserved_durable_count": len(plan.get("preserved_ids", [])),
                "recent_count": len(plan.get("recent_ids", [])),
                "deleted": 0,
                "digests_created": [],
            }
            if dry_run or report["delete_count"] <= 0:
                return report

            backup_path = self.backup(reason="retention_lifecycle", force=True)
            report["backup_path"] = backup_path
            digest_ready_days = set(existing_digest_days)
            for day, selected_rows in selected_by_day.items():
                if day in digest_ready_days:
                    continue
                digest_text = self._build_conversation_digest(day, selected_rows)
                digest_id = self.add_memory(
                    digest_text,
                    kind="procedure",
                    tags=["long_term", "conversation_digest", "summarized_history", f"digest_day_{day}"],
                )
                if digest_id is not None:
                    digest_ready_days.add(day)
                    report["digests_created"].append({"day": day, "id": digest_id})

            covered_regular_ids = [
                int(row.get("id") or 0)
                for day, selected_rows in selected_by_day.items()
                if day in digest_ready_days
                for row in selected_rows
                if int(row.get("id") or 0) > 0
            ]
            delete_ids = list(dict.fromkeys(failed_ids + covered_regular_ids))
            if delete_ids:
                conn = sqlite3.connect(self.db_path)
                cur = conn.cursor()
                placeholders = ",".join(["?"] * len(delete_ids))
                cur.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", tuple(delete_ids))
                report["deleted"] = int(cur.rowcount or 0)
                conn.commit()
                conn.close()
                conn = None
                self.small_index, self.base_index = self._rebuild_indexes()

            return report
        except Exception as e:
            if conn is not None:
                conn.close()
            self._set_error(f"retention_lifecycle_error: {e}")
            return {"ok": False, "dry_run": bool(dry_run), "error": str(e)}

    @_tracked_memory_operation("maintenance")
    def maintenance_tick(self):
        """
        Call this periodically from core.py (e.g. every N user turns).
        Handles:
        - scheduled backups
        - optional trimming
        - light health check
        """
        now = time.time()
        self.status["last_maintenance"] = now

        # Best-effort flush of buffered writes that previously failed embedding.
        self._flush_pending_memories(max_items=20)

        # scheduled backup by time
        last_backup = self.status.get("last_backup")
        if (
            self.auto_backup_every_seconds is not None
            and (last_backup is None or (now - last_backup) >= self.auto_backup_every_seconds)
        ):
            self.backup(reason="scheduled_time")

        # trimming by size
        if self.trim_max_rows is not None:
            self.trim_by_size(self.trim_max_rows)

        # trimming by age
        if self.trim_max_age_seconds is not None:
            self.trim_by_age(self.trim_max_age_seconds)

        if (
            self.retention_enabled
            and (now - float(self._last_retention_maintenance or 0.0)) >= float(self.retention_interval_seconds)
        ):
            self._last_retention_maintenance = now
            self.status["last_retention"] = self.run_retention_lifecycle(dry_run=False, now_ts=now)

    def close(self, flush_rounds: int = 3, flush_batch_size: int = 200) -> Dict[str, Any]:
        """
        Graceful shutdown hook for core: drain buffered pending memories and
        persist a final backup snapshot when possible.
        """
        self._ensure_operation_tracking()
        self.begin_shutdown_quiesce()
        if not self.wait_for_memory_idle(timeout_seconds=600.0):
            return {
                "ok": False,
                "result": self.memory_activity_status(),
                "error": "active_memory_operations_not_complete",
            }

        with self._memory_operation_condition:
            self._memory_closing = True

        if not self._begin_memory_operation("shutdown_close", internal_close=True):
            return {"ok": False, "result": self.memory_activity_status(), "error": "memory_close_not_started"}

        self._memory_operation_serial_lock.acquire()
        rounds = max(1, int(flush_rounds))
        batch = max(1, int(flush_batch_size))
        total_flushed = 0
        total_failed = 0
        try:
            for _ in range(rounds):
                result = self._flush_pending_memories(max_items=batch)
                flushed = int(result.get("flushed") or 0)
                failed = int(result.get("failed") or 0)
                total_flushed += flushed
                total_failed += failed

                pending = int(self.status.get("buffer_pending_count") or 0)
                if pending <= 0 or (flushed == 0 and failed == 0):
                    break

            backup_path = self.backup(reason="shutdown", force=True)
            with self._memory_operation_condition:
                self._memory_closed = True
            return {
                "ok": True,
                "result": {
                    "flushed": total_flushed,
                    "failed": total_failed,
                    "pending": int(self.status.get("buffer_pending_count") or 0),
                    "backup_path": backup_path,
                },
                "error": None,
            }
        finally:
            self._memory_operation_serial_lock.release()
            self._end_memory_operation("shutdown_close")

    # ================= ADD MEMORY =================

    @_tracked_memory_operation("add_memory")
    def add_memory(
        self,
        text: str,
        kind: str = "fact",
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[int]:

        if tags is None:
            tags = []

        # Silently discard empty text — embed server rejects it and fills the buffer
        if not str(text or "").strip():
            return None

        due_at = kwargs.get("due_at")
        task_status = str(kwargs.get("task_status") or "stored")

        vec_small = self._embed(text, use_base=False)
        vec_base = self._embed(text, use_base=True)

        if not vec_small or not vec_base:
            self._set_error("no_embedding_returned")
            self._enqueue_pending_memory(
                text=str(text or ""),
                kind=str(kind or "fact"),
                tags=tags,
                due_at=due_at if isinstance(due_at, (int, float)) else None,
                task_status=task_status,
                last_error=str(self.status.get("last_error") or "no_embedding_returned"),
            )
            return None

        mem_id = self._insert_memory_with_vectors(
            text=str(text or ""),
            kind=str(kind or "fact"),
            tags=tags,
            due_at=due_at if isinstance(due_at, (int, float)) else None,
            task_status=task_status,
            vec_small=vec_small,
            vec_base=vec_base,
        )

        # Opportunistically flush a few buffered items while embedding is healthy.
        self._flush_pending_memories(max_items=3)
        return mem_id

    @_tracked_memory_operation("add_time_passage_note")
    def add_time_passage_note(self, text: str, tags: Optional[List[str]] = None) -> Optional[int]:
        base_tags = ["system_seed", "time_tick"]
        if tags:
            for tag in tags:
                if tag and tag not in base_tags:
                    base_tags.append(tag)
        return self.add_memory(
            text,
            kind="procedure",
            tags=base_tags,
            task_status="stored",
        )

    @_tracked_memory_operation("add_task_memory")
    def add_task_memory(
        self,
        text: str,
        due_at: Optional[float] = None,
        tags: Optional[List[str]] = None,
        status: str = "pending",
    ) -> Optional[int]:
        task_tags = ["scheduled_task"]
        if tags:
            for tag in tags:
                if tag and tag not in task_tags:
                    task_tags.append(tag)
        return self.add_memory(
            text,
            kind="task",
            tags=task_tags,
            due_at=due_at,
            task_status=status,
        )

    @_tracked_memory_operation("get_due_tasks", rejected=lambda self, *args, **kwargs: [])
    def get_due_tasks(
        self,
        now_ts: Optional[float] = None,
        top_k: int = 10,
        include_tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        now_val = float(now_ts if now_ts is not None else time.time())
        safe_limit = max(1, int(top_k))

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, kind, tags, timestamp, due_at, task_status FROM memories WHERE kind = 'task' AND due_at IS NOT NULL AND due_at <= ? ORDER BY due_at ASC LIMIT ?",
            (now_val, safe_limit * 4),
        )
        rows = cur.fetchall()
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in rows:
            row_tags = json.loads(row[3]) if row[3] else []
            if include_tags and not any(tag in row_tags for tag in include_tags):
                continue
            if str(row[6] or "pending").lower() == "done":
                continue
            out.append({
                "id": row[0],
                "text": row[1],
                "kind": row[2],
                "tags": row_tags,
                "timestamp": row[4],
                "due_at": row[5],
                "task_status": row[6],
            })
            if len(out) >= safe_limit:
                break

        return out

    @_tracked_memory_operation("get_task_memories", rejected=lambda self, *args, **kwargs: [])
    def get_task_memories(
        self,
        top_k: int = 20,
        include_done: bool = False,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(top_k))

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, kind, tags, timestamp, due_at, task_status FROM memories WHERE kind = 'task' ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, timestamp DESC LIMIT ?",
            (safe_limit * 4,),
        )
        rows = cur.fetchall()
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in rows:
            status = str(row[6] or "pending").lower()
            if not include_done and status == "done":
                continue

            row_tags = json.loads(row[3]) if row[3] else []
            out.append({
                "id": row[0],
                "text": row[1],
                "kind": row[2],
                "tags": row_tags,
                "timestamp": row[4],
                "due_at": row[5],
                "task_status": status,
            })

            if len(out) >= safe_limit:
                break

        return out

    @_tracked_memory_operation("set_task_status", rejected=False)
    def set_task_status(self, task_id: int, status: str) -> bool:
        try:
            safe_status = str(status or "pending").strip().lower()
            if safe_status not in {"pending", "done", "cancelled"}:
                safe_status = "pending"

            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "UPDATE memories SET task_status = ?, timestamp = ? WHERE id = ? AND kind = 'task'",
                (safe_status, time.time(), int(task_id)),
            )
            conn.commit()
            updated = int(cur.rowcount or 0) > 0
            conn.close()
            return updated
        except Exception as e:
            self._set_error(f"set_task_status_error: {e}")
            return False

    @_tracked_memory_operation("snooze_task", rejected=False)
    def snooze_task(self, task_id: int, seconds: int) -> bool:
        try:
            delta = max(1, int(seconds))

            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT due_at FROM memories WHERE id = ? AND kind = 'task'",
                (int(task_id),),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return False

            current_due = row[0]
            base_due = float(current_due) if current_due is not None else time.time()
            new_due = max(base_due, time.time()) + float(delta)

            cur.execute(
                "UPDATE memories SET due_at = ?, task_status = 'pending', timestamp = ? WHERE id = ? AND kind = 'task'",
                (new_due, time.time(), int(task_id)),
            )
            conn.commit()
            updated = int(cur.rowcount or 0) > 0
            conn.close()
            return updated
        except Exception as e:
            self._set_error(f"snooze_task_error: {e}")
            return False

    # ================= SEARCH / CONTEXT =================

    def _matches_filters(
        self,
        kind: str,
        tags: List[str],
        timestamp: float,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        since_ts: Optional[float] = None,
    ) -> bool:
        if include_kinds is not None and kind not in include_kinds:
            return False

        if include_tags is not None:
            if not tags:
                return False
            if not any(tag in tags for tag in include_tags):
                return False

        if since_ts is not None and timestamp < since_ts:
            return False

        return True

    @_tracked_memory_operation("recent_memories", rejected=lambda self, *args, **kwargs: [])
    def recent_memories(
        self,
        top_k: int = 6,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        since_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC LIMIT ?",
            (max(top_k * 4, top_k),),
        )
        rows = cur.fetchall()
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in rows:
            row_tags = json.loads(row[3]) if row[3] else []
            if not self._matches_filters(
                kind=row[2],
                tags=row_tags,
                timestamp=row[4],
                include_kinds=include_kinds,
                include_tags=include_tags,
                since_ts=since_ts,
            ):
                continue

            out.append({
                "id": row[0],
                "text": row[1],
                "kind": row[2],
                "tags": row_tags,
                "timestamp": row[4],
            })

            if len(out) >= top_k:
                break

        return out

    def _normalize_text(self, text: str) -> str:
        return " ".join((text or "").strip().lower().split())

    def _is_promotable_text(self, text: str) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False

        lowered = cleaned.lower()

        # Do not promote question turns into long-term facts.
        if cleaned.endswith("?"):
            return False

        question_starts = (
            "what ",
            "why ",
            "how ",
            "when ",
            "where ",
            "who ",
            "which ",
            "is ",
            "are ",
            "am ",
            "can ",
            "could ",
            "do ",
            "did ",
            "does ",
            "will ",
            "would ",
            "should ",
        )
        if lowered.startswith(question_starts):
            return False

        # Avoid promoting assistant/tool boilerplate.
        blocked_phrases = (
            "stored memory:",
            "memory stored",
            "tool ",
            "mk1 process start",
            "user input:",
        )
        if any(p in lowered for p in blocked_phrases):
            return False

        return True

    def _exists_text(
        self,
        text: str,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
    ) -> bool:
        target = self._normalize_text(text)
        if not target:
            return False

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT text, kind, tags, timestamp FROM memories")
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            row_text = row[0] or ""
            row_kind = row[1]
            row_tags = json.loads(row[2]) if row[2] else []
            row_ts = float(row[3])

            if not self._matches_filters(
                kind=row_kind,
                tags=row_tags,
                timestamp=row_ts,
                include_kinds=include_kinds,
                include_tags=include_tags,
                since_ts=None,
            ):
                continue

            if self._normalize_text(row_text) == target:
                return True

        return False

    @_tracked_memory_operation("find_memory", rejected=None)
    def find_memory_id_by_text(
        self,
        text: str,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
    ) -> Optional[int]:
        target = self._normalize_text(text)
        if not target:
            return None

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC"
        )
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            row_id = int(row[0])
            row_text = row[1] or ""
            row_kind = row[2]
            row_tags = json.loads(row[3]) if row[3] else []
            row_ts = float(row[4])

            if not self._matches_filters(
                kind=row_kind,
                tags=row_tags,
                timestamp=row_ts,
                include_kinds=include_kinds,
                include_tags=include_tags,
                since_ts=None,
            ):
                continue

            if self._normalize_text(row_text) == target:
                return row_id

        return None

    @_tracked_memory_operation("delete_memory", rejected=0)
    def delete_memory_ids(self, memory_ids: List[int]) -> int:
        ids = []
        for mem_id in memory_ids or []:
            try:
                ids.append(int(mem_id))
            except Exception:
                continue

        ids = [mem_id for mem_id in dict.fromkeys(ids) if mem_id > 0]
        if not ids:
            return 0

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            placeholders = ",".join(["?"] * len(ids))
            cur.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", tuple(ids))
            deleted = int(cur.rowcount or 0)
            conn.commit()
            conn.close()
        except Exception as e:
            self._set_error(f"delete_memory_ids_error: {e}")
            return 0

        # Why: an index-rebuild failure here is unrelated to whether the delete itself
        # committed. Letting it escape used to make this function report 0 rows deleted
        # even though the DELETE had already succeeded, hiding real deletions from callers.
        if deleted > 0:
            try:
                self.small_index, self.base_index = self._rebuild_indexes()
            except Exception as e:
                self._set_error(f"delete_memory_ids_rebuild_error: {e}")

        return deleted

    @_tracked_memory_operation("delete_memory_by_text", rejected=0)
    def delete_memory_by_text(
        self,
        text: str,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
    ) -> int:
        target = self._normalize_text(text)
        if not target:
            return 0

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC")
            rows = cur.fetchall()
            conn.close()

            delete_ids: List[int] = []
            for row in rows:
                row_id = int(row[0])
                row_text = row[1] or ""
                row_kind = row[2]
                row_tags = json.loads(row[3]) if row[3] else []
                row_ts = float(row[4])

                if not self._matches_filters(
                    kind=row_kind,
                    tags=row_tags,
                    timestamp=row_ts,
                    include_kinds=include_kinds,
                    include_tags=include_tags,
                    since_ts=None,
                ):
                    continue

                if self._normalize_text(row_text) == target:
                    delete_ids.append(row_id)

            return self.delete_memory_ids(delete_ids)
        except Exception as e:
            self._set_error(f"delete_memory_by_text_error: {e}")
            return 0

    @_tracked_memory_operation("touch_memory", rejected=False)
    def touch_memory_by_text(
        self,
        text: str,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        add_tags: Optional[List[str]] = None,
    ) -> bool:
        target = self._normalize_text(text)
        if not target:
            return False

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC")
            rows = cur.fetchall()

            now_ts = time.time()
            touched = False
            for row in rows:
                row_id = int(row[0])
                row_text = row[1] or ""
                row_kind = row[2]
                row_tags = json.loads(row[3]) if row[3] else []
                row_ts = float(row[4])

                if not self._matches_filters(
                    kind=row_kind,
                    tags=row_tags,
                    timestamp=row_ts,
                    include_kinds=include_kinds,
                    include_tags=include_tags,
                    since_ts=None,
                ):
                    continue

                if self._normalize_text(row_text) != target:
                    continue

                merged_tags = list(row_tags)
                for tag in add_tags or []:
                    if tag and tag not in merged_tags:
                        merged_tags.append(tag)

                cur.execute(
                    "UPDATE memories SET tags = ?, timestamp = ? WHERE id = ?",
                    (json.dumps(merged_tags), now_ts, row_id),
                )
                conn.commit()
                touched = True
                break

            conn.close()
            return touched
        except Exception as e:
            self._set_error(f"touch_memory_by_text_error: {e}")
            return False

    @_tracked_memory_operation("auto_promote", rejected=lambda self, *args, **kwargs: [])
    def auto_promote_short_term(
        self,
        seed_text: str,
        min_hits: int = 2,
        recent_window: int = 40,
        semantic_top_k: int = 8,
    ) -> List[int]:
        """
        Promote repeated short-term interaction memories into long-term facts.

        Strategy:
        - gather recent short-term interaction rows
        - gather semantically relevant short-term rows for seed_text
        - promote rows that appear at least min_hits times in recent window
        - skip if already present in long-term kinds
        """
        if not seed_text or not seed_text.strip():
            return []

        recent = self.recent_memories(
            top_k=max(recent_window, 1),
            include_kinds=["interaction"],
            include_tags=["short_term"],
        )

        if not recent:
            return []

        freq: Dict[str, int] = {}
        canonical: Dict[str, str] = {}
        for item in recent:
            txt = (item.get("text") or "").strip()
            norm = self._normalize_text(txt)
            if not norm:
                continue
            freq[norm] = freq.get(norm, 0) + 1
            if norm not in canonical:
                canonical[norm] = txt

        semantic = self.search(
            seed_text,
            top_k=max(semantic_top_k, 1),
            include_kinds=["interaction"],
            include_tags=["short_term"],
        )

        promoted: List[int] = []
        for item in semantic:
            txt = (item.get("text") or "").strip()
            norm = self._normalize_text(txt)
            if not norm or len(norm) < 12:
                continue

            if not self._is_promotable_text(txt):
                continue

            if freq.get(norm, 0) < max(min_hits, 1):
                continue

            candidate = canonical.get(norm, txt)

            if self._exists_text(
                candidate,
                include_kinds=["fact", "preference", "procedure"],
            ):
                continue

            mem_id = self.add_memory(
                candidate,
                kind="fact",
                tags=["long_term", "auto_promoted", "from_short_term"],
            )
            if mem_id is not None:
                promoted.append(mem_id)

        return promoted

    @_tracked_memory_operation("get_promoted_memories", rejected=lambda self, *args, **kwargs: [])
    def get_auto_promoted_memories(self, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, kind, tags, timestamp FROM memories ORDER BY timestamp DESC LIMIT ?",
            (max(safe_limit * 6, safe_limit),),
        )
        rows = cur.fetchall()
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in rows:
            row_tags = json.loads(row[3]) if row[3] else []
            if "auto_promoted" not in row_tags:
                continue

            out.append({
                "id": row[0],
                "text": row[1],
                "kind": row[2],
                "tags": row_tags,
                "timestamp": row[4],
            })

            if len(out) >= safe_limit:
                break

        return out

    @_tracked_memory_operation("search", rejected=lambda self, *args, **kwargs: [])
    def search(
        self,
        query: str,
        top_k: int = 5,
        include_kinds: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        since_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        vec_s = self._embed(query, use_base=False)
        vec_b = self._embed(query, use_base=True)

        if not vec_s and not vec_b:
            # Embed unavailable — fall back to keyword search over SQLite
            return self._sqlite_keyword_search(query, top_k, include_kinds, include_tags, since_ts)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        results: Dict[int, Dict[str, Any]] = {}

        candidate_k = max(top_k * 4, top_k)

        if vec_s and self.small_index is not None and self.small_index.ntotal > 0:
            qs = np.array([vec_s], dtype=np.float32)
            ds, ids = self.small_index.search(qs, candidate_k)
            for dist, mem_id in zip(ds[0], ids[0]):
                if mem_id < 0:
                    continue
                mem_id_int = int(mem_id)
                cur.execute("SELECT id, text, kind, tags, timestamp FROM memories WHERE id = ?", (mem_id_int,))
                row = cur.fetchone()
                if row:
                    row_tags = json.loads(row[3]) if row[3] else []
                    if not self._matches_filters(
                        kind=row[2],
                        tags=row_tags,
                        timestamp=row[4],
                        include_kinds=include_kinds,
                        include_tags=include_tags,
                        since_ts=since_ts,
                    ):
                        continue

                    score = float(dist)
                    if mem_id_int not in results or score < results[mem_id_int]["score"]:
                        results[mem_id_int] = {
                            "id": row[0],
                            "text": row[1],
                            "kind": row[2],
                            "tags": row_tags,
                            "timestamp": row[4],
                            "score": score,
                        }

        if vec_b and self.base_index is not None and self.base_index.ntotal > 0:
            qb = np.array([vec_b], dtype=np.float32)
            ds, ids = self.base_index.search(qb, candidate_k)
            for dist, mem_id in zip(ds[0], ids[0]):
                if mem_id < 0:
                    continue
                mem_id_int = int(mem_id)
                cur.execute("SELECT id, text, kind, tags, timestamp FROM memories WHERE id = ?", (mem_id_int,))
                row = cur.fetchone()
                if row:
                    row_tags = json.loads(row[3]) if row[3] else []
                    if not self._matches_filters(
                        kind=row[2],
                        tags=row_tags,
                        timestamp=row[4],
                        include_kinds=include_kinds,
                        include_tags=include_tags,
                        since_ts=since_ts,
                    ):
                        continue

                    score = float(dist)
                    if mem_id_int not in results or score < results[mem_id_int]["score"]:
                        results[mem_id_int] = {
                            "id": row[0],
                            "text": row[1],
                            "kind": row[2],
                            "tags": row_tags,
                            "timestamp": row[4],
                            "score": score,
                        }

        conn.close()

        merged = sorted(results.values(), key=lambda r: r["score"])
        for r in merged:
            r.pop("score", None)

        return merged[:top_k]

    @_tracked_memory_operation("get_context", rejected=lambda self, *args, **kwargs: [])
    def get_context(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        return self.search(query, top_k=top_k)
