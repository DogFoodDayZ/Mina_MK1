from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from tools.file_inspector import inspect_file, is_probably_text_path


DEFAULT_EXCLUDED_PARTS = {
    "$recycle.bin",
    "appdata",
    "boot",
    "config.msi",
    "edge",
    "google",
    "microsoftedge",
    "mozilla",
    "msocache",
    "onedrive",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "system volume information",
    "windows",
    "windows.old",
}

DISCOVERY_NOISE_PARTS = {
    ".git",
    ".openclaw_backup",
    "archive",
    "backups",
    "build",
    "dist",
    "node_modules",
    "tmp",
}

DISCOVERY_NOISE_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}

SECRET_NAME_PATTERN = re.compile(
    r"(?:^|[._-])(?:credential|credentials|password|passwords|passwd|secret|secrets|token|tokens|wallet|cookies?)(?:$|[._-])",
    re.IGNORECASE,
)

SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd)\b\s*[:=]\s*[^\s]{6,})",
    re.IGNORECASE,
)

SECRET_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
}

TOPIC_STOP_WORDS = {
    "about", "after", "again", "also", "because", "before", "being", "could",
    "create", "does", "from", "have", "into", "just", "make", "message", "mina",
    "more", "should", "that", "their", "there", "these", "they", "this", "through",
    "want", "what", "when", "where", "which", "with", "would", "your",
}

OSTRANAUTS_WIKI_HOST_HINTS = (
    "ostranauts.wiki.gg",
    "ostranauts.fandom.com",
)

CODING_REFERENCE_HOST_HINTS = (
    "docs.python.org",
    "developer.mozilla.org",
    "stackoverflow.com",
    "github.com",
    "gitlab.com",
    "learn.microsoft.com",
    "readthedocs.io",
)

CODING_SIGNAL_KEYWORDS = (
    "python",
    "javascript",
    "typescript",
    "rust",
    "c++",
    "api",
    "sdk",
    "library",
    "framework",
    "algorithm",
    "debug",
    "testing",
    "pytest",
    "function",
    "class",
    "repository",
    "source code",
    "documentation",
)


def normalize_roots(roots: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in roots or []:
        value = str(raw or "").strip()
        if not value:
            continue
        normalized = os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(value))))
        if normalized not in out:
            out.append(normalized)
    return out


def is_autonomous_path_allowed(
    path: str,
    approved_roots: Iterable[str],
    excluded_parts: Iterable[str] = DEFAULT_EXCLUDED_PARTS,
) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False

    candidate = os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(raw))))
    roots = normalize_roots(approved_roots)

    def is_within(root: str) -> bool:
        try:
            return os.path.commonpath([candidate, root]) == root
        except ValueError:
            return False

    if not any(is_within(root) for root in roots):
        return False

    parts = {part.lower() for part in Path(candidate).parts}
    blocked = {str(part or "").strip().lower() for part in excluded_parts if str(part or "").strip()}
    if parts.intersection(blocked):
        return False
    if any(part.startswith("windows") for part in parts):
        return False

    name = os.path.basename(candidate).lower()
    if name in SECRET_FILENAMES or SECRET_NAME_PATTERN.search(name):
        return False
    if name.endswith((".key", ".pem", ".pfx", ".p12", ".kdbx")):
        return False

    return True


def contains_sensitive_content(text: str) -> bool:
    return SENSITIVE_CONTENT_PATTERN.search(str(text or "")) is not None


def is_autonomous_discovery_path_relevant(path: str, topics: Iterable[str]) -> bool:
    candidate = os.path.normcase(os.path.abspath(str(path or "")))
    parts = {part.lower() for part in Path(candidate).parts}
    if parts.intersection(DISCOVERY_NOISE_PARTS):
        return False
    if os.path.basename(candidate).lower() in DISCOVERY_NOISE_FILENAMES:
        return False
    normalized_topics = [str(topic or "").strip().lower() for topic in topics]
    return any(topic and topic in candidate.lower() for topic in normalized_topics)


