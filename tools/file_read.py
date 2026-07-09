import os
from typing import Any, Dict

def file_read_impl(path: str) -> Dict[str, Any]:
    try:
        # Reject empty paths early
        if not path:
            return {
                "ok": False,
                "result": None,
                "error": "no_path_provided"
            }

        # Expand ~ and environment variables
        path = os.path.expanduser(os.path.expandvars(path))

        # Normalize relative paths
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        # Ensure file exists
        if not os.path.isfile(path):
            return {
                "ok": False,
                "result": None,
                "error": f"file_not_found: {path}"
            }

        with open(path, "r", encoding="utf-8") as f:
            data = f.read()

        return {"ok": True, "result": {"content": data}, "error": None}

    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    return file_read_impl(path)
