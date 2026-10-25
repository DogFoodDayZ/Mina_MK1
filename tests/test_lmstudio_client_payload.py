import json

import pytest

import agent.core as core_module
from agent.core import LMStudioClient, MK1Core


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _completion_payload(text="ok"):
    return {
        "choices": [{"message": {"content": text, "reasoning_content": ""}}],
    }


@pytest.mark.parametrize("content", [None, "", " \n\t", [], [{"type": "text", "text": " "}]])
def test_read_broken_empty_completion_fails_without_retry_or_reasoning_leak(monkeypatch, content):
    """Live prompt: 'Read [path] and explain why it will not start. Do not edit or run it.'"""
    calls = []
    payload = {
        "choices": [{"finish_reason": "stop", "message": {
            "content": content, "reasoning_content": "PRIVATE_REASONING",
        }}],
        "usage": {"completion_tokens": 336, "completion_tokens_details": {"reasoning_tokens": 335}},
    }
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: (
        calls.append(kwargs) or FakeResponse(payload)
    ))
    client = LMStudioClient({"default_model": "fixture", "fallback_models": ["other"]})
    with pytest.raises(RuntimeError, match="Model returned no final answer or tool call") as error:
        client.chat([{"role": "user", "content": "Read repair.py and explain why it will not start."}],
                    allow_fallback=True)
    assert len(calls) == 1
    assert "PRIVATE_REASONING" not in str(error.value)


@pytest.mark.parametrize("content", [
    "Hello!", [{"type": "text", "text": "Hello!"}], {"content": "Hello!"}, {"text": "Hello!"},
])
def test_nonempty_completion_is_preserved(monkeypatch, content):
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: (
        FakeResponse(_completion_payload(content))
    ))
    result = LMStudioClient({}).chat([{"role": "user", "content": "hi mina"}])
    assert result["choices"][0]["message"]["content"] == content


def test_empty_content_with_native_tool_call_is_not_empty_response(monkeypatch):
    call = {"id": "read-1", "type": "function", "function": {
        "name": "file_read", "arguments": '{"path":"repair.py"}',
    }}
    payload = {"choices": [{"message": {"content": None, "tool_calls": [call]}}]}
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: FakeResponse(payload))
    assert LMStudioClient({}).chat([{"role": "user", "content": "read repair.py"}]) == payload


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_missing_completion_also_fails_explicitly(monkeypatch, payload):
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: FakeResponse(payload))
    with pytest.raises(RuntimeError, match="Model returned no final answer or tool call"):
        LMStudioClient({}).chat([{"role": "user", "content": "hello"}])


@pytest.mark.parametrize("delta", [
    {}, {"content": None}, {"content": " \n"}, {"reasoning_content": "PRIVATE_REASONING"},
    {"content": "<think>PRIVATE_REASONING</think>"},
    {"content": "<think>PRIVATE_REASONING"},
])
def test_empty_stream_fails_closes_response_and_does_not_retry(monkeypatch, delta):
    import requests

    response = requests.Response()
    response.status_code = 200
    response._content = (
        "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")
    response._content_consumed = True
    calls, closed, visible = [], [], []
    monkeypatch.setattr(response, "close", lambda: closed.append(True))
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: (
        calls.append(kwargs) or response
    ))
    client = LMStudioClient({"default_model": "fixture", "fallback_models": ["other"]})
    with pytest.raises(RuntimeError, match="Model returned no final answer or tool call") as error:
        visible.extend(client.chat_stream([{"role": "user", "content": "Read repair.py."}],
                                          allow_fallback=True))
    assert len(calls) == len(closed) == 1
    assert "PRIVATE_REASONING" not in str(error.value) + str(visible)


