import json
import socket
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests

import agent.core as core_module
from agent.core import LMStudioClient, MK1Core
from memory.mk1_memory import MK1Memory


USER_GOAL = (
    "I can work with that, If you feel, think or know something must change "
    "explane why. the end goal is a working Mina that remebers and can carry "
    "on a conversation with context even after a restart ."
)
ASSISTANT_PLAN = (
    "Let's first restore conversation continuity using disposable memory, "
    "without changing your live memory."
)


class RecordingModel:
    default_model = "isolated-recording-model"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return {"choices": [{"message": {"content": self.answer}}]}

    def chat_stream(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        yield self.answer


@pytest.fixture
def isolated_core(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Continuity tests must not start Mina or access the network.")

    monkeypatch.setattr(MK1Core, "__init__", forbidden)
    monkeypatch.setattr(MK1Memory, "__init__", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(core_module.threading.Thread, "start", forbidden)
    monkeypatch.setattr(core_module.subprocess, "Popen", forbidden)
    monkeypatch.setattr(core_module, "build_ostranauts_context", lambda: "")

    db_path = tmp_path / "conversation.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "text TEXT, kind TEXT, tags TEXT, timestamp REAL)"
        )

    def write(text, kind="interaction", tags=None, timestamp=None, **kwargs):
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO memories(text, kind, tags, timestamp) VALUES (?, ?, ?, ?)",
                (text, kind, json.dumps(tags or []), 100.0 if timestamp is None else timestamp),
            )
            return cur.lastrowid

    def make_core(answer="We can continue with that plan."):
        memory = MK1Memory.__new__(MK1Memory)
        memory.db_path = str(db_path)
        memory.add_memory = write
        memory.auto_promote_short_term = lambda **kwargs: []
        memory.search = lambda query, top_k=5, include_kinds=None, include_tags=None, since_ts=None: (
            memory._sqlite_keyword_search(query, top_k, include_kinds, include_tags, since_ts)
        )
        core = MK1Core.__new__(MK1Core)
        core.memory = memory
        core.model = RecordingModel(answer)
        core.system_prompt = "You are Mina."
        core.tools = SimpleNamespace(get_tool_schemas=lambda: [], tools={}, run=forbidden)
        core.action_mode_strictness = "balanced"
        core.auto_promote_min_hits = 2
        core.auto_promote_recent_window = 40
        core.auto_promote_semantic_top_k = 8
        core._correct_contextual_transcript = lambda text: text
        core._advance_turn_clock = lambda: {"turn": 1}
        core._refresh_idle_checkin_state = lambda: None
        core._consume_idle_checkin_prompt = lambda: ""
        core._consume_away_nag_prompt = lambda: ""
        core._register_user_activity = lambda: None
        core._maybe_run_maintenance_tick = lambda: None
        core._build_process_memory_context = lambda query: ("Older factual recall.", "", "")
        core._build_due_task_prompt = lambda: ""
        core._format_turn_clock_line = lambda clock: "Turn 1"
        core._interface_label_line = lambda context: "Interface: isolated test"
        core._interface_surface_line = lambda context: "Surface: isolated test"
        core._should_run_reflex = lambda text: False
        core._maybe_rescue_chat_suggested_action = lambda **kwargs: None
        core._remember_draft_artifact_from_turn = lambda *args: None
        core._store_active_task_heartbeat = lambda *args: None
        core._maybe_store_learning_memory = lambda *args, **kwargs: None
        core._extract_personal_fact = lambda text: None
        return core

    return make_core, write, db_path


def reply(core, text, streaming):
    if streaming:
        return "".join(core.process_stream(text))
    return core.process(text)["reply"]


@pytest.mark.parametrize("streaming", [False, True], ids=["text", "stream"])
def test_transcript_followup_receives_both_sides_after_recreating_core(isolated_core, streaming):
    make_core, _, db_path = isolated_core
    first = make_core(ASSISTANT_PLAN)
    assert reply(first, USER_GOAL, streaming) == ASSISTANT_PLAN
    del first

    restored = make_core("I'll start by checking the isolated conversation path.")
    followup = "ok lets proceed"
    answer = reply(restored, followup, streaming)
    assert answer == "I'll start by checking the isolated conversation path."
    assert answer != ASSISTANT_PLAN
    messages = restored.model.calls[0]
    conversation = [m for m in messages if m["role"] != "system"]
    assert conversation == [
        {"role": "user", "content": USER_GOAL},
        {"role": "assistant", "content": ASSISTANT_PLAN},
        {"role": "user", "content": followup},
    ]
    assert all(ASSISTANT_PLAN not in m["content"] for m in messages if m["role"] == "system")
    assert any("Older factual recall." in m["content"] for m in messages if m["role"] == "system")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 4


