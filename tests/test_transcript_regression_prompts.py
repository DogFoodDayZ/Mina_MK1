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
