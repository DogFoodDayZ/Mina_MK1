"""Contract: on the GUI path (/process-stream), Mina's own tool calls run.

Transcript-grounded prompts (TODO.md):
- "mina make a script that counts down from 10 to 0 save it as countdown.py"
- "TRAVIS hay mina how's the memory ?"

Before the fix, process_stream only executed tools when the user's wording hit
keyword regexes; otherwise the tool call was streamed raw and dropped. JSON
tool args were also mangled ("E:\\..." parsed as key "E").
"""
from agent.core import MK1Core
import pytest
import json
from types import SimpleNamespace

SAVE_PROMPT = "mina make a script that counts down from 10 to 0 save it as countdown.py"
MEMORY_PROMPT = "TRAVIS hay mina how's the memory ?"
SCRIPT = "for i in range(10, -1, -1):\n    print(i)\n"


class StreamingModel:
    def __init__(self, stream_text, followups=()):
        self.stream_text = stream_text
        self.followups = list(followups)
        self.chat_calls = []

    def chat_stream(self, messages, **kwargs):
        for i in range(0, len(self.stream_text), 5):
            yield self.stream_text[i:i + 5]

    def chat(self, messages, **kwargs):
        self.chat_calls.append([dict(m) for m in messages])
        text = self.followups.pop(0) if self.followups else ""
        return {"choices": [{"message": {"content": text}}]}


class RecordingTools:
    def __init__(self, names, result):
        self.tools = {name: object() for name in names}
        self.result = result
        self.runs = []

    def run(self, name, args):
        self.runs.append((name, args))
        return self.result


def _core(model, tools):
    core = MK1Core.__new__(MK1Core)
    core.reflex_enabled = False
    core.reflex_requires_prefix = False
    core.reflex_prefixes = ["/tool"]
    core.model = model
    core.tools = tools
    core.stored = []
    core._advance_turn_clock = lambda: 1
    core._refresh_idle_checkin_state = lambda: None
    core._consume_idle_checkin_prompt = lambda: ""
    core._consume_away_nag_prompt = lambda: ""
    core._register_user_activity = lambda: None
    core._maybe_run_maintenance_tick = lambda: None
    core._build_process_memory_context = lambda text: ("", "", "")
    core._build_process_messages = lambda **kwargs: ([{"role": "system", "content": "persona"}], None)
    core._capture_tool_failure_for_learning = lambda **kwargs: None
    core._maybe_rescue_chat_suggested_action = lambda **kwargs: None
    core._store_turn_memory = lambda **kwargs: core.stored.append(kwargs["assistant_text"])
    return core


def test_save_script_tool_call_runs_with_real_args_and_markup_is_not_streamed():
    call = (
        "On it!\n```json\n"
        '{"tool": "file_write", "args": {"path": "E:\\\\workspace\\\\countdown.py", '
        '"content": "for i in range(10, -1, -1):\\n    print(i)\\n"}}\n```'
    )
    model = StreamingModel(call, followups=["Saved E:\\workspace\\countdown.py."])
    tools = RecordingTools(["file_write"], {"ok": True, "result": {"path": "E:\\workspace\\countdown.py"}})
    core = _core(model, tools)

    streamed = "".join(MK1Core.process_stream(core, SAVE_PROMPT))

    assert tools.runs == [("file_write", {"path": "E:\\workspace\\countdown.py", "content": SCRIPT})]
    assert any(m.get("role") == "tool" for m in model.chat_calls[0])
    assert "```json" not in streamed and '"tool"' not in streamed
    assert streamed.startswith("On it!")
    assert streamed.endswith("Saved E:\\workspace\\countdown.py.")
    assert core.stored == [streamed.strip()]


def test_tool_call_runs_even_when_wording_misses_old_keyword_gates():
    core_probe = MK1Core.__new__(MK1Core)
    assert not MK1Core._is_action_request_like(core_probe, MEMORY_PROMPT)
    assert not MK1Core._is_command_like_query(core_probe, MEMORY_PROMPT)

    call = '```json\n{"tool": "memory_read", "args": {"query": "recent memory health", "top_k": 3}}\n```'
    model = StreamingModel(call, followups=["Memory's humming, 121 records and counting."])
    tools = RecordingTools(["memory_read"], {"ok": True, "result": "121 records"})
    core = _core(model, tools)

    streamed = "".join(MK1Core.process_stream(core, MEMORY_PROMPT))

    assert tools.runs == [("memory_read", {"query": "recent memory health", "top_k": 3})]
    assert streamed == "Memory's humming, 121 records and counting."


