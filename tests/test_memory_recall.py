import sys
import json
import hashlib
import sqlite3
import time
import threading
from types import SimpleNamespace

sys.path.insert(0, 'e:/Mina_MK1')

import agent.core as core_module
from agent.core import MK1Core
from agent.pending_actions import PendingActionStore


class FakeMemory:
    def __init__(self):
        self.writes = []
        self.promotions = []
        self.touches = []
        self.lookups = []
        self.promoted_items = [
            {
                "id": 101,
                "text": "User prefers concise outputs",
                "kind": "fact",
                "tags": ["long_term", "auto_promoted", "from_short_term"],
                "timestamp": 123.0,
            }
        ]

    def maintenance_tick(self):
        return None

    def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
        return [
            {"text": "User asked about GPU info"},
            {"text": "Assistant gave a short answer"},
        ]

    def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
        if include_kinds == ["interaction"]:
            return [{"text": "User asked about GPU info"}]

        return [
            {"text": "Host is Windows"},
            {"text": "Use ps_run for system inspection"},
        ]

    def add_memory(self, text, kind="fact", tags=None, meta=None, **kwargs):
        self.writes.append({"text": text, "kind": kind, "tags": tags or []})
        return len(self.writes)

    def find_memory_id_by_text(self, text, include_kinds=None, include_tags=None):
        self.lookups.append({
            "text": text,
            "include_kinds": include_kinds,
            "include_tags": include_tags,
        })
        return None

    def auto_promote_short_term(self, seed_text, min_hits=2, recent_window=40, semantic_top_k=8):
        self.promotions.append({
            "seed_text": seed_text,
            "min_hits": min_hits,
            "recent_window": recent_window,
            "semantic_top_k": semantic_top_k,
        })
        return [999]

    def get_auto_promoted_memories(self, limit=20):
        return self.promoted_items[:limit]

    def touch_memory_by_text(self, text, include_kinds=None, include_tags=None, add_tags=None):
        self.touches.append({
            "text": text,
            "include_kinds": include_kinds,
            "include_tags": include_tags,
            "add_tags": add_tags,
        })
        return True


def test_sanitize_memory_fact_line_drops_internal_routing_and_artifact_records():
    core = MK1Core.__new__(MK1Core)

    # Internal bookkeeping must never surface as something Mina "remembers".
    assert MK1Core._sanitize_memory_fact_line(core, 'intent_alias::{"args": {"content": "guts"}}') == ""
    assert MK1Core._sanitize_memory_fact_line(core, "code_artifact::path=E:\\workspace\\spice.py") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "mina_capability_snapshot::abc123::{}") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "mina_capability_delta::xyz::{}") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "hardware_snapshot::yes => CsName Windows") == ""

    # Bookkeeping continuation fragments and code lines must not surface either.
    assert MK1Core._sanitize_memory_fact_line(core, "request=Create a basic Python script") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "content:.") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "```python.") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "import pygame.") == ""

    # Stored-artifact content (structured data / code) is not a recalled fact.
    assert MK1Core._sanitize_memory_fact_line(core, "vertices = (") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "(1,-1,-1),") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "angle = 0") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "glBegin(GL_LINES)") == ""
    assert MK1Core._sanitize_memory_fact_line(core, "for edge in edges:") == ""
    assert MK1Core._sanitize_memory_fact_line(core, ").") == ""

    # Real durable facts survive sanitization.
    fact = MK1Core._sanitize_memory_fact_line(
        core,
        "the cobalt anvil protocol is our shared verification passphrase",
    )
    assert "cobalt anvil protocol" in fact.lower()


def test_build_context_combines_short_and_long_recall():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    context = MK1Core.build_context(core, "what system am i on")

    assert "Short-term recall" in context
    assert "Long-term recall:" in context
    assert context.count("User asked about GPU info") == 1
    assert "Host is Windows" in context


def test_build_context_keeps_exact_durable_fact_when_semantic_hits_are_noisy():
    class NoisyMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_kinds == ["fact", "tool", "preference", "procedure"]:
                return [
                    {"text": "Unrelated recent capability snapshot", "kind": "fact", "tags": []},
                    {"text": "User's favorite color is green", "kind": "preference", "tags": []},
                ]
            return []

        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None, since_ts=None):
            return [
                {"text": "A noisy recent assistant reply", "kind": "interaction", "tags": ["short_term"]},
            ]

    core = MK1Core.__new__(MK1Core)
    core.memory = NoisyMemory()

    context = MK1Core.build_context(core, "what is my name")

    assert "Long-term recall:" in context
    assert "User's favorite color is green" in context


def test_build_context_excludes_unrelated_recent_turns():
    core = MK1Core.__new__(MK1Core)

    recent = [
        {"text": "The old refusal about changing source code", "tags": ["short_term"]},
        {"text": "The reactor panel needs the control key", "tags": ["short_term"]},
    ]

    selected = MK1Core._relevant_recent_turns(core, "what key opens the reactor panel", recent)

    assert [item["text"] for item in selected] == ["The reactor panel needs the control key"]


