"""Apply an authorized single-file Python repair and verify the actual saved bytes."""
import hashlib
from pathlib import Path
from typing import Any, Dict

from tools import code_execute, coding_specialist, file_write
from tools.file_access_policy import authorize_write_paths, normalize_tool_path


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path")
    objective = args.get("objective")
    diagnostics = args.get("diagnostics", "")
    lines = args.get("expected_lines")
    newline = args.get("append_newline", True)
    timeout = args.get("timeout", 5)
    if not isinstance(path, str) or not path.strip() or not isinstance(objective, str) or not objective.strip():
        return {"ok": False, "error": "code_repair_requires_path_and_objective"}
    if not isinstance(diagnostics, str) or not isinstance(newline, bool):
        return {"ok": False, "error": "code_repair_invalid_diagnostics_or_append_newline"}
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30:
        return {"ok": False, "error": "code_repair_timeout_must_be_1_to_30_seconds"}
    if lines is not None and (
        not isinstance(lines, list) or not all(
            isinstance(line, str) and "\r" not in line and "\n" not in line for line in lines
        )
    ):
        return {"ok": False, "error": "code_repair_expected_lines_must_be_strings_without_line_breaks"}
    target = Path(normalize_tool_path(path))
    if target.suffix.lower() != ".py" or not target.is_file():
        return {"ok": False, "error": "code_repair_requires_existing_python_file"}
    authorized = args.get("authorized_write", False)
    token = args.get("auth_token", "")
    allowed, error, _ = authorize_write_paths([str(target)], authorized, token)
    if not allowed:
        return {"ok": False, "error": error}
    proof: Dict[str, Any] = {
        "path": str(target), "saved": False, "tested": False, "changed": False,
        "steps": [], "attempts": 0, "changes": [],
        "verification_level": "expected_output" if lines is not None else "exit_code_only",
    }

    def step(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = {"coding_specialist": coding_specialist, "file_write": file_write,
                "code_execute": code_execute}[name]
        if args.get("_python_syntax_evidence") is True and name in {"file_write", "code_execute"}:
            arguments = {**arguments, "_python_syntax_evidence": True}
        result = tool.tool_entry(arguments)
        proof["steps"].append({"tool": name, "result": result})
        return result

    try:
        for attempt in range(2):
            proof["attempts"] = attempt + 1
            consultation = step("coding_specialist", {
                "path": str(target), "objective": objective, "diagnostics": diagnostics,
            })
            if not consultation.get("ok"):
                return {"ok": False, "result": proof, "error": consultation.get("error")}
            assessment = consultation["result"]
            proof["model"] = assessment["model"]
            if assessment["status"] == "clarify":
                return {"ok": False, "result": {**proof, "questions": assessment["questions"]},
                        "error": "code_repair_requires_clarification"}
            if assessment["status"] == "proposal":
                write = step("file_write", {
                    "path": str(target), "content": assessment["proposed_content"],
                    "expected_sha256": assessment["source_sha256"],
                    "authorized_write": authorized, "auth_token": token,
                })
                receipt = write.get("result") or {}
                proof["saved"] = proof["saved"] or bool(receipt.get("written"))
                proof["changed"] = proof["changed"] or bool(receipt.get("changed"))
                if "verification" in receipt:
                    proof["verification"] = receipt["verification"]
                if "change_evidence" in receipt:
                    proof["changes"].append(receipt["change_evidence"])
                if not write.get("ok"):
                    return {"ok": False, "result": proof, "error": write.get("error")}
                revision = receipt["verification"]["sha256"]
            else:
                revision = assessment["source_sha256"]
            if hashlib.sha256(target.read_bytes()).hexdigest() != revision:
                return {"ok": False, "result": proof, "error": "code_repair_source_changed_before_run"}
            run_args: Dict[str, Any] = {"path": str(target), "mode": "run", "timeout": timeout}
            if lines is not None:
                run_args.update(expected_lines=lines, append_newline=newline)
            run = step("code_execute", run_args)
            proof["tested"] = True
            for field in ("stdout", "stderr", "exit_code", "sha256", "expected_stdout"):
                if field in run:
                    proof[field] = run[field]
            if run.get("sha256") != revision:
                return {"ok": False, "result": proof, "error": "code_repair_tested_revision_mismatch"}
            if hashlib.sha256(target.read_bytes()).hexdigest() != revision:
                return {"ok": False, "result": proof, "error": "code_repair_source_changed_during_run"}
            if run.get("ok"):
                return {"ok": True, "result": proof, "error": None}
            if run.get("error") in ("timeout", "file_changed_during_verification"):
                return {"ok": False, "result": proof, "error": run["error"]}
            diagnostics = (
                f"Actual saved-file test failed: {run.get('error')}\n"
                f"stdout: {run.get('stdout', '')}\nstderr: {run.get('stderr', '')}"
            )
        return {"ok": False, "result": proof, "error": "code_repair_verification_failed_after_two_attempts"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "result": proof, "error": f"code_repair_{type(exc).__name__}: {exc}"}


tool_entry.schema = {
    "description": (
        "Fix an existing Python file when the user authorizes editing AND execution. "
        "Owns the complete read -> coding specialist -> revision-checked save -> saved-file test "
        "workflow, with at most one correction retry using actual errors. Prefer this over "
        "manually alternating consultation/write/run for repairs. Never use for review-only, "
        "ordinary save, planning, or persistent/interactive programs. Not an OS sandbox. "
        "Include expected_lines when known; otherwise exit zero alone is not behavior proof."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Existing authorized Python file."},
            "objective": {"type": "string", "description": "Agreed behavior, not a guessed diagnosis."},
            "diagnostics": {"type": "string", "description": "Optional actual error output."},
            "expected_lines": {"type": "array", "items": {"type": "string"},
                               "description": "Exact stdout lines without line-break characters."},
            "append_newline": {"type": "boolean", "default": True},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 30, "default": 5},
            "authorized_write": {"type": "boolean", "default": False},
            "auth_token": {"type": "string"},
        },
        "required": ["path", "objective"],
        "additionalProperties": False,
    },
}