def test_plain_chat_streams_unchanged_and_runs_no_tool():
    reply = "Hey Travis! Just hanging out in the wires, what's up?"
    model = StreamingModel(reply)
    tools = RecordingTools(["file_write"], {"ok": True})
    core = _core(model, tools)

    streamed = "".join(MK1Core.process_stream(core, "hey mina"))

    assert streamed == reply
    assert tools.runs == []
    assert model.chat_calls == []
    assert core.stored == [reply]


@pytest.mark.parametrize("streaming", [True, False])
def test_empty_reply_is_explicit_failure_without_retry_or_stored_answer(streaming):
    """Live read/explain request returned reasoning-only EOS, not a final answer."""
    model = StreamingModel("")
    core = _core(model, RecordingTools([], {}))
    prompt = "Read repair.py and explain why it will not start. Do not edit or run it."
    if streaming:
        events = list(core.process_stream(prompt, request_context={"include_action_reports": True}))
        text = "".join(event for event in events if isinstance(event, str))
        assert next(event for event in events if isinstance(event, dict))["actions"] == []
        assert model.chat_calls == []
    else:
        result = core.process(prompt)
        text = result["reply"]
        assert result["actions"] == []
        assert len(model.chat_calls) == 1
    assert "Model returned no final answer or tool call" in text
    assert core.stored == [] and core.tools.runs == []


@pytest.mark.parametrize("streaming", [True, False])
def test_empty_followup_keeps_completed_action_report_without_success_claim(streaming):
    call = '```json\n{"tool":"file_read","args":{"path":"repair.py"}}\n```'
    model = StreamingModel(call)
    if not streaming:
        model.followups = [call, ""]
    tools = RecordingTools(["file_read"], {"ok": True, "result": {"content": "print(6)"}})
    core = _core(model, tools)
    if streaming:
        events = list(core.process_stream("read repair.py", request_context={"include_action_reports": True}))
        report = next(event for event in events if isinstance(event, dict))
        text = "".join(event for event in events if isinstance(event, str))
    else:
        report = core.process("read repair.py")
        text = report["reply"]
    assert "Model returned no final answer or tool call" in text
    assert len(report["actions"]) == 1 and report["actions"][0]["ok"]
    assert len(tools.runs) == 1 and core.stored == []


def test_json_tool_args_keep_windows_paths():
    core = MK1Core.__new__(MK1Core)
    parsed = MK1Core._parse_tool_args(core, '{"path": "E:\\\\Mina_MK1\\\\hello.py", "content": "x"}')
    assert parsed == {"path": "E:\\Mina_MK1\\hello.py", "content": "x"}
    legacy = MK1Core._parse_tool_args(core, 'path: "notes.txt", overwrite: true')
    assert legacy == {"path": "notes.txt", "overwrite": True}