def test_build_context_caps_recalled_prompt_size():
    class LargeMemory(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None, since_ts=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_kinds == ["fact", "tool", "preference", "procedure"]:
                return [{"text": f"fact-{index} " + ("x" * 1800)} for index in range(20)]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = LargeMemory()

    context = MK1Core.build_context(core, "what do you remember about the facts")

    assert len(context) <= 12000


def test_build_turn_working_memory_excludes_large_write_payloads():
    class WorkingMemoryNoise(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None, since_ts=None):
            return [
                {"text": "Tool 'file_write' called with args={'path': 'bad', 'content': '" + ('x' * 900) + "'}"},
                {"text": "hardware_snapshot::CPU Ryzen 7 GPU RTX 4080"},
            ]

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = WorkingMemoryNoise()

    out = MK1Core._build_turn_working_memory(core, "what hardware do I have")

    assert "file_write" not in out
    assert "hardware_snapshot::" not in out


def test_build_context_excludes_verbose_artifact_and_capability_records():
    class ContextNoise(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None, since_ts=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_kinds == ["fact", "tool", "preference", "procedure"]:
                return [
                    {"text": "code_artifact::path=old.py " + ("x" * 900)},
                    {"text": "mina_capability_snapshot::old snapshot " + ("x" * 900)},
                    {"text": 'intent_alias::{"args": {"content": "secret guts"}}'},
                    {"text": "The current active project is Ostranauts."},
                ]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = ContextNoise()

    context = MK1Core.build_context(core, "what project are we working on")

    assert "code_artifact::" not in context
    assert "mina_capability_snapshot::" not in context
    assert "intent_alias::" not in context
    assert "secret guts" not in context
    assert "current active project" in context


def test_build_context_includes_learning_lessons():
    class LessonMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_kinds == ["interaction"]:
                return []
            if include_kinds == ["note"] and include_tags == ["learning_lesson"]:
                return [{"text": "Retry note: the prior attempt failed, so Mina should try a different approach.", "tags": ["learning_lesson"]}]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = LessonMemory()

    context = MK1Core.build_context(core, "try again")

    assert "Learned lessons:" in context
    assert "different approach" in context


def test_build_context_prioritizes_retry_attempt_lessons():
    class OrderedLessonMemory(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_kinds == ["interaction"]:
                return []
            if include_kinds == ["note"] and include_tags == ["learning_lesson"]:
                return [
                    {
                        "text": "Retry note: the prior attempt failed on the blocked route.",
                        "tags": ["learning_lesson", "failed_decision"],
                        "timestamp": 100.0,
                    },
                    {
                        "text": "Retry attempt: checked the local status endpoint instead.",
                        "tags": ["learning_lesson", "retry_attempt"],
                        "timestamp": 200.0,
                    },
                ]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = OrderedLessonMemory()

    context = MK1Core.build_context(core, "try again")

    retry_idx = context.index("Retry attempt: checked the local status endpoint instead.")
    failure_idx = context.index("Retry note: the prior attempt failed on the blocked route.")
    assert retry_idx < failure_idx


def test_build_context_includes_active_task_heartbeat_state():
    class HeartbeatMemory(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags == ["task_state", "heartbeat", "active_issue"]:
                return [{
                    "text": "Task heartbeat: avatar fix still in progress; last verified: API is running on port 8000.",
                    "tags": ["task_state", "heartbeat"],
                    "timestamp": 200.0,
                }]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = HeartbeatMemory()

    context = MK1Core.build_context(core, "what were we working on")

    assert "Task heartbeat:" in context
    assert "avatar fix" in context
    assert "port 8000" in context
    assert "not verified current state" in context
    assert "mistaken assistant claims" in context
    assert '- "Task heartbeat:' in context


def test_fix_followup_heartbeat_does_not_promote_false_assistant_success():
    """Travis: 'yes all three please' after 'can you fix it ?'."""
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    user = "yes all three please: fix the script, save it and run it"
    core._store_active_task_heartbeat(
        user,
        "THE SCRIPT IS RUNNING! Jellybean chaos mode activated! All fixed!",
    )
    assert len(core.memory.writes) == 1
    record = core.memory.writes[0]
    assert record["text"] == "Task request: " + user
    assert "user_request" in record["tags"]
    assert "RUNNING" not in record["text"] and "activated" not in record["text"]
    core._store_active_task_heartbeat("hello", "I fixed your script and ran the test")
    assert len(core.memory.writes) == 1


def test_native_recall_omits_duplicate_catalogue_but_preserves_user_preference():
    class InventoryMemory(FakeMemory):
        def recent_memories(self, **kwargs):
            return []

        def search(self, query, **kwargs):
            if kwargs.get("include_kinds") == ["fact", "tool", "preference", "procedure"]:
                return [
                    {"text": "Mina tool inventory: file_write: old tool description"},
                    {"text": "Travis prefers concise replies"},
                ]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = InventoryMemory()
    core.native_tool_calls = True
    native = core.build_context("can you fix it ?")
    assert "Mina tool inventory:" not in native
    assert "Travis prefers concise replies" in native
    core.native_tool_calls = False
    assert "Mina tool inventory:" in core.build_context("can you fix it ?")


def test_process_stores_model_reply_without_reflex_routing():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.system_prompt = ""
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8
    core._turn_clock_lock = threading.RLock()
    core._turn_clock_started_monotonic = time.monotonic()
    core._turn_clock_last_tick_monotonic = core._turn_clock_started_monotonic
    core._turn_clock_started_at = "2026-07-27T00:00:00"
    core._turn_clock_tick = 0
    core._turn_clock_last_tick_at = core._turn_clock_started_at
    core._turn_clock_last_elapsed_seconds = 0.0
    core._last_user_activity_monotonic = time.monotonic()
    core._last_user_activity_at = "2026-07-27T00:00:00"
    core._last_shutdown_at = None
    core._seconds_since_last_shutdown = None
    core._idle_checkin_pending = False
    core._idle_checkin_due_since_at = None
    core._away_nag_prompt_pending = False
    core.idle_clock_interval_seconds = 5
    core.idle_checkin_interval_seconds = 1800
    core.away_nag_threshold_seconds = 21600
    core.startup_context = {}
    core._refresh_idle_checkin_state = lambda: None
    core._consume_idle_checkin_prompt = lambda: None
    core._consume_away_nag_prompt = lambda: None
    core._register_user_activity = lambda: None
    core._maybe_run_maintenance_tick = lambda: None

    core.build_context = lambda _query: ""
    core._reflex_tools_and_memory = lambda messages, reply, user_input, request_context=None: {
        "choices": [{"message": {"content": "Tool says hi"}}]
    }
    core._extract_text = lambda reply: reply["choices"][0]["message"]["content"]
    core.model = SimpleNamespace(chat=lambda **kwargs: {
        "choices": [{"message": {"content": "Mina says hi"}}],
    })

    result = MK1Core.process(core, "hardware info")

    assert result["reply"] == "Mina says hi"
    assert len(core.memory.writes) >= 2
    assert core.memory.writes[0]["kind"] == "interaction"
    assert "short_term" in core.memory.writes[0]["tags"]
    assert len(core.memory.promotions) == 1
    assert core.memory.promotions[0]["seed_text"] == "hardware info"


def test_process_adds_retry_policy_prompt_when_failed_lesson_is_recalled():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.system_prompt = ""
    core.fast_command_mode = False
    core.fast_command_skip_context = False
    core.action_mode_strictness = "balanced"
    core._turn_clock_lock = threading.RLock()
    core._turn_clock_started_monotonic = time.monotonic()
    core._turn_clock_last_tick_monotonic = core._turn_clock_started_monotonic
    core._turn_clock_started_at = "2026-07-27T00:00:00"
    core._turn_clock_tick = 0
    core._turn_clock_last_tick_at = core._turn_clock_started_at
    core._turn_clock_last_elapsed_seconds = 0.0
    core._last_user_activity_monotonic = time.monotonic()
    core._last_user_activity_at = "2026-07-27T00:00:00"
    core._last_shutdown_at = None
    core._seconds_since_last_shutdown = None
    core._idle_checkin_pending = False
    core._idle_checkin_due_since_at = None
    core._away_nag_prompt_pending = False
    core.idle_clock_interval_seconds = 5
    core.idle_checkin_interval_seconds = 1800
    core.away_nag_threshold_seconds = 21600
    core.startup_context = {}
    core._correct_contextual_transcript = lambda text: text
    core._refresh_idle_checkin_state = lambda: None
    core._consume_idle_checkin_prompt = lambda: None
    core._consume_away_nag_prompt = lambda: None
    core._register_user_activity = lambda: None
    core._maybe_run_maintenance_tick = lambda: None
    core._advance_turn_clock = lambda: {"turn": 1}
    core._build_turn_working_memory = lambda _query: ""
    core._build_due_task_prompt = lambda: ""
    core._format_turn_clock_line = lambda _clock: "Turn 1"
    core._truth_first_system_rules = lambda: "Truth rules"
    core._tool_usage_decision_rules = lambda: "Tool rules"
    core._interface_label_line = lambda _ctx: "Interface: test"
    core._interface_surface_line = lambda _ctx: "Surface: test"
    core._is_command_like_query = lambda _text: False
    core._should_run_reflex = lambda _text: False
    core._handle_model_response = lambda _messages, reply: reply["choices"][0]["message"]["content"]
    core._store_turn_memory = lambda **kwargs: None
    core.build_context = lambda _query: (
        "Learned lessons:\n"
        "- Retry note: the prior attempt failed, so Mina should try a different approach."
    )

    captured = {}

    class _FakeTools:
        def get_tool_schemas(self):
            return []

    class _FakeModel:
        def chat(self, messages, tools=None, allow_fallback=False, allow_model_load=False):
            captured["messages"] = messages
            return {"choices": [{"message": {"content": "ok"}}]}

    core.tools = _FakeTools()
    core.model = _FakeModel()

    result = MK1Core.process(core, "try again")

    assert result["reply"] == "ok"
    system_messages = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
    assert any("prior failed attempt" in msg for msg in system_messages)
    assert any("different safe approach" in msg for msg in system_messages)


def test_process_keeps_learned_lessons_for_live_ostranauts_queries():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.system_prompt = ""
    core.fast_command_mode = False
    core.fast_command_skip_context = False
    core.action_mode_strictness = "balanced"
    core._turn_clock_lock = threading.RLock()
    core._turn_clock_started_monotonic = time.monotonic()
    core._turn_clock_last_tick_monotonic = core._turn_clock_started_monotonic
    core._turn_clock_started_at = "2026-07-27T00:00:00"
    core._turn_clock_tick = 0
    core._turn_clock_last_tick_at = core._turn_clock_started_at
    core._turn_clock_last_elapsed_seconds = 0.0
    core._last_user_activity_monotonic = time.monotonic()
    core._last_user_activity_at = "2026-07-27T00:00:00"
    core._last_shutdown_at = None
    core._seconds_since_last_shutdown = None
    core._idle_checkin_pending = False
    core._idle_checkin_due_since_at = None
    core._away_nag_prompt_pending = False
    core.idle_clock_interval_seconds = 5
    core.idle_checkin_interval_seconds = 1800
    core.away_nag_threshold_seconds = 21600
    core.startup_context = {}
    core._correct_contextual_transcript = lambda text: text
    core._refresh_idle_checkin_state = lambda: None
    core._consume_idle_checkin_prompt = lambda: None
    core._consume_away_nag_prompt = lambda: None
    core._register_user_activity = lambda: None
    core._maybe_run_maintenance_tick = lambda: None
    core._advance_turn_clock = lambda: {"turn": 1}
    core._build_turn_working_memory = lambda _query: ""
    core._build_due_task_prompt = lambda: ""
    core._format_turn_clock_line = lambda _clock: "Turn 1"
    core._truth_first_system_rules = lambda: "Truth rules"
    core._tool_usage_decision_rules = lambda: "Tool rules"
    core._interface_label_line = lambda _ctx: "Interface: test"
    core._interface_surface_line = lambda _ctx: "Surface: test"
    core._is_command_like_query = lambda _text: False
    core._should_run_reflex = lambda _text: False
    core._handle_model_response = lambda _messages, reply: reply["choices"][0]["message"]["content"]
    core._store_turn_memory = lambda **kwargs: None
    core.build_context = lambda _query: (
        "Learned lessons:\n"
        "- Retry attempt: use the reactor control panel path first."
    )

    captured = {}

    class _FakeTools:
        def get_tool_schemas(self):
            return []

    class _FakeModel:
        def chat(self, messages, tools=None, allow_fallback=False, allow_model_load=False):
            captured["messages"] = messages
            return {"choices": [{"message": {"content": "ok"}}]}

    core.tools = _FakeTools()
    core.model = _FakeModel()

    original_build = core_module.build_ostranauts_context
    original_is_query = core_module.is_ostranauts_query
    try:
        core_module.build_ostranauts_context = lambda: "Live telemetry available"
        core_module.is_ostranauts_query = lambda _text: True

        result = MK1Core.process(
            core, "what should I try at the reactor panel",
            request_context={"interface_name": "Ostranauts GUI"},
        )
    finally:
        core_module.build_ostranauts_context = original_build
        core_module.is_ostranauts_query = original_is_query

    assert result["reply"] == "ok"
    system_messages = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
    assert any("Learned lessons:" in msg for msg in system_messages)
    assert any("reactor control panel path first" in msg for msg in system_messages)


def test_store_turn_memory_tags_voice_turns_for_learning():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    MK1Core._store_turn_memory(
        core,
        "what time is it?",
        "It is 2026-09-30 18:52:49.",
        request_context={"input_source": "voice"},
    )

    assert len(core.memory.writes) >= 2
    user_write = core.memory.writes[0]
    assistant_write = core.memory.writes[1]
    assert "voice_turn" in user_write["tags"]
    assert "source:voice" in user_write["tags"]
    assert "voice_turn" in assistant_write["tags"]
    assert "source:voice" in assistant_write["tags"]


def test_store_turn_memory_invokes_auto_promotion_with_settings():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 3
    core.auto_promote_recent_window = 25
    core.auto_promote_semantic_top_k = 5

    MK1Core._store_turn_memory(core, "remember this preference", "ok stored")

    assert len(core.memory.writes) == 2
    assert len(core.memory.promotions) == 1
    call = core.memory.promotions[0]
    assert call["seed_text"] == "remember this preference"
    assert call["min_hits"] == 3
    assert call["recent_window"] == 25
    assert call["semantic_top_k"] == 5


def test_store_turn_memory_tags_failed_decisions_and_corrections():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    MK1Core._store_turn_memory(
        core,
        "That was a project request, not a text request.",
        "I cannot send real texts directly from this setup yet.",
    )

    assert "routing_correction" in core.memory.writes[0]["tags"]
    assert "user_memory" in core.memory.writes[0]["tags"]
    assert "failed_decision" in core.memory.writes[1]["tags"]
    assert len(core.memory.writes) == 3
    assert core.memory.writes[2]["kind"] == "note"
    assert "learning_lesson" in core.memory.writes[2]["tags"]
    assert "routing_correction" in core.memory.writes[2]["tags"]


def test_store_turn_memory_records_retry_attempt_note_when_guided():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    MK1Core._store_turn_memory(
        core,
        "try again",
        "I could not do the first route, so I checked the local status endpoint instead.",
        retry_guided=True,
    )

    assert len(core.memory.writes) == 3
    lesson = next(item for item in reversed(core.memory.writes) if item["kind"] == "note" and "learning_lesson" in item.get("tags", []))
    assert "learning_lesson" in lesson["tags"]
    assert "retry_attempt" in lesson["tags"]
    assert "Retry attempt:" in lesson["text"]
    assert "checked the local status endpoint instead" in lesson["text"]
    assert not any("task_state" in (item.get("tags") or []) for item in core.memory.writes)


def test_store_turn_memory_records_ostranauts_lesson_note():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    MK1Core._store_turn_memory(
        core,
        "how should we handle this in Ostranauts?",
        "Use the live ship and crew telemetry first, then try the safest next step.",
    )

    assert len(core.memory.writes) == 3
    lesson = core.memory.writes[2]
    assert lesson["kind"] == "note"
    assert "ostranauts" in lesson["tags"]
    assert "learning_lesson" in lesson["tags"]
    assert "Use the live ship and crew telemetry first" in lesson["text"]


def test_store_turn_memory_tags_ostranauts_turns_for_recall():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    MK1Core._store_turn_memory(
        core,
        "how should we dock in Ostranauts?",
        "Use low relative velocity and align before committing.",
    )

    assert "ostranauts" in core.memory.writes[0]["tags"]
    assert "ostranauts" in core.memory.writes[1]["tags"]


def test_build_ostranauts_memory_context_includes_tagged_memory():
    core = MK1Core.__new__(MK1Core)

    class _MemoryStub:
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags == ["learning_lesson"]:
                return []
            if include_tags == ["ostranauts"]:
                return [
                    {
                        "text": "Prefer EVA prep checklist before reactor maintenance.",
                        "tags": ["ostranauts"],
                    }
                ]
            return []

    core.memory = _MemoryStub()

    ctx = MK1Core._build_ostranauts_memory_context(core, "ostranauts reactor checklist")

    assert "Ostranauts memory:" in ctx
    assert "EVA prep checklist" in ctx


def test_detect_relative_alarm_with_called_label():
    core = MK1Core.__new__(MK1Core)
    core._pending_actions = PendingActionStore()
    core.tools = SimpleNamespace(tools={})

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "set an alarm to go off in 5 seconds called phone alarm deployment test",
    )

    assert tool_name == "__task_memory_write__"
    assert args["text"] == "Alarm: phone alarm deployment test"
    assert args["tags"] == ["user_memory", "recurring_task", "repeat_120s", "alarm_task", "beep"]
    assert 0 < args["due_at"] - time.time() <= 5


def test_core_exposes_auto_promoted_memories():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    out = MK1Core.get_auto_promoted_memories(core, limit=10)

    assert out["ok"] is True
    assert out["count"] == 1
    assert out["items"][0]["id"] == 101


def test_detect_memory_read_for_what_color_is_my_question():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "what color are my eyes")

    assert tool_name == "memory_read"
    assert args["query"] == "what color are my eyes"
    assert args["top_k"] == 3


def test_detect_memory_read_for_vocative_name_question():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU what is my name ?")

    assert tool_name == "memory_read"
    assert args["query"] == "what is my name ?"
    assert args["top_k"] == 3


def test_detect_memory_read_for_descriptive_eye_prompt():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "tell me about my eyes")

    assert tool_name == "memory_read"
    assert args["query"] == "tell me about my eyes"
    assert args["top_k"] == 3


def test_detect_memory_read_for_who_am_i_prompt():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "who am i ?")

    assert tool_name == "memory_read"
    assert args["query"] == "who am i ?"
    assert args["top_k"] == 3