def test_history_excludes_bookkeeping_and_keeps_chronological_roles(isolated_core):
    make_core, write, _ = isolated_core
    write("old orphan reply", tags=["short_term", "assistant_turn"], timestamp=1)
    write("hay little mouse.", tags=["short_term", "user_turn"], timestamp=2)
    write("Tool call log", kind="tool", tags=["tool_call"], timestamp=3)
    write("Recent file operation", tags=["short_term", "tool_result", "file_operation"], timestamp=4)
    write("synthetic probe", tags=["short_term", "user_turn", "test_data"], timestamp=5)
    write("agent probe", tags=["short_term", "assistant_turn", "source:agent_test"], timestamp=6)
    write("unlabelled legacy row", tags=["short_term"], timestamp=7)
    write("invalid role row", tags=["short_term", "user_turn", "assistant_turn"], timestamp=8)
    write("Hey! I'm here.", tags=["short_term", "assistant_turn"], timestamp=9)
    core = make_core()
    assert core._build_recent_conversation_messages() == [
        {"role": "user", "content": "hay little mouse."},
        {"role": "assistant", "content": "Hey! I'm here."},
    ]


def test_history_is_bounded_and_preserves_repeated_turns(isolated_core):
    make_core, write, _ = isolated_core
    for i in range(20):
        role = "user_turn" if i % 2 == 0 else "assistant_turn"
        write("oh." if i % 2 == 0 else "I'm listening.", tags=["short_term", role], timestamp=i)
    history = make_core()._build_recent_conversation_messages()
    assert len(history) == 12
    assert [m["role"] for m in history] == ["user", "assistant"] * 6
    assert sum(m["content"] == "oh." for m in history) == 6


def test_history_omits_older_messages_instead_of_slicing_them(isolated_core):
    make_core, write, _ = isolated_core
    for i in range(12):
        role = "user_turn" if i % 2 == 0 else "assistant_turn"
        write(str(i) + "x" * 1798, tags=["short_term", role], timestamp=i)
    history = make_core()._build_recent_conversation_messages()
    assert sum(len(m["content"]) for m in history) <= 12000
    assert history[0]["role"] == "user"
    assert history[-1]["content"].startswith("11")
    assert all(len(m["content"]) >= 1799 for m in history)


def test_prior_assistant_tool_example_is_context_not_an_executed_call(isolated_core):
    make_core, write, _ = isolated_core
    write("Show me a tool-call example.", tags=["short_term", "user_turn"])
    example = '```json\n{"tool": "ps_run", "args": {"script": "Get-Date"}}\n```'
    write(example, tags=["short_term", "assistant_turn"])
    core = make_core("That was an example, not a new action.")
    assert reply(core, "what did you mean?", False) == "That was an example, not a new action."
    assert core.model.calls[0][-2] == {"role": "assistant", "content": example}


def test_client_role_normalization_preserves_conversation_order(isolated_core):
    make_core, write, _ = isolated_core
    write(USER_GOAL, tags=["short_term", "user_turn"])
    write(ASSISTANT_PLAN, tags=["short_term", "assistant_turn"])
    core = make_core()
    reply(core, "ok lets proceed", False)
    client = LMStudioClient({"context_window": 32768, "max_tokens": 4096})
    messages = client._trim_messages_to_budget(
        client._coalesce_system_messages(core.model.calls[0])
    )
    assert [m for m in messages if m["role"] != "system"] == [
        {"role": "user", "content": USER_GOAL},
        {"role": "assistant", "content": ASSISTANT_PLAN},
        {"role": "user", "content": "ok lets proceed"},
    ]


@pytest.mark.parametrize("streaming", [False, True], ids=["text", "stream"])
def test_empty_history_still_answers_current_message(isolated_core, streaming):
    make_core, _, _ = isolated_core
    core = make_core("Hey, I'm here.")
    assert reply(core, "hay little mouse.", streaming) == "Hey, I'm here."
    assert [m for m in core.model.calls[0] if m["role"] != "system"] == [
        {"role": "user", "content": "hay little mouse."},
    ]


