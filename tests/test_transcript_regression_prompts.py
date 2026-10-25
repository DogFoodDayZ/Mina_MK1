from types import SimpleNamespace

from agent.core import MK1Core
from agent.pending_actions import PendingActionStore


def _core_for_intent_tests():
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={})
    core._pending_actions = PendingActionStore()
    core._intent_alias_cache = {}
    core.ambiguity_ask_on_medium_low = False
    core.memory = SimpleNamespace(add_memory=lambda *args, **kwargs: 1, find_memory_id_by_text=lambda *args, **kwargs: None)
    return core


def test_transcript_prompt_create_notes_routes_file_write():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "create a text file named notes.txt with hello world",
    )

    assert tool_name == "file_write"
    assert args["path"] == "notes.txt"
    assert "hello world" in args["content"].lower()


def test_transcript_prompt_recursive_directory_read_is_queued_scan():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "Mina read all files in E:\\Mina_MK1\\integrations\\ostranauts",
    )

    assert tool_name == "__inspect_directory_tree__"
    assert args["path"].lower().endswith("mina_mk1\\integrations\\ostranauts")
    assert args["mode"] == "all"


def test_parse_tool_args_handles_ansi_code_payload_without_unicodeescape_crash():
    core = _core_for_intent_tests()

    parsed = MK1Core._parse_tool_args(
        core,
        'path:"random_code_generator.py", content:"print(\\"\\033[92m*\\033[0m\\")"',
    )

    assert parsed["path"] == "random_code_generator.py"
    assert "print" in str(parsed["content"])


def test_parse_tool_args_falls_back_safely_on_malformed_trailing_backslash():
    core = _core_for_intent_tests()

    parsed = MK1Core._parse_tool_args(
        core,
        'content:"line with trailing slash \\", overwrite:true',
    )

    assert parsed["overwrite"] is True
    assert "trailing slash" in str(parsed["content"])


def test_malformed_empty_tool_markup_rescues_to_file_write_for_drafted_script():
    core = _core_for_intent_tests()
    captured = {}

    def _fake_reflex(messages, reply, user_input, request_context=None):
        captured["user_input"] = user_input
        captured["reply_text"] = MK1Core._extract_text(core, reply)
        return {"choices": [{"message": {"content": "rescued"}}]}

    core._reflex_tools_and_memory = _fake_reflex

    assistant_text = (
        "Here is `random_code_generator.py` once more:\n\n"
        "```python\n"
        "import random\n"
        "print(\"ready\")\n"
        "```\n\n"
        "|tool_call><tool_call|>"
    )

    out = MK1Core._maybe_rescue_chat_suggested_action(
        core,
        messages=[],
        user_input="create a program and save it",
        assistant_text=assistant_text,
        request_context=None,
    )

    assert out is not None
    assert captured["user_input"] == "save this to random_code_generator.py"
    assert "random" in captured["reply_text"].lower()


def test_malformed_empty_tool_markup_rescues_to_memory_status_intent_without_code_block():
    core = _core_for_intent_tests()
    captured = {}

    def _fake_reflex(messages, reply, user_input, request_context=None):
        captured["user_input"] = user_input
        return {"choices": [{"message": {"content": "rescued"}}]}

    core._reflex_tools_and_memory = _fake_reflex

    out = MK1Core._maybe_rescue_chat_suggested_action(
        core,
        messages=[],
        user_input="TRAVIS hay mina how's the memory ?",
        assistant_text="|tool_call><tool_call|>\n\nHere we go!\n\n|tool_call></tool_call|",
        request_context=None,
    )

    assert out is not None
    assert "memory" in captured.get("user_input", "").lower()


def test_explicit_python_path_handoff_routes_file_read_and_sets_execution_context():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        '"E:\\workspace\\random_code_generator.py" is the code generator.',
    )

    assert tool_name == "file_read"
    assert str(args.get("path") or "").lower().endswith("random_code_generator.py")

    stored = MK1Core._get_last_draft_artifact(core)
    assert isinstance(stored, dict)
    assert str(stored.get("target_path") or "").lower().endswith("random_code_generator.py")


def test_execution_followup_runs_from_explicit_python_path_handoff_context():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    MK1Core.detect_tool_intent(
        core,
        '"E:\\workspace\\random_code_generator.py" is the code generator.',
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "run the random code generator")

    assert tool_name == "ps_run"
    assert "python" in str(args.get("script") or "").lower()
    assert "random_code_generator.py" in str(args.get("script") or "").lower()


