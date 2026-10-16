#!/usr/bin/env python3
"""Regression tests for natural-phrasing learning fixes.

Grounded in the 2026-09-20 transcript where three natural prompts failed:
  1. "what time is it" -> gated to a yes/no alias confirmation EVERY time
  2. "write a short hello script call it im_here.py code it to say I am here."
     -> chat model hallucinated "Wrote that simple hello script" with no file
  3. "run the im_here.py" -> raw English shipped to PowerShell:
     "run: The term 'run' is not recognized..."

These tests exercise the routing/learning units offline (no live API) so the
fixes can be verified without restarting the server. Live verification is
tracked separately in TODO.md.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.core import MK1Core  # noqa: E402


# ---------------------------------------------------------------------------
# Fix 2: "call it <name>" filename extraction (live method, not the dead module)
# ---------------------------------------------------------------------------

def _live_core():
    """Bare MK1Core instance sufficient for _detect_file_directory_intent paths
    exercised by these prompts (scaffold/dir/sqlite branches are pure-regex or
    call pure helper methods; workspace_root only used by dir_list)."""
    core = MK1Core.__new__(MK1Core)  # bypass heavy __init__
    core.workspace_root = str(REPO_ROOT)
    return core


def _detect(prompt: str):
    core = _live_core()
    return MK1Core._detect_file_directory_intent(core, prompt, prompt, prompt.lower())


@pytest.mark.parametrize("prompt,expected_name", [
    ("write a short hello script call it im_here.py code it to say I am here.", "im_here.py"),
    ("create a python script and call it hello.py that prints hi", "hello.py"),
    ("make a script name it greeter.py", "greeter.py"),
    ("write a script called notes.py", "notes.py"),
    ("save a script named out.py", "out.py"),
])
def test_call_it_filename_extraction(prompt, expected_name):
    """'call it'/'name it'/'called'/'named' phrasings must yield a file_write path."""
    result = _detect(prompt)
    assert result is not None, f"no intent detected for: {prompt}"
    tool, args = result
    assert tool == "file_write", f"expected file_write, got {tool} for: {prompt}"
    assert args.get("path", "").endswith(expected_name), \
        f"expected path ending {expected_name}, got {args.get('path')!r} for: {prompt}"


def test_write_intent_without_filename_asks_once():
    """Write intent with no filename must ask one targeted question, not drop
    to chat (which hallucinated success in the transcript)."""
    result = _detect("write a script for me")
    assert result is not None, "no intent detected"
    tool, args = result
    assert tool == "__clarify_intent__", f"expected clarify, got {tool}"
    assert "filename" in str(args.get("question", "")).lower()


# ---------------------------------------------------------------------------
# Fix 1: confirmed-alias promotion (unit-level on the confidence function)
# ---------------------------------------------------------------------------

def test_confirmed_alias_replays_immediately():
    """A confirmed learned shortcut must clear the 0.8 gate (no re-asking)."""
    from agent.core import MK1Core

    core = MK1Core.__new__(MK1Core)  # bypass heavy __init__
    core._intent_alias_cache = {
        "what time is it": {
            "tool": "ps_run",
            "args": {"script": "Get-Date"},
            "confirmed": True,
        }
    }
    # _normalize_user_intent_text and _is_action_request_like are real methods;
    # ensure they exist on the instance via the class.
    conf = MK1Core._alias_replay_confidence(core, "what time is it", "ps_run", {"script": "Get-Date"})
    assert conf >= 0.8, f"confirmed alias should clear gate, got {conf}"


def test_unconfirmed_info_alias_stays_below_gate():
    """An unconfirmed info-query alias still asks once (gate intact)."""
    from agent.core import MK1Core

    core = MK1Core.__new__(MK1Core)
    core._intent_alias_cache = {}
    conf = MK1Core._alias_replay_confidence(core, "what time is it", "ps_run", {"script": "Get-Date"})
    assert conf < 0.8, f"unconfirmed info alias should stay below gate, got {conf}"


# ---------------------------------------------------------------------------
# Fix 3: sanitizer guard rejects raw-English commands
# ---------------------------------------------------------------------------

def test_sanitizer_rejects_raw_english_command():
    """Planner shipping 'run the im_here.py'-style English must not reach ps_run."""
    from agent.core import MK1Core

    core = MK1Core.__new__(MK1Core)
    clean = MK1Core._sanitize_planned_tool_args(core, "ps_run", {"script": "run the im here script please"})
    assert clean.get("script", "") == "", \
        f"raw-English command should be blanked, got {clean.get('script')!r}"


def test_sanitizer_allows_real_script():
    """A real command with path markers must survive sanitization."""
    from agent.core import MK1Core

    core = MK1Core.__new__(MK1Core)
    clean = MK1Core._sanitize_planned_tool_args(core, "ps_run", {"script": "python im_here.py"})
    assert "im_here.py" in clean.get("script", ""), \
        f"real script path should survive, got {clean.get('script')!r}"


# ---------------------------------------------------------------------------
# Follow-up: markdown fences must never land in a written code file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_first", [
    ("```python\nprint('hi')\n```", "print('hi')"),
    ("```python\nprint('hi')\n```\nLet me know if you need changes!", "print('hi')"),
    ("```python\nprint('hi')", "print('hi')"),
    ("print('hi')", "print('hi')"),
])
def test_strip_code_fences_robust(raw, expected_first):
    """Generated file content must be fence-free regardless of model sloppiness."""
    from agent.core import MK1Core

    core = MK1Core.__new__(MK1Core)
    out = MK1Core._strip_code_fences(core, raw)
    assert out.splitlines()[0] == expected_first, f"got {out!r}"
    assert "```" not in out, f"fence survived: {out!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