@pytest.mark.parametrize("tool_name", ["file_read", "code_repair"])
def test_diagnosis_evidence_reaches_native_wire_without_losing_existing_indentation(monkeypatch, tool_name):
    """'Read [path] and explain why it will not start. Do not edit or run it.'"""
    source = "values = [1, 2, 3]\r\nfor value in values\r\n    print(value)\r\n"
    diff = (
        "--- before\n+++ saved\n@@ -1,3 +1,3 @@\n values = [1, 2, 3]\n"
        "-for value in values\n+for value in values:\n     print(value)\n"
    )
    result = (
        {"content": source, "decoder_used": "utf-8", "bom": None}
        if tool_name == "file_read" else
        {"changes": [{"unified_diff": diff, "truncated": False}], "stdout": "1\n2\n3\n"}
    )
    messages = [
        {"role": "system", "content": "Use actual tool evidence, not prior narration."},
        {"role": "user", "content": "Read the file and explain why it will not start."},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "observed", "type": "function",
            "function": {"name": tool_name, "arguments": '{"path":"repair.py"}'},
        }]},
        {"role": "tool", "tool_call_id": "observed", "content": json.dumps({"ok": True, "result": result})},
    ]
    captured = []
    monkeypatch.setattr(core_module.requests, "post", lambda url, json, timeout: (
        captured.append(json) or FakeResponse(_completion_payload())
    ))
    client = LMStudioClient({"context_window": 8448, "max_tokens": 2048})
    client.chat(messages, tools=[{
        "type": "function", "function": {"name": tool_name, "parameters": {"type": "object"}},
    }], max_tokens_override=1024)
    wire_messages = captured[-1]["messages"]
    assert wire_messages == messages
    observed = json.loads(wire_messages[-1]["content"])["result"]
    assert observed == result
    if tool_name == "file_read":
        assert observed["content"].splitlines()[2] == "    print(value)"
        assert observed["content"].count("\r\n") == 3
        assert observed["bom"] is None
    else:
        assert "\n     print(value)\n" in observed["changes"][0]["unified_diff"]
        assert "\n+    print(value)" not in observed["changes"][0]["unified_diff"]


def test_native_followup_budgets_actual_output_cap_without_dropping_current_evidence(monkeypatch):
    """Live GUI repair: saved code, then failed at 6490 input tokens against a 6400 budget."""
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model", "context_window": 8448, "max_tokens": 2048,
    })
    messages = [
        {"role": "system", "content": "Original personality and required schemas: " + "x" * 19000},
        {"role": "user", "content": "Please get the file working. Save and test the saved file."},
        {"role": "tool", "content": "file_write verified SHA-256: current revision"},
    ]
    tools = [{"type": "function", "function": {"name": "code_execute", "parameters": {"type": "object"}}}]
    captured = []
    monkeypatch.setattr(core_module.requests, "post", lambda url, json, timeout: (
        captured.append(json) or FakeResponse(_completion_payload())
    ))
    with pytest.raises(ValueError, match="budget is 6400"):
        client.chat(messages, tools=tools)
    client.chat(messages, tools=tools, max_tokens_override=1024)
    assert captured[-1]["max_tokens"] == 1024 and captured[-1]["tools"] == tools
    assert captured[-1]["messages"] == messages
    assert client.max_tokens == 2048


def test_context_trim_keeps_current_request_with_tool_result_and_drops_old_turns():
    """Travis: 'find workspace\\jellybean.py and read it tell me what it does'."""
    client = LMStudioClient({"context_window": 800, "max_tokens": 200})
    current = [
        {"role": "user", "content": "find workspace\\jellybean.py and read it tell me what it does"},
        {"role": "assistant", "content": "Reading jellybean.py."},
        {"role": "tool", "content": "[file_read result]\nprint('jellybeans')"},
    ]
    messages = [
        {"role": "system", "content": "Personality"},
        {"role": "system", "content": "Relevant memory context:\n" + "old fact " * 200},
        {"role": "user", "content": "Old unrelated image"},
        {"role": "assistant", "content": "Old image answer " * 1000},
    ] + current
    result = client._trim_messages_to_budget(messages)
    assert result[-3:] == current
    assert result[0] == messages[0]
    assert client._estimate_tokens(result) <= 600
    assert messages[2] not in result
    assert messages[3] not in result
    assert messages[1]["content"].endswith("old fact " * 200)


def test_context_budget_rejects_oversize_current_tool_result_instead_of_dropping_request():
    client = LMStudioClient({"context_window": 300, "max_tokens": 100})
    with pytest.raises(ValueError, match="current turn"):
        client._trim_messages_to_budget([
            {"role": "system", "content": "Personality"},
            {"role": "user", "content": "Read jellybean.py"},
            {"role": "tool", "content": "x" * 3000},
        ])


