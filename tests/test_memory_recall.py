import sys
import json
import hashlib
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


def test_build_context_combines_short_and_long_recall():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()

    context = MK1Core.build_context(core, "what system am i on")

    assert "Short-term recall:" in context
    assert "Long-term recall:" in context
    assert context.count("User asked about GPU info") == 1
    assert "Host is Windows" in context


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


def test_process_stores_turn_memory_for_reflex_reply():
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

    result = MK1Core.process(core, "hardware info")

    assert result["reply"] == "Tool says hi"
    assert len(core.memory.writes) == 2
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
    core._store_turn_memory = lambda user_input, assistant_text, retry_guided=False: None
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
    core._store_turn_memory = lambda user_input, assistant_text, retry_guided=False: None
    core.build_context = lambda _query: ""
    core._build_ostranauts_memory_context = lambda _query: (
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

        result = MK1Core.process(core, "what should I try at the reactor panel")
    finally:
        core_module.build_ostranauts_context = original_build
        core_module.is_ostranauts_query = original_is_query

    assert result["reply"] == "ok"
    system_messages = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
    assert any("Learned lessons:" in msg for msg in system_messages)
    assert any("reactor control panel path first" in msg for msg in system_messages)


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
    lesson = core.memory.writes[2]
    assert lesson["kind"] == "note"
    assert "learning_lesson" in lesson["tags"]
    assert "retry_attempt" in lesson["tags"]
    assert "Retry attempt:" in lesson["text"]
    assert "checked the local status endpoint instead" in lesson["text"]


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


def test_detect_system_question_does_not_force_ps_run():
    # Contract update 2026-08-05: hardware/system keyword routing removed — model decides
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, _args = MK1Core.detect_tool_intent(core, "what cpu are you on?")

    assert tool_name is None


def test_hardware_question_is_command_like_for_reflex_execution():
    core = MK1Core.__new__(MK1Core)

    assert MK1Core._is_command_like_query(core, "how much physical ram is on a rx 6700 tx") is True


def test_detect_time_question_does_not_force_ps_run():
    # Contract update 2026-08-05: hardware/system keyword routing removed — model decides
    core = MK1Core.__new__(MK1Core)

    tool_name, _args = MK1Core.detect_tool_intent(core, "what time is it?")

    assert tool_name is None


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


def test_send_text_to_brother_routes_to_external_texting_limit():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={})
    core._intent_alias_cache = {}
    core._pending_actions = PendingActionStore()

    tool_name, args = MK1Core.detect_tool_intent(core, "send my brother a hello text from Mina")

    assert tool_name == "__capability_limit__"
    assert "cannot send real texts" in args["message"]


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

    assert "dirty with modified and untracked files" in out
    assert "VERIFIED OUTPUT:" in out
    system_messages = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
    assert any("concise summary of the verified state" in msg for msg in system_messages)


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

    assert "recall should be available" in out
    assert "VERIFIED OUTPUT:" in out
    system_messages = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
    assert any("concise summary of the verified state" in msg for msg in system_messages)


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


def test_build_turn_working_memory_includes_durable_tool_records():
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

    assert "Turn working memory" in out
    assert "hardware_snapshot::" in out
    assert "test_run_result::" in out
    assert "normal chatter line" not in out


def test_build_turn_working_memory_uses_semantic_suggestion_context_hits():
    class WorkingMemorySemanticFake(FakeMemory):
        def recent_memories(self, top_k=6, include_kinds=None, include_tags=None):
            return []

        def search(self, query, top_k=5, include_kinds=None, include_tags=None, since_ts=None):
            return [{"text": "mina_capability_delta::old->new::changed tools.count: 5 -> 6"}]

    core = MK1Core.__new__(MK1Core)
    core.memory = WorkingMemorySemanticFake()

    out = MK1Core._build_turn_working_memory(core, "what changed")

    assert "mina_capability_delta::" in out


def test_enforce_tool_outcome_truth_surfaces_last_failed_tool():
    core = MK1Core.__new__(MK1Core)

    final = MK1Core._enforce_tool_outcome_truth(
        core,
        "Everything completed.",
        [
            ("ps_run", {"ok": True, "result": {"stdout": "ok"}, "error": None}),
            ("ps_run", {"ok": False, "result": None, "error": "permission_denied"}),
        ],
    )

    low = final.lower()
    assert "powershell execution failed" in low
    assert "permission_denied" in low


def test_enforce_tool_outcome_truth_keeps_text_when_no_failures():
    core = MK1Core.__new__(MK1Core)

    final = MK1Core._enforce_tool_outcome_truth(
        core,
        "Everything completed.",
        [
            ("ps_run", {"ok": True, "result": {"stdout": "ok"}, "error": None}),
        ],
    )

    assert final == "Everything completed."


def test_clean_response_text_strips_internal_db_tone_instruction_line():
    core = MK1Core.__new__(MK1Core)

    cleaned = MK1Core._clean_response_text(
        core,
        "- Use the DB-backed Mina tone.\nMina here, verified output follows.",
    )

    assert "db-backed mina tone" not in cleaned.lower()
    assert "verified output follows" in cleaned


def test_verified_execution_fallback_forces_real_tool_reply_when_output_is_unverified_claim():
    core = MK1Core.__new__(MK1Core)
    core.detect_tool_intent = lambda _text: ("ps_run", {"script": "python countdown.py"})
    core._plan_tool_action = lambda _text: None
    core._reflex_tools_and_memory = lambda **_kwargs: {
        "choices": [{"message": {"content": "Here's the verified PowerShell output:\n10\n9\n...\n0\nBlast off!"}}]
    }
    core._extract_text = lambda reply: reply["choices"][0]["message"]["content"]

    out = MK1Core._maybe_enforce_verified_execution_reply(
        core,
        messages=[],
        user_input="yes run the script countdown.py",
        assistant_text="```powershell\nPS E:\\workspace> python E:\\workspace\\countdown.py\n10\n9\nBlast off!\n```",
        request_context=None,
    )

    assert out is not None
    assert "verified powershell output" in out.lower()
    assert "blast off" in out.lower()


def test_verified_execution_fallback_refuses_to_claim_output_when_no_runnable_tool_found():
    core = MK1Core.__new__(MK1Core)
    core.detect_tool_intent = lambda _text: ("__clarify_intent__", {"question": "which file?"})
    core._plan_tool_action = lambda _text: None

    out = MK1Core._maybe_enforce_verified_execution_reply(
        core,
        messages=[],
        user_input="run that script",
        assistant_text="```powershell\nPS E:\\workspace> python E:\\workspace\\countdown.py\n10\n9\nBlast off!\n```",
        request_context=None,
    )

    assert out is not None
    assert "cannot claim output" in out.lower()
