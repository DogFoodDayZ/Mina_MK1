import os
import sys
from typing import Any, Dict
from pathlib import Path

# Add project root to path so imports work when run directly
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.file_access_policy import authorize_write_paths, normalize_tool_path

def file_delete_impl(path: str, authorized_write: bool = False, auth_token: str = "") -> Dict[str, Any]:
    try:
        if not path:
            return {"ok": False, "result": None, "error": "no_path_provided"}

        path = normalize_tool_path(path)

        allowed, error, _ = authorize_write_paths(
            [path],
            authorized_write=authorized_write,
            auth_token=auth_token,
        )
        if not allowed:
            return {"ok": False, "result": None, "error": error}

        if not os.path.exists(path):
            return {"ok": False, "result": None, "error": "file_not_found"}

        if os.path.isdir(path):
            return {"ok": False, "result": None, "error": "path_is_directory"}

        os.remove(path)

        return {
            "ok": True,
            "result": {"deleted": True, "path": path},
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    authorized_write = bool(args.get("authorized_write", False))
    auth_token = str(args.get("auth_token") or "")
    return file_delete_impl(path, authorized_write=authorized_write, auth_token=auth_token)


tool_entry.schema = {
    "description": "Delete a file at the given path.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to delete.",
            },
            "authorized_write": {
                "type": "boolean",
                "description": "Required for deletes outside E:\\workspace.",
                "default": False,
            },
            "auth_token": {
                "type": "string",
                "description": "Optional write authorization token for deletes outside E:\\workspace.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
