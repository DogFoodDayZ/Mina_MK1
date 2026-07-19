# mk1_api.py
import os
import time
import tempfile
import subprocess
import ctypes
import threading
import json
import hashlib
import re
import wave
import sys
import difflib
from collections import deque
from pathlib import Path
from typing import Any, Optional

import uvicorn
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

try:
    import winsound  # type: ignore
except Exception:
    winsound = None

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
    input_source: Optional[str] = None


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


class ModelSelectRequest(BaseModel):
    model: str
    autoload: bool = True
    persist: bool = False
    force_reload: bool = False


def _resolve_tts_rate() -> float:
    """
    Resolve TTS speaking rate multiplier.
    Priority: env MK1_TTS_RATE -> config voice.tts_rate -> default 1.15
    Clamped to a safe intelligibility range.
    """
    default_rate = 1.15

    env_raw = (os.getenv("MK1_TTS_RATE", "") or "").strip()
    if env_raw:
        try:
            val = float(env_raw)
            return max(0.75, min(2.0, val))
        except Exception:
            pass

    try:
        cfg_val = core.config.get("voice", "tts_rate", default_rate)
        val = float(cfg_val)
        return max(0.75, min(2.0, val))
    except Exception:
        return default_rate


# ------------------------------------------------------------
# Initialize CORE + API
# ------------------------------------------------------------
core = MK1Core()
app = FastAPI(title="MK1 Core API", version="1.0")


@app.on_event("shutdown")
def _shutdown_core() -> None:
    try:
        core.close()
    except Exception:
        pass


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
        const PHONE_TTS_RATE = 1.2;
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
            msg.rate = PHONE_TTS_RATE;
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
                        // Treat phone-origin turns as voice relay events for desktop sync.
                        input_source: 'voice',
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
CONFIG_GUI_DIR = str(getattr(core, "config", None).get("gui", "fancy_dir", "") if getattr(core, "config", None) else "").strip()
FANCY_GUI_DIR = Path(os.getenv("MK1_FANCY_GUI_DIR", CONFIG_GUI_DIR or "C:/dev/mina-gui"))
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


def _norm_echo_text(text: str) -> str:
    t = str(text or "").lower()
    t = t.replace("\n", " ").replace("\r", " ")
    t = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in t)
    t = " ".join(t.split())
    return t


def _is_probable_self_echo(transcript: str) -> bool:
    probe = _norm_echo_text(transcript)
    if not probe:
        return False

    # Keep this conservative to avoid suppressing normal short utterances.
    if len(probe.split()) < 6:
        return False

    with _gui_event_lock:
        recent = list(_gui_events)[-20:]

    now = time.time()
    for ev in reversed(recent):
        try:
            ts = float(ev.get("ts") or 0.0)
        except Exception:
            ts = 0.0

        # Only compare to very recent assistant outputs.
        if ts <= 0 or (now - ts) > 180:
            continue

        reply = _norm_echo_text(ev.get("reply", ""))
        if len(reply.split()) < 6:
            continue

        # Exact/near containment catches classic feedback snippets.
        if probe in reply or reply in probe:
            return True

        score = difflib.SequenceMatcher(None, probe, reply).ratio()
        if score >= 0.82:
            return True

    return False


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

    # If the API restarts, event IDs reset to low values while GUI clients may
    # continue polling with an old high since_id. In that case, recover by
    # returning the latest available events for the requested source.
    if items:
        max_id = max(int(ev.get("id", 0)) for ev in items)
    else:
        max_id = 0

    out = []
    for ev in items:
        if int(ev.get("id", 0)) <= int(since_id):
            continue
        if src and str(ev.get("source", "")).lower() != src:
            continue
        out.append(ev)

    if len(out) > lim:
        out = out[-lim:]

    if not out and int(since_id) > 0 and max_id > 0 and int(since_id) > max_id:
        fallback = []
        for ev in items:
            if src and str(ev.get("source", "")).lower() != src:
                continue
            fallback.append(ev)
        if len(fallback) > lim:
            fallback = fallback[-lim:]
        return fallback

    return out


def _safe_logs_dir() -> str:
    base = os.path.abspath("logs")
    os.makedirs(base, exist_ok=True)
    return base


def _derive_models_endpoint(chat_url: str) -> str:
    base = str(chat_url or "").strip()
    if not base:
        return "http://127.0.0.1:1234/v1/models"

    if "/v1/chat/completions" in base:
        return base.replace("/v1/chat/completions", "/v1/models")

    if "/v1/chat" in base:
        return base.replace("/v1/chat", "/v1/models")

    return base.rstrip("/") + "/models"


