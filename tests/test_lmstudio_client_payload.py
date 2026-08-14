import agent.core as core_module
from agent.core import LMStudioClient


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