@pytest.mark.parametrize("streaming", [True, False])
def test_native_gui_skully_call_executes_and_returns_matching_call_id(streaming):
    """Travis: 'mina read \\workspace\\skully.py'."""
    call = {
        "id": "read-skully", "type": "function",
        "function": {"name": "file_read", "arguments": '{"path":"E:\\\\workspace\\\\skully.py"}'},
    }

    class NativeModel(StreamingModel):
        first_call = True

        def chat_stream(self, messages, **kwargs):
            assert kwargs["tools"][0]["function"]["name"] == "file_read"
            yield {"tool_calls": [call]}

        def chat(self, messages, **kwargs):
            if not streaming and self.first_call:
                self.first_call = False
                assert kwargs["tools"][0]["function"]["name"] == "file_read"
                return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}]}
            assert messages[-2]["tool_calls"] == [call]
            assert messages[-1]["tool_call_id"] == "read-skully"
            assert messages[-1]["name"] == "file_read"
            assert "Hello, Skully!" in messages[-1]["content"]
            return super().chat(messages, **kwargs)

    model = NativeModel("", ["It prints Hello, Skully!"])
    tools = RecordingTools(["file_read"], {"ok": True, "result": {"content": 'print("Hello, Skully!")'}})
    tools.get_tool_schemas = lambda: [{
        "type": "function", "function": {
            "name": "file_read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    core = _core(model, tools)
    core.native_tool_calls = True
    result = (
        "".join(core.process_stream("mina read \\workspace\\skully.py"))
        if streaming else core.process("mina read \\workspace\\skully.py")["reply"]
    )
    assert tools.runs == [("file_read", {"path": "E:\\workspace\\skully.py"})]
    assert result == "It prints Hello, Skully!"
    assert core.stored == [result]


def test_native_followup_error_is_not_returned_as_success():
    class FailingModel:
        def chat(self, **kwargs):
            raise RuntimeError("backend disconnected")

    core = _core(FailingModel(), RecordingTools(["file_read"], {"ok": True, "result": "content"}))
    call = {"id": "read-1", "type": "function", "function": {
        "name": "file_read", "arguments": '{"path":"skully.py"}',
    }}
    with pytest.raises(RuntimeError, match="backend disconnected"):
        core._run_process_tool_loop(
            [{"role": "user", "content": "read skully.py"}],
            {"choices": [{"message": {"content": "", "tool_calls": [call]}}]},
            "read skully.py",
        )


def test_native_tool_limit_reports_failure_instead_of_unexecuted_success():
    call = {"id": "read-1", "type": "function", "function": {
        "name": "file_read", "arguments": '{"path":"skully.py"}',
    }}
    reply = {"choices": [{"message": {"content": "", "tool_calls": [call]}}]}

    class RepeatingModel:
        def chat(self, **kwargs):
            return reply

    tools = RecordingTools(["file_read"], {"ok": True, "result": "content"})
    core = _core(RepeatingModel(), tools)
    with pytest.raises(RuntimeError, match="Tool-call limit"):
        core._run_process_tool_loop(
            [{"role": "user", "content": "read skully.py"}], reply, "read skully.py", max_tool_loops=2,
        )
    assert len(tools.runs) == 2


def test_sparky_workspace_run_followup_preserves_verified_spelling_across_core_recreation():
    """Travis: 'read sparky_fireworks.py', 'its in workspace', 'run it'."""
    rows = []

    def remember(text, **kwargs):
        rows.append({"text": text, **kwargs})

    memory = SimpleNamespace(
        add_memory=remember,
        recent_memories=lambda **kwargs: list(reversed(rows[-kwargs["top_k"]:])),
    )
    core = MK1Core.__new__(MK1Core)
    core.native_tool_calls = True
    core.memory = memory
    exchange = [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "read-sparkly", "type": "function", "function": {
                "name": "file_read", "arguments": json.dumps({
                    "path": "E:\\workspace\\sparkly_fireworks.py", "auth_token": "secret",
                }),
            },
        }]},
        {"role": "tool", "tool_call_id": "read-sparkly", "name": "file_read",
         "content": '[file_read result]\n{"ok":true,"result":{"content":"print("}}'},
    ]
    core._remember_native_tool_exchange(exchange, "its in workspace")
    restored = MK1Core.__new__(MK1Core)
    restored.native_tool_calls = True
    restored.memory = memory
    messages = restored._recent_native_tool_messages()
    assert messages[0]["content"].endswith("its in workspace")
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"]
    args = json.loads(messages[1]["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "E:\\workspace\\sparkly_fireworks.py"}
    assert messages[2] == exchange[1]
    assert "secret" not in json.dumps(messages)
    assert "secret" in exchange[0]["tool_calls"][0]["function"]["arguments"]
    assert all(row["kind"] == "note" and row["tags"] == ["native_tool_exchange"] for row in rows)


def test_failed_execution_keeps_stderr_and_exit_code_in_model_result():
    tools = RecordingTools(["code_execute"], {
        "ok": False, "error": None, "exit_code": 1,
        "stderr": "SyntaxError: '(' was never closed", "stdout": "",
    })
    core = _core(None, tools)
    name, result, message = core._execute_process_tool_call(
        "code_execute", '{"code":"print("}', "run it",
    )
    assert name == "code_execute" and not result["ok"]
    assert "SyntaxError" in message
    assert '"exit_code":1' in message


def test_repeating_the_same_fix_reports_no_code_change():
    core = _core(None, RecordingTools(["file_write"], {
        "ok": True, "result": {"path": "repair.py", "written": True, "changed": False},
    }))
    _, result, message = core._execute_process_tool_call(
        "file_write", '{"path":"repair.py","content":"same code"}', "can you fix it ?",
    )
    assert result["ok"]  # ordinary idempotent saves remain successful
    assert "FILE UNCHANGED" in message and "no code change was applied" in message


def test_native_exchange_history_bounds_whole_pairs_and_marks_truncated_results():
    rows = []
    core = MK1Core.__new__(MK1Core)
    core.native_tool_calls = True
    core.memory = SimpleNamespace(
        add_memory=lambda text, **kwargs: rows.append({"text": text, **kwargs}),
        recent_memories=lambda **kwargs: list(reversed(rows[-kwargs["top_k"]:])),
    )
    for i in range(3):
        core._remember_native_tool_exchange([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": f"read-{i}", "type": "function", "function": {
                    "name": "file_read", "arguments": '{"path":"sparkly_fireworks.py"}',
                },
            }]},
            {"role": "tool", "tool_call_id": f"read-{i}", "content": "x" * 4000},
        ], f"read file {i}")
    messages = core._recent_native_tool_messages()
    assert len(messages) == 6
    assert messages[0]["content"].endswith("read file 1")
    assert messages[3]["content"].endswith("read file 2")
    for offset in (0, 3):
        assert messages[offset + 1]["tool_calls"][0]["id"] == messages[offset + 2]["tool_call_id"]
        assert messages[offset + 2]["content"].endswith("[Historical result truncated]")


