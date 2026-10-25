"""Non-executing Python compiler observations for exact source bytes."""
import hashlib
import sys
from typing import Any


MAX_SOURCE_BYTES = 50_000


def inspect_python_syntax(source: bytes, filename: str) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "method": "compile(bytes, snapshot label, 'exec', dont_inherit=True); code object not executed",
        "source_path": filename,
        "python_version": sys.version.split()[0],
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_bytes": len(source),
        "executed": False,
        "scope": "Syntax only; passing does not verify runtime behavior, imports, output or correctness.",
        "syntax_valid": None,
        "diagnostic": None,
    }
    if len(source) > MAX_SOURCE_BYTES:
        return {**facts, "checked": False, "error": "python_syntax_source_exceeds_50000_bytes"}
    try:
        # Python 3.14 can reread filename for SyntaxError.text after a repair write.
        compile(source, "<python-syntax-snapshot>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        return {
            **facts, "checked": True, "syntax_valid": False,
            "diagnostic": {
                "type": type(exc).__name__, "message": exc.msg[:500],
                "line": exc.lineno, "offset": exc.offset,
                "end_line": exc.end_lineno, "end_offset": exc.end_offset,
                "source_line": (exc.text or "")[:500],
            },
        }
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        return {**facts, "checked": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    return {**facts, "checked": True, "syntax_valid": True}