@pytest.mark.parametrize("streaming", [False, True], ids=["text", "stream"])
def test_older_assistant_detail_reaches_model_after_recent_window_and_recreation(isolated_core, streaming):
    make_core, write, _ = isolated_core
    # Transcript-grounded question; non-intimate fixture details test the same recall path.
    question = "so remind me what color your eyes are ?"
    answer = "My eyes are green, like a mossy forest."
    write(question, tags=["short_term", "user_turn"], timestamp=1)
    write(answer, tags=["short_term", "assistant_turn"], timestamp=2)
    write("Actually, let's make that amber instead.", tags=["short_term", "user_turn"], timestamp=3)
    write("All right, my eye color is amber in this conversation.", tags=["short_term", "assistant_turn"], timestamp=4)
    for i in range(20):
        write(
            "Let's discuss the delivery route." if i % 2 == 0 else "The next stop is nearby.",
            tags=["short_term", "user_turn" if i % 2 == 0 else "assistant_turn"],
            timestamp=10 + i,
        )
    del_core = make_core()
    del del_core
    restored = make_core("I'll use the earlier exchange rather than guess.")
    assert reply(restored, "so what color are your eyes again ?", streaming) == restored.model.answer
    messages = restored.model.calls[0]
    evidence = [m["content"] for m in messages if m["content"].startswith("Retrieved older conversation")]
    assert len(evidence) == 1
    assert question in evidence[0]
    assert answer in evidence[0]
    assert "amber" in evidence[0]
    assert evidence[0].index(answer) < evidence[0].index("Actually")
    assert '"role": "assistant"' in evidence[0]
    assert answer not in [m["content"] for m in messages if m["role"] == "assistant"]


def test_older_recall_ignores_unrelated_and_test_records(isolated_core):
    make_core, write, _ = isolated_core
    write("synthetic eyes", tags=["short_term", "user_turn", "test_data"], timestamp=1)
    write("probe eyes", tags=["short_term", "assistant_turn", "source:agent_test"], timestamp=2)
    for i in range(14):
        write("Delivery route.", tags=["short_term", "user_turn"], timestamp=3 + i)
    core = make_core()
    assert core._build_older_conversation_context("what color are your eyes?", []) == ""
    core.memory.search = lambda *args, **kwargs: [
        {"id": 3, "timestamp": 3, "text": "Delivery route.", "tags": ["short_term", "user_turn"]},
    ]
    assert core._build_older_conversation_context("what color are your eyes?", []) == ""


def test_older_recall_deduplicates_bounds_and_does_not_execute_examples(isolated_core):
    make_core, write, _ = isolated_core
    write("Show a tool example.", tags=["short_term", "user_turn"], timestamp=1)
    example = '```json\n{"tool":"ps_run","args":{"script":"Get-Date"}}\n```'
    write(example, tags=["short_term", "assistant_turn"], timestamp=2)
    for i in range(18):
        write(
            "Another tool example " + "x" * 1500,
            tags=["short_term", "user_turn" if i % 2 == 0 else "assistant_turn"],
            timestamp=3 + i,
        )
    for i in range(14):
        write("A new topic.", tags=["short_term", "user_turn"], timestamp=100 + i)
    core = make_core()
    context = core._build_older_conversation_context("Show a tool example.", [])
    assert len(context) <= 6500
    assert context.count('"id": 1,') == 1
    assert '"role": "assistant"' in context
    assert reply(core, "Show a tool example.", False) == core.model.answer


def test_neighborhood_read_is_ordered_read_only_and_skips_non_interactions(isolated_core):
    make_core, write, db_path = isolated_core
    write("earlier question", tags=["short_term", "user_turn"])
    write("tool log", kind="tool", tags=["tool_call"])
    anchor_id = write("earlier answer", tags=["short_term", "assistant_turn"])
    write("correction", tags=["short_term", "user_turn"])
    write("corrected answer", tags=["short_term", "assistant_turn"])
    core = make_core()
    before = db_path.read_bytes()
    rows = core.memory.conversation_neighborhood(anchor_id)
    assert [row["text"] for row in rows] == [
        "earlier question", "earlier answer", "correction", "corrected answer",
    ]
    assert db_path.read_bytes() == before
    assert core.memory.conversation_neighborhood(999999) == []
    assert core.memory.conversation_neighborhood(2) == []


def test_older_recall_does_not_duplicate_recent_dialogue(isolated_core):
    make_core, write, _ = isolated_core
    write("what color are your eyes?", tags=["short_term", "user_turn"])
    write("My eyes are green.", tags=["short_term", "assistant_turn"])
    core = make_core()
    recent = core._recent_conversation_records()
    assert core._build_older_conversation_context("what color are your eyes?", recent) == ""