@pytest.mark.parametrize("streaming", [True, False])
def test_yes_all_three_report_preserves_failed_run_even_when_model_claims_success(streaming):
    """Travis: 'can you fix it ?', 'yes all three please'."""
    write_call = {"id": "write-1", "type": "function", "function": {
        "name": "file_write", "arguments": '{"path":"sparkly_fireworks.py","content":"print(1)"}',
    }}
    run_call = {"id": "run-1", "type": "function", "function": {
        "name": "code_execute", "arguments": '{"code":"raise RuntimeError()"}',
    }}

    class Model:
        first_call = True

        def chat_stream(self, **kwargs):
            yield {"tool_calls": [write_call]}

        def chat(self, **kwargs):
            if not streaming and self.first_call:
                self.first_call = False
                return {"choices": [{"message": {"content": "", "tool_calls": [write_call]}}]}
            if kwargs["messages"][-1]["name"] == "file_write":
                return {"choices": [{"message": {"content": "", "tool_calls": [run_call]}}]}
            return {"choices": [{"message": {"content": "All three done! Script running!"}}]}

    class Tools(RecordingTools):
        def run(self, name, args):
            self.runs.append((name, args))
            if name == "file_write":
                return {"ok": True, "result": {"written": True, "path": "sparkly_fireworks.py",
                        "verification": {"exists": True, "content_matches": True, "sha256": "abc"}}}
            return {"ok": False, "exit_code": 1, "error": "process_exited_with_code_1",
                    "stderr": "TimeoutExpired after 3 seconds"}

    tools = Tools(["file_write", "code_execute"], {})
    core = _core(Model(), tools)
    if streaming:
        events = list(core.process_stream(
            "yes all three please", request_context={"include_action_reports": True},
        ))
        report = next(event for event in events if isinstance(event, dict))
        assert report["type"] == "actions"
        text = "".join(event for event in events if isinstance(event, str))
    else:
        report = core.process("yes all three please")
        text = report["reply"]
    assert [action["tool"] for action in report["actions"]] == ["file_write", "code_execute"]
    assert report["actions"][0]["verification"]["content_matches"]
    assert report["actions"][1]["ok"] is False
    assert report["actions"][1]["exit_code"] == 1
    assert "TimeoutExpired" in report["actions"][1]["stderr"]
    assert "All three done!" in text


def test_no_tool_reply_has_explicit_empty_action_report_when_requested():
    core = _core(StreamingModel("I did everything!"), RecordingTools([], {}))
    events = list(core.process_stream(
        "yes all three please", request_context={"include_action_reports": True},
    ))
    assert events[-1] == {"type": "actions", "actions": []}
    assert core.tools.runs == []


