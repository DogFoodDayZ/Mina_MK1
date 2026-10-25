import subprocess
import sys
import os
import tempfile
import platform
import hashlib
import io
import tokenize
from pathlib import Path
from typing import Any, Dict, Optional

from tools.file_access_policy import normalize_tool_path
from tools.python_syntax import inspect_python_syntax

MAX_OUTPUT_BYTES = 50_000  # 50KB max output
MAX_TIMEOUT = 120  # Max 2 minutes execution
MIN_TIMEOUT = 1


def _execute_python_file(
    path: str, mode: str, timeout: int, expected_stdout: Optional[str] = None,
    *, python_syntax_evidence: bool = False,
) -> Dict[str, Any]:
    """Check or run the saved bytes, retaining the tested revision's identity."""
    target = Path(normalize_tool_path(path)).resolve()
    proof: Dict[str, Any] = {"path": str(target), "mode": mode}
    if expected_stdout is not None:
        proof["expected_stdout"] = expected_stdout
    try:
        source = target.read_bytes()
        proof["sha256"] = hashlib.sha256(source).hexdigest()
        if python_syntax_evidence and target.suffix.lower() == ".py" and mode in {"inspect", "check"}:
            proof["python_syntax_check"] = inspect_python_syntax(source, str(target))
        if mode == "inspect":
            encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
            content = source.decode(encoding)
            return {**proof, "ok": True, "exit_code": None,
                    "stdout": content[:MAX_OUTPUT_BYTES] + (
                        "\n[SOURCE TRUNCATED]" if len(content) > MAX_OUTPUT_BYTES else ""
                    ), "stderr": "", "error": None}
        if mode == "check":
            compile(source, str(target), "exec")
            return {**proof, "ok": True, "exit_code": 0, "stdout": "Python syntax check passed.",
                    "stderr": "", "error": None}
        result = subprocess.run(
            [sys.executable, str(target)], capture_output=True, text=True,
            timeout=timeout, cwd=str(target.parent),
        )
        unchanged = target.read_bytes() == source
        output_matches = expected_stdout is None or result.stdout == expected_stdout
        stderr = result.stderr
        if not output_matches:
            stderr += (
                f"\nOutput assertion failed despite exit code {result.returncode}."
                f"\nExpected stdout ({len(expected_stdout or '')} chars): {expected_stdout!r}"
                f"\nActual stdout ({len(result.stdout)} chars): {result.stdout!r}"
            )
        return {
            **proof, "ok": result.returncode == 0 and unchanged and output_matches,
            "exit_code": result.returncode,
            "stdout": result.stdout[:MAX_OUTPUT_BYTES] + (
                "\n[OUTPUT TRUNCATED]" if len(result.stdout) > MAX_OUTPUT_BYTES else ""
            ),
            "stderr": stderr[:MAX_OUTPUT_BYTES] + (
                "\n[ERROR OUTPUT TRUNCATED]" if len(stderr) > MAX_OUTPUT_BYTES else ""
            ),
            "error": (
                f"process_exited_with_code_{result.returncode}" if result.returncode
                else "file_changed_during_verification" if not unchanged
                else "stdout_mismatch" if not output_matches else None
            ),
        }
    except subprocess.TimeoutExpired as exc:
        def output(value: Any) -> str:
            return (value.decode("utf-8", errors="replace") if isinstance(value, bytes)
                    else str(value or ""))[:MAX_OUTPUT_BYTES]
        return {**proof, "ok": False, "exit_code": -1, "stdout": output(exc.stdout),
                "stderr": output(exc.stderr) + f"\nExecution timed out after {timeout} seconds",
                "error": "timeout"}
    except (OSError, SyntaxError, ValueError, UnicodeError) as exc:
        return {**proof, "ok": False, "exit_code": -1, "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}", "error": type(exc).__name__}


def _is_dangerous(code: str, language: str) -> tuple[bool, str]:
    """Check for obviously dangerous operations (not comprehensive security)."""
    code_lower = code.lower()
    
    dangerous_patterns = {
        "python": [
            ("import os", "os.system(", "subprocess."),  # Shell execution
            ("__import__", "exec(", "eval("),  # Dynamic execution
            ("open(", "remove(", "unlink("),  # File deletion (allow write/read)
        ],
        "powershell": [
            ("remove-item", "del ", "rm "),  # Destructive ops
            ("format-volume", "diskpart", "cipher /w"),  # System destruction
        ],
    }
    
    patterns = dangerous_patterns.get(language, [])
    for pattern in patterns:
        for p in pattern:
            if p in code_lower:
                return True, f"Potentially dangerous pattern detected: '{p}'"
    
    return False, ""


