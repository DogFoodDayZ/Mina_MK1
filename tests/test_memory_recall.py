import sys
import json
import hashlib
from types import SimpleNamespace

sys.path.insert(0, 'e:/Mina_MK1')

from agent.core import MK1Core


class FakeMemory:
    def __init__(self):
        self.writes = []
        self.promotions = []
        self.touches = []
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


def test_process_stores_turn_memory_for_reflex_reply():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.system_prompt = ""
    core.auto_promote_min_hits = 2
    core.auto_promote_recent_window = 40
    core.auto_promote_semantic_top_k = 8

    core.build_context = lambda _query: ""
    core._reflex_tools_and_memory = lambda messages, reply, user_input: {
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


def test_detect_system_question_routes_to_ps_run():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "what cpu are you on?")

    assert tool_name == "ps_run"
    assert args["script"] == "what cpu are you on?"


def test_detect_time_question_routes_to_ps_run():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "what time is it?")

    assert tool_name == "ps_run"
    assert args["script"] == "what time is it?"


def test_detect_tool_list_routes_tool_inventory_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "YOU tool list")

    assert tool_name == "__tool_list__"
    assert args == {}


def test_tool_list_reflex_returns_inventory_text_directly():
    core = MK1Core.__new__(MK1Core)
    core.memory = FakeMemory()
    core.tools = SimpleNamespace(
        tools={"alpha": object(), "beta": object()},
        get_status=lambda: {"alpha": "ok", "beta": "ok"},
    )

    out = MK1Core._reflex_tools_and_memory(core, messages=[], reply={}, user_input="tool list")
    text = out["choices"][0]["message"]["content"]

    assert text.startswith("Available tools:")
    assert "- alpha" in text
    assert "- beta" in text


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


def test_detect_code_execute_strips_trailing_explanation_phrase():
    core = MK1Core.__new__(MK1Core)

    tool_name, args = MK1Core.detect_tool_intent(core, "run this powershell: Get-Date -Format o and explain what it means")

    assert tool_name == "code_execute"
    assert args["language"] == "powershell"
    assert args["code"] == "Get-Date -Format o"


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