def _list_lmstudio_models() -> dict:
    chat_url = str(getattr(core.model, "chat_url", "") or "")
    endpoint = _derive_models_endpoint(chat_url)

    try:
        resp = requests.get(endpoint, timeout=10)
        body = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}

        if resp.status_code >= 400:
            return {
                "ok": False,
                "endpoint": endpoint,
                "status_code": resp.status_code,
                "error": "models_list_failed",
                "detail": (resp.text or "")[:500],
            }

        items = body.get("data") if isinstance(body, dict) else None
        out = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or "").strip()
                    if model_id:
                        out.append(model_id)

        return {
            "ok": True,
            "endpoint": endpoint,
            "models": out,
            "count": len(out),
        }
    except Exception as e:
        return {
            "ok": False,
            "endpoint": endpoint,
            "error": "models_list_exception",
            "detail": str(e),
        }


def _persist_default_model(model_name: str) -> dict:
    cfg_path = PROJECT_ROOT / "config" / "mk1_config.json"
    try:
        if not cfg_path.exists():
            return {
                "ok": False,
                "error": "config_missing",
                "path": str(cfg_path),
            }

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error": "config_invalid",
                "path": str(cfg_path),
            }

        model_cfg = data.get("model")
        if not isinstance(model_cfg, dict):
            model_cfg = {}
            data["model"] = model_cfg

        model_cfg["default_model"] = model_name
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "path": str(cfg_path),
            "default_model": model_name,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "config_write_failed",
            "detail": str(e),
            "path": str(cfg_path),
        }


