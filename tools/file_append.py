import os
from typing import Any, Dict

def file_append_impl(path: str, content: str) -> Dict[str, Any]:
    try:
        if not path:
            return {"ok": False, "result": None, "error": "no_path_provided"}

        path = os.path.expanduser(os.path.expandvars(path))

        if not os.path.isabs(path):
            path = os.path.abspath(path)

        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        with open(path, "a", encoding="utf-8") as f:
            f.write(content or "")

        return {
            "ok": True,
            "result": {"appended": True, "path": path},
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    content = args.get("content") or ""
    return file_append_impl(path, content)