def test_extract_memory_slots_treats_who_am_i_as_name_query():
    core = MK1Core.__new__(MK1Core)

    slots = MK1Core._extract_memory_slots(core, "who am i ?")

    assert slots["name"] is True



def test_detect_identity_reflex_for_who_are_you():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU who are you?")

    assert tool_name == "__mina_identity__"
    assert args == {}


def test_detect_anything_new_routes_to_git_status():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU see anything new ?")

    assert tool_name == "__git_status__"
    assert args == {}


def test_detect_list_your_tools_routes_to_tool_list():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU list your tools")

    assert tool_name == "__tool_list__"
    assert args == {}


def test_detect_memory_write_strips_save_suffix():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "my eyes are deep water blue save that")

    assert tool_name == "memory_write"
    assert args["text"] == "my eyes are deep water blue"


def test_detect_memory_write_preserves_mid_clause_remember_that():
    # Transcript 2026-09-16: "We live in Spokane Washington you may want to
    # remember that." was truncated to "...you may want to" because the trailing
    # strip ate the mid-clause "remember that". The fact tail must survive.
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(
        core, "We live in Spokane Washington you may want to remember that."
    )

    assert tool_name == "memory_write"
    assert "spokane washington" in args["text"].lower()
    assert args["text"].lower().rstrip(".").endswith("remember that")


