import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
import requests

from agent.inference_gate import inference_slot, serial_completion, serial_stream
from tools import coding_specialist as specialist
from tools.tool_loader import ToolLoader


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"coding_specialist": {
        "enabled": True, "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "model": "qwen2.5-coder-7b-instruct",
    }}))
    monkeypatch.setattr(specialist, "CONFIG_PATH", config)
    path = tmp_path / "repair.py"
    path.write_text("values = [1, 2, 3]\nprint(sum(value))\n")
    state = SimpleNamespace(
        loaded=True, posts=[], gets=[], content="values = [1, 2, 3]\nprint(sum(values))",
        finish="stop", exception=None, mutate=False, tools=None, assessment=None,
    )

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            if state.exception:
                raise state.exception

        def json(self):
            return self.payload

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert self.trust_env is False
            assert kwargs["allow_redirects"] is False
            state.gets.append(url)
            return Response({"models": [{
                "key": "qwen2.5-coder-7b-instruct",
                "loaded_instances": [{"id": "coder"}] if state.loaded else [],
            }]})

        def post(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            state.posts.append((url, kwargs))
            if state.mutate:
                path.write_text("print('external edit')\n")
            return Response({"choices": [{
                "finish_reason": state.finish,
                "message": {"content": json.dumps(state.assessment if state.assessment is not None else {
                    "status": "proposal", "summary": "The source uses undefined value instead of values.",
                    "proposed_content": state.content,
                }), "tool_calls": state.tools},
            }]})

    monkeypatch.setattr(specialist.requests, "Session", Session)
    return path, config, state


def test_fix_it_uses_fresh_file_and_returns_proposal_without_writing_or_execution(setup):
    """Travis: 'can you fix it ?' / 'yes all three please'."""
    path, _, state = setup
    before = path.read_bytes()
    result = specialist.tool_entry({
        "path": str(path), "objective": "Fix it and print the sum.",
        "diagnostics": "NameError: name 'value' is not defined",
    })
    assert result["ok"]
    payload = result["result"]
    assert payload["proposed_content"].endswith("print(sum(values))")
    assert payload["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert payload["saved"] is False and payload["tested"] is False
    assert payload["status"] == "proposal" and payload["assessment_verified"] is False
    assert path.read_bytes() == before
    sent = state.posts[0][1]["json"]
    assert sent["model"] == "qwen2.5-coder-7b-instruct"
    assert sent["max_tokens"] == 1024 and sent["temperature"] == 0
    assert "tools" not in sent
    data = json.loads(sent["messages"][1]["content"])
    assert data["source"] == before.decode()
    assert "NameError" in data["diagnostics"]


@pytest.mark.parametrize("content,error", [
    ("", "empty_proposal"),
    ("print(", "invalid_python"),
])
def test_invalid_proposal_is_explicit(setup, content, error):
    path, _, state = setup
    state.content = content
    result = specialist.tool_entry({"path": str(path), "objective": "fix it"})
    assert not result["ok"] and error in result["error"]


def test_unloaded_model_is_not_loaded_or_posted_to(setup):
    path, _, state = setup
    state.loaded = False
    result = specialist.tool_entry({"path": str(path), "objective": "fix it"})
    assert not result["ok"] and "not_loaded" in result["error"]
    assert not state.posts


@pytest.mark.parametrize("endpoint", [
    "https://remote.example/v1/chat/completions",
    "http://127.0.0.1:1234/wrong",
    "http://user:pass@localhost:1234/v1/chat/completions",
])
def test_no_source_is_sent_to_nonlocal_or_invalid_endpoint(setup, endpoint):
    path, config, state = setup
    data = json.loads(config.read_text())
    data["coding_specialist"]["endpoint"] = endpoint
    config.write_text(json.dumps(data))
    result = specialist.tool_entry({"path": str(path), "objective": "fix it"})
    assert not result["ok"] and "local_lmstudio" in result["error"]
    assert not state.posts and not state.gets


def test_truncated_response_and_source_race_fail(setup):
    path, _, state = setup
    state.finish = "length"
    assert "incomplete_response" in specialist.tool_entry({"path": str(path), "objective": "fix"})["error"]
    state.finish = "stop"
    state.mutate = True
    assert "source_changed" in specialist.tool_entry({"path": str(path), "objective": "fix"})["error"]
    assert path.read_text() == "print('external edit')\n"


def test_clarification_is_not_saved_code(setup):
    path, _, state = setup
    state.assessment = {
        "status": "clarify", "summary": "The requested output format is unspecified.",
        "questions": ["Which output format?", "Should empty inputs be allowed?"],
    }
    result = specialist.tool_entry({"path": str(path), "objective": "fix it"})
    assert result["ok"] and "Which output format?" in result["result"]["questions"]
    assert "proposed_content" not in result["result"]


def test_review_healthy_source_can_return_no_change_without_a_proposal(setup):
    """Travis: requirement is sum(values), exactly one newline; review only."""
    path, _, state = setup
    path.write_text("values = [1, 2, 3]\nprint(sum(values))\n")
    before = path.read_bytes()
    state.assessment = {
        "status": "no_change", "summary": "The source already computes the sum and prints one newline.",
    }
    result = specialist.tool_entry({"path": str(path), "objective": "Review only; print the sum with one newline."})
    assert result["ok"] and result["result"]["status"] == "no_change"
    assert result["result"]["changed"] is False
    assert result["result"]["assessment_verified"] is False
    assert "proposed_content" not in result["result"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("assessment,error", [
    ([], "invalid_assessment"),
    ({"status": "fixed", "summary": "Done"}, "invalid_assessment"),
    ({"status": "no_change"}, "invalid_assessment"),
    ({"status": "clarify", "summary": "Missing requirements", "questions": []}, "empty_clarification"),
    ({"status": "clarify", "summary": "Missing requirements", "questions": [""]}, "empty_clarification"),
    ({"status": "no_change", "summary": "Fine", "proposed_content": "print(6)"}, "conflicting_assessment"),
    ({"status": "proposal", "summary": "Fix", "proposed_content": "print(6)", "questions": []},
     "conflicting_assessment"),
])
def test_invalid_or_contradictory_assessments_are_explicit_failures(setup, assessment, error):
    path, _, state = setup
    state.assessment = assessment
    result = specialist.tool_entry({"path": str(path), "objective": "Review this."})
    assert not result["ok"] and error in result["error"]


def test_unchanged_proposal_is_not_reported_as_a_repair(setup):
    path, _, state = setup
    state.content = path.read_text()
    result = specialist.tool_entry({"path": str(path), "objective": "can you fix it ?"})
    assert not result["ok"] and "unchanged_proposal" in result["error"]


def test_oversize_and_missing_arguments_do_not_call_model(setup):
    path, _, state = setup
    for args in ({}, {"path": str(path)}, {"path": str(path), "objective": ""}):
        assert not specialist.tool_entry(args)["ok"]
    assert not specialist.tool_entry({"path": str(path), "objective": "x" * 12000})["ok"]
    path.write_text("x" * 25000)
    assert not specialist.tool_entry({"path": str(path), "objective": "fix"})["ok"]
    assert not state.posts


def test_http_error_not_success_and_gate_released(setup):
    path, _, state = setup
    state.exception = requests.HTTPError("backend unavailable")
    result = specialist.tool_entry({"path": str(path), "objective": "fix"})
    assert not result["ok"] and "backend unavailable" in result["error"]
    with inference_slot():
        pass


def test_inference_stream_holds_gate_until_closed_and_releases_on_failure():
    entered = threading.Event()
    attempted = threading.Event()

    @serial_stream
    def stream():
        yield "first"
        yield "second"

    @serial_completion
    def completion():
        entered.set()

    def worker():
        attempted.set()
        completion()

    active = stream()
    assert next(active) == "first"
    thread = threading.Thread(target=worker)
    thread.start()
    assert attempted.wait(1)
    assert not entered.wait(0.05)
    active.close()
    thread.join(2)
    assert not thread.is_alive() and entered.is_set()

    @serial_completion
    def fail():
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError):
        fail()
    with inference_slot():
        pass


def test_loader_advertises_specialist_without_initializing_it():
    loader = ToolLoader(str(specialist.CONFIG_PATH.parents[1] / "tools"))
    names = [row["function"]["name"] for row in loader.get_tool_schemas()]
    assert "coding_specialist" in names
