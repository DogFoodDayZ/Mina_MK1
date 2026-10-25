"""Travis: 'I talk to mina not command her'; no wording-dependent execution."""
import json
from types import SimpleNamespace

import pytest

import agent.core as core_module
from agent.core import MK1Core
from test_model_decides_tools import ScriptedModel, _core
from tools.file_write import tool_entry as file_write
from tools.ps_run import tool_entry as ps_run


PROMPTS = [
    "hey mina",
    "Amina, what's my eye color?",
    "Meena, assume I was born May 21st, 1971.",
    "what cpu are you on?",
    "TRAVIS hay mina how's the memory ?",
    "mina make a script that counts down from 10 to 0 save it as countdown.py",
    "I want to design a desktop surprise timer before we write code",
    "/tool run python -c \"print(2 + 2)\"",
]


def forbidden(*args, **kwargs):
    pytest.fail("Normal conversation invoked a keyword route or automatic tool rescue.")


class Model(ScriptedModel):
    def chat_stream(self, messages, **kwargs):
        yield self.chat(messages, **kwargs)["choices"][0]["message"]["content"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prompt", PROMPTS)
def test_only_mina_decides_for_all_wording_and_legacy_switches(prompt, streaming):
    core = _core(Model(["Let's talk about it."]), reflex_enabled=True)
    core._should_run_reflex = forbidden
    core._maybe_rescue_chat_suggested_action = forbidden
    core.detect_tool_intent = forbidden
    core._plan_tool_action = forbidden
    if streaming:
        result = "".join(core.process_stream(prompt))
    else:
        result = core.process(prompt)["reply"]
    assert result == "Let's talk about it."
    assert core.model.calls[0][-1] == {"role": "user", "content": prompt}
    assert core.reflex_calls == core.tool_calls == []


@pytest.mark.parametrize("tool,args", [
    ("web_search", {"query": "AMD GPU specifications"}),
    ("ps_run", {"command": "Get-Date", "timeout": 7}),
    ("powershell", {}),
    ("hardware_info", {}),
    ("play_track", {"name": "example"}),
])
def test_dispatch_never_rewrites_selected_tool_or_arguments(tool, args):
    calls = []
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(
        tools={"spotify_link": object()},
        run=lambda name, arguments: calls.append((name, arguments)) or {
            "ok": False, "result": None, "error": "fixture_failure",
        },
    )
    core._capture_tool_failure_for_learning = lambda **kwargs: None
    core._is_local_hardware_question = forbidden
    name, result, message = core._execute_process_tool_call(
        tool, json.dumps(args), "What CPU and GPU are actually installed in this computer?"
    )
    assert calls == [(tool, args)]
    assert name == tool
    assert result["ok"] is False
    assert "fixture_failure" in message


def prompt_core():
    core = MK1Core.__new__(MK1Core)
    core.system_prompt = "Mina's personality stays intact."
    core._recent_conversation_records = lambda: []
    core._build_older_conversation_context = lambda *args: ""
    core._build_due_task_prompt = lambda: ""
    core._format_turn_clock_line = lambda state: "Current time: fixture"
    core._is_command_like_query = forbidden
    core._is_action_request_like = forbidden
    core._is_operational_incident_report = forbidden
    schemas = [
        {"type": "function", "function": {"name": name, **entry.schema}}
        for name, entry in (("file_write", file_write), ("ps_run", ps_run))
    ]
    core.tools = SimpleNamespace(get_tool_schemas=lambda: schemas)
    return core, schemas


def assemble(core, prompt):
    return core._build_process_messages(
        context="", working_memory="", turn_clock={}, checkin_prompt="",
        away_prompt="", request_context=None, user_input=prompt,
    )[0]


def test_native_protocol_replaces_text_catalogue_without_changing_personality_or_schema():
    core, schemas = prompt_core()
    core.native_tool_calls = True
    before = json.dumps(schemas)
    messages = assemble(core, "read jellybeans.py")
    assert messages[0]["content"] == core.system_prompt
    assert not any("Available tools (JSON Schema arguments)" in m["content"] for m in messages)
    assert "fresh tool result" in messages[-1]["content"]
    public = core._native_tool_schemas()
    assert len(public) == len(schemas)
    assert json.dumps(schemas) == before
    for tool in public:
        assert not {"authorized_write", "auth_token", "token"}.intersection(
            tool["function"]["parameters"]["properties"]
        )


def test_transcript_prompt_cleanup_keeps_recall_once_and_preserves_dialogue():
    """Travis: 'does the core.py still load exess or rendundant info into her propts?'"""
    core, _ = prompt_core()
    core._recent_conversation_records = lambda: [
        {"text": "We are checking image and voice separation.", "tags": ["user_turn"]},
        {"text": "I will keep the paths separate.", "tags": ["assistant_turn"]},
        {"text": "We are checking image and voice separation.", "tags": ["user_turn"]},
    ]
    context = (
        "Short-term recall (what the user said recently):\n"
        "- We are checking image and voice separation.\n\n"
        "Long-term recall:\n"
        "- Travis is the user.\n"
        "- Preserve the selected image.\n\n"
        "Learned lessons:\n"
        "- Preserve the selected image.\n"
    )
    working = "Turn working memory (authoritative DB facts):\n- Travis is the user."
    messages, _ = core._build_process_messages(
        context=context, working_memory=working, turn_clock={},
        checkin_prompt="", away_prompt="", request_context=None,
        user_input="well lets get that cleaned up",
    )
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "We are checking image and voice separation." not in system
    assert system.count("Travis is the user.") == 1
    assert system.count("Preserve the selected image.") == 1
    assert "Short-term recall" not in system
    assert "Learned lessons:" not in system
    assert [m["role"] for m in messages[-3:]] == ["user", "assistant", "user"]
    assert messages[-1] == messages[-3]
    assert messages[0]["content"] == core.system_prompt
    assert context.count("Preserve the selected image.") == 2


def test_recall_cleanup_preserves_corrections_authority_and_retry_guidance():
    core, _ = prompt_core()
    core._recent_conversation_records = lambda: [
        {"text": "Travis is the user.", "tags": ["user_turn"]},
    ]
    context = (
        "Long-term recall:\n- Travis is the user.\n"
        "- Version A is active.\n- Version a is active.\n"
        "- The timeout is 45 seconds.\n- The timeout is 120 seconds.\n\n"
        "Learned lessons:\n- Retry note: prior attempt failed.\n"
        "- Retry note: prior attempt failed.\n"
    )
    messages, retry = core._build_process_messages(
        context=context, working_memory="", turn_clock={}, checkin_prompt="",
        away_prompt="", request_context=None, user_input="well lets get that cleaned up",
    )
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "Travis is the user." in system
    for text in ("Version A", "Version a", "45 seconds", "120 seconds"):
        assert text in system
    assert system.count("Retry note: prior attempt failed.") == 1
    assert retry and system.count(retry) == 1


def test_tools_always_have_complete_public_schemas_without_wording_gates():
    core, schemas = prompt_core()
    before = json.dumps(schemas)
    plain = assemble(core, PROMPTS[0])
    action = assemble(core, PROMPTS[3])
    assert plain == action
    assert plain[0]["content"] == core.system_prompt
    text = "\n".join(message["content"] for message in plain)
    assert "Unknown GUI" not in text and "Visible surface:" not in text
    catalogue = next(m["content"] for m in plain if m["content"].startswith("Available tools"))
    entries = [
        entry for line in catalogue.splitlines()
        if line.startswith('{"tool":') and "parameters" in (entry := json.loads(line))
    ]
    write, ps = entries
    assert write["parameters"]["required"] == ["path", "content"]
    assert write["parameters"]["properties"]["overwrite"]["default"] is True
    assert "auth_token" not in write["parameters"]["properties"]
    assert "authorized_write" not in write["parameters"]["properties"]
    assert ps["parameters"]["properties"]["timeout"]["maximum"] == 60
    assert ps["parameters"]["properties"]["command"]["type"] == "string"
    assert json.dumps(schemas) == before
    call = '```json\n{"tool":"file_write","args":{"path":"countdown.py","content":"print(10)\\n"}}\n```'
    name, args = core._extract_tool_calls_from_text(call)[0]
    assert name == "file_write"
    assert core._parse_tool_args(args) == {"path": "countdown.py", "content": "print(10)\n"}


def test_memory_lane_is_not_selected_by_command_or_game_keywords(monkeypatch):
    core = MK1Core.__new__(MK1Core)
    core.fast_command_mode = core.fast_command_skip_context = True
    core._is_command_like_query = forbidden
    core._build_ostranauts_memory_context = forbidden
    core._build_autonomy_awareness_context = forbidden
    core.build_context = lambda query: f"Relevant recall for {query}"
    core._build_turn_working_memory = lambda query: "Identity"
    monkeypatch.setattr(core_module, "build_ostranauts_context", lambda: "Game telemetry")
    for prompt in PROMPTS:
        assert core._build_process_memory_context(prompt) == (
            f"Relevant recall for {prompt}", "Identity", "Game telemetry",
        )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("interface,expected", [("Mina Mk1 GUI", False), ("Ostranauts GUI", True)])
def test_game_telemetry_belongs_only_to_its_client_surface(streaming, interface, expected):
    core = _core(Model(["Hi!"]), reflex_enabled=True)
    core._build_process_memory_context = lambda text: ("", "", "GAME_TELEMETRY_FIXTURE")
    context = {"interface_name": interface}
    if streaming:
        assert "".join(core.process_stream("hey mina", request_context=context)) == "Hi!"
    else:
        assert core.process("hey mina", request_context=context)["reply"] == "Hi!"
    assert any(m["content"] == "GAME_TELEMETRY_FIXTURE" for m in core.model.calls[0]) is expected


def test_old_runtime_records_are_not_authoritative_identity_on_greetings():
    core = MK1Core.__new__(MK1Core)
    core.memory = SimpleNamespace(
        recent_memories=lambda **kwargs: [
            {"text": "hardware_snapshot::old GPU", "tags": ["hardware_snapshot"]},
            {"text": "test_run_result::old failed test", "tags": ["tool_result"]},
            {"text": "path_alias::old_project", "tags": ["tool_result"]},
            {"text": "Travis prefers concise conversation.", "tags": ["authoritative_profile"]},
        ],
        search=lambda *args, **kwargs: [],
    )
    block = core._build_turn_working_memory("hey mina")
    assert "Travis prefers concise conversation." in block
    assert "hardware_snapshot" not in block
    assert "test_run_result" not in block
    assert "path_alias" not in block


def test_due_reminders_cannot_override_the_current_request():
    core = MK1Core.__new__(MK1Core)
    core.memory = SimpleNamespace(get_due_tasks=lambda **kwargs: [{"text": "Check the garden"}])
    block = core._build_due_task_prompt()
    assert "Check the garden" in block
    assert "before unrelated replies" not in block
    assert "not permission to replace the user's request" in block


def test_task_recall_does_not_search_for_unrelated_tasks_on_every_turn():
    calls = []
    core = MK1Core.__new__(MK1Core)
    core.memory = SimpleNamespace(search=lambda query, **kwargs: calls.append(query) or [])
    assert core._build_active_task_heartbeat_context("hey mina") == ""
    assert calls == ["hey mina"]


def test_bookkeeping_is_not_replayed_as_conversational_facts(monkeypatch):
    core = MK1Core.__new__(MK1Core)
    core.memory = SimpleNamespace(
        recent_memories=lambda **kwargs: [],
        search=lambda query, **kwargs: [
            {"text": "Tool 'file_read' called with args={'path': 'old-file'}"},
            {"text": "hardware_snapshot::old machine"},
            {"text": "path_alias::old project"},
            {"text": "Travis prefers concise conversation."},
        ] if kwargs.get("include_kinds") == ["fact", "tool", "preference", "procedure"] else [],
    )
    for helper in (
        "_build_active_task_heartbeat_context", "_build_hardware_followup_context",
        "_build_long_term_archive_context",
    ):
        monkeypatch.setattr(core, helper, lambda *args, **kwargs: "")
    block = core.build_context("hey mina")
    assert "Travis prefers concise conversation." in block
    assert "Tool '" not in block
    assert "hardware_snapshot::" not in block
    assert "path_alias::" not in block
