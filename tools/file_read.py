import os
import sys
from typing import Any, Dict
from pathlib import Path

# Add project root to path so imports work when run directly
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.file_access_policy import normalize_tool_path
from tools.file_inspector import inspect_file

def file_read_impl(path: str, *, python_syntax_evidence: bool = False) -> Dict[str, Any]:
    try:
        # Reject empty paths early
        if not path:
            return {
                "ok": False,
                "result": None,
                "error": "no_path_provided"
            }

        path = normalize_tool_path(path)

        # Ensure file exists
        if not os.path.isfile(path):
            return {
                "ok": False,
                "result": None,
                "error": f"file_not_found: {path}"
            }

        inspected = (
            inspect_file(path, python_syntax_evidence=True)
            if python_syntax_evidence else inspect_file(path)
        )
        if not inspected.get("ok"):
            return inspected
        return inspected

    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    return file_read_impl(path, python_syntax_evidence=args.get("_python_syntax_evidence") is True)


tool_entry.schema = {
    "description": "Read text files with encoding detection, or return a grounded metadata/header summary for binary files.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to read.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
