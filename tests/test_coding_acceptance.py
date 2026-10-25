import json
from types import SimpleNamespace
from typing import Any

import pytest

from tools import coding_acceptance as gate


def case(kind):
    return next(item for item in gate.cases() if item.kind == kind)


def record() -> dict[str, Any]:
    return {
        "calls": [], "responses": [{"reply": "I need the file path before I can inspect it."}],
        "error": None, "after_source": None, "observation": {},
    }


def test_transcript_classes_have_two_wording_variants_with_distinct_prompts():
    rows = gate.cases()
    assert len(rows) == 16 and len({row.id for row in rows}) == 16
    for kind in {"runtime", "syntax", "wrong_output", "healthy", "explain", "missing", "save", "chat"}:
        variants = [row for row in rows if row.kind == kind]
        assert len(variants) == 2 and variants[0].turns != variants[1].turns
    assert "can you fix it ?" in case("runtime").turns[0]
    assert "tell me what it does" in case("explain").turns[0]


@pytest.mark.parametrize("kind,name", [
    ("healthy", "code_repair"), ("healthy", "code_execute"), ("explain", "file_write"),
    ("save", "code_execute"), ("save", "coding_specialist"), ("missing", "file_read"),
    ("chat", "memory_write"),
])
def test_neighboring_intents_cannot_mutate_execute_or_touch_production_memory(tmp_path, kind, name):
    target = tmp_path / "acceptance.py"
    assert gate.tool_permission(case(kind), target, name, {"path": str(target)})


def test_repair_tools_must_target_exact_fixture_no_inline_code_or_extended_timeout(tmp_path):
    target = tmp_path / "acceptance.py"
    row = case("runtime")
    for name in gate.FILE_TOOLS:
        assert gate.tool_permission(row, target, name, {"path": str(target)}) is None
        assert gate.tool_permission(row, target, name, {"path": str(tmp_path / "other.py")})
    assert gate.tool_permission(row, target, "code_execute", {"path": str(target), "code": "print(6)"})
    assert gate.tool_permission(row, target, "code_repair", {"path": str(target), "timeout": 30})
    assert gate.tool_permission(row, target, "file_read", {"path": ""})


@pytest.mark.parametrize("kind", ["healthy", "explain"])
def test_source_inspect_is_read_only_not_an_execution_permission_violation(tmp_path, kind):
    target = tmp_path / "acceptance.py"
    args = {"path": str(target), "mode": "inspect"}
    assert gate.tool_permission(case(kind), target, "code_execute", args) is None
    row = record()
    row["after_source"] = gate.SUM_SOURCE
    row["calls"] = [{"tool": "code_execute", "args": args, "result": {"ok": True, "mode": "inspect"}}]
    scored = gate.score(case(kind), row)
    assert scored["appropriate_selection"] and scored["behavior_assertions"] and scored["evidence_verified"]
    assert not gate.tool_permission(case(kind), target, "file_read", {"path": str(target)})
    assert gate.tool_permission(case(kind), target, "code_execute", {**args, "mode": "run"})


def test_save_may_prepare_only_the_exact_disposable_parent(tmp_path):
    target = tmp_path / "acceptance.py"
    assert gate.tool_permission(case("save"), target, "dir_create", {"path": str(tmp_path)}) is None
    assert gate.tool_permission(case("save"), target, "dir_create", {"path": str(tmp_path.parent)})
    assert gate.tool_permission(case("healthy"), target, "dir_create", {"path": str(tmp_path)})
    row = record()
    row["after_source"] = gate.SAVE_SOURCE
    row["calls"] = [
        {"tool": "dir_create", "result": {"ok": True}},
        {"tool": "file_write", "result": {"ok": True, "result": {
            "verification": {"content_matches": True},
        }}},
    ]
    scored = gate.score(case("save"), row)
    assert scored["appropriate_selection"] and scored["behavior_assertions"] and scored["evidence_verified"]


def test_behavior_check_rejects_hardcoded_output(tmp_path):
    target = tmp_path / "acceptance.py"
    target.write_text("values = [1, 2, 3]\nprint(6)\n")
    observed = gate.observe_program(target, case("runtime"))
    assert observed["saved"]["passed"] is True
    assert observed["alternate"]["passed"] is False
    assert observed["alternate"]["stdout"] == "6\n"


def test_behavior_check_requires_exact_newlines_and_supports_syntax_error_evidence(tmp_path):
    target = tmp_path / "acceptance.py"
    target.write_text("values = [1, 2, 3]\nprint(sum(values), end='')\n")
    assert not gate.observe_program(target, case("runtime"))["saved"]["passed"]
    target.write_text("print(\n")
    observation = gate.observe_program(target, case("runtime"))
    assert not observation["saved"]["passed"]
    assert "SyntaxError" in observation["alternate"]["error"]


