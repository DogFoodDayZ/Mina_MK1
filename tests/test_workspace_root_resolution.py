import os

from agent.core import MK1Core


class _WorkspaceCore:
    _resolve_workspace_root = MK1Core._resolve_workspace_root
    _normalize_path = MK1Core._normalize_path


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_resolve_workspace_root_prefers_repo_for_legacy_default():
    core = _WorkspaceCore()

    resolved = core._resolve_workspace_root(r"E:\\workspace")

    assert os.path.normcase(resolved) == os.path.normcase(os.path.abspath(r"E:\\workspace"))


def test_resolve_workspace_root_uses_repo_when_empty():
    core = _WorkspaceCore()

    resolved = core._resolve_workspace_root("")

    assert os.path.normcase(resolved) == os.path.normcase(_repo_root())


def test_normalize_path_anchors_to_resolved_workspace_root():
    core = _WorkspaceCore()
    core.workspace_root = core._resolve_workspace_root(r"E:\\workspace")
    core.project_root = _repo_root()

    normalized = core._normalize_path("agent/core.py")

    assert os.path.normcase(normalized).startswith(os.path.normcase(_repo_root()))


def test_normalize_new_relative_path_defaults_to_personal_workspace_root():
    core = _WorkspaceCore()
    core.workspace_root = core._resolve_workspace_root(r"E:\\workspace")
    core.project_root = _repo_root()

    normalized = core._normalize_path("new_probe_file.txt")

    assert os.path.normcase(normalized).startswith(os.path.normcase(os.path.abspath(r"E:\\workspace")))