def is_autonomous_news_result_relevant(row: Dict[str, Any], topics: Iterable[str]) -> bool:
    title = str(row.get("title") or "")
    url = str(row.get("url") or "")
    snippet = str(row.get("snippet") or "")
    haystack = f"{title} {snippet} {url}".lower()
    normalized_topics = [str(topic or "").strip().lower() for topic in topics]
    topic_match = any(topic and topic in haystack for topic in normalized_topics)
    trusted_source = any(host in haystack for host in OSTRANAUTS_WIKI_HOST_HINTS + CODING_REFERENCE_HOST_HINTS)
    return topic_match and trusted_source


def conversation_topics(memory: Any, limit: int = 12) -> List[str]:
    try:
        rows = memory.recent_memories(
            top_k=max(4, int(limit)),
            include_kinds=["interaction"],
            include_tags=["user_turn"],
        )
    except Exception:
        return []

    counts: Dict[str, int] = {}
    for row in rows or []:
        text = str(row.get("text") or "").lower()
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", text):
            token = token.strip("_-")
            if len(token) < 4 or token in TOPIC_STOP_WORDS or token.isdigit():
                continue
            counts[token] = counts.get(token, 0) + 1

    return [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]]


class AutonomousCuriosity:
    def __init__(
        self,
        memory: Any,
        tools: Any,
        approved_roots: Iterable[str],
        enabled: bool = True,
        idle_seconds: int = 1800,
        local_interval_seconds: int = 3600,
        news_interval_seconds: int = 21600,
        work_interval_seconds: int = 1800,
        max_directories_per_cycle: int = 40,
        max_file_bytes: int = 2_000_000,
        max_work_retries: int = 2,
        work_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        work_executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.memory = memory
        self.tools = tools
        self.approved_roots = normalize_roots(approved_roots)
        self.enabled = bool(enabled)
        self.idle_seconds = max(300, int(idle_seconds))
        self.local_interval_seconds = max(900, int(local_interval_seconds))
        self.news_interval_seconds = max(3600, int(news_interval_seconds))
        self.work_interval_seconds = max(900, int(work_interval_seconds))
        self.max_directories_per_cycle = max(1, min(200, int(max_directories_per_cycle)))
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.max_work_retries = max(1, min(5, int(max_work_retries)))
        self.work_provider = work_provider
        self.work_executor = work_executor
        self._directory_queue = deque(self.approved_roots)
        self._seen_directories = set(self.approved_roots)
        self._seen_items = set()
        self._events = deque(maxlen=100)
        self._event_seq = 0
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._last_local_at = 0.0
        self._last_news_at = 0.0
        self._last_work_at = 0.0
        self._current_task: Optional[Dict[str, Any]] = None
        self._last_work_result: Optional[Dict[str, Any]] = None
        self._queued_work_count = 0
        self._completed_work_keys = set()
        self._work_attempts: Dict[str, int] = {}
        self._last_error = ""
        self._paused_until = 0.0
        self._pause_reason = ""

    def set_pause(self, seconds: int, reason: str = "") -> float:
        now = time.time()
        duration = max(0, int(seconds))
        until = now + duration if duration > 0 else now
        with self._lock:
            if until > self._paused_until:
                self._paused_until = until
                self._pause_reason = str(reason or "runtime_pause").strip()[:160]
            return self._paused_until

    def maybe_start(self, current_idle_seconds: float) -> bool:
        now = time.time()
        if not self.enabled or float(current_idle_seconds) < float(self.idle_seconds):
            return False

        with self._lock:
            if now < float(self._paused_until or 0.0):
                return False
            if self._worker is not None and self._worker.is_alive():
                return False
            do_local = now - self._last_local_at >= self.local_interval_seconds
            do_news = now - self._last_news_at >= self.news_interval_seconds
            do_work = (
                self.work_provider is not None
                and self.work_executor is not None
                and now - self._last_work_at >= self.work_interval_seconds
            )
            if not do_local and not do_news and not do_work:
                return False
            if do_local:
                self._last_local_at = now
            if do_news:
                self._last_news_at = now
            if do_work:
                self._last_work_at = now
            self._worker = threading.Thread(
                target=self._run_cycle,
                args=(do_local, do_news, do_work),
                name="mk1-autonomous-curiosity",
                daemon=True,
            )
            self._worker.start()
            return True

    def _run_cycle(self, do_local: bool, do_news: bool, do_work: bool = False) -> None:
        try:
            if do_work:
                self._run_useful_work()
            topics = conversation_topics(self.memory)
            if not topics:
                return
            if do_local:
                self._scan_local(topics)
            if do_news:
                self._scan_news(topics)
            self._last_error = ""
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _run_useful_work(self) -> None:
        if self.work_provider is None or self.work_executor is None:
            return
        candidates = self.work_provider() or []
        with self._lock:
            self._queued_work_count = len(candidates)
        task = next(
            (
                dict(candidate)
                for candidate in candidates
                if str(candidate.get("key") or "").strip()
                and str(candidate.get("key") or "").strip() not in self._completed_work_keys
                and self._work_attempts.get(str(candidate.get("key") or "").strip(), 0) < self.max_work_retries
            ),
            None,
        )
        if task is None:
            return

        task_key = str(task.get("key") or "").strip()
        with self._lock:
            self._current_task = task
        try:
            result = self.work_executor(task)
            if not isinstance(result, dict):
                result = {"ok": False, "summary": str(result or "invalid work result")}
        except Exception as exc:
            result = {
                "ok": False,
                "summary": f"{type(exc).__name__}: {exc}",
            }
        finally:
            with self._lock:
                self._current_task = None

        record = {
            "task": task,
            "result": result,
            "completed_at": time.time(),
        }
        with self._lock:
            self._last_work_result = record
            if bool(result.get("ok")):
                self._completed_work_keys.add(task_key)
            elif not bool(result.get("deferred")):
                # Why: a task deferred because the user just became active isn't a real
                # failure and shouldn't burn one of its limited retry attempts.
                self._work_attempts[task_key] = self._work_attempts.get(task_key, 0) + 1

        title = str(task.get("title") or task_key)
        summary = str(result.get("summary") or result.get("error") or "No result").strip()
        outcome = str(result.get("outcome") or "").strip()
        # Why: evidence-only research is not a failed action, but it also cannot close a task.
        # Guard: keep the event and memory history honest without bypassing bounded retries.
        result_tag = "success" if result.get("ok") else "research" if outcome == "research_complete" else "failed"
        memory_id = self.memory.add_memory(
            f"Autonomous work: {title} | {summary}",
            kind="procedure",
            tags=["autonomous_work", "audit", result_tag],
        )
        with self._lock:
            self._event_seq += 1
            self._events.append({
                "id": self._event_seq,
                "ts": time.time(),
                "source": "work",
                "message": f"Autonomous work: {title} - {summary}",
                "reference": str(task.get("reference") or ""),
                "memory_id": memory_id,
                "ok": bool(result.get("ok")),
                "outcome": outcome,
            })

    def _scan_local(self, topics: List[str]) -> None:
        best_path = ""
        best_score = 0

        for _ in range(self.max_directories_per_cycle):
            if not self._directory_queue:
                self._directory_queue.extend(self.approved_roots)
                self._seen_directories = set(self.approved_roots)
            directory = self._directory_queue.popleft()
            if not is_autonomous_path_allowed(directory, self.approved_roots):
                continue
            try:
                entries = list(os.scandir(directory))
            except (OSError, PermissionError):
                continue

            for entry in entries:
                path = entry.path
                if not is_autonomous_path_allowed(path, self.approved_roots):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        normalized = os.path.normcase(os.path.abspath(path))
                        if normalized not in self._seen_directories:
                            self._seen_directories.add(normalized)
                            self._directory_queue.append(normalized)
                        continue
                    if not entry.is_file(follow_symlinks=False) or not is_probably_text_path(path):
                        continue
                    if entry.stat(follow_symlinks=False).st_size > self.max_file_bytes:
                        continue
                except (OSError, PermissionError):
                    continue

                if not is_autonomous_discovery_path_relevant(path, topics):
                    continue
                lowered = path.lower()
                score = sum(1 for topic in topics if topic in lowered)
                if score > best_score and lowered not in self._seen_items:
                    best_path = path
                    best_score = score

        if not best_path or best_score <= 0:
            return

        inspected = inspect_file(best_path, max_chars=900)
        if not inspected.get("ok"):
            return
        result = inspected.get("result") if isinstance(inspected.get("result"), dict) else {}
        if not bool(result.get("is_text")):
            return
        excerpt = " ".join(str(result.get("content") or "").split())[:500]
        if not excerpt or contains_sensitive_content(excerpt):
            return

        self._seen_items.add(best_path.lower())
        summary = f"Autonomous local discovery: {best_path} | {excerpt}"
        self._store_and_announce("local", summary, best_path)

    def _scan_news(self, topics: List[str]) -> None:
        available = getattr(self.tools, "tools", {})
        if "web_search" not in available:
            return
        query_topics = " ".join(topics[:3])
        queries: List[str] = []
        if query_topics:
            queries.append(f"{query_topics} latest news")
        for search_query in queries:
            result = self.tools.run("web_search", {
                "query": search_query,
                "max_results": 3,
                "timeout": 15,
                "include_content": False,
            })
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            payload = result.get("result") if isinstance(result.get("result"), dict) else {}
            rows = payload.get("results") if isinstance(payload.get("results"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or "").strip()
                url = str(row.get("url") or "").strip()
                snippet = " ".join(str(row.get("snippet") or "").split())[:500]
                key = url.lower() or title.lower()
                if not key or key in self._seen_items:
                    continue
                if not is_autonomous_news_result_relevant(row, topics):
                    continue
                self._seen_items.add(key)
                summary = f"Autonomous news discovery: {title} | {snippet} | {url}"
                self._store_and_announce("news", summary, url)
                return

    def _store_and_announce(self, source: str, summary: str, reference: str) -> None:
        memory_id = self.memory.add_memory(
            summary,
            kind="procedure",
            tags=["autonomous_curiosity", "relevant_discovery", source],
        )
        with self._lock:
            self._event_seq += 1
            self._events.append({
                "id": self._event_seq,
                "ts": time.time(),
                "source": source,
                "message": f"Hey, look at this: {summary}",
                "reference": reference,
                "memory_id": memory_id,
            })

    def events_since(self, since_id: int = 0, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._events if int(row.get("id") or 0) > int(since_id)]
        return rows[-max(1, min(100, int(limit))):]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = self._worker is not None and self._worker.is_alive()
            event_count = len(self._events)
        return {
            "enabled": self.enabled,
            "approved_roots": list(self.approved_roots),
            "idle_seconds": self.idle_seconds,
            "local_interval_seconds": self.local_interval_seconds,
            "news_interval_seconds": self.news_interval_seconds,
            "work_interval_seconds": self.work_interval_seconds,
            "max_work_retries": self.max_work_retries,
            "running": running,
            "queued_directories": len(self._directory_queue),
            "event_count": event_count,
            "last_local_at": self._last_local_at or None,
            "last_news_at": self._last_news_at or None,
            "last_work_at": self._last_work_at or None,
            "current_task": dict(self._current_task) if self._current_task else None,
            "queued_work_count": self._queued_work_count,
            "next_work_at": (
                self._last_work_at + self.work_interval_seconds
                if self._last_work_at
                else None
            ),
            "last_work_result": dict(self._last_work_result) if self._last_work_result else None,
            "last_error": self._last_error or None,
            "paused": time.time() < float(self._paused_until or 0.0),
            "paused_until": self._paused_until or None,
            "pause_reason": self._pause_reason or None,
        }