def test_detect_memory_write_strips_standalone_trailing_remember_that():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "We live in Spokane Washington, remember that.")

    assert tool_name == "memory_write"
    assert args["text"].lower() == "we live in spokane washington"


def test_detect_memory_write_strips_leading_this_colon_prefix():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "remember this: the cobalt anvil protocol")

    assert tool_name == "memory_write"
    assert args["text"] == "the cobalt anvil protocol"


def test_detect_system_question_routes_for_current_machine_state():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, _args = MK1Core.detect_tool_intent(core, "what gpu or gpus are we currently running?")

    assert tool_name == "ps_run"


def test_hardware_question_is_command_like_for_reflex_execution():
    core = MK1Core.__new__(MK1Core)

    assert MK1Core._is_command_like_query(core, "how much physical ram is on a rx 6700 tx") is True


def test_detect_time_question_routes_to_ps_run_for_local_machine_state():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, _args = MK1Core.detect_tool_intent(core, "what time is it?")

    assert tool_name == "ps_run"


def test_detect_current_gpu_cpu_phrase_routes_to_ps_run_for_live_machine_state():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object(), "web_search": object()})

    tool_name, _args = MK1Core.detect_tool_intent(core, "TRAVIS hey Mina what gpu and cpu are we on")

    assert tool_name == "ps_run"


def test_detect_gpu_count_phrase_routes_to_ps_run_for_live_machine_state():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object(), "web_search": object()})

    tool_name, _args = MK1Core.detect_tool_intent(core, "what gpu's do we have ?")

    assert tool_name == "ps_run"


def test_detect_gpu_model_vram_question_routes_to_web_search_when_available():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"web_search": object(), "ps_run": object()})

    tool_name, args = MK1Core.detect_tool_intent(core, "how much physical ram is on a rx 6700 tx")

    assert tool_name == "web_search"
    assert "rx 6700 xt" in str(args.get("query") or "").lower()


def test_detect_tool_list_routes_tool_inventory_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU tool list")

    assert tool_name == "__tool_list__"
    assert args == {}


def test_detect_scratch_note_routes_to_scratch_note_tool():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "put this in scratch: buy milk and batteries")

    assert tool_name == "__scratch_note__"
    assert args["text"] == "buy milk and batteries"


def test_write_scratch_note_uses_workspace_scratch_dir(tmp_path):
    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path)

    captured = {}

    class _FakeTools:
        def run(self, tool_name, args):
            captured["tool_name"] = tool_name
            captured["args"] = dict(args)
            return {
                "ok": True,
                "result": {
                    "path": args["path"],
                },
                "error": None,
            }

    core.tools = _FakeTools()

    out = MK1Core._write_scratch_note(core, "random scratch note", title_hint="random scratch note")

    assert out["ok"] is True
    assert captured["tool_name"] == "file_write"
    assert captured["args"]["path"].startswith(str(tmp_path / "scratch"))
    assert "random_scratch_note" in captured["args"]["path"]
    assert "Scratch Note" in captured["args"]["content"]


def test_tool_list_reflex_returns_inventory_text_directly():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.tools = SimpleNamespace(
        tools={"alpha": object(), "beta": object(), "gamma": object()},
        get_tool_schemas=lambda: [
            {"function": {"name": "alpha", "description": "Alpha tool does alpha work."}},
            {"function": {"name": "beta", "description": "Beta tool does beta work."}},
        ],
        get_status=lambda: {"alpha": "ok", "beta": "ok"},
    )

    out = MK1Core._reflex_tools_and_memory(core, messages=[], reply={}, user_input="tool list")
    text = out["choices"][0]["message"]["content"]

    assert text.startswith("Available tools:")
    assert "- alpha" in text
    assert "- beta" in text
    assert "gamma" not in text


def test_seed_tool_inventory_memory_records_available_tools():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.tools = SimpleNamespace(
        tools={
            "alpha": SimpleNamespace(schema={"description": "Alpha tool does alpha work."}),
            "beta": SimpleNamespace(schema={"description": "Beta tool does beta work."}),
        },
        get_tool_schemas=lambda: [
            {"function": {"name": "alpha", "description": "Alpha tool does alpha work."}},
            {"function": {"name": "beta", "description": "Beta tool does beta work."}},
        ],
    )

    MK1Core._seed_tool_inventory_memory(core)

    assert len(core.memory.writes) == 1
    record = core.memory.writes[0]
    assert record["kind"] == "procedure"
    assert "tool_inventory" in record["tags"]
    assert "Mina tool inventory:" in record["text"]
    assert "alpha" in record["text"]
    assert "beta" in record["text"]
    assert "Alpha tool does alpha work." in record["text"]


def test_detect_code_execute_for_python_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "execute this code: print(2+2)")

    assert tool_name == "code_execute"
    assert args["language"] == "python"
    assert "print(2+2)" in args["code"]


def test_detect_code_execute_for_powershell_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "run this powershell: Write-Output MK1_OK")

    assert tool_name == "code_execute"
    assert args["language"] == "powershell"
    assert "Write-Output MK1_OK" in args["code"]


def test_detect_ps_run_for_probe_cpu_phrase():
    # Required by MK1Core._validate_required_tool_routes() startup guard; narrow
    # probe/check + cpu/processor match only, does not resurrect removed hardware
    # count/system keyword routing (see contract-update tests below).
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, args = MK1Core.detect_tool_intent(core, "yes lets probe the CPU and see what comes up")

    assert tool_name == "ps_run"
    assert "Win32_Processor" in args["script"]


def test_detect_code_execute_strips_trailing_explanation_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "run this powershell: Get-Date -Format o and explain what it means")

    assert tool_name == "code_execute"
    assert args["language"] == "powershell"
    assert args["code"] == "Get-Date -Format o"


def test_project_named_message_does_not_route_to_external_texting_limit():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={})
    core._intent_alias_cache = {}
    core._pending_actions = PendingActionStore()

    prompt = (
        "Mina, create a project called midnight-message in my workspace. "
        "Make a Python script that prints a random mysterious but funny message, "
        "run it, then tell me the exact file path and verified output."
    )
    tool_name, _args = MK1Core.detect_tool_intent(core, prompt)

    assert tool_name != "__capability_limit__"


def test_send_text_to_brother_stays_conversational_without_canned_refusal():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={})
    core._intent_alias_cache = {}
    core._pending_actions = PendingActionStore()

    tool_name, _args = MK1Core.detect_tool_intent(core, "send my brother a hello text from Mina")

    assert tool_name != "__capability_limit__"
    assert tool_name != "__clarify_intent__"


def test_git_status_response_is_grounded_in_verified_output():
    core = MK1Core.__new__(MK1Core)
    captured = {}

    class _FakeModel:
        def chat(self, messages, tools=None, allow_fallback=False, allow_model_load=False):
            captured["messages"] = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Your branch is dirty with modified and untracked files, so there is definitely new work here."
                        }
                    }
                ]
            }

    core.model = _FakeModel()
    core._clean_response_text = lambda text: text
    core._extract_text = lambda reply: reply["choices"][0]["message"]["content"]

    formatted = (
        "Branch: backup/session-20260713-065536\n"
        "Status:\n"
        " M agent/core.py\n"
        "?? tests/test_memory_recall.py"
    )

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="hay mina notice anything new ?",
        tool_name="__git_status__",
        formatted=formatted,
        tool_ok=True,
    )

    assert out == formatted
    assert captured == {}