def test_spotify_typo_spotiy_routes_current_playback_action():
    # Contract update 2026-08-05: Spotify keyword routing removed — model handles music requests
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"spotify_link": object()})

    tool_name, args = MK1Core.detect_tool_intent(core, "whats on spotiy right now?")

    # Goes to model for conversational handling, not keyword-matched
    assert tool_name is None


def test_transcript_prompt_yes_confirms_pending_external_write():
    core = _core_for_intent_tests()

    MK1Core._set_pending_write_authorization(
        core,
        "file_write",
        {"path": "E:\\external\\notes.txt", "content": "hello"},
        "create notes externally",
        "outside workspace requires confirmation",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "yes")

    assert tool_name == "__confirm_external_write__"
    assert args == {}


def test_transcript_prompt_please_run_the_command_confirms_when_pending_write_exists():
    core = _core_for_intent_tests()

    MK1Core._set_pending_write_authorization(
        core,
        "file_write",
        {"path": "E:\\external\\notes.txt", "content": "hello"},
        "create notes externally",
        "outside workspace requires confirmation",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "please run the command")

    assert tool_name == "__confirm_external_write__"
    assert args == {}


def test_command_like_query_detects_explicit_run_script_phrase():
    core = _core_for_intent_tests()

    assert MK1Core._is_command_like_query(core, "run powershell script deploy_mina_android_auto.ps1") is True


def test_shared_process_tool_executor_formats_and_captures_failure():
    core = _core_for_intent_tests()
    captured = {}
    core._capture_tool_failure_for_learning = lambda **kwargs: captured.update(kwargs)
    core.tools = SimpleNamespace(
        tools={},
        run=lambda _name, _args: {"ok": False, "result": None, "error": "permission_denied"},
    )

    tool_name, result, message = MK1Core._execute_process_tool_call(
        core,
        tool_name="ps_run",
        args_str="script:'Get-Date'",
        user_intent="run the command",
    )

    assert tool_name == "ps_run"
    assert result["ok"] is False
    assert message == "[TOOL FAILED] ps_run: permission_denied"
    assert captured["tool_name"] == "ps_run"


def test_shared_process_tool_executor_preserves_web_fetch_for_hardware_question():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "CPU: Ryzen"}},
    )

    tool_name, result, message = MK1Core._execute_process_tool_call(
        core,
        tool_name="web_fetch",
        args_str="url:'https://example.invalid/invented-by-model'",
        user_intent="What CPU and GPU are actually installed in this computer?",
    )

    assert tool_name == "web_fetch"
    assert result["ok"] is True
    assert calls == [("web_fetch", {"url": "https://example.invalid/invented-by-model"})]
    assert "CPU: Ryzen" in message


def test_tool_call_parser_accepts_json_tool_block():
    core = _core_for_intent_tests()
    core._tool_registry = lambda: {"ps_run": object()}

    calls = MK1Core._extract_tool_calls_from_text(
        core,
        '```json\n{"tool":"ps_run","args":{"script":"Get-Date"}}\n```',
    )

    assert calls == [("ps_run", '{"script": "Get-Date"}')]


def test_tool_call_parser_accepts_single_quoted_json_shaped_block():
    core = _core_for_intent_tests()
    core._tool_registry = lambda: {"ps_run": object()}

    calls = MK1Core._extract_tool_calls_from_text(
        core,
        "```json\n{'tool': 'ps_run', 'args': {'script': 'Get-Date'}}\n```",
    )

    assert calls == [("ps_run", '{"script": "Get-Date"}')]


def test_shared_process_tool_executor_does_not_invent_system_info_alias():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "CPU: Ryzen"}},
    )

    tool_name, result, _message = MK1Core._execute_process_tool_call(
        core,
        tool_name="system_info",
        args_str="",
        user_intent="What CPU and GPU are actually installed in this computer?",
    )

    assert tool_name == "system_info"
    assert result["ok"] is True
    assert calls == [("system_info", {})]


def test_shared_process_tool_executor_preserves_minas_search_choice():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "CPU: Ryzen"}},
    )

    tool_name, _result, _message = MK1Core._execute_process_tool_call(
        core,
        tool_name="web_search",
        args_str="query:'invented query'",
        user_intent="What CPU and GPU are actually installed in this computer?",
    )

    assert tool_name == "web_search"
    assert calls == [("web_search", {"query": "invented query"})]