def test_context_budget_keeps_skully_followup_before_older_recalled_conversation():
    client = LMStudioClient({"context_window": 900, "max_tokens": 200})
    recent = [
        {"role": "user", "content": "ready skully.py"},
        {"role": "assistant", "content": "I checked the active directory; skully.py was not there."},
        {"role": "user", "content": "the file is in your workspace find it"},
    ]
    messages = [
        {"role": "system", "content": "Original personality"},
        {"role": "system", "content": "Available tools (JSON Schema arguments):\nfile_read, dir_list"},
        {"role": "system", "content": "Relevant memory context:\n" + "Past workspace conversation. " * 70},
        {"role": "system", "content": "Retrieved older conversation (historical evidence)\nHoodie Edition"},
    ] + recent
    result = client._trim_messages_to_budget(messages)
    assert result[-3:] == recent
    assert messages[2] not in result
    assert messages[3] not in result
    assert result[:2] == messages[:2]
    assert client._estimate_tokens(result) <= 700


def test_context_budget_never_leaves_an_orphaned_historical_assistant_reply():
    client = LMStudioClient({"context_window": 400, "max_tokens": 100})
    messages = [
        {"role": "system", "content": "Original personality"},
        {"role": "user", "content": "Old request " * 65},
        {"role": "assistant", "content": "Unrelated old answer"},
        {"role": "user", "content": "mina read \\workspace\\skully.py"},
    ]
    result = client._trim_messages_to_budget(messages)
    assert result == [messages[0], messages[-1]]


@pytest.mark.parametrize("vision", [True, False])
def test_vision_uses_backend_metadata_without_inference(monkeypatch, vision):
    """User: 'can add a image as beforee but cant actualy send the image'."""
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "qwen/qwen3.5-9b",
    })
    calls = []

    def get(url, timeout):
        calls.append(url)
        return FakeResponse({"models": [
            {"key": "unrelated", "capabilities": {"vision": not vision}},
            {"key": client.default_model, "capabilities": {"vision": vision}},
        ]})

    monkeypatch.setattr(core_module.requests, "get", get)
    monkeypatch.setattr(core_module.requests, "post", lambda *a, **kw: pytest.fail("Capability lookup ran inference"))
    assert client.supports_vision(allow_probe=True) is vision
    assert client.supports_vision() is vision
    assert calls == ["http://127.0.0.1:1234/api/v1/models"]


def test_chat_payload_includes_frequency_and_presence_penalty(monkeypatch):
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model",
        "max_tokens": 4096,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.4,
    })

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(_completion_payload())

    monkeypatch.setattr(core_module.requests, "post", fake_post)

    client.chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["frequency_penalty"] == 0.3
    assert captured["payload"]["presence_penalty"] == 0.4
    assert captured["payload"]["max_tokens"] == 4096


def test_chat_max_tokens_override_replaces_configured_default(monkeypatch):
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model",
        "max_tokens": 4096,
    })

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(_completion_payload())

    monkeypatch.setattr(core_module.requests, "post", fake_post)

    client.chat([{"role": "user", "content": "hi"}], max_tokens_override=1200)

    assert captured["payload"]["max_tokens"] == 1200


@pytest.mark.parametrize("effort", ["none", "off"])
def test_chat_explicitly_disables_reasoning(monkeypatch, effort):
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model",
        "reasoning_effort": effort,
    })

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(_completion_payload())

    monkeypatch.setattr(core_module.requests, "post", fake_post)

    client.chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["reasoning_effort"] == "none"


def test_chat_forwards_enabled_reasoning_effort(monkeypatch):
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model",
        "reasoning_effort": "low",
    })

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(_completion_payload())

    monkeypatch.setattr(core_module.requests, "post", fake_post)

    client.chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["reasoning_effort"] == "low"


