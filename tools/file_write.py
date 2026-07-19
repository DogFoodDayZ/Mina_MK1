import os
from typing import Any, Dict

def file_write_impl(path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
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

        # Ensure parent directory exists
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        if os.path.exists(path) and not overwrite:
            return {
                "ok": False,
                "result": {"written": False, "path": path},
                "error": "file_exists_no_overwrite"
            }

        # Write file
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")

        return {
            "ok": True,
            "result": {"written": True, "path": path},
            "error": None
        }

    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path") or ""
    content = args.get("content") or ""
    overwrite = bool(args.get("overwrite", True))
    return file_write_impl(path, content, overwrite=overwrite)


tool_entry.schema = {
    "description": "Write text content to a file, creating parent folders if needed.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to write.",
            },
            "content": {
                "type": "string",
                "description": "Content to write into the file.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Whether to overwrite existing files.",
                "default": True,
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