def test_shared_process_tool_executor_does_not_invent_powershell_arguments():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "CPU: Ryzen"}},
    )

    tool_name, result, _message = MK1Core._execute_process_tool_call(
        core,
        tool_name="powershell",
        args_str="",
        user_intent="What CPU and GPU are actually installed in this computer?",
    )

    assert tool_name == "powershell"
    assert result["ok"] is True
    assert calls == [("powershell", {})]


def test_shared_process_tool_executor_does_not_replace_powershell_with_memory():
    core = _core_for_intent_tests()
    core.memory = SimpleNamespace(
        search=lambda *_args, **_kwargs: [
            {"text": "hardware_snapshot::ram probe => FreePhysicalMemory: 4848168"},
        ],
    )
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "RAM snapshot"}},
    )

    tool_name, result, _message = MK1Core._execute_process_tool_call(
        core,
        tool_name="powershell",
        args_str="",
        user_intent="Looks like we need to do something about the RAM situation.",
    )

    assert tool_name == "powershell"
    assert result["ok"] is True
    assert calls == [("powershell", {})]


def test_shared_process_tool_executor_does_not_replace_ps_run_with_memory():
    core = _core_for_intent_tests()
    core.memory = SimpleNamespace(
        search=lambda *_args, **_kwargs: [
            {"text": "hardware_snapshot::ram probe => FreePhysicalMemory: 4848168"},
        ],
    )
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "RAM snapshot"}},
    )

    tool_name, result, message = MK1Core._execute_process_tool_call(
        core,
        tool_name="ps_run",
        args_str="",
        user_intent="Looks like we need to do something about the RAM situation.",
    )

    assert tool_name == "ps_run"
    assert result["ok"] is True
    assert "RAM snapshot" in message
    assert calls == [("ps_run", {})]


def test_hardware_rescue_executes_fenced_powershell_suggestion():
    core = _core_for_intent_tests()
    captured = {}

    def fake_reflex(messages, reply, user_input, request_context=None):
        captured["user_input"] = user_input
        return {"choices": [{"message": {"content": "verified hardware"}}]}

    core._reflex_tools_and_memory = fake_reflex

    result = MK1Core._maybe_rescue_chat_suggested_action(
        core,
        messages=[],
        user_input="What CPU and GPU are actually installed in this computer?",
        assistant_text="```powershell\nGet-CimInstance Win32_Processor | Select-Object Name\n```",
    )

    assert result is not None
    assert "Win32_Processor" in captured["user_input"]


def test_hardware_ps_probe_keeps_the_command_mina_actually_requested():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "CPU and GPU"}},
    )

    _tool_name, _result, _message = MK1Core._execute_process_tool_call(
        core,
        tool_name="ps_run",
        args_str="script:'Get-CimInstance Win32_VideoController | Select-Object Name'",
        user_intent="What CPU and GPU are actually installed in this computer?",
    )

    assert calls[0][0] == "ps_run"
    assert calls[0][1] == {"script": "Get-CimInstance Win32_VideoController | Select-Object Name"}


def test_file_path_correction_supersedes_stale_directory_context(tmp_path):
    core = _core_for_intent_tests()
    corrected = str(tmp_path)

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        f"that file is in {corrected}",
    )

    assert tool_name == "dir_list"
    assert args["path"] == corrected


def test_shared_tool_executor_rejects_file_read_without_path():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    core.tools = SimpleNamespace(tools={}, run=lambda *_args: (_ for _ in ()).throw(AssertionError("must not execute")))

    tool_name, result, message = MK1Core._execute_process_tool_call(
        core,
        tool_name="file_read",
        args_str="",
        user_intent="you lol",
    )

    assert tool_name == "file_read"
    assert result["error"] == "file_read_requires_explicit_path"
    assert "Ask the user for the exact path" in message


def test_transcript_prompt_read_and_summarize_todo_is_bounded_to_active_backlog():
    content = "\n".join([
        "# TODO",
        "- [ ] Historical item",
        "## Next",
        "### Autonomous Work Intake",
        "- [ ] <bounded task title>",
        "### Core work",
        "- [ ] Run pytest tests/test_worker.py",
        "### Issue: Old record",
        "- [ ] Historical done criterion",
    ])

    summary = MK1Core._summarize_file_read_content(
        "Read E:\\Mina_MK1\\TODO.md and summarize the active autonomous backlog.",
        content,
    )

    assert "Run pytest tests/test_worker.py" in summary
    assert "Historical item" not in summary
    assert "<bounded task title>" not in summary
    assert "Historical done criterion" not in summary


