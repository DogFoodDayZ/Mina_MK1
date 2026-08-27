"""
SafeMemoryProxy: Isolates alternative model writes while allowing reads from Mina's memory.

This proxy allows models like dolphin3 to:
- Read personality, system state, and current context from Mina's memory
- Write only to an isolated "alternative_model_session.jsonl" log
- Zero pollution back to Mina's core memory
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path


class SafeMemoryProxy:
    """
    Wraps MK1Memory to provide read-through access to Mina's memory
    while redirecting all writes to an isolated alternative model session.
    """

    def __init__(
        self,
        mina_memory,  # Reference to the real MK1Memory instance
        alt_model_name: str = "dolphin3",
        session_dir: Optional[str] = None,
    ):
        """
        Args:
            mina_memory: The MK1Memory instance (Mina's core memory)
            alt_model_name: Name of the alternative model (used in filenames/tags)
            session_dir: Directory for isolated model session logs. 
                         Defaults to memory/alt_model_sessions/
        """
        self.mina_memory = mina_memory
        self.alt_model_name = alt_model_name

        if session_dir is None:
            memory_root = os.path.dirname(os.path.abspath(__file__))
            session_dir = os.path.join(memory_root, "alt_model_sessions")

        self.session_dir = session_dir
        self.session_log_path = os.path.join(
            session_dir, f"{alt_model_name}_session.jsonl"
        )

        # Ensure session directory exists
        Path(self.session_dir).mkdir(parents=True, exist_ok=True)

        # Track write count for this session
        self._write_count = 0
        self._session_start = datetime.now(timezone.utc).isoformat()

    # ========== READ-THROUGH OPERATIONS (pass to Mina's memory) ==========

    def get_context(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve context from Mina's memory via semantic search."""
        return self.mina_memory.get_context(query, top_k=top_k)

    def get_auto_promoted_memories(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get auto-promoted memories from Mina's memory."""
        return self.mina_memory.get_auto_promoted_memories(limit=limit)

    def get_status(self) -> Dict[str, Any]:
        """Get Mina's memory system status."""
        return self.mina_memory.get_status()

    def get_personality(self) -> Optional[str]:
        """
        Load Mina's personality directly from the config file.
        This gives the alt model Mina's core identity.
        """
        personality_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "personality.txt",
        )
        if os.path.exists(personality_path):
            try:
                with open(personality_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return None

    def get_due_tasks(self) -> List[Dict[str, Any]]:
        """Get current due tasks from Mina's task memory."""
        return self.mina_memory.get_due_tasks()

    def get_task_memories(self) -> List[Dict[str, Any]]:
        """Get all tasks from Mina's task memory."""
        return self.mina_memory.get_task_memories()

    # ========== ISOLATED WRITE OPERATIONS (sandbox) ==========

    def add_memory(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        source: str = "alt_model",
    ) -> Optional[int]:
        """
        Write memory to isolated session log instead of Mina's memory.
        Optionally include source tag to identify this as alt model data.
        """
        return self._write_to_session_log(
            operation="add_memory",
            text=text,
            tags=tags or [],
            source=source,
        )

    def add_time_passage_note(
        self,
        text: str,
        tags: Optional[List[str]] = None,
    ) -> Optional[int]:
        """
        Write time passage note to isolated session log.
        """
        return self._write_to_session_log(
            operation="time_passage",
            text=text,
            tags=tags or [],
        )

    def add_task_memory(
        self,
        text: str,
        start_time: Optional[float] = None,
        due_time: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[int]:
        """
        Write task to isolated session log.
        """
        return self._write_to_session_log(
            operation="add_task",
            text=text,
            start_time=start_time,
            due_time=due_time,
            tags=tags or [],
        )

    # ========== INTERNAL SESSION LOG ==========

    def _write_to_session_log(self, operation: str, **kwargs) -> Optional[int]:
        """
        Write an operation to the isolated session log.
        Returns an operation ID.
        """
        try:
            operation_id = int(time.time() * 1000000) % (10**9)  # microsecond timestamp
            record = {
                "op_id": operation_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": operation,
                "model": self.alt_model_name,
                "data": kwargs,
            }

            # Append to session log
            with open(self.session_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            self._write_count += 1
            return operation_id

        except Exception as e:
            print(f"[SafeMemoryProxy] Error writing to session log: {e}")
            return None

    def get_session_writes(self) -> List[Dict[str, Any]]:
        """Retrieve all writes made during this session."""
        if not os.path.exists(self.session_log_path):
            return []

        writes = []
        try:
            with open(self.session_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        writes.append(json.loads(line))
        except Exception as e:
            print(f"[SafeMemoryProxy] Error reading session log: {e}")

        return writes

    def clear_session_writes(self) -> bool:
        """Clear this session's isolated write log (use before switching to permanent mode)."""
        try:
            if os.path.exists(self.session_log_path):
                os.remove(self.session_log_path)
            self._write_count = 0
            return True
        except Exception as e:
            print(f"[SafeMemoryProxy] Error clearing session log: {e}")
            return False

    def migrate_session_to_mina_memory(self) -> bool:
        """
        Migrate all isolated session writes to Mina's core memory.
        Call this when you're ready to permanently switch the coding agent.
        """
        try:
            writes = self.get_session_writes()
            if not writes:
                return True

            for record in writes:
                operation = record.get("operation", "add_memory")
                data = record.get("data", {})

                if operation == "add_memory":
                    self.mina_memory.add_memory(
                        text=data.get("text", ""),
                        tags=data.get("tags", []),
                    )
                elif operation == "time_passage":
                    self.mina_memory.add_time_passage_note(
                        text=data.get("text", ""),
                        tags=data.get("tags", []),
                    )
                elif operation == "add_task":
                    self.mina_memory.add_task_memory(
                        text=data.get("text", ""),
                        start_time=data.get("start_time"),
                        due_time=data.get("due_time"),
                        tags=data.get("tags", []),
                    )

            # Clear the session log after successful migration
            self.clear_session_writes()
            return True

        except Exception as e:
            print(f"[SafeMemoryProxy] Error migrating session: {e}")
            return False

    def get_session_info(self) -> Dict[str, Any]:
        """Get info about this proxy session."""
        return {
            "model": self.alt_model_name,
            "session_start": self._session_start,
            "write_count": self._write_count,
            "session_log": self.session_log_path,
            "isolation_status": "active",
        }
