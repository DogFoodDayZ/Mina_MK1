"""GUI text and voice share core ownership and honor speech settings."""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agent.server.mk1_api as api


@pytest.mark.parametrize("engine", ["whisper", "faster_whisper"])
@pytest.mark.parametrize("override", [None, "A conversation with Ren and Nova."])
def test_name_vocabulary_reaches_transcriber_without_rewriting_text(monkeypatch, engine, override):
    """Transcript: 'Meena, assume I was born May 21st, 1971.'"""
    monkeypatch.delenv("MK1_STT_INITIAL_PROMPT", raising=False)
    if override is not None:
        monkeypatch.setenv("MK1_STT_INITIAL_PROMPT", override)
    configured = "A conversation between Travis and Mina."
    monkeypatch.setattr(api, "core", SimpleNamespace(
        config=SimpleNamespace(get=lambda section, key, default: configured),
    ))
    calls = []
    transcript = "Meena, assume I was born May 21st, 1971."

    def transcribe(path, **kwargs):
        calls.append((path, kwargs))
        if engine == "whisper":
            return {"text": transcript}
        return iter([SimpleNamespace(text=transcript)]), SimpleNamespace()

    model = SimpleNamespace(transcribe=transcribe)
    if engine == "whisper":
        monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=lambda name: model))
    else:
        monkeypatch.setitem(sys.modules, "whisper", None)
        monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **kw: model))
    result = api._transcribe_audio_file("fixture.wav")
    assert result["ok"]
    assert result["text"] == transcript
    assert calls[0][1]["initial_prompt"] == (configured if override is None else override)


@pytest.fixture
def rig(monkeypatch):
    calls = []

    def process(text, image_attachment=None, request_context=None):
        calls.append(("process", text, request_context))
        return {"reply": "Hello Travis!"}

    def stream(text, image_attachment=None, request_context=None):
        calls.append(("stream", text, request_context))
        yield "Hello "
        yield "Travis!"

    monkeypatch.setattr(api, "core", SimpleNamespace(
        process=process, process_stream=stream,
        model=SimpleNamespace(default_model="fixture-model"),
    ))
    monkeypatch.setattr(api, "_get_speak_enabled", lambda: True)
    monkeypatch.setattr(api, "_should_bypass_process_dedupe", lambda req: True)
    monkeypatch.setattr(api, "_push_gui_event", lambda **kwargs: None)

    def tts(**kwargs):
        calls.append(("tts", kwargs))
        return {"ok": True, "audio_path": "fixture.wav"}

    def play(path):
        calls.append(("play", path))
        return {"ok": True, "started": True}

    monkeypatch.setattr(api, "_synthesize_tts", tts)
    monkeypatch.setattr(api, "_play_audio_local_async", play)
    return TestClient(api.app), calls


def frames(response):
    return [
        json.loads(line[5:].strip()) for line in response.text.splitlines()
        if line.startswith("data:")
    ]


def test_empty_model_failure_is_not_cached_as_completed_reply(rig, monkeypatch):
    client, calls = rig
    monkeypatch.setattr(api, "_should_bypass_process_dedupe", lambda req: False)
    monkeypatch.setattr(api, "_process_dedupe_cache", {})
    replies = iter([
        "(Mina error: Model returned no final answer or tool call. This request is incomplete.)",
        "Hello Travis!",
    ])

    def process(*args, **kwargs):
        calls.append(("process",))
        return {"reply": next(replies), "actions": []}

    monkeypatch.setattr(api.core, "process", process)
    request = {"input": "Read repair.py and explain why it will not start.", "speak_response": False}
    failed = client.post("/process", json=request).json()
    assert "request is incomplete" in failed["reply"]
    assert not api._process_dedupe_cache
    assert client.post("/process", json=request).json()["reply"] == "Hello Travis!"
    assert len([call for call in calls if call[0] == "process"]) == 2


