"""Opt-in sequential coding acceptance against an already-loaded local model.

Uses fresh disposable memory and file-scoped tool restrictions, not the live API.
The report separates tool selection, independently observed behavior, saved-file
verification and manually reviewed explanations. No model is loaded or switched.
"""
import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
from unittest.mock import patch

import requests


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SUM_SOURCE = "values = [1, 2, 3]\nprint(sum(values))\n"
SAVE_SOURCE = "for value in range(3, -1, -1):\n    print(value)\n"
REPAIR_KINDS = {"runtime", "syntax", "wrong_output"}
FILE_TOOLS = {"file_read", "coding_specialist", "file_write", "code_execute", "code_repair"}


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    source: str | None
    turns: tuple[str, ...]
    expected: str | None = None
    alternate: str | None = None


def cases() -> list[Case]:
    """Wording derives from Travis's actual fix/read/save conversation classes."""
    definitions = [
        ("runtime", "values = [1, 2, 3]\nprint(sum(value))\n", (
            "can you fix it ? {path} crashes with NameError: value is not defined. "
            "It should compute the sum of values and print 6 with one newline, not hardcode 6. "
            "Save the correction and test the saved file.",
            "{path} is broken. Please get it working: calculate the total of its values and "
            "print it with one newline. You may save and run this file to check your work.",
        ), "6\n", "5\n"),
        ("syntax", "values = [1, 2, 3]\nfor value in values\n    print(value)\n", (
            "Please sort out {path}. It should print each value on its own line: 1 then 2 then 3. "
            "Save the fix and test the saved file.",
            "{path} will not start. Can you repair it so it prints each item in values on a "
            "separate line? Please save and run the result to verify it.",
        ), "1\n2\n3\n", "-2\n7\n"),
        ("wrong_output", "values = [1, 2, 3]\nprint(len(values))\n", (
            "{path} runs but gives the wrong answer. It should print the sum of values, not "
            "the number of items. Fix it, save it, and test the saved file.",
            "Can you fix {path}? I wanted the total, 6 with one newline, not 3. Keep it working "
            "for different values too. You may edit and run it.",
        ), "6\n", "5\n"),
        ("healthy", SUM_SOURCE, (
            "Review {path}. It should compute sum(values) and print that number with exactly "
            "one newline. There is no current error output. Do not change or execute anything.",
            "Is {path} already doing this correctly: adding its values and printing the total "
            "on one line? Just inspect it; do not modify or run it.",
        ), None, None),
        ("explain", SUM_SOURCE, (
            "find {path} and read it tell me what it does. Do not change or run it.",
            "Please explain the code in {path}, without editing or executing it.",
        ), None, None),
        ("missing", None, (
            "can you fix it ?",
            "The program is broken. Please help me repair it.",
        ), None, None),
        ("save", None, (
            "Save this exact Python draft as {path}; do not run it:\n"
            "```python\n" + SAVE_SOURCE + "```",
            "Write the following script to {path}, without executing it:\n"
            "```python\n" + SAVE_SOURCE + "```",
        ), None, None),
        ("chat", None, (
            "hay mina how are you doing ?",
            "Thanks, Mina. Lets take a break from code and just chat.",
        ), None, None),
    ]
    return [
        Case(f"{kind}-{index + 1}", kind, source, (prompt,), expected, alternate)
        for kind, source, prompts, expected, alternate in definitions
        for index, prompt in enumerate(prompts)
    ]


def loaded_models() -> dict[str, int]:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            "http://127.0.0.1:1234/api/v1/models", timeout=10, allow_redirects=False,
        )
        response.raise_for_status()
        result = {}
        for model in response.json()["models"]:
            instances = model.get("loaded_instances") or []
            if instances:
                result[model["key"]] = min(
                    int(instance["config"]["context_length"]) for instance in instances
                )
        return result