def test_action_report_is_preserved_on_followup_model_failure():
    class Model:
        def chat_stream(self, **kwargs):
            yield {"tool_calls": [{"id": "run-1", "type": "function", "function": {
                "name": "code_execute", "arguments": '{"code":"raise RuntimeError()"}',
            }}]}

        def chat(self, **kwargs):
            raise RuntimeError("model disconnected")

    core = _core(Model(), RecordingTools(
        ["code_execute"], {"ok": False, "error": "process_exited_with_code_1", "exit_code": 1},
    ))
    events = list(core.process_stream("run it", request_context={"include_action_reports": True}))
    assert events[0]["actions"][0]["ok"] is False
    assert events[1] == "(Mina error: model disconnected)"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prompt", ["can you fix it ?", "yes all three please", "please repair that script"])
def test_authorized_repair_uses_real_saved_file_and_retests_new_failure(tmp_path, monkeypatch, streaming, prompt):
    """Exact transcript follow-ups after a repair/save/run offer; no live DB or API."""
    from tools import code_execute, file_write
    monkeypatch.setattr(file_write, "authorize_write_paths", lambda *a, **k: (True, None, None))
    path = tmp_path / "sparkly_fireworks.py"
    path.write_text("print(\n", encoding="utf-8")
    steps = [
        ("code_execute", {"path": str(path), "mode": "inspect", "expected_stdout": "jellybean sparkle\n"}),
        ("code_execute", {"path": str(path), "mode": "run", "timeout": 3}),
        ("file_write", {"path": str(path), "content": "print(1 / 0)\n"}),
        ("code_execute", {"path": str(path), "mode": "run", "timeout": 3}),
        ("file_write", {"path": str(path), "content": "print('jellybean sparkle')\n"}),
        ("code_execute", {"path": str(path), "mode": "check"}),
        ("code_execute", {"path": str(path), "mode": "run", "timeout": 3}),
    ]

    def response(index):
        name, args = steps[index]
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": f"repair-{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]}}]}

    class Tools(RecordingTools):
        def run(self, name, args):
            self.runs.append((name, args))
            return {"file_write": file_write.tool_entry, "code_execute": code_execute.tool_entry}[name](args)

    class Model:
        index = 0
        reviewed = False

        def chat_stream(self, **kwargs):
            self.index = 1
            yield {"tool_calls": response(0)["choices"][0]["message"]["tool_calls"]}

        def chat(self, messages, **kwargs):
            if self.index == 3 and not self.reviewed:
                self.reviewed = True
                return {"choices": [{"message": {"content": "All three done! Script running!"}}]}
            if self.index == 3:
                assert messages[-1]["role"] == "system"
                assert "Repair verification checkpoint" in messages[-1]["content"]
            if self.index == 2:
                assert "SyntaxError" in messages[-1]["content"]
            if self.index == 4:
                assert "ZeroDivisionError" in messages[-1]["content"]
            if self.index == len(steps):
                assert "jellybean sparkle" in messages[-1]["content"]
                return {"choices": [{"message": {"content": "Saved and tested: jellybean sparkle."}}]}
            reply = response(self.index)
            self.index += 1
            return reply

    tools = Tools(["code_execute", "file_write"], {})
    tools.get_tool_schemas = lambda: []
    model = Model()
    core = _core(model, tools)
    core.native_tool_calls = True
    if streaming:
        events = list(core.process_stream(prompt, request_context={"include_action_reports": True}))
        text = "".join(event for event in events if isinstance(event, str))
        actions = next(event["actions"] for event in events if isinstance(event, dict))
    else:
        result = core.process(prompt)
        text, actions = result["reply"], result["actions"]
    assert len(actions) == 7
    assert not actions[1]["ok"] and not actions[3]["ok"]
    assert actions[-1]["ok"] and actions[-1]["stdout"] == "jellybean sparkle\n"
    assert actions[-1]["sha256"] == actions[-3]["verification"]["sha256"]
    assert text == "Saved and tested: jellybean sparkle."
    assert model.reviewed
    assert path.read_text(encoding="utf-8") == "print('jellybean sparkle')\n"