def _allowed_switch_models() -> list[str]:
    cfg = getattr(core, "config", None)
    if cfg is None:
        return []

    raw = cfg.get("model", "switch_allowed", [])
    if not isinstance(raw, list):
        return []

    out = []
    seen = set()
    for item in raw:
        name = str(item or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _switch_model_runtime(
    model_name: str,
    autoload: bool = True,
    persist: bool = False,
    force_reload: bool = False,
) -> dict:
    target = str(model_name or "").strip()
    if not target:
        return {
            "ok": False,
            "error": "model_required",
        }

    previous = str(getattr(core.model, "default_model", "") or "")
    allowed = _allowed_switch_models()
    if allowed:
        allowed_map = {m.lower(): m for m in allowed}
        mapped = allowed_map.get(target.lower())
        if mapped is None:
            return {
                "ok": False,
                "error": "model_not_allowed",
                "active_model": previous,
                "requested_model": target,
                "allowed_models": allowed,
            }
        target = mapped

    load_endpoint = str(getattr(core, "config", None).get("model", "load_endpoint", "") if getattr(core, "config", None) else "").strip()

    load_attempted = False
    load_ok = False
    load_error = None

    skip_reason = None
    same_model = previous.strip().lower() == target.strip().lower()
    if same_model and autoload and not force_reload:
        skip_reason = "same_model_already_active"
        autoload = False

    if autoload and load_endpoint:
        load_attempted = True
        payload_candidates = [
            {"model": target},
            {"identifier": target},
            {"id": target},
        ]

        for payload in payload_candidates:
            try:
                resp = requests.post(load_endpoint, json=payload, timeout=30)
                if resp.status_code < 400:
                    load_ok = True
                    load_error = None
                    break
                load_error = (resp.text or "")[:500]
            except Exception as e:
                load_error = str(e)

    setattr(core.model, "default_model", target)
    setattr(core.model, "_vision_support_cache_value", None)
    setattr(core.model, "_vision_support_cache_until", 0.0)

    persisted = None
    if persist:
        persisted = _persist_default_model(target)

    return {
        "ok": True,
        "previous_model": previous,
        "active_model": target,
        "same_model": same_model,
        "autoload": bool(autoload),
        "force_reload": bool(force_reload),
        "load_attempted": load_attempted,
        "load_ok": load_ok,
        "skip_reason": skip_reason,
        "load_endpoint": load_endpoint or None,
        "load_error": load_error,
        "allowed_models": allowed,
        "persist": bool(persist),
        "persist_result": persisted,
    }


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


def _extract_repeat_seconds(tags: Any) -> Optional[int]:
    if not isinstance(tags, list):
        return None
    for tag in tags:
        m = re.match(r"^repeat_(\d+)s$", str(tag or "").strip().lower())
        if not m:
            continue
        val = int(m.group(1))
        if val > 0:
            return val
    return None


def _task_is_alarm(item: dict) -> bool:
    tags = item.get("tags")
    if isinstance(tags, list):
        lowered = {str(t or "").strip().lower() for t in tags}
        if "alarm_task" in lowered:
            return True
    text = str(item.get("text") or "").strip().lower()
    return text.startswith("alarm")


def _play_alarm_beep_async(repeats: int = 2, frequency_hz: int = 880, duration_ms: int = 250) -> dict:
    safe_repeats = max(1, min(8, int(repeats)))
    safe_hz = max(250, min(2400, int(frequency_hz)))
    safe_duration = max(60, min(1500, int(duration_ms)))

    def _worker() -> None:
        try:
            if winsound is not None:
                for _ in range(safe_repeats):
                    winsound.Beep(safe_hz, safe_duration)
                    time.sleep(0.08)
                return
        except Exception:
            pass

        try:
            for _ in range(safe_repeats):
                print("\a", end="", flush=True)
                time.sleep(max(0.08, safe_duration / 1000.0))
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {
        "ok": True,
        "started": True,
        "mode": "async",
        "repeats": safe_repeats,
        "frequency_hz": safe_hz,
        "duration_ms": safe_duration,
    }


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
    tts_rate = _resolve_tts_rate()
    allow_local_fallback = os.getenv("MK1_ALLOW_PYTTSX3_FALLBACK", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Prefer Edge TTS for higher quality voices (e.g., en-US-AnaNeural).
    try:
        import asyncio
        import edge_tts  # type: ignore

        async def _save_edge_tts(tts_text: str, tts_voice: str, tts_out: str):
            # edge-tts expects a signed percent string, e.g. "+15%".
            rate_pct = int(round((tts_rate - 1.0) * 100.0))
            rate_str = f"{rate_pct:+d}%"
            communicate = edge_tts.Communicate(tts_text, voice=tts_voice, rate=rate_str)
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
            "rate": tts_rate,
        }
    except Exception:
        pass

    # Fallback for offline/local environments without edge-tts.
    if not allow_local_fallback:
        return {
            "ok": False,
            "error": "tts_engine_unavailable",
            "detail": "Edge TTS failed and pyttsx3 fallback is disabled. Set MK1_ALLOW_PYTTSX3_FALLBACK=1 to allow the local voice fallback.",
        }

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
        base_rate = engine.getProperty("rate") or 200
        try:
            engine.setProperty("rate", int(float(base_rate) * float(tts_rate)))
        except Exception:
            pass
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
            "rate": tts_rate,
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
    source_hint = str(req.input_source or "").strip().lower()
    event_source = "voice" if source_hint == "voice" else "process"
    channel = "voice" if event_source == "voice" else "text"

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
                source=event_source,
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

    if isinstance(out, dict):
        out.setdefault("source", event_source)
        out.setdefault("channel", channel)

    _push_gui_event(
        source=event_source,
        input_text=req.input,
        reply_text=str((out or {}).get("reply") or ""),
        extra={"deduped": bool(out.get("deduped"))} if isinstance(out, dict) else None,
    )

    return out


@app.post("/process-stream")
def process_stream(req: ProcessRequest):
    """
    Streaming processing endpoint (SSE).
    Emits JSON lines in SSE data frames:
      data: {"type":"chunk","content":"..."}
      data: {"type":"done"}
    """

    def event_stream():
        full_parts: list[str] = []
        try:
            for chunk in core.process_stream(
                req.input,
                image_attachment=req.image_attachment,
            ):
                part = str(chunk or "")
                if not part:
                    continue
                full_parts.append(part)
                payload = json.dumps(
                    {"type": "chunk", "content": part},
                    ensure_ascii=False,
                )
                yield f"data: {payload}\n\n"

            full_reply = "".join(full_parts).strip()
            _push_gui_event(
                source="process_stream",
                input_text=req.input,
                reply_text=full_reply,
                extra=None,
            )
            yield "data: {\"type\":\"done\"}\n\n"
        except Exception as e:
            err = json.dumps(
                {"type": "error", "error": str(e)},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


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

            if _is_probable_self_echo(user_text):
                return {
                    "ok": True,
                    "stage": "filtered",
                    "source": "voice",
                    "channel": "voice",
                    "input_text": user_text,
                    "reply": "",
                    "filtered": "self_echo",
                    "stt_engine": stt.get("engine"),
                }

            proc = core.process(user_text)
            reply_text = str(proc.get("reply") or "").strip()

            response = {
                "ok": True,
                "stage": "done",
                "source": "voice",
                "channel": "voice",
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


@app.get("/tasks/list")
def tasks_list(limit: int = 12):
    safe_limit = max(1, min(50, int(limit)))
    now_ts = time.time()

    try:
        if not hasattr(core, "memory"):
            return {"ok": True, "tasks": [], "due_count": 0, "upcoming_count": 0, "now": now_ts}

        mem = core.memory
        if not hasattr(mem, "get_task_memories"):
            return {"ok": True, "tasks": [], "due_count": 0, "upcoming_count": 0, "now": now_ts}

        rows = mem.get_task_memories(top_k=safe_limit, include_done=False)
        tasks = []
        due_count = 0
        upcoming_count = 0

        for item in rows:
            due_at = item.get("due_at")
            due = False
            if due_at is not None:
                try:
                    due = float(due_at) <= now_ts
                except Exception:
                    due = False

            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            repeat_seconds = _extract_repeat_seconds(tags)
            is_alarm = _task_is_alarm(item)

            if due:
                due_count += 1
            else:
                upcoming_count += 1

            tasks.append(
                {
                    "id": item.get("id"),
                    "text": item.get("text"),
                    "status": item.get("task_status", "pending"),
                    "due_at": due_at,
                    "due": due,
                    "alarm": is_alarm,
                    "repeat_seconds": repeat_seconds,
                    "repeat_minutes": (int(round(float(repeat_seconds) / 60.0)) if repeat_seconds else None),
                    "beep": bool(is_alarm and due),
                }
            )

        tasks.sort(
            key=lambda t: (
                0 if t.get("due") else 1,
                float(t.get("due_at")) if t.get("due_at") is not None else 9e18,
                int(t.get("id") or 0),
            )
        )

        return {
            "ok": True,
            "now": now_ts,
            "due_count": due_count,
            "upcoming_count": upcoming_count,
            "tasks": tasks[:safe_limit],
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "now": now_ts,
            "due_count": 0,
            "upcoming_count": 0,
            "tasks": [],
        }


@app.post("/tasks/ring_due_alarm")
def tasks_ring_due_alarm(repeats: int = 2):
    now_ts = time.time()
    try:
        mem = getattr(core, "memory", None)
        if mem is None or not hasattr(mem, "get_due_tasks"):
            return {"ok": False, "error": "task_memory_not_available"}

        due_rows = mem.get_due_tasks(top_k=25, include_tags=["scheduled_task"])
        alarm_row = None
        for row in due_rows:
            if _task_is_alarm(row):
                alarm_row = row
                break

        if alarm_row is None:
            return {
                "ok": True,
                "played": False,
                "reason": "no_due_alarm",
                "now": now_ts,
            }

        beep_result = _play_alarm_beep_async(repeats=repeats)
        return {
            "ok": bool(beep_result.get("ok")),
            "played": bool(beep_result.get("ok")),
            "task_id": alarm_row.get("id"),
            "text": alarm_row.get("text"),
            "due_at": alarm_row.get("due_at"),
            "now": now_ts,
            "beep": beep_result,
        }
    except Exception as e:
        return {
            "ok": False,
            "played": False,
            "error": str(e),
            "now": now_ts,
        }


@app.post("/tasks/mark_done")
def tasks_mark_done(task_id: int):
    try:
        mem = getattr(core, "memory", None)
        if mem is None or not hasattr(mem, "set_task_status"):
            return {"ok": False, "error": "task_status_update_not_supported"}

        ok = bool(mem.set_task_status(int(task_id), "done"))
        return {"ok": ok, "task_id": int(task_id)}
    except Exception as e:
        return {"ok": False, "error": str(e), "task_id": int(task_id)}


@app.post("/tasks/snooze")
def tasks_snooze(task_id: int, minutes: int = 10):
    try:
        mem = getattr(core, "memory", None)
        if mem is None or not hasattr(mem, "snooze_task"):
            return {"ok": False, "error": "task_snooze_not_supported"}

        safe_minutes = max(1, min(24 * 60, int(minutes)))
        ok = bool(mem.snooze_task(int(task_id), safe_minutes * 60))
        return {"ok": ok, "task_id": int(task_id), "minutes": safe_minutes}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "task_id": int(task_id),
            "minutes": int(minutes),
        }


@app.get("/model/status")
def model_status(force_refresh: bool = False, check_vision: bool = False):
    model_name = str(getattr(core.model, "default_model", "") or "unknown")
    supports_vision = bool(
        getattr(core.model, "supports_vision", lambda *_args, **_kwargs: False)(
            force_refresh=force_refresh,
            allow_probe=bool(check_vision),
        )
    )
    return {
        "model": model_name,
        "supports_vision": supports_vision,
        "mode": "VISION_READY" if supports_vision else "TEXT_ONLY",
        "allowed_switch_models": _allowed_switch_models(),
    }


@app.get("/model/list")
def model_list():
    return _list_lmstudio_models()


@app.post("/model/select")
def model_select(req: ModelSelectRequest):
    return _switch_model_runtime(
        model_name=req.model,
        autoload=bool(req.autoload),
        persist=bool(req.persist),
        force_reload=bool(req.force_reload),
    )


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
