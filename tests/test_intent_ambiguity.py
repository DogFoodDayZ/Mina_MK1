from types import SimpleNamespace

from agent.core import MK1Core


def _core_with_spotify():
    core = MK1Core.__new__(MK1Core)
    core.ambiguity_ask_on_medium_low = True
    core.tools = SimpleNamespace(tools={"spotify_link": object()})
    return core


def _core_with_tools(tool_names):
    core = MK1Core.__new__(MK1Core)
    core.ambiguity_ask_on_medium_low = True
    core.tools = SimpleNamespace(tools={name: object() for name in tool_names})
    return core


def test_companion_play_list_request_is_explicit_music_intent():
    core = _core_with_spotify()
    prompt = """You are Mina acting as my Ostranauts co-pilot.
Keep the answer concise and practical for active play.
Question: we need a ostranauts play list can you create one with up beat techno tunes?"""

    assert core._maybe_build_ambiguity_prompt(prompt) is None


def test_mixed_game_play_request_prefers_game_context_without_spotify_clarifier():
    core = _core_with_spotify()

    assert core._maybe_build_ambiguity_prompt("can you play with the game interface?") is None


def test_casual_typo_play_text_stays_conversational():
    core = _core_with_spotify()

    assert core._maybe_build_ambiguity_prompt("play my bad fat fingers miss keys ..lol") is None


def test_bulk_genre_tracks_route_to_named_playlist():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("Add 10 upbeat techno tracks to my Ostranauts playlist")

    assert tool == "spotify_link"
    assert args == {
        "action": "add_tracks_to_playlist",
        "playlist_name": "Ostranauts",
        "track_query": "upbeat techno",
        "limit": 10,
    }


def test_explicit_spotify_command_ignores_stale_learned_alias():
    core = _core_with_spotify()
    phrase = "add 10 upbeat techno tracks to my ostranauts playlist"
    core._intent_alias_cache = {
        phrase: {
            "tool": "spotify_link",
            "args": {"action": "list_playlists", "limit": 50},
        }
    }

    tool, args = core.detect_tool_intent(phrase)

    assert tool == "spotify_link"
    assert args["action"] == "add_tracks_to_playlist"
    assert args["playlist_name"] == "ostranauts"


def test_ostranauts_query_does_not_use_spotify_alias_without_music_intent():
    core = _core_with_spotify()
    phrase = "how about ostranauts ?"
    core._intent_alias_cache = {
        phrase: {
            "tool": "spotify_link",
            "args": {"action": "play"},
        }
    }

    tool, _args = core.detect_tool_intent(phrase)

    assert tool != "spotify_link"


def test_ostranauts_query_with_explicit_music_intent_can_route_to_spotify():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("play music for my ostranauts session")

    assert tool == "spotify_link"
    assert args["action"] in {"play", "play_track"}


def test_play_ostranauts_prompt_does_not_hijack_to_spotify():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("want to play ostranauts the steam game ?")

    assert tool != "spotify_link"
    if tool == "__clarify_intent__":
        assert "spotify playback" not in str(args.get("question") or "").lower()


def test_grounded_tool_analysis_ignores_prior_conversation_history():
    class FakeModel:
        def __init__(self):
            self.last_messages = None

        def chat(self, messages, **_kwargs):
            self.last_messages = messages
            return {"choices": [{"message": {"content": "Mina saw the command output."}}]}

    core = MK1Core.__new__(MK1Core)
    fake_model = FakeModel()
    core.model = fake_model

    prior_messages = [
        {"role": "user", "content": "probe the CPU"},
        {"role": "assistant", "content": "Use Get-CimInstance -ClassName Win32_Processor."},
    ]

    text = core._render_grounded_tool_analysis(
        messages=prior_messages,
        user_input="run this powershell: Get-Date -Format o and explain what it means",
        tool_name="code_execute",
        formatted="VERIFIED OUTPUT:\n```text\n2026-07-29T12:34:56.7890000Z\n```",
        tool_ok=True,
    )

    assert "Mina saw the command output." in text
    assert fake_model.last_messages is not None
    joined = "\n".join(str(item.get("content") or "") for item in fake_model.last_messages if isinstance(item, dict))
    assert "probe the CPU" not in joined
    assert "Win32_Processor" not in joined
    assert "Get-Date -Format o" in joined


def test_play_ostanauts_misspelling_does_not_hijack_to_spotify():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("Mina want to play ostanauts ?")

    assert tool != "spotify_link"
    if tool == "__clarify_intent__":
        assert "spotify playback" not in str(args.get("question") or "").lower()


def test_bare_play_prompt_asks_neutral_clarification_when_spotify_exists():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("play")

    assert tool == "__clarify_intent__"
    q = str(args.get("question") or "").lower()
    assert "spotify playback" not in q
    assert "game" in q and "music" in q


def test_play_title_request_routes_directly_without_generic_play_clarify():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("play scissorhands")

    assert tool == "spotify_link"
    assert args["action"] == "play_track"
    assert args["track_query"] == "scissorhands"


def test_play_with_me_stays_conversational_not_spotify_hijack():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent("play with me")

    assert tool != "spotify_link"
    if tool == "__clarify_intent__":
        assert "what do you want to play" not in str(args.get("question") or "").lower()


def test_info_question_does_not_trigger_play_clarification():
    core = _core_with_spotify()

    assert core._maybe_build_ambiguity_prompt("what time is it?") is None


def test_meta_play_rule_is_learned_not_executed_immediately():
    core = _core_with_spotify()
    core._intent_alias_cache = {}

    tool, args = core.detect_tool_intent(
        "If i say 'play scissorhands' that means i want you to play the song."
    )

    assert tool == "__learn_play_song_shorthand__"
    assert args["phrase"] == "play scissorhands"
    assert args["track_query"] == "scissorhands"


def test_learned_play_shorthand_routes_to_exact_track_query():
    core = _core_with_spotify()
    core._intent_alias_cache = {}
    core._play_song_shorthand_cache = {"play scissorhands": "scissorhands"}

    tool, args = core.detect_tool_intent("play scissorhands")

    assert tool == "spotify_link"
    assert args["action"] == "play_track"
    assert args["track_query"] == "scissorhands"


def test_non_learnable_chitchat_alias_is_not_applied():
    core = _core_with_spotify()
    core._intent_alias_cache = {
        "yes thanks hottie": {
            "tool": "spotify_link",
            "args": {"action": "play"},
        }
    }

    tool, _args = core.detect_tool_intent("yes thanks hottie")

    assert tool != "spotify_link"


def test_info_query_alias_can_apply_for_general_learning():
    core = _core_with_tools(["code_execute"])
    core._intent_alias_cache = {
        "what weather should i expect": {
            "tool": "code_execute",
            "args": {"lang": "powershell", "script": "Get-Date"},
        }
    }

    tool, args = core.detect_tool_intent("what weather should i expect")

    assert tool == "__clarify_intent__"
    assert "learned shortcut" in str(args.get("question") or "").lower()

    tool, args = core.detect_tool_intent("yes")

    assert tool == "code_execute"
    assert args["script"] == "Get-Date"


def test_deterministic_route_beats_stale_alias_before_replay():
    core = _core_with_tools(["spotify_link", "ps_run"])
    core._intent_alias_cache = {
        "what time is it": {
            "tool": "spotify_link",
            "args": {"action": "play"},
        }
    }

    tool, args = core.detect_tool_intent("what time is it")

    assert tool == "ps_run"
    assert args["script"] == "what time is it"


def test_internal_virtual_alias_is_never_replayed():
    core = _core_with_tools(["spotify_link"])
    core._intent_alias_cache = {
        "play now": {
            "tool": "__mina_shutdown__",
            "args": {},
        }
    }

    tool, _args = core.detect_tool_intent("play now")

    assert tool != "__mina_shutdown__"


def test_fuzzy_alias_replay_handles_small_wording_variation():
    core = _core_with_tools(["code_execute"])
    core._intent_alias_cache = {
        "run mk1 diagnostics script": {
            "tool": "code_execute",
            "args": {"lang": "powershell", "script": "Get-Date"},
        }
    }

    tool, args = core.detect_tool_intent("please run the mk1 diagnostic script now")

    assert tool == "code_execute"
    assert args["script"] == "Get-Date"


def test_fuzzy_alias_replay_does_not_trigger_on_weak_overlap():
    core = _core_with_tools(["code_execute"])
    core._intent_alias_cache = {
        "run mk1 diagnostics script": {
            "tool": "code_execute",
            "args": {"lang": "powershell", "script": "Get-Date"},
        }
    }

    tool, _args = core.detect_tool_intent("how are you today")

    assert tool != "code_execute"


def test_alias_recall_from_memory_when_cache_misses_phrase_variant():
    core = _core_with_tools(["code_execute"])
    core._intent_alias_cache = {}

    record = core._intent_alias_record(
        phrase="run mk1 diagnostics script",
        tool="code_execute",
        args={"lang": "powershell", "script": "Get-Date"},
    )

    class _AliasMemory:
        def search(self, query, top_k=24, include_kinds=None, include_tags=None):
            return [{"text": record}]

        def recent_memories(self, top_k=200, include_kinds=None, include_tags=None):
            return []

    core.memory = _AliasMemory()

    tool, args = core.detect_tool_intent("please run the mk1 diagnostic script")

    assert tool == "code_execute"
    assert args["script"] == "Get-Date"


def test_spotify_play_started_without_track_details_is_not_reported_as_confirmed_playback():
    core = _core_with_spotify()

    msg = core._format_tool_result(
        "spotify_link",
        {
            "ok": True,
            "result": {
                "status": "play_started",
            },
            "error": None,
        },
    )

    low = msg.lower()
    assert "confirmed track details" in low
    assert "playback started" not in low