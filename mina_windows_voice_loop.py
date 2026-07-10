"""
Windows-native voice loop for MK1 API.

Flow:
- Record microphone input on Windows.
- POST audio to MK1 /voice/process.
- Print Mina reply.
- Optionally play generated TTS audio if returned.

Dependencies:
- pip install sounddevice soundfile requests

Notes:
- Requires MK1 API running on localhost.
- For /voice/process file upload support, install python-multipart in API env.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import time
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
import sounddevice as sd
import soundfile as sf


def clean_text(text: str) -> str:
    t = re.sub(r"\[\[\s*reply_to[^\]]*\]\]", "", text, flags=re.IGNORECASE)
    t = re.sub(r"[`*_#>~]", "", t)
    t = t.replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def strip_emoji(text: str) -> str:
    return re.sub(r"[\U00010000-\U0010ffff]", "", text)


def play_audio_windows(path: str) -> None:
    ff = subprocess.run(
        ["where", "ffplay"],
        capture_output=True,
        text=True,
        shell=True,
        check=False,
    )
    if ff.returncode == 0:
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", path],
            check=False,
        )
        return

    # Built-in Windows fallback via MCI (plays mp3/wav without extra packages).
    try:
        mci = ctypes.windll.winmm.mciSendStringW
        safe_path = os.path.abspath(path).replace('"', "")
        alias = "mina_tts"
        mci(f"close {alias}", None, 0, None)
        open_cmd = f'open "{safe_path}" alias {alias}'
        if mci(open_cmd, None, 0, None) == 0:
            mci(f"play {alias} wait", None, 0, None)
            mci(f"close {alias}", None, 0, None)
            return
    except Exception:
        pass

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Start-Process '{path}'",
        ],
        check=False,
    )


def record_to_wav(seconds: int, sample_rate: int, input_device: int | None) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav_path = tmp.name

    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=input_device,
    )
    sd.wait()
    sf.write(wav_path, audio, sample_rate)
    return wav_path


def ask_mina_voice(base_url: str, wav_path: str, speak_response: bool, voice_hint: str) -> dict:
    url = f"{base_url.rstrip('/')}/voice/process"
    params = {"speak_response": str(bool(speak_response)).lower()}
    if voice_hint:
        params["voice_hint"] = voice_hint
    with open(wav_path, "rb") as f:
        files = {"file": (Path(wav_path).name, f, "audio/wav")}
        resp = requests.post(url, params=params, files=files, timeout=120)

    try:
        data = resp.json()
    except Exception:
        return {
            "ok": False,
            "error": "bad_json",
            "detail": (resp.text or "")[:500],
            "status": resp.status_code,
        }

    if resp.status_code >= 400:
        data.setdefault("ok", False)
        data.setdefault("status", resp.status_code)
    return data


def _is_privacy_enabled(privacy_file: str | None, privacy_env: str | None) -> bool:
    if privacy_env:
        val = str(os.getenv(privacy_env, "")).strip().lower()
        if val in {"1", "true", "yes", "on"}:
            return True
    if privacy_file:
        return os.path.exists(privacy_file)
    return False


def _handle_voice_result(out: dict) -> None:
    if not out.get("ok"):
        print("Voice request failed:")
        print(out)
        if out.get("error") == "multipart_not_installed":
            print("Hint: install python-multipart in the API environment.")
        return

    said = str(out.get("input_text") or "").strip()
    stt_engine = str(out.get("stt_engine") or "").strip()
    reply = str(out.get("reply") or "").strip()
    reply = strip_emoji(clean_text(reply))
    reply = reply.encode("ascii", "ignore").decode("ascii")

    if said:
        print(f"You: {said}")
    if stt_engine:
        print(f"STT: {stt_engine}")
    print(f"Mina: {reply or '(no reply)'}")

    tts = out.get("tts") if isinstance(out.get("tts"), dict) else None
    if tts and tts.get("ok") and tts.get("audio_path"):
        audio_path = str(tts.get("audio_path"))
        tts_engine = str(tts.get("engine") or "").strip()
        tts_voice = str(tts.get("voice") or "").strip()
        if tts_engine or tts_voice:
            print(f"TTS: {tts_engine or 'unknown'} {tts_voice}".strip())
        if os.path.exists(audio_path):
            play_audio_windows(audio_path)


def _send_audio_to_mina(audio, sr: int, args) -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav = tmp.name
    try:
        sf.write(wav, audio, sr)
        out = ask_mina_voice(args.api, wav, args.speak_response, args.voice_hint)
    finally:
        if os.path.exists(wav):
            os.remove(wav)
    _handle_voice_result(out)


def run_continuous_loop(args) -> int:
    import numpy as np

    frame_samples = max(1, int(args.sr * (args.frame_ms / 1000.0)))
    silence_chunks = max(1, int(args.silence_ms / args.frame_ms))
    min_voice_chunks = max(1, int(args.min_speech_ms / args.frame_ms))
    device = None if args.device < 0 else args.device

    print("Mina real-time voice monitor ready.")
    print(f"TTS voice hint: {args.voice_hint}")
    if args.privacy_file:
        print(f"Privacy file: {args.privacy_file} (exists => muted)")
    if args.privacy_env:
        print(f"Privacy env: {args.privacy_env}=1 (muted)")
    print("Ctrl+C to stop.")

    with sd.InputStream(
        samplerate=args.sr,
        channels=1,
        dtype="float32",
        blocksize=frame_samples,
        device=device,
    ) as stream:
        capturing = False
        frames = []
        quiet_count = 0
        voiced_count = 0
        was_muted = False
        max_rms_recent = 0.0
        last_heartbeat = time.monotonic()

        while True:
            muted = _is_privacy_enabled(args.privacy_file, args.privacy_env)
            if muted:
                if not was_muted:
                    print("[privacy] capture muted")
                    was_muted = True
                capturing = False
                frames = []
                quiet_count = 0
                voiced_count = 0
                time.sleep(0.25)
                continue
            elif was_muted:
                print("[privacy] capture resumed")
                was_muted = False

            chunk, overflowed = stream.read(frame_samples)
            if overflowed:
                continue

            rms = float(np.sqrt((chunk * chunk).mean()))
            if rms > max_rms_recent:
                max_rms_recent = rms

            now = time.monotonic()
            if now - last_heartbeat >= 3.0:
                print(f"[listen] rms_max={max_rms_recent:.4f} threshold={args.speech_threshold:.4f}")
                max_rms_recent = 0.0
                last_heartbeat = now

            voiced = rms >= args.speech_threshold

            if voiced:
                if not capturing:
                    capturing = True
                    frames = []
                    quiet_count = 0
                    voiced_count = 0
                frames.append(chunk.copy())
                voiced_count += 1
                quiet_count = 0
            elif capturing:
                frames.append(chunk.copy())
                quiet_count += 1
                if quiet_count >= silence_chunks:
                    if voiced_count >= min_voice_chunks and frames:
                        audio = np.concatenate(frames, axis=0)
                        print("Sending to Mina...")
                        _send_audio_to_mina(audio=audio, sr=args.sr, args=args)
                    capturing = False
                    frames = []
                    quiet_count = 0
                    voiced_count = 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows voice loop for MK1 API")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="MK1 API base URL")
    parser.add_argument("--seconds", type=int, default=6, help="Recording seconds per turn")
    parser.add_argument("--sr", type=int, default=16000, help="Sample rate")
    parser.add_argument("--device", type=int, default=-1, help="Input device index (-1 = default)")
    parser.add_argument("--voice-hint", default="en-US-AnaNeural", help="Preferred TTS voice")
    parser.add_argument("--continuous", action="store_true", help="Real-time VAD monitoring mode")
    parser.add_argument("--speech-threshold", type=float, default=0.002, help="RMS threshold for VAD")
    parser.add_argument("--frame-ms", type=int, default=100, help="Frame duration for VAD")
    parser.add_argument("--silence-ms", type=int, default=700, help="Silence duration to end utterance")
    parser.add_argument("--min-speech-ms", type=int, default=120, help="Minimum voiced duration to send")
    parser.add_argument("--privacy-file", default="", help="Optional: if this file exists, capture is muted")
    parser.add_argument("--privacy-env", default="", help="Optional: if set to true/on/1, capture is muted")
    parser.add_argument(
        "--speak-response",
        action="store_true",
        help="Ask API to synthesize speech and attempt playback",
    )
    args = parser.parse_args()

    device = None if args.device < 0 else args.device

    if args.privacy_file:
        args.privacy_file = os.path.abspath(args.privacy_file)

    if args.continuous:
        try:
            return run_continuous_loop(args)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")
            return 1

    print("Mina Windows voice loop ready.")
    print(f"TTS voice hint: {args.voice_hint}")
    print("Press Enter to talk, or type q then Enter to quit.")

    while True:
        try:
            cmd = input("\nPress Enter to record> ").strip().lower()
            if cmd in {"q", "quit", "exit"}:
                print("Bye.")
                return 0

            print(f"Recording {args.seconds}s...")
            wav = record_to_wav(args.seconds, args.sr, device)
            try:
                print("Sending to Mina...")
                out = ask_mina_voice(args.api, wav, args.speak_response, args.voice_hint)
            finally:
                if os.path.exists(wav):
                    os.remove(wav)
            _handle_voice_result(out)

        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except EOFError:
            print("\nInput closed.")
            return 0
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