def test_file_read_without_summary_request_preserves_full_content():
    core = _core_for_intent_tests()

    reply = MK1Core._render_tool_response_as_mina(
        core,
        messages=[],
        user_input="Read E:\\Mina_MK1\\TODO.md",
        tool_name="file_read",
        formatted="full file content",
        tool_ok=True,
    )

    assert reply == "full file content"


def test_shared_model_tool_loop_executes_tool_and_stops_on_final_reply():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    calls = []
    replies = iter([
        {"choices": [{"message": {"content": "call"}}]},
        {"choices": [{"message": {"content": "done"}}]},
    ])
    core._extract_tool_calls_from_text = lambda text: [("ps_run", "script:'Get-Date'")] if text == "call" else []
    core.model = SimpleNamespace(chat=lambda **_kwargs: next(replies))
    core.tools = SimpleNamespace(
        tools={},
        run=lambda name, args: calls.append((name, args)) or {"ok": True, "result": {"stdout": "ready"}},
    )
    messages = []

    reply, events = MK1Core._run_process_tool_loop(core, messages, next(replies), "run the command")

    assert reply["choices"][0]["message"]["content"] == "done"
    assert len(events) == 1
    assert calls[0][0] == "ps_run"
    assert messages[-1]["content"] == "[ps_run result]\n{'ok': True, 'result': {'stdout': 'ready'}}"


def test_shared_model_tool_loop_stops_repeated_identical_tool_reply():
    core = _core_for_intent_tests()
    core._capture_tool_failure_for_learning = lambda **_kwargs: None
    run_count = []
    core._extract_tool_calls_from_text = lambda _text: [("ps_run", "script:'Get-Date'")]
    core.model = SimpleNamespace(chat=lambda **_kwargs: {"choices": [{"message": {"content": "same"}}]})
    core.tools = SimpleNamespace(
        tools={},
        run=lambda _name, _args: run_count.append(True) or {"ok": True, "result": {}},
    )

    _reply, events = MK1Core._run_process_tool_loop(
        core,
        [],
        {"choices": [{"message": {"content": "same"}}]},
        "run the command",
    )

    assert len(events) == 1
    assert len(run_count) == 1


def test_obsolete_spotify_dj_canned_response_no_longer_short_circuits_chat():
    core = _core_for_intent_tests()

    result = core._handle_direct_response_reflex(
        "__spotify_dj_capability__",
        {},
        "can you keep playing my liked songs",
    )

    assert result is None


def test_removed_unused_interface_response_has_no_live_callers():
    source = open("agent/core.py", encoding="utf-8").read()

    assert "def _maybe_reply_interface_name" not in source


def test_conversational_system_matcher_blocks_are_removed():
    source = open("agent/core.py", encoding="utf-8").read()

    assert "system_query_terms = [" not in source
    assert "hardware_count_query = (" not in source
    assert "cpu_probe_query" in source


def test_file_index_status_and_refresh_use_single_routing_block():
    core = _core_for_intent_tests()

    status_tool, status_args = MK1Core.detect_tool_intent(core, "show the file index")
    refresh_tool, refresh_args = MK1Core.detect_tool_intent(core, "refresh the file index")

    assert status_tool == "__file_index_status__"
    assert status_args == {}
    assert refresh_tool == "__file_index_refresh__"
    assert refresh_args == {}


def test_core_uses_its_authoritative_file_intent_parser():
    source = open("agent/core.py", encoding="utf-8").read()

    assert "from agent.file_intents import detect_file_directory_intent" not in source
    assert "def _detect_file_directory_intent" in source
    assert "def _message_choice" not in source


def test_ps_inference_does_not_duplicate_hardware_count_rules():
    source = open("agent/core.py", encoding="utf-8").read()
    tool_source = open("tools/ps_run.py", encoding="utf-8").read()

    assert source.count('return "(Get-CimInstance Win32_VideoController).Count"') == 0
    assert source.count('return "(Get-CimInstance Win32_DiskDrive).Count"') == 0
    assert "Win32_VideoController" in tool_source


def test_core_ps_inference_delegates_to_tool_normalizer():
    source = open("agent/core.py", encoding="utf-8").read()

    infer_start = source.index("        def _infer_ps_command(text_value: str) -> str:")
    direct_markup = source.index("        # ====================================================\n        # DIRECT TOOL MARKUP", infer_start)
    infer_body = source[infer_start:direct_markup]

    assert "from tools.ps_run import _normalize_command" in infer_body
    assert "return normalized" in infer_body
    assert 'Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer' not in infer_body


def test_verified_tool_fallback_does_not_append_canned_flair():
    source = open("agent/core.py", encoding="utf-8").read()

    assert "def _with_mina_flair" not in source
    assert "Gremlin check complete." not in source


def test_transcript_memory_recall_drops_unrelated_recent_turns_after_topic_shift():
    core = _core_for_intent_tests()
    recent_turns = [
        {"text": "Mina refused to modify source code autonomously", "tags": ["short_term"]},
        {"text": "We chose the cobalt lantern protocol for memory reliability", "tags": ["short_term"]},
        {"text": "The weather forecast says rain tomorrow", "tags": ["short_term"]},
    ]

    selected = MK1Core._relevant_recent_turns(
        core,
        "what protocol did we choose for memory reliability",
        recent_turns,
    )

    assert [item["text"] for item in selected] == [
        "We chose the cobalt lantern protocol for memory reliability",
    ]


def test_incident_report_routes_to_startup_status_diagnostic_tool():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS yah you crashed on me and i couldnt reach you all day",
    )

    assert tool_name == "__startup_status__"
    assert args == {}


def test_casual_chat_with_crash_word_does_not_force_status_tool():
    core = _core_for_intent_tests()

    tool_name, _args = MK1Core.detect_tool_intent(
        core,
        "tell me a short story about a ship crash and recovery",
    )

    assert tool_name is None


def test_operational_incident_detector_is_true_for_unreachable_report():
    core = _core_for_intent_tests()

    assert MK1Core._is_operational_incident_report(
        core,
        "you crashed and i couldn't reach you all day",
    ) is True


def test_operational_incident_detector_stays_false_for_story_chat():
    core = _core_for_intent_tests()

    assert MK1Core._is_operational_incident_report(
        core,
        "tell me a story about a crash landing",
    ) is False


def test_cpu_gpu_conversational_prompt_routes_ps_run_with_executable_script():
    # Contract update 2026-08-05: hardware keyword routing removed — model decides
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS hay mina tell me about our gpu and cpu",
    )

    assert tool_name is None


def test_cpu_ping_prompt_routes_ps_run_with_cores_speed_usage_script():
    # Contract update 2026-08-05: hardware keyword routing removed — model decides
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS ping the cpu get me speed cores and use %",
    )

    assert tool_name is None


def test_bare_powershell_command_routes_to_ps_run():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "Get-WmiObject -Class Win32_VideoController | Select-Object Name, DeviceID",
    )

    assert tool_name == "ps_run"
    assert "Get-WmiObject" in str(args.get("script") or "")


def test_how_many_gpus_prompt_routes_to_ps_run_count_command():
    # Contract update 2026-08-05: hardware keyword routing removed — model decides
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS how many gpu's are there ?",
    )

    assert tool_name is None


def test_number_of_hard_drives_prompt_routes_to_ps_run_count_command():
    # Contract update 2026-08-05: hardware keyword routing removed — model decides
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object()})

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS what is the number of hard drives ?",
    )

    assert tool_name is None


def test_take_a_look_with_pending_write_routes_directory_scan_with_focus_hint(tmp_path):
    core = _core_for_intent_tests()
    core.workspace_root = str(tmp_path)

    MK1Core._set_pending_write_authorization(
        core,
        "file_write",
        {"path": "C:\\Scripts\\jellybeans.py", "content": "print('x')"},
        "save jellybeans script",
        "outside workspace",
    )

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "take a look and try again",
    )

    assert tool_name == "dir_list"
    assert str(args.get("path") or "") == str(tmp_path)


def test_take_a_look_replays_last_non_file_tool_when_useful():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"web_search": object()})

    MK1Core._set_last_tool_event(
        core,
        "web_search",
        {"query": "latest python release notes", "max_results": 5},
        "search web",
        {"ok": True},
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "take a look")

    assert tool_name == "web_search"
    assert str(args.get("query") or "") == "latest python release notes"