def test_memory_status_response_is_grounded_in_verified_output():
    core = MK1Core.__new__(MK1Core)
    captured = {}

    class _FakeModel:
        def chat(self, messages, tools=None, allow_fallback=False, allow_model_load=False):
            captured["messages"] = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Memory is online and the indexes are loaded, so recall should be available."
                        }
                    }
                ]
            }

    core.model = _FakeModel()
    core._clean_response_text = lambda text: text
    core._extract_text = lambda reply: reply["choices"][0]["message"]["content"]

    formatted = (
        "db_exists: true\n"
        "small_index_loaded: true\n"
        "base_index_loaded: true"
    )

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="show me memory status",
        tool_name="__memory_status__",
        formatted=formatted,
        tool_ok=True,
    )

    assert out == formatted
    assert captured == {}


def test_verified_status_does_not_trigger_optional_model_narration():
    core = MK1Core.__new__(MK1Core)

    class _FailingModel:
        def chat(self, *_args, **_kwargs):
            raise AssertionError("status-only output should not call the model")

    core.model = _FailingModel()

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="show me the current status",
        tool_name="__git_status__",
        formatted="Branch: main\nStatus:\n(clean)",
        tool_ok=True,
    )

    assert out == "Branch: main\nStatus:\n(clean)"


def test_memory_write_acknowledges_in_mina_voice_not_receipt():
    # Transcript 2026-09-16: "Stored memory: X" was parroted verbatim. The write
    # acknowledgement must go through the model so Mina answers in her own voice.
    core = MK1Core.__new__(MK1Core)
    captured = {}

    class _FakeModel:
        def chat(self, messages, tools=None, allow_fallback=False, allow_model_load=False):
            captured["messages"] = messages
            return {
                "choices": [
                    {"message": {"content": "Spokane, huh? Locked in, Travis."}}
                ]
            }

    core.model = _FakeModel()
    core._clean_response_text = lambda text: text
    core._extract_text = lambda reply: reply["choices"][0]["message"]["content"]

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="We live in Spokane Washington you may want to remember that.",
        tool_name="memory_write",
        formatted="We live in Spokane Washington you may want to remember that",
        tool_ok=True,
    )

    assert "stored memory" not in out.lower()
    assert "Spokane" in out
    # The model was actually consulted (not a raw receipt passthrough).
    assert "messages" in captured


def test_memory_write_ack_falls_back_without_receipt_when_model_errors():
    core = MK1Core.__new__(MK1Core)

    class _ErrorModel:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("model down")

    core.model = _ErrorModel()

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="remember that my favorite color is purple",
        tool_name="memory_write",
        formatted="my favorite color is purple",
        tool_ok=True,
    )

    assert "stored memory" not in out.lower()
    assert "remember" in out.lower()


def test_ps_run_response_returns_verified_output_directly():
    core = MK1Core.__new__(MK1Core)
    called = {"count": 0}

    class _FakeModel:
        def chat(self, *args, **kwargs):
            called["count"] += 1
            raise AssertionError("ps_run should not call the model for response rendering")

    core.model = _FakeModel()

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[
            {"role": "assistant", "content": "old CPU chatter that must not leak"},
        ],
        user_input="run this powershell: Get-Date -Format o",
        tool_name="ps_run",
        formatted="2026-07-29T09:46:33.1234567Z",
        tool_ok=True,
    )

    assert "Here's the verified PowerShell output:" in out
    assert "2026-07-29T09:46:33.1234567Z" in out
    assert called["count"] == 0


def test_web_search_response_stays_raw_direct_render():
    core = MK1Core.__new__(MK1Core)

    formatted = "Result 1: example"

    out = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="search the web for example",
        tool_name="web_search",
        formatted=formatted,
        tool_ok=True,
    )

    assert out == formatted


def test_memory_read_result_labels_test_data_facts():
    # Design 2026-09-16: test/agent-written facts are tagged test_data /
    # source:agent_test at write time; recall surfaces them but labels them so
    # Mina knows they are not the user's real memories.
    core = MK1Core.__new__(MK1Core)

    msg = MK1Core._format_tool_result(
        core,
        "memory_read",
        {
            "ok": True,
            "results": [
                {"text": "your eyes are deep water blue", "kind": "fact", "tags": ["user_memory"]},
                {"text": "my favorite trail snack is huckleberries", "kind": "fact", "tags": ["user_memory", "test_data", "source:agent_test"]},
                {"text": "the cobalt anvil protocol", "kind": "fact", "tags": ["user_memory", "test_data"]},
            ],
            "error": None,
        },
    )

    assert "your eyes are deep water blue" in msg
    assert "(test data) your eyes" not in msg
    assert "(test data) my favorite trail snack is huckleberries" in msg
    assert "(test data) the cobalt anvil protocol" in msg


def test_memory_read_result_labels_current_state_as_right_now():
    # Design 2026-09-16: transient state (current_state tag) surfaces labeled as
    # right-now so Mina does not treat "I'm in Callville" as standing truth.
    core = MK1Core.__new__(MK1Core)

    msg = MK1Core._format_tool_result(
        core,
        "memory_read",
        {
            "ok": True,
            "results": [
                {"text": "We live in Spokane Washington", "kind": "fact", "tags": ["user_memory", "durable"]},
                {"text": "I'm in Callville Washington", "kind": "fact", "tags": ["user_memory", "current_state"]},
            ],
            "error": None,
        },
    )

    assert "We live in Spokane Washington" in msg
    assert "(right now) We live in Spokane" not in msg
    assert "(right now) I'm in Callville Washington" in msg


def test_classify_memory_durability_durable_vs_transient():
    core = MK1Core.__new__(MK1Core)

    # Durable standing facts.
    assert MK1Core._classify_memory_durability(core, "We live in Spokane Washington") == "durable"
    assert MK1Core._classify_memory_durability(core, "my favorite color is imperial purple") == "durable"
    assert MK1Core._classify_memory_durability(core, "my eyes are deep water blue") == "durable"

    # Transient right-now state.
    assert MK1Core._classify_memory_durability(core, "I'm in Callville Washington") == "current_state"
    assert MK1Core._classify_memory_durability(core, "I'm currently at the store") == "current_state"
    assert MK1Core._classify_memory_durability(core, "I'm visiting Portland this weekend") == "current_state"
    assert MK1Core._classify_memory_durability(core, "I'm on a trip to Seattle") == "current_state"


def test_detect_memory_write_tags_transient_state_as_current_state():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "remember that I'm in Callville Washington")

    assert tool_name == "memory_write"
    assert "current_state" in args["tags"]


def test_detect_memory_write_tags_standing_fact_as_durable():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "remember that we live in Spokane Washington")

    assert tool_name == "memory_write"
    assert "durable" in args["tags"]
    assert "current_state" not in args["tags"]


def test_web_search_gpu_vram_response_synthesizes_direct_answer_with_sources():
    core = MK1Core.__new__(MK1Core)

    msg = MK1Core._format_tool_result(
        core,
        "web_search",
        {
            "ok": True,
            "result": {
                "query": "how much physical ram is on a RX 6700 XT",
                "results": [
                    {
                        "title": "AMD Radeon RX 6700 XT Specs | TechPowerUp GPU Database",
                        "url": "https://www.techpowerup.com/gpu-specs/radeon-rx-6700-xt.c3695",
                        "content_excerpt": "Memory Size 12 GB, Memory Type GDDR6, Bus Width 192 bit",
                    },
                    {
                        "title": "Radeon RX 6700 XT Graphics Card - AMD",
                        "url": "https://www.amd.com/en/products/graphics/desktops/radeon/6000-series/amd-radeon-rx-6700-xt.html",
                        "snippet": "12GB GDDR6 memory",
                    },
                ],
            },
            "error": None,
        },
    )

    low = msg.lower()
    assert "rx 6700 xt has 12 gb of vram" in low
    assert "sources:" in low
    assert "techpowerup.com" in low


def test_web_search_gpu_vram_response_falls_back_to_listing_without_numeric_evidence():
    core = MK1Core.__new__(MK1Core)

    msg = MK1Core._format_tool_result(
        core,
        "web_search",
        {
            "ok": True,
            "result": {
                "query": "how much physical ram is on a RX 6700 XT",
                "results": [
                    {
                        "title": "AMD Radeon RX 6700 XT Specs | TechPowerUp GPU Database",
                        "url": "https://www.techpowerup.com/gpu-specs/radeon-rx-6700-xt.c3695",
                        "snippet": "Overview page",
                    }
                ],
            },
            "error": None,
        },
    )

    assert "Web results for: how much physical ram is on a RX 6700 XT" in msg