def test_action_reports_are_separate_sse_events_not_reply_or_speech(rig, monkeypatch):
    client, calls = rig
    actions = [{"tool": "code_execute", "ok": False, "exit_code": 1,
                "error": "process_exited_with_code_1", "stderr": "TimeoutExpired after 3 seconds"}]

    def stream(text, image_attachment=None, request_context=None):
        assert request_context["include_action_reports"] is True
        yield {"type": "actions", "actions": actions}
        yield "The script is running!"

    monkeypatch.setattr(api.core, "process_stream", stream)
    events = frames(client.post("/process-stream", json={
        "input": "yes all three please", "speak_response": True,
    }))
    assert events[0] == {"type": "actions", "actions": actions}
    assert "".join(e["content"] for e in events if e["type"] == "chunk") == "The script is running!"
    assert next(call[1]["text"] for call in calls if call[0] == "tts") == "The script is running!"
    assert events[-1]["type"] == "done"

def test_image_send_preserves_attachment_and_returns_confirmation(rig, monkeypatch):
    client, calls = rig
    attachment = {
        "name": "Grant.png", "type": "image/png", "size": 42,
        "data_url": "data:image/png;base64,fixture",
    }

    def process(text, image_attachment=None, request_context=None):
        assert image_attachment == attachment
        assert request_context["interface_name"] == "Mina Mk1 GUI"
        return {"reply": "Image received.", "image": {
            "received": True, "name": attachment["name"],
            "vision_model": True, "forwarded_to_model": True,
        }}

    monkeypatch.setattr(api.core, "process", process)
    response = client.post("/process", json={
        "input": "What is in this image?", "image_attachment": attachment,
        "interface_name": "Mina Mk1 GUI", "speak_response": False,
    })
    assert response.status_code == 200
    assert response.json()["image"]["forwarded_to_model"] is True
    assert response.json()["image"]["name"] == "Grant.png"


@pytest.mark.parametrize("route", ["/process", "/process-stream"])
@pytest.mark.parametrize("speak", [True, False, None])
def test_gui_text_passes_interface_and_speaks_once_when_enabled(rig, route, speak):
    client, calls = rig
    response = client.post(route, json={
        "input": "hey mina", "input_source": "gui", "interface_name": "Mina Mk1 GUI",
        "speak_response": speak, "voice_hint": "fixture-voice",
    })
    assert response.status_code == 200
    assert calls[0][2]["interface_name"] == "Mina Mk1 GUI"
    assert calls[0][2]["input_source"] == "gui"
    tts = [call for call in calls if call[0] == "tts"]
    playback = [call for call in calls if call[0] == "play"]
    assert len(tts) == len(playback) == (0 if speak is False else 1)
    if tts:
        assert tts[0][1] == {
            "text": "Hello Travis!", "voice_hint": "fixture-voice", "output_path": None,
        }
    if route.endswith("stream"):
        events = frames(response)
        assert "".join(e["content"] for e in events if e["type"] == "chunk") == "Hello Travis!"
        assert events[-1] == {"type": "done"}
        assert len([e for e in events if e["type"] == "speech"]) == len(tts)


@pytest.mark.parametrize("stage", ["synthesis", "playback"])
def test_stream_surfaces_speech_failure_without_losing_text(rig, monkeypatch, stage):
    client, calls = rig
    if stage == "synthesis":
        monkeypatch.setattr(api, "_synthesize_tts", lambda **kwargs: {"ok": False, "error": "fixture_failure"})
    else:
        monkeypatch.setattr(api, "_play_audio_local_async", lambda path: {"ok": False, "error": "fixture_failure"})
    events = frames(client.post("/process-stream", json={"input": "hello", "speak_response": True}))
    assert "".join(e["content"] for e in events if e["type"] == "chunk") == "Hello Travis!"
    speech = next(e for e in events if e["type"] == "speech")
    assert speech["tts" if stage == "synthesis" else "tts_playback"]["error"] == "fixture_failure"
    if stage == "synthesis":
        assert not any(call[0] == "play" for call in calls)
    assert events[-1]["type"] == "done"


def test_stream_failure_does_not_synthesize_partial_reply(rig, monkeypatch):
    client, calls = rig

    def broken(*args, **kwargs):
        yield "Partial"
        raise RuntimeError("fixture_stream_failure")

    monkeypatch.setattr(api.core, "process_stream", broken)
    events = frames(client.post("/process-stream", json={"input": "hello", "speak_response": True}))
    assert events[-1] == {"type": "error", "error": "fixture_stream_failure"}
    assert not any(call[0] == "tts" for call in calls)