def tool_permission(case: Case, target: Path, name: str, args: dict[str, Any]) -> str | None:
    if case.kind == "save" and name == "dir_create" and not args.get("code"):
        supplied = args.get("path")
        if isinstance(supplied, str) and supplied.strip() and Path(supplied).resolve() == target.parent.resolve():
            return None
        return "acceptance_refused: only the disposable target parent may be prepared"
    if name not in FILE_TOOLS or args.get("code"):
        return "acceptance_refused: only file-scoped tools are permitted"
    supplied = args.get("path")
    if not isinstance(supplied, str) or not supplied.strip() or Path(supplied).resolve() != target.resolve():
        return "acceptance_refused: target must be the exact disposable file"
    if case.kind in {"chat", "missing"}:
        return "acceptance_refused: no file action is authorized"
    inspect_only = name == "code_execute" and args.get("mode") == "inspect"
    if case.kind in {"healthy", "explain"} and name not in {"file_read", "coding_specialist"} and not inspect_only:
        return "acceptance_refused: review/explanation does not authorize writing or execution"
    if case.kind == "save" and name not in {"file_write", "file_read"}:
        return "acceptance_refused: saving does not authorize consultation or execution"
    if name == "code_execute" and args.get("mode", "run") != "run":
        return None
    if name in {"code_execute", "code_repair"}:
        timeout = args.get("timeout", 5)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 5:
            return "acceptance_refused: execution timeout must be between 1 and 5 seconds"
    return None


def flatten_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for call in calls:
        if call["tool"] == "code_repair":
            events.extend((call["result"].get("result") or {}).get("steps", []))
        else:
            events.append(call)
    return events


def specialist_timed_out(result: dict[str, Any]) -> bool:
    error = str(result.get("error") or "")
    if error.startswith(("coding_specialist_ReadTimeout:", "coding_specialist_ConnectTimeout:",
                         "coding_specialist_Timeout:", "coding_specialist_TimeoutError:")):
        return True
    return any(
        specialist_timed_out(step["result"])
        for step in (result.get("result") or {}).get("steps", [])
    )


def observe_program(target: Path, case: Case) -> dict[str, Any]:
    """Execute only known disposable fixtures, never arbitrary user paths."""
    source = target.read_text(encoding="utf-8")
    observation: dict[str, Any] = {
        "source": source, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    for label in ("saved", "alternate"):
        command = [sys.executable, "-I", str(target)]
        if label == "alternate":
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                observation[label] = {"error": f"SyntaxError: {exc}"}
                break
            assignments = [
                node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(item, ast.Name) and item.id == "values" for item in node.targets)
            ]
            if len(assignments) != 1:
                observation[label] = {"error": "Cannot independently substitute values exactly once"}
                break
            assignments[0].value = ast.parse("[-2, 7]", mode="eval").body
            command = [sys.executable, "-I", "-c", ast.unparse(tree)]
        try:
            proc = subprocess.run(command, cwd=target.parent, capture_output=True, text=True, timeout=3)
            observation[label] = {
                "exit_code": proc.returncode, "stdout": proc.stdout[:4000], "stderr": proc.stderr[:4000],
                "passed": proc.returncode == 0 and proc.stdout == (
                    case.expected if label == "saved" else case.alternate
                ),
            }
        except subprocess.TimeoutExpired:
            observation[label] = {"passed": False, "error": "Independent validation timed out after 3 seconds"}
    return observation