def test_repair_gate_rejects_syntax_only_wrong_revision_and_stale_test():
    core = MK1Core.__new__(MK1Core)
    inspect = ("code_execute", {"ok": True, "path": "repair.py", "mode": "inspect"})
    write = ("file_write", {"ok": True, "result": {
        "path": "repair.py", "verification": {"sha256": "current"},
    }})
    check = ("code_execute", {"ok": True, "path": "repair.py", "mode": "check",
                              "sha256": "current", "exit_code": 0})
    run = ("code_execute", {"ok": True, "path": "repair.py", "mode": "run",
                            "sha256": "current", "exit_code": 0})
    assert core._python_repair_verification_gaps([inspect, write, check])
    assert core._python_repair_verification_gaps([
        inspect, write, ("code_execute", {**run[1], "sha256": "old"}),
    ])
    assert not core._python_repair_verification_gaps([inspect, write, run])
    assert core._python_repair_verification_gaps([inspect, write, run, write])
    assert not core._python_repair_verification_gaps([write])  # ordinary save, no authorized repair
    assert core._python_repair_verification_gaps([write, check])  # explicit test starts verification scope
    assert not core._python_repair_verification_gaps([write, run])
    failed_run = ("code_execute", {**run[1], "ok": False, "error": "stdout_mismatch"})
    assert core._python_repair_verification_gaps([write, failed_run])
    inspect_output = ("code_execute", {**inspect[1], "expected_stdout": "jellybean sparkle\n"})
    assert core._python_repair_verification_gaps([inspect_output, write, run])
    exact_run = ("code_execute", {**run[1], "stdout": "jellybean sparkle\n"})
    assert not core._python_repair_verification_gaps([inspect_output, write, exact_run])
    consultation = ("coding_specialist", {"ok": True, "result": {
        "path": "repair.py", "proposed_content": "print(1)", "saved": False, "tested": False,
    }})
    assert core._python_repair_verification_gaps([consultation, write])
    assert not core._python_repair_verification_gaps([consultation, write, run])


def test_specialist_assessment_report_preserves_no_change_and_unverified_summary():
    """Actual review: valid sum(values) was incorrectly described as an extra-newline bug."""
    report = MK1Core._tool_action_report("coding_specialist", {"path": "repair.py"}, {
        "ok": True, "result": {
            "path": "repair.py", "status": "no_change", "summary": "Already prints one newline.",
            "assessment_verified": False, "saved": False, "tested": False, "changed": False,
        },
    })
    assert report["status"] == "no_change" and report["assessment_verified"] is False
    assert report["summary"] == "Already prints one newline."
    assert report["saved"] is False and report["tested"] is False and report["changed"] is False
    assert "verification" not in report and "exit_code" not in report


def test_workflow_inference_summary_omits_only_nested_details_not_actual_receipts():
    result = {"ok": True, "result": {
        "path": "repair.py", "saved": True, "tested": True, "stdout": "6\n", "sha256": "tested",
        "changes": [{"unified_diff": "-for x in values\n+for x in values:\n", "after_sha256": "tested"}],
        "steps": [{"tool": "file_write", "result": {"ok": True, "result": {
            "path": "repair.py", "verification": {"exists": True, "sha256": "tested"},
        }}}],
    }}
    core = _core(None, RecordingTools(["code_repair"], result))
    _, actual, model_text = core._execute_process_tool_call(
        "code_repair", '{"path":"repair.py","objective":"Fix it, save and run."}', "can you fix it ?",
    )
    assert actual is result and actual["result"]["steps"]
    assert '"steps":' not in model_text and "steps_omitted" in model_text
    assert '"saved":true' in model_text and '"sha256":"tested"' in model_text
    report = MK1Core._tool_action_report("code_repair", {}, actual)
    assert report["steps"][0]["verification"]["sha256"] == "tested"
    assert '"changes":' in model_text and "for x in values:" in model_text
    assert report["changes"] == actual["result"]["changes"]


def test_bom_byte_fact_is_preserved_in_independent_read_action_report():
    report = MK1Core._tool_action_report("file_read", {"path": "source.py"}, {
        "ok": True, "result": {"decoder_used": "utf-8", "bom": None},
    })
    assert report["decoder_used"] == "utf-8" and "bom" in report and report["bom"] is None


