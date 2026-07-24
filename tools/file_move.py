import os
from typing import Any, Dict

from tools.file_access_policy import authorize_write_paths, normalize_tool_path

def file_move_impl(
    src: str,
    dst: str,
    authorized_write: bool = False,
    auth_token: str = "",
) -> Dict[str, Any]:
    try:
        if not src:
            return {"ok": False, "result": None, "error": "no_source_path_provided"}
        if not dst:
            return {"ok": False, "result": None, "error": "no_destination_path_provided"}

        src = normalize_tool_path(src)
        dst = normalize_tool_path(dst)

        allowed, error, _ = authorize_write_paths(
            [src, dst],
            authorized_write=authorized_write,
            auth_token=auth_token,
        )
        if not allowed:
            return {"ok": False, "result": None, "error": error}

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
    authorized_write = bool(args.get("authorized_write", False))
    auth_token = str(args.get("auth_token") or "")
    return file_move_impl(
        src,
        dst,
        authorized_write=authorized_write,
        auth_token=auth_token,
    )


tool_entry.schema = {
    "description": "Move or rename a file from one path to another.",
    "parameters": {
        "type": "object",
        "properties": {
            "src": {
                "type": "string",
                "description": "Source file path.",
            },
            "dst": {
                "type": "string",
                "description": "Destination file path.",
            },
            "authorized_write": {
                "type": "boolean",
                "description": "Required for moves affecting paths outside E:\\workspace.",
                "default": False,
            },
            "auth_token": {
                "type": "string",
                "description": "Optional write authorization token for moves outside E:\\workspace.",
            },
        },
        "required": ["src", "dst"],
        "additionalProperties": False,
    },
}