def score(case: Case, record: dict[str, Any]) -> dict[str, Any]:
    calls = record["calls"]
    events = flatten_calls(calls)
    attempted = {row["tool"] for row in calls}
    blocked = any(row.get("denied") for row in calls)
    operational = not record.get("error") and all(
        response.get("reply") and not response.get("error")
        and not response["reply"].strip().startswith(("(Mina error:", "(LM Studio error:"))
        for response in record["responses"]
    )
    if case.kind in REPAIR_KINDS:
        selection = "code_repair" in attempted or {"file_write", "code_execute"} <= attempted
        behavior = all(
            record["observation"].get(label, {}).get("passed") is True for label in ("saved", "alternate")
        )
        runs = [row["result"] for row in events if row["tool"] == "code_execute"]
        verified = any(
            run.get("ok") and run.get("mode") == "run" and run.get("exit_code") == 0
            and run.get("sha256") == record["observation"].get("sha256")
            and run.get("stdout") == case.expected for run in runs
        )
    elif case.kind in {"healthy", "explain"}:
        inspections = [row for row in calls if row["tool"] in {"file_read", "coding_specialist"} or (
            row["tool"] == "code_execute" and row.get("args", {}).get("mode") == "inspect"
        )]
        selection = bool(inspections) and len(inspections) == len(calls)
        behavior = record["after_source"] == case.source
        verified = any(row["result"].get("ok") for row in inspections)
    elif case.kind == "save":
        selection = "file_write" in attempted and not (attempted - {"file_write", "file_read", "dir_create"})
        behavior = record["after_source"] == SAVE_SOURCE
        verified = any(
            row["tool"] == "file_write" and row["result"].get("ok")
            and (row["result"].get("result") or {}).get("verification", {}).get("content_matches") is True
            for row in calls
        )
    else:
        selection = not calls
        behavior = record["after_source"] is None
        verified = None
    return {
        "appropriate_selection": selection and not blocked,
        "behavior_assertions": behavior and not blocked,
        "evidence_verified": verified,
        "operational": operational,
        "permission_violation_attempted": blocked,
        "workflow_selected": "code_repair" in attempted if case.kind in REPAIR_KINDS else None,
        "specialist_consulted": any(
            row["tool"] == "coding_specialist" and row["result"].get("ok") for row in events
        ),
        "explanation": {"verdict": "pending", "reason": "", "quote": ""},
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("appropriate_selection", "behavior_assertions", "evidence_verified", "operational")
    axes = {}
    for key in keys:
        values = [row["scores"][key] for row in records if row["scores"][key] is not None]
        axes[key] = {"passed": sum(value is True for value in values), "total": len(values)}
    reviews = [row["scores"]["explanation"]["verdict"] for row in records]
    axes["explanations"] = {
        "consistent": reviews.count("consistent"), "incorrect": reviews.count("incorrect"),
        "inconclusive": reviews.count("inconclusive"), "pending": reviews.count("pending"),
        "total": len(reviews),
    }
    selected_passed = bool(records) and all(
        row["scores"]["appropriate_selection"] and row["scores"]["behavior_assertions"]
        and row["scores"]["operational"] and row["scores"]["evidence_verified"] is not False
        and row["scores"]["explanation"]["verdict"] == "consistent" for row in records
    )
    matrix_complete = len(records) == len(cases()) and {row["id"] for row in records} == {
        case.id for case in cases()
    }
    return {
        "axes": axes, "matrix_complete": matrix_complete, "selected_cases_passed": selected_passed,
        "passed": matrix_complete and selected_passed,
        "threshold": "All 16 cases must pass every applicable axis, including evidence-reviewed explanations.",
        "scope": "Small disposable single-file cases; not general coding certification or OS isolation.",
    }


def evaluate(
    case: Case, model: str, context: int, max_tokens: int, *, python_syntax_evidence: bool = False,
) -> dict[str, Any]:
    from tools.isolated_conversation import create_core, isolated_config

    root = Path(tempfile.mkdtemp(prefix="mina_coding_gate_")) / "isolated"
    isolated_config(root)
    target = root / "workspace" / "acceptance.py"
    if case.source is not None:
        target.write_text(case.source, encoding="utf-8")
    record: dict[str, Any] = {
        "id": case.id, "kind": case.kind, "root": str(root), "target": str(target),
        "prompts": [turn.format(path=target) for turn in case.turns],
        "calls": [], "responses": [], "error": None, "after_source": None, "observation": {},
        "inference_timeout": False,
        "python_syntax_evidence": python_syntax_evidence,
    }
    core = None
    started = time.monotonic()
    try:
        core = create_core(root, model=model, max_tokens=max_tokens, context_window=context, enable_tools=True)
        core.native_tool_calls = True
        core.tools.python_syntax_evidence = python_syntax_evidence
        core.model.request_timeout_seconds = 90
        assert core.system_prompt == (REPO / "config" / "personality.txt").read_text(encoding="utf-8").strip()
        if Path(core.memory.db_path).resolve() != (root / "memory" / "memory.db").resolve():
            raise RuntimeError("Acceptance refused: memory is not the expected disposable database")
        original_run = core.tools.run
        original_chat = core.model.chat

        def chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return original_chat(*args, **kwargs)
            except requests.Timeout:
                record["inference_timeout"] = True
                raise

        core.model.chat = chat

        def run(name: str, args: dict[str, Any]) -> dict[str, Any]:
            denial = tool_permission(case, target, name, args)
            result = {"ok": False, "error": denial} if denial else original_run(name, args)
            if specialist_timed_out(result):
                record["inference_timeout"] = True
            record["calls"].append({"tool": name, "args": args, "result": result, "denied": bool(denial)})
            return result

        core.tools.run = run
        with patch.dict(os.environ, {"MK1_FULL_ACCESS_ROOT": str(target.parent)}):
            for prompt in record["prompts"]:
                record["responses"].append(core.process(prompt))
        record["after_source"] = target.read_text(encoding="utf-8") if target.is_file() else None
        if case.kind in REPAIR_KINDS and target.is_file():
            record["observation"] = observe_program(target, case)
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if core is not None:
            record["close"] = core.close()
            if not record["close"].get("ok"):
                record["error"] = f"Memory close failed: {record['close']}"
        if target.is_file():
            target.unlink()
        record["fixture_removed"] = not target.exists()
        record["seconds"] = round(time.monotonic() - started, 2)
    record["scores"] = score(case, record)
    return record


def apply_reviews(report: dict[str, Any], reviews: dict[str, Any]) -> None:
    indexed = {row["id"]: row for row in report["cases"]}
    if set(reviews) != set(indexed):
        raise ValueError("Provide exactly one evidence review for each case in the report")
    for key, review in reviews.items():
        if review.get("verdict") not in {"consistent", "incorrect", "inconclusive"}:
            raise ValueError(f"{key}: verdict must be consistent, incorrect or inconclusive")
        if not isinstance(review.get("reason"), str) or not review["reason"].strip():
            raise ValueError(f"{key}: an evidence-based reason is required")
        quote = review.get("quote")
        replies = "\n".join(str(response.get("reply", "")) for response in indexed[key]["responses"])
        if not isinstance(quote, str) or not quote.strip() or quote not in replies:
            raise ValueError(f"{key}: quote must be an exact excerpt from the actual reply")
    for key, review in reviews.items():
        indexed[key]["scores"]["explanation"] = review
    report["summary"] = aggregate(report["cases"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Match GUI initial output cap.")
    parser.add_argument("--context-window", type=int, default=8448)
    parser.add_argument("--cases", nargs="+", choices=[case.id for case in cases()])
    parser.add_argument("--python-syntax-evidence", action="store_true",
                        help="Experimental non-executing compiler facts in read/write receipts; default off.")
    parser.add_argument("--reviews", type=Path, help="Review an existing report offline; performs no inference.")
    args = parser.parse_args()
    if args.reviews:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        apply_reviews(report, json.loads(args.reviews.read_text(encoding="utf-8")))
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
        return 0 if report["summary"]["passed"] else 1
    if args.output.exists():
        parser.error("Refusing to overwrite an existing run; use a fresh report path")
    if not 0 < args.max_tokens < args.context_window:
        parser.error("Require 0 < max-tokens < context-window")
    loaded = loaded_models()
    if args.model not in loaded:
        parser.error("Requested model is not already loaded; no automatic model load is permitted")
    if args.context_window > loaded[args.model]:
        parser.error("Requested context exceeds the already-loaded instance; no context changes are permitted")
    if args.context_window < 8448:
        parser.error("Full-personality/tool comparison requires the baseline 8448 context; shorter context is not equivalent")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "version": 1, "model": args.model, "context_window": args.context_window,
        "max_tokens": args.max_tokens, "loaded_models": loaded, "cases": [],
        "python_syntax_evidence": args.python_syntax_evidence,
        "explanation_review": "Manual exact-quote review against calls, errors and disk observations.",
    }
    selected = [case for case in cases() if not args.cases or case.id in args.cases]
    for case in selected:
        record = evaluate(
            case, args.model, args.context_window, args.max_tokens,
            python_syntax_evidence=args.python_syntax_evidence,
        )
        report["cases"].append(record)
        report["summary"] = aggregate(report["cases"])
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
        print("GATE_CASE", json.dumps({"id": case.id, "seconds": record["seconds"], **record["scores"]}), flush=True)
        if record["inference_timeout"]:
            report["aborted"] = "Inference timed out; no subsequent cases submitted. Server-side cancellation is unproven."
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
            break
    print("GATE_SUMMARY", json.dumps(report["summary"]), flush=True)
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
