"""Transcript: Read [path] and explain why it will not start. Do not edit or run it."""
import hashlib
import sys

import pytest

from agent.core import MK1Core
from tools import code_execute, code_repair, file_read, file_write
from tools.file_inspector import inspect_file
from tools.python_syntax import MAX_SOURCE_BYTES, inspect_python_syntax
from tools.tool_loader import ToolLoader


BROKEN = b"values = [1, 2, 3]\r\nfor value in values\r\n    print(value)\r\n"
FIXED = BROKEN.replace(b"in values\r\n", b"in values:\r\n")


def test_exact_compiler_diagnostic_not_indentation_guess():
    facts = inspect_python_syntax(BROKEN, "repair.py")
    assert facts["checked"] and facts["syntax_valid"] is False
    assert facts["source_sha256"] == hashlib.sha256(BROKEN).hexdigest()
    assert facts["diagnostic"]["type"] == "SyntaxError"
    assert facts["diagnostic"]["message"] == "expected ':'"
    assert facts["diagnostic"]["line"] == 2 and facts["diagnostic"]["offset"] == 20
    assert facts["executed"] is False and facts["python_version"] == sys.version.split()[0]
    assert inspect_python_syntax(FIXED, "repair.py")["syntax_valid"]


@pytest.mark.parametrize("source", [
    b"", b"print(sum(value))\n", b"raise RuntimeError('not executed')\n",
    b"# coding: latin-1\nname = 'caf\xe9'\n", b"\xef\xbb\xbfprint(6)\r\n",
])
def test_valid_syntax_is_not_execution_or_runtime_correctness(source, tmp_path):
    assert inspect_python_syntax(source, str(tmp_path / "source.py"))["syntax_valid"]
    assert not list(tmp_path.iterdir())


def test_compile_never_runs_imports_or_writes(tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\nimport missing_module\n"
    facts = inspect_python_syntax(source.encode(), str(tmp_path / "source.py"))
    assert facts["syntax_valid"] and facts["executed"] is False
    assert "Syntax only" in facts["scope"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("source", [b"\x00", b"# coding: unknown-codec\nprint(1)", b"\xff"])
def test_invalid_python_bytes_are_not_redecoded_to_hide_diagnostic(source):
    facts = inspect_python_syntax(source, "source.py")
    assert facts["checked"] and facts["syntax_valid"] is False and facts["diagnostic"]
    assert facts["source_sha256"] == hashlib.sha256(source).hexdigest()


def test_source_limit_is_explicit_and_diagnostics_are_bounded():
    facts = inspect_python_syntax(b"#" + b"x" * MAX_SOURCE_BYTES, "large.py")
    assert facts["checked"] is False and facts["syntax_valid"] is None and facts["error"]
    assert inspect_python_syntax(b"#" * MAX_SOURCE_BYTES, "limit.py")["syntax_valid"]
    facts = inspect_python_syntax(b"x" * 1000 + b" =\n", "source.py")
    assert len(facts["diagnostic"]["source_line"]) <= 500


def test_authorized_read_is_opt_in_same_bytes_and_truncation_scope(tmp_path):
    path = tmp_path / "source.py"
    path.write_bytes(BROKEN)
    assert "python_syntax_check" not in file_read.file_read_impl(str(path))["result"]
    receipt = file_read.file_read_impl(str(path), python_syntax_evidence=True)
    facts = receipt["result"]["python_syntax_check"]
    assert receipt["ok"] and receipt["result"]["content"].encode() == BROKEN
    assert facts["diagnostic"]["line"] == 2
    truncated = inspect_file(str(path), max_chars=4, python_syntax_evidence=True)["result"]
    assert truncated["truncated"] and truncated["python_syntax_check"] == facts
    assert path.read_bytes() == BROKEN and not (tmp_path / "__pycache__").exists()
    report = MK1Core._tool_action_report("file_read", {"path": str(path)}, receipt)
    assert report["python_syntax_check"] == facts


def test_file_read_compiles_the_original_read_not_a_reread(tmp_path, monkeypatch):
    path = tmp_path / "source.py"
    path.write_bytes(BROKEN)
    original = file_read.inspect_file

    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(FIXED)
        return result

    monkeypatch.setattr(file_read, "inspect_file", inspect)
    result = file_read.file_read_impl(str(path), python_syntax_evidence=True)["result"]
    assert result["python_syntax_check"]["source_sha256"] == hashlib.sha256(BROKEN).hexdigest()
    assert result["content"].encode() == BROKEN and path.read_bytes() == FIXED


def test_error_source_line_is_from_checked_bytes_not_current_file(tmp_path):
    path = tmp_path / "rewritten.py"
    path.write_bytes(FIXED)
    facts = inspect_python_syntax(BROKEN, str(path))
    assert facts["source_path"] == str(path)
    assert facts["diagnostic"]["source_line"].strip() == "for value in values"
    assert facts["diagnostic"]["line"] == 2 and facts["diagnostic"]["message"] == "expected ':'"
    assert facts["source_sha256"] == hashlib.sha256(BROKEN).hexdigest()
    assert path.read_bytes() == FIXED


def test_loader_default_off_preserves_args_and_schemas():
    loader = ToolLoader.__new__(ToolLoader)
    loader.python_syntax_evidence = False
    calls = []
    loader.tools = {"file_read": lambda args: calls.append(args) or {"ok": True}}
    loader.status = {}
    args = {"path": "source.py"}
    loader.run("file_read", args)
    assert calls[-1] == args
    loader.python_syntax_evidence = True
    loader.run("file_read", args)
    assert calls[-1]["_python_syntax_evidence"] is True
    assert args == {"path": "source.py"}
    assert "_python_syntax_evidence" not in file_read.tool_entry.schema["parameters"]["properties"]


def test_writes_and_nested_repair_keep_before_saved_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / "source.py"
    path.write_bytes(BROKEN)
    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", lambda args: {
        "ok": True, "result": {
            "status": "proposal", "model": "fixture",
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "proposed_content": FIXED.decode().replace("\r\n", "\n"),
        },
    })
    result = code_repair.tool_entry({
        "path": str(path), "objective": "Repair, save and test it.", "expected_lines": ["1", "2", "3"],
        "_python_syntax_evidence": True,
    })
    assert result["ok"]
    change = result["result"]["changes"][0]
    facts = change["python_syntax_check"]
    assert facts["before"]["syntax_valid"] is False and facts["saved"]["syntax_valid"]
    assert facts["before"]["diagnostic"]["source_line"].strip() == "for value in values"
    assert facts["before"]["source_sha256"] == change["before_sha256"]
    assert facts["saved"]["source_sha256"] == change["after_sha256"] == result["result"]["sha256"]
    report = MK1Core._tool_action_report("code_repair", {"path": str(path)}, result)
    assert report["changes"][0]["python_syntax_check"] == facts


