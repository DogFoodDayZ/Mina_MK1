import subprocess
import sys
import os
import tempfile
import platform
from typing import Any, Dict, Optional


MAX_OUTPUT_BYTES = 50_000  # 50KB max output
MAX_TIMEOUT = 120  # Max 2 minutes execution
MIN_TIMEOUT = 1


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
            "error": None,
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
            "error": None,
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
        timeout = int(args.get("timeout", 30) or 30)
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
    
    return code_execute_impl(
        code=code,
        language=language,
        timeout=timeout,
        check_dangerous=check_dangerous,
    )