@pytest.mark.parametrize("configured,expected", [(2048, 1024), (512, 512), (-1, 1024)])
def test_native_followup_caps_output_without_increasing_smaller_configured_limit(configured, expected):
    model = StreamingModel("", followups=["Read the actual source."])
    model.max_tokens = configured
    caps = []
    original_chat = model.chat

    def chat(messages, **kwargs):
        caps.append(kwargs["max_tokens_override"])
        return original_chat(messages, **kwargs)

    model.chat = chat
    core = _core(model, RecordingTools(["file_read"], {"ok": True, "result": {"content": "print(6)"}}))
    core.native_tool_calls = True
    core._native_tool_schemas = lambda: []
    reply = {"choices": [{"message": {"content": "", "tool_calls": [{
        "id": "read", "function": {"name": "file_read", "arguments": '{"path":"repair.py"}'},
    }]}}]}
    core._run_process_tool_loop([{"role": "user", "content": "can you fix it ?"}], reply, "can you fix it ?")
    assert caps == [expected] and model.max_tokens == configured


def test_repair_checkpoint_cannot_accept_repeated_false_completion():
    def reply(name=None, args=None):
        message = {"content": "All fixed! Running!"}
        if name:
            message["tool_calls"] = [{"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps(args),
            }}]
        return {"choices": [{"message": message}]}

    class Model:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            return reply("file_write", {"path": "repair.py", "content": "print(1)"}) if self.calls == 1 else reply()

    class Tools(RecordingTools):
        def run(self, name, args):
            self.runs.append((name, args))
            return {"ok": True, "result": {"path": "repair.py", "mode": "inspect",
                                         "verification": {"sha256": "saved"}}}

    tools = Tools(["file_write", "code_execute"], {})
    tools.get_tool_schemas = lambda: []
    core = _core(Model(), tools)
    core.native_tool_calls = True
    reports = []
    with pytest.raises(RuntimeError, match="Repair verification incomplete"):
        core._run_process_tool_loop(
            [{"role": "user", "content": "can you fix it ?"}],
            reply("code_execute", {"path": "repair.py", "mode": "inspect"}),
            "can you fix it ?", action_reports=reports,
        )
    assert len(tools.runs) == 2 and len(reports) == 2
    assert core.model.calls == 3  # one bounded review, no infinite prompting


def test_earlier_tool_compaction_preserves_ids_proof_and_latest_diagnostics():
    from agent.core import LMStudioClient
    core = MK1Core.__new__(MK1Core)
    messages = [{"role": "system", "content": "Original personality"}, {
        "role": "user", "content": "can you fix it ?",
    }]
    exchanges = []
    source = "print('jellybean sparkle')\n" * 100
    result = {"ok": True, "result": {"path": "repair.py", "verification": {
        "exists": True, "content_matches": True, "sha256": "saved",
    }}}
    for i in range(7):
        call = {"id": f"write-{i}", "type": "function", "function": {
            "name": "file_write", "arguments": json.dumps({"path": "repair.py", "content": source}),
        }}
        exchange = [
            {"role": "assistant", "content": "Earlier narration " * 200, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "name": "file_write",
             "content": json.dumps({**result, "verbose_output": "x" * 1000})},
        ]
        messages.extend(exchange)
        exchanges.append(exchange)
    latest = [
        {"role": "assistant", "tool_calls": [{
            "id": "run-latest", "type": "function", "function": {
                "name": "code_execute", "arguments": '{"path":"repair.py","mode":"run"}',
            },
        }], "content": ""},
        {"role": "tool", "name": "code_execute", "tool_call_id": "run-latest",
         "content": "[TOOL FAILED] code_execute\nSyntaxError: '(' was never closed"},
    ]
    messages.extend(latest)
    original_latest = json.loads(json.dumps(latest))
    client = LMStudioClient({"context_window": 8448, "max_tokens": 2048})
    with pytest.raises(ValueError, match="current turn"):
        client._trim_messages_to_budget(messages)
    for exchange in exchanges:
        core._compact_earlier_tool_exchange(exchange, [("file_write", result)])
        assert exchange[0]["tool_calls"][0]["id"] == exchange[1]["tool_call_id"]
        assert "saved" in exchange[1]["content"]
        assert "Earlier tool result summary" in exchange[1]["content"]
        assert "Earlier source omitted" in exchange[0]["tool_calls"][0]["function"]["arguments"]
    assert latest == original_latest
    assert client._estimate_tokens(messages) < 2500
    assert client._trim_messages_to_budget(messages) == messages
    assert result["result"]["verification"]["sha256"] == "saved"  # full evidence not mutated