def test_explicit_absolute_file_save_does_not_route_to_memory_write():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        'save this to C:\\Scripts\\jellybeans.py with content print("hi")',
    )

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("jellybeans.py")


def test_take_a_look_without_context_defaults_to_workspace_scan(tmp_path):
    core = _core_for_intent_tests()
    core.workspace_root = str(tmp_path)

    tool_name, args = MK1Core.detect_tool_intent(core, "take a look")

    assert tool_name == "dir_list"
    assert str(args.get("path") or "") == str(tmp_path)


def test_failed_incomplete_followup_retries_last_draft_artifact_write():
    core = _core_for_intent_tests()

    MK1Core._set_last_draft_artifact(
        core,
        target_path="sparkly_fireworks.py",
        content="print('sparkles')\n",
        user_input="create a fireworks display in ascii art with little sparkly pops and a big one every so often",
    )

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "that failed try again the file was incomplete",
    )

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("sparkly_fireworks.py")
    assert args.get("overwrite") is True


def test_try_again_with_last_draft_artifact_prefers_write_retry_over_directory_scan():
    core = _core_for_intent_tests()
    core.workspace_root = "E:\\workspace"

    MK1Core._set_last_draft_artifact(
        core,
        target_path="sparkly_fireworks.py",
        content="print('sparkles')\n",
        user_input="create a fireworks display in ascii art with little sparkly pops and a big one every so often",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "try again")

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("sparkly_fireworks.py")


def test_hardware_chat_phrase_routes_to_ps_run():
    # Contract update 2026-08-05: hardware keyword routing removed — model handles these
    core = _core_for_intent_tests()

    tool_name, _args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS hay mina tell me about our gpu and cpu",
    )

    assert tool_name is None


def test_ping_cpu_phrase_routes_to_ps_run():
    # Contract update 2026-08-05: hardware keyword routing removed — model handles these
    core = _core_for_intent_tests()

    tool_name, _args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS ping the cpu get me speed cores and use %",
    )

    assert tool_name is None


def test_context_continuation_style_shift_targets_last_python_file():
    core = _core_for_intent_tests()

    MK1Core._set_context_continuation(
        core,
        "file_write",
        {"path": "C:\\Scripts\\jellybeans.py", "content": "print('x')"},
        "save the script",
        {"ok": True, "result": {"path": "C:\\Scripts\\jellybeans.py"}},
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "can you do ascii instead")

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("jellybeans.py")
    assert args.get("overwrite") is True


def test_context_continuation_yes_runs_suggested_next_action_when_available():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object(), "file_write": object()})

    MK1Core._set_context_continuation(
        core,
        "file_write",
        {"path": "C:\\Scripts\\jellybeans.py", "content": "print('x')"},
        "save the script",
        {"ok": True, "result": {"path": "C:\\Scripts\\jellybeans.py"}},
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "yes")

    assert tool_name == "ps_run"
    assert "python" in str(args.get("script") or "").lower()
    assert "jellybeans.py" in str(args.get("script") or "").lower()


def test_save_the_script_prefers_last_draft_over_stale_context_continuation():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object(), "file_write": object()})

    MK1Core._set_context_continuation(
        core,
        "file_write",
        {"path": "screen_filler.py", "content": "print('old')"},
        "save the script",
        {"ok": True, "result": {"path": "screen_filler.py"}},
    )
    MK1Core._set_last_draft_artifact(
        core,
        target_path="countdown.py",
        content=(
            "import time\n\n"
            "def countdown():\n"
            "    for i in range(10, -1, -1):\n"
            "        print(i)\n"
            "        time.sleep(1)\n\n"
            "if __name__ == \"__main__\":\n"
            "    countdown()\n"
        ),
        user_input="TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "save the script")

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("countdown.py")
    assert args.get("overwrite") is True
    assert args.get("content") == ""


def test_save_the_script_with_explicit_name_uses_requested_target_not_stale_context():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object(), "file_write": object()})

    MK1Core._set_context_continuation(
        core,
        "file_write",
        {"path": "screen_filler.py", "content": "print('old')"},
        "save the script",
        {"ok": True, "result": {"path": "screen_filler.py"}},
    )
    MK1Core._set_last_draft_artifact(
        core,
        target_path="countdown.py",
        content="print('countdown')\n",
        user_input="TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "save your script in countdown.py")

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("countdown.py")


def test_create_script_save_it_as_filename_routes_direct_file_write():
    core = _core_for_intent_tests()

    tool_name, args = MK1Core.detect_tool_intent(
        core,
        "TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py",
    )

    assert tool_name == "file_write"
    assert str(args.get("path") or "").lower().endswith("countdown.py")


def test_repo_local_file_write_is_not_high_risk_after_workspace_root_fix():
    core = _core_for_intent_tests()

    assert MK1Core._is_high_risk_tool(
        core,
        "file_write",
        {"path": r"E:\Mina_MK1\countdown.py", "content": "print('ok')"},
    ) is False


def test_execution_followup_runs_python_from_context_continuation_artifact():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object(), "file_write": object()})

    MK1Core._set_context_continuation(
        core,
        "file_write",
        {"path": "random_code_generator.py", "content": "print('x')"},
        "save script",
        {"ok": True, "result": {"path": "random_code_generator.py"}},
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "Mina execute the file now")

    assert tool_name == "ps_run"
    assert "python" in str(args.get("script") or "").lower()
    assert "random_code_generator.py" in str(args.get("script") or "").lower()


