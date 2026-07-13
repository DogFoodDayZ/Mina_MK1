import sys
from types import SimpleNamespace

sys.path.insert(0, 'e:/Mina_MK1')

from agent.core import MK1Core


class FakeMemory:
    def __init__(self):
        self.writes = []
        self.promotions = []
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
