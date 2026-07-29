from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


APP_ID = "1022980"
MAX_TELEMETRY_BYTES = 1024 * 1024
FRESH_SECONDS = 10.0


_CALIBRATION_CATEGORIES = (
    "skills",
    "training",
    "traits",
    "needs_mood",
    "injuries_health",
    "identity_flags",
    "plot_flags",
    "hidden_internal",
    "unknown",
)


def _normalize_condition_name(name: Any) -> str:
    return str(name or "").strip()


def _classify_condition(condition: Dict[str, Any]) -> str:
    name = _normalize_condition_name(condition.get("name"))
    lowered = name.lower()
    friendly = str(condition.get("friendly_name") or "").strip()
    description = str(condition.get("description") or "").strip()
    negative = bool(condition.get("negative"))
    fatal = bool(condition.get("fatal"))
    knockout = bool(condition.get("knockout"))

    if lowered.startswith("plot_"):
        return "plot_flags"
    if lowered.startswith("skill"):
        return "skills"
    if lowered.startswith("dctraining") or lowered.startswith("stattraining"):
        return "training"
    if "trait" in lowered:
        return "traits"

    if negative or fatal or knockout:
        return "injuries_health"

    need_tokens = (
        "hygiene",
        "hydration",
        "fatigue",
        "food",
        "heat",
        "blood",
        "infection",
        "pain",
        "stress",
        "morale",
        "hunger",
        "thirst",
        "oxygen",
        "toxic",
        "rad",
        "injur",
        "wound",
    )
    if any(token in lowered for token in need_tokens):
        return "needs_mood" if ("rate" in lowered or "coeff" in lowered) else "injuries_health"

    if lowered.startswith("is"):
        if friendly or description:
            return "identity_flags"
        return "hidden_internal"

    if lowered.startswith("stat"):
        # Stats with no display metadata are often internal mechanics.
        if not (friendly or description):
            return "hidden_internal"
        return "needs_mood"

    return "unknown"


def _calibrate_character_conditions(character: Dict[str, Any], max_examples: int = 6) -> Dict[str, Any]:
    rows = character.get("conditions") if isinstance(character.get("conditions"), list) else []
    counts: Dict[str, int] = {k: 0 for k in _CALIBRATION_CATEGORIES}
    examples: Dict[str, List[str]] = {k: [] for k in _CALIBRATION_CATEGORIES}

    for row in rows:
        if not isinstance(row, dict):
            continue
        category = _classify_condition(row)
        if category not in counts:
            category = "unknown"
        counts[category] += 1
        name = _normalize_condition_name(row.get("name"))
        if name and len(examples[category]) < max_examples and name not in examples[category]:
            examples[category].append(name)

    nonzero = {
        key: value for key, value in counts.items() if int(value) > 0
    }
    nonzero_examples = {
        key: value for key, value in examples.items() if value
    }
    return {
        "condition_total": sum(counts.values()),
        "counts": nonzero,
        "examples": nonzero_examples,
    }


def _build_condition_calibration(telemetry: Dict[str, Any], max_crew: int = 6) -> Dict[str, Any]:
    entities: List[Dict[str, Any]] = []

    player = telemetry.get("player")
    if isinstance(player, dict):
        entities.append(
            {
                "entity": str(player.get("name") or "player").strip() or "player",
                "life_state": _character_life_state(player),
                **_calibrate_character_conditions(player),
            }
        )

    crew = telemetry.get("crew")
    if isinstance(crew, list):
        for member in crew[: max(1, int(max_crew))]:
            if not isinstance(member, dict):
                continue
            entities.append(
                {
                    "entity": str(member.get("name") or "crew").strip() or "crew",
                    "life_state": _character_life_state(member),
                    **_calibrate_character_conditions(member),
                }
            )

    return {
        "schema_version": telemetry.get("schema_version"),
        "entity_count": len(entities),
        "entities": entities,
        "note": (
            "Preliminary condition calibration based on condition names plus available metadata. "
            "Use as read-only guidance; unknown/internal items remain unclassified until schema 4 metadata is live."
        ),
    }