def test_execution_followup_runs_python_from_last_draft_when_no_continuation_state():
    core = _core_for_intent_tests()
    core.tools = SimpleNamespace(tools={"ps_run": object(), "file_write": object()})

    MK1Core._set_last_draft_artifact(
        core,
        target_path="random_code_generator.py",
        content="print('x')\n",
        user_input="create random code generator",
    )

    tool_name, args = MK1Core.detect_tool_intent(core, "the file is not executing, run it")

    assert tool_name == "ps_run"
    assert "python" in str(args.get("script") or "").lower()
    assert "random_code_generator.py" in str(args.get("script") or "").lower()


def test_file_write_candidate_prefers_reply_code_over_generic_stub():
    core = _core_for_intent_tests()

    reply = {
        "choices": [
            {
                "message": {
                    "content": (
                        "```python\n"
                        "def draw_skull():\n"
                        "    print(\"x_x\")\n\n"
                        "def main():\n"
                        "    print(\"HA HA HA\")\n"
                        "    draw_skull()\n\n"
                        "if __name__ == \"__main__\":\n"
                        "    main()\n"
                        "```"
                    )
                }
            }
        ]
    }

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="create a skull and cross bones image in ascii characters that laughs on the screen as skully.py",
        target_path="skully.py",
        existing_content="def say_hello():\n    print(\"Hello, Skully!\")\n",
        reply=reply,
    )

    assert source == "reply_code"
    assert "draw_skull" in content
    assert "HA HA HA" in content


def test_file_write_candidate_blocks_generic_stub_hijack_when_no_valid_artifact_exists():
    core = _core_for_intent_tests()
    core._generate_file_content_from_intent = lambda user_input, target_path: "def say_hello():\n    print(\"Hello, Skully!\")\n"

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="create a skull and cross bones image in ascii characters that laughs on the screen as skully.py",
        target_path="skully.py",
        existing_content="def say_hello():\n    print(\"Hello, Skully!\")\n",
        reply={},
    )

    assert content == ""
    assert source == "artifact_payload_hijack"


def test_file_write_candidate_reuses_stored_draft_artifact_on_later_save_turn():
    core = _core_for_intent_tests()
    core._generate_file_content_from_intent = lambda user_input, target_path: "def draw_skull(size):\n    print(\"*\")\n"

    MK1Core._set_last_draft_artifact(
        core,
        target_path="Big_Skully.py",
        content=(
            "def draw_skull():\n"
            "    print(\"x_x\")\n\n"
            "def draw_cross_bones():\n"
            "    print(\"/\\\\ /\\\\\")\n\n"
            "def open_mouth():\n"
            "    print(\"O O O O\")\n\n"
            "def close_mouth():\n"
            "    print(\"  o  \")\n"
        ),
        user_input="create a script that creates a big skull and cross bones image in ascii characters that opens and closes its mouth on the screen",
    )

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="create file big_skully.py save the script into it simple",
        target_path="big_skully.py",
        existing_content="",
        reply={},
    )

    assert source == "stored_draft"