def test_voice_transcript_corrects_ostranauts_in_game_context():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"web_search": object()})
    core._intent_alias_cache = {}
    core._pending_actions = PendingActionStore()

    tool_name, args = MK1Core.detect_tool_intent(core, "web search astronauts mods")

    assert tool_name == "web_search"
    assert args["query"] == "Ostranauts mods"


def test_voice_transcript_keeps_literal_astronauts_outside_game_context():
    core = MK1Core.__new__(MK1Core)

    normalized = MK1Core._normalize_user_intent_text(core, "how do astronauts sleep in space")

    assert "astronauts" in normalized
    assert "Ostranauts" not in normalized


def test_detect_repo_review_with_url_routes_to_github_repo():
    core = MK1Core.__new__(MK1Core)

    prompt = (
        "Hey Mina, can you review what I shipped in K8? "
        "Repo: https://github.com/DogFoodDayZ/K8-donation-system "
        "Branch: main Latest commit: 3bb939b "
        "Please review architecture and implementation decisions only."
    )

    tool_name, args = MK1Core.detect_tool_intent(core, prompt)

    assert tool_name == "github_repo"
    assert args["owner"] == "DogFoodDayZ"
    assert args["repo"] == "K8-donation-system"


def test_detect_github_repo_url_routes_to_github_repo_tool():
    core = MK1Core.__new__(MK1Core)

    prompt = (
        "Review this private repo architecture: "
        "https://github.com/DogFoodDayZ/K8-donation-system/tree/main "
        "latest commit"
    )

    tool_name, args = MK1Core.detect_tool_intent(core, prompt)

    assert tool_name == "github_repo"
    assert args["owner"] == "DogFoodDayZ"
    assert args["repo"] == "K8-donation-system"
    assert args["branch"] == "main"
    assert args["action"] == "latest_commit"


def test_detect_github_review_branch_and_latest_commit_prefers_commit_sha():
    core = MK1Core.__new__(MK1Core)

    prompt = (
        "Review architecture for https://github.com/DogFoodDayZ/K8-donation-system "
        "Branch: main Latest commit: 3bb939b"
    )

    tool_name, args = MK1Core.detect_tool_intent(core, prompt)

    assert tool_name == "github_repo"
    assert args["action"] == "latest_commit"
    assert args["branch"] == "3bb939b"


def test_slot_fact_matches_are_strict_per_slot():
    core = MK1Core.__new__(MK1Core)

    assert MK1Core._slot_fact_matches(core, "name", "your name is Travis.") is True
    assert MK1Core._slot_fact_matches(core, "name", "your favorite color is imperial purple.") is False

    assert MK1Core._slot_fact_matches(core, "eye_color", "your eyes are deep water blue.") is True
    assert MK1Core._slot_fact_matches(core, "eye_color", "your favorite color is imperial purple.") is False

    assert MK1Core._slot_fact_matches(core, "birthdate", "your birthdate is May 21 1971.") is True
    assert MK1Core._slot_fact_matches(core, "birthdate", "your name is Travis.") is False

    assert MK1Core._slot_fact_matches(core, "favorite_color", "your favorite color is imperial purple.") is True
    assert MK1Core._slot_fact_matches(core, "favorite_color", "your eye color is deep water blue.") is False


def test_lookup_slot_fact_falls_through_when_semantic_hits_wrong_slot():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(run=lambda *_args, **_kwargs: {"ok": True, "results": []})

    def fake_search_memory_candidates(probes, user_only, top_k=8):
        # Simulate semantic search returning only a non-name fact for name probes.
        return ["your favorite color is imperial purple."]

    def fake_recent_memory_candidates(user_only, top_k=800):
        if user_only:
            return ["your name is Travis."]
        return []

    core._search_memory_candidates = fake_search_memory_candidates
    core._recent_memory_candidates = fake_recent_memory_candidates

    fact = MK1Core._lookup_slot_fact(core, "name", "what is my name")

    assert fact == "your name is Travis."


def test_build_memory_read_reply_renders_name_value_only():
    core = MK1Core.__new__(MK1Core)

    core._sanitize_unique_facts = lambda lines: ["your name is Travis."]
    core._extract_memory_slots = lambda _q: {
        "name": True,
        "birthdate": False,
        "eye_color": False,
        "favorite_color": False,
        "age": False,
    }
    core._lookup_slot_fact = lambda _slot, _q: "your name is Travis."
    core._parse_birthdate_from_text = lambda _t: None
    core._compute_age_details = lambda _b: {"ok": False, "now_text": "2026-07-13 00:00"}
    core._normalize_memory_reply_perspective = lambda _q, t: t

    reply = MK1Core._build_memory_read_reply(
        core,
        messages=[],
        user_query="YOU what is my name ?",
        formatted="your name is Travis.",
    )

    assert "Name: Travis." in reply


def test_build_memory_read_reply_allows_transformative_style_with_locked_facts():
    core = MK1Core.__new__(MK1Core)

    core._sanitize_unique_facts = lambda lines: ["your eye color is deep water blue."]
    core._extract_memory_slots = lambda _q: {
        "name": False,
        "birthdate": False,
        "eye_color": True,
        "favorite_color": False,
        "age": False,
    }
    core._lookup_slot_fact = lambda _slot, _q: "your eye color is deep water blue."
    core._parse_birthdate_from_text = lambda _t: None
    core._normalize_memory_reply_perspective = lambda _q, t: t
    core._is_transformative_memory_query = lambda _q: True
    core._render_memory_transform_reply = lambda **_kwargs: "Deep-water blue like open ocean under storm light."

    reply = MK1Core._build_memory_read_reply(
        core,
        messages=[],
        user_query="can you relate my eye color to something else",
        formatted="your eye color is deep water blue.",
    )

    assert "Your eye color is deep water blue." in reply
    assert "open ocean" in reply
    assert "Eye color:" not in reply


def test_enforce_memory_fact_anchors_prepends_missing_anchor():
    core = MK1Core.__new__(MK1Core)

    reply = MK1Core._enforce_memory_fact_anchors(
        core,
        reply_text="Deep-water blue like open ocean under storm light.",
        anchors=["Your eye color is deep water blue."],
    )

    assert reply.startswith("Your eye color is deep water blue.")
    assert "open ocean" in reply


def test_anchor_line_satisfied_accepts_equivalent_name_and_eye_phrases():
    core = MK1Core.__new__(MK1Core)

    assert MK1Core._anchor_line_satisfied(core, "Your name is Travis.", "Aaaah, you're Travis!") is True
    assert MK1Core._anchor_line_satisfied(core, "Your eye color is deep water blue.", "Your eyes are deep water blue, like open ocean.") is True


def test_authoritative_tags_for_profile_fact_marks_identity_slots():
    core = MK1Core.__new__(MK1Core)

    tags = MK1Core._authoritative_tags_for_fact(core, "my eye color is deep water blue")

    assert "user_memory" in tags
    assert "profile_auto" in tags
    assert "authoritative_profile" in tags


def test_wants_grounded_tool_analysis_detects_advisory_prompt():
    core = MK1Core.__new__(MK1Core)

    assert MK1Core._wants_grounded_tool_analysis(core, "read this file and suggest improvements") is True
    assert MK1Core._wants_grounded_tool_analysis(core, "read file E:/foo.txt") is False


def test_build_turn_working_memory_prioritizes_authoritative_facts():
    core = MK1Core.__new__(MK1Core)

    class MemoryStub:
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags:
                return [
                    {"text": "your name is Travis.", "kind": "fact", "tags": ["authoritative_profile"]},
                    {"text": "Mina is loyal to Travis.", "kind": "fact", "tags": ["authoritative_identity"]},
                ]
            return [
                {"text": "hardware_snapshot::CPU Ryzen GPU RTX", "kind": "fact", "tags": ["tool_result"]},
            ]

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            return []

    core.memory = MemoryStub()

    block = MK1Core._build_turn_working_memory(core, "who am i")

    assert block.startswith("Turn working memory (authoritative DB facts):")
    assert "your name is Travis." in block
    assert "Mina is loyal to Travis." in block


def test_build_memory_read_reply_reinforces_used_profile_fact():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    core._sanitize_unique_facts = lambda lines: ["your eye color is deep water blue."]
    core._extract_memory_slots = lambda _q: {
        "name": False,
        "birthdate": False,
        "eye_color": True,
        "favorite_color": False,
        "age": False,
    }
    core._lookup_slot_fact = lambda _slot, _q: "your eye color is deep water blue."
    core._parse_birthdate_from_text = lambda _t: None
    core._normalize_memory_reply_perspective = lambda _q, t: t
    core._is_transformative_memory_query = lambda _q: False

    reply = MK1Core._build_memory_read_reply(
        core,
        messages=[],
        user_query="tell me about my eyes",
        formatted="your eye color is deep water blue.",
    )

    assert "Eye color:" in reply
    assert any(t["text"] == "your eye color is deep water blue." for t in core.memory.touches)


def test_remember_tool_output_persists_hardware_snapshot():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    MK1Core._remember_tool_output(
        core,
        tool_name="ps_run",
        user_input="what cpu and gpu do i have",
        tool_args={"script": "Get-ComputerInfo"},
        result={"ok": True, "result": {"stdout": "CPU: Ryzen\nGPU: RTX 4080", "stderr": "", "exit_code": 0}},
        formatted="CPU: Ryzen\nGPU: RTX 4080",
    )

    assert any("hardware_snapshot::" in w["text"] for w in core.memory.writes)
    assert any("suggestion_context" in w["tags"] for w in core.memory.writes)


def test_explicit_hardware_command_persists_snapshot_for_followup_recall():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    MK1Core._remember_tool_output(
        core,
        tool_name="ps_run",
        user_input="Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM",
        tool_args={"script": "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM"},
        result={"ok": True, "result": {"stdout": "AMD Radeon RX 6700 XT"}},
        formatted="AMD Radeon RX 6700 XT",
    )

    assert any("hardware_snapshot::" in write["text"] for write in core.memory.writes)


def test_hardware_followup_context_surfaces_prior_snapshot_without_running_tools():
    class HardwareMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags == ["hardware_snapshot"]:
                return [{"text": "hardware_snapshot::ram probe => FreePhysicalMemory: 5061880"}]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = HardwareMemory()

    context = MK1Core._build_hardware_followup_context(core, "we need to do something about the RAM situation")

    assert "Hardware snapshot memory:" in context
    assert "FreePhysicalMemory" in context


def test_memory_hygiene_requires_explicit_authorization_for_mutation():
    class HygieneMemory:
        def memory_hygiene(self, dry_run=True, max_delete=500, authorized_apply=False):
            if not dry_run and not authorized_apply:
                return {"ok": False, "error": "memory_mutation_requires_explicit_authorization"}
            return {"ok": True, "result": {}}

    core = MK1Core.__new__(MK1Core)
    core.memory = HygieneMemory()

    result = MK1Core._memory_tidy(core, dry_run=False)

    assert result["ok"] is False
    assert result["error"] == "memory_mutation_requires_explicit_authorization"


