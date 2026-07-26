import json

from agent.ostranauts import (
    format_ostranauts_context,
    get_ostranauts_status,
    is_ostranauts_query,
)


def test_ostranauts_status_reports_fresh_read_only_telemetry(tmp_path):
    game_dir = tmp_path / "Ostranauts"
    plugin_dir = game_dir / "BepInEx" / "plugins"
    core_dir = game_dir / "BepInEx" / "core"
    telemetry = game_dir / "BepInEx" / "config" / "MinaOstranauts" / "telemetry.json"
    plugin_dir.mkdir(parents=True)
    core_dir.mkdir(parents=True)
    telemetry.parent.mkdir(parents=True)
    (game_dir / "Ostranauts.exe").write_bytes(b"")
    (core_dir / "BepInEx.dll").write_bytes(b"")
    (plugin_dir / "MinaOstranauts.dll").write_bytes(b"")
    telemetry.write_text(
        json.dumps({"written_at_unix": 100.0, "scene": "MainGame", "bridge": "online"}),
        encoding="utf-8",
    )

    status = get_ostranauts_status(game_dir=game_dir, telemetry_path=telemetry, now=104.0)

    assert status["installed"] is True
    assert status["bepinex_installed"] is True
    assert status["plugin_installed"] is True
    assert status["telemetry_online"] is True
    assert status["telemetry_age_seconds"] == 4.0
    assert status["telemetry"]["scene"] == "MainGame"


def test_ostranauts_status_marks_old_telemetry_offline(tmp_path):
    game_dir = tmp_path / "Ostranauts"
    game_dir.mkdir()
    (game_dir / "Ostranauts.exe").write_bytes(b"")
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(json.dumps({"written_at_unix": 10.0}), encoding="utf-8")

    status = get_ostranauts_status(game_dir=game_dir, telemetry_path=telemetry, now=30.0)

    assert status["telemetry_online"] is False
    assert status["telemetry_age_seconds"] == 20.0


def test_ostranauts_context_includes_only_bounded_active_objectives():
    status = {
        "telemetry_online": True,
        "telemetry": {
            "scene": "CrewSim",
            "game_focused": True,
            "objectives": [
                {"name": "Finished tutorial", "description": "Done", "complete": True},
                {"name": "The Ceres Affair", "description": "Have you seen the tape?", "complete": False},
                {"name": "Matchmaker", "description": "Talk to Bo Conway", "complete": False},
            ],
            "diagnostics": ["player_unavailable", "ship_unavailable"],
        },
    }

    context = format_ostranauts_context(status, max_objectives=1)

    assert "Scene: CrewSim" in context
    assert "The Ceres Affair" in context
    assert "Finished tutorial" not in context
    assert "Matchmaker" not in context
    assert "player_unavailable" in context
    assert "showing 1 of 2" in context
    assert "use only these observed facts" in context


def test_ostranauts_context_ignores_offline_telemetry():
    status = {
        "telemetry_online": False,
        "telemetry": {"scene": "CrewSim"},
    }

    assert format_ostranauts_context(status) == ""


def test_ostranauts_query_detection_is_explicit():
    assert is_ostranauts_query("What active objectives do you see?") is True
    assert is_ostranauts_query("What is happening in Ostranauts?") is True
    assert is_ostranauts_query("Remind me about tomorrow's errands") is False