"""Conversation-only acceptance harness. Never launches the normal Mina API.

Run with --root pointing to a NEW disposable directory outside the repository.
LM Studio and the configured embedding service must already be available.
Type /quit to close memory cleanly, then reuse the same root to check continuity.
Use --model, --max-tokens and --context-window to match an already loaded model
without changing normal Mina configuration or loading another model.
Tools are disabled by default. The create_core enable_tools option uses the real
tool loader and execution paths for explicitly authorized tool acceptance.
Background study remains disabled; normal Mina configuration is not changed.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent.core as core_module
from agent.core import MK1Core
from config.config_loader import MK1Config
from memory.mk1_memory import MK1Memory


class DisabledTools:
    def __init__(self, tools_dir):
        self.tools = {}

    def get_tool_schemas(self):
        return []

    def get_status(self):
        return {}

    def run(self, name, args):
        raise RuntimeError("Tool execution is disabled in the isolated conversation test.")


class ConversationCore(MK1Core):
    """Use production dialogue and memory paths without background work or actions."""

    def _start_turn_clock_heartbeat(self):
        return None

    def _maybe_run_maintenance_tick(self):
        return None

    def _validate_required_tool_routes(self):
        return None

    def _should_run_reflex(self, user_input):
        return False

    def _maybe_rescue_chat_suggested_action(self, **kwargs):
        return None

    def _run_process_tool_loop(self, messages, reply, user_intent, max_tool_loops=8, action_reports=None):
        text = self._extract_text(reply)
        if self._extract_tool_calls_from_text(text):
            raise RuntimeError("The model requested a tool; this is a conversation-only test.")
        return reply, []

    def _execute_inline_tool_calls(self, text):
        if self._extract_tool_calls_from_text(text):
            raise RuntimeError("The model requested a tool; this is a conversation-only test.")
        return text

    def _build_process_memory_context(self, user_input):
        return self.build_context(user_input), self._build_turn_working_memory(user_input), ""

    def _seed_startup_memory_facts(self):
        return None

    def _seed_authoritative_identity_memory(self):
        return None

    def _seed_runtime_capability_snapshot(self):
        return None

    def _seed_tool_usage_rules_memory(self):
        return None

    def _seed_autonomy_awareness_memory(self):
        return None

    def _refresh_file_index(self, force=False):
        return None


class ToolConversationCore(ConversationCore):
    """Keep disposable memory/background isolation but execute model-owned tools."""

    _run_process_tool_loop = MK1Core._run_process_tool_loop
    _execute_inline_tool_calls = MK1Core._execute_inline_tool_calls


def isolated_config(root: Path) -> MK1Config:
    root = root.resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        raise ValueError("Use a disposable directory outside the repository.")
    marker = root / "isolated-conversation.json"
    if root.exists() and not marker.is_file():
        raise ValueError("Refusing to reuse a directory without an isolated-conversation marker.")
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved != {"purpose": "mina-isolated-conversation", "root": str(root)}:
            raise ValueError("Isolated-conversation marker does not match this directory.")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"purpose": "mina-isolated-conversation", "root": str(root)}, indent=2),
        encoding="utf-8",
    )
    config = MK1Config(str(REPO_ROOT / "config" / "mk1_config.json"))
    personality_path = config.get("personality", "path", "")
    config.personality = (REPO_ROOT / personality_path).read_text(encoding="utf-8").strip()
    memory_dir = root / "memory"
    config.data["memory"].update({
        "db_path": str(memory_dir / "memory.db"),
        "faiss_small": str(memory_dir / "small.index"),
        "faiss_base": str(memory_dir / "base.index"),
        "backup_dir": str(root / "backups"),
        "obsidian_vault": "",
        "retention_enabled": False,
        "auto_backup_every_writes": None,
        "auto_backup_every_seconds": None,
    })
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    config.data["workspace"]["root"] = str(workspace)
    config.data["curiosity"].update({
        "enabled": False,
        "approved_roots": [str(workspace)],
        "project_roots": [str(workspace)],
        "todo_paths": [],
        "allow_source_edits": False,
    })
    config.data["tools"].update({"reflex_enabled": False, "test_mode": False})
    return config


def create_core(
    root: Path,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    context_window: int | None = None,
    enable_tools: bool = False,
) -> ConversationCore:
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError("max_tokens must be positive.")
    if context_window is not None and context_window <= 0:
        raise ValueError("context_window must be positive.")
    config = isolated_config(root)
    for key, value in (
        ("default_model", model),
        ("max_tokens", max_tokens),
        ("context_window", context_window),
    ):
        if value is not None:
            config.data["model"][key] = value

    def scoped_memory(**kwargs):
        kwargs["backup_dir"] = config.get("memory", "backup_dir")
        kwargs["obsidian_vault"] = ""
        return MK1Memory(**kwargs)

    # Construction overrides are scoped to this single-process test harness.
    with (
        patch.object(core_module, "load_config", return_value=config),
        patch.object(core_module, "MK1Memory", side_effect=scoped_memory),
        patch.object(core_module, "ToolLoader", core_module.ToolLoader if enable_tools else DisabledTools),
        patch.dict(os.environ, {"MK1_OBSIDIAN_VAULT": "", "MK1_REQUIRE_EMBED": "1"}),
    ):
        core = (ToolConversationCore if enable_tools else ConversationCore)()
    return core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="New disposable directory outside the repo.")
    parser.add_argument("--model", help="Already loaded model identifier; affects only this test.")
    parser.add_argument("--max-tokens", type=int, help="Positive reply token cap for this test.")
    parser.add_argument("--context-window", type=int, help="Loaded model context size for this test.")
    args = parser.parse_args()
    core = create_core(
        args.root,
        model=args.model,
        max_tokens=args.max_tokens,
        context_window=args.context_window,
    )
    print(f"ISOLATED TEST MEMORY: {core.memory.db_path}")
    print("Tools, background work, retention and vault export are disabled only in this test.")
    print("Enter /quit to stop; relaunch with the same --root to test restart continuity.")
    try:
        while True:
            try:
                text = input("You: ").strip()
            except EOFError:
                break
            if text == "/quit":
                break
            if not text:
                continue
            result = core.process(text)
            print(f"Mina: {result['reply']}")
    finally:
        result = core.close()
        if not result.get("ok"):
            raise RuntimeError(f"Isolated memory shutdown failed: {result.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
