import json
import os
import subprocess
import time
from datetime import datetime, timezone
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
        "# TODO\n\n- [ ] Investigate flaky tests:\n  - Run pytest tests/test_flaky.py to find the failing module.\n- [x] Already done.\n",
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
    assert candidates[0]["details"] == ["Run pytest tests/test_flaky.py to find the failing module."]
    assert candidates[0]["project_root"] == str(project_root)


def test_only_active_next_backlog_items_become_autonomous_work_candidates(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "# TODO\n\n"
        "- [ ] Historical checklist must not run\n\n"
        "## Next\n\n"
        "- [ ] Run tests/test_worker.py and verify the worker result\n\n"
        "### Issue: Historical repair record\n\n"
        "- [ ] Old done criterion must not run\n",
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

    assert [candidate["title"] for candidate in candidates] == [
        "Run tests/test_worker.py and verify the worker result"
    ]


def test_autonomous_work_intake_template_is_not_a_task(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "## Next\n\n"
        "### Autonomous Work Intake\n\n"
        "- [ ] <bounded task title>\n"
        "  - Proof target: `tests/test_example.py`\n\n"
        "### Core work\n\n"
        "- [ ] Run pytest tests/test_worker.py\n",
        encoding="utf-8",
    )

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
    })
    core._load_project_tracker = lambda: {"projects": {}}

    assert [candidate["title"] for candidate in core._autonomous_work_candidates()] == [
        "Run pytest tests/test_worker.py"
    ]


def test_suspended_task_keyword_is_not_queued(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "## Next\n\n"
        "### Ostranauts integration\n\n"
        "- [ ] Resume telemetry integration\n"
        "\n### Core work\n\n"
        "- [ ] Run tests/test_worker.py\n",
        encoding="utf-8",
    )

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
        ("curiosity", "suspended_task_keywords"): ["ostranauts"],
    })
    core._load_project_tracker = lambda: {"projects": {}}

    assert [candidate["title"] for candidate in core._autonomous_work_candidates()] == [
        "Run tests/test_worker.py"
    ]


def test_only_concrete_non_destructive_tasks_are_queued(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "## Next\n\n"
        "### Core work\n\n"
        "- [ ] Improve Mina reliability\n"
        "- [ ] Run pytest tests/test_worker.py\n"
        "- [ ] Validate the real reboot/sign-in cycle\n"
        "- [ ] Review and delete E:\\workspace\\tmp\\old.txt\n"
        "- [ ] Verify GET /status returns core_online\n",
        encoding="utf-8",
    )

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
    })
    core._load_project_tracker = lambda: {"projects": {}}

    assert [candidate["title"] for candidate in core._autonomous_work_candidates()] == [
        "Run pytest tests/test_worker.py",
        "Verify GET /status returns core_online",
    ]


def test_autonomous_decision_summary_reports_deferred_reasons_and_watchdog_state(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text(
        "## Next\n\n"
        "### Ostranauts integration\n\n"
        "- [ ] Resume telemetry\n\n"
        "### Core work\n\n"
        "- [ ] Improve reliability\n"
        "- [ ] Validate a real reboot/sign-in cycle\n"
        "- [ ] Delete E:\\workspace\\tmp\\old.txt\n"
        "- [ ] Run pytest tests/test_worker.py\n",
        encoding="utf-8",
    )
    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "project_roots"): [str(project_root)],
        ("curiosity", "todo_paths"): [str(todo_path)],
        ("curiosity", "suspended_task_keywords"): ["ostranauts"],
        ("curiosity", "allow_source_edits"): True,
    })
    core._load_project_tracker = lambda: {"projects": {}}
    core._autonomous_watchdog_ready = lambda: (False, "watchdog_state_stale")
    audit_path = Path(core._autonomy_audit_path())
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps({"error": "model_unavailable"}) + "\n", encoding="utf-8")

    summary = core._autonomous_decision_summary()

    assert summary["eligible_count"] == 1
    assert summary["eligible_preview"][0]["title"] == "Run pytest tests/test_worker.py"
    assert summary["deferred_by_reason"] == {
        "suspended_topic": 1,
        "no_proof_target": 1,
        "manual_validation_required": 1,
        "destructive_operation": 1,
    }
    assert summary["model_unavailable_count"] == 1
    assert summary["last_outcome"] == "blocked"
    assert summary["watchdog"]["source_edits_permitted"] is False


def test_user_approved_task_becomes_a_bounded_autonomy_candidate(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({("curiosity", "project_roots"): [str(project_root)]})
    core._load_project_tracker = lambda: {"projects": {}}

    result = core._add_autonomy_approved_task(
        title="Verify status route",
        proof_target="GET /status",
        allowed_action="read_only",
        done_when="The response reports core_online.",
    )

    assert result["ok"] is True
    candidates = core._autonomous_work_candidates()
    assert candidates[0]["source"] == "approved_task"
    assert candidates[0]["allowed_action"] == "read_only"
    assert core._autonomous_decision_summary()["approved_task_count"] == 1


def test_completed_audit_task_is_not_queued_again(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text("- [ ] Run pytest tests/test_log_review.py\n", encoding="utf-8")

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
    audit_path.write_text(
        json.dumps(
            {
                "task_key": first["key"],
                "ok": True,
                "verification": {"completion_verified": True},
            }
        ) + "\n",
        encoding="utf-8",
    )

    assert core._autonomous_work_candidates() == []


def test_unverified_audit_task_remains_queued(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    todo_path = project_root / "TODO.md"
    todo_path.write_text("- [ ] Run pytest tests/test_log_review.py\n", encoding="utf-8")

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

    queued = core._autonomous_work_candidates()
    assert len(queued) == 1
    assert queued[0]["key"] == first["key"]


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
    core._autonomous_watchdog_ready = lambda: (True, "")

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


def test_autonomous_patch_runs_core_boot_smoke_and_full_suite_when_project_has_agent_core(tmp_path):
    project_root = tmp_path / "project"
    agent_dir = project_root / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "__init__.py").write_text("", encoding="utf-8")
    (agent_dir / "core.py").write_text("class MK1Core: pass\n", encoding="utf-8")
    tests_dir = project_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    initialize_git_project(project_root)

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    watchdog_dir = tmp_path / "watchdog"
    core.config = FakeConfig({
        ("curiosity", "keep_patch_files"): False,
        ("curiosity", "watchdog_dir"): str(watchdog_dir),
    })
    core._autonomous_watchdog_ready = lambda: (True, "")

    patch = make_patch("class MK1Core: pass", "class MK1Core: pass  # patched").replace("sample.py", "agent/core.py")

    result = core._apply_autonomous_patch(patch, str(project_root))

    assert result["ok"] is True
    names = [item["name"] for item in result["validations"]]
    assert "core_boot_smoke" in names
    assert "pytest_full" in names
    assert all(item["ok"] for item in result["validations"])


def test_autonomous_patch_reverted_when_core_boot_smoke_fails(tmp_path):
    project_root = tmp_path / "project"
    agent_dir = project_root / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "__init__.py").write_text("", encoding="utf-8")
    (agent_dir / "core.py").write_text(
        "class MK1Core:\n    def __init__(self):\n        pass\n",
        encoding="utf-8",
    )
    initialize_git_project(project_root)

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    watchdog_dir = tmp_path / "watchdog"
    core.config = FakeConfig({
        ("curiosity", "keep_patch_files"): False,
        ("curiosity", "watchdog_dir"): str(watchdog_dir),
    })
    core._autonomous_watchdog_ready = lambda: (True, "")

    patch = (
        "diff --git a/agent/core.py b/agent/core.py\n"
        "--- a/agent/core.py\n"
        "+++ b/agent/core.py\n"
        "@@ -1,3 +1,3 @@\n"
        " class MK1Core:\n"
        "     def __init__(self):\n"
        "-        pass\n"
        "+        raise RuntimeError(\"boom\")\n"
    )

    result = core._apply_autonomous_patch(patch, str(project_root))

    assert result["ok"] is False
    assert result["error"] == "validation_failed_patch_reverted"
    boot_check = next(item for item in result["validations"] if item["name"] == "core_boot_smoke")
    assert boot_check["ok"] is False
    assert (agent_dir / "core.py").read_text(encoding="utf-8") == "class MK1Core:\n    def __init__(self):\n        pass\n"


def test_autonomous_patch_refuses_when_watchdog_is_not_healthy(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    initialize_git_project(project_root)

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({("curiosity", "watchdog_dir"): str(tmp_path / "watchdog")})

    result = core._apply_autonomous_patch(make_patch("value = 1", "value = 2"), str(project_root))

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["error"] == "watchdog_state_or_pid_missing"
    assert source.read_text(encoding="utf-8") == "value = 1\n"


def test_watchdog_readiness_requires_live_pid_and_fresh_health(tmp_path):
    watchdog_dir = tmp_path / "watchdog"
    watchdog_dir.mkdir()
    (watchdog_dir / "state.json").write_text(
        json.dumps({"healthy": True, "updated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    (watchdog_dir / "watchdog.pid").write_text(str(os.getpid()), encoding="utf-8")

    core = MK1Core.__new__(MK1Core)
    core.workspace_root = str(tmp_path / "workspace")
    core.config = FakeConfig({
        ("curiosity", "watchdog_dir"): str(watchdog_dir),
        ("curiosity", "watchdog_state_max_age_seconds"): 600,
    })

    assert core._autonomous_watchdog_ready() == (True, "")
    (watchdog_dir / "state.json").write_text(
        json.dumps({"healthy": True, "updated_at": "2000-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    assert core._autonomous_watchdog_ready() == (False, "watchdog_state_stale")

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
    def __init__(self):
        self.chat_kwargs = None

    def chat(self, _messages, **_kwargs):
        self.chat_kwargs = _kwargs
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


def test_autonomous_model_only_research_is_not_marked_complete(tmp_path):
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

    assert result["ok"] is False
    assert result["outcome"] == "research_complete"
    assert result["summary"] == "Found a bounded improvement."
    assert core.model.chat_kwargs["allow_fallback"] is False
    assert core.model.chat_kwargs["allow_model_load"] is False
    audit_rows = [json.loads(line) for line in Path(core._autonomy_audit_path()).read_text(encoding="utf-8").splitlines()]
    assert audit_rows[0]["task_key"] == "todo:improve-worker"
    assert audit_rows[0]["evidence"] == ["sample.py contains the relevant implementation"]
    assert audit_rows[0]["outcome"] == "research_complete"
    assert audit_rows[0]["verification"]["completion_verified"] is False
    assert audit_rows[0]["verification"]["patch_applied"] is False


def test_autonomous_work_task_defers_when_user_just_became_active(tmp_path):
    # Why: a background autonomous model call can take minutes; if the user resumes
    # chatting mid-call both requests hit LM Studio concurrently and contend for the
    # same KV-cache slots (observed live as context-loss/cache-restore failures).
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
    core._last_user_activity_monotonic = time.monotonic()

    result = core._run_autonomous_work_task({
        "key": "todo:improve-worker",
        "title": "Improve worker implementation",
        "details": ["Inspect the existing worker."],
        "reference": str(project_root / "sample.py"),
        "project_root": str(project_root),
    })

    assert result["ok"] is False
    assert result["deferred"] is True
    assert core.model.chat_kwargs is None