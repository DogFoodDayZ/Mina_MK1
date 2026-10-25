import hashlib
from pathlib import Path

import pytest

from agent.core import MK1Core
from tools import code_repair, file_write
from tools.tool_loader import ToolLoader


@pytest.fixture
def repair(tmp_path, monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", str(tmp_path))
    path = tmp_path / "repair.py"
    path.write_text("values = [1, 2, 3]\nprint(sum(value))\n")
    calls = []

    def consult(args):
        calls.append(args)
        return {"ok": True, "result": {
            "path": str(path), "model": "test-coder", "status": "proposal",
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "proposed_content": "values = [1, 2, 3]\nprint(sum(values))\n",
        }}

    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", consult)
    return path, calls


def test_can_you_fix_it_saves_proposal_before_running_same_revision(repair):
    """Travis: 'can you fix it ?' / 'yes all three please'."""
    path, calls = repair
    result = code_repair.tool_entry({
        "path": str(path), "objective": "Fix it, save and print the sum.", "expected_lines": ["6"],
    })
    assert result["ok"]
    proof = result["result"]
    assert proof["saved"] and proof["tested"] and proof["changed"]
    assert proof["stdout"] == "6\n" and proof["exit_code"] == 0
    assert proof["sha256"] == proof["verification"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert proof["verification_level"] == "expected_output"
    assert [step["tool"] for step in proof["steps"]] == ["coding_specialist", "file_write", "code_execute"]
    assert not MK1Core._python_repair_verification_gaps([("code_repair", result)])
    assert len(calls) == 1
    assert len(proof["changes"]) == 1
    change = proof["changes"][0]
    assert "-print(sum(value))\n+print(sum(values))\n" in change["unified_diff"]
    assert change["after_sha256"] == proof["sha256"]


def test_failure_retries_once_using_actual_saved_file_diagnostics(repair, monkeypatch):
    path, calls = repair
    original = code_repair.coding_specialist.tool_entry

    def consult(args):
        result = original(args)
        if len(calls) == 1:
            result["result"]["proposed_content"] = "raise ValueError('real saved error')\n"
        return result

    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", consult)
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it.", "expected_lines": ["6"]})
    assert result["ok"] and result["result"]["attempts"] == 2 and len(calls) == 2
    assert "ValueError: real saved error" in calls[1]["diagnostics"]
    assert "6\n" == result["result"]["stdout"]
    assert len(result["result"]["changes"]) == 2
    assert result["result"]["changes"][0]["after_sha256"] == result["result"]["changes"][1]["before_sha256"]


def test_wrong_output_fails_after_two_attempts_without_weakening_assertion(repair):
    path, calls = repair
    result = code_repair.tool_entry({"path": str(path), "objective": "Print seven.", "expected_lines": ["7"]})
    assert not result["ok"] and "after_two_attempts" in result["error"]
    assert result["result"]["stdout"] == "6\n" and result["result"]["expected_stdout"] == "7\n"
    assert len(calls) == 2 and "Expected stdout" in calls[1]["diagnostics"]
    assert MK1Core._python_repair_verification_gaps([("code_repair", result)])


@pytest.mark.parametrize("status", ["no_change", "clarify"])
def test_no_change_and_questions_never_save_proposals(repair, monkeypatch, status):
    path, calls = repair
    path.write_text("print(6)\n")
    before = path.read_bytes()
    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", lambda args: {
        "ok": True, "result": {
            "status": status, "questions": ["Which output?", "Should empty inputs be accepted?"],
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "model": "test-coder",
        },
    })
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix if needed.", "expected_lines": ["6"]})
    assert result["ok"] is (status == "no_change")
    assert result["result"]["saved"] is False and path.read_bytes() == before
    if status == "clarify":
        assert not result["result"]["tested"] and len(result["result"]["questions"]) == 2
    else:
        assert result["result"]["tested"]


def test_stale_proposal_cannot_overwrite_external_edit(repair, monkeypatch):
    path, calls = repair
    original = code_repair.coding_specialist.tool_entry

    def consult(args):
        result = original(args)
        path.write_text("print('external edit')\n")
        return result

    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", consult)
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it."})
    assert not result["ok"] and result["error"] == "file_write_source_changed"
    assert path.read_text() == "print('external edit')\n"
    assert result["result"]["saved"] is False and result["result"]["tested"] is False


@pytest.mark.parametrize("extra", [
    {"expected_lines": ["6\n"]}, {"expected_lines": "6"}, {"timeout": 0},
    {"timeout": True}, {"timeout": 31}, {"append_newline": "true"}, {"diagnostics": []},
])
def test_invalid_arguments_fail_before_consultation_or_write(repair, extra):
    path, calls = repair
    before = path.read_bytes()
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it.", **extra})
    assert not result["ok"] and result["error"] and not calls
    assert path.read_bytes() == before


def test_disallowed_path_never_sends_source_to_coder(repair, monkeypatch):
    path, calls = repair
    monkeypatch.setattr(code_repair, "authorize_write_paths", lambda *args: (False, "not_authorized", []))
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it."})
    assert not result["ok"] and result["error"] == "not_authorized" and not calls


def test_timeout_does_not_retry_or_claim_running(repair, monkeypatch):
    path, calls = repair
    monkeypatch.setattr(code_repair.code_execute, "tool_entry", lambda args: {
        "ok": False, "error": "timeout", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exit_code": -1, "stdout": "", "stderr": "Execution timed out",
    })
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it."})
    assert not result["ok"] and result["error"] == "timeout" and len(calls) == 1
    assert result["result"]["saved"] and result["result"]["tested"]


def test_external_edit_during_failed_run_is_not_repaired_over(repair, monkeypatch):
    path, calls = repair

    def run(args):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text("print('external edit')\n")
        return {"ok": False, "sha256": digest, "error": "process_exited_with_code_1"}

    monkeypatch.setattr(code_repair.code_execute, "tool_entry", run)
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it."})
    assert not result["ok"] and result["error"] == "code_repair_source_changed_during_run"
    assert len(calls) == 1 and path.read_text() == "print('external edit')\n"


def test_exit_only_proof_is_not_labeled_behavior_proof(repair):
    path, _ = repair
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix the crash."})
    assert result["ok"] and result["result"]["verification_level"] == "exit_code_only"


def test_failed_consultation_never_writes_or_runs(repair, monkeypatch):
    path, _ = repair
    before = path.read_bytes()
    monkeypatch.setattr(code_repair.coding_specialist, "tool_entry", lambda args: {
        "ok": False, "error": "coding_specialist_not_loaded",
    })
    result = code_repair.tool_entry({"path": str(path), "objective": "Fix it."})
    assert not result["ok"] and path.read_bytes() == before
    assert not result["result"]["saved"] and not result["result"]["tested"]


def test_hash_precondition_rejects_stale_write_without_creating_missing_file(repair):
    path, _ = repair
    missing = path.parent / "not-created.py"
    result = file_write.tool_entry({
        "path": str(missing), "content": "print(6)\n", "expected_sha256": "stale",
    })
    assert not result["ok"] and result["error"] == "file_write_source_changed" and not missing.exists()


def test_repair_tool_is_discoverable_without_starting_models():
    assert "code_repair" in ToolLoader(str(Path(code_repair.__file__).parent)).tools
