import os
from typing import Any, Dict

from tools.file_access_policy import normalize_tool_path
from tools.file_inspector import inspect_file

def file_read_impl(path: str) -> Dict[str, Any]:
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

        inspected = inspect_file(path)
        if not inspected.get("ok"):
            return inspected
        return inspected

    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    return file_read_impl(path)


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
