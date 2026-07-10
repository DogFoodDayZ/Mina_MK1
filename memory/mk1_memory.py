import os
import sqlite3
import time
import json
import shutil
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import faiss
import requests


ROOT = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = ROOT

DB_PATH_DEFAULT = os.path.join(MEMORY_DIR, "memory.db")
FAISS_SMALL_PATH_DEFAULT = os.path.join(MEMORY_DIR, "faiss_small.index")
FAISS_BASE_PATH_DEFAULT = os.path.join(MEMORY_DIR, "faiss_base.index")
BACKUP_DIR_DEFAULT = os.path.join(MEMORY_DIR, "backups")


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
    ):
        self.db_path = db_path
        self.faiss_small_path = faiss_small_path
        self.faiss_base_path = faiss_base_path
        self.backup_dir = backup_dir

        self.embed_small_url = embed_small_url
        self.embed_base_url = embed_base_url
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

        self._write_counter = 0

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

        try:
            resp = requests.post(url, json={"text": text}, timeout=30)
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
            emb_small BLOB NOT NULL,
            dim_small INTEGER NOT NULL,
            emb_base BLOB NOT NULL,
            dim_base INTEGER NOT NULL
        );
        """)

        conn.commit()
        conn.close()
        self._set_ok_flag("db_ok", True)

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

        dim_small = rows[0][2]
        dim_base = rows[0][4]

        small = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_small))
        base = faiss.IndexIDMap2(faiss.IndexFlatL2(dim_base))

        for mem_id, emb_s, _, emb_b, _ in rows:
            vs = np.frombuffer(emb_s, dtype=np.float32)
            vb = np.frombuffer(emb_b, dtype=np.float32)
            small.add_with_ids(np.array([vs]), np.array([mem_id], dtype=np.int64))
            base.add_with_ids(np.array([vb]), np.array([mem_id], dtype=np.int64))

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

                if not t:
                    return True

                noise_patterns = [
                    r"^mina-style output\s*:",
                    r"^mina-sign-off\s*:",
                    r"^verified output\s*:",
                    r"^```text$",
                    r"^```$",
                    r"^gremlin memory ping:\s*i do not have that in memory yet\.?$",
                    r"^gremlin memory check, incoming\.?",
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

    # ================= ADD MEMORY =================

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

        vec_small = self._embed(text, use_base=False)
        vec_base = self._embed(text, use_base=True)

        if not vec_small or not vec_base:
            self._set_error("no_embedding_returned")
            return None

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
            INSERT INTO memories (text, kind, tags, timestamp, emb_small, dim_small, emb_base, dim_base)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            text,
            kind,
            json.dumps(tags),
            time.time(),
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

        # write‑based maintenance triggers
        self._write_counter += 1
        if (
            self.auto_backup_every_writes is not None
            and self._write_counter >= self.auto_backup_every_writes
        ):
            self.backup(reason="scheduled_writes")
            self._write_counter = 0

        return mem_id

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

            if deleted > 0:
                self.small_index, self.base_index = self._rebuild_indexes()
            return deleted
        except Exception as e:
            self._set_error(f"delete_memory_ids_error: {e}")
            return 0

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
            return []

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

    def get_context(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        return self.search(query, top_k=top_k)
