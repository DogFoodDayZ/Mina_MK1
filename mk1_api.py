# mk1_api.py
import os
import time
import tempfile
import subprocess
import ctypes
import threading
import json
import wave
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File
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
                play = _play_audio_local(str(tts.get("audio_path")))
            out["tts"] = tts
            if play is not None:
                out["tts_playback"] = play

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