def test_countdown_stream_sends_configured_token_cap_and_timeout(monkeypatch):
    client = LMStudioClient({
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "default_model": "test-model",
        "max_tokens": 37,
        "request_timeout_seconds": 23,
        "reasoning_effort": "none",
    })
    captured = {}

    class StreamingResponse(FakeResponse):
        closed = False

        def iter_lines(self, decode_unicode=False):
            yield 'data: {"choices":[{"delta":{"content":"On it!"}}]}'
            yield "data: [DONE]"

        def close(self):
            self.closed = True

    response = StreamingResponse({})

    def fake_post(url, json=None, stream=None, timeout=None):
        captured.update(payload=json, stream=stream, timeout=timeout)
        return response

    monkeypatch.setattr(core_module.requests, "post", fake_post)
    prompt = "mina make a script that counts down from 10 to 0 save it as countdown.py"

    assert "".join(client.chat_stream([{"role": "user", "content": prompt}])) == "On it!"
    assert captured["payload"]["messages"] == [{"role": "user", "content": prompt}]
    assert captured["payload"]["max_tokens"] == 37
    assert captured["payload"]["reasoning_effort"] == "none"
    assert captured["timeout"] == 23
    assert captured["stream"] is True
    assert response.closed


def test_stream_decodes_utf8_even_when_response_defaults_to_latin1(monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 200
    response.encoding = "ISO-8859-1"
    expected = "Hello Travis \U0001f98a \u2014 Mina here!"
    response._content = (
        'data: {"choices":[{"delta":{"content":"' + expected + '"}}]}\n\n'
        'data: [DONE]\n\n'
    ).encode("utf-8")
    response._content_consumed = True
    monkeypatch.setattr(core_module.requests, "post", lambda *args, **kwargs: response)
    client = LMStudioClient({"default_model": "fixture", "max_tokens": 64})
    assert "".join(client.chat_stream([{"role": "user", "content": "Hi Mina"}])) == expected


def test_extract_text_does_not_promote_reasoning_to_chat_reply():
    core = MK1Core.__new__(MK1Core)

    payload = {
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": "ghost the\n\n,, to with: is, _file:",
            }
        }]
    }

    assert core._extract_text(payload) == ""


def test_extract_text_preserves_visible_content_and_emoji():
    core = MK1Core.__new__(MK1Core)

    payload = {
        "choices": [{
            "message": {
                "content": "Hi 😊",
                "reasoning_content": "hidden reasoning",
            }
        }]
    }

    assert core._extract_text(payload) == "Hi 😊"


def test_native_stream_reassembles_fragmented_call_arguments(monkeypatch):
    import requests
    import json

    response = requests.Response()
    response.status_code = 200
    fragments = [
        {"index": 0, "id": "call-1", "function": {"name": "file_read", "arguments": '{"path":'}},
        {"index": 0, "function": {"arguments": '"E:\\\\workspace\\\\skully.py"}'}},
    ]
    response._content = (
        "".join("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [fragment]}}]}) + "\n\n"
                for fragment in fragments) + "data: [DONE]\n\n"
    ).encode("utf-8")
    response._content_consumed = True
    monkeypatch.setattr(core_module.requests, "post", lambda *a, **kw: response)
    client = LMStudioClient({"default_model": "fixture", "max_tokens": 64})
    events = list(client.chat_stream([{"role": "user", "content": "read skully.py"}]))
    call = events[0]["tool_calls"][0]
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "file_read"
    assert json.loads(call["function"]["arguments"]) == {"path": "E:\\workspace\\skully.py"}


def test_native_schema_cost_cannot_bypass_context_budget():
    client = LMStudioClient({"context_window": 400, "max_tokens": 100})
    with pytest.raises(ValueError, match="Context budget"):
        client._budget_model_messages(
            [{"role": "system", "content": "Original personality"}, {"role": "user", "content": "hi"}],
            [{"description": "x" * 2000}],
        )


def test_native_call_arguments_count_toward_input_budget():
    client = LMStudioClient({"context_window": 400, "max_tokens": 100})
    with pytest.raises(ValueError, match="Context budget"):
        client._trim_messages_to_budget([
            {"role": "system", "content": "Original personality"},
            {"role": "user", "content": "inspect this"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "file_read", "arguments": "x" * 2000},
            }]},
        ])
