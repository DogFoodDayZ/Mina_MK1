"""Mina acceptance check on the real GUI chat path (MK1Core.process_stream).

Uses production MK1Core with only these differences:
  - memory database and workspace live in a disposable folder outside the repo
  - background timers (turn heartbeat, maintenance tick) are off
  - optional model override
Everything else is production: memory context, seeding, startup guard, tools.
Requires LM Studio (model already loaded) and the embedding server on 8084.

Each check prints the full transcript so a human can judge the replies.
Run:  .venv\\Scripts\\python.exe tools\\mina_acceptance.py --model qwen/qwen3.8-27b
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent.core as core_module  # noqa: E402
from agent.core import MK1Core  # noqa: E402
from memory.mk1_memory import MK1Memory  # noqa: E402
from tools.isolated_conversation import isolated_config  # noqa: E402


class AcceptanceCore(MK1Core):
    def _start_turn_clock_heartbeat(self):
        return None

    def _maybe_run_maintenance_tick(self):
        return None


def build_core(root: Path, model: str | None, native_tools: bool | None):
    config = isolated_config(root)
    # Tool write policy reads this for every call, so it must stay set for the run.
    os.environ["MK1_FULL_ACCESS_ROOT"] = str(Path(config.get("workspace", "root")))
    os.environ["MK1_FULL_ACCESS_ROOTS"] = ""
    if model:
        config.data["model"]["default_model"] = model
    if native_tools is not None:
        config.data["model"]["native_tool_calls"] = native_tools

    def scoped_memory(**kwargs):
        kwargs["backup_dir"] = config.get("memory", "backup_dir")
        kwargs["obsidian_vault"] = ""
        return MK1Memory(**kwargs)

    with (
        patch.object(core_module, "load_config", return_value=config),
        patch.object(core_module, "MK1Memory", side_effect=scoped_memory),
        patch.dict(os.environ, {"MK1_OBSIDIAN_VAULT": "", "MK1_REQUIRE_EMBED": "1"}),
    ):
        return AcceptanceCore()


def close_core(core):
    result = core.close()
    if isinstance(result, dict) and result.get("ok") is False:
        print(f"  SHUTDOWN REPORTED PROBLEM: {result}", flush=True)


def say(core, text: str, log: list) -> str:
    started = time.time()
    chunks = []
    actions = []
    for piece in core.process_stream(text, request_context={"include_action_reports": True}):
        if isinstance(piece, dict):
            actions.extend(piece.get("actions") or [])
        else:
            chunks.append(str(piece))
    reply = "".join(chunks).strip()
    tools_used = [a.get("tool") or a.get("name") for a in actions if isinstance(a, dict)]
    log.append({"user": text, "mina": reply, "tools": tools_used, "seconds": round(time.time() - started, 1)})
    print(f"\n  YOU : {text}\n  MINA: {reply}\n  tools={tools_used}  ({log[-1]['seconds']}s)", flush=True)
    return reply


def has(text: str, *words: str) -> bool:
    low = text.lower()
    return all(w.lower() in low for w in words)


def check_learn_and_recall(args, base: Path):
    """She is told a fact once, then asked for it after unrelated turns and after a restart."""
    root = base / "learn_recall"
    log = []
    core = build_core(root, args.model, args.native)
    say(core, "Hey Mina, it's Travis. My dog's name is Biscuit, he's a three-legged beagle.", log)
    say(core, "What do you think makes a good weekend project?", log)
    say(core, "Do you like rainy days?", log)
    same_session = say(core, "Quick one, what's my dog's name?", log)
    close_core(core)
    core = build_core(root, args.model, args.native)
    after_restart = say(core, "Do you remember what my dog's name is?", log)
    close_core(core)
    results = {
        "recall in same session": has(same_session, "biscuit"),
        "recall after restart": has(after_restart, "biscuit"),
    }
    return results, log


def check_correction(args, base: Path):
    """A correction must replace the old fact, including after a restart."""
    root = base / "correction"
    log = []
    core = build_core(root, args.model, args.native)
    say(core, "My truck is a 1998 Ford Ranger.", log)
    say(core, "Sorry, I got that wrong. It's actually a 2001 Ford Ranger, not 1998.", log)
    close_core(core)
    core = build_core(root, args.model, args.native)
    reply = say(core, "What year is my truck?", log)
    close_core(core)
    return {"uses corrected fact after restart": has(reply, "2001")}, log


def check_continuity(args, base: Path):
    """A follow-up that only makes sense with the previous turn."""
    root = base / "continuity"
    log = []
    core = build_core(root, args.model, args.native)
    say(core, "I'm thinking about planting tomatoes and peppers along the south fence this spring.", log)
    reply = say(core, "Which of those two needs more water?", log)
    close_core(core)
    return {"follow-up understood": has(reply, "tomato") or has(reply, "pepper")}, log


def check_tool_use(args, base: Path):
    """Natural phrasing must produce a real file, then a real read of it."""
    root = base / "tools"
    log = []
    core = build_core(root, args.model, args.native)
    workspace = Path(core.config.get("workspace", "root"))
    target = workspace / "shopping.txt"
    say(core, f"Can you make me a shopping list at {target} with eggs, flour and coffee on it?", log)
    exists = target.is_file()
    content = target.read_text(encoding="utf-8", errors="replace") if exists else ""
    wrote = exists and all(w in content.lower() for w in ("eggs", "flour", "coffee"))
    if exists:
        target.write_text("eggs\nflour\ncoffee\nsecret-marker-7731\n", encoding="utf-8")
    reply = say(core, f"What's on the list in {target} right now?", log)
    close_core(core)
    return {
        "file really created with the items": wrote,
        "really read the file (sees marker added outside chat)": exists and "7731" in reply,
    }, log


def check_long_gap(args, base: Path):
    """The fact must come from learned memory, not replay: push it past the 12-message replay window."""
    root = base / "long_gap"
    log = []
    core = build_core(root, args.model, args.native)
    say(core, "By the way, my sister Dana lives in Tucson and she's a welder.", log)
    for filler in (
        "Give me one word that describes autumn.",
        "What's a fun fact about octopuses? Keep it short.",
        "Name a good board game for two people.",
        "Short answer: tea or coffee?",
        "What's your favourite colour today?",
        "Tell me a one-line joke.",
        "What's a good name for a cat?",
        "One sentence: why do people like camping?",
    ):
        say(core, filler, log)
    close_core(core)
    core = build_core(root, args.model, args.native)
    reply = say(core, "Where does my sister live, and what does she do?", log)
    close_core(core)
    return {"recalls fact from memory beyond replay window": has(reply, "tucson") and has(reply, "weld")}, log


def check_correction_long_gap(args, base: Path):
    """A correction must win after both statements have left the replay window."""
    root = base / "correction_long_gap"
    log = []
    core = build_core(root, args.model, args.native)
    say(core, "My truck is a 1998 Ford Ranger.", log)
    say(core, "Sorry, I got that wrong. It's actually a 2001 Ford Ranger, not 1998.", log)
    for filler in (
        "Give me one word that describes winter.",
        "Name a fruit that's in season in summer.",
        "Short answer: mountains or beach?",
        "What's a good name for a goldfish?",
        "Tell me a one-line riddle.",
        "One word for how you feel today.",
        "Name a classic movie in one line.",
    ):
        say(core, filler, log)
    close_core(core)
    core = build_core(root, args.model, args.native)
    reply = say(core, "What year is my truck?", log)
    close_core(core)
    return {"corrected fact wins beyond replay window": has(reply, "2001")}, log


CHECKS = {
    "learn_recall": check_learn_and_recall,
    "long_gap": check_long_gap,
    "correction_long_gap": check_correction_long_gap,
    "correction": check_correction,
    "continuity": check_continuity,
    "tools": check_tool_use,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--native", choices=["on", "off", "config"], default="config",
                        help="Native tool calls: on, off, or whatever mk1_config.json says.")
    parser.add_argument("--only", nargs="*", choices=sorted(CHECKS))
    parser.add_argument("--out", type=Path, default=Path(r"E:\Mina_acceptance_runs"))
    args = parser.parse_args()
    args.native = {"on": True, "off": False, "config": None}[args.native]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = args.out / stamp
    base.mkdir(parents=True, exist_ok=False)
    report = {"model": args.model, "native_tools": args.native, "checks": {}}
    passed = failed = 0
    for name in args.only or list(CHECKS):
        print(f"\n=== {name} ===", flush=True)
        try:
            results, log = CHECKS[name](args, base)
        except Exception as exc:
            results, log = {"ran without crashing": False}, [{"error": repr(exc)}]
            print(f"  CRASH: {exc!r}", flush=True)
        for label, ok in results.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}", flush=True)
            passed += bool(ok)
            failed += not ok
        report["checks"][name] = {"results": results, "transcript": log}
    report["summary"] = {"passed": passed, "failed": failed}
    (base / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSUMMARY: {passed} passed, {failed} failed. Report: {base / 'report.json'}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
