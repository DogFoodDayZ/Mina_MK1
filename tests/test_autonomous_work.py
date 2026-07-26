import json
import subprocess
from pathlib import Path

from agent.core import MK1Core


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, section, key, default=None):
        return self.values.get((section, key), default)


def test_todo_items_become_autonomous_work_candidates(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "# TODO\n\n- [ ] Investigate flaky tests:\n  - Find the failing module.\n- [x] Already done.\n",
        encoding="utf-8",
    )

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
    })
    core._load_project_tracker = lambda: {"projects": {}}

    candidates = core._autonomous_work_candidates()

    assert len(candidates) == 1
    assert candidates[0]["title"] == "Investigate flaky tests"
    assert candidates[0]["details"] == ["Find the failing module."]
    assert candidates[0]["project_root"] == str(project_root)


def test_completed_audit_task_is_not_queued_again(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text("- [ ] Review logs\n", encoding="utf-8")

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
    })
    core._load_project_tracker = lambda: {"projects": {}}
    first = core._autonomous_work_candidates()[0]
    audit_path = Path(core._autonomy_audit_path())
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps({"task_key": first["key"], "ok": True}) + "\n", encoding="utf-8")

    assert core._autonomous_work_candidates() == []


def test_autonomous_model_result_accepts_fenced_json():
    core = MK1Core.__new__(MK1Core)

    result = core._parse_autonomous_model_result(
        '```json\n{"summary":"checked", "confidence":0.8, "patch":""}\n```'
    )

    assert result["summary"] == "checked"
    assert result["confidence"] == 0.8


def initialize_git_project(project_root):
    subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "mina-test@example.invalid"], cwd=project_root, check=True)
    subprocess.run(["git", "config", "user.name", "Mina Test"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "."], cwd=project_root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=project_root, check=True, capture_output=True)


def make_patch(old_line, new_line):
    return (
        "diff --git a/sample.py b/sample.py\n"
        "--- a/sample.py\n"
        "+++ b/sample.py\n"
        "@@ -1 +1 @@\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
    )


def test_clean_autonomous_patch_applies_and_validates(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    initialize_git_project(project_root)

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    watchdog_dir = tmp_path / "watchdog"
    core.config = FakeConfig({
        ("curiosity", "keep_patch_files"): False,
        ("curiosity", "watchdog_dir"): str(watchdog_dir),
    })

    result = core._apply_autonomous_patch(make_patch("value = 1", "value = 2"), str(project_root))

    assert result["ok"] is True
    assert result["applied"] is True
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    assert [item["name"] for item in result["validations"]] == ["git_diff_check", "python_ast"]
    candidate = json.loads((watchdog_dir / "candidate.json").read_text(encoding="utf-8"))
    assert candidate["candidate_id"] == result["candidate_id"]
    assert candidate["phase"] == "pending_restart"
    assert candidate["snapshot"] == result["safety_snapshot"]
    assert candidate["files"] == ["sample.py"]
    assert candidate["created_files"] == []


def test_autonomous_patch_refuses_preexisting_user_changes(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    initialize_git_project(project_root)
    source.write_text("value = 99\n", encoding="utf-8")

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({})

    result = core._apply_autonomous_patch(make_patch("value = 99", "value = 2"), str(project_root))

    assert result["ok"] is False
    assert result["error"].startswith("patch_target_has_existing_changes")
    assert source.read_text(encoding="utf-8") == "value = 99\n"


def test_autonomous_patch_refuses_recovery_infrastructure(tmp_path):
    project_root = tmp_path / "project"
    restore_dir = project_root / "restore"
    restore_dir.mkdir(parents=True)
    source = restore_dir / "code_backup.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    initialize_git_project(project_root)
    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({})
    patch = make_patch("VALUE = 1", "VALUE = 2").replace("sample.py", "restore/code_backup.py")

    result = core._apply_autonomous_patch(patch, str(project_root))

    assert result["ok"] is False
    assert result["error"].startswith("patch_path_is_recovery_infrastructure")
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"


class FakeModel:
    def chat(self, _messages, **_kwargs):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "summary": "Found a bounded improvement.",
                        "confidence": 0.9,
                        "evidence": ["sample.py contains the relevant implementation"],
                        "next_step": "Review the proposed approach.",
                        "patch": "",
                    })
                }
            }]
        }


class FakeTools:
    def __init__(self):
        self.tools = {"web_search": lambda _args: None, "code_execute": lambda _args: None}
        self.calls = []

    def run(self, _name, _args):
        self.calls.append((_name, _args))
        return {"ok": True, "result": {"results": []}}


def test_autonomous_tool_policy_only_allows_bounded_web_search():
    core = MK1Core.__new__(MK1Core)
    core.tools = FakeTools()

    blocked = core._run_autonomous_tool("code_execute", {"code": "print(1)"})
    searched = core._run_autonomous_tool(
        "web_search",
        {"query": "current Python release", "max_results": 99, "timeout": 99, "include_content": True},
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "autonomous_tool_not_allowed:code_execute"
    assert searched["ok"] is True
    assert core.tools.calls == [("web_search", {
        "query": "current Python release",
        "max_results": 3,
        "timeout": 20,
        "include_content": False,
    })]
    assert core._autonomous_tool_status()["ready"] is True


def test_autonomous_executor_writes_structured_audit(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "sample.py").write_text("def improve_worker():\n    return True\n", encoding="utf-8")

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "allow_source_edits"): True,
    })
    core.model = FakeModel()
    core.tools = FakeTools()

    result = core._run_autonomous_work_task({
        "key": "todo:improve-worker",
        "title": "Improve worker implementation",
        "details": ["Inspect the existing worker."],
        "reference": str(project_root / "sample.py"),
        "project_root": str(project_root),
    })

    assert result["ok"] is True
    assert result["summary"] == "Found a bounded improvement."
    audit_rows = [json.loads(line) for line in Path(core._autonomy_audit_path()).read_text(encoding="utf-8").splitlines()]
    assert audit_rows[0]["task_key"] == "todo:improve-worker"
    assert audit_rows[0]["evidence"] == ["sample.py contains the relevant implementation"]