from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


APP_ID = "1022980"
MAX_TELEMETRY_BYTES = 1024 * 1024
FRESH_SECONDS = 10.0


def is_ostranauts_query(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    return any(
        phrase in normalized
        for phrase in (
            "ostranauts",
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

    payload: Dict[str, Any] = {
        "ok": True,
        "installed": bool(resolved_game and (resolved_game / "Ostranauts.exe").is_file()),
        "game_dir": str(resolved_game) if resolved_game else None,
        "bepinex_installed": bool(resolved_game and (resolved_game / "BepInEx" / "core" / "BepInEx.dll").is_file()),
        "plugin_installed": bool(resolved_game and (resolved_game / "BepInEx" / "plugins" / "MinaOstranauts.dll").is_file()),
        "telemetry_path": str(resolved_telemetry) if resolved_telemetry else None,
        "telemetry_online": False,
        "telemetry_age_seconds": None,
        "telemetry": None,
        "error": None,
    }

    if resolved_telemetry is None or not resolved_telemetry.is_file():
        return payload

    try:
        size = resolved_telemetry.stat().st_size
        if size <= 0 or size > MAX_TELEMETRY_BYTES:
            payload["error"] = "telemetry_size_invalid"
            return payload

        parsed = json.loads(resolved_telemetry.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            payload["error"] = "telemetry_payload_invalid"
            return payload

        written_at = float(parsed.get("written_at_unix") or resolved_telemetry.stat().st_mtime)
        age = max(0.0, current_time - written_at)
        payload["telemetry"] = parsed
        payload["telemetry_age_seconds"] = round(age, 2)
        payload["telemetry_online"] = age <= FRESH_SECONDS
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        payload["error"] = f"telemetry_read_failed: {exc}"
        return payload


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

    crew = telemetry.get("crew")
    if isinstance(crew, list) and crew:
        crew_names = [
            str(member.get("name") or "unknown").strip()
            for member in crew
            if isinstance(member, dict)
        ]
        if crew_names:
            lines.append(f"- Crew: {', '.join(crew_names[:8])}")

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