import ast
import difflib
import hashlib
import os
import sys
from typing import Any, Dict
from pathlib import Path

# Add project root to path so imports work when run directly
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.file_access_policy import authorize_write_paths, normalize_tool_path
from tools.python_syntax import inspect_python_syntax


def _change_evidence(before: bytes | None, after: bytes) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {
        "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
        "after_sha256": hashlib.sha256(after).hexdigest(),
        "source": "actual pre-write and read-back bytes",
    }
    try:
        old = (before or b"").decode("utf-8-sig").replace("\r\n", "\n")
        new = after.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return {**evidence, "diff_available": False,
                "reason": "Previous content is not UTF-8; byte hashes do not establish a textual diagnosis."}
    limit = 3000
    chunks = []
    length = 0
    truncated = False
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile="before", tofile="saved", n=2, lineterm="",
    ):
        piece = line + "\n"
        remaining = limit - length
        chunks.append(piece[:remaining])
        length += min(len(piece), remaining)
        if len(piece) > remaining:
            truncated = True
            break
    return {
        **evidence, "diff_available": True, "unified_diff": "".join(chunks), "truncated": truncated,
        "before_final_newline": old.endswith("\n"), "after_final_newline": new.endswith("\n"),
        "note": "UTF-8 text diff normalizes CRLF; hashes identify exact bytes. A diff is not a diagnosis.",
    }


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
    expected_sha256: str = "",
    python_syntax_evidence: bool = False,
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

        previous_bytes = Path(path).read_bytes() if os.path.isfile(path) else None
        if expected_sha256 and (
            previous_bytes is None or hashlib.sha256(previous_bytes).hexdigest() != expected_sha256
        ):
            return {"ok": False, "result": {"written": False, "path": path},
                    "error": "file_write_source_changed"}

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

        with open(path, "rb") as f:
            written_bytes = f.read()
        actual_content = written_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if actual_content != normalized_content.replace("\r\n", "\n").replace("\r", "\n"):
            return {"ok": False, "result": {"written": True, "path": path},
                    "error": "file_write_verification_failed"}
        change_evidence = _change_evidence(previous_bytes, written_bytes)
        if python_syntax_evidence and Path(path).suffix.lower() == ".py":
            change_evidence["python_syntax_check"] = {
                "before": inspect_python_syntax(previous_bytes, path) if previous_bytes is not None else None,
                "saved": inspect_python_syntax(written_bytes, path),
            }
        return {
            "ok": True,
            "result": {
                "written": True, "path": path,
                "changed": previous_bytes != written_bytes,
                "change_evidence": change_evidence,
                "verification": {
                    "exists": True, "content_matches": True,
                    "sha256": hashlib.sha256(written_bytes).hexdigest(),
                    "bytes": len(written_bytes),
                    "first_line": actual_content.splitlines()[0][:160] if actual_content else "",
                },
            },
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
        expected_sha256=args.get("expected_sha256", ""),
        python_syntax_evidence=args.get("_python_syntax_evidence") is True,
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
            "expected_sha256": {
                "type": "string",
                "description": "Optional expected current file SHA-256; reject stale edits before writing.",
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