def test_correct_manual_repair_is_not_misclassified_as_workflow_failure():
    row = record()
    row["calls"] = [
        {"tool": "file_write", "result": {"ok": True}},
        {"tool": "code_execute", "result": {
            "ok": True, "mode": "run", "sha256": "actual", "exit_code": 0, "stdout": "6\n",
        }},
    ]
    row["observation"] = {
        "sha256": "actual", "saved": {"passed": True}, "alternate": {"passed": True},
    }
    scored = gate.score(case("runtime"), row)
    assert scored["appropriate_selection"] and scored["behavior_assertions"] and scored["evidence_verified"]
    assert scored["workflow_selected"] is False and scored["explanation"]["verdict"] == "pending"


def test_nested_workflow_evidence_requires_same_revision_and_exact_output():
    row = record()
    run = {"ok": True, "mode": "run", "sha256": "actual", "exit_code": 0, "stdout": "6\n"}
    row["calls"] = [{"tool": "code_repair", "result": {"ok": True, "result": {"steps": [
        {"tool": "coding_specialist", "result": {"ok": True}},
        {"tool": "code_execute", "result": run},
    ]}}}]
    row["observation"] = {
        "sha256": "actual", "saved": {"passed": True}, "alternate": {"passed": True},
    }
    assert gate.score(case("runtime"), row)["evidence_verified"]
    run["sha256"] = "stale"
    assert gate.score(case("runtime"), row)["evidence_verified"] is False


def test_permission_denial_is_a_failure_even_if_disk_behavior_is_unchanged():
    row = record()
    row["after_source"] = gate.SUM_SOURCE
    row["calls"] = [
        {"tool": "file_read", "result": {"ok": True}},
        {"tool": "code_execute", "result": {"ok": False}, "denied": True},
    ]
    scored = gate.score(case("healthy"), row)
    assert scored["permission_violation_attempted"]
    assert not scored["appropriate_selection"] and not scored["behavior_assertions"]


@pytest.mark.parametrize("reply", [
    "(Mina error: Context budget exceeded)",
    "(LM Studio error: Read timed out. (read timeout=90))",
])
def test_mina_error_reply_is_not_success_shaped_operational_result(reply):
    row = record()
    row["responses"] = [{"reply": reply, "actions": []}]
    assert gate.score(case("missing"), row)["operational"] is False


def test_explanation_review_requires_exact_evidence_and_does_not_partially_apply():
    row = record()
    row["id"] = "missing-1"
    row["scores"] = gate.score(case("missing"), row)
    report: dict[str, Any] = {"cases": [row]}
    with pytest.raises(ValueError, match="exact excerpt"):
        gate.apply_reviews(report, {"missing-1": {
            "verdict": "consistent", "reason": "Asks for path", "quote": "Invented proof",
        }})
    assert row["scores"]["explanation"]["verdict"] == "pending"
    gate.apply_reviews(report, {"missing-1": {
        "verdict": "consistent", "reason": "Requests the absent path; no tools or action claims.",
        "quote": "I need the file path",
    }})
    assert report["summary"]["selected_cases_passed"]
    assert report["summary"]["passed"] is False


def test_unreviewed_or_incorrect_explanations_never_pass_gate():
    row = record()
    row["id"] = "missing-1"
    row["scores"] = gate.score(case("missing"), row)
    assert not gate.aggregate([row])["passed"]
    row["scores"]["explanation"]["verdict"] = "incorrect"
    assert not gate.aggregate([row])["passed"]
    row["scores"]["explanation"]["verdict"] = "consistent"
    assert gate.aggregate([row])["selected_cases_passed"]
    assert not gate.aggregate([row])["passed"]  # A partial matrix cannot certify acceptance.
    assert not gate.aggregate([])["passed"]


