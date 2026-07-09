import os
from typing import Any, Dict

def file_delete_impl(path: str) -> Dict[str, Any]:
    try:
        if not path:
            return {"ok": False, "result": None, "error": "no_path_provided"}

        path = os.path.expanduser(os.path.expandvars(path))

        if not os.path.isabs(path):
            path = os.path.abspath(path)

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
    return file_delete_impl(path)
