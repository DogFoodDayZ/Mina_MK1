import os
from typing import Any, Dict

def dir_list_impl(path: str) -> Dict[str, Any]:
    try:
        # If no path provided, default to the intended workspace root
        if not path:
            path = r"E:\workspace"

        items = os.listdir(path)
        return {"ok": True, "result": {"items": items}, "error": None}

    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    # Accept "path" or fallback to default
    path = args.get("path") or ""
    return dir_list_impl(path)