def test_stream_respects_disabled_shared_speech_and_empty_reply(rig, monkeypatch):
    client, calls = rig
    monkeypatch.setattr(api, "_get_speak_enabled", lambda: False)
    client.post("/process-stream", json={"input": "hello"})
    assert not any(call[0] == "tts" for call in calls)

    def empty(*args, **kwargs):
        yield ""

    monkeypatch.setattr(api.core, "process_stream", empty)
    events = frames(client.post("/process-stream", json={"input": "hello", "speak_response": True}))
    assert events == [{"type": "done"}]
    assert not any(call[0] == "tts" for call in calls)


@pytest.mark.skipif(not api.MULTIPART_AVAILABLE, reason="Voice upload support is not installed")
@pytest.mark.parametrize("speak", [True, False, None])
def test_voice_transcription_uses_text_core_and_returns_audio_for_voice_client(rig, monkeypatch, speak):
    client, calls = rig
    monkeypatch.setattr(api, "_transcribe_audio_file", lambda path: {"ok": True, "text": "hey mina", "engine": "fixture"})
    monkeypatch.setattr(api, "_is_probable_self_echo", lambda text: False)
    params = {
        "voice_hint": "fixture-voice", "client_id": "fixture-client",
        "interface_name": "Mina Mk1 GUI",
    }
    if speak is not None:
        params["speak_response"] = str(speak).lower()
    http_response = client.post(
        "/voice/process", params=params,
        files={"file": ("fixture.wav", b"fixture", "audio/wav")},
    )
    assert http_response.status_code == 200
    response = http_response.json()
    assert response["reply"] == "Hello Travis!"
    assert calls[0] == ("process", "hey mina", {
        "input_source": "voice", "interface_name": "Mina Mk1 GUI",
        "client_id": "fixture-client", "client_token": None,
    })
    assert "tts" in response
    assert not any(call[0] == "play" for call in calls)


@pytest.mark.skipif(not api.MULTIPART_AVAILABLE, reason="Voice upload support is not installed")
def test_voice_monitor_cannot_override_gui_mute(rig, monkeypatch):
    client, calls = rig
    monkeypatch.setattr(api, "_get_speak_enabled", lambda: False)
    monkeypatch.setattr(api, "_transcribe_audio_file", lambda path: {"ok": True, "text": "hey mina"})
    monkeypatch.setattr(api, "_is_probable_self_echo", lambda text: False)
    response = client.post(
        "/voice/process", params={"speak_response": "true"},
        files={"file": ("fixture.wav", b"fixture", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "Hello Travis!"
    assert "tts" not in response.json()
    assert not any(call[0] in {"tts", "play"} for call in calls)


@pytest.mark.skipif(not api.MULTIPART_AVAILABLE, reason="Voice upload support is not installed")
def test_voice_reply_is_available_to_gui_while_synthesis_is_still_blocked(rig, monkeypatch):
    """User: '36 second delay between when LM Studio responds ... and the gui'."""
    client, calls = rig
    monkeypatch.setattr(api, "_transcribe_audio_file", lambda path: {"ok": True, "text": "Hello Mina"})
    monkeypatch.setattr(api, "_is_probable_self_echo", lambda text: False)
    published = []
    monkeypatch.setattr(api, "_push_gui_event", lambda **kwargs: published.append(kwargs))
    started = threading.Event()
    release = threading.Event()

    def slow_tts(**kwargs):
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("Test did not release speech synthesis")
        return {"ok": True, "audio_path": "fixture.wav"}

    monkeypatch.setattr(api, "_synthesize_tts", slow_tts)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post, "/voice/process",
            files={"file": ("fixture.wav", b"fixture", "audio/wav")},
        )
        try:
            assert started.wait(timeout=5)
            assert not pending.done()
            assert len(published) == 1
            assert published[0]["input_text"] == "Hello Mina"
            assert published[0]["reply_text"] == "Hello Travis!"
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 200
    assert response.json()["tts"]["ok"]
    assert len(published) == 1
    assert not any(call[0] == "play" for call in calls)