def _execute_python(code: str, timeout: int) -> Dict[str, Any]:
    """Execute Python code in subprocess."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Truncate if too large
        if len(stdout) > MAX_OUTPUT_BYTES:
            stdout = stdout[:MAX_OUTPUT_BYTES] + "\n[OUTPUT TRUNCATED]"
        if len(stderr) > MAX_OUTPUT_BYTES:
            stderr = stderr[:MAX_OUTPUT_BYTES] + "\n[ERROR OUTPUT TRUNCATED]"
        
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": None if result.returncode == 0 else f"process_exited_with_code_{result.returncode}",
        }
    
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "error": "timeout",
        }
    
    except Exception as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "error": type(e).__name__,
        }


def _execute_powershell(code: str, timeout: int) -> Dict[str, Any]:
    """Execute PowerShell code in subprocess."""
    try:
        # Use PowerShell's -NoProfile and -Command for lean execution
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "RemoteSigned",
            "-Command",
            code,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Truncate if too large
        if len(stdout) > MAX_OUTPUT_BYTES:
            stdout = stdout[:MAX_OUTPUT_BYTES] + "\n[OUTPUT TRUNCATED]"
        if len(stderr) > MAX_OUTPUT_BYTES:
            stderr = stderr[:MAX_OUTPUT_BYTES] + "\n[ERROR OUTPUT TRUNCATED]"
        
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": None if result.returncode == 0 else f"process_exited_with_code_{result.returncode}",
        }
    
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "error": "timeout",
        }
    
    except Exception as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "error": type(e).__name__,
        }


def code_execute_impl(
    code: str,
    language: str = "python",
    timeout: int = 30,
    check_dangerous: bool = True,
) -> Dict[str, Any]:
    """
    Execute code in isolated subprocess.
    
    Args:
        code: Source code to execute
        language: "python" or "powershell"
        timeout: Max execution time in seconds (1-120)
        check_dangerous: Warn about dangerous patterns
    
    Returns:
        {
            ok: bool,
            exit_code: int,
            stdout: str,
            stderr: str,
            error: str or None,
            warning: str or None,
        }
    """
    if not code or not isinstance(code, str):
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "No code provided",
            "error": "no_code",
            "warning": None,
        }
    
    language = (language or "python").lower().strip()
    if language not in ("python", "powershell", "ps"):
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Unsupported language: {language}. Use 'python' or 'powershell'.",
            "error": "unsupported_language",
            "warning": None,
        }
    
    if timeout < MIN_TIMEOUT or timeout > MAX_TIMEOUT:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timeout must be between {MIN_TIMEOUT} and {MAX_TIMEOUT} seconds",
            "error": "invalid_timeout",
            "warning": None,
        }
    
    # Check for dangerous patterns
    warning = None
    if check_dangerous:
        is_dangerous, reason = _is_dangerous(code, language)
        if is_dangerous:
            warning = f"⚠️ Warning: {reason}"
    
    # Execute
    if language == "python":
        result = _execute_python(code, timeout)
    else:  # powershell or ps
        result = _execute_powershell(code, timeout)
    
    result["warning"] = warning
    return result


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    """Tool loader interface."""
    code = str(args.get("code") or "").strip()
    language = str(args.get("language") or "python").strip().lower()
    
    try:
        timeout = int(args.get("timeout", 30))
    except Exception:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Invalid timeout type (must be int)",
            "error": "invalid_timeout_type",
            "warning": None,
        }
    
    check_dangerous = args.get("check_dangerous", True)
    if isinstance(check_dangerous, str):
        check_dangerous = check_dangerous.lower() not in ("false", "0", "no")
    else:
        check_dangerous = bool(check_dangerous)
    
    path = str(args.get("path") or "").strip()
    if path:
        mode = str(args.get("mode") or "run")
        if code or language != "python" or mode not in ("inspect", "check", "run"):
            return {"ok": False, "error": "Use path without code, language=python, mode=inspect, check or run."}
        if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
            return {"ok": False, "error": "invalid_timeout"}
        expected_stdout = args.get("expected_stdout")
        expected_lines = args.get("expected_lines")
        if expected_lines is not None:
            if expected_stdout is not None or not isinstance(expected_lines, list) or not all(
                isinstance(line, str) and "\n" not in line and "\r" not in line
                for line in expected_lines
            ):
                return {"ok": False, "error": "expected_lines_requires_strings_without_line_breaks_and_no_expected_stdout"}
            append_newline = args.get("append_newline", True)
            if not isinstance(append_newline, bool):
                return {"ok": False, "error": "append_newline_must_be_boolean"}
            expected_stdout = "\n".join(expected_lines) + (
                "\n" if append_newline and expected_lines else ""
            )
        if expected_stdout is not None and not isinstance(expected_stdout, str):
            return {"ok": False, "error": "expected_stdout_must_be_a_string"}
        return _execute_python_file(
            path, mode, timeout, expected_stdout=expected_stdout,
            python_syntax_evidence=args.get("_python_syntax_evidence") is True,
        )

    return code_execute_impl(
        code=code,
        language=language,
        timeout=timeout,
        check_dangerous=check_dangerous,
    )


tool_entry.schema = {
    "description": (
        "Run code in a subprocess, not an OS sandbox. For saved Python use path instead of code: "
        "inspect starts authorized repair by reading source; check compiles only; run executes "
        "the file and returns SHA-256/exit/output. Declare expected_lines on inspect/run when output "
        "is known; use array entries for lines, not escaped newline characters. Timeout is failure."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Source code to execute.",
            },
            "path": {
                "type": "string",
                "description": "Saved Python file to check or run. Do not also supply code.",
            },
            "mode": {
                "type": "string",
                "enum": ["inspect", "check", "run"],
                "description": "For path: begin repair with a source read, syntax-only check, or bounded execution.",
                "default": "run",
            },
            "expected_lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact output lines, without line-break characters. Empty entries represent blank lines.",
            },
            "append_newline": {
                "type": "boolean",
                "default": True,
                "description": "Expect one final newline after the joined expected_lines.",
            },
            "language": {
                "type": "string",
                "description": "Code language to execute.",
                "enum": ["python", "powershell", "ps"],
                "default": "python",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds.",
                "minimum": 1,
                "maximum": 120,
                "default": 30,
            },
            "check_dangerous": {
                "type": "boolean",
                "description": "Warn on obviously dangerous patterns.",
                "default": True,
            },
        },
        "anyOf": [{"required": ["code"]}, {"required": ["path"]}],
        "additionalProperties": False,
    },
}