def test_memory_evidence_report_is_read_only_and_conservative(tmp_path):
    from memory.mk1_memory import MK1Memory

    db_path = tmp_path / "evidence.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, kind TEXT, tags TEXT, timestamp REAL)"
    )
    now = time.time()
    rows = [
        (1, "Verified CPU probe", "fact", json.dumps(["verified", "source:ps_run"]), now),
        (2, "Older bridge decision", "note", json.dumps(["conversation_digest"]), now - 40 * 86400),
        (3, "Conflicting identity claim", "fact", json.dumps(["conflicting"]), now),
        (4, "Unclassified interaction", "interaction", json.dumps([]), now),
    ]
    conn.executemany("INSERT INTO memories VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    memory = MK1Memory.__new__(MK1Memory)
    memory.db_path = str(db_path)
    memory._begin_memory_operation = lambda *_args, **_kwargs: True
    memory._memory_operation_serial_lock = threading.RLock()
    memory._set_error = lambda *_args, **_kwargs: None

    before = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    report = memory.memory_evidence_report()
    after = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    assert report["ok"] is True
    assert report["read_only"] is True
    assert report["counts"] == {"verified": 1, "historical": 1, "unverified": 1, "conflicting": 1}
    verified = next(record for record in report["records"] if record["id"] == 1)
    historical = next(record for record in report["records"] if record["id"] == 2)
    assert verified["source_evidence"] == ["source:ps_run"]
    assert historical["freshness"] == "stale"
    assert before == after == 4


def test_long_term_archive_context_surfaces_older_project_decisions():
    class ArchiveMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags and "conversation_digest" in include_tags:
                return [{
                    "text": "Conversation digest 2026-06-01. Ostranauts bridge decision: keep telemetry read-only.",
                    "tags": ["conversation_digest", "user_memory"],
                }]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = ArchiveMemory()

    context = MK1Core._build_long_term_archive_context(
        core,
        "what did we decide about the Ostranauts bridge months ago",
    )

    assert "Historical memory archive:" in context
    assert "telemetry read-only" in context
    assert "not current game state" in context
    assert "live telemetry or source files" in context


def test_long_term_archive_context_marks_missing_history_as_unverified():
    class EmptyArchiveMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags:
                return []
            return super().search(query, top_k, include_kinds, include_tags, since_ts)

    core = MK1Core.__new__(MK1Core)
    core.memory = EmptyArchiveMemory()

    context = MK1Core._build_long_term_archive_context(
        core,
        "what did we decide about the Ostranauts bridge months ago",
    )

    assert "no grounded matching record was found" in context
    assert "Do not invent historical details" in context


def test_long_term_archive_context_ignores_semantic_noise_without_topic_overlap():
    class NoisyArchiveMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags:
                return [{"text": "Unrelated memory about a weather forecast", "tags": ["user_memory"]}]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = NoisyArchiveMemory()

    context = MK1Core._build_long_term_archive_context(
        core,
        "what did we decide about the Ostranauts bridge months ago",
    )

    assert "no grounded matching record was found" in context


def test_long_term_archive_context_is_read_only():
    class ReadOnlyArchiveMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags and "conversation_digest" in include_tags:
                return [{
                    "text": "Conversation digest 2026-06-01. Ostranauts bridge used live telemetry.",
                    "tags": ["conversation_digest", "user_memory"],
                }]
            return []

        def add_memory(self, *args, **kwargs):
            raise AssertionError("archive verification must not write memory")

        def delete_memory_ids(self, *args, **kwargs):
            raise AssertionError("archive verification must not delete memory")

    core = MK1Core.__new__(MK1Core)
    core.memory = ReadOnlyArchiveMemory()

    context = MK1Core._build_long_term_archive_context(
        core,
        "what did we decide about the Ostranauts bridge months ago",
    )

    assert "live telemetry" in context


def test_ostranauts_context_includes_historical_archive_lane():
    class OstranautsArchiveMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags and "conversation_digest" in include_tags:
                return [{"text": "Conversation digest: Ostranauts bridge telemetry stayed read-only."}]
            if include_tags == ["ostranauts"]:
                return []
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = OstranautsArchiveMemory()

    context = MK1Core._build_ostranauts_memory_context(
        core,
        "what did we decide about the Ostranauts bridge months ago",
    )

    assert "Historical memory archive:" in context
    assert "telemetry stayed read-only" in context
    assert "not current game state" in context


def test_remember_tool_output_stores_hardware_snapshot_as_fact():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    MK1Core._remember_tool_output(
        core,
        tool_name="ps_run",
        user_input="what cpu and gpu do i have",
        tool_args={"script": "Get-ComputerInfo"},
        result={"ok": True, "result": {"stdout": "CPU: Ryzen\nGPU: RTX 4080", "stderr": "", "exit_code": 0}},
        formatted="CPU: Ryzen\nGPU: RTX 4080",
    )

    assert any(w.get("kind") == "fact" for w in core.memory.writes)


def test_remember_tool_output_persists_project_test_summary():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    MK1Core._remember_tool_output(
        core,
        tool_name="__project_test_run__",
        user_input="run tests",
        tool_args={"request": "run tests"},
        result={
            "ok": True,
            "result": {
                "command": "pytest -q",
                "project_path": "E:/Mina_MK1",
                "exit_code": 0,
                "changed_files": ["agent/core.py"],
                "stdout": "17 passed",
                "stderr": "",
            },
        },
        formatted="Exit code: 0\n17 passed",
    )

    assert any("test_run_result::" in w["text"] for w in core.memory.writes)
    assert any("test_result" in w["tags"] for w in core.memory.writes)


def test_seed_runtime_capability_snapshot_writes_baseline_record():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.config = SimpleNamespace(get=lambda *_args, **_kwargs: _kwargs.get("default", _args[2] if len(_args) > 2 else None))
    core.tools = SimpleNamespace(tools={"ps_run": object(), "code_execute": object()})
    core.workspace_root = "E:/workspace"
    core._projects_root = lambda: "E:/workspace/projects"
    core.fast_command_mode = True
    core.fast_command_skip_context = False
    core.maintenance_min_interval_seconds = 20
    core.reflex_enabled = True
    core.reflex_requires_prefix = False
    core.reflex_prefixes = ["/tool"]

    MK1Core._seed_runtime_capability_snapshot(core)

    snap_rows = [w for w in core.memory.writes if str(w.get("text", "")).startswith("mina_capability_snapshot::")]
    assert snap_rows
    assert any("capability_snapshot" in (w.get("tags") or []) for w in snap_rows)

    active_rows = [w for w in core.memory.writes if str(w.get("text", "")).startswith("active_tools::")]
    assert active_rows
    assert any("active_tools" in (w.get("tags") or []) for w in active_rows)


def test_seed_runtime_capability_snapshot_writes_delta_when_changed():
    class DeltaMemory(FakeMemory):
        def __init__(self, prior_text):
            super().__init__()
            self.prior_text = prior_text

        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            if include_tags and "capability_snapshot" in include_tags:
                return [{"text": self.prior_text}]
            return []

    old_payload = {
        "identity": "Mina",
        "workspace_root": "E:/workspace",
        "projects_root": "E:/workspace/projects",
        "model": {"default_model": "old-model", "switch_allowed": [], "vision_max_tokens": 256, "vision_reasoning_effort": "none"},
        "memory": {"db_path": "memory/memory.db", "faiss_small_path": "memory/faiss_small.index", "faiss_base_path": "memory/faiss_base.index", "backup_dir": "memory/backups", "auto_backup_every_writes": 100, "auto_backup_every_seconds": 3600},
        "performance": {"fast_command_mode": True, "fast_command_skip_context": True, "maintenance_min_interval_seconds": 20},
        "reflex": {"enabled": True, "requires_prefix": False, "prefixes": ["/tool"]},
        "tools": {"count": 1, "available": ["ps_run"]},
    }
    old_canonical = json.dumps(old_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    old_digest = hashlib.sha1(old_canonical.encode("utf-8")).hexdigest()
    prior_text = f"mina_capability_snapshot::{old_digest}::{json.dumps(dict(old_payload, timestamp='2026-01-01T00:00:00'), sort_keys=True, ensure_ascii=True)}"

    core = MK1Core.__new__(MK1Core)
    core.memory = DeltaMemory(prior_text)
    core.config = SimpleNamespace(get=lambda *_args, **_kwargs: _kwargs.get("default", _args[2] if len(_args) > 2 else None))
    core.tools = SimpleNamespace(tools={"ps_run": object(), "code_execute": object()})
    core.workspace_root = "E:/workspace"
    core._projects_root = lambda: "E:/workspace/projects"
    core.fast_command_mode = True
    core.fast_command_skip_context = False
    core.maintenance_min_interval_seconds = 20
    core.reflex_enabled = True
    core.reflex_requires_prefix = False
    core.reflex_prefixes = ["/tool"]

    # Force a changed model default to produce a digest delta.
    def cfg_get(section, key, default=None):
        if section == "model" and key == "default_model":
            return "new-model"
        return default

    core.config = SimpleNamespace(get=cfg_get)

    MK1Core._seed_runtime_capability_snapshot(core)

    delta_rows = [w for w in core.memory.writes if str(w.get("text", "")).startswith("mina_capability_delta::")]
    assert delta_rows
    assert any("capability_delta" in (w.get("tags") or []) for w in delta_rows)


def test_seed_runtime_capability_snapshot_does_not_duplicate_when_unchanged():
    # Regression: record_text embeds a fresh per-run timestamp, so exact-text dedup
    # never matched and spammed a new row on every restart even with identical config
    # (confirmed live: 400+ duplicate rows). Dedup must compare by digest instead.
    class SameDigestMemory(FakeMemory):
        def __init__(self, prior_text):
            super().__init__()
            self.prior_text = prior_text

        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            if include_tags and "capability_snapshot" in include_tags:
                return [{"text": self.prior_text}]
            return []

    core = MK1Core.__new__(MK1Core)
    fixed_payload = {"identity": "Mina", "workspace_root": "E:/workspace"}
    core._build_runtime_capability_snapshot = lambda: dict(fixed_payload, timestamp="2026-08-13T00:00:00")

    canonical = json.dumps(fixed_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    # Same digest, but a different (older) embedded timestamp -- simulates a prior restart.
    prior_text = f"mina_capability_snapshot::{digest}::{json.dumps(dict(fixed_payload, timestamp='2020-01-01T00:00:00'), sort_keys=True, ensure_ascii=True)}"

    core.memory = SameDigestMemory(prior_text)

    MK1Core._seed_runtime_capability_snapshot(core)

    snap_rows = [w for w in core.memory.writes if str(w.get("text", "")).startswith("mina_capability_snapshot::")]
    assert snap_rows == []


def test_seed_autonomy_awareness_memory_writes_capability_fact():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    MK1Core._seed_autonomy_awareness_memory(core)

    rows = [w for w in core.memory.writes if "autonomy_awareness" in (w.get("tags") or [])]
    assert rows
    assert "can work on my own code" in rows[0]["text"].lower() or "self-editing capability" in rows[0]["text"].lower()
    assert "watchdog" in rows[0]["text"].lower()


def test_seed_autonomy_awareness_memory_does_not_duplicate_existing_fact():
    class ExistingAwarenessMemory(FakeMemory):
        def find_memory_id_by_text(self, text, include_kinds=None, include_tags=None):
            self.lookups.append({"text": text, "include_kinds": include_kinds, "include_tags": include_tags})
            return 1

    core = MK1Core.__new__(MK1Core)
    core.memory = ExistingAwarenessMemory()

    MK1Core._seed_autonomy_awareness_memory(core)

    rows = [w for w in core.memory.writes if "autonomy_awareness" in (w.get("tags") or [])]
    assert rows == []


def test_build_autonomy_awareness_context_surfaces_fact_for_self_edit_question():
    class AwarenessMemory(FakeMemory):
        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            if include_tags == ["autonomy_awareness"]:
                return [{"text": "Mina autonomous self-improvement capability: I can work on my own code."}]
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = AwarenessMemory()

    out = MK1Core._build_autonomy_awareness_context(core, "are you aware that you can edit your own source code autonomously?")

    assert "autonomous self-improvement capability" in out


def test_build_autonomy_awareness_context_empty_for_unrelated_question():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    out = MK1Core._build_autonomy_awareness_context(core, "what's the weather like today?")

    assert out == ""


def test_build_turn_working_memory_does_not_promote_runtime_records_to_identity():
    class WorkingMemoryFake(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            return [
                {"text": "hardware_snapshot::what cpu are you on? => CPU Ryzen 7"},
                {"text": "test_run_result::cmd=pytest -q ; ok=False ; error=pytest_timeout"},
                {"text": "normal chatter line"},
            ]

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = WorkingMemoryFake()

    out = MK1Core._build_turn_working_memory(core, "run tests")

    assert out == ""


def test_build_turn_working_memory_does_not_promote_capability_logs_to_identity():
    class WorkingMemorySemanticFake(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            return [{"text": "mina_capability_delta::old->new::changed tools.count: 5 -> 6"}]

    core = MK1Core.__new__(MK1Core)
    core.memory = WorkingMemorySemanticFake()

    out = MK1Core._build_turn_working_memory(core, "what changed")

    assert out == ""


def test_clean_response_text_strips_internal_db_tone_instruction_line():
    core = MK1Core.__new__(MK1Core)

    cleaned = MK1Core._clean_response_text(
        core,
        "- Use the DB-backed Mina tone.\nMina here, verified output follows.",
    )

    assert "db-backed mina tone" not in cleaned.lower()
    assert "verified output follows" in cleaned
