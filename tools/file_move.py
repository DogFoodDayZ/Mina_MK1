import os
from typing import Any, Dict

def file_move_impl(src: str, dst: str) -> Dict[str, Any]:
    try:
        if not src:
            return {"ok": False, "result": None, "error": "no_source_path_provided"}
        if not dst:
            return {"ok": False, "result": None, "error": "no_destination_path_provided"}

        src = os.path.expanduser(os.path.expandvars(src))
        dst = os.path.expanduser(os.path.expandvars(dst))

        if not os.path.isabs(src):
            src = os.path.abspath(src)
        if not os.path.isabs(dst):
            dst = os.path.abspath(dst)

        if not os.path.exists(src):
            return {"ok": False, "result": None, "error": "source_not_found"}
        if os.path.isdir(src):
            return {"ok": False, "result": None, "error": "source_is_directory"}

        parent = os.path.dirname(dst)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        os.replace(src, dst)

        return {
            "ok": True,
            "result": {"moved": True, "src": src, "dst": dst},
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    src = args.get("src") or ""
    dst = args.get("dst") or ""
    return file_move_impl(src, dst)