def test_file_write_candidate_prefers_stored_draft_over_generated_save_stub_on_followup():
    core = _core_for_intent_tests()

    MK1Core._set_last_draft_artifact(
        core,
        target_path="screen_filler.py",
        content=(
            "import random\n"
            "import time\n"
            "import os\n\n"
            "def screen_filler():\n"
            "    chars = ['@', '#', '$', '%', '&', '*', '+', '-', '=', '!', '?']\n"
            "    for _ in range(10):\n"
            "        os.system('cls' if os.name == 'nt' else 'clear')\n"
            "        print('\\n'.join([''.join(random.choice(chars) * random.randint(1, 40)) for _ in range(20)]))\n"
            "        time.sleep(1)\n\n"
            "screen_filler()\n"
        ),
        user_input="TRAVIS Mina create a script call it screen_filler.py have it fill the screen with random ascii for ten seconds then exit.",
    )

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="save your script in screen_filler.py",
        target_path="screen_filler.py",
        existing_content=(
            "import os\n\n"
            "def fill_screen_with_character(char):\n"
            "    width = os.get_terminal_size().columns\n"
            "    height = os.get_terminal_size().lines\n"
            "    for _ in range(height):\n"
            "        print(char * width)\n\n"
            "if __name__ == \"__main__\":\n"
            "    fill_screen_with_character('*')\n"
        ),
        reply={},
    )

    assert source == "stored_draft"
    assert "chars = ['@', '#', '$', '%', '&', '*', '+', '-', '=', '!', '?']" in content
    assert "fill_screen_with_character" not in content


def test_file_write_candidate_uses_stored_draft_on_generic_save_followup_when_existing_is_empty():
    core = _core_for_intent_tests()

    MK1Core._set_last_draft_artifact(
        core,
        target_path="screen_filler.py",
        content=(
            "import random\n"
            "import time\n"
            "import os\n\n"
            "def screen_filler():\n"
            "    chars = ['@', '#', '$', '%', '&', '*', '+', '-', '=', '!', '?']\n"
            "    for _ in range(10):\n"
            "        os.system('cls' if os.name == 'nt' else 'clear')\n"
            "        print('\\n'.join([''.join(random.choice(chars) * random.randint(1, 40)) for _ in range(20)]))\n"
            "        time.sleep(1)\n\n"
            "screen_filler()\n"
        ),
        user_input="TRAVIS Mina create a script call it screen_filler.py have it fill the screen with random ascii for ten seconds then exit.",
    )

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="save your script in screen_filler.py",
        target_path="screen_filler.py",
        existing_content="",
        reply={},
    )

    assert source == "stored_draft"
    assert "screen_filler" in content
    assert "fill_screen_with_character" not in content


def test_file_write_candidate_accepts_generated_countdown_script_for_countdown_request():
    core = _core_for_intent_tests()
    core._generate_file_content_from_intent = lambda user_input, target_path: (
        "import time\n\n"
        "for i in range(10, -1, -1):\n"
        "    print(i)\n"
        "    time.sleep(1)\n\n"
        "print(\"Blastoff!\")\n"
    )

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py",
        target_path="countdown.py",
        existing_content="",
        reply={},
    )

    assert source == "generated"
    assert "range(10, -1, -1)" in content
    assert "time.sleep(1)" in content


def test_file_write_candidate_reuses_durable_memory_artifact_when_draft_cache_missing():
    core = _core_for_intent_tests()
    core._generate_file_content_from_intent = lambda user_input, target_path: "def draw_skull(size):\n    print(\"*\")\n"

    artifact_record = MK1Core._code_artifact_memory_record(
        core,
        "big_skully.py",
        "create a script that creates a big skull and cross bones image in ascii characters that opens and closes its mouth on the screen",
        (
            "def draw_skull():\n"
            "    print(\"x_x\")\n\n"
            "def draw_cross_bones():\n"
            "    print(\"/\\\\ /\\\\\")\n\n"
            "def open_mouth():\n"
            "    print(\"O O O O\")\n\n"
            "def close_mouth():\n"
            "    print(\"  o  \")\n"
        ),
    )
    core.memory = SimpleNamespace(
        add_memory=lambda *args, **kwargs: 1,
        find_memory_id_by_text=lambda *args, **kwargs: None,
        search=lambda *args, **kwargs: [{"text": artifact_record, "tags": ["code_artifact"], "kind": "procedure"}],
    )

    content, source = MK1Core._resolve_file_write_content_candidate(
        core,
        user_input="create file big_skully.py save the script into it simple",
        target_path="big_skully.py",
        existing_content="",
        reply={},
    )

    assert source == "memory_artifact"
    assert "draw_cross_bones" in content
    assert "open_mouth" in content
