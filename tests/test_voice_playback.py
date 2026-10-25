"""Transcript: speaker output appeared as VOICE and Mina answered her own echo."""
from types import SimpleNamespace
import subprocess
import sys
import threading

import numpy as np
import pytest

import agent.voice_playback as playback
import mina_windows_voice_loop as voice
import agent.server.mk1_api as api


def test_overlapping_reply_audio_has_distinct_files(tmp_path, monkeypatch):
    class Speech:
        def __init__(self, text, **kwargs):
            self.text = text

        async def save(self, path):
            from pathlib import Path
            Path(path).write_text(self.text, encoding="utf-8")

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Speech))
    monkeypatch.setattr(api, "_safe_logs_dir", lambda: str(tmp_path))
    first = api._synthesize_tts("First voice reply", None, None)
    second = api._synthesize_tts("Different image reply", None, None)
    assert first["ok"] and second["ok"]
    assert first["audio_path"] != second["audio_path"]
    from pathlib import Path
    assert Path(first["audio_path"]).read_text() == "First voice reply"
    assert Path(second["audio_path"]).read_text() == "Different image reply"


def test_speaker_lock_crosses_processes_and_recovers_after_exit(tmp_path, monkeypatch):
    path = tmp_path / "speaker.lock"
    monkeypatch.setattr(playback, "LOCK_PATH", path)
    code = (
        "from pathlib import Path; import sys; import agent.voice_playback as p; "
        "p.LOCK_PATH=Path(sys.argv[1]); "
        "guard=p.speaker_guard(); guard.__enter__(); print('playing',flush=True); "
        "sys.stdin.readline()"
    )
    child = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-u", "-c", code, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "playing"
        assert playback.playback_active()
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
    assert not playback.playback_active()


def test_api_playback_holds_speaker_guard_until_audio_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(playback, "LOCK_PATH", tmp_path / "speaker.lock")
    audio = tmp_path / "fixture.wav"
    audio.write_bytes(b"fixture")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def play(path):
        assert playback.playback_active()
        started.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(api, "_play_audio_local", play)
    real_guard = api.speaker_guard

    from contextlib import contextmanager

    @contextmanager
    def tracked_guard():
        try:
            with real_guard():
                yield
        finally:
            finished.set()

    monkeypatch.setattr(api, "speaker_guard", tracked_guard)
    try:
        assert api._play_audio_local_async(str(audio))["started"]
        assert started.wait(5)
        assert playback.playback_active()
    finally:
        release.set()
    assert finished.wait(5)
    assert not playback.playback_active()


@pytest.mark.parametrize("external_playback", [True, False])
def test_capture_device_is_stopped_during_playback_or_voice_request(monkeypatch, external_playback):
    events = []

    class Stream:
        active = True
        reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def abort(self):
            self.active = False
            events.append("abort")

        def start(self):
            self.active = True
            events.append("start")

        def read(self, count):
            assert self.active
            self.reads += 1
            if external_playback or self.reads > 2:
                raise KeyboardInterrupt
            return np.full((count, 1), 0.1 if self.reads == 1 else 0, dtype="float32"), False

    stream = Stream()
    states = iter([True, False]) if external_playback else None
    monkeypatch.setattr(voice, "playback_active", lambda: next(states, False) if states else False)
    monkeypatch.setattr(voice.sd, "InputStream", lambda **kwargs: stream)
    monkeypatch.setattr(voice, "_is_privacy_enabled", lambda *args: False)
    monkeypatch.setattr(voice.os.path, "exists", lambda *args: False)
    monkeypatch.setattr(voice.time, "sleep", lambda *args: None)
    monkeypatch.setattr(voice, "_post_vad_metrics", lambda *args: None)

    def send(**kwargs):
        assert not stream.active
        events.append("send")

    monkeypatch.setattr(voice, "_send_audio_to_mina", send)
    args = SimpleNamespace(
        sr=16000, frame_ms=100, silence_ms=100, min_speech_ms=100, device=1,
        privacy_file="", privacy_env="", voice_hint="fixture",
        noise_floor_alpha=0.98, snr_ratio=0.5, speech_threshold=0.002,
    )
    with pytest.raises(KeyboardInterrupt):
        voice.run_continuous_loop(args)
    assert events == (["abort", "start"] if external_playback else ["abort", "send", "start"])
