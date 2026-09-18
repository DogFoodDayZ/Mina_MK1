from types import SimpleNamespace

from agent.core import MK1Core


def _core_with_tools(*tool_names):
    core = MK1Core.__new__(MK1Core)
    core.tools = SimpleNamespace(tools={name: object() for name in tool_names})
    core.ambiguity_ask_on_medium_low = True
    core._intent_alias_cache = {}
    return core


def test_capability_matrix_local_hardware_question_stays_model_owned_at_detection():
    core = _core_with_tools("ps_run", "web_search")

    tool, _args = core.detect_tool_intent("what CPU and GPU do I have?")

    assert tool is None


def test_capability_matrix_cpu_probe_keeps_boot_guard_route():
    core = _core_with_tools("ps_run")

    tool, args = core.detect_tool_intent("probe the CPU and see what comes up")

    assert tool == "ps_run"
    assert "Win32_Processor" in args["script"]


def test_capability_matrix_explicit_powershell_routes_to_local_tool():
    core = _core_with_tools("ps_run")

    tool, args = core.detect_tool_intent(
        "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM"
    )

    assert tool == "ps_run"
    assert "Win32_VideoController" in args["script"]


def test_capability_matrix_k2200_research_routes_to_web_search():
    core = _core_with_tools("web_search")

    tool, args = core.detect_tool_intent("look up the NVIDIA K2200 GPU")

    assert tool == "web_search"
    assert "K2200" in args["query"]


def test_capability_matrix_explicit_song_routes_to_spotify():
    core = _core_with_tools("spotify_link")

    tool, args = core.detect_tool_intent("play scissorhands")

    assert tool == "spotify_link"
    assert args == {"action": "play_track", "track_query": "scissorhands"}


def test_capability_matrix_bare_play_stays_neutral():
    core = _core_with_tools("spotify_link")

    tool, _args = core.detect_tool_intent("play")

    assert tool != "__clarify_intent__"


def test_capability_matrix_game_play_does_not_hijack_to_spotify():
    core = _core_with_tools("spotify_link")

    tool, _args = core.detect_tool_intent("want to play Ostranauts the Steam game")

    assert tool != "spotify_link"


def test_capability_matrix_memory_context_excludes_unrelated_recent_turns():
    class MemoryStub:
        def maintenance_tick(self):
            return None

        def recent_memories(self, **_kwargs):
            return [
                {"text": "An unrelated old refusal"},
                {"text": "The cobalt lantern protocol is our memory plan"},
            ]

        def search(self, _query, **_kwargs):
            return []

    core = MK1Core.__new__(MK1Core)
    core.memory = MemoryStub()

    context = core.build_context("what protocol is our memory plan")

    assert "cobalt lantern protocol" in context
    assert "unrelated old refusal" not in context