def test_compaction_does_not_replay_short_stale_edits_as_current_source():
    core = MK1Core.__new__(MK1Core)
    exchange = [
        {"role": "assistant", "content": "It is fixed!", "tool_calls": [{
            "id": "old-write", "type": "function", "function": {
                "name": "file_write",
                "arguments": json.dumps({"path": "repair.py", "content": 'print("wrong\\n")'}),
            },
        }]},
        {"role": "tool", "name": "file_write", "tool_call_id": "old-write",
         "content": "x" * 600},
    ]
    result = {"ok": True, "result": {"path": "repair.py", "verification": {
        "sha256": "old-hash", "content_matches": True, "first_line": 'print("wrong\\n")',
    }}}
    core._compact_earlier_tool_exchange(exchange, [("file_write", result)])
    assert "wrong" not in json.dumps(exchange)
    assert "old-hash" in exchange[1]["content"]
    assert exchange[1]["tool_call_id"] == exchange[0]["tool_calls"][0]["id"]
    assert result["result"]["verification"]["first_line"] == 'print("wrong\\n")'


def test_focused_repair_preserves_persona_facts_current_exchange_and_followup_meaning():
    persona = {"role": "system", "content": "Original Mina personality"}
    facts = {"role": "system", "content": "Turn working memory (authoritative DB facts): Travis"}
    safety = {"role": "system", "content": "Write permissions apply."}
    current = [
        {"role": "user", "content": "yes all three please"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "inspect", "type": "function", "function": {
                "name": "code_execute", "arguments": '{"path":"repair.py","mode":"inspect"}',
            },
        }]},
        {"role": "tool", "tool_call_id": "inspect", "name": "code_execute",
         "content": '{"ok":true,"stdout":"print("}'},
    ]
    messages = [
        persona, facts, safety,
        {"role": "system", "content": "Relevant memory context:\n" + "old claims " * 1000},
        {"role": "system", "content": "Retrieved older conversation (historical evidence)\nold story"},
        {"role": "system", "content": "A recalled lesson indicates a prior failed attempt. retry"},
        {"role": "user", "content": "old unrelated image conversation"},
        {"role": "assistant", "content": "old unrelated reaction"},
        {"role": "user", "content": "can you fix it ?"},
        {"role": "assistant", "content": "Save the fix, run it, and add jellybeans?"},
    ] + current
    original = json.loads(json.dumps(messages))
    MK1Core._focus_repair_messages(messages)
    assert messages[0] == persona
    assert facts in messages and safety in messages
    assert messages[-3:] == current
    text = json.dumps(messages)
    assert "Save the fix, run it, and add jellybeans?" in text
    assert "follow-up meaning only, not action proof" in text
    assert "old unrelated" not in text and "old claims" not in text and "old story" not in text
    assert "prior failed attempt" not in text
    assert original[-3:] == current  # only inference list changed, not records
    assert len(text) < 2000


def test_focused_repair_requires_current_request():
    with pytest.raises(ValueError, match="current user"):
        MK1Core._focus_repair_messages([{"role": "system", "content": "persona"}])


@pytest.mark.parametrize("tool,arguments,should_focus", [
    ("file_read", {"path": "repair.py"}, False),
    ("file_write", {"path": "new.py", "content": "print(1)"}, False),
    ("code_execute", {"code": "print(1)"}, False),
    ("code_execute", {"path": "repair.py", "mode": "inspect"}, True),
])
def test_focus_is_model_selected_not_a_keyword_trigger(tool, arguments, should_focus):
    seen = []
    core = _core(StreamingModel("", ["Finished."]), RecordingTools(
        [tool], {"ok": True, "result": {"path": arguments.get("path")}},
    ))
    core.native_tool_calls = True
    core.tools.get_tool_schemas = lambda: []
    core._focus_repair_messages = lambda messages: seen.append(list(messages))
    core.focused_repair_context = True
    reply = {"choices": [{"message": {"content": "", "tool_calls": [{
        "id": "action", "type": "function", "function": {
            "name": tool, "arguments": json.dumps(arguments),
        },
    }]}}]}
    core._run_process_tool_loop(
        [{"role": "user", "content": "fix repair debug script all three"}],
        reply, "fix repair debug script all three",
    )
    assert bool(seen) is should_focus
