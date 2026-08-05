import ast
import os
import sys
from typing import Any, Dict
from pathlib import Path

# Add project root to path so imports work when run directly
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.file_access_policy import authorize_write_paths, normalize_tool_path


def _normalize_write_content(content: Any) -> str:
    text = str(content or "")
    trimmed = text.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
        if any(token in trimmed for token in ["\\n", "\\r", "\\t", "\\\""]):
            try:
                parsed = ast.literal_eval(trimmed)
                if isinstance(parsed, str):
                    return parsed
            except Exception:
                pass
    return text

def file_write_impl(
    path: str,
    content: str,
    overwrite: bool = True,
    authorized_write: bool = False,
    auth_token: str = "",
) -> Dict[str, Any]:
    try:
        # Reject empty paths early
        if not path:
            return {
                "ok": False,
                "result": None,
                "error": "no_path_provided"
            }

        path = normalize_tool_path(path)

        allowed, error, _ = authorize_write_paths(
            [path],
            authorized_write=authorized_write,
            auth_token=auth_token,
        )
        if not allowed:
            return {"ok": False, "result": None, "error": error}

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

        normalized_content = _normalize_write_content(content)

        # Write file
        with open(path, "w", encoding="utf-8") as f:
            f.write(normalized_content)

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
    authorized_write = bool(args.get("authorized_write", False))
    auth_token = str(args.get("auth_token") or "")
    return file_write_impl(
        path,
        content,
        overwrite=overwrite,
        authorized_write=authorized_write,
        auth_token=auth_token,
    )


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
            "authorized_write": {
                "type": "boolean",
                "description": "Required for writes outside E:\\workspace.",
                "default": False,
            },
            "auth_token": {
                "type": "string",
                "description": "Optional write authorization token for writes outside E:\\workspace.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