def test_independent_fixture_cleanup_and_blocked_tools_do_not_hide_selection_failure(tmp_path, monkeypatch):
    from tools import isolated_conversation

    constructed = []
    dispatched = []
    root = tmp_path / "isolated"
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))

    def configure(path):
        (path / "workspace").mkdir(parents=True, exist_ok=True)

    def create(path, **_kwargs):
        constructed.append(path)
        core = SimpleNamespace(
            system_prompt=(gate.REPO / "config" / "personality.txt").read_text(encoding="utf-8").strip(),
            memory=SimpleNamespace(db_path=str(root / "memory" / "memory.db")),
            model=SimpleNamespace(chat=lambda **_kwargs: {}), tools=SimpleNamespace(),
            close=lambda: {"ok": True},
        )
        core.tools.run = lambda name, _args: dispatched.append(name)

        def process(_prompt):
            assert str(root) in core.memory.db_path
            core.tools.run("memory_write", {"text": "Do not touch production memory"})
            return {"reply": "I need the file path first."}

        core.process = process
        return core

    monkeypatch.setattr(isolated_conversation, "isolated_config", configure)
    monkeypatch.setattr(isolated_conversation, "create_core", create)
    result = gate.evaluate(case("missing"), "test-model", 8448, 2048)
    assert constructed == [root] and not dispatched
    assert result["fixture_removed"] and result["scores"]["permission_violation_attempted"]
    assert not result["scores"]["appropriate_selection"]


def test_cli_refuses_unloaded_model_without_construction(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "loaded_models", lambda: {})
    monkeypatch.setattr(gate.sys, "argv", [
        "coding_acceptance", "--output", str(tmp_path / "report.json"),
    ])
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2 and not (tmp_path / "report.json").exists()


def test_cli_refuses_unequal_short_context_comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "loaded_models", lambda: {"coder": 4608})
    monkeypatch.setattr(gate.sys, "argv", [
        "coding_acceptance", "--output", str(tmp_path / "report.json"),
        "--model", "coder", "--context-window", "4608",
    ])
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2 and not (tmp_path / "report.json").exists()


def test_cli_offline_review_does_not_contact_model(tmp_path, monkeypatch):
    row = record()
    row["id"] = "missing-1"
    row["scores"] = gate.score(case("missing"), row)
    report = tmp_path / "report.json"
    reviews = tmp_path / "reviews.json"
    report.write_text(json.dumps({"cases": [row]}))
    reviews.write_text(json.dumps({"missing-1": {
        "verdict": "consistent", "reason": "Asks for missing path without tools.", "quote": "file path",
    }}))
    monkeypatch.setattr(gate, "loaded_models", lambda: pytest.fail("Offline review made a network request"))
    monkeypatch.setattr(gate.sys, "argv", [
        "coding_acceptance", "--output", str(report), "--reviews", str(reviews),
    ])
    assert gate.main() == 1  # Reviewed subset passes locally but is not the full acceptance matrix.
    assert json.loads(report.read_text())["summary"]["selected_cases_passed"]


def test_complete_matrix_is_required_even_with_all_selected_cases_reviewed():
    rows = []
    for item in gate.cases():
        row = record()
        row["id"] = item.id
        row["scores"] = {
            "appropriate_selection": True, "behavior_assertions": True, "evidence_verified": None,
            "operational": True, "explanation": {"verdict": "consistent"},
        }
        rows.append(row)
    assert gate.aggregate(rows)["passed"] and gate.aggregate(rows)["matrix_complete"]
    assert not gate.aggregate(rows[:-1])["passed"]


def test_inference_timeouts_are_distinct_from_saved_program_timeouts():
    assert gate.specialist_timed_out({"ok": False, "error": "coding_specialist_ReadTimeout: local"})
    assert gate.specialist_timed_out({"ok": False, "result": {"steps": [
        {"tool": "coding_specialist", "result": {"ok": False, "error": "coding_specialist_TimeoutError: busy"}},
    ]}})
    assert not gate.specialist_timed_out({"ok": False, "error": "timeout"})  # Bounded subprocess.


@pytest.mark.parametrize("syntax_evidence", [False, True])
def test_cli_stops_submitting_cases_after_inference_timeout(tmp_path, monkeypatch, syntax_evidence):
    monkeypatch.setattr(gate, "loaded_models", lambda: {"qwen/qwen3.5-9b": 8448})
    evaluated = []

    def evaluate(item, *_args, python_syntax_evidence=False):
        assert python_syntax_evidence is syntax_evidence
        evaluated.append(item.id)
        row = record()
        row["id"] = item.id
        row["inference_timeout"] = True
        row["seconds"] = 0
        row["error"] = "ReadTimeout"
        row["scores"] = gate.score(item, row)
        return row

    monkeypatch.setattr(gate, "evaluate", evaluate)
    output = tmp_path / "report.json"
    monkeypatch.setattr(gate.sys, "argv", ["coding_acceptance", "--output", str(output)]
                        + (["--python-syntax-evidence"] if syntax_evidence else []))
    assert gate.main() == 1
    assert evaluated == ["runtime-1"]
    report = json.loads(output.read_text())
    assert "Server-side cancellation is unproven" in report["aborted"]
    assert not report["summary"]["matrix_complete"]
