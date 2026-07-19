from __future__ import annotations

from typing import Any, Dict, Iterable

from tools.ps_run import _normalize_command, run_ps


def _join_commands(commands: Any) -> str:
    if isinstance(commands, (list, tuple)):
        parts = [str(item).strip() for item in commands if str(item).strip()]
        return "\n".join(parts)
    return str(commands or "").strip()


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    command = args.get("command") or args.get("script") or ""
    commands = args.get("commands")

    if commands:
        command = _join_commands(commands)

    command = _normalize_command(command)
    timeout = args.get("timeout", 30)
    cwd = args.get("cwd")
    return run_ps(command, timeout, cwd)


tool_entry.schema = {
    "description": "Execute one or more PowerShell commands through a single interface.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "A PowerShell command or script block to execute.",
            },
            "commands": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Optional list of PowerShell commands to run in order.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory for the command.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (1–60).",
                "minimum": 1,
                "maximum": 60,
            },
        },
        "additionalProperties": False,
    },
}