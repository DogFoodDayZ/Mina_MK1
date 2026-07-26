from types import SimpleNamespace

from agent.core import MK1Core


def _core_with_spotify():
    core = MK1Core.__new__(MK1Core)
    core.ambiguity_ask_on_medium_low = True
    core.tools = SimpleNamespace(tools={"spotify_link": object()})
    return core


def test_companion_play_list_request_is_explicit_music_intent():
    core = _core_with_spotify()
    prompt = """You are Mina acting as my Ostranauts co-pilot.
Keep the answer concise and practical for active play.
Question: we need a ostranauts play list can you create one with up beat techno tunes?"""

    assert core._maybe_build_ambiguity_prompt(prompt) is None


def test_mixed_game_play_request_still_requires_clarification():
    core = _core_with_spotify()

    assert core._maybe_build_ambiguity_prompt("can you play with the game interface?") is not None


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