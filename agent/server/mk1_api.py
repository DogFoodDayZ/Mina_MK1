# mk1_api.py
import os
import time
import tempfile
import subprocess
import ctypes
import threading
import json
import hashlib
import wave
import sys
from collections import deque
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

try:
    import multipart  # type: ignore  # noqa: F401

    MULTIPART_AVAILABLE = True
except Exception:
    MULTIPART_AVAILABLE = False

from agent.core import MK1Core  # <-- note: from agent.core


# ------------------------------------------------------------
# Request Models
# ------------------------------------------------------------
class ProcessRequest(BaseModel):
    input: str
    speak_response: Optional[bool] = None
    voice_hint: Optional[str] = None
    image_attachment: Optional[dict] = None


class TTSRequest(BaseModel):
    text: str
    voice_hint: Optional[str] = None
    output_path: Optional[str] = None


class MemoryWriteRequest(BaseModel):
    text: str
    kind: str = "fact"
    tags: Optional[list[str]] = None


class MemoryDeleteRequest(BaseModel):
    text: Optional[str] = None
    memory_id: Optional[int] = None
    include_kinds: Optional[list[str]] = None
    include_tags: Optional[list[str]] = None


# ------------------------------------------------------------
# Initialize CORE + API
# ------------------------------------------------------------
core = MK1Core()
app = FastAPI(title="MK1 Core API", version="1.0")


PHONE_HTML = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <title>Mina Phone Link</title>
    <style>
        :root {
            --bg: #06090a;
            --panel: #0c1214;
            --line: #1a2a2f;
            --txt: #d6ffe6;
            --muted: #8fc6a8;
            --ok: #2cfaa2;
            --warn: #ffbf3a;
            --danger: #ff4a5e;
        }
        * { box-sizing: border-box; }
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: radial-gradient(circle at 50% 0%, #132027 0%, var(--bg) 58%);
            color: var(--txt);
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        }
        .app {
            max-width: 760px;
            margin: 0 auto;
            min-height: 100dvh;
            display: grid;
            grid-template-rows: auto 1fr auto;
            gap: 10px;
            padding: 12px;
        }
        .top {
            display: grid;
            grid-template-columns: 92px 1fr;
            gap: 12px;
            align-items: center;
            padding: 10px;
            border: 1px solid var(--line);
            border-radius: 14px;
            background: linear-gradient(180deg, #0d1518 0%, #0a1013 100%);
        }
        .avatar {
            width: 92px;
            height: 92px;
            border-radius: 50%;
            border: 2px solid #1b3b33;
            overflow: hidden;
            background: radial-gradient(circle at 45% 35%, #9dffd0 0%, #35d48a 50%, #14975f 100%);
            box-shadow: 0 0 0 2px #0a1713, 0 0 16px #2cfaa255;
            transition: transform 120ms ease, box-shadow 180ms ease;
        }
        .avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }
        .avatar.talk {
            transform: scale(1.03);
            box-shadow: 0 0 0 2px #0a1713, 0 0 22px #2cfaa2aa;
            animation: pulse 700ms infinite;
        }
        @keyframes pulse {
            0% { filter: brightness(1); }
            50% { filter: brightness(1.15); }
            100% { filter: brightness(1); }
        }
        .status {
            display: grid;
            gap: 6px;
            min-width: 0;
        }
        .title {
            font-size: 16px;
            color: var(--ok);
            font-weight: 700;
            letter-spacing: 0.4px;
        }
        .sub {
            color: var(--muted);
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .badges {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .badge {
            font-size: 11px;
            border-radius: 999px;
            border: 1px solid #335;
            padding: 3px 8px;
            color: #d6e8ff;
            background: #1a2438;
        }
        .badge.ok {
            border-color: #115a3e;
            background: #0f2f24;
            color: #8dffca;
        }
        .log {
            border: 1px solid var(--line);
            border-radius: 14px;
            background: #060b0c;
            padding: 12px;
            overflow: auto;
            display: grid;
            gap: 10px;
            align-content: start;
            min-height: 0;
        }
        .row {
            border: 1px solid #163136;
            border-radius: 10px;
            padding: 8px 10px;
            font-size: 13px;
            line-height: 1.36;
            white-space: pre-wrap;
            word-break: break-word;
            background: #091214;
        }
        .row.you {
            border-color: #4b3810;
            background: #1a1407;
            color: #ffd982;
        }
        .row.mina {
            border-color: #1b3f35;
            background: #08130f;
            color: #c6ffe0;
        }
        .controls {
            border: 1px solid var(--line);
            border-radius: 14px;
            background: #0a1012;
            padding: 10px;
            display: grid;
            gap: 8px;
        }
        textarea {
            width: 100%;
            min-height: 74px;
            max-height: 180px;
            resize: vertical;
            border-radius: 10px;
            border: 1px solid #29424a;
            background: #060d10;
            color: var(--txt);
            padding: 10px;
            font: inherit;
            outline: none;
        }
        .btnrow {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 8px;
        }
        button {
            border-radius: 10px;
            border: 1px solid #3e3020;
            background: linear-gradient(180deg, #38260f 0%, #221507 100%);
            color: #ffd38a;
            padding: 10px;
            font: inherit;
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
        }
        button.alt {
            border-color: #184234;
            background: linear-gradient(180deg, #0f3025 0%, #0a2018 100%);
            color: #9cffce;
        }
        button.warn {
            border-color: #472029;
            background: linear-gradient(180deg, #33141a 0%, #250d12 100%);
            color: #ff9ca8;
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
    </style>
</head>
<body>
    <div class="app">
        <section class="top">
            <div id="avatar" class="avatar"><img id="avatarImg" alt="Mina avatar" /></div>
            <div class="status">
                <div class="title">Mina Phone Link</div>
                <div id="endpoint" class="sub"></div>
                <div class="badges">
                    <span id="apiBadge" class="badge">API CHECKING</span>
                    <span id="voiceBadge" class="badge ok">PHONE VOICE ON</span>
                </div>
            </div>
        </section>

        <section id="log" class="log"></section>

        <section class="controls">
            <textarea id="input" placeholder="Talk to Mina..."></textarea>
            <input id="audioInput" type="file" accept="audio/*" capture="user" style="display:none" />
            <div class="btnrow">
                <button id="sendBtn" class="alt">SEND</button>
                <button id="micBtn">MIC</button>
                <button id="voiceBtn" class="warn">VOICE ON</button>
            </div>
        </section>
    </div>

    <script>
        const apiBase = window.location.origin;
        const endpoint = document.getElementById('endpoint');
        const apiBadge = document.getElementById('apiBadge');
        const voiceBadge = document.getElementById('voiceBadge');
        const log = document.getElementById('log');
        const input = document.getElementById('input');
        const audioInput = document.getElementById('audioInput');
        const avatar = document.getElementById('avatar');
        const avatarImg = document.getElementById('avatarImg');
        const sendBtn = document.getElementById('sendBtn');
        const micBtn = document.getElementById('micBtn');
        const voiceBtn = document.getElementById('voiceBtn');

        endpoint.textContent = apiBase;

        let phoneVoiceOn = true;
        let avatarTalkTimer = null;
        let avatarAlt = false;
        let currentAvatarState = '';

        function addRow(kind, text) {
            const row = document.createElement('div');
            row.className = `row ${kind}`;
            row.textContent = text;
            log.appendChild(row);
            log.scrollTop = log.scrollHeight;
        }

        function setAvatarFrame(state) {
            if (currentAvatarState === state) return;
            currentAvatarState = state;
            avatarImg.src = `${apiBase}/phone/avatar/${state}`;
        }

        function setAvatarTalk(on) {
            const active = Boolean(on);
            avatar.classList.toggle('talk', active);

            if (active) {
                if (!avatarTalkTimer) {
                    avatarAlt = false;
                    setAvatarFrame('talk');
                    avatarTalkTimer = setInterval(() => {
                        avatarAlt = !avatarAlt;
                        setAvatarFrame(avatarAlt ? 'alt' : 'talk');
                    }, 240);
                }
                return;
            }

            if (avatarTalkTimer) {
                clearInterval(avatarTalkTimer);
                avatarTalkTimer = null;
            }
            setAvatarFrame('idle');
        }

        function setVoiceBadge() {
            voiceBadge.textContent = phoneVoiceOn ? 'PHONE VOICE ON' : 'PHONE VOICE OFF';
            voiceBadge.className = phoneVoiceOn ? 'badge ok' : 'badge';
            voiceBtn.textContent = phoneVoiceOn ? 'VOICE ON' : 'VOICE OFF';
        }

        async function checkApi() {
            try {
                const r = await fetch(`${apiBase}/status?force_refresh=true`);
                if (!r.ok) throw new Error(String(r.status));
                apiBadge.textContent = 'API ONLINE';
                apiBadge.className = 'badge ok';
            } catch {
                apiBadge.textContent = 'API OFFLINE';
                apiBadge.className = 'badge';
            }
        }

        function speakOnPhone(text) {
            if (!phoneVoiceOn || !window.speechSynthesis) return;
            const msg = new SpeechSynthesisUtterance(text);
            msg.rate = 1.0;
            msg.pitch = 1.0;
            msg.onstart = () => setAvatarTalk(true);
            msg.onend = () => setAvatarTalk(false);
            msg.onerror = () => setAvatarTalk(false);
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(msg);
        }

        async function sendPrompt() {
            const text = input.value.trim();
            if (!text) return;
            addRow('you', `YOU: ${text}`);
            input.value = '';
            sendBtn.disabled = true;
            setAvatarTalk(true);

            try {
                const res = await fetch(`${apiBase}/process`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        input: text,
                        // Let phone do playback to avoid desktop speaker dependency.
                        speak_response: false
                    })
                });

                const data = await res.json();
                const reply = String(data?.reply || '(no reply)');
                addRow('mina', `MINA: ${reply}`);
                speakOnPhone(reply);
            } catch (err) {
                addRow('mina', `MINA: (connection error: ${err?.message || err})`);
                setAvatarTalk(false);
            } finally {
                sendBtn.disabled = false;
                if (!window.speechSynthesis || !phoneVoiceOn) {
                    setAvatarTalk(false);
                }
            }
        }

        async function sendCapturedAudio(file) {
            if (!file) return;
            addRow('you', 'YOU: [voice captured]');
            micBtn.disabled = true;
            micBtn.textContent = 'SENDING';
            setAvatarTalk(true);

            try {
                const form = new FormData();
                form.append('file', file, file.name || 'phone_capture.webm');

                const res = await fetch(`${apiBase}/voice/process?speak_response=false`, {
                    method: 'POST',
                    body: form,
                });

                const data = await res.json();
                if (!res.ok || !data?.ok) {
                    const detail = String(data?.detail || data?.error || `HTTP ${res.status}`);
                    addRow('mina', `MINA: (voice failed: ${detail})`);
                    return;
                }

                const said = String(data?.input_text || '').trim();
                const reply = String(data?.reply || '(no reply)').trim();
                if (said) {
                    addRow('you', `YOU: ${said}`);
                }
                addRow('mina', `MINA: ${reply}`);
                speakOnPhone(reply);
            } catch (err) {
                addRow('mina', `MINA: (voice upload error: ${err?.message || err})`);
            } finally {
                micBtn.disabled = false;
                micBtn.textContent = 'MIC';
                if (!window.speechSynthesis || !phoneVoiceOn) {
                    setAvatarTalk(false);
                }
            }
        }

        sendBtn.addEventListener('click', sendPrompt);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendPrompt();
            }
        });
        voiceBtn.addEventListener('click', () => {
            phoneVoiceOn = !phoneVoiceOn;
            setVoiceBadge();
            if (!phoneVoiceOn && window.speechSynthesis) {
                window.speechSynthesis.cancel();
                setAvatarTalk(false);
            }
        });
        micBtn.addEventListener('click', () => {
            audioInput.click();
        });
        audioInput.addEventListener('change', () => {
            const file = audioInput.files && audioInput.files[0] ? audioInput.files[0] : null;
            if (file) {
                sendCapturedAudio(file);
            }
            audioInput.value = '';
        });

        setAvatarFrame('idle');
        setVoiceBadge();
        checkApi();
        setInterval(checkApi, 5000);
        addRow('mina', 'MINA: Phone link online.');
    </script>
</body>
</html>
"""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESTORE_SCRIPT = PROJECT_ROOT / "restore" / "restore.py"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
START_API_SCRIPT = PROJECT_ROOT / "start_mk1_api.ps1"
FANCY_GUI_DIR = Path(os.getenv("MK1_FANCY_GUI_DIR", "C:/dev/mina-gui"))
FANCY_AVATAR_FILES = {
    "idle": "avatar_idle.png",
    "talk": "avatar_talk.png",
    "alt": "avatar_talk_2.png",
    "smirk": "avatar_smirk.png",
}


STATUS_CACHE_TTL = float(os.getenv("MK1_STATUS_CACHE_TTL", "1.0"))
DB_STATUS_CACHE_TTL = float(os.getenv("MK1_DB_STATUS_CACHE_TTL", "1.0"))

_status_cache = {
    "value": None,
    "expires_at": 0.0,
}

_db_status_cache = {
    "value": None,
    "expires_at": 0.0,
}

GUI_EVENT_BUFFER_MAX = int(os.getenv("MK1_GUI_EVENT_BUFFER_MAX", "300"))
_gui_events = deque(maxlen=max(50, GUI_EVENT_BUFFER_MAX))
_gui_event_lock = threading.Lock()
_gui_event_seq = 0

PROCESS_DEDUPE_WINDOW_SECONDS = float(os.getenv("MK1_PROCESS_DEDUPE_WINDOW_SECONDS", "30"))
_process_dedupe_lock = threading.Lock()
_process_dedupe_cache = {
    "fingerprint": None,
    "response": None,
    "ts": 0.0,
}


def _resolve_phone_avatar_path(state: str) -> Optional[Path]:
    key = str(state or "").strip().lower()
    if key not in FANCY_AVATAR_FILES:
        key = "idle"

    filename = FANCY_AVATAR_FILES.get(key, "avatar_idle.png")
    candidates = [
        FANCY_GUI_DIR / "src" / "assets" / filename,
        PROJECT_ROOT / "agent" / "gui" / "assets" / filename,
        PROJECT_ROOT / "assets" / filename,
    ]

    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _build_process_fingerprint(req: ProcessRequest) -> str:
    text = (req.input or "").strip()
    img = req.image_attachment if isinstance(req.image_attachment, dict) else {}

    name = str(img.get("name") or "")
    media_type = str(img.get("type") or "")
    size = int(img.get("size") or 0)
    data_url = str(img.get("data_url") or "")
    data_hash = hashlib.sha1(data_url.encode("utf-8", errors="ignore")).hexdigest() if data_url else ""

    fp_src = json.dumps(
        {
            "input": text,
            "name": name,
            "type": media_type,
            "size": size,
            "data_hash": data_hash,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha1(fp_src.encode("utf-8")).hexdigest()


def _extract_tool_output(reply_text: str) -> str:
    text = str(reply_text or "")
    marker = "VERIFIED OUTPUT:"
    if marker not in text:
        return ""

    tail = text.split(marker, 1)[1].strip()
    if tail.startswith("```text"):
        tail = tail[len("```text"):].lstrip("\r\n")
    if tail.startswith("```"):
        tail = tail[3:].lstrip("\r\n")

    if "```" in tail:
        tail = tail.split("```", 1)[0]

    return tail.strip()


def _push_gui_event(source: str, input_text: str, reply_text: str, extra: Optional[dict[str, Any]] = None) -> None:
    global _gui_event_seq

    cleaned_input = str(input_text or "").strip()
    cleaned_reply = str(reply_text or "").strip()
    tool_output = _extract_tool_output(cleaned_reply)

    with _gui_event_lock:
        _gui_event_seq += 1
        event = {
            "id": _gui_event_seq,
            "ts": time.time(),
            "source": source,
            "input_text": cleaned_input,
            "reply": cleaned_reply,
            "tool_output": tool_output,
            "has_tool_output": bool(tool_output),
        }
        if isinstance(extra, dict) and extra:
            event["extra"] = extra
        _gui_events.append(event)


def _get_gui_events_since(since_id: int, limit: int, source: str = "") -> list[dict[str, Any]]:
    lim = min(max(1, int(limit)), 200)
    src = str(source or "").strip().lower()

    with _gui_event_lock:
        items = list(_gui_events)

    out = []
    for ev in items:
        if int(ev.get("id", 0)) <= int(since_id):
            continue
        if src and str(ev.get("source", "")).lower() != src:
            continue
        out.append(ev)

    if len(out) > lim:
        out = out[-lim:]
    return out


def _safe_logs_dir() -> str:
    base = os.path.abspath("logs")
    os.makedirs(base, exist_ok=True)
    return base


def _play_audio_local(path: str) -> dict:
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return {"ok": False, "error": "audio_missing", "path": p}

    try:
        ff = subprocess.run(
            ["where", "ffplay"],
            capture_output=True,
            text=True,
            shell=True,
            check=False,
        )
        if ff.returncode == 0:
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", p],
                check=False,
            )
            return {"ok": True, "method": "ffplay"}
    except Exception:
        pass

    try:
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "mina_server_tts"
        mci(f"close {alias}", None, 0, None)
        if mci(f'open "{p}" alias {alias}', None, 0, None) == 0:
            mci(f"play {alias} wait", None, 0, None)
            mci(f"close {alias}", None, 0, None)
            return {"ok": True, "method": "mci"}
    except Exception:
        pass

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Start-Process '{p}'",
            ],
            check=False,
        )
        return {"ok": True, "method": "start-process"}
    except Exception as e:
        return {"ok": False, "error": "playback_failed", "detail": str(e)}


def _play_audio_local_async(path: str) -> dict:
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return {"ok": False, "error": "audio_missing", "path": p}

    def _worker() -> None:
        try:
            _play_audio_local(p)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"ok": True, "started": True, "mode": "async", "path": p}


def _transcribe_audio_file(path: str) -> dict:
    """
    Local STT strategy:
    1) If openai-whisper is installed, use it (supports many formats).
    2) Else if faster-whisper is installed, use it (CPU-friendly).
    3) Else if vosk is installed, transcribe WAV PCM mono files.
    """
    # Whisper fallback (best compatibility)
    try:
        import whisper  # type: ignore

        model_name = os.getenv("MK1_STT_MODEL", "base")
        model = whisper.load_model(model_name)
        result = model.transcribe(path, fp16=False)
        text = str(result.get("text") or "").strip()
        if text:
            return {"ok": True, "text": text, "engine": f"whisper:{model_name}"}
    except Exception:
        pass

    # faster-whisper fallback
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model_name = os.getenv("MK1_STT_MODEL", "small.en")
        compute_type = os.getenv("MK1_STT_COMPUTE_TYPE", "int8")
        model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        segments, _ = model.transcribe(
            path,
            vad_filter=False,
            language="en",
            beam_size=5,
        )
        text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip()).strip()
        if text:
            return {"ok": True, "text": text, "engine": f"faster-whisper:{model_name}"}
    except Exception:
        pass

    # Vosk fallback (WAV-only)
    try:
        from vosk import Model, KaldiRecognizer  # type: ignore

        model_path = os.getenv("MK1_VOSK_MODEL_PATH", "")
        if not model_path or not os.path.isdir(model_path):
            return {
                "ok": False,
                "error": "stt_model_missing",
                "detail": "Set MK1_VOSK_MODEL_PATH to a valid vosk model directory, or install openai-whisper.",
            }

        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            if channels != 1 or sampwidth != 2:
                return {
                    "ok": False,
                    "error": "unsupported_audio_format",
                    "detail": "Vosk fallback expects WAV PCM mono 16-bit audio.",
                }

            rec = KaldiRecognizer(Model(model_path), framerate)
            out_parts = []
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    out_parts.append(rec.Result())
            out_parts.append(rec.FinalResult())

        # Parse simplistic JSON chunks without external deps.
        text_pieces = []
        for chunk in out_parts:
            if '"text"' not in chunk:
                continue
            # Very small parse helper; avoids adding json import overhead here.
            marker = '"text"'
            idx = chunk.find(marker)
            if idx < 0:
                continue
            colon = chunk.find(":", idx)
            if colon < 0:
                continue
            q1 = chunk.find('"', colon + 1)
            if q1 < 0:
                continue
            q2 = chunk.find('"', q1 + 1)
            if q2 < 0:
                continue
            piece = chunk[q1 + 1:q2].strip()
            if piece:
                text_pieces.append(piece)

        text = " ".join(text_pieces).strip()
        if text:
            return {"ok": True, "text": text, "engine": "vosk"}

        return {"ok": False, "error": "stt_empty", "detail": "No speech recognized."}
    except Exception as e:
        return {"ok": False, "error": "stt_failed", "detail": str(e)}


def _synthesize_tts(text: str, voice_hint: Optional[str], output_path: Optional[str]) -> dict:
    voice_pref = (voice_hint or os.getenv("MK1_TTS_VOICE", "en-US-AnaNeural") or "").strip()

    # Prefer Edge TTS for higher quality voices (e.g., en-US-AnaNeural).
    try:
        import asyncio
        import edge_tts  # type: ignore

        async def _save_edge_tts(tts_text: str, tts_voice: str, tts_out: str):
            communicate = edge_tts.Communicate(tts_text, voice=tts_voice)
            await communicate.save(tts_out)

        def _run_edge_tts_sync(tts_text: str, tts_voice: str, tts_out: str):
            try:
                # If this does not raise, we are inside an active loop (e.g., async FastAPI route).
                asyncio.get_running_loop()
                in_running_loop = True
            except RuntimeError:
                in_running_loop = False

            if not in_running_loop:
                asyncio.run(_save_edge_tts(tts_text, tts_voice, tts_out))
                return

            err_box = {"error": None}

            def _worker():
                try:
                    asyncio.run(_save_edge_tts(tts_text, tts_voice, tts_out))
                except Exception as e:
                    err_box["error"] = e

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join()

            if err_box["error"] is not None:
                raise err_box["error"]

        edge_out = output_path or os.path.join(_safe_logs_dir(), "mina_tts_last.mp3")
        edge_out = os.path.abspath(edge_out)
        os.makedirs(os.path.dirname(edge_out), exist_ok=True)

        _run_edge_tts_sync(text, (voice_pref or "en-US-AnaNeural"), edge_out)
        return {
            "ok": True,
            "audio_path": edge_out,
            "engine": "edge-tts",
            "voice": (voice_pref or "en-US-AnaNeural"),
        }
    except Exception:
        pass

    # Fallback for offline/local environments without edge-tts.
    try:
        import pyttsx3  # type: ignore
    except Exception:
        return {
            "ok": False,
            "error": "tts_dependency_missing",
            "detail": "Install edge-tts (preferred) or pyttsx3 (fallback) for local TTS support.",
        }

    out = output_path or os.path.join(_safe_logs_dir(), "mina_tts_last.wav")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    try:
        engine = pyttsx3.init()
        selected_voice = ""
        if voice_pref:
            hint = voice_pref.lower()
            for v in engine.getProperty("voices"):
                name = str(getattr(v, "name", ""))
                vid = str(getattr(v, "id", ""))
                if hint in name.lower() or hint in vid.lower():
                    engine.setProperty("voice", getattr(v, "id", None))
                    selected_voice = name or vid
                    break

        engine.save_to_file(text, out)
        engine.runAndWait()
        return {
            "ok": True,
            "audio_path": out,
            "engine": "pyttsx3",
            "voice": selected_voice or "system-default",
        }
    except Exception as e:
        return {"ok": False, "error": "tts_failed", "detail": str(e)}


def _get_cached(cache_obj, ttl_seconds, fetch_fn, force_refresh=False):
    now = time.monotonic()

    if not force_refresh and cache_obj["value"] is not None and now < cache_obj["expires_at"]:
        return cache_obj["value"]

    value = fetch_fn()
    cache_obj["value"] = value
    cache_obj["expires_at"] = now + max(0.0, ttl_seconds)
    return value


# ------------------------------------------------------------
# CORS (THIS IS WHERE IT GOES)
# ------------------------------------------------------------
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/phone", response_class=HTMLResponse)
def phone_ui():
    return HTMLResponse(content=PHONE_HTML)


@app.get("/phone/avatar/{state}")
def phone_avatar(state: str):
    path = _resolve_phone_avatar_path(state)
    if path is None:
        raise HTTPException(status_code=404, detail="avatar_not_found")
    return FileResponse(str(path))


# ------------------------------------------------------------
# POST /process
# ------------------------------------------------------------
@app.post("/process")
def process(req: ProcessRequest):
    """
    Main processing endpoint.
    Accepts: { "input": "string" }
    Returns: { "output": { "reply": "string" } }
    """
    fp = _build_process_fingerprint(req)
    now = time.time()

    with _process_dedupe_lock:
        cached_fp = _process_dedupe_cache.get("fingerprint")
        cached_out = _process_dedupe_cache.get("response")
        cached_ts = float(_process_dedupe_cache.get("ts") or 0.0)

        if (
            cached_fp
            and cached_fp == fp
            and isinstance(cached_out, dict)
            and now - cached_ts <= max(0.0, PROCESS_DEDUPE_WINDOW_SECONDS)
        ):
            out = dict(cached_out)
            out["deduped"] = True
            _push_gui_event(
                source="process",
                input_text=req.input,
                reply_text=str(out.get("reply") or ""),
                extra={"deduped": True},
            )
            # Skip optional TTS/playback on deduped requests to avoid repetitive loops.
            return out

    try:
        out = core.process(
            req.input,
            image_attachment=req.image_attachment,
        )
    except TypeError as e:
        # Backward-compatible path for tests/mocks or older core adapters
        # that still expose process(user_input) without image_attachment.
        if "unexpected keyword argument 'image_attachment'" in str(e):
            out = core.process(req.input)
        else:
            raise

    with _process_dedupe_lock:
        _process_dedupe_cache["fingerprint"] = fp
        _process_dedupe_cache["response"] = dict(out) if isinstance(out, dict) else {"reply": str(out)}
        _process_dedupe_cache["ts"] = time.time()

    env_auto = os.getenv("MK1_PROCESS_AUTO_SPEAK", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    speak = env_auto if req.speak_response is None else bool(req.speak_response)

    if speak:
        reply_text = str((out or {}).get("reply") or "").strip()
        if reply_text:
            voice_pref = (req.voice_hint or os.getenv("MK1_PROCESS_VOICE", "en-US-AnaNeural") or "").strip()
            tts = _synthesize_tts(text=reply_text, voice_hint=voice_pref, output_path=None)
            play = None
            if tts.get("ok") and tts.get("audio_path"):
                play = _play_audio_local_async(str(tts.get("audio_path")))
            out["tts"] = tts
            if play is not None:
                out["tts_playback"] = play

    _push_gui_event(
        source="process",
        input_text=req.input,
        reply_text=str((out or {}).get("reply") or ""),
        extra={"deduped": bool(out.get("deduped"))} if isinstance(out, dict) else None,
    )

    return out


if MULTIPART_AVAILABLE:

    @app.post("/voice/stt")
    def voice_stt(file: UploadFile = File(...)):
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
                data = file.file.read()
                tmp.write(data)

            out = _transcribe_audio_file(tmp_path)
            return out
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
else:

    @app.post("/voice/stt")
    def voice_stt_unavailable():
        return {
            "ok": False,
            "error": "multipart_not_installed",
            "detail": "Install python-multipart to enable file upload STT endpoints.",
        }


@app.post("/voice/tts")
def voice_tts(req: TTSRequest):
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text"}
    return _synthesize_tts(text=text, voice_hint=req.voice_hint, output_path=req.output_path)


if MULTIPART_AVAILABLE:

    @app.post("/voice/process")
    def voice_process(file: UploadFile = File(...), speak_response: bool = False, voice_hint: Optional[str] = None):
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
                data = file.file.read()
                tmp.write(data)

            stt = _transcribe_audio_file(tmp_path)
            if not stt.get("ok"):
                return {"ok": False, "stage": "stt", **stt}

            user_text = str(stt.get("text") or "").strip()
            if not user_text:
                return {"ok": False, "stage": "stt", "error": "empty_transcript"}

            proc = core.process(user_text)
            reply_text = str(proc.get("reply") or "").strip()

            response = {
                "ok": True,
                "stage": "done",
                "input_text": user_text,
                "reply": reply_text,
                "stt_engine": stt.get("engine"),
            }

            if speak_response and reply_text:
                tts = _synthesize_tts(text=reply_text, voice_hint=voice_hint, output_path=None)
                response["tts"] = tts

            _push_gui_event(
                source="voice",
                input_text=user_text,
                reply_text=reply_text,
                extra={"stt_engine": stt.get("engine")},
            )

            return response
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
else:

    @app.post("/voice/process")
    def voice_process_unavailable():
        return {
            "ok": False,
            "error": "multipart_not_installed",
            "detail": "Install python-multipart to enable voice/process file upload endpoint.",
        }


# ------------------------------------------------------------
# GET /status
# ------------------------------------------------------------
@app.get("/status")
def status(force_refresh: bool = False):
    """
    Returns CORE health block.
    All fields must be strings.
    """
    return _get_cached(
        _status_cache,
        STATUS_CACHE_TTL,
        core.get_core_status,
        force_refresh=force_refresh,
    )


# ------------------------------------------------------------
# GET /db/status
# ------------------------------------------------------------
@app.get("/db/status")
def db_status(force_refresh: bool = False):
    """
    Returns DB health block.
    All fields must be strings.
    """
    return _get_cached(
        _db_status_cache,
        DB_STATUS_CACHE_TTL,
        core.get_db_status,
        force_refresh=force_refresh,
    )


@app.get("/model/status")
def model_status(force_refresh: bool = False):
    model_name = str(getattr(core.model, "default_model", "") or "unknown")
    supports_vision = bool(
        getattr(core.model, "supports_vision", lambda *_args, **_kwargs: False)(
            force_refresh=force_refresh
        )
    )
    return {
        "model": model_name,
        "supports_vision": supports_vision,
        "mode": "VISION_READY" if supports_vision else "TEXT_ONLY",
    }


@app.get("/events/recent")
def events_recent(since_id: int = 0, limit: int = 50, source: str = ""):
    return _get_gui_events_since(since_id=since_id, limit=limit, source=source)


@app.post("/restore/open")
def restore_open():
    if not RESTORE_SCRIPT.exists():
        return {
            "ok": False,
            "error": "restore_script_missing",
            "path": str(RESTORE_SCRIPT),
        }

    python_exe = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    if not python_exe.exists():
        return {
            "ok": False,
            "error": "python_not_found",
            "path": str(python_exe),
        }

    try:
        kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        proc = subprocess.Popen(
            [str(python_exe), str(RESTORE_SCRIPT)],
            **kwargs,
        )
        return {
            "ok": True,
            "pid": proc.pid,
            "script": str(RESTORE_SCRIPT),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "launch_failed",
            "detail": str(e),
        }


@app.post("/service/start_api")
def service_start_api():
    if not START_API_SCRIPT.exists():
        return {
            "ok": False,
            "error": "start_script_missing",
            "path": str(START_API_SCRIPT),
        }

    try:
        kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        proc = subprocess.Popen(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(START_API_SCRIPT),
            ],
            **kwargs,
        )
        return {
            "ok": True,
            "pid": proc.pid,
            "script": str(START_API_SCRIPT),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "launch_failed",
            "detail": str(e),
        }


@app.post("/service/open_fancy_gui")
def service_open_fancy_gui():
    if not FANCY_GUI_DIR.exists():
        return {
            "ok": False,
            "error": "fancy_gui_dir_missing",
            "path": str(FANCY_GUI_DIR),
        }

    try:
        kwargs: dict[str, Any] = {
            "cwd": str(FANCY_GUI_DIR),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        cmd = "Set-Location '{}' ; pnpm tauri dev".format(str(FANCY_GUI_DIR).replace("'", "''"))
        proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                cmd,
            ],
            **kwargs,
        )
        return {
            "ok": True,
            "pid": proc.pid,
            "cwd": str(FANCY_GUI_DIR),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "launch_failed",
            "detail": str(e),
        }


# ------------------------------------------------------------
# GET /memory/promoted
# ------------------------------------------------------------
@app.get("/memory/promoted")
def memory_promoted(limit: int = 20):
    """
    Returns the latest auto-promoted long-term memories.
    """
    return core.get_auto_promoted_memories(limit=limit)


@app.get("/memory/read")
def memory_read(query: str, top_k: int = 5):
    result = core.tools.run("memory_read", {"query": query, "top_k": top_k})
    return result


@app.post("/memory/write")
def memory_write(req: MemoryWriteRequest):
    tags = req.tags if isinstance(req.tags, list) else []
    result = core.tools.run("memory_write", {"text": req.text, "kind": req.kind, "tags": tags})
    return result


@app.post("/memory/delete")
def memory_delete(req: MemoryDeleteRequest):
    text = (req.text or "").strip()
    deleted = 0

    if req.memory_id is not None:
        deleted = core.memory.delete_memory_ids([int(req.memory_id)])
    elif text:
        deleted = core.memory.delete_memory_by_text(
            text,
            include_kinds=req.include_kinds,
            include_tags=req.include_tags,
        )

    return {
        "ok": deleted >= 0,
        "deleted": deleted,
        "text": text or None,
        "memory_id": req.memory_id,
    }


# ------------------------------------------------------------
# MAIN ENTRYPOINT
# ------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "mk1_api:app",
        host="127.0.0.1",
        port=8000,
        reload=False
    )