def is_ostranauts_query(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False

    direct_name_signals = (
        "ostranauts",
        "ostranaut",
        "ostanauts",
        "ostanaut",
        "ostronauts",
        "ostronaut",
        "ostranaughts",
        "ostranaught",
    )
    if any(phrase in normalized for phrase in direct_name_signals):
        return True

    game_context = (
        "steam",
        "game",
        "objective",
        "crew",
        "ship",
        "bepinex",
        "telemetry",
    )
    if "astronauts" in normalized and any(token in normalized for token in game_context):
        return True

    return any(
        phrase in normalized
        for phrase in (
            "active objective",
            "game objective",
            "game state",
            "crew status",
            "ship status",
            "what should i do next in the game",
        )
    )


def discover_ostranauts_dir() -> Optional[Path]:
    configured = os.getenv("MK1_OSTRANAUTS_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if (candidate / "Ostranauts.exe").is_file():
            return candidate.resolve()

    steam_roots = [
        Path(r"C:\Program Files (x86)\Steam\steamapps"),
        Path(r"C:\Program Files\Steam\steamapps"),
    ]
    steam_roots.extend(Path(f"{letter}:\\SteamLibrary\\steamapps") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ")

    for steamapps in steam_roots:
        manifest = steamapps / f"appmanifest_{APP_ID}.acf"
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
            marker = '"installdir"'
            line = next((item for item in text.splitlines() if marker in item), "")
            install_name = line.split(marker, 1)[1].strip().strip('"') if line else "Ostranauts"
            candidate = steamapps / "common" / install_name
            if (candidate / "Ostranauts.exe").is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _telemetry_path(game_dir: Optional[Path]) -> Optional[Path]:
    configured = os.getenv("MK1_OSTRANAUTS_TELEMETRY", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if game_dir is None:
        return None
    return game_dir / "BepInEx" / "config" / "MinaOstranauts" / "telemetry.json"


def get_ostranauts_status(
    game_dir: Optional[Path] = None,
    telemetry_path: Optional[Path] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    resolved_game = Path(game_dir).resolve() if game_dir is not None else discover_ostranauts_dir()
    resolved_telemetry = Path(telemetry_path).resolve() if telemetry_path is not None else _telemetry_path(resolved_game)
    current_time = float(now if now is not None else time.time())
    telemetry_exists = bool(resolved_telemetry and resolved_telemetry.is_file())
    telemetry_size_bytes: Optional[int] = None
    telemetry_written_at_unix: Optional[float] = None

    game_installed = bool(resolved_game and (resolved_game / "Ostranauts.exe").is_file())
    bepinex_installed = bool(resolved_game and (resolved_game / "BepInEx" / "core" / "BepInEx.dll").is_file())
    plugin_installed = bool(resolved_game and (resolved_game / "BepInEx" / "plugins" / "MinaOstranauts.dll").is_file())

    payload: Dict[str, Any] = {
        "ok": True,
        "installed": game_installed,
        "game_dir": str(resolved_game) if resolved_game else None,
        "bepinex_installed": bepinex_installed,
        "plugin_installed": plugin_installed,
        "telemetry_path": str(resolved_telemetry) if resolved_telemetry else None,
        "telemetry_online": False,
        "telemetry_age_seconds": None,
        "telemetry": None,
        "condition_calibration": None,
        "error": None,
        "source_freshness": {
            "evaluated_at_unix": current_time,
            "fresh_seconds_threshold": FRESH_SECONDS,
            "telemetry": {
                "exists": telemetry_exists,
                "size_bytes": telemetry_size_bytes,
                "written_at_unix": telemetry_written_at_unix,
                "age_seconds": None,
                "online": False,
            },
            "sources": {
                "game_exe": game_installed,
                "bepinex_core": bepinex_installed,
                "mina_plugin": plugin_installed,
            },
        },
    }

    if resolved_telemetry is None or not resolved_telemetry.is_file():
        return payload

    try:
        size = resolved_telemetry.stat().st_size
        telemetry_size_bytes = int(size)
        payload["source_freshness"]["telemetry"]["size_bytes"] = telemetry_size_bytes
        if size <= 0 or size > MAX_TELEMETRY_BYTES:
            payload["error"] = "telemetry_size_invalid"
            return payload

        parsed = json.loads(resolved_telemetry.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            payload["error"] = "telemetry_payload_invalid"
            return payload

        written_at = float(parsed.get("written_at_unix") or resolved_telemetry.stat().st_mtime)
        telemetry_written_at_unix = written_at
        age = max(0.0, current_time - written_at)
        payload["telemetry"] = parsed
        payload["condition_calibration"] = _build_condition_calibration(parsed)
        payload["telemetry_age_seconds"] = round(age, 2)
        payload["telemetry_online"] = age <= FRESH_SECONDS
        payload["source_freshness"]["telemetry"]["written_at_unix"] = telemetry_written_at_unix
        payload["source_freshness"]["telemetry"]["age_seconds"] = payload["telemetry_age_seconds"]
        payload["source_freshness"]["telemetry"]["online"] = payload["telemetry_online"]
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        payload["error"] = f"telemetry_read_failed: {exc}"
        return payload


def _character_life_state(character: Dict[str, Any]) -> str:
    if character.get("destroyed") is True:
        return "destroyed"
    if character.get("alive") is True:
        return "living"
    if character.get("alive") is False:
        return "deceased/remains"
    return "unknown"


def _format_character(character: Dict[str, Any]) -> str:
    name = str(character.get("name") or "unknown").strip()
    return f"{name} ({_character_life_state(character)})"


def format_ostranauts_context(status: Dict[str, Any], max_objectives: int = 20) -> str:
    if not status.get("telemetry_online"):
        return ""

    telemetry = status.get("telemetry")
    if not isinstance(telemetry, dict):
        return ""

    lines = [
        "Live Ostranauts context (read-only and authoritative for current game-state questions):",
        f"- Scene: {str(telemetry.get('scene') or 'unknown').strip()}",
        f"- Game focused: {bool(telemetry.get('game_focused'))}",
    ]

    player = telemetry.get("player")
    if isinstance(player, dict):
        lines.append(f"- Player: {_format_character(player)}")

    crew = telemetry.get("crew")
    if isinstance(crew, list) and crew:
        crew_entries = [
            _format_character(member)
            for member in crew
            if isinstance(member, dict)
        ]
        if crew_entries:
            lines.append(f"- Crew collection: {', '.join(crew_entries[:8])}")

    objectives = telemetry.get("objectives")
    active_objectives = []
    if isinstance(objectives, list):
        active_objectives = [
            objective
            for objective in objectives
            if isinstance(objective, dict) and not bool(objective.get("complete"))
        ]

    if active_objectives:
        objective_limit = max(1, int(max_objectives))
        shown_objectives = active_objectives[:objective_limit]
        if len(shown_objectives) < len(active_objectives):
            lines.append(
                f"- Active objectives (showing {len(shown_objectives)} of {len(active_objectives)}):"
            )
        else:
            lines.append("- Active objectives:")
        for objective in shown_objectives:
            name = str(objective.get("name") or "Unnamed objective").strip()
            description = " ".join(str(objective.get("description") or "").split())
            if len(description) > 240:
                description = f"{description[:237]}..."
            lines.append(f"  - {name}: {description}" if description else f"  - {name}")
    else:
        lines.append("- Active objectives: none reported")

    diagnostics = telemetry.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        lines.append(f"- Unavailable data: {', '.join(str(item) for item in diagnostics[:8])}")

    calibration = status.get("condition_calibration")
    if isinstance(calibration, dict):
        entities = calibration.get("entities") if isinstance(calibration.get("entities"), list) else []
        if entities:
            primary = entities[0] if isinstance(entities[0], dict) else {}
            primary_name = str(primary.get("entity") or "player")
            counts = primary.get("counts") if isinstance(primary.get("counts"), dict) else {}
            if counts:
                top = sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:4]
                summary = ", ".join(f"{k}={v}" for k, v in top)
                lines.append(f"- Condition calibration ({primary_name}): {summary}")

    lines.append(
        "For Ostranauts questions, use only these observed facts; do not add names or details "
        "from memory, and say when requested data is unavailable."
    )
    return "\n".join(lines)


def build_ostranauts_context(max_objectives: int = 20) -> str:
    return format_ostranauts_context(
        get_ostranauts_status(),
        max_objectives=max_objectives,
    )