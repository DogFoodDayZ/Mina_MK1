import os
from typing import Any, Dict


def dir_create_impl(path: str) -> Dict[str, Any]:
    try:
        if not path:
            return {"ok": False, "result": None, "error": "no_path_provided"}

        path = os.path.expanduser(os.path.expandvars(path))

        if not os.path.isabs(path):
            path = os.path.abspath(path)

        already_exists = os.path.isdir(path)
        os.makedirs(path, exist_ok=True)

        return {
            "ok": True,
            "result": {
                "created": True,
                "already_exists": already_exists,
                "path": path,
            },
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    return dir_create_impl(path)


tool_entry.schema = {
    "description": "Create a directory path if it does not already exist.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to create.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
