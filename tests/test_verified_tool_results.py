import hashlib

import pytest

from tools import code_execute, file_write
from tools.file_read import file_read_impl


@pytest.mark.parametrize("language", ["python", "powershell"])
def test_nonzero_execution_has_explicit_error(monkeypatch, language):
    from types import SimpleNamespace
    monkeypatch.setattr(code_execute.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="subprocess.TimeoutExpired after 3 seconds",
    ))
    result = code_execute.tool_entry({"code": "example", "language": language})
    assert result["ok"] is False
    assert result["error"] == "process_exited_with_code_1"
    assert result["exit_code"] == 1
    assert "TimeoutExpired" in result["stderr"]


def test_write_receipt_proves_actual_content_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(file_write, "authorize_write_paths", lambda *args, **kwargs: (True, None, None))
    path = tmp_path / "sparkly_fireworks.py"
    result = file_write.tool_entry({"path": str(path), "content": "import random\nprint('*')\n"})
    assert result["ok"] is True
    proof = result["result"]["verification"]
    assert proof["exists"] and proof["content_matches"]
    assert proof["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert proof["first_line"] == "import random"
    assert proof["bytes"] == len(path.read_bytes())
    assert result["result"]["changed"] is True
    repeated = file_write.tool_entry({"path": str(path), "content": "import random\nprint('*')\n"})
    assert repeated["ok"] and repeated["result"]["changed"] is False


def test_syntax_repair_diff_proves_colon_change_not_invented_indentation(tmp_path, monkeypatch):
    """Gate syntax-2: Mina incorrectly claimed a missing indentation fix."""
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / "repair.py"
    old = b"values = [1, 2, 3]\nfor value in values\n    print(value)\n"
    path.write_bytes(old)
    result = file_write.tool_entry({
        "path": str(path), "content": old.decode().replace("in values\n", "in values:\n"),
    })
    evidence = result["result"]["change_evidence"]
    assert evidence["before_sha256"] == hashlib.sha256(old).hexdigest()
    assert evidence["after_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "-for value in values\n+for value in values:\n" in evidence["unified_diff"]
    assert "\n     print(value)\n" in evidence["unified_diff"]  # Context, not an indentation edit.
    assert not evidence["truncated"] and evidence["diff_available"]
    assert "not a diagnosis" in evidence["note"]


def test_change_receipts_bound_large_diffs_and_expose_unavailable_text(tmp_path, monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / "draft.txt"
    path.write_bytes(b"\xff\x00")
    result = file_write.tool_entry({"path": str(path), "content": "new\n"})
    evidence = result["result"]["change_evidence"]
    assert not evidence["diff_available"] and "not UTF-8" in evidence["reason"]
    result = file_write.tool_entry({"path": str(path), "content": "x" * 10000})
    evidence = result["result"]["change_evidence"]
    assert evidence["truncated"] and len(evidence["unified_diff"]) == 3000
    assert evidence["before_final_newline"] and not evidence["after_final_newline"]
    repeat = file_write.tool_entry({"path": str(path), "content": "x" * 10000})
    assert not repeat["result"]["changed"]
    assert repeat["result"]["change_evidence"]["unified_diff"] == ""


def test_new_file_newline_only_and_exact_limit_change_evidence():
    evidence = file_write._change_evidence(None, b"print(6)\n")
    assert evidence["before_sha256"] is None
    assert evidence["diff_available"] and "+print(6)\n" in evidence["unified_diff"]
    assert evidence["after_final_newline"]
    newline = file_write._change_evidence(b"print(6)", b"print(6)\r\n")
    assert newline["unified_diff"] == ""
    assert not newline["before_final_newline"] and newline["after_final_newline"]
    # Exactly at the bound is not truncated unless more evidence was omitted.
    header = "--- before\n+++ saved\n@@ -0,0 +1 @@\n+"
    exact = file_write._change_evidence(None, b"x" * (3000 - len(header) - 1))
    assert len(exact["unified_diff"]) == 3000 and not exact["truncated"]
    overflow = file_write._change_evidence(None, b"x" * (3001 - len(header) - 1))
    assert len(overflow["unified_diff"]) == 3000 and overflow["truncated"]


@pytest.mark.parametrize("suffix", [".py", ".csv"])
@pytest.mark.parametrize("raw,decoder,bom", [
    (b"print(6)\n", "utf-8", None),
    (b"", "utf-8", None),
    (b"\xef\xbb\xbfprint(6)\n", "utf-8-sig", "utf-8"),
    (b"\xff\xfe" + "print(6)\n".encode("utf-16-le"), "utf-16", "utf-16-le"),
    (b"\xfe\xff" + "print(6)\n".encode("utf-16-be"), "utf-16", "utf-16-be"),
    (b"\xff\xfe\x00\x00" + "print(6)\n".encode("utf-32-le"), "utf-32", "utf-32-le"),
    (b"\x00\x00\xfe\xff" + "print(6)\n".encode("utf-32-be"), "utf-32", "utf-32-be"),
    (b"# caf\xe9\n", "cp1252", None),
])
def test_read_reports_actual_bom_separately_from_decoder(tmp_path, suffix, raw, decoder, bom):
    """Gate explain-1: utf-8-sig decoder was mistaken for an actual BOM marker."""
    path = tmp_path / ("source" + suffix)
    path.write_bytes(raw)
    result = file_read_impl(str(path))
    assert result["ok"]
    payload = result["result"]
    assert payload["decoder_used"] == payload["encoding"] == decoder
    assert payload["bom"] == bom
    assert payload["is_text"]

def test_saved_python_inspection_check_and_run_prove_same_bytes(tmp_path):
    path = tmp_path / "repair.py"
    path.write_text("print('jellybean sparkle')\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    inspected = code_execute.tool_entry({"path": str(path), "mode": "inspect"})
    assert inspected["ok"] and "print('jellybean sparkle')" in inspected["stdout"]
    for mode in ("check", "run"):
        result = code_execute.tool_entry({"path": str(path), "mode": mode, "timeout": 3})
        assert result["ok"] and result["exit_code"] == 0
        assert result["sha256"] == digest
        assert result["path"] == str(path.resolve())
    assert result["stdout"] == "jellybean sparkle\n"


def test_zero_exit_with_extra_newlines_does_not_pass_expected_output(tmp_path):
    path = tmp_path / "output.py"
    path.write_text("print('jellybean sparkle\\r\\n')\n", encoding="utf-8")
    result = code_execute.tool_entry({
        "path": str(path), "mode": "run", "expected_stdout": "jellybean sparkle\n",
    })
    assert result["exit_code"] == 0
    assert not result["ok"] and result["error"] == "stdout_mismatch"
    path.write_text("print('jellybean sparkle')\n", encoding="utf-8")
    result = code_execute.tool_entry({
        "path": str(path), "mode": "run", "expected_stdout": "jellybean sparkle\n",
    })
    assert result["ok"] and result["stdout"] == "jellybean sparkle\n"


def test_line_array_output_contract_avoids_escaped_newline_ambiguity(tmp_path):
    path = tmp_path / "output.py"
    path.write_text("print('jellybean sparkle')\n", encoding="utf-8")
    args = {"path": str(path), "mode": "run", "expected_lines": ["jellybean sparkle"]}
    result = code_execute.tool_entry(args)
    assert result["ok"] and result["expected_stdout"] == "jellybean sparkle\n"
    assert not code_execute.tool_entry({**args, "append_newline": False})["ok"]
    path.write_text("print('jellybean sparkle\\n')\n", encoding="utf-8")
    assert not code_execute.tool_entry(args)["ok"]
    assert code_execute.tool_entry({**args, "expected_lines": ["jellybean sparkle", ""]})["ok"]
    path.write_text("", encoding="utf-8")
    assert code_execute.tool_entry({**args, "expected_lines": []})["ok"]


@pytest.mark.parametrize("extra", [
    {"expected_lines": "not an array"},
    {"expected_lines": ["embedded\nnewline"]},
    {"expected_lines": [1]},
    {"expected_lines": ["one"], "expected_stdout": "one\n"},
    {"expected_lines": ["one"], "append_newline": "false"},
])
def test_invalid_line_output_contract_is_explicit(extra):
    result = code_execute.tool_entry({"path": "unused.py", "mode": "run", **extra})
    assert not result["ok"] and result["error"]


def test_saved_python_error_and_missing_file_are_explicit(tmp_path):
    path = tmp_path / "repair.py"
    path.write_text("print(\n", encoding="utf-8")
    for mode in ("check", "run"):
        result = code_execute.tool_entry({"path": str(path), "mode": mode})
        assert not result["ok"] and "SyntaxError" in result["stderr"]
    missing = code_execute.tool_entry({"path": str(tmp_path / "missing.py")})
    assert not missing["ok"] and missing["error"] == "FileNotFoundError"


def test_endless_saved_program_timeout_keeps_output_and_is_not_a_pass(tmp_path):
    path = tmp_path / "endless.py"
    path.write_text("print('started', flush=True)\nwhile True: pass\n", encoding="utf-8")
    result = code_execute.tool_entry({"path": str(path), "mode": "run", "timeout": 1})
    assert not result["ok"] and result["error"] == "timeout"
    assert "started" in result["stdout"]
    assert "timed out" in result["stderr"]


def test_saved_verification_rejects_file_changed_during_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / "changing.py"
    path.write_text("print('before')\n", encoding="utf-8")

    def run(*args, **kwargs):
        path.write_text("print('after')\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="before\n", stderr="")

    monkeypatch.setattr(code_execute.subprocess, "run", run)
    result = code_execute.tool_entry({"path": str(path), "mode": "run"})
    assert not result["ok"] and result["error"] == "file_changed_during_verification"


@pytest.mark.parametrize("args", [
    {"code": "print(1)", "path": "file.py"},
    {"path": "file.py", "mode": "launch"},
    {"path": "file.py", "language": "powershell"},
    {"path": "file.py", "timeout": 0},
])
def test_saved_verification_rejects_ambiguous_or_invalid_arguments(args):
    assert not code_execute.tool_entry(args)["ok"]