@pytest.mark.parametrize("suffix", [".txt", ".csv"])
def test_non_python_reads_and_writes_are_unchanged(tmp_path, suffix, monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / ("source" + suffix)
    path.write_bytes(BROKEN)
    assert "python_syntax_check" not in file_read.file_read_impl(
        str(path), python_syntax_evidence=True,
    )["result"]
    result = file_write.file_write_impl(str(path), "hello", python_syntax_evidence=True)
    assert result["ok"] and "python_syntax_check" not in result["result"]["change_evidence"]


def test_new_file_and_stale_write_and_inspect_keep_proof_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / "new.py"
    write = file_write.file_write_impl(str(path), "raise RuntimeError()", python_syntax_evidence=True)
    facts = write["result"]["change_evidence"]["python_syntax_check"]
    assert facts["before"] is None and facts["saved"]["syntax_valid"]
    stale = file_write.file_write_impl(str(path), "pass", expected_sha256="stale",
                                      python_syntax_evidence=True)
    assert not stale["ok"] and stale["error"] == "file_write_source_changed"
    result = code_execute.tool_entry({"path": str(path), "mode": "inspect", "_python_syntax_evidence": True})
    assert result["ok"] and result["exit_code"] is None
    assert result["python_syntax_check"]["source_sha256"] == result["sha256"]
    assert not list(tmp_path.glob("__pycache__"))


def test_default_writes_and_unauthorized_writes_do_not_compile(tmp_path, monkeypatch):
    path = tmp_path / "source.py"
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    monkeypatch.setattr(file_write, "inspect_python_syntax",
                        lambda *args: pytest.fail("No compiler check authorized/enabled"))
    result = file_write.file_write_impl(str(path), "pass")
    assert result["ok"] and "python_syntax_check" not in result["result"]["change_evidence"]
    monkeypatch.setattr(file_write, "authorize_write_paths",
                        lambda *args, **kwargs: (False, "write_denied", None))
    denied = file_write.file_write_impl(str(path), "print(1)", python_syntax_evidence=True)
    assert not denied["ok"] and denied["error"] == "write_denied"
    assert path.read_text() == "pass"
