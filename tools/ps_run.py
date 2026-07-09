import os
import re
import subprocess
from typing import Any, Dict


APPROVED_DIRECTORIES = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..')),
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')),
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agent')),
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'memory')),
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config')),
]


def _normalize_command(script: str) -> str:
    if not script or not isinstance(script, str):
        return ""

    text = script.strip()
    if not text:
        return ""

    lowered = text.lower().strip()

    if lowered.startswith("powershell:"):
        text = text.split(":", 1)[1].strip()
    elif lowered.startswith("ps:"):
        text = text.split(":", 1)[1].strip()

    if lowered in {"hardware info", "list hardware", "list the current hardware"}:
        return "Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer"

    if lowered in {"gpu info", "graphics info"}:
        return "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM"

    if lowered in {"cpu info", "processor info"}:
        return "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed"

    if lowered in {"memory info", "ram info"}:
        return "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory"

    if lowered in {"what time is it", "current time", "local time", "time now"}:
        return "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""

    if lowered in {"what date is it", "current date", "today's date", "todays date"}:
        return "Get-Date -Format \"yyyy-MM-dd\""

    if re.search(r"\bwhat\b.*\bcpu\b", lowered):
        return "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed"

    if re.search(r"\bwhat\b.*\bgpu\b", lowered):
        return "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM"

    if re.search(r"\bwhat\b.*\b(ram|memory)\b", lowered):
        return "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory"

    if re.search(r"\bwhat\b.*\b(system|spec|specs|os|platform|hardware)\b", lowered):
        return "Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer"

    if re.search(r"\b(what|current|local)\b.*\btime\b", lowered):
        return "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""

    if re.search(r"\b(what|current)\b.*\bdate\b", lowered):
        return "Get-Date -Format \"yyyy-MM-dd\""

    if re.search(r"\b(run|execute|show|list|get)\b", lowered):
        if "powershell" in lowered or "ps" in lowered:
            text = text.replace("powershell", "", 1).strip()
            text = text.replace("ps", "", 1).strip()

    if text.startswith("\"") and text.endswith("\""):
        text = text[1:-1]

    return text


def _is_approved_directory(cwd: str) -> bool:
    if not cwd:
        return True

    try:
        abs_cwd = os.path.abspath(cwd)
    except Exception:
        return False

    try:
        return any(os.path.commonpath([abs_cwd, approved]) == approved for approved in APPROVED_DIRECTORIES)
    except ValueError:
        return False


def run_ps(command: str, timeout: int = 30, cwd: str | None = None) -> Dict[str, Any]:
    # Validate command
    if not command or not isinstance(command, str):
        return {
            "ok": False,
            "result": None,
            "error": "no_command_provided"
        }

    # Validate timeout
    try:
        timeout = int(timeout)
    except Exception:
        return {
            "ok": False,
            "result": None,
            "error": "invalid_timeout_type"
        }

    # Enforce safe timeout range
    if timeout < 1 or timeout > 60:
        return {
            "ok": False,
            "result": None,
            "error": "timeout_out_of_range_1_to_60"
        }

    if cwd is not None and not _is_approved_directory(cwd):
        return {
            "ok": False,
            "result": None,
            "error": f"disallowed_directory: {cwd} is not in approved directories"
        }

    ps_cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]

    try:
        proc = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        return {
            "ok": proc.returncode == 0,
            "result": {
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "exit_code": proc.returncode,
            },
            "error": None if proc.returncode == 0 else proc.stderr.strip(),
        }

    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "result": {
                "stdout": (e.stdout or "").strip() if e.stdout else "",
                "stderr": (e.stderr or "").strip() if e.stderr else "",
                "exit_code": None,
            },
            "error": f"timeout_after_{timeout}_seconds",
        }

    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": f"exception: {type(e).__name__}: {str(e)}"
        }

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    command = args.get("command") or args.get("script") or ""
    command = _normalize_command(command)
    timeout = args.get("timeout", 30)
    cwd = args.get("cwd")
    return run_ps(command, timeout, cwd)

# Optional schema for better model behavior
tool_entry.schema = {
    "description": "Execute a PowerShell command with a safe timeout.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "PowerShell command to execute."
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (1–60).",
                "minimum": 1,
                "maximum": 60
            }
        },
        "required": ["command"]
    }
}
