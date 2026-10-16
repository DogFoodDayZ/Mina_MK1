import atexit
import os
from pathlib import Path
import socket
import sqlite3
import threading

import pytest

from memory.mk1_memory import MK1Memory
from tools.isolated_conversation import (
    ConversationCore,
    DisabledTools,
    REPO_ROOT,
    create_core,
    isolated_config,
)


def test_isolated_config_rejects_repo_and_existing_unmarked_directory(tmp_path):
    with pytest.raises(ValueError, match="outside the repository"):
        isolated_config(REPO_ROOT)
    with pytest.raises(ValueError, match="without an isolated-conversation marker"):
        isolated_config(tmp_path)


def test_isolated_config_paths_and_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("MK1_OBSIDIAN_VAULT", str(tmp_path / "forbidden-vault"))
    root = tmp_path / "conversation"
    config = isolated_config(root)
    for name in ("db_path", "faiss_small", "faiss_base", "backup_dir"):
        assert Path(config.get("memory", name)).is_relative_to(root)
    assert Path(config.get("workspace", "root")).is_relative_to(root)
    assert config.get("memory", "retention_enabled") is False
    assert config.get("memory", "auto_backup_every_writes") is None
    assert config.get("memory", "obsidian_vault") == ""
    assert config.get("curiosity", "enabled") is False
    assert config.personality
    assert isolated_config(root).data == config.data
    (root / "isolated-conversation.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        isolated_config(root)


def test_real_memory_storage_reopens_only_inside_test_root(tmp_path, monkeypatch):
    root = tmp_path / "conversation"
    real_connect = sqlite3.connect
    opened = []

    def confined_connect(database, *args, **kwargs):
        path = Path(database).resolve()
        assert path.is_relative_to(root), f"Attempt to open non-test database: {path}"
        opened.append(path)
        return real_connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Isolated launcher attempted network access or background startup.")

    monkeypatch.setattr(sqlite3, "connect", confined_connect)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(MK1Memory, "_validate_embed_startup", lambda self: None)
    monkeypatch.setattr(MK1Memory, "_embed", lambda self, text, use_base=False: [1.0, 0.0, 0.0, 0.0])
    monkeypatch.setenv("MK1_OBSIDIAN_VAULT", str(tmp_path / "forbidden-vault"))

    for iteration in range(2):
        core = create_core(root)
        atexit.unregister(core.close)
        try:
            assert core.memory.obsidian_vault is None
            assert Path(core.memory.backup_dir).is_relative_to(root)
            assert core._turn_clock_thread is None
            assert core.curiosity.enabled is False
            assert core.tools.get_tool_schemas() == []
            if iteration == 0:
                assert core.memory.add_memory(
                    "Remember the conversation plan.",
                    kind="interaction",
                    tags=["short_term", "user_turn"],
                ) is not None
                assert core.memory.add_memory(
                    "We agreed to check continuity first.",
                    kind="interaction",
                    tags=["short_term", "assistant_turn"],
                ) is not None
            else:
                assert core._build_recent_conversation_messages() == [
                    {"role": "user", "content": "Remember the conversation plan."},
                    {"role": "assistant", "content": "We agreed to check continuity first."},
                ]
        finally:
            assert core.close()["ok"]
    assert opened
    assert not (tmp_path / "forbidden-vault").exists()
    assert os.environ["MK1_OBSIDIAN_VAULT"] == str(tmp_path / "forbidden-vault")
    assert list((root / "backups").glob("*"))


def test_harness_rejects_model_tool_calls_without_execution():
    core = ConversationCore.__new__(ConversationCore)
    call = '```json\n{"tool":"ps_run","args":{"script":"Get-Date"}}\n```'
    with pytest.raises(RuntimeError, match="conversation-only"):
        core._execute_inline_tool_calls(call)
    with pytest.raises(RuntimeError, match="conversation-only"):
        core._run_process_tool_loop(
            [], {"choices": [{"message": {"content": call}}]}, "hello"
        )
    with pytest.raises(RuntimeError, match="disabled"):
        DisabledTools("").run("memory_write", {"text": "must not execute"})
