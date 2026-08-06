# core.py

import os
import time
import json
import ast
import difflib
import hashlib
import hmac
import base64
import html
import atexit
import traceback
import re
import subprocess
import sys
import threading
import mimetypes
import py_compile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from agent.file_intents import detect_file_directory_intent
from agent.curiosity import AutonomousCuriosity, TOPIC_STOP_WORDS
from agent.pending_actions import (
    PendingActionStore,
    is_directory_scan_cancel,
    is_directory_scan_confirmation,
    is_write_authorization_cancel,
    is_write_authorization_confirmation,
)
from agent.reflex_helpers import (
    handle_direct_response_reflex,
    handle_directory_scan_reflex,
    handle_external_write_reflex,
    handle_identity_reflex,
    message_choice,
)
from agent.ostranauts import build_ostranauts_context, get_ostranauts_status, is_ostranauts_query
from config.config_loader import load_config
from tools.tool_loader import ToolLoader
from tools.file_access_policy import full_access_roots, normalize_tool_path
from tools.file_inspector import inspect_file, inspect_sqlite_table, is_probably_text_path
from memory.mk1_memory import MK1Memory
from memory.retention import is_failed_decision_text



# ============================================================
# LM STUDIO CLIENT
# ============================================================

class LMStudioClient:

    def __init__(self, model_cfg: Dict[str, Any]):

        self.chat_url = model_cfg.get(
            "endpoint",
            "http://127.0.0.1:1234/v1/chat",
        )

        self.default_model = model_cfg.get(
            "default_model",
            "local-model",
        )

        self.load_endpoint = str(
            model_cfg.get(
                "load_endpoint",
                "",
            )
            or ""
        ).strip()

        fallback_raw = model_cfg.get("fallback_models", [])
        if isinstance(fallback_raw, list):
            self.fallback_models = [
                str(x).strip()
                for x in fallback_raw
                if str(x).strip()
            ]
        elif isinstance(fallback_raw, str) and fallback_raw.strip():
            self.fallback_models = [x.strip() for x in fallback_raw.split(",") if x.strip()]
        else:
            self.fallback_models = []

        self.auto_fallback_on_error = bool(
            model_cfg.get(
                "auto_fallback_on_error",
                str(os.getenv("MK1_AUTO_MODEL_FALLBACK", "1")).strip().lower() in {"1", "true", "yes", "on"},
            )
        )

        self._vision_support_cache_value: Optional[bool] = None
        self._vision_support_cache_until: float = 0.0
        self._vision_support_cache_ttl: float = float(os.getenv("MK1_VISION_CHECK_TTL", "30"))
        self.vision_max_tokens: int = int(
            model_cfg.get(
                "vision_max_tokens",
                os.getenv("MK1_VISION_MAX_TOKENS", "512"),
            )
        )
        self.vision_reasoning_effort: str = str(
            model_cfg.get(
                "vision_reasoning_effort",
                os.getenv("MK1_VISION_REASONING_EFFORT", "none"),
            )
        ).strip()
        # Non-vision token cap; 0 means no explicit cap sent to the model
        self.max_tokens: int = int(
            model_cfg.get(
                "max_tokens",
                os.getenv("MK1_MAX_TOKENS", "0"),
            )
        )
        # Non-vision reasoning effort (e.g. "none", "low", "medium", "high")
        self.reasoning_effort: str = str(
            model_cfg.get(
                "reasoning_effort",
                os.getenv("MK1_REASONING_EFFORT", ""),
            )
        ).strip()
        # Context window in tokens; used to budget message trimming before sending
        self.context_window: int = int(
            model_cfg.get(
                "context_window",
                os.getenv("MK1_CONTEXT_WINDOW", "32768"),
            )
        )
        self.request_timeout_seconds: int = int(
            model_cfg.get(
                "request_timeout_seconds",
                os.getenv("MK1_LMSTUDIO_TIMEOUT_SECONDS", "240"),
            )
        )

    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Rough token estimate: 4 chars ≈ 1 token, plus 4 overhead per message."""
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            total += len(str(content)) // 4 + 4
        return total

    def _trim_messages_to_budget(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop oldest non-system messages until payload fits within context_window budget."""
        if not self.context_window or self.context_window <= 0:
            return messages
        # Reserve headroom for the model's own output
        budget = self.context_window - (self.max_tokens if self.max_tokens > 0 else 2048)
        if budget <= 0 or self._estimate_tokens(messages) <= budget:
            return messages

        system = [m for m in messages if str(m.get("role", "")).lower() == "system"]
        non_system = [m for m in messages if str(m.get("role", "")).lower() != "system"]

        # Always keep the last user message
        if non_system:
            pinned = [non_system[-1]]
            trimmable = non_system[:-1]
        else:
            return messages

        # Drop from oldest end until we fit
        while trimmable and self._estimate_tokens(system + trimmable + pinned) > budget:
            trimmable.pop(0)

        trimmed = system + trimmable + pinned
        if len(trimmed) < len(messages):
            dropped = len(messages) - len(trimmed)
            print(f"[context budget] trimmed {dropped} message(s) to fit {self.context_window}-token window")
        return trimmed

    def _has_system_messages(self, messages: List[Dict[str, Any]]) -> bool:
        return any(str(m.get("role", "")).strip().lower() == "system" for m in (messages or []))

    def _system_message_count(self, messages: List[Dict[str, Any]]) -> int:
        return sum(1 for m in (messages or []) if str(m.get("role", "")).strip().lower() == "system")

    def _extract_text_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt)
                elif isinstance(item, str) and item.strip():
                    parts.append(item)
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            txt = content.get("text")
            if isinstance(txt, str):
                return txt
        return ""

    def _flatten_system_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        system_parts: List[str] = []
        non_system: List[Dict[str, Any]] = []

        for m in (messages or []):
            role = str(m.get("role", "")).strip().lower()
            if role == "system":
                txt = self._extract_text_content(m.get("content"))
                if txt.strip():
                    system_parts.append(txt.strip())
                continue
            non_system.append(dict(m))

        if not system_parts:
            return list(messages or [])

        preamble = "[System instructions]\n" + "\n\n".join(system_parts).strip()
        merged = False

        for idx, m in enumerate(non_system):
            role = str(m.get("role", "")).strip().lower()
            if role != "user":
                continue

            content = m.get("content")
            if isinstance(content, str):
                m["content"] = f"{preamble}\n\n{content}" if content else preamble
                non_system[idx] = m
                merged = True
                break

            if isinstance(content, list):
                text_idx = None
                for j, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        text_idx = j
                        break

                if text_idx is not None:
                    part = dict(content[text_idx])
                    part["text"] = f"{preamble}\n\n{part.get('text', '')}".strip()
                    new_content = list(content)
                    new_content[text_idx] = part
                    m["content"] = new_content
                else:
                    m["content"] = [{"type": "text", "text": preamble}] + list(content)

                non_system[idx] = m
                merged = True
                break

            m["content"] = preamble
            non_system[idx] = m
            merged = True
            break

        if not merged:
            non_system.insert(0, {"role": "user", "content": preamble})

        return non_system

    def _coalesce_system_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not messages:
            return []

        system_parts: List[str] = []
        non_system: List[Dict[str, Any]] = []

        for m in messages:
            role = str(m.get("role", "")).strip().lower()
            if role == "system":
                txt = self._extract_text_content(m.get("content"))
                if txt.strip():
                    system_parts.append(txt.strip())
                continue
            non_system.append(dict(m))

        if not system_parts:
            return [dict(m) for m in messages]

        merged_system = {
            "role": "system",
            "content": "\n\n".join(system_parts).strip(),
        }

        return [merged_system] + non_system

    def _is_system_role_unsupported_error(self, body_text: str) -> bool:
        low = str(body_text or "").lower()
        markers = [
            "system role not supported",
            "role not supported",
            "unsupported role",
            "jinja exception",
            "automatic parser generation failed",
        ]
        return any(m in low for m in markers)

    def _is_retryable_model_error(self, error_text: str) -> bool:
        low = str(error_text or "").lower()
        markers = [
            "channel error",
            "failed to create completion",
            "model not loaded",
            "no model loaded",
            "context canceled",
            "broken pipe",
            "connection reset",
            "read timed out",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
            "backend error",
        ]
        return any(m in low for m in markers)

    def _candidate_models(self, requested_model: str) -> List[str]:
        out: List[str] = []
        seen = set()

        def _add(name: str) -> None:
            n = str(name or "").strip()
            if not n:
                return
            key = n.lower()
            if key in seen:
                return
            seen.add(key)
            out.append(n)

        _add(requested_model)

        for m in self.fallback_models:
            _add(m)

        env_fallbacks = str(os.getenv("MK1_MODEL_FALLBACKS", "") or "").strip()
        if env_fallbacks:
            for m in env_fallbacks.split(","):
                _add(m)

        return out

    def _try_load_model(self, model_name: str) -> bool:
        endpoint = str(self.load_endpoint or "").strip()
        if not endpoint:
            return False

        payload_candidates = [
            {"model": model_name},
            {"identifier": model_name},
            {"id": model_name},
        ]
        for payload in payload_candidates:
            try:
                resp = requests.post(endpoint, json=payload, timeout=30)
                if resp.status_code < 400:
                    return True
            except Exception:
                continue

        return False

    def _supports_vision_by_name(self) -> bool:
        model_name = str(self.default_model or "").lower()
        vision_hints = [
            "vision",
            "-vl",
            "vl-",
            "llava",
            "gemma-4",
            "qwen2-vl",
            "qwen2.5-vl",
            "qwen-vl",
            "minicpm-v",
            "internvl",
            "phi-3-vision",
            "moondream",
        ]
        return any(h in model_name for h in vision_hints)

    def _probe_vision_support(self) -> bool:
        try:
            import base64
            import binascii
            import struct
            import zlib

            width = 1
            height = 1
            bit_depth = 8
            color_type = 2

            ihdr = struct.pack(
                "!IIBBBBB",
                width,
                height,
                bit_depth,
                color_type,
                0,
                0,
                0,
            )

            raw_scanline = b"\x00\xff\x00\x00"
            idat = zlib.compress(raw_scanline, level=9)

            def _png_chunk(tag: bytes, data: bytes) -> bytes:
                crc = binascii.crc32(tag + data) & 0xFFFFFFFF
                return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", crc)

            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                + _png_chunk(b"IHDR", ihdr)
                + _png_chunk(b"IDAT", idat)
                + _png_chunk(b"IEND", b"")
            )

            tiny_png_data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        except Exception:
            tiny_png_data_url = (
                "data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7fNwAAAABJRU5ErkJggg=="
            )

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in one short phrase."},
                        {
                            "type": "image_url",
                            "image_url": {"url": tiny_png_data_url},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "stream": False,
        }

        try:
            resp = requests.post(
                self.chat_url,
                json=payload,
                timeout=20,
            )

            if resp.status_code < 400:
                return True

            body = ""
            try:
                body = (resp.text or "").lower()
            except Exception:
                body = ""

            known_non_vision_markers = [
                "failed to load image or audio file",
                "image input is not supported",
                "does not support image",
                "invalid image",
                "unsupported content type",
            ]
            if any(m in body for m in known_non_vision_markers):
                return False

            return False
        except Exception:
            return self._supports_vision_by_name()

    def supports_vision(self, force_refresh: bool = False, allow_probe: bool = False) -> bool:
        now = time.monotonic()

        # Fast path: known vision model families should not need active probing.
        if not force_refresh and self._supports_vision_by_name():
            self._vision_support_cache_value = True
            self._vision_support_cache_until = now + max(1.0, self._vision_support_cache_ttl)
            return True

        if (
            not force_refresh
            and self._vision_support_cache_value is not None
            and now < self._vision_support_cache_until
        ):
            return bool(self._vision_support_cache_value)

        # Synthetic capability probing is disabled to avoid background
        # image turns and context churn. Use model-name hints/cache only.
        return bool(self._vision_support_cache_value) if self._vision_support_cache_value is not None else False

    def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
        allow_fallback: bool = False,
        allow_model_load: bool = False,
    ) -> Dict[str, Any]:

        has_image = False
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                if any(isinstance(part, dict) and part.get("type") == "image_url" for part in content):
                    has_image = True
                    break

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        # Normalize role layout for templates that allow only one optional
        # system message before user/assistant alternation.
        if self._system_message_count(payload.get("messages", [])) > 1:
            payload["messages"] = self._coalesce_system_messages(payload.get("messages", []))

        # Trim to context window budget before sending
        payload["messages"] = self._trim_messages_to_budget(payload["messages"])

        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Vision calls are slower; cap response length to keep UI snappy.
        if has_image and self.vision_max_tokens > 0:
            payload["max_tokens"] = self.vision_max_tokens
        if has_image and self.vision_reasoning_effort:
            payload["reasoning_effort"] = self.vision_reasoning_effort

        # Apply non-vision token cap and reasoning effort when configured
        if not has_image:
            if self.max_tokens > 0:
                payload["max_tokens"] = self.max_tokens
            if self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort

        print("\n========================================")
        print("MK1 → LM STUDIO REQUEST")
        print("========================================")
        print("URL:")
        print(self.chat_url)

        requested_model = str(payload.get("model") or self.default_model)
        attempted_models = self._candidate_models(requested_model) if allow_fallback else [requested_model]
        if not attempted_models:
            attempted_models = [str(self.default_model or "local-model")]

        last_error: Optional[Exception] = None
        last_error_text = ""

        for model_idx, model_name in enumerate(attempted_models):
            attempt_payload = dict(payload)
            attempt_payload["model"] = model_name

            try:
                resp = requests.post(
                    self.chat_url,
                    json=attempt_payload,
                    timeout=self.request_timeout_seconds,
                )

                # Some OpenAI-compatible backends may not accept reasoning_effort.
                if resp.status_code >= 400 and "reasoning_effort" in attempt_payload:
                    lowered = ""
                    try:
                        lowered = (resp.text or "").lower()
                    except Exception:
                        lowered = ""

                    unsupported_markers = [
                        "reasoning_effort",
                        "unknown field",
                        "unrecognized",
                        "unexpected",
                        "invalid request",
                    ]
                    if any(m in lowered for m in unsupported_markers):
                        retry_payload = dict(attempt_payload)
                        retry_payload.pop("reasoning_effort", None)
                        resp = requests.post(
                            self.chat_url,
                            json=retry_payload,
                            timeout=self.request_timeout_seconds,
                        )

                # Some templates reject system role entirely (e.g. specific GGUF chat templates).
                if resp.status_code >= 400 and self._has_system_messages(attempt_payload.get("messages", [])):
                    body = ""
                    try:
                        body = resp.text or ""
                    except Exception:
                        body = ""

                    if self._is_system_role_unsupported_error(body):
                        retry_payload = dict(attempt_payload)
                        retry_payload["messages"] = self._flatten_system_messages(attempt_payload.get("messages", []))
                        resp = requests.post(
                            self.chat_url,
                            json=retry_payload,
                            timeout=self.request_timeout_seconds,
                        )

                # If backend channel/model state is broken, try a model load once and retry.
                if (
                    allow_model_load
                    and resp.status_code >= 400
                    and self._is_retryable_model_error(resp.text or "")
                ):
                    if self._try_load_model(model_name):
                        resp = requests.post(
                            self.chat_url,
                            json=attempt_payload,
                            timeout=self.request_timeout_seconds,
                        )

                print("\n========================================")
                print("LM STUDIO STATUS")
                print("========================================")
                print(resp.status_code)

                resp.raise_for_status()

                data = resp.json()

                # Strip reasoning traces from all choices and nested payloads.
                def _scrub_reasoning(obj: Any) -> Any:
                    if isinstance(obj, dict):
                        obj = dict(obj)
                        obj.pop("reasoning_content", None)
                        for k, v in list(obj.items()):
                            obj[k] = _scrub_reasoning(v)
                        return obj
                    if isinstance(obj, list):
                        return [_scrub_reasoning(x) for x in obj]
                    return obj

                try:
                    data = _scrub_reasoning(data)
                except Exception:
                    pass

                if model_idx > 0 and model_name != self.default_model:
                    print(f"LM STUDIO MODEL FALLBACK: switched from '{self.default_model}' to '{model_name}'")
                    self.default_model = model_name

                return data

            except Exception as e:
                last_error = e
                err_text = str(e)
                try:
                    resp_obj = getattr(e, "response", None)
                    if isinstance(e, requests.HTTPError) and resp_obj is not None:
                        err_text = f"{err_text} | {str(getattr(resp_obj, 'text', '') or '')}"
                except Exception:
                    pass
                last_error_text = err_text

                if (not self.auto_fallback_on_error) or (not self._is_retryable_model_error(err_text)):
                    break

                # Continue to next fallback model on retryable backend/model failures.
                continue

        print("\n========================================")
        print("LM STUDIO ERROR")
        print("========================================")
        traceback.print_exc()

        final_msg = last_error_text or (str(last_error) if last_error is not None else "unknown error")
        return {
            "choices": [
                {
                    "message": {
                        "content": f"(LM Studio error: {final_msg})"
                    }
                }
            ]
        }

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
        allow_fallback: bool = False,
        allow_model_load: bool = False,
    ):

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        # Normalize role layout for templates that allow only one optional
        # system message before user/assistant alternation.
        if self._system_message_count(payload.get("messages", [])) > 1:
            payload["messages"] = self._coalesce_system_messages(payload.get("messages", []))

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if allow_fallback:
            attempted_models = self._candidate_models(str(payload.get("model") or self.default_model))
        else:
            attempted_models = [str(payload.get("model") or self.default_model)]
        if not attempted_models:
            attempted_models = [str(self.default_model or "local-model")]

        last_http_error: Optional[Exception] = None

        for model_idx, model_name in enumerate(attempted_models):
            attempt_base = dict(payload)
            attempt_base["model"] = model_name

            attempts = [dict(attempt_base)]
            if self._has_system_messages(attempt_base.get("messages", [])):
                fallback_payload = dict(attempt_base)
                fallback_payload["messages"] = self._flatten_system_messages(attempt_base.get("messages", []))
                attempts.append(fallback_payload)

            for idx, attempt in enumerate(attempts):
                resp = requests.post(
                    self.chat_url,
                    json=attempt,
                    stream=True,
                    timeout=180,
                )
                try:
                    if resp.status_code >= 400:
                        body = ""
                        try:
                            body = resp.text or ""
                        except Exception:
                            body = ""

                        if (
                            idx == 0
                            and len(attempts) > 1
                            and self._is_system_role_unsupported_error(body)
                        ):
                            continue

                        if allow_model_load and self._is_retryable_model_error(body) and self._try_load_model(model_name):
                            resp.close()
                            resp = requests.post(
                                self.chat_url,
                                json=attempt,
                                stream=True,
                                timeout=180,
                            )

                        resp.raise_for_status()

                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if not raw_line:
                            continue

                        line = str(raw_line).strip()
                        if not line:
                            continue

                        if line.startswith("data:"):
                            line = line[5:].strip()

                        if line == "[DONE]":
                            break

                        try:
                            item = json.loads(line)
                        except Exception:
                            continue

                        choices = item.get("choices") or []
                        if not choices:
                            continue

                        choice0 = choices[0] if isinstance(choices[0], dict) else {}
                        delta = choice0.get("delta") if isinstance(choice0, dict) else {}

                        piece = ""
                        if isinstance(delta, dict):
                            content = delta.get("content")
                            if isinstance(content, str):
                                piece = content
                            elif isinstance(content, list):
                                out_parts: List[str] = []
                                for part in content:
                                    if isinstance(part, dict):
                                        txt = part.get("text")
                                        if isinstance(txt, str) and txt:
                                            out_parts.append(txt)
                                piece = "".join(out_parts)

                        if not piece and isinstance(choice0.get("text"), str):
                            piece = str(choice0.get("text"))

                        if piece:
                            yield piece

                    if model_idx > 0 and model_name != self.default_model:
                        print(f"LM STUDIO MODEL FALLBACK: switched from '{self.default_model}' to '{model_name}'")
                        self.default_model = model_name
                    return
                except Exception as e:
                    last_http_error = e
                finally:
                    resp.close()

            if (not allow_fallback) or (not self.auto_fallback_on_error):
                break

            if last_http_error is None or not self._is_retryable_model_error(str(last_http_error)):
                break

        if last_http_error is not None:
            raise last_http_error


# ============================================================
# MK1 CORE
# ============================================================

class MK1Core:

    def __init__(self):

        self.config = load_config()

        self.personality = (
            self.config.personality
            if hasattr(self.config, "personality")
            else ""
        )

        self.memory = MK1Memory(
            db_path=self.config.get(
                "memory",
                "db_path",
                "memory/memory.db",
            ),

            faiss_small_path=self.config.get(
                "memory",
                "faiss_small",
                "",
            ),

            faiss_base_path=self.config.get(
                "memory",
                "faiss_base",
                "",
            ),

            embed_small_url=self.config.get(
                "embedding",
                "small",
                "",
            ),

            embed_base_url=self.config.get(
                "embedding",
                "base",
                "",
            ),

            auto_backup_every_writes=self.config.get(
                "memory",
                "auto_backup_every_writes",
                100,
            ),

            auto_backup_every_seconds=self.config.get(
                "memory",
                "auto_backup_every_seconds",
                3600,
            ),

            backup_min_interval_seconds=self.config.get(
                "memory",
                "backup_min_interval_seconds",
                300,
            ),

            backup_keep_hourly_hours=self.config.get(
                "memory",
                "backup_keep_hourly_hours",
                48,
            ),

            backup_keep_daily_days=self.config.get(
                "memory",
                "backup_keep_daily_days",
                30,
            ),

            backup_keep_weekly_weeks=self.config.get(
                "memory",
                "backup_keep_weekly_weeks",
                26,
            ),

            backup_max_total=self.config.get(
                "memory",
                "backup_max_total",
                120,
            ),

            retention_enabled=self.config.get(
                "memory",
                "retention_enabled",
                True,
            ),

            interaction_retention_days=self.config.get(
                "memory",
                "interaction_retention_days",
                90,
            ),

            failed_decision_retention_days=self.config.get(
                "memory",
                "failed_decision_retention_days",
                14,
            ),

            retention_interval_seconds=self.config.get(
                "memory",
                "retention_interval_seconds",
                86400,
            ),

            retention_max_delete=self.config.get(
                "memory",
                "retention_max_delete",
                1000,
            ),

            obsidian_vault=self.config.get(
                "memory",
                "obsidian_vault",
                os.getenv("MK1_OBSIDIAN_VAULT", ""),
            ),
        )

        tools_dir = self.config.get(
            "tools",
            "directory",
            "tools",
        )

        self.tools = ToolLoader(tools_dir)
        self._seed_tool_inventory_memory()

        # Reflex tool layer controls:
        # - enabled by default so Mina can actually use tools and memory
        # - prefixes remain available for explicit triggering when desired
        self.reflex_enabled = bool(
            self.config.get(
                "tools",
                "reflex_enabled",
                True,
            )
        )
        self.reflex_requires_prefix = bool(
            self.config.get(
                "tools",
                "reflex_requires_prefix",
                False,
            )
        )
        reflex_prefixes_cfg = self.config.get(
            "tools",
            "reflex_prefixes",
            ["/tool", "tool:", "run tool"],
        )
        if isinstance(reflex_prefixes_cfg, list):
            self.reflex_prefixes = [
                str(x).strip().lower()
                for x in reflex_prefixes_cfg
                if str(x).strip()
            ]
        else:
            self.reflex_prefixes = ["/tool", "tool:", "run tool"]

        self.model = LMStudioClient({
            "endpoint": self.config.get(
                "model",
                "endpoint",
                "http://127.0.0.1:1234/v1/chat/",
            ),

            "load_endpoint": self.config.get(
                "model",
                "load_endpoint",
                "",
            ),

            "default_model": self.config.get(
                "model",
                "default_model",
                "local-model",
            ),

            "fallback_models": self.config.get(
                "model",
                "fallback_models",
                self.config.get("model", "switch_allowed", []),
            ),

            "auto_fallback_on_error": self.config.get(
                "model",
                "auto_fallback_on_error",
                True,
            ),

            # Pass through vision tuning from config so image replies are not
            # silently capped by constructor defaults.
            "vision_max_tokens": self.config.get(
                "model",
                "vision_max_tokens",
                int(os.getenv("MK1_VISION_MAX_TOKENS", "512")),
            ),

            "vision_reasoning_effort": self.config.get(
                "model",
                "vision_reasoning_effort",
                os.getenv("MK1_VISION_REASONING_EFFORT", "none"),
            ),
        })

        self.system_prompt = (
            self.personality.strip()
            if self.personality
            else ""
        )

        self.auto_promote_min_hits = self.config.get(
            "memory",
            "auto_promote_min_hits",
            2,
        )

        self.auto_promote_recent_window = self.config.get(
            "memory",
            "auto_promote_recent_window",
            40,
        )

        self.auto_promote_semantic_top_k = self.config.get(
            "memory",
            "auto_promote_semantic_top_k",
            8,
        )

        self.fast_command_mode = bool(
            self.config.get(
                "performance",
                "fast_command_mode",
                True,
            )
        )
        self.fast_command_skip_context = bool(
            self.config.get(
                "performance",
                "fast_command_skip_context",
                False,
            )
        )
        self.maintenance_min_interval_seconds = int(
            self.config.get(
                "performance",
                "maintenance_min_interval_seconds",
                20,
            )
        )
        self.idle_clock_interval_seconds = max(
            1,
            int(self.config.get("performance", "idle_clock_interval_seconds", 5)),
        )
        self.idle_checkin_interval_seconds = max(
            60,
            int(self.config.get("performance", "idle_checkin_interval_seconds", 1800)),
        )
        self.away_nag_threshold_seconds = max(
            300,
            int(self.config.get("performance", "away_nag_threshold_seconds", 21600)),
        )
        self._last_maintenance_tick_ts: float = 0.0
        self._turn_clock_lock = threading.Lock()
        self._turn_clock_stop_event = threading.Event()
        self._turn_clock_thread: Optional[threading.Thread] = None
        self._turn_clock_started_monotonic: float = time.monotonic()
        self._turn_clock_started_at: str = datetime.now().isoformat(timespec="seconds")
        self._turn_clock_tick: int = 0
        self._turn_clock_last_tick_monotonic: float = self._turn_clock_started_monotonic
        self._turn_clock_last_tick_at: str = self._turn_clock_started_at
        self._turn_clock_last_elapsed_seconds: float = 0.0
        self._last_user_activity_monotonic: float = self._turn_clock_started_monotonic
        self._last_user_activity_at: str = self._turn_clock_started_at
        self._last_shutdown_at: Optional[str] = None
        self._seconds_since_last_shutdown: Optional[float] = None
        self._away_nag_prompt_pending: bool = False
        self._away_nag_prompt_text: str = ""
        self._runtime_state_last_written_at: Optional[str] = None
        self._last_time_passage_note_monotonic: float = self._turn_clock_started_monotonic
        self._idle_checkin_pending: bool = False
        self._idle_checkin_due_since_at: Optional[str] = None
        self.curiosity_suspend_during_ostranauts = bool(
            self.config.get("curiosity", "suspend_during_ostranauts", True)
        )
        self.curiosity_pause_after_ostranauts_seconds = max(
            0,
            int(self.config.get("curiosity", "pause_after_ostranauts_seconds", 900)),
        )
        self._curiosity_gate_last_check_monotonic: float = 0.0
        self._curiosity_gate_last_active: bool = False

        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        workspace_root_cfg = self.config.get(
            "workspace",
            "root",
            "",
        )
        self.workspace_root = self._resolve_workspace_root(str(workspace_root_cfg or ""))

        configured_search_mode = str(
            self.config.get(
                "tools",
                "web_search_mode",
                os.getenv("MK1_WEB_SEARCH_MODE", "balanced"),
            )
            or "balanced"
        ).strip().lower()
        if configured_search_mode not in {"balanced", "strict", "wide"}:
            configured_search_mode = "balanced"
        self.web_search_mode = configured_search_mode
        self._load_web_search_mode_state()

        configured_action_mode = str(
            self.config.get(
                "tools",
                "action_mode_strictness",
                os.getenv("MK1_ACTION_MODE_STRICTNESS", "balanced"),
            )
            or "balanced"
        ).strip().lower()
        if configured_action_mode not in {"balanced", "strict", "relaxed"}:
            configured_action_mode = "balanced"
        self.action_mode_strictness = configured_action_mode

        self.ambiguity_ask_on_medium_low = bool(
            self.config.get(
                "tools",
                "ambiguity_ask_on_medium_low",
                True,
            )
        )
        self.action_mode_light_tone = bool(
            self.config.get(
                "tools",
                "action_mode_light_tone",
                True,
            )
        )
        self.auto_learn_intent_aliases = bool(
            self.config.get(
                "tools",
                "auto_learn_intent_aliases",
                True,
            )
        )
        self.identity_guard_enabled = bool(
            self.config.get(
                "tools",
                "identity_guard_enabled",
                True,
            )
        )
        self.voice_auth_ttl_seconds = max(
            60,
            int(self.config.get("tools", "voice_auth_ttl_seconds", 1200)),
        )
        self.voice_passphrase_kdf_iterations = max(
            120000,
            int(self.config.get("tools", "voice_passphrase_kdf_iterations", 240000)),
        )
        self.voice_passphrase_pepper = str(
            self.config.get(
                "tools",
                "voice_passphrase_pepper",
                os.getenv("MK1_VOICE_PASSPHRASE_PEPPER", ""),
            )
            or ""
        )

        trusted_tokens_raw = str(
            self.config.get(
                "tools",
                "trusted_client_tokens",
                os.getenv("MK1_TRUSTED_CLIENT_TOKENS", ""),
            )
            or ""
        ).strip()
        self.trusted_client_tokens = {
            tok.strip()
            for tok in trusted_tokens_raw.split(",")
            if tok.strip()
        }

        self._intent_alias_cache: Dict[str, Dict[str, Any]] = {}
        self._intent_alias_cache_loaded = False
        self._play_song_shorthand_cache: Dict[str, str] = {}
        self._play_song_shorthand_cache_loaded = False
        self._pending_actions = PendingActionStore()
        self._voice_passphrase_hash: str = ""
        self._voice_passphrase_hint: str = ""
        self._verified_clients: Dict[str, float] = {}
        self._file_index: Dict[str, Any] = {
            "generated_at": "",
            "entries": [],
            "by_name": {},
        }

        workspace_dirs_cfg = self.config.get(
            "workspace",
            "default_dirs",
            ["projects", "scratch", "templates", "archive"],
        )
        if isinstance(workspace_dirs_cfg, list):
            self.workspace_default_dirs = [
                str(d).strip() for d in workspace_dirs_cfg if str(d).strip()
            ]
        else:
            self.workspace_default_dirs = ["projects", "scratch", "templates", "archive"]

        self._ensure_workspace_structure()
        self._refresh_file_index(force=False)
        self._load_runtime_state()
        self._load_identity_state()
        self.startup_context = self._build_startup_context()
        self._seed_startup_memory_facts()
        self._seed_authoritative_identity_memory()
        self._seed_runtime_capability_snapshot()
        self._seed_tool_usage_rules_memory()
        self._intent_alias_cache = self._get_intent_alias_map()
        self._intent_alias_cache_loaded = True
        self._play_song_shorthand_cache = self._get_play_song_shorthand_map()
        self._play_song_shorthand_cache_loaded = True
        self.last_active_project_path: Optional[str] = None

        curiosity_roots = self.config.get("curiosity", "approved_roots", ["D:\\", "E:\\", "G:\\"])
        if not isinstance(curiosity_roots, list):
            curiosity_roots = ["D:\\", "E:\\", "G:\\"]
        self.curiosity = AutonomousCuriosity(
            memory=self.memory,
            tools=self.tools,
            approved_roots=curiosity_roots,
            enabled=bool(self.config.get("curiosity", "enabled", True)),
            idle_seconds=int(self.config.get("curiosity", "idle_seconds", 1800)),
            local_interval_seconds=int(self.config.get("curiosity", "local_interval_seconds", 3600)),
            news_interval_seconds=int(self.config.get("curiosity", "news_interval_seconds", 21600)),
            work_interval_seconds=int(self.config.get("curiosity", "work_interval_seconds", 1800)),
            max_directories_per_cycle=int(self.config.get("curiosity", "max_directories_per_cycle", 40)),
            max_file_bytes=int(self.config.get("curiosity", "max_file_bytes", 2000000)),
            max_work_retries=int(self.config.get("curiosity", "max_work_retries", 2)),
            work_provider=self._autonomous_work_candidates,
            work_executor=self._run_autonomous_work_task,
        )

        self._validate_required_tool_routes()
        self._start_turn_clock_heartbeat()
        atexit.register(self.close)

    def _resolve_workspace_root(self, configured_root: str) -> str:
        """
        Resolve workspace root deterministically for runtime behavior.

        Why: Mina needs stable access to her personal workspace and project root.
        Keep configured workspace root authoritative, with sane fallback when
        config is empty or invalid.
        """
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        raw = str(configured_root or "").strip()
        if not raw:
            return repo_root

        resolved = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(raw)
            )
        )

        if not os.path.exists(resolved) and os.path.isdir(repo_root):
            return repo_root

        return resolved

    def _search_mode_state_path(self) -> str:
        return os.path.join(self.workspace_root, ".mina_search_mode.json")

    def _runtime_state_path(self) -> str:
        return os.path.join(self.workspace_root, ".mina_runtime_state.json")

    def _save_runtime_state(self, reason: str = "tick") -> None:
        temp_path: Optional[str] = None
        try:
            now_iso = datetime.now().isoformat(timespec="seconds")
            reason_text = str(reason or "tick")
            if reason_text in {"shutdown", "shutdown_requested"}:
                self._last_shutdown_at = now_iso

            payload = {
                "updated_at": now_iso,
                "reason": reason_text,
                "turn_clock_started_at": self._turn_clock_started_at,
                "turn_clock_tick": int(self._turn_clock_tick),
                "turn_clock_last_tick_at": self._turn_clock_last_tick_at,
                "turn_clock_last_elapsed_seconds": float(self._turn_clock_last_elapsed_seconds),
                "last_user_activity_at": self._last_user_activity_at,
                "last_shutdown_at": self._last_shutdown_at,
                "idle_checkin_pending": bool(self._idle_checkin_pending),
                "idle_checkin_due_since_at": self._idle_checkin_due_since_at,
            }
            path = self._runtime_state_path()
            temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
            temp_path = None
            self._runtime_state_last_written_at = now_iso
        except Exception:
            traceback.print_exc()
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass

    def _identity_state_path(self) -> str:
        return os.path.join(self.workspace_root, ".mina_identity_state.json")

    def _save_identity_state(self) -> None:
        try:
            payload = {
                "voice_passphrase_hash": self._voice_passphrase_hash,
                "voice_passphrase_hint": self._voice_passphrase_hint,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(self._identity_state_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _load_identity_state(self) -> None:
        path = self._identity_state_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return
            self._voice_passphrase_hash = str(payload.get("voice_passphrase_hash") or "").strip()
            self._voice_passphrase_hint = str(payload.get("voice_passphrase_hint") or "").strip()
        except Exception:
            traceback.print_exc()

    def _hash_voice_phrase(self, phrase: str) -> str:
        # Legacy unsalted SHA-256 fallback kept for compatibility/migration only.
        return hashlib.sha256(str(phrase or "").strip().lower().encode("utf-8")).hexdigest()

    def _normalize_voice_phrase(self, phrase: str) -> str:
        clean = str(phrase or "").strip().lower()
        if self.voice_passphrase_pepper:
            return f"{clean}\n{self.voice_passphrase_pepper}"
        return clean

    def _kdf_voice_phrase(self, phrase: str) -> str:
        normalized = self._normalize_voice_phrase(phrase).encode("utf-8")
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            normalized,
            salt,
            int(self.voice_passphrase_kdf_iterations),
            dklen=32,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        digest_b64 = base64.b64encode(digest).decode("ascii")
        return f"pbkdf2_sha256${int(self.voice_passphrase_kdf_iterations)}${salt_b64}${digest_b64}"

    def _verify_voice_phrase_hardened(self, phrase: str, stored: str) -> bool:
        if not str(stored or "").startswith("pbkdf2_sha256$"):
            return False
        parts = str(stored).split("$")
        if len(parts) != 4:
            return False
        try:
            rounds = int(parts[1])
            salt = base64.b64decode(parts[2].encode("ascii"), validate=True)
            expected = base64.b64decode(parts[3].encode("ascii"), validate=True)
        except Exception:
            return False

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            self._normalize_voice_phrase(phrase).encode("utf-8"),
            salt,
            rounds,
            dklen=len(expected) if expected else 32,
        )
        return hmac.compare_digest(candidate, expected)

    def _voice_hint_from_phrase(self, phrase: str) -> str:
        parts = [p for p in re.findall(r"[a-zA-Z0-9]+", str(phrase or "")) if p]
        if not parts:
            return ""
        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""
        if last:
            return f"{first[:2]}...{last[-2:]}"
        return f"{first[:2]}..."

    def _enroll_voice_passphrase(self, phrase: str) -> Dict[str, Any]:
        clean = str(phrase or "").strip()
        if len(clean) < 6:
            return {"ok": False, "result": None, "error": "voice_passphrase_too_short"}
        self._voice_passphrase_hash = self._kdf_voice_phrase(clean)
        self._voice_passphrase_hint = self._voice_hint_from_phrase(clean)
        self._save_identity_state()
        return {
            "ok": True,
            "result": {
                "voice_passphrase_enrolled": True,
                "hint": self._voice_passphrase_hint,
            },
            "error": None,
        }

    def _is_trusted_client(self, request_context: Optional[Dict[str, Any]]) -> bool:
        if not self.trusted_client_tokens:
            return False
        token = str((request_context or {}).get("client_token") or "").strip()
        return bool(token and token in self.trusted_client_tokens)

    def _client_identity_key(self, request_context: Optional[Dict[str, Any]]) -> str:
        if isinstance(request_context, dict):
            cid = str(request_context.get("client_id") or "").strip()
            if cid:
                return cid
            src = str(request_context.get("input_source") or "").strip().lower()
            if src:
                return f"source:{src}"
        return "source:unknown"

    def _interface_name(self, request_context: Optional[Dict[str, Any]]) -> str:
        name = str((request_context or {}).get("interface_name") or "").strip()
        return name if name else "Unknown GUI"

    def _interface_label_line(self, request_context: Optional[Dict[str, Any]]) -> str:
        gui_name = self._interface_name(request_context)
        if gui_name == "Ostranauts GUI":
            return (
                "Current interface: Ostranauts GUI. Treat Ostranauts/companion prompts as the active operational context. "
                "If asked what GUI you are in, answer 'Ostranauts GUI'; gremlin tone is only style."
            )
        if gui_name == "Mina Mk1 GUI":
            return (
                "Current interface: Mina Mk1 GUI. Treat the desktop/work interface as the active operational context. "
                "If asked what GUI you are in, answer 'Mina Mk1 GUI'; gremlin tone is only style."
            )
        return (
            "Current interface: Unknown GUI. Use the visible client context if one is present. "
            "If asked what GUI you are in, say the GUI is unknown until the client provides a name."
        )

    def _interface_surface_line(self, request_context: Optional[Dict[str, Any]]) -> str:
        gui_name = self._interface_name(request_context)
        if gui_name == "Ostranauts GUI":
            return (
                "Visible surface: Ostranauts companion GUI with open comms, telemetry panels, buttons, status lights, and an avatar. "
                "This is not a plain text-only chat box."
            )
        if gui_name == "Mina Mk1 GUI":
            return (
                "Visible surface: Mina Mk1 GUI with buttons, glowing panels, status meters, avatar display, and a chat console. "
                "This is not a plain text-only chat box."
            )
        return (
            "Visible surface: unknown GUI until the client provides an interface name. Do not assume plain text-only chat if the client shows controls or panels."
        )

    def _maybe_reply_interface_name(self, user_input: str, request_context: Optional[Dict[str, Any]]) -> Optional[str]:
        t = " ".join(str(user_input or "").lower().split())
        if not t:
            return None

        asks_mode = (
            re.search(r"\b(what|which)\s+mode\b", t) is not None
            or re.search(r"\bwhat\s+interface\s+are\s+you\s+in\b", t) is not None
            or re.search(r"\bwhat\s+gui\b", t) is not None
            or re.search(r"\bare\s+you\s+in\s+(game|work)\s+mode\b", t) is not None
            or re.search(r"\b(which\s+gui|which\s+interface)\b", t) is not None
            or ("gremlin mode" in t and any(word in t for word in ["what", "which", "are you in", "in?"]))
        )
        if not asks_mode:
            return None

        gui_name = self._interface_name(request_context)
        if gui_name == "Ostranauts GUI":
            return "I am in the Ostranauts GUI, with telemetry panels, buttons, and an avatar."
        if gui_name == "Mina Mk1 GUI":
            return "I am in the Mina Mk1 GUI, with buttons, glowing panels, and an avatar."
        return "I do not have a confirmed GUI name from this client yet, but I should not assume plain text-only chat if the client shows controls or panels."

    def _mark_client_verified(self, request_context: Optional[Dict[str, Any]]) -> None:
        key = self._client_identity_key(request_context)
        self._verified_clients[key] = time.time() + float(self.voice_auth_ttl_seconds)

    def _is_client_recently_verified(self, request_context: Optional[Dict[str, Any]]) -> bool:
        key = self._client_identity_key(request_context)
        expires = float(self._verified_clients.get(key) or 0.0)
        return expires > time.time()

    def _is_voice_phrase_match(self, phrase: str) -> bool:
        if not self._voice_passphrase_hash:
            return False
        stored = str(self._voice_passphrase_hash or "").strip()

        # Preferred path: salted PBKDF2 record.
        if stored.startswith("pbkdf2_sha256$"):
            return self._verify_voice_phrase_hardened(phrase, stored)

        # Legacy compatibility path: unsalted SHA-256.
        is_legacy_match = self._hash_voice_phrase(phrase) == stored
        if is_legacy_match:
            # Opportunistic migration to hardened storage after a successful legacy verify.
            try:
                self._voice_passphrase_hash = self._kdf_voice_phrase(phrase)
                self._save_identity_state()
            except Exception:
                traceback.print_exc()
        return is_legacy_match

    def _extract_explicit_windows_path(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""

        quoted = re.findall(r'["\']([A-Za-z]:(?:\\|/)[^"\']+)["\']', raw)
        candidates: List[str] = [q.strip() for q in quoted if q.strip()]

        bare_matches = re.findall(r'([A-Za-z]:(?:\\|/)[^\r\n]+)', raw)
        for match in bare_matches:
            candidate = str(match or "").strip().rstrip('.,;:!?"\'')
            if candidate:
                candidates.append(candidate)

        for candidate in candidates:
            normalized = normalize_tool_path(candidate)
            if os.path.exists(normalized):
                return normalized

            trimmed = normalized
            while trimmed and not os.path.exists(trimmed):
                parent = os.path.dirname(trimmed.rstrip("\\/"))
                if not parent or parent == trimmed:
                    break
                trimmed = parent
            if trimmed and os.path.exists(trimmed):
                return trimmed

        return ""

    def _is_recursive_directory_read_request(self, raw_text: str, lowered: str) -> bool:
        if not raw_text:
            return False
        if re.search(r'[A-Za-z]:(?:\\|/)', raw_text) is None:
            return False

        has_read_verb = re.search(r'\b(read|inspect|analy[sz]e|describe|summari[sz]e|explain)\b', lowered) is not None
        recursive_scope = (
            re.search(r'\b(all\s+files|each\s+file|every\s+file|entire\s+tree|directory\s+tree|folder\s+tree|recursive(?:ly)?|recurse|all\s+sub\s*directories|all\s+subfolders?|deep\s+scan|full\s+scan)\b', lowered) is not None
        )
        return bool(has_read_verb and recursive_scope)

    def _extract_directory_inspection_filter(self, raw_text: str) -> str:
        lowered = str(raw_text or "").lower()
        if re.search(r'\bonly\s+(?:the\s+)?(?:config|configs|configuration|configurations|settings)\b', lowered):
            return "config"
        if re.search(r'\bonly\s+(?:the\s+)?(?:logs|log files|crash logs|diagnostics?)\b', lowered):
            return "logs"
        if re.search(r'\bonly\s+(?:the\s+)?(?:mods|mod files|server files|server settings|server configs?)\b', lowered):
            return "server_mod"
        if re.search(r'\bonly\s+(?:the\s+)?(?:scripts|script files|batch files)\b', lowered):
            return "scripts"
        if re.search(r'\bonly\s+(?:the\s+)?(?:media|images|audio|video)\b', lowered):
            return "media"
        return "all"

    def _is_text_like_file_for_inspection(self, path: str) -> bool:
        return is_probably_text_path(path)

    def _describe_inspected_file(self, path: str, sample: str = "") -> str:
        base = os.path.basename(path)
        ext = os.path.splitext(base)[1].lower()
        lowered = sample.lower()

        if base.lower() == "steam_appid.txt":
            digits = re.findall(r'\d+', sample)
            if digits:
                return f"Steam application ID marker. The value {digits[0]} tells Steam which game the folder belongs to."
            return "Steam application ID marker used by Steam/game launch flow."
        if ext in {".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml", ".properties"}:
            return "Configuration or metadata file. Its contents can be used to inspect settings, flags, paths, or feature toggles."
        if ext in {".log", ".txt"}:
            if "error" in lowered or "warning" in lowered:
                return "Log or plain-text file with diagnostic signals. Useful for troubleshooting errors, warnings, and runtime behavior."
            return "Plain-text or log file. I can extract identifiers, settings, notes, or diagnostics from it."
        if ext in {".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".lua"}:
            return "Script or source file. I can explain behavior, entry points, configuration use, and potential modifications."
        if ext in {".exe"}:
            return "Windows executable binary. I can identify it by name/location, but not semantically read its compiled logic without separate reverse-engineering."
        if ext in {".dll"}:
            return "Windows library binary. It is a compiled dependency rather than directly readable text."
        if ext in {".pak", ".sig", ".bin", ".dat", ".db", ".uasset", ".umap"}:
            return "Game asset or packaged binary data. It is not meaningfully readable as plain text; analysis would require format-specific tooling."
        return "File identified by path/type. If it is text-based I can read and explain it directly; if binary, I can classify but not semantically decode it as plain text."

    def _directory_inspection_priority(self, root: str, path: str) -> int:
        rel = os.path.relpath(path, root).replace("/", "\\").lower()
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1].lower()

        score = 0

        if ext in {".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml", ".properties"}:
            score += 120
        elif ext in {".log", ".txt"}:
            score += 80
        elif ext in {".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".lua"}:
            score += 70

        if any(token in rel for token in ["server", "dedicated", "mod", "mods", "battleye", "saved", "config", "settings", "logs", "crash"]):
            score += 90

        if any(token in rel for token in ["eula\\", "licenses", "license", "privacy", "text\\", "localization", "translations"]):
            score -= 140

        if base in {"engine.ini", "game.ini", "gameusersettings.ini", "servermodlist.txt", "steam_appid.txt", "belauncher.ini", "belaunchersteam.ini", "beclient_x64.cfg", "beserver_x64.cfg"}:
            score += 200

        if ext in {".txt", ".ini"} and any(part in rel for part in ["eula\\", "text\\"]):
            score -= 80

        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0

        if 0 < size <= 16 * 1024:
            score += 20
        elif size > 256 * 1024:
            score -= 40

        return score

    def _directory_inspection_bucket(self, root: str, path: str) -> str:
        rel = os.path.relpath(path, root).replace("/", "\\").lower()
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1].lower()

        if ext in {".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml", ".properties"}:
            return "Config"
        if any(token in rel for token in ["server", "dedicated", "mod", "mods"]):
            return "Server/Mod"
        if ext in {".log", ".txt"} or "crash" in rel or "logs" in rel:
            return "Logs/Text"
        if ext in {".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".lua"}:
            return "Scripts"
        if base == "steam_appid.txt":
            return "Metadata"
        return "Other"

    def _matches_directory_inspection_filter(self, root: str, path: str, mode: str) -> bool:
        eff = str(mode or "all").strip().lower()
        if eff == "all":
            return True

        rel = os.path.relpath(path, root).replace("/", "\\").lower()
        ext = os.path.splitext(path)[1].lower()

        if eff == "config":
            return ext in {".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml", ".properties", ".toml"}
        if eff == "logs":
            return ext in {".log", ".txt", ".json"} and any(token in rel for token in ["log", "logs", "crash", "diagnostic", "report"])
        if eff == "server_mod":
            return any(token in rel for token in ["server", "mod", "mods", "dedicated"]) or os.path.basename(rel) in {"servermodlist.txt", "serversettings.ini", "modrestartdata.json", "modcontrollercache.json"}
        if eff == "scripts":
            return ext in {".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".lua", ".sh"}
        if eff == "media":
            return ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".wav", ".ogg", ".mp4", ".avi", ".mkv", ".mov", ".bk2"}
        return True

    def _inspect_directory_tree(self, path: str, mode: str = "all") -> Dict[str, Any]:
        root = normalize_tool_path(path)
        if not root:
            return {"ok": False, "result": None, "error": "no_path_provided"}
        if not os.path.isdir(root):
            return {"ok": False, "result": None, "error": f"directory_not_found: {root}"}

        max_files = 4000
        max_text_files = 48
        max_sample_chars = 2400
        max_file_size = 256 * 1024

        total_dirs = 0
        total_files = 0
        ext_counts: Dict[str, int] = {}
        text_candidates: List[str] = []
        inspected_text: List[Dict[str, Any]] = []
        binary_samples: List[str] = []
        truncated_walk = False

        try:
            for walk_root, dirs, files in os.walk(root):
                total_dirs += len(dirs)
                for name in files:
                    total_files += 1
                    if total_files > max_files:
                        truncated_walk = True
                        break

                    fpath = os.path.join(walk_root, name)
                    ext = os.path.splitext(name)[1].lower() or "[no extension]"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1

                    if self._matches_directory_inspection_filter(root, fpath, mode) and self._is_text_like_file_for_inspection(fpath):
                        text_candidates.append(fpath)
                    elif self._matches_directory_inspection_filter(root, fpath, mode) and len(binary_samples) < 12:
                        binary_samples.append(fpath)

                if truncated_walk:
                    break
        except Exception as exc:
            return {"ok": False, "result": None, "error": str(exc)}

        ranked_candidates = sorted(
            text_candidates,
            key=lambda candidate: (
                -self._directory_inspection_priority(root, candidate),
                os.path.relpath(candidate, root).lower(),
            ),
        )

        basename_counts: Dict[str, int] = {}
        family_counts: Dict[str, int] = {}

        for fpath in ranked_candidates:
            if len(inspected_text) >= max_text_files:
                break

            rel_low = os.path.relpath(fpath, root).replace("/", "\\").lower()
            base_low = os.path.basename(rel_low)
            family_key = ""
            if "saved\\config\\crashreportclient\\" in rel_low:
                family_key = "crashreportclient"
            elif "\\battleye\\eula\\" in rel_low:
                family_key = "battleye_eula"
            elif "\\battleye\\text\\" in rel_low:
                family_key = "battleye_text"

            if basename_counts.get(base_low, 0) >= 3:
                continue
            if family_key and family_counts.get(family_key, 0) >= 2:
                continue

            try:
                size = os.path.getsize(fpath)
            except Exception:
                size = 0
            if size > max_file_size:
                inspected_text.append({
                    "path": fpath,
                    "size": size,
                    "description": "Text-like file skipped for full read because it exceeds the safe inspection size cap.",
                    "sample": "",
                    "bucket": self._directory_inspection_bucket(root, fpath),
                })
                basename_counts[base_low] = basename_counts.get(base_low, 0) + 1
                if family_key:
                    family_counts[family_key] = family_counts.get(family_key, 0) + 1
                continue
            try:
                inspected = inspect_file(fpath, max_chars=max_sample_chars)
            except Exception as exc:
                inspected_text.append({
                    "path": fpath,
                    "size": size,
                    "description": f"Readable-looking file, but open/read failed: {exc}",
                    "sample": "",
                    "bucket": self._directory_inspection_bucket(root, fpath),
                })
                basename_counts[base_low] = basename_counts.get(base_low, 0) + 1
                if family_key:
                    family_counts[family_key] = family_counts.get(family_key, 0) + 1
                continue

            if not inspected.get("ok"):
                inspected_text.append({
                    "path": fpath,
                    "size": size,
                    "description": f"Readable-looking file, but open/read failed: {inspected.get('error')}",
                    "sample": "",
                    "bucket": self._directory_inspection_bucket(root, fpath),
                })
                basename_counts[base_low] = basename_counts.get(base_low, 0) + 1
                if family_key:
                    family_counts[family_key] = family_counts.get(family_key, 0) + 1
                continue

            sample = str(((inspected.get("result") or {}).get("content")) or "")

            inspected_text.append({
                "path": fpath,
                "size": size,
                "description": self._describe_inspected_file(fpath, sample),
                "sample": sample.strip(),
                "bucket": self._directory_inspection_bucket(root, fpath),
            })
            basename_counts[base_low] = basename_counts.get(base_low, 0) + 1
            if family_key:
                family_counts[family_key] = family_counts.get(family_key, 0) + 1

        top_exts = sorted(ext_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        lines: List[str] = []
        lines.append(f"Scanned: {root}")
        if str(mode or "all") != "all":
            lines.append(f"Filter: {mode}")
        lines.append(f"Found {total_files} files across {total_dirs} subdirectories.")
        if truncated_walk:
            lines.append(f"Traversal capped at {max_files} files to keep the inspection grounded and responsive.")

        if top_exts:
            ext_text = ", ".join([f"{ext}={count}" for ext, count in top_exts])
            lines.append(f"Most common file types: {ext_text}")

        if inspected_text:
            bucket_order = ["Config", "Server/Mod", "Logs/Text", "Scripts", "Metadata", "Other"]
            bucketed: Dict[str, List[Dict[str, Any]]] = {name: [] for name in bucket_order}
            for item in inspected_text:
                bucket = str(item.get("bucket") or "Other")
                bucketed.setdefault(bucket, []).append(item)

            lines.append("")
            lines.append("Readable files I actually inspected, prioritized by usefulness:")
            for bucket_name in bucket_order:
                bucket_items = bucketed.get(bucket_name, [])
                if not bucket_items:
                    continue
                lines.append("")
                lines.append(f"{bucket_name}:")
                for item in bucket_items:
                    rel = os.path.relpath(str(item.get("path") or root), root)
                    size = int(item.get("size") or 0)
                    desc = str(item.get("description") or "File inspected.")
                    lines.append(f"- {rel} ({size} bytes): {desc}")

                    sample = str(item.get("sample") or "").strip()
                    if sample:
                        snippet = re.sub(r'\s+', ' ', sample[:220]).strip()
                        if snippet:
                            lines.append(f"  Sample: {snippet}")

        if binary_samples:
            lines.append("")
            lines.append("Representative binary or packaged files:")
            for sample_path in binary_samples[:8]:
                rel = os.path.relpath(sample_path, root)
                lines.append(f"- {rel}: {self._describe_inspected_file(sample_path, '')}")

        if not inspected_text:
            lines.append("")
            lines.append("I did not find readable text/config/script files in the inspected slice. This tree appears to be mostly binaries/assets, which I can classify by type but not honestly describe line-by-line as plain text.")

        return {
            "ok": True,
            "result": {
                "path": root,
                "report": "\n".join(lines).strip(),
                "total_files": total_files,
                "total_dirs": total_dirs,
                "inspected_text_files": len(inspected_text),
                "truncated": truncated_walk,
            },
            "error": None,
        }

    def _is_high_risk_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> bool:
        tname = str(tool_name or "")
        if tname in {"__mina_shutdown__", "__mina_shutdown_status__", "__git_push__", "__git_snapshot__"}:
            return True

        if tname in self._write_auth_tool_names():
            roots = [normalize_tool_path(root) for root in full_access_roots()]
            targets: List[str] = []
            if tname == "file_move":
                targets = [
                    normalize_tool_path(str(tool_args.get("src") or "")),
                    normalize_tool_path(str(tool_args.get("dst") or "")),
                ]
            elif tname == "safe_copy":
                targets = [normalize_tool_path(str(tool_args.get("dst") or ""))]
            else:
                targets = [normalize_tool_path(str(tool_args.get("path") or ""))]

            for target in [p for p in targets if p]:
                try:
                    if not any(
                        os.path.commonpath([os.path.normcase(target), os.path.normcase(root)]) == os.path.normcase(root)
                        for root in roots if root
                    ):
                        return True
                except Exception:
                    return True
        return False

    def _identity_gate_decision(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        request_context: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not bool(getattr(self, "identity_guard_enabled", True)):
            return None
        if not self._is_high_risk_tool(tool_name, tool_args):
            return None
        if self._is_trusted_client(request_context):
            return None
        if self._is_client_recently_verified(request_context):
            return None
        if not self._voice_passphrase_hash:
            return (
                "Identity check required for sensitive actions. "
                "Please enroll a voice passphrase first: set voice passphrase to <your phrase>."
            )
        hint = self._voice_passphrase_hint or "your passphrase"
        return (
            "Identity check required for this action. "
            f"Say: verify voice {hint}"
        )

    def _load_runtime_state(self) -> None:
        path = self._runtime_state_path()
        if not os.path.isfile(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            if not isinstance(payload, dict):
                return

            last_user_activity = str(payload.get("last_user_activity_at") or "").strip()
            if not last_user_activity:
                return

            last_dt = datetime.fromisoformat(last_user_activity)
            now_dt = datetime.now()
            age_seconds = max(0.0, (now_dt - last_dt).total_seconds())

            now_mono = time.monotonic()
            self._last_user_activity_at = last_user_activity
            self._last_user_activity_monotonic = max(0.0, now_mono - age_seconds)

            last_shutdown = str(payload.get("last_shutdown_at") or "").strip()
            if last_shutdown:
                self._last_shutdown_at = last_shutdown
                shutdown_dt = datetime.fromisoformat(last_shutdown)
                seconds_since_shutdown = max(0.0, (now_dt - shutdown_dt).total_seconds())
                self._seconds_since_last_shutdown = seconds_since_shutdown
                if seconds_since_shutdown >= float(self.away_nag_threshold_seconds):
                    hours = max(0.1, round(seconds_since_shutdown / 3600.0, 1))
                    self._away_nag_prompt_pending = True
                    self._away_nag_prompt_text = (
                        f"Reconnection check-in: user has been away about {hours} hours since last shutdown. "
                        "Greet them and briefly ask how they are doing before diving into tasks."
                    )
        except Exception:
            traceback.print_exc()

    def _save_web_search_mode_state(self) -> None:
        try:
            payload = {
                "web_search_mode": self.web_search_mode,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(self._search_mode_state_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _load_web_search_mode_state(self) -> None:
        path = self._search_mode_state_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return
            mode = str(payload.get("web_search_mode") or "").strip().lower()
            if mode in {"balanced", "strict", "wide"}:
                self.web_search_mode = mode
        except Exception:
            traceback.print_exc()

    def _set_web_search_mode(self, mode: str) -> Dict[str, Any]:
        requested = str(mode or "").strip().lower()
        if requested not in {"balanced", "strict", "wide"}:
            return {
                "ok": False,
                "result": {
                    "requested": requested,
                    "allowed": ["balanced", "strict", "wide"],
                    "current_mode": self.web_search_mode,
                },
                "error": "invalid_search_mode",
            }

        self.web_search_mode = requested
        self._save_web_search_mode_state()
        return {
            "ok": True,
            "result": {
                "mode": self.web_search_mode,
            },
            "error": None,
        }

    def _get_web_search_mode(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "result": {
                "mode": self.web_search_mode,
                "allowed": ["balanced", "strict", "wide"],
            },
            "error": None,
        }

    def _normalize_user_intent_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""

        out = self._correct_contextual_transcript(raw)
        out = re.sub(r'^\s*(?:hey\s+)?(?:you|mina)[,\s:;-]+', '', out, flags=re.IGNORECASE).strip()
        out = re.sub(
            r'^\s*(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}[,\s:;-]+(?=(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\b|in\s+\d+\s*(?:seconds?|minutes?|hours?)\b)',
            '',
            out,
            flags=re.IGNORECASE,
        ).strip()
        out = re.sub(r'^\s*(?:voice|audio|mic)\s*[:,-]?\s*', '', out, flags=re.IGNORECASE).strip()

        out = re.sub(
            r'^\s*(?:please\s+)?(?:can|could|would|will)\s+you\s+',
            '',
            out,
            flags=re.IGNORECASE,
        ).strip()
        out = re.sub(
            r'^\s*(?:please\s+)?i\s+(?:need|want|would\s+like)\s+you\s+to\s+',
            '',
            out,
            flags=re.IGNORECASE,
        ).strip()
        out = re.sub(
            r'^\s*(?:please\s+)?do\s+me\s+a\s+favo(?:u)?r\s+and\s+',
            '',
            out,
            flags=re.IGNORECASE,
        ).strip()
        out = re.sub(
            r'\s+(?:please|thanks|thank\s+you)\s*[.!?]*$',
            '',
            out,
            flags=re.IGNORECASE,
        ).strip()

        if out.lower().startswith("please "):
            out = out[7:].strip()
        return out

    def _correct_contextual_transcript(self, text: str) -> str:
        out = str(text or "")
        out = re.sub(r"\b(?:ostanauts?|ostronauts?|ostranaughts?)\b", "Ostranauts", out, flags=re.IGNORECASE)
        if re.search(r'\bastronauts\b', out, re.IGNORECASE) and re.search(
            r'\b(?:steam|games?|mods?|modding|vortex|library)\b',
            out,
            re.IGNORECASE,
        ):
            return re.sub(r'\bastronauts\b', 'Ostranauts', out, flags=re.IGNORECASE)
        return out

    def _is_tool_self_test_request(self, text: str) -> bool:
        # Disabled by default: self-test should not be chat-triggered unless
        # explicitly re-enabled via config.
        allow_chat_self_test = False
        try:
            allow_chat_self_test = bool(self.config.get("curiosity", "allow_chat_tool_self_test", False))
        except Exception:
            allow_chat_self_test = False

        if not allow_chat_self_test:
            return False

        t = str(text or "").strip().lower()
        if not t:
            return False
        return (
            re.search(r"\b(?:self[\s-]?test|smoke\s*test|tool\s*test|tool\s*smoke)\b", t) is not None
            or (
                re.search(r"\btest\b", t) is not None
                and re.search(r"\btools?\b", t) is not None
            )
        )

    def _tool_plan_confidence_threshold(self) -> float:
        mode = str(getattr(self, "action_mode_strictness", "balanced") or "balanced").strip().lower()
        if mode == "strict":
            return 0.70
        if mode == "relaxed":
            return 0.50
        return 0.60

    def _truth_first_system_rules(self) -> str:
        tone = "Keep Mina tone light but concise." if bool(getattr(self, "action_mode_light_tone", True)) else "Use direct, minimal tone."
        return (
            "Truth-first action mode is active. "
            "Mina stays Mina in every interface and wrapper; never swap her personality for a bot persona or wrapper script. "
            "Never claim external actions (texts, calls, file reads, web checks, Spotify state, system commands) unless verified by tool output in this turn. "
            "If not verified, say what you can verify next. "
            "Do not invent file contents, directory listings, or execution results. "
            + tone
        )

    def _tool_usage_decision_rules(self) -> str:
        return (
            "Tool decision rules: "
            "Preserve Mina's personality first; wrappers only add context, not a new persona. "
            "Treat this as ACTION when user asks to run, create, write, edit, read, list, search, fetch, start, stop, or check status, "
            "or provides paths/URLs/commands. "
            "Treat this as CHAT when user asks opinion, explanation, brainstorming, or casual conversation without an execution target. "
            "If ACTION is likely but target/argument is missing, ask clarifying questions as needed until required target/arguments are unambiguous, instead of chatting. "
            "Tool call format must be exactly: <|tool_call>call:tool_name{key: value}<tool_call|>."
        )

    def _is_action_request_like(self, text: str) -> bool:
        src = str(text or "").strip()
        if not src:
            return False

        t = src.lower()

        if re.search(r"https?://", src, re.IGNORECASE):
            return True
        if re.search(r"[A-Za-z]:\\|[.]{1,2}[\\/]", src):
            return True
        if re.search(r"\b(?:\.ps1|\.py|\.cmd|\.bat|\.json|\.txt|\.md|\.csv)\b", t):
            return True

        imperative = re.search(
            r"^(?:please\s+)?(?:run|execute|create|make|write|save|read|open|show|list|delete|remove|move|rename|copy|append|search|fetch|check|start|stop|restart|set|schedule|play|pause|resume|pull|push|commit)\b",
            t,
        ) is not None
        asks_action = re.search(
            r"\b(?:can|could|would|will)\s+you\s+(?:run|execute|create|make|write|read|open|show|list|delete|remove|move|rename|search|fetch|check|start|stop|restart|set|schedule|play|pause|resume)\b",
            t,
        ) is not None
        object_signal = re.search(
            r"\b(file|folder|directory|path|script|command|powershell|python|repo|git|url|website|web|memory|alarm|timer|spotify|song|track|playlist|server|api|database|db|status|tool)\b",
            t,
        ) is not None

        return bool((imperative or asks_action) and object_signal)

    def _is_operational_incident_report(self, text: str) -> bool:
        src = str(text or "").strip()
        if not src:
            return False

        t = src.lower()

        # Keep this narrow so normal chat does not get hijacked.
        target_signal = re.search(
            r"\b(mina|mk1|assistant|api|server|service|you)\b",
            t,
        ) is not None
        failure_signal = re.search(
            r"\b(crash(?:ed|es|ing)?|offline|down|unreachable|not\s+responding|could(?:n't| not)\s+reach|unable\s+to\s+reach|stopped\s+working|went\s+down)\b",
            t,
        ) is not None
        report_style = (
            re.search(r"\b(i|we)\b", t) is not None
            or re.search(r"\b(can't|cannot|couldn't|unable)\b", t) is not None
            or re.search(r"\ball\s+day\b", t) is not None
        )

        if not (target_signal and failure_signal and report_style):
            return False

        # If user is explicitly asking for pure small talk, do not force tools.
        if re.search(r"\b(joke|poem|story|opinion|chat)\b", t):
            return False

        return True

    def _is_retryable_tool_failure(self, result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict) or bool(result.get("ok", True)):
            return False

        err = str(result.get("error") or "").strip().lower()
        payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
        stderr = str(payload.get("stderr") or "").strip().lower()

        retryable_tokens = [
            "unknown_error",
            "no_command_provided",
            "invalid_tool_result",
            "timeout_after_",
            "exception:",
            "commandnotfoundexception",
        ]
        return any(tok in err for tok in retryable_tokens) or any(tok in stderr for tok in retryable_tokens)

    def _recover_failed_tool_once(
        self,
        user_input: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any], bool]:
        if not self._is_retryable_tool_failure(result):
            return tool_name, tool_args, result, False

        current_tool = str(tool_name or "")
        current_args = dict(tool_args or {})

        if current_tool == "ps_run":
            try:
                from tools.ps_run import _normalize_command as _normalize_ps_command

                normalized = _normalize_ps_command(str(user_input or ""))
                if normalized:
                    existing = str(current_args.get("script") or current_args.get("command") or "").strip()
                    if normalized.strip() and normalized.strip() != existing.strip():
                        retry_args = dict(current_args)
                        retry_args["script"] = normalized
                        retry_args.pop("command", None)
                        retry_result = self.tools.run("ps_run", retry_args)
                        if isinstance(retry_result, dict) and bool(retry_result.get("ok", False)):
                            return "ps_run", retry_args, retry_result, True
            except Exception:
                traceback.print_exc()

        planned = self._plan_tool_action(user_input)
        if planned is not None:
            planned_tool, planned_args = planned
            if planned_tool == "powershell" and "ps_run" in self._tool_registry():
                cmd = str(planned_args.get("command") or planned_args.get("script") or "").strip()
                planned_tool = "ps_run"
                planned_args = {"script": cmd} if cmd else {}

            if planned_tool in self._tool_registry():
                if planned_tool != current_tool or dict(planned_args or {}) != current_args:
                    retry_result = self.tools.run(planned_tool, planned_args)
                    if isinstance(retry_result, dict) and bool(retry_result.get("ok", False)):
                        return planned_tool, dict(planned_args or {}), retry_result, True

        return tool_name, tool_args, result, False

    def _is_learnable_intent_phrase(self, text: str) -> bool:
        src = self._normalize_user_intent_text(text)
        if not src:
            return False

        t = src.lower().strip()
        words = [w for w in re.split(r"\s+", t) if w]
        if len(words) < 2:
            return False

        if self._is_action_request_like(src):
            return True

        imperative = re.search(
            r"^(?:please\s+)?(?:run|execute|create|make|write|save|read|open|show|list|delete|remove|move|rename|copy|append|search|fetch|check|start|stop|restart|set|schedule|play|pause|resume|pull|push|commit)\b",
            t,
        ) is not None
        if imperative and len(words) >= 2:
            return True

        info_query = re.search(r"^(?:what|who|where|when|why|how|which)\b", t) is not None
        info_subject = re.search(
            r"\b(time|date|mode|status|memory|tool|tools|cpu|os|system|project|workspace|song|music|game|weather|news)\b",
            t,
        ) is not None
        if info_query and info_subject:
            return True

        return False

    def _alias_replay_confidence(self, phrase: str, tool: str, args: Optional[Dict[str, Any]] = None) -> float:
        text = self._normalize_user_intent_text(phrase)
        t = text.lower().strip()
        tool_name = str(tool or "").strip()

        if not text or not tool_name:
            return 0.0

        if tool_name.startswith("__"):
            return 0.0

        if self._is_action_request_like(text):
            return 0.85

        info_query = re.search(r"^(?:what|who|where|when|why|how|which)\b", t) is not None
        if info_query and tool_name in {"memory_read", "ps_run", "web_search", "web_fetch", "github_repo", "dir_list", "file_read"}:
            return 0.75

        if tool_name == "spotify_link":
            if self._is_explicit_spotify_command(text):
                return 0.9
            music_signal = re.search(
                r"\b(music|song|songs|track|tracks|playlist|playlists|spotify|favorites?|favourites?)\b",
                text,
                re.IGNORECASE,
            ) is not None
            return 0.75 if music_signal else 0.45

        if re.search(r"^(?:yes|yeah|yep|ok|okay|thanks|thank\s+you)\b", t):
            return 0.2

        return 0.6

    def _intent_alias_record(self, phrase: str, tool: str, args: Optional[Dict[str, Any]] = None) -> str:
        payload = {
            "phrase": str(phrase or "").strip().lower(),
            "tool": str(tool or "").strip(),
            "args": args if isinstance(args, dict) else {},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        return "intent_alias::" + json.dumps(payload, sort_keys=True, ensure_ascii=True)

    def _parse_intent_alias_record(self, text: str) -> Optional[Dict[str, Any]]:
        src = str(text or "").strip()
        if not src.startswith("intent_alias::"):
            return None
        raw = src[len("intent_alias::"):].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        phrase = str(payload.get("phrase") or "").strip().lower()
        tool = str(payload.get("tool") or "").strip()
        args = payload.get("args", {})
        if not phrase or not tool:
            return None
        if not isinstance(args, dict):
            args = {}
        return {
            "phrase": phrase,
            "tool": tool,
            "args": args,
        }

    def _get_intent_alias_map(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        try:
            recent = self.memory.recent_memories(
                top_k=1000,
                include_kinds=["procedure", "fact"],
                include_tags=["intent_alias"],
            )
            for item in recent or []:
                parsed = self._parse_intent_alias_record(str(item.get("text") or ""))
                if not parsed:
                    continue
                phrase = str(parsed.get("phrase") or "")
                if phrase and phrase not in out:
                    out[phrase] = {
                        "tool": str(parsed.get("tool") or ""),
                        "args": parsed.get("args", {}) if isinstance(parsed.get("args", {}), dict) else {},
                    }
        except Exception:
            traceback.print_exc()
        return out

    def _resolve_intent_alias(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        phrase = self._normalize_user_intent_text(text).lower().strip()
        if not phrase:
            return None
        if not self._is_learnable_intent_phrase(phrase):
            return None

        alias_map = self._intent_alias_cache if isinstance(getattr(self, "_intent_alias_cache", None), dict) else {}
        row = alias_map.get(phrase)

        if not isinstance(row, dict):
            row = self._resolve_fuzzy_intent_alias_row(phrase, alias_map)

        if not isinstance(row, dict):
            memory_aliases = self._alias_candidates_from_memory(phrase)
            if memory_aliases:
                if not isinstance(getattr(self, "_intent_alias_cache", None), dict):
                    self._intent_alias_cache = {}
                for alias_phrase, alias_row in memory_aliases.items():
                    if alias_phrase not in self._intent_alias_cache:
                        self._intent_alias_cache[alias_phrase] = alias_row

                row = memory_aliases.get(phrase)
                if not isinstance(row, dict):
                    row = self._resolve_fuzzy_intent_alias_row(phrase, memory_aliases)

        if not isinstance(row, dict):
            return None

        tool = str(row.get("tool") or "").strip()
        args = row.get("args", {}) if isinstance(row.get("args", {}), dict) else {}
        if not tool:
            return None
        if tool.startswith("__"):
            return None
        if tool == "spotify_link" and self._is_explicit_spotify_command(phrase):
            return None

        available = self._tool_registry()
        if tool in available:
            return tool, dict(args)
        return None

    def _alias_candidates_from_memory(self, phrase: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not phrase:
            return out

        mem = getattr(self, "memory", None)
        if mem is None:
            return out

        rows: List[Dict[str, Any]] = []

        try:
            if hasattr(mem, "search"):
                sem_rows = mem.search(
                    phrase,
                    top_k=24,
                    include_kinds=["procedure", "fact"],
                    include_tags=["intent_alias"],
                )
                if isinstance(sem_rows, list):
                    rows.extend([r for r in sem_rows if isinstance(r, dict)])
        except Exception:
            traceback.print_exc()

        try:
            if hasattr(mem, "recent_memories"):
                recent_rows = mem.recent_memories(
                    top_k=200,
                    include_kinds=["procedure", "fact"],
                    include_tags=["intent_alias"],
                )
                if isinstance(recent_rows, list):
                    rows.extend([r for r in recent_rows if isinstance(r, dict)])
        except Exception:
            traceback.print_exc()

        for item in rows:
            parsed = self._parse_intent_alias_record(str(item.get("text") or ""))
            if not parsed:
                continue
            alias_phrase = str(parsed.get("phrase") or "").strip().lower()
            if not alias_phrase or alias_phrase in out:
                continue
            out[alias_phrase] = {
                "tool": str(parsed.get("tool") or "").strip(),
                "args": parsed.get("args", {}) if isinstance(parsed.get("args", {}), dict) else {},
            }

        return out

    def _intent_alias_tokens(self, phrase: str) -> List[str]:
        normalized = re.sub(r"[^a-z0-9\s]", " ", str(phrase or "").lower())
        tokens = [tok for tok in normalized.split() if tok]
        stop = {
            "the", "a", "an", "to", "for", "with", "and", "or", "my", "me",
            "please", "now", "just", "can", "could", "would", "will", "you",
        }
        return [tok for tok in tokens if tok not in stop]

    def _intent_alias_similarity(self, a: str, b: str) -> float:
        left = str(a or "").strip().lower()
        right = str(b or "").strip().lower()
        if not left or not right:
            return 0.0

        seq_ratio = difflib.SequenceMatcher(None, left, right).ratio()
        left_tokens = self._intent_alias_tokens(left)
        right_tokens = self._intent_alias_tokens(right)
        if not left_tokens or not right_tokens:
            return seq_ratio

        left_set = set(left_tokens)
        right_set = set(right_tokens)
        overlap = len(left_set.intersection(right_set)) / float(max(len(left_set), len(right_set), 1))

        first_token_bonus = 0.08 if left_tokens[0] == right_tokens[0] else 0.0
        score = (0.68 * seq_ratio) + (0.32 * overlap) + first_token_bonus
        return max(0.0, min(1.0, score))

    def _resolve_fuzzy_intent_alias_row(
        self,
        phrase: str,
        alias_map: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not phrase or not isinstance(alias_map, dict):
            return None

        phrase_tokens = self._intent_alias_tokens(phrase)
        if len(phrase_tokens) < 2:
            return None

        best_row: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for alias_phrase, candidate in alias_map.items():
            if not isinstance(candidate, dict):
                continue
            alias_text = str(alias_phrase or "").strip().lower()
            if not alias_text:
                continue

            alias_tokens = self._intent_alias_tokens(alias_text)
            if len(alias_tokens) < 2:
                continue

            shared = len(set(phrase_tokens).intersection(set(alias_tokens)))
            if shared < 2:
                continue

            score = self._intent_alias_similarity(phrase, alias_text)
            if score < 0.86:
                continue

            if score > best_score:
                best_score = score
                best_row = candidate

        return best_row

    def _play_song_shorthand_record(self, trigger_phrase: str, track_query: str) -> str:
        payload = {
            "phrase": str(trigger_phrase or "").strip().lower(),
            "track_query": str(track_query or "").strip(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        return "play_song_shorthand::" + json.dumps(payload, sort_keys=True, ensure_ascii=True)

    def _parse_play_song_shorthand_record(self, text: str) -> Optional[Dict[str, str]]:
        src = str(text or "").strip()
        if not src.startswith("play_song_shorthand::"):
            return None

        raw = src[len("play_song_shorthand::"):].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None

        phrase = str(payload.get("phrase") or "").strip().lower()
        track_query = str(payload.get("track_query") or "").strip()
        if not phrase or not track_query:
            return None

        return {
            "phrase": phrase,
            "track_query": track_query,
        }

    def _get_play_song_shorthand_map(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            recent = self.memory.recent_memories(
                top_k=1000,
                include_kinds=["procedure", "fact"],
                include_tags=["play_song_shorthand"],
            )
            for item in recent or []:
                parsed = self._parse_play_song_shorthand_record(str(item.get("text") or ""))
                if not parsed:
                    continue
                phrase = str(parsed.get("phrase") or "")
                track_query = str(parsed.get("track_query") or "")
                if phrase and track_query and phrase not in out:
                    out[phrase] = track_query
        except Exception:
            traceback.print_exc()
        return out

    def _resolve_play_song_shorthand(self, text: str) -> Optional[str]:
        phrase = self._normalize_user_intent_text(text).lower().strip()
        if not phrase:
            return None

        cache = self._play_song_shorthand_cache if isinstance(getattr(self, "_play_song_shorthand_cache", None), dict) else {}
        track_query = str(cache.get(phrase) or "").strip()
        return track_query or None

    def _extract_play_song_shorthand_rule(self, raw_text: str) -> Optional[Dict[str, str]]:
        src = str(raw_text or "").strip()
        if not src:
            return None

        m = re.search(
            r"\b(?:if|when)\s+i\s+say\s+[\"']?(.+?)[\"']?\s*(?:,)?\s*(?:that\s+means|it\s+means|then)\b",
            src,
            re.IGNORECASE,
        )
        if not m:
            return None

        trigger_phrase = self._normalize_user_intent_text(str(m.group(1) or "")).lower().strip(" .!?,;:\"'")
        if not trigger_phrase.startswith("play "):
            return None

        track_query = re.sub(r"^play\s+", "", trigger_phrase, flags=re.IGNORECASE).strip()
        if not track_query:
            return None

        return {
            "phrase": trigger_phrase,
            "track_query": track_query,
        }

    def _is_explicit_spotify_command(self, text: str) -> bool:
        phrase = self._normalize_user_intent_text(text).lower().strip()
        action = re.search(r"\b(add|create|make|list|show|get|play|pause|stop|resume|shuffle|search|find)\b", phrase)
        subject = re.search(
            r"\b(spotify|spotiy|sportify|spotfiy|spotfy|spotifi|music|songs?|tracks?|play\s*lists?|albums?|artists?|liked|favorites?|favourites?)\b",
            phrase,
        )
        return action is not None and subject is not None

    def _maybe_learn_intent_alias(
        self,
        user_input: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if not bool(getattr(self, "auto_learn_intent_aliases", True)):
            return
        if not isinstance(result, dict) or not bool(result.get("ok", True)):
            return

        blocked_tools = {
            "__clarify_intent__",
            "__confirm_external_write__",
            "__cancel_external_write__",
            "__capability_limit__",
            "__learn_play_song_shorthand__",
        }
        if tool_name in blocked_tools:
            return
        if str(tool_name or "").startswith("__"):
            return

        phrase = self._normalize_user_intent_text(user_input).lower().strip()
        if not phrase or len(phrase) < 8 or len(phrase) > 180:
            return
        if not self._is_learnable_intent_phrase(phrase):
            return
        if re.search(r"\b(?:if|when)\s+i\s+say\b", phrase, re.IGNORECASE):
            return
        if tool_name == "spotify_link" and self._is_explicit_spotify_command(phrase):
            return
        if "http://" in phrase or "https://" in phrase:
            return
        if re.search(r"[A-Za-z]:\\", phrase):
            return
        if "```" in phrase:
            return

        existing = self._intent_alias_cache.get(phrase)
        if isinstance(existing, dict) and str(existing.get("tool") or "") == tool_name:
            return

        args_to_store = dict(tool_args or {})
        if len(args_to_store) > 8:
            args_to_store = {}

        record_text = self._intent_alias_record(phrase=phrase, tool=tool_name, args=args_to_store)

        try:
            exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    record_text,
                    include_kinds=["procedure"],
                    include_tags=["intent_alias"],
                ) is not None
            if not exists:
                self.memory.add_memory(
                    record_text,
                    kind="procedure",
                    tags=["user_memory", "intent_alias"],
                )
            self._intent_alias_cache[phrase] = {
                "tool": tool_name,
                "args": args_to_store,
            }
        except Exception:
            traceback.print_exc()

    # Why: failed turns and Ostranauts play sessions should leave a compact lesson trail.
    # Guard: stores lessons as advisory notes so retries stay available and routing is unchanged.
    def _maybe_store_learning_memory(
        self,
        user_input: str,
        assistant_text: str,
        retry_guided: bool = False,
    ) -> None:
        try:
            user_mem = str(user_input or "").strip()
            assistant_mem = str(assistant_text or "").strip()
            if not user_mem and not assistant_mem:
                return

            lesson_parts: List[str] = []
            lesson_tags = ["user_memory", "learning_lesson"]

            correction_patterns = [
                r"\bthat was (?:a|an) .{2,60} request\b",
                r"\bi meant (?:to|that|a|an)\b",
                r"\byou should have (?:used|called|treated|routed)\b",
                r"\bnot .{1,40}, (?:use|do|create|run|search|read|write)\b",
            ]
            if any(re.search(pattern, user_mem, re.IGNORECASE) is not None for pattern in correction_patterns):
                lesson_parts.append(f"User correction: {self._compress_multiline(user_mem, max_lines=2, max_chars=260)}")
                lesson_tags.append("routing_correction")

            if is_failed_decision_text(assistant_mem):
                lesson_parts.append(
                    "Retry note: the prior attempt failed, so Mina should keep trying with a different approach rather than blocking the next attempt."
                )
                lesson_tags.append("failed_decision")

            if retry_guided and assistant_mem:
                lesson_parts.append(
                    f"Retry attempt: {self._compress_multiline(assistant_mem, max_lines=3, max_chars=320)}"
                )
                lesson_tags.append("retry_attempt")

            if is_ostranauts_query(user_mem) and assistant_mem:
                lesson_parts.append(
                    f"Ostranauts lesson: {self._compress_multiline(assistant_mem, max_lines=4, max_chars=360)}"
                )
                lesson_tags.append("ostranauts")

            if not lesson_parts:
                return

            lesson_text = " | ".join(lesson_parts)
            if len(lesson_text) > 1800:
                lesson_text = self._compress_multiline(lesson_text, max_lines=4, max_chars=1800)

            self.memory.add_memory(
                lesson_text,
                kind="note",
                tags=lesson_tags,
            )
        except Exception:
            traceback.print_exc()

    # Why: tests can construct MK1Core without ToolLoader, but intent routing still needs a safe lookup.
    # Guard: returns an empty registry instead of raising AttributeError on partial core objects.
    def _tool_registry(self) -> Dict[str, Any]:
        tools = getattr(self, "tools", None)
        return getattr(tools, "tools", {}) if tools is not None else {}

    def _intent_signal_profile(self, raw_text: str) -> Dict[str, bool]:
        t = str(raw_text or "").lower()
        play_signal = re.search(r"\b(play|paly|place)\b", t) is not None
        play_with_target = re.search(r"\b(?:play|paly|place)\s+.+", t) is not None
        game_signals = re.search(
            r"\b(game|steam|ostranauts?|ostanauts?|ostronauts?|ostranaughts?|interface|integrat(?:e|ion)|build|app|ui|character|npc|mod)\b",
            t,
        ) is not None
        music_signals = re.search(r"\b(song|track|play\s*list|spotify|music|album|artist)\b", t) is not None
        explicit_spotify = self._is_explicit_spotify_command(raw_text)
        ostranauts_query = is_ostranauts_query(raw_text)

        return {
            "play_signal": play_signal,
            "play_with_target": play_with_target,
            "game_signals": game_signals,
            "music_signals": music_signals,
            "explicit_spotify": explicit_spotify,
            "ostranauts_query": ostranauts_query,
        }

    def _maybe_build_ambiguity_prompt(self, raw_text: str, intent_signals: Optional[Dict[str, bool]] = None) -> Optional[str]:
        if not bool(getattr(self, "ambiguity_ask_on_medium_low", True)):
            return None

        t = str(raw_text or "").lower()
        if re.search(r"^\s*(what|who|where|when|why|how)\b", t) and not re.search(r"\b(play|game|music|spotify)\b", t):
            return None
        if any(phrase in t for phrase in [
            "what is my name",
            "what's my name",
            "who am i",
            "what time is it",
            "current time",
            "local time",
            "what date is it",
            "current date",
            "today's date",
            "todays date",
        ]):
            return None
        signals = intent_signals or self._intent_signal_profile(raw_text)
        play_signal = bool(signals.get("play_signal"))
        play_with_target = bool(signals.get("play_with_target"))
        game_signals = bool(signals.get("game_signals"))
        music_signals = bool(signals.get("music_signals"))
        spotify_available = "spotify_link" in self._tool_registry()

        casual_typo_markers = re.search(r"\b(my\s+bad|fat\s+fingers|miss\s+keys|oops|lol|haha|typo)\b", t) is not None
        if casual_typo_markers and not (game_signals or music_signals):
            return None

        # Game-targeted "play" should stay in game context and avoid music hijack.
        if play_signal and game_signals and not music_signals:
            return None

        # Explicit play requests with a concrete target should execute routing
        # directly instead of falling into generic play clarification.
        if play_signal and play_with_target and not game_signals:
            return None

        # Unqualified "play" with Spotify available is ambiguous by default.
        if play_signal and spotify_available and not game_signals and not music_signals:
            return (
                "When you say 'play', what do you want to play right now: a game, music, or something else?"
            )

        if re.search(r"\bplay\s+with\s+me\b", t) and (game_signals or not music_signals):
            return None

        return None

    def _write_auth_tool_names(self) -> set:
        return {"dir_create", "file_write", "file_append", "file_delete", "file_move", "safe_copy"}

    def _set_pending_action(self, action_key: str, payload: Dict[str, Any]) -> None:
        store = getattr(self, "_pending_actions", None)
        if store is None:
            store = PendingActionStore()
            self._pending_actions = store
        store.set(action_key, payload)

    def _get_pending_action(self, action_key: str) -> Optional[Dict[str, Any]]:
        store = getattr(self, "_pending_actions", None)
        if store is None:
            return None
        return store.get(action_key)

    def _clear_pending_action(self, action_key: str) -> None:
        store = getattr(self, "_pending_actions", None)
        if store is None:
            return
        store.clear(action_key)

    def _set_last_tool_event(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_input: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_args = dict(tool_args or {})
        safe_args.pop("auth_token", None)
        payload = {
            "tool_name": str(tool_name or "").strip(),
            "tool_args": safe_args,
            "user_input": str(user_input or "").strip(),
            "ok": bool((result or {}).get("ok", False)) if isinstance(result, dict) else None,
            "created_at": time.time(),
        }
        self._set_pending_action("last_tool_event", payload)

    def _set_context_continuation(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_input: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_args = dict(tool_args or {})
        safe_args.pop("auth_token", None)

        artifact_path = ""
        artifact_url = ""
        artifact_query = ""
        payload_result = result.get("result", {}) if isinstance(result, dict) and isinstance(result.get("result"), dict) else {}

        for key in ["path", "dst", "src"]:
            val = str(payload_result.get(key) or safe_args.get(key) or "").strip()
            if val:
                artifact_path = val
                break

        artifact_url = str(payload_result.get("url") or safe_args.get("url") or "").strip()
        artifact_query = str(payload_result.get("query") or safe_args.get("query") or "").strip()

        suggested_yes_action: Dict[str, Any] = {}
        normalized_tool = str(tool_name or "").strip()
        if normalized_tool == "file_write":
            target = str(payload_result.get("path") or safe_args.get("path") or "").strip()
            if target.lower().endswith(".py"):
                suggested_yes_action = {
                    "tool_name": "ps_run",
                    "tool_args": {
                        "script": f'python "{target}"',
                    },
                }

        continuation = {
            "tool_name": normalized_tool,
            "tool_args": safe_args,
            "user_input": str(user_input or "").strip(),
            "artifact_path": artifact_path,
            "artifact_url": artifact_url,
            "artifact_query": artifact_query,
            "suggested_yes_action": suggested_yes_action,
            "ok": bool((result or {}).get("ok", False)) if isinstance(result, dict) else None,
            "created_at": time.time(),
        }
        self._set_pending_action("context_continuation", continuation)

    def _get_context_continuation(self) -> Optional[Dict[str, Any]]:
        return self._get_pending_action("context_continuation")

    def _set_last_draft_artifact(self, target_path: str, content: str, user_input: str) -> None:
        payload = {
            "target_path": str(target_path or "").strip(),
            "content": str(content or ""),
            "user_input": str(user_input or "").strip(),
            "created_at": time.time(),
        }
        self._set_pending_action("last_draft_artifact", payload)

    def _get_last_draft_artifact(self) -> Optional[Dict[str, Any]]:
        return self._get_pending_action("last_draft_artifact")

    def _extract_code_block_from_text(self, text: str, target_path: str = "") -> str:
        raw = str(text or "")
        if not raw.strip():
            return ""

        ext = os.path.splitext(str(target_path or ""))[1].lower()
        preferred_langs = {
            ".py": {"python", "py"},
            ".ps1": {"powershell", "ps1", "pwsh"},
            ".js": {"javascript", "js"},
            ".ts": {"typescript", "ts"},
            ".json": {"json"},
            ".md": {"markdown", "md"},
            ".html": {"html"},
            ".css": {"css"},
        }.get(ext, set())

        blocks = re.findall(r"```([A-Za-z0-9_+-]*)\n([\s\S]*?)```", raw)
        if not blocks:
            return ""

        for lang, body in blocks:
            if preferred_langs and str(lang or "").strip().lower() in preferred_langs:
                return str(body or "").strip("\n")

        return str(blocks[0][1] or "").strip("\n")

    def _infer_draft_target_path(self, user_input: str, assistant_text: str) -> str:
        for source in [str(assistant_text or ""), str(user_input or "")]:
            m = re.search(r"(?:^|\b#\s*)([A-Za-z0-9_. -]+\.[A-Za-z0-9]{1,8})", source, re.IGNORECASE | re.MULTILINE)
            if m:
                return str(m.group(1) or "").strip().strip("`")
            m = re.search(r"`([^`]+\.[A-Za-z0-9]{1,8})`", source)
            if m:
                return str(m.group(1) or "").strip()
            m = re.search(r"\b(?:named|called|as)\s+([A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})\b", source, re.IGNORECASE)
            if m:
                return str(m.group(1) or "").strip()
        return ""

    def _remember_draft_artifact_from_turn(self, user_input: str, assistant_text: str) -> None:
        target_path = self._infer_draft_target_path(user_input, assistant_text)
        code = self._extract_code_block_from_text(assistant_text, target_path=target_path)
        if not target_path or not code.strip():
            return
        self._set_last_draft_artifact(target_path=target_path, content=code, user_input=user_input)
        self._remember_code_artifact_memory(
            target_path=target_path,
            content=code,
            user_input=user_input,
            source_tag="draft_artifact",
        )

    def _code_artifact_memory_record(self, target_path: str, user_input: str, content: str) -> str:
        safe_path = str(target_path or "").strip()
        safe_request = self._compress_multiline(str(user_input or ""), max_lines=2, max_chars=320)
        safe_content = str(content or "").strip()
        if len(safe_content) > 6000:
            safe_content = safe_content[:6000].rstrip() + "\n# ... truncated for memory ..."
        return (
            f"code_artifact::path={safe_path}\n"
            f"request={safe_request}\n"
            "content:\n"
            f"{safe_content}"
        )

    def _parse_code_artifact_memory_record(self, text: str) -> Optional[Dict[str, str]]:
        raw = str(text or "")
        if not raw.startswith("code_artifact::path="):
            return None

        lines = raw.splitlines()
        if len(lines) < 3:
            return None

        path = str(lines[0].split("=", 1)[1] if "=" in lines[0] else "").strip()
        request = str(lines[1].split("=", 1)[1] if "=" in lines[1] else "").strip()
        content_start = raw.find("content:\n")
        content = raw[content_start + len("content:\n"):] if content_start >= 0 else ""
        if not path or not content.strip():
            return None
        return {
            "target_path": path,
            "user_input": request,
            "content": content,
        }

    def _remember_code_artifact_memory(
        self,
        target_path: str,
        content: str,
        user_input: str,
        source_tag: str,
    ) -> None:
        safe_path = str(target_path or "").strip()
        safe_content = str(content or "").strip()
        if not safe_path or not safe_content:
            return
        try:
            record = self._code_artifact_memory_record(safe_path, user_input, safe_content)
            exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    record,
                    include_kinds=["procedure"],
                    include_tags=["code_artifact"],
                ) is not None
            if not exists:
                self.memory.add_memory(
                    record,
                    kind="procedure",
                    tags=["user_memory", "suggestion_context", "code_artifact", str(source_tag or "artifact")],
                )
        except Exception:
            traceback.print_exc()

    def _search_code_artifact_memory(self, target_path: str, user_input: str) -> Optional[Dict[str, str]]:
        safe_path = str(target_path or "").strip()
        target_name = os.path.basename(safe_path).strip().lower()
        if not target_name:
            return None

        query = f"{target_name} {user_input or ''}".strip()
        try:
            hits = self.memory.search(
                query,
                top_k=6,
                include_kinds=["procedure"],
                include_tags=["code_artifact"],
            )
        except Exception:
            hits = []

        best: Optional[Dict[str, str]] = None
        best_score = -1
        for item in hits or []:
            parsed = self._parse_code_artifact_memory_record(str(item.get("text") or ""))
            if not isinstance(parsed, dict):
                continue
            stored_name = os.path.basename(str(parsed.get("target_path") or "")).strip().lower()
            if stored_name != target_name:
                continue
            score = 1
            stored_request = str(parsed.get("user_input") or "")
            if self._content_matches_artifact_intent(stored_request or user_input, safe_path, str(parsed.get("content") or "")):
                score += 2
            if safe_path.lower() == str(parsed.get("target_path") or "").strip().lower():
                score += 1
            if score > best_score:
                best = parsed
                best_score = score

        return best

    def _is_short_confirmation(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        return re.fullmatch(r"(?:yes|y|yeah|yep|ok|okay|sure|go\s+ahead|do\s+it|proceed|run\s+it)", t) is not None

    def _is_execution_followup_request(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        return re.search(
            r"\b(run\s+it|execute\s+it|launch\s+it|start\s+it|execute\s+the\s+file|run\s+the\s+file|launch\s+the\s+file|file\s+is\s+not\s+executing|run\s+the\s+.*generator|execute\s+the\s+.*generator|run\s+the\s+.*script|execute\s+the\s+.*script)\b",
            t,
        ) is not None

    def _resolve_draft_execution_intent(self, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not self._is_execution_followup_request(raw_text):
            return None

        draft = self._get_last_draft_artifact()
        if not isinstance(draft, dict):
            return None
        target_path = str(draft.get("target_path") or "").strip()
        if not target_path:
            return None
        if not target_path.lower().endswith(".py"):
            return None

        return "ps_run", {
            "script": f'python "{target_path}"',
        }

    def _is_draft_save_followup_request(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        if re.search(r"\b(save|write)\b", t) is None:
            return False
        if "```" in t:
            return False
        return (
            re.search(r"\b(script|file|it|this)\b", t) is not None
            or re.search(r"\.[a-z0-9]{1,8}\b", t) is not None
        )

    def _resolve_draft_save_intent(self, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not self._is_draft_save_followup_request(raw_text):
            return None

        draft = self._get_last_draft_artifact()
        if not isinstance(draft, dict):
            return None

        draft_target = str(draft.get("target_path") or "").strip()
        if not draft_target:
            return None

        explicit_target = self._infer_draft_target_path(raw_text, raw_text)
        target_path = str(explicit_target or draft_target).strip()
        if not target_path:
            return None

        return "file_write", {
            "path": target_path,
            "content": "",
            "overwrite": True,
        }

    def _resolve_continuation_intent(self, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        state = self._get_context_continuation()
        if not isinstance(state, dict):
            return None

        created_at = float(state.get("created_at") or 0.0)
        if created_at > 0 and (time.time() - created_at) > 900:
            return None

        t = str(raw_text or "").strip().lower()
        if not t:
            return None

        artifact_path = str(state.get("artifact_path") or "").strip()
        artifact_tool = str(state.get("tool_name") or "").strip()

        if self._is_execution_followup_request(raw_text) and artifact_path.lower().endswith(".py"):
            return "ps_run", {
                "script": f'python "{artifact_path}"',
            }

        # Contextual mode switches for script artifacts.
        style_shift = re.search(r"\b(ascii|emoji|text\s*art|terminal\s*art)\b", t) is not None
        if style_shift and artifact_path.lower().endswith(".py"):
            return "file_write", {
                "path": artifact_path,
                "content": "",
                "overwrite": True,
            }

        if self._is_short_confirmation(raw_text):
            suggested = state.get("suggested_yes_action", {}) if isinstance(state.get("suggested_yes_action", {}), dict) else {}
            next_tool = str(suggested.get("tool_name") or "").strip()
            next_args = suggested.get("tool_args", {}) if isinstance(suggested.get("tool_args", {}), dict) else {}
            if next_tool and next_tool in self._tool_registry():
                return next_tool, next_args

            if artifact_tool and artifact_tool in self._tool_registry():
                replay_args = state.get("tool_args", {}) if isinstance(state.get("tool_args", {}), dict) else {}
                return artifact_tool, dict(replay_args)

        return None

    def _get_last_tool_event(self) -> Optional[Dict[str, Any]]:
        return self._get_pending_action("last_tool_event")

    def _is_context_followup_request(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        return (
            re.search(r"\b(take\s+a\s+look|look\s+around|look\s+there|check\s+there|see\s+what(?:'s|\s+is)\s+there|scan\s+(?:the\s+)?(?:folder|directory)|try\s+again|do\s+that\s+again)\b", t) is not None
            or t in {"take a look", "look", "look there", "check there", "try again"}
        )

    def _is_failed_artifact_followup_request(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        failure_markers = [
            r"\bthat\s+failed\b",
            r"\bfile\s+was\s+incomplete\b",
            r"\bincomplete\b",
            r"\bfinish\s+this\s+correctly\b",
            r"\bbad\s+call\b",
            r"\byou\s+failed\b",
            r"\btry\s+again\b",
        ]
        return any(re.search(pattern, t) is not None for pattern in failure_markers)

    def _resolve_contextual_followup_intent(self, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not self._is_context_followup_request(raw_text):
            draft_retry = self._get_last_draft_artifact()
            if not (isinstance(draft_retry, dict) and self._is_failed_artifact_followup_request(raw_text)):
                return None

        draft_retry = self._get_last_draft_artifact()
        if isinstance(draft_retry, dict) and self._is_failed_artifact_followup_request(raw_text):
            target_path = str(draft_retry.get("target_path") or "").strip()
            if target_path:
                return "file_write", {
                    "path": target_path,
                    "content": "",
                    "overwrite": True,
                }

        pending_write = self._get_pending_write_authorization()
        if isinstance(pending_write, dict):
            pending_args = pending_write.get("tool_args", {}) if isinstance(pending_write.get("tool_args", {}), dict) else {}
            pending_path = str(
                pending_args.get("path")
                or pending_args.get("dst")
                or pending_args.get("src")
                or ""
            ).strip()
            suggested_file = os.path.basename(pending_path) if pending_path else ""

            hinted_root = self._extract_explicit_windows_path(raw_text)
            inspect_path = hinted_root or str(getattr(self, "workspace_root", "") or "").strip()
            if not inspect_path:
                inspect_path = pending_path
            if inspect_path and not os.path.isdir(inspect_path):
                inspect_path = os.path.dirname(inspect_path)

            if inspect_path:
                return "dir_list", {
                    "path": inspect_path,
                    "__followup_hint__": "focus_prompt",
                    "__followup_target__": suggested_file,
                }

        last_event = self._get_last_tool_event()
        if not isinstance(last_event, dict):
            workspace_path = str(getattr(self, "workspace_root", "") or "").strip()
            if workspace_path:
                return "dir_list", {
                    "path": workspace_path,
                    "__followup_hint__": "focus_prompt",
                    "__followup_target__": "",
                }
            return None

        last_tool = str(last_event.get("tool_name") or "").strip()
        last_args = last_event.get("tool_args", {}) if isinstance(last_event.get("tool_args", {}), dict) else {}
        if not last_tool:
            return None

        file_family = {"file_read", "file_write", "file_append", "file_delete", "dir_create", "dir_list", "file_move", "safe_copy"}
        if last_tool in file_family:
            candidate = str(last_args.get("path") or last_args.get("dst") or last_args.get("src") or "").strip()
            if not candidate:
                return None
            inspect_path = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
            if not inspect_path:
                inspect_path = str(getattr(self, "workspace_root", "") or "").strip()
            if inspect_path:
                suggested_file = os.path.basename(candidate) if os.path.basename(candidate) else ""
                return "dir_list", {
                    "path": inspect_path,
                    "__followup_hint__": "focus_prompt",
                    "__followup_target__": suggested_file,
                }

        # For non-file tools, replay the most recent tool call as a broad, useful
        # follow-up behavior unless it is an explicit confirmation/cancel primitive.
        blocked = {"__clarify_intent__", "__confirm_external_write__", "__cancel_external_write__"}
        if last_tool in blocked:
            return None
        if last_tool in self._tool_registry():
            return last_tool, dict(last_args)

        workspace_path = str(getattr(self, "workspace_root", "") or "").strip()
        if workspace_path:
            return "dir_list", {
                "path": workspace_path,
                "__followup_hint__": "focus_prompt",
                "__followup_target__": "",
            }

        return None

    def _set_pending_alias_replay(
        self,
        phrase: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        confidence: float,
    ) -> None:
        args_copy = dict(tool_args or {})
        args_copy.pop("auth_token", None)
        self._set_pending_action("intent_alias_replay", {
            "phrase": str(phrase or "").strip(),
            "tool_name": str(tool_name or "").strip(),
            "tool_args": args_copy,
            "confidence": float(confidence or 0.0),
            "created_at": time.time(),
        })

    def _get_pending_alias_replay(self) -> Optional[Dict[str, Any]]:
        return self._get_pending_action("intent_alias_replay")

    def _clear_pending_alias_replay(self) -> None:
        self._clear_pending_action("intent_alias_replay")

    def _is_generic_confirmation(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        return re.search(r"^(?:yes|yeah|yep|yup|ok|okay|sure|do\s+it|run\s+it|use\s+it|that\s+one)\b", t) is not None

    def _is_generic_cancellation(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        return re.search(r"^(?:no|nope|nah|cancel|stop|nevermind|never\s+mind|ignore\s+it)\b", t) is not None

    def _pending_write_target(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        if tool_name == "file_move":
            src = str(tool_args.get("src") or "").strip()
            dst = str(tool_args.get("dst") or "").strip()
            return f"{src} -> {dst}".strip()
        if tool_name == "safe_copy":
            src = str(tool_args.get("src") or "").strip()
            dst = str(tool_args.get("dst") or "").strip()
            return f"{src} -> {dst}".strip()
        return str(tool_args.get("path") or "").strip()

    def _set_pending_write_authorization(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_input: str,
        error: str,
    ) -> None:
        args_copy = dict(tool_args or {})
        args_copy.pop("auth_token", None)
        self._set_pending_action("external_write", {
            "tool_name": str(tool_name or "").strip(),
            "tool_args": args_copy,
            "user_input": str(user_input or "").strip(),
            "target": self._pending_write_target(tool_name, args_copy),
            "error": str(error or "").strip(),
            "created_at": time.time(),
        })

    def _get_pending_write_authorization(self) -> Optional[Dict[str, Any]]:
        return self._get_pending_action("external_write")

    def _clear_pending_write_authorization(self) -> None:
        self._clear_pending_action("external_write")

    def _is_write_authorization_confirmation(self, text: str) -> bool:
        return is_write_authorization_confirmation(text)

    def _is_write_authorization_cancel(self, text: str) -> bool:
        return is_write_authorization_cancel(text)

    def _message_choice(self, content: str) -> Dict[str, Any]:
        return message_choice(content)

    def _handle_directory_scan_reflex(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_input: str,
    ) -> Optional[Dict[str, Any]]:
        return handle_directory_scan_reflex(self, tool_name, tool_args, user_input)

    def _handle_external_write_reflex(
        self,
        tool_name: str,
    ) -> Optional[Dict[str, Any]]:
        return handle_external_write_reflex(self, tool_name)

    def _handle_identity_reflex(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        request_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        return handle_identity_reflex(self, tool_name, tool_args, request_context)

    def _handle_direct_response_reflex(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_input: str,
    ) -> Optional[Dict[str, Any]]:
        return handle_direct_response_reflex(self, tool_name, tool_args, user_input)

    def _stop_driving_checkins(self) -> Dict[str, Any]:
        try:
            mem = getattr(self, "memory", None)
            if mem is None or not hasattr(mem, "get_task_memories"):
                return {"ok": False, "result": None, "error": "task_memory_not_available"}

            rows = mem.get_task_memories(top_k=250, include_done=False)
            if not isinstance(rows, list):
                rows = []

            removed = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue

                text = str(row.get("text") or "").strip().lower()
                tags_raw = row.get("tags")
                tags_l: List[str] = []
                if isinstance(tags_raw, list):
                    for tag in tags_raw:
                        tag_text = str(tag).strip().lower()
                        if tag_text:
                            tags_l.append(tag_text)

                is_drive = ("driving_checkin" in tags_l) or text.startswith("driving check-in")
                if not is_drive:
                    continue

                task_id = int(row.get("id") or 0)
                if task_id <= 0:
                    continue

                if hasattr(mem, "set_task_status"):
                    try:
                        if mem.set_task_status(task_id, "done"):
                            removed += 1
                    except Exception:
                        traceback.print_exc()

            return {
                "ok": True,
                "result": {
                    "stopped_count": removed,
                },
                "error": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "result": None, "error": str(e)}

    def _start_driving_checkins(self, repeat_seconds: int) -> Dict[str, Any]:
        try:
            safe_repeat = max(300, min(6 * 3600, int(repeat_seconds or 1200)))
            mem = getattr(self, "memory", None)
            if mem is None or not hasattr(mem, "get_task_memories"):
                return {"ok": False, "result": None, "error": "task_memory_not_available"}

            rows = mem.get_task_memories(top_k=500, include_done=False)
            if not isinstance(rows, list):
                rows = []

            removed = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue

                text = str(row.get("text") or "").strip().lower()
                tags_raw = row.get("tags")
                tags_l: List[str] = []
                if isinstance(tags_raw, list):
                    for tag in tags_raw:
                        tag_text = str(tag).strip().lower()
                        if tag_text:
                            tags_l.append(tag_text)

                is_drive = ("driving_checkin" in tags_l) or text.startswith("driving check-in")
                if not is_drive:
                    continue

                task_id = int(row.get("id") or 0)
                if task_id <= 0:
                    continue

                if hasattr(mem, "set_task_status"):
                    try:
                        if mem.set_task_status(task_id, "done"):
                            removed += 1
                    except Exception:
                        traceback.print_exc()

            task_text = (
                "Driving check-in: ask how I'm doing, remind me to hydrate, "
                "check focus level, and keep me company for a minute."
            )
            due_at = time.time() + float(safe_repeat)
            tags = [
                "user_memory",
                "recurring_task",
                f"repeat_{int(safe_repeat)}s",
                "reminder_task",
                "driving_checkin",
            ]

            if not hasattr(mem, "add_task_memory"):
                return {"ok": False, "result": None, "error": "task_memory_write_not_supported"}

            memory_id = mem.add_task_memory(
                task_text,
                due_at=due_at,
                tags=tags,
                status="pending",
            )

            return {
                "ok": memory_id is not None,
                "result": {
                    "memory_id": memory_id,
                    "repeat_seconds": int(safe_repeat),
                    "due_at": float(due_at),
                    "reset_count": removed,
                    "text": task_text,
                    "tags": tags,
                },
                "error": None if memory_id is not None else "task_memory_write_failed",
            }
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "result": None, "error": str(e)}

    def _validate_required_tool_routes(self) -> None:
        """
        Fail fast if critical intent routes are missing.

        This protects against silent regressions where key commands fall back to
        model chat instead of local tool execution.
        """
        if str(os.getenv("MK1_SKIP_ROUTE_GUARD", "0")).strip().lower() in {"1", "true", "yes", "on"}:
            return

        probes: List[Tuple[str, str]] = [
            ("execute this code: print(1)", "code_execute"),
            ("run this powershell: Write-Output ok", "code_execute"),
            ("probe the CPU and see what comes up", "ps_run"),
        ]

        missing: List[str] = []

        for sample, expected_tool in probes:
            try:
                got_tool, _ = self.detect_tool_intent(sample)
            except Exception as e:
                missing.append(f"{sample} -> exception: {type(e).__name__}: {e}")
                continue

            if got_tool != expected_tool:
                missing.append(f"{sample} -> expected {expected_tool}, got {got_tool}")

        if missing:
            raise RuntimeError(
                "required_tool_routes_missing: " + "; ".join(missing)
            )

    def _store_turn_memory(
        self,
        user_input: str,
        assistant_text: str,
        retry_guided: bool = False,
    ) -> None:
        try:
            user_mem = str(user_input or "").strip()
            assistant_mem = str(assistant_text or "").strip()

            self._remember_draft_artifact_from_turn(user_mem, assistant_mem)

            if len(user_mem) > 1800:
                user_mem = user_mem[:1800].rstrip() + "..."

            # Long list/tool outputs can overwhelm embedding services.
            # Persist a compact representative summary for short-term recall.
            if len(assistant_mem) > 1800:
                assistant_mem = self._compress_multiline(assistant_mem, max_lines=24, max_chars=1800)

            user_tags = ["short_term", "user_turn"]
            correction_patterns = [
                r"\bthat was (?:a|an) .{2,60} request\b",
                r"\bi meant (?:to|that|a|an)\b",
                r"\byou should have (?:used|called|treated|routed)\b",
                r"\bnot .{1,40}, (?:use|do|create|run|search|read|write)\b",
            ]
            if any(re.search(pattern, user_mem, re.IGNORECASE) is not None for pattern in correction_patterns):
                user_tags.extend(["user_memory", "routing_correction"])

            assistant_tags = ["short_term", "assistant_turn"]

            if is_ostranauts_query(user_mem):
                user_tags.append("ostranauts")
                assistant_tags.append("ostranauts")

            if is_failed_decision_text(assistant_mem):
                assistant_tags.append("failed_decision")

            self.memory.add_memory(
                user_mem,
                kind="interaction",
                tags=user_tags,
            )

            self.memory.add_memory(
                assistant_mem,
                kind="interaction",
                tags=assistant_tags,
            )

            # Why: turn storage is the best place to convert corrections and Ostranauts play sessions into lessons.
            # Guard: keeps memory reinforcement additive instead of changing the live routing path.
            self._maybe_store_learning_memory(
                user_mem,
                assistant_mem,
                retry_guided=bool(retry_guided),
            )

            # Promote repeated short-term interaction fragments into long-term facts.
            if hasattr(self.memory, "auto_promote_short_term"):
                self.memory.auto_promote_short_term(
                    seed_text=user_input,
                    min_hits=int(self.auto_promote_min_hits),
                    recent_window=int(self.auto_promote_recent_window),
                    semantic_top_k=int(self.auto_promote_semantic_top_k),
                )

            fact = self._extract_personal_fact(user_input)
            if fact:
                self._store_user_fact_if_new(fact)

        except Exception:
            traceback.print_exc()

    def _extract_personal_fact(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        low = raw.lower()
        if "?" in raw:
            return ""
        if any(x in low for x in [" remember ", "store", "save this", "write this down"]):
            return ""

        # Core pattern: "my <attribute> is <value>"
        m = re.search(r"\bmy\s+([a-z][a-z0-9_\- ]{1,40})\s+is\s+(.+)$", raw, re.IGNORECASE)
        if not m:
            return ""

        attr = re.sub(r"\s+", " ", m.group(1).strip())
        value = m.group(2).strip()
        value = re.sub(
            r"[,;:\-]?\s*(?:save|store|remember|keep)(?:\s+(?:this|that))?(?:\s+please)?[.!?]*$",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip()
        value = value.strip(" .")

        if not attr or not value:
            return ""

        fact = f"my {attr} is {value}"
        return fact

    def _store_user_fact_if_new(self, fact: str) -> None:
        f = (fact or "").strip()
        if not f:
            return

        try:
            name_match = re.match(r"^my\s+name\s+is\s+(.+)$", f, re.IGNORECASE)
            if name_match and hasattr(self.memory, "recent_memories") and hasattr(self.memory, "delete_memory_ids"):
                target_name = name_match.group(1).strip().lower()
                existing = self.memory.recent_memories(
                    top_k=500,
                    include_kinds=["fact"],
                    include_tags=["user_memory", "profile_auto", "authoritative_profile"],
                )
                delete_ids = []
                for item in existing or []:
                    text = (item.get("text") or "").strip()
                    if not text:
                        continue
                    m = re.match(r"^my\s+name\s+is\s+(.+)$", text, re.IGNORECASE)
                    if not m:
                        continue
                    current_name = m.group(1).strip().lower()
                    if current_name and current_name != target_name:
                        delete_ids.append(int(item.get("id") or 0))

                if delete_ids:
                    self.memory.delete_memory_ids(delete_ids)

            exists = None
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    f,
                    include_kinds=["fact", "preference", "procedure"],
                    include_tags=["user_memory"],
                )

            if exists is None:
                self.memory.add_memory(
                    f,
                    kind="fact",
                    tags=self._authoritative_tags_for_fact(f),
                )
        except Exception:
            traceback.print_exc()

    def _strip_code_fences(self, text: str) -> str:
        if not text:
            return ""

        raw = text.strip()
        m = re.search(r'^```(?:[A-Za-z0-9_+-]+)?\n([\s\S]*?)\n```$', raw)
        if m:
            return m.group(1).strip("\n")
        return raw

    def _clean_response_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        lines = cleaned.splitlines()
        kept: List[str] = []
        for ln in lines:
            low = ln.strip().lower()
            if low.startswith("- do not ") or low.startswith("do not "):
                continue
            if low in {"---", "important rules:"}:
                continue
            if "db-backed mina tone" in low:
                continue
            if "prefer these db-backed facts over guessing" in low:
                continue
            if re.match(r"^\s*mina-style sentence", low):
                ln = re.sub(r"^\s*mina-style sentence[^:]*:\s*", "", ln, flags=re.IGNORECASE)
                if not ln.strip():
                    continue
            if re.match(r"^\s*mina-style output", low):
                ln = re.sub(r"^\s*mina-style output[^:]*:\s*", "", ln, flags=re.IGNORECASE)
                if not ln.strip():
                    continue
            if re.match(r"^\s*mina-sign-off", low):
                continue
            kept.append(ln)

        out = "\n".join(kept).strip()
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def _tool_call_pattern(self) -> re.Pattern:
        return re.compile(
            r'(?:<\|tool_call\>|<\|[\w.-]+\||\|?<spotify_link>)\s*call:(\w+)\{([\s\S]*?)\}(?:<tool_call\|>|\|>|>|</spotify_link>\|?)',
            flags=re.IGNORECASE,
        )

    def _extract_tool_calls_from_text(self, text: str) -> List[Tuple[str, str]]:
        content = str(text or "")
        if not content.strip():
            return []

        out: List[Tuple[str, str]] = []
        seen = set()

        def _add(name: str, args: str) -> None:
            n = str(name or "").strip()
            a = str(args or "").strip()
            if not n:
                return
            key = (n.lower(), a)
            if key in seen:
                return
            seen.add(key)
            out.append((n, a))

        for name, args in re.findall(self._tool_call_pattern(), content):
            _add(name, args)

        # Fallback for malformed wrappers seen in the wild, e.g.
        # |：<spotify_link{library:"true", track_selection:"random"}>|
        for args in re.findall(
            r"[|｜]?\s*[：:]?\s*<\s*spotify_link\s*\{([\s\S]*?)\}\s*>\s*[|｜]?",
            content,
            flags=re.IGNORECASE,
        ):
            _add("spotify_link", args)

        # Fallback for plain call:name{...} emitted without wrappers.
        for name, args in re.findall(
            r"\bcall\s*:\s*([A-Za-z_][\w.-]*)\s*\{([\s\S]*?)\}",
            content,
            flags=re.IGNORECASE,
        ):
            _add(name, args)

        # Fallback for bare spotify_link{...}.
        for args in re.findall(r"spotify_link\s*\{([\s\S]*?)\}", content, flags=re.IGNORECASE):
            _add("spotify_link", args)

        # Fallback for bare single-line tool calls inside plain text or fenced
        # blocks, e.g.:
        # file_write{path: "foo.py", content: "print('ok')"}
        available_tools = set(self._tool_registry().keys())
        for name, args in re.findall(
            r"(?m)^\s*([A-Za-z_][\w.-]*)\s*\{([^\n\r]*?)\}\s*$",
            content,
            flags=re.IGNORECASE,
        ):
            if available_tools and name not in available_tools:
                continue
            _add(name, args)

        # Fallback for pipe-arrow wrapper emitted by some templates, e.g.
        # |>web_search{query: "...", max_results: 5}
        # <|tool_call>
        for name, args in re.findall(
            r"\|>\s*([A-Za-z_][\w.-]*)\s*\{([\s\S]*?)\}\s*(?:<\|tool_call\>|<tool_call\|>)?",
            content,
            flags=re.IGNORECASE,
        ):
            _add(name, args)

        return out

    def _parse_tool_args(self, args_str: str) -> Dict[str, Any]:
        text = str(args_str or "").strip()
        if not text:
            return {}

        out: Dict[str, Any] = {}
        for m in re.finditer(
            r"([A-Za-z_][\w.-]*)\s*:\s*(\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^,{}]+)",
            text,
        ):
            key = m.group(1).strip()
            raw = m.group(2).strip()
            val: Any = raw

            if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "'" and raw[-1] == "'")):
                inner = raw[1:-1]
                try:
                    parsed = ast.literal_eval(raw)
                    val = parsed if isinstance(parsed, str) else inner
                except Exception:
                    try:
                        val = bytes(inner, "utf-8").decode("unicode_escape")
                    except Exception:
                        val = inner
            else:
                low = raw.lower()
                if low in {"true", "false"}:
                    val = low == "true"
                elif re.fullmatch(r"-?\d+", raw):
                    try:
                        val = int(raw)
                    except Exception:
                        val = raw
                elif re.fullmatch(r"-?\d+\.\d+", raw):
                    try:
                        val = float(raw)
                    except Exception:
                        val = raw

            out[key] = val

        return out

    def _strip_tool_call_markup(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        cleaned = self._tool_call_pattern().sub("", cleaned)
        cleaned = re.sub(
            r"[|｜]?\s*[：:]?\s*<\s*spotify_link\s*\{[\s\S]*?\}\s*>\s*[|｜]?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bcall\s*:\s*[A-Za-z_][\w.-]*\s*\{[\s\S]*?\}",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\|>\s*[A-Za-z_][\w.-]*\s*\{[\s\S]*?\}\s*(?:<\|tool_call\>|<tool_call\|>)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"(?m)^\s*[A-Za-z_][\w.-]*\s*\{[^\n\r]*?\}\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _with_mina_flair(self, text: str, tool_name: str = "") -> str:
        out = (text or "").strip()
        if not out:
            return out

        # Keep raw file content exact.
        if tool_name in {"file_read"}:
            return out

        low = out.lower()
        if any(x in low for x in ["gremlin", "chaos", "*twinkle", "*cackles"]):
            return out

        flair = "Gremlin check complete."
        if out[-1] not in ".!?":
            out += "."

        return f"{out}\n\n{flair}"

    def _wants_grounded_tool_analysis(self, user_input: str) -> bool:
        q = (user_input or "").lower()
        if not q:
            return False

        patterns = [
            r"\banaly[sz]e\b",
            r"\bexplain\b",
            r"\breview\b",
            r"\bimprove\b",
            r"\bsuggest\b",
            r"\brecommend\b",
            r"\bfix\b",
            r"\brefactor\b",
            r"\bwhat do you think\b",
            r"\banything wrong\b",
            r"\bchanges?\b",
            r"\btell me about\b",
        ]
        return any(re.search(p, q) is not None for p in patterns)

    def _render_grounded_tool_analysis(
        self,
        messages: List[Dict[str, Any]],
        user_input: str,
        tool_name: str,
        formatted: str,
        tool_ok: bool,
    ) -> str:
        convo = [
            {
                "role": "system",
                "content": (
                    "You are answering from verified tool output. "
                    "Start with a concise summary of the verified state shown in VERIFIED OUTPUT. "
                    "After that, you may give concise suggestions, interpretation, or next steps that stay consistent with that verified state. "
                    "Do not invent missing data, file contents, failures, or tool results. "
                    "If the output shows an error, say the error plainly first, then suggest what to do next. "
                    "Keep it concise and in Mina voice."
                ),
            },
            {
                "role": "user",
                "content": user_input,
            },
            {
                "role": "assistant",
                "content": f"VERIFIED OUTPUT:\n```text\n{formatted}\n```",
            },
        ]

        try:
            reply = self.model.chat(convo)
            text = self._clean_response_text(self._extract_text(reply)).strip()
            low_text = text.lower().strip()
            if (
                not text
                or low_text.startswith("(lm studio error:")
                or "httpconnectionpool(" in low_text
                or "read timed out" in low_text
                or "connect timeout" in low_text
                or "connection refused" in low_text
            ):
                return f"VERIFIED OUTPUT:\n```text\n{formatted}\n```"

            if text[-1] not in ".!?":
                text += "."
            return f"{text}\n\nVERIFIED OUTPUT:\n```text\n{formatted}\n```"
        except Exception:
            traceback.print_exc()
            return f"VERIFIED OUTPUT:\n```text\n{formatted}\n```"

    def _render_tool_response_as_mina(
        self,
        messages: List[Dict[str, Any]],
        user_input: str,
        tool_name: str,
        formatted: str,
        tool_ok: bool,
    ) -> str:
        if tool_name == "ps_run":
            if tool_ok:
                return f"Here's the verified PowerShell output:\n\n```text\n{formatted}\n```"
            return f"PowerShell execution failed.\n\n```text\n{formatted}\n```"

        if tool_name in self._raw_direct_render_tool_names():
            return formatted

        if tool_name == "file_read" and not self._wants_grounded_tool_analysis(user_input):
            return formatted

        if tool_name in self._grounded_direct_render_tool_names():
            return self._render_grounded_tool_analysis(
                messages=messages,
                user_input=user_input,
                tool_name=tool_name,
                formatted=formatted,
                tool_ok=tool_ok,
            )

        if tool_name in {"file_read", "ps_run", "code_execute", "__project_test_run__"} and self._wants_grounded_tool_analysis(user_input):
            return self._render_grounded_tool_analysis(
                messages=messages,
                user_input=user_input,
                tool_name=tool_name,
                formatted=formatted,
                tool_ok=tool_ok,
            )

        convo = [
            {
                "role": "system",
                "content": (
                    "You are answering from verified tool output. "
                    "Write exactly one short Mina-style line with no quotes, no labels, no markdown, and no emojis. "
                    "The verified output is the source of truth. Do not invent missing details or claim a different result. "
                    "Do not include technical details beyond a tiny summary; those are handled separately."
                ),
            },
            {
                "role": "user",
                "content": user_input,
            },
            {
                "role": "assistant",
                "content": f"VERIFIED OUTPUT:\n```text\n{formatted}\n```",
            },
        ]

        try:
            reply = self.model.chat(convo)
            text = self._clean_response_text(self._extract_text(reply))
            # If the model call failed/timed out, do not leak transport errors to users
            # when we already have verified tool output to return.
            low_text = text.lower().strip()
            if (
                low_text.startswith("(lm studio error:")
                or "httpconnectionpool(" in low_text
                or "read timed out" in low_text
                or "connect timeout" in low_text
                or "connection refused" in low_text
            ):
                return formatted
            while True:
                lines = text.splitlines()
                if not lines:
                    break
                first = lines[0].strip().lower()
                if re.match(r'^\d+\)\s+', first):
                    text = "\n".join(lines[1:]).strip()
                    continue
                break
            text = self._strip_tool_call_markup(text)
            flair = text.strip()
            if not flair:
                flair = "Gremlin check complete."
            if flair[-1] not in ".!?":
                flair += "."

            return f"{flair}\n\nVERIFIED OUTPUT:\n```text\n{formatted}\n```"
        except Exception:
            traceback.print_exc()

        return self._with_mina_flair(formatted, tool_name)

    def _normalize_memory_reply_perspective(self, user_query: str, text: str) -> str:
        out = (text or "").strip()
        if not out:
            return ""

        q = (user_query or "").lower()
        if " my " in f" {q} " or q.startswith("my "):
            out = re.sub(r"^\s*my\b", "your", out, flags=re.IGNORECASE)

        return out

    def _reinforce_memory_texts(self, texts: List[str], add_tags: Optional[List[str]] = None) -> None:
        try:
            if not hasattr(self.memory, "touch_memory_by_text"):
                return
            for txt in texts or []:
                clean = str(txt or "").strip()
                if not clean:
                    continue
                self.memory.touch_memory_by_text(
                    clean,
                    include_kinds=["fact", "preference", "procedure"],
                    include_tags=["user_memory"],
                    add_tags=add_tags or ["reinforced_recall"],
                )
        except Exception:
            traceback.print_exc()

    def _extract_name_value(self, fact_text: str) -> str:
        src = (fact_text or "").strip().rstrip(".")
        if not src:
            return ""

        m = re.search(r"\b(?:my|your)\s+name\s+is\s+(.+)$", src, re.IGNORECASE)
        if not m:
            return ""

        value = m.group(1).strip().strip(".,;:!? ")
        # Trim trailing helper phrases that may sneak in from noisy memory lines.
        value = re.split(r"\b(?:and|but)\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        return value

    def _cap_sentences(self, text: str, max_sentences: int = 2) -> str:
        value = (text or "").strip()
        if not value or max_sentences < 1:
            return value

        parts = re.split(r"(?<=[.!?])\s+", value)
        if len(parts) <= max_sentences:
            return value

        return " ".join(parts[:max_sentences]).strip()

    def _cap_paragraphs(self, text: str, max_paragraphs: int = 2) -> str:
        value = (text or "").strip()
        if not value or max_paragraphs < 1:
            return value

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", value) if p.strip()]
        if len(paragraphs) <= max_paragraphs:
            return value

        return "\n\n".join(paragraphs[:max_paragraphs]).strip()

    def _extract_memory_request_parts(self, user_query: str) -> List[str]:
        q = (user_query or "").strip().lower()
        if not q:
            return []

        q = q.replace("?", " ")
        q = re.sub(r"\b(what is|what's|what are|tell me|do you remember|can you remember|recall)\b", " ", q)
        q = re.sub(r"\b(my|the|a|an|please|about|for|to|of)\b", " ", q)
        q = re.sub(r"\s+", " ", q).strip()

        if " and " not in q:
            return []

        parts = [p.strip(" ,.;:") for p in q.split(" and ") if p.strip(" ,.;:")]
        cleaned: List[str] = []
        for p in parts:
            p = re.sub(r"\b(is|are|was|were)\b", " ", p)
            p = re.sub(r"\bhow old am i(?: today)?(?: down to the minute)?\b", " ", p)
            p = re.sub(r"\s+", " ", p).strip()
            if p:
                cleaned.append(p)
        return cleaned[:3]

    def _extract_memory_slots(self, user_query: str) -> Dict[str, bool]:
        q = (user_query or "").lower()
        return {
            "name": bool(re.search(r"\b(what\s+is\s+my\s+name|my\s+name|who\s+am\s+i)\b", q)),
            "birthdate": bool(re.search(r"\b(birthdate|birthday|date of birth|dob)\b", q)),
            "eye_color": bool(re.search(r"\b(eye color|eyes color|eye|eyes)\b", q)),
            "favorite_color": bool(re.search(r"\b(favorite|favourite)\s+color\b", q)),
            "age": bool(re.search(r"\bhow old\b|\bage\b", q)),
        }

    def _is_transformative_memory_query(self, user_query: str) -> bool:
        q = (user_query or "").lower()
        if not q:
            return False

        patterns = [
            r"\btell me about\b",
            r"\brelate\b",
            r"\banalogy\b",
            r"\bmetaphor\b",
            r"\bcompare\b",
            r"\blike\b",
            r"\bsimilar\b",
            r"\bsuggest\b",
            r"\brecommend\b",
            r"\bnext step",
            r"\bapply\b",
            r"\bconnect\b",
            r"\bwhat else\b",
            r"\bwho am i\b",
        ]
        return any(re.search(p, q) is not None for p in patterns)

    def _render_memory_transform_reply(
        self,
        messages: List[Dict[str, str]],
        user_query: str,
        fact_lines: List[str],
        missing_lines: List[str],
    ) -> str:
        facts = [str(x or "").strip() for x in (fact_lines or []) if str(x or "").strip()]
        if not facts:
            return ""

        strict_facts = "\n".join([f"- {f}" for f in facts])
        missing = "\n".join([f"- {m}" for m in (missing_lines or []) if str(m or "").strip()])

        convo = list(messages or [])
        convo.append(
            {
                "role": "system",
                "content": (
                    "You are answering a memory transformation request. "
                    "Use Mina voice, but keep factual truth locked. "
                    "These facts are authoritative and immutable:\n"
                    f"{strict_facts}\n"
                    "Never change or contradict those facts. "
                    "Your first sentence must explicitly restate the relevant fact before any analogy or suggestion. "
                    "You may relate, compare, suggest, and add analogies based on them. "
                    "Do not say a lookup failed, a tool failed, or data could not be fetched unless the provided facts explicitly say that. "
                    "Do not invent new personal facts. Keep it concise (2-4 short lines)."
                    + (f"\nIf useful, mention missing info:\n{missing}" if missing else "")
                ),
            }
        )

        try:
            reply = self.model.chat(convo)
            text = self._clean_response_text(self._extract_text(reply)).strip()
            return text
        except Exception:
            traceback.print_exc()
            return ""

    def _extract_profile_slot_value(self, slot: str, fact_text: str) -> str:
        src = (fact_text or "").strip().rstrip(".")
        if not src:
            return ""

        if slot == "name":
            return self._extract_name_value(src)

        if slot == "eye_color":
            patterns = [
                r"\b(?:my|your)\s+eye\s+color\s+is\s+(.+)$",
                r"\b(?:my|your)\s+eyes\s+are\s+(.+)$",
                r"\b(?:my|your)\s+eyes\s+color\s+is\s+(.+)$",
            ]
            for pattern in patterns:
                m = re.search(pattern, src, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip(".,;:!? ")

        if slot == "favorite_color":
            patterns = [
                r"\b(?:my|your)\s+favorite\s+color\s+is\s+(.+)$",
                r"\b(?:my|your)\s+favourite\s+color\s+is\s+(.+)$",
            ]
            for pattern in patterns:
                m = re.search(pattern, src, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip(".,;:!? ")

        if slot == "birthdate":
            bdt = self._parse_birthdate_from_text(src)
            if bdt:
                return self._format_birthdate_pretty(bdt)

        return src.strip().strip(".,;:!? ")

    def _build_memory_fact_anchor_lines(
        self,
        user_query: str,
        name_fact: str,
        eye_fact: str,
        favorite_fact: str,
        birth_fact: str,
        age_sentence: str,
    ) -> List[str]:
        anchors: List[str] = []

        if name_fact:
            name_value = self._extract_profile_slot_value("name", name_fact)
            if name_value:
                anchors.append(f"Your name is {name_value}.")

        if eye_fact:
            eye_value = self._extract_profile_slot_value("eye_color", eye_fact)
            if eye_value:
                anchors.append(f"Your eye color is {eye_value}.")

        if favorite_fact:
            fav_value = self._extract_profile_slot_value("favorite_color", favorite_fact)
            if fav_value:
                anchors.append(f"Your favorite color is {fav_value}.")

        if birth_fact:
            birth_value = self._extract_profile_slot_value("birthdate", birth_fact)
            if birth_value:
                anchors.append(f"Your birthdate is {birth_value}.")

        if age_sentence:
            anchors.append(age_sentence.rstrip())

        deduped: List[str] = []
        seen = set()
        for anchor in anchors:
            key = " ".join(anchor.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(anchor)
        return deduped

    def _anchor_line_satisfied(self, anchor: str, reply_text: str) -> bool:
        anchor_txt = str(anchor or "").strip()
        reply_txt = str(reply_text or "").strip()
        if not anchor_txt or not reply_txt:
            return False

        anchor_low = " ".join(anchor_txt.lower().split())
        reply_low = " ".join(reply_txt.lower().split())
        if anchor_low in reply_low:
            return True

        m = re.match(r"^your name is (.+)\.$", anchor_txt, re.IGNORECASE)
        if m:
            value = re.escape(m.group(1).strip())
            return re.search(rf"\b(?:your\s+name\s+is|you'?re|you\s+are)\s+{value}\b", reply_txt, re.IGNORECASE) is not None

        m = re.match(r"^your eye color is (.+)\.$", anchor_txt, re.IGNORECASE)
        if m:
            value = re.escape(m.group(1).strip())
            return re.search(rf"\b(?:your\s+eye\s+color\s+is|your\s+eyes\s+are|eyes\s+are)\s+{value}\b", reply_txt, re.IGNORECASE) is not None

        m = re.match(r"^your favorite color is (.+)\.$", anchor_txt, re.IGNORECASE)
        if m:
            value = re.escape(m.group(1).strip())
            return re.search(rf"\b(?:your\s+favou?rite\s+color\s+is)\s+{value}\b", reply_txt, re.IGNORECASE) is not None

        m = re.match(r"^your birthdate is (.+)\.$", anchor_txt, re.IGNORECASE)
        if m:
            value = re.escape(m.group(1).strip())
            return re.search(rf"\b(?:your\s+birthdate\s+is|your\s+birthday\s+is)\s+{value}\b", reply_txt, re.IGNORECASE) is not None

        return False

    def _enforce_memory_fact_anchors(self, reply_text: str, anchors: List[str]) -> str:
        text = (reply_text or "").strip()
        required = [str(a or "").strip() for a in (anchors or []) if str(a or "").strip()]
        if not required:
            return text

        missing = []
        for anchor in required:
            if not self._anchor_line_satisfied(anchor, text):
                missing.append(anchor)

        if not missing:
            return text

        prefix = " ".join(missing).strip()
        if not text:
            return prefix
        return f"{prefix} {text}".strip()

    def _is_broad_memory_recall_query(self, user_query: str) -> bool:
        q = (user_query or "").strip().lower()
        if not q:
            return False
        patterns = [
            r"\bwhat do you remember\b",
            r"\bdo you remember anything\b",
            r"\bwhat do you know about me\b",
            r"\bwhat do you remember about me\b",
            r"\bremember anything\b",
            r"\banything about me\b",
        ]
        return any(re.search(p, q) is not None for p in patterns)

    def _pick_fact_for_keywords(self, facts: List[str], keywords: List[str], used: set) -> str:
        for idx, fact in enumerate(facts):
            if idx in used:
                continue
            low = fact.lower()
            if any(k in low for k in keywords):
                used.add(idx)
                return fact
        return ""

    def _parse_birthdate_from_text(self, text: str) -> Optional[datetime]:
        src = (text or "").strip().rstrip(".")
        if not src:
            return None

        month_rx = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
        m = re.search(rf"{month_rx}\s+\d{{1,2}},?\s+\d{{4}}", src, re.IGNORECASE)
        if m:
            token = m.group(0)
            for fmt in ["%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y"]:
                try:
                    return datetime.strptime(token, fmt)
                except Exception:
                    pass

        m2 = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", src)
        if m2:
            token = m2.group(0)
            for fmt in ["%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y"]:
                try:
                    return datetime.strptime(token, fmt)
                except Exception:
                    pass

        return None

    def _compute_age_sentence(self, birth_dt: datetime) -> str:
        now = datetime.now()
        b = birth_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if b > now:
            return "Current datetime is " + now.strftime("%Y-%m-%d %H:%M") + "; I cannot compute age from a future birthdate."

        years = now.year - b.year - ((now.month, now.day) < (b.month, b.day))
        days = (now - b).days
        minutes = int((now - b).total_seconds() // 60)

        now_text = now.strftime("%Y-%m-%d %H:%M")
        return f"Current datetime is {now_text}; you are {years} years old, {days:,} days old, and about {minutes:,} minutes old."

    def _sanitize_memory_fact_line(self, line: str) -> str:
        txt = (line or "").strip().lstrip("- ").strip()
        if not txt:
            return ""

        if txt.lower().startswith("no matching memories found"):
            return ""

        txt = re.sub(r"^\s*(store|save|remember|note|add|keep)\s+", "", txt, flags=re.IGNORECASE)
        txt = re.sub(
            r"[,;:\-]?\s*(?:save|store|remember|keep)(?:\s+(?:this|that))?(?:\s+please)?[.!?]*$",
            "",
            txt,
            flags=re.IGNORECASE,
        ).strip()
        txt = self._clean_response_text(txt)
        txt = self._normalize_memory_reply_perspective("my", txt)

        if not txt:
            return ""

        # Drop conversational echoes/questions that can leak from interaction memory.
        low_all = txt.lower()
        if any(x in low_all for x in [
            "what color are my eyes",
            "what is my eye color",
            "what is my birthdate",
            "how old am i",
        ]):
            return ""

        if re.match(r"^(mark|set|update|create|delete|remove|run|execute|list|show)\b", low_all):
            return ""

        # Keep the first declarative sentence only.
        for sent in re.split(r"(?<=[.!?])\s+", txt):
            s = sent.strip()
            if not s:
                continue
            if s.endswith("?"):
                continue
            low = s.lower()
            if any(x in low for x in ["curiosity", "mystery", "wondering"]):
                continue
            if re.match(r"^(what|when|where|who|why|how|can|do|is|are)\b", low):
                continue
            if s[-1] not in ".!?":
                s += "."
            return s

        return ""

    def _add_months(self, dt: datetime, months: int) -> datetime:
        year = dt.year + (dt.month - 1 + months) // 12
        month = (dt.month - 1 + months) % 12 + 1
        # Clamp day to end-of-month.
        day = dt.day
        for d in [31, 30, 29, 28]:
            try:
                return dt.replace(year=year, month=month, day=min(day, d))
            except Exception:
                continue
        return dt.replace(year=year, month=month, day=1)

    def _compute_age_details(self, birth_dt: datetime) -> Dict[str, Any]:
        now = datetime.now()
        b = birth_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if b > now:
            return {
                "ok": False,
                "now_text": now.strftime("%Y-%m-%d %H:%M"),
                "error": "future_birthdate",
            }

        # Calendar breakdown: years, months, days.
        years = now.year - b.year - ((now.month, now.day) < (b.month, b.day))
        anchor_year = b.replace(year=b.year + years)
        months = (now.year - anchor_year.year) * 12 + (now.month - anchor_year.month)
        if now.day < anchor_year.day:
            months -= 1
        if months < 0:
            months = 0

        anchor_month = self._add_months(anchor_year, months)
        days = (now.date() - anchor_month.date()).days
        if days < 0:
            days = 0

        total_days = (now - b).days
        total_minutes = int((now - b).total_seconds() // 60)

        return {
            "ok": True,
            "now_text": now.strftime("%Y-%m-%d %H:%M"),
            "years": years,
            "months": months,
            "days": days,
            "total_days": total_days,
            "total_minutes": total_minutes,
        }

    def _format_birthdate_pretty(self, birth_dt: datetime) -> str:
        month_name = birth_dt.strftime("%B")
        return f"{month_name} {birth_dt.day}, {birth_dt.year}"

    def _search_memory_candidates(
        self,
        probes: List[str],
        user_only: bool,
        top_k: int = 8,
    ) -> List[str]:
        candidates: List[str] = []
        include_tags = ["user_memory"] if user_only else None

        for q in probes:
            try:
                results = self.memory.search(
                    q,
                    top_k=top_k,
                    include_kinds=["fact", "preference", "procedure"],
                    include_tags=include_tags,
                )
                for item in results:
                    s = self._sanitize_memory_fact_line(str(item.get("text") or ""))
                    if s:
                        candidates.append(s)
            except Exception:
                traceback.print_exc()

        return candidates

    def _recent_memory_candidates(
        self,
        user_only: bool,
        top_k: int = 800,
    ) -> List[str]:
        candidates: List[str] = []
        include_tags = ["user_memory"] if user_only else None

        try:
            recents = self.memory.recent_memories(
                top_k=top_k,
                include_kinds=["fact", "preference", "procedure"],
                include_tags=include_tags,
            )
        except Exception:
            recents = []

        for item in recents or []:
            raw = str(item.get("text") or "")
            s = self._sanitize_memory_fact_line(raw)
            if s:
                candidates.append(s)

        return candidates

    def _slot_fact_matches(self, slot: str, fact_text: str) -> bool:
        low = (fact_text or "").lower()
        if not low:
            return False

        if slot == "name":
            return "my name is" in low or "name is" in low

        if slot == "eye_color":
            # Avoid matching generic color facts that are not about eyes.
            return ("eye color" in low) or ("eyes are" in low) or ("eye is" in low) or (
                ("eye" in low or "eyes" in low) and "color" in low
            )

        if slot == "birthdate":
            return any(x in low for x in ["birthdate", "birthday", "date of birth", "dob", "born"])

        if slot == "favorite_color":
            return (
                ("favorite color" in low)
                or ("favourite color" in low)
                or (("favorite" in low or "favourite" in low) and ("color" in low or "colour" in low))
            )

        return False

    def _lookup_slot_fact(self, slot: str, user_query: str) -> str:
        slot_queries = {
            "name": ["my name is", "name", "what is my name"],
            "eye_color": ["eye color", "eyes", "eye"],
            "birthdate": ["birthdate", "birthday", "date of birth", "dob"],
            "favorite_color": ["favorite color", "favourite color", "color", "colour"],
        }
        probes = slot_queries.get(slot, [])
        if not probes:
            return ""

        def _first_slot_match(candidates: List[str]) -> str:
            seen = set()
            for c in candidates:
                n = " ".join(c.lower().split())
                if n in seen:
                    continue
                seen.add(n)
                if self._slot_fact_matches(slot, c):
                    return c
            return ""

        # Try multiple sources in priority order; continue until a real slot match is found.
        cand_user_sem = self._search_memory_candidates(
            probes=probes,
            user_only=True,
            top_k=8,
        )
        hit = _first_slot_match(cand_user_sem)
        if hit:
            return hit

        cand_any_sem = self._search_memory_candidates(
            probes=probes,
            user_only=False,
            top_k=8,
        )
        hit = _first_slot_match(cand_any_sem)
        if hit:
            return hit

        # Lexical fallback over recent explicit facts.
        cand_user_recent = self._recent_memory_candidates(
            user_only=True,
            top_k=800,
        )
        hit = _first_slot_match(cand_user_recent)
        if hit:
            return hit

        cand_any_recent = self._recent_memory_candidates(
            user_only=False,
            top_k=800,
        )
        hit = _first_slot_match(cand_any_recent)
        if hit:
            return hit

        # Final fallback: reuse memory_read tool query behavior.
        tool_queries = {
            "name": "what is my name",
            "eye_color": "what is my eye color",
            "birthdate": "what is my birthdate",
            "favorite_color": "what is my favorite color",
        }
        tq = tool_queries.get(slot)
        if tq:
            tool_candidates: List[str] = []
            try:
                tool_out = self.tools.run("memory_read", {"query": tq, "top_k": 3})
                if isinstance(tool_out, dict) and tool_out.get("ok"):
                    for item in tool_out.get("results", []) or []:
                        raw = str(item.get("text") or "")
                        s = self._sanitize_memory_fact_line(raw)
                        if s:
                            tool_candidates.append(s)
            except Exception:
                traceback.print_exc()

            hit = _first_slot_match(tool_candidates)
            if hit:
                return hit

        return ""

    def _select_memory_facts_for_parts(self, parts: List[str], facts: List[str]) -> Tuple[List[str], List[str]]:
        if not parts:
            picked = [facts[0]] if facts else []
            return picked, []

        selected: List[str] = []
        missing: List[str] = []
        used = set()
        stop = {"color", "date", "birthdate", "birthday", "eyes", "eye"}

        for part in parts:
            tokens = [t for t in re.findall(r"[a-z0-9]+", part.lower()) if len(t) > 2 and t not in stop]
            if not tokens:
                tokens = [part.lower()]

            hit = ""
            for idx, fact in enumerate(facts):
                if idx in used:
                    continue
                low = fact.lower()
                def _tok_variants(tok: str) -> List[str]:
                    base = tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok
                    return list({tok, base, f"{base}s"})

                if any(any(v in low for v in _tok_variants(tok)) for tok in tokens) or part.lower() in low:
                    hit = fact
                    used.add(idx)
                    break

            if hit:
                selected.append(hit)
            else:
                missing.append(part)

        return selected, missing

    def _sanitize_unique_facts(self, lines: List[str]) -> List[str]:
        clean_facts: List[str] = []
        seen = set()
        for ln in lines:
            s = self._sanitize_memory_fact_line(str(ln or ""))
            if not s:
                continue
            norm = " ".join(s.lower().split())
            if norm in seen:
                continue
            seen.add(norm)
            clean_facts.append(s)
        return clean_facts

    def _memory_read_first_fact(self, query: str, top_k: int = 1) -> str:
        try:
            tool_out = self.tools.run("memory_read", {"query": query, "top_k": top_k})
            if isinstance(tool_out, dict) and tool_out.get("ok"):
                first = (tool_out.get("results", []) or [{}])[0]
                return self._sanitize_memory_fact_line(str(first.get("text") or ""))
        except Exception:
            traceback.print_exc()
        return ""

    def _build_memory_read_reply(
        self,
        messages: List[Dict[str, str]],
        user_query: str,
        formatted: str,
    ) -> str:
        raw_lines = [
            ln.strip()
            for ln in (formatted or "").splitlines()
            if ln.strip()
        ]
        clean_facts = self._sanitize_unique_facts(raw_lines)

        slots = self._extract_memory_slots(user_query)
        used = set()
        selected: List[str] = []
        missing: List[str] = []

        if any(slots.values()):
            name_fact = ""
            birth_fact = ""
            eye_fact = ""
            favorite_fact = ""

            if slots.get("name"):
                for idx, fact in enumerate(clean_facts):
                    if idx in used:
                        continue
                    if self._slot_fact_matches("name", fact):
                        name_fact = fact
                        used.add(idx)
                        break
                if not name_fact:
                    name_fact = self._lookup_slot_fact("name", user_query)
                if name_fact:
                    selected.append(name_fact)
                else:
                    missing.append("name")

            if slots.get("birthdate") or slots.get("age"):
                for idx, fact in enumerate(clean_facts):
                    if idx in used:
                        continue
                    if self._slot_fact_matches("birthdate", fact):
                        birth_fact = fact
                        used.add(idx)
                        break
                if not birth_fact:
                    birth_fact = self._lookup_slot_fact("birthdate", user_query)
                if not birth_fact and slots.get("age"):
                    birth_fact = self._memory_read_first_fact(
                        query="what is my birthdate",
                        top_k=1,
                    )
                if birth_fact and slots.get("birthdate"):
                    selected.append(birth_fact)
                if not birth_fact and slots.get("birthdate"):
                    missing.append("birthdate")

            if slots.get("eye_color"):
                for idx, fact in enumerate(clean_facts):
                    if idx in used:
                        continue
                    if self._slot_fact_matches("eye_color", fact):
                        eye_fact = fact
                        used.add(idx)
                        break
                if not eye_fact:
                    eye_fact = self._lookup_slot_fact("eye_color", user_query)
                if eye_fact:
                    selected.append(eye_fact)
                else:
                    missing.append("eye color")

            if slots.get("favorite_color"):
                for idx, fact in enumerate(clean_facts):
                    if idx in used:
                        continue
                    if self._slot_fact_matches("favorite_color", fact):
                        favorite_fact = fact
                        used.add(idx)
                        break
                if not favorite_fact:
                    favorite_fact = self._lookup_slot_fact("favorite_color", user_query)
                if favorite_fact:
                    selected.append(favorite_fact)
                else:
                    missing.append("favorite color")

            age_sentence = ""
            if slots.get("age"):
                if birth_fact:
                    bdt = self._parse_birthdate_from_text(birth_fact)
                    if bdt:
                        age_details = self._compute_age_details(bdt)
                        if age_details.get("ok"):
                            months_val = int(age_details.get("months") or 0)
                            month_unit = "month" if months_val == 1 else "months"
                            days_val = int(age_details.get("days") or 0)
                            day_unit = "day" if days_val == 1 else "days"
                            age_sentence = (
                                f"Age: {age_details.get('years')} years, {months_val} {month_unit}, and {days_val} {day_unit} "
                                f"(about {age_details.get('total_days'):,} days / {age_details.get('total_minutes'):,} minutes)."
                            )
                            current_dt_line = f"Current datetime: {age_details.get('now_text')}."
                        else:
                            current_dt_line = f"Current datetime: {age_details.get('now_text')}."
                            age_sentence = "Age: I cannot compute from a future birthdate."
                    else:
                        missing.append("enough birthdate detail to compute age")
                else:
                    missing.append("birthdate to compute age")
                    current_dt_line = f"Current datetime: {datetime.now().strftime('%Y-%m-%d %H:%M')}."
            else:
                current_dt_line = ""
        else:
            if not clean_facts:
                return "Gremlin memory ping: I do not have that in memory yet."
            parts = self._extract_memory_request_parts(user_query)
            selected, missing = self._select_memory_facts_for_parts(parts, clean_facts)
            if not selected:
                selected = [clean_facts[0]]
            age_sentence = ""
            current_dt_line = ""

        selected_for_sentence = [re.sub(r"[.!?]+$", "", s).strip() for s in selected[:2] if s.strip()]
        fact_clause = " and ".join(selected_for_sentence[:2]).strip()

        uniq_missing = []
        seen_missing = set()
        for m in missing:
            k = m.strip().lower()
            if not k or k in seen_missing:
                continue
            seen_missing.add(k)
            uniq_missing.append(m)

        missing_clause = ""
        if uniq_missing:
            missing_clause = "I do not have your " + ", ".join(uniq_missing) + " in memory yet."

        missing_clause_no_dot = missing_clause.rstrip(".") if missing_clause else ""

        # Multipart response style for slot-based memory questions.
        if any(slots.values()):
            anchor_lines = self._build_memory_fact_anchor_lines(
                user_query=user_query,
                name_fact=name_fact,
                eye_fact=eye_fact,
                favorite_fact=favorite_fact,
                birth_fact=birth_fact,
                age_sentence=age_sentence,
            )
            self._reinforce_memory_texts(
                [fact for fact in [name_fact, eye_fact, favorite_fact, birth_fact] if fact],
                add_tags=["reinforced_recall", "authoritative_recall"] if any([name_fact, eye_fact, favorite_fact, birth_fact]) else ["reinforced_recall"],
            )
            lines: List[str] = [
                "Gremlin memory check, incoming. *gears whir softly*",
            ]

            if name_fact:
                name_value = self._extract_name_value(name_fact)
                if name_value:
                    lines.append(f"Name: {name_value}.")
                else:
                    name_line = self._normalize_memory_reply_perspective(user_query, name_fact)
                    name_line = re.sub(r"[.!?]+$", "", name_line).strip()
                    lines.append(f"Name: {name_line}.")
            elif slots.get("name"):
                lines.append("Name: I do not have that in memory yet.")

            if eye_fact:
                eye_line = self._normalize_memory_reply_perspective(user_query, eye_fact)
                eye_line = re.sub(r"[.!?]+$", "", eye_line).strip()
                lines.append(f"Eye color: {eye_line}.")
            elif slots.get("eye_color"):
                lines.append("Eye color: I do not have that in memory yet.")
            if favorite_fact:
                fav_line = self._normalize_memory_reply_perspective(user_query, favorite_fact)
                fav_line = re.sub(r"[.!?]+$", "", fav_line).strip()
                lines.append(f"Favorite color: {fav_line}.")
            elif slots.get("favorite_color"):
                lines.append("Favorite color: I do not have that in memory yet.")

            if birth_fact:
                bdt = self._parse_birthdate_from_text(birth_fact)
                if bdt:
                    lines.append(f"Birthdate: {self._format_birthdate_pretty(bdt)}.")
                else:
                    birth_line = self._normalize_memory_reply_perspective(user_query, birth_fact)
                    birth_line = re.sub(r"[.!?]+$", "", birth_line).strip()
                    lines.append(f"Birthdate: {birth_line}.")
            elif slots.get("birthdate"):
                lines.append("Birthdate: I do not have that in memory yet.")

            if slots.get("age"):
                if current_dt_line:
                    lines.append(current_dt_line)
                if age_sentence:
                    lines.append(age_sentence)
                elif not birth_fact:
                    lines.append("Age: I need your birthdate in memory to compute this.")

            if self._is_transformative_memory_query(user_query):
                fact_constraints: List[str] = []
                if name_fact:
                    name_value = self._extract_name_value(name_fact)
                    fact_constraints.append(f"Name: {name_value}." if name_value else f"Name fact: {name_fact}")
                if eye_fact:
                    fact_constraints.append(f"Eye color fact: {self._normalize_memory_reply_perspective(user_query, eye_fact)}")
                if favorite_fact:
                    fact_constraints.append(f"Favorite color fact: {self._normalize_memory_reply_perspective(user_query, favorite_fact)}")
                if birth_fact:
                    bdt = self._parse_birthdate_from_text(birth_fact)
                    if bdt:
                        fact_constraints.append(f"Birthdate: {self._format_birthdate_pretty(bdt)}.")
                    else:
                        fact_constraints.append(f"Birthdate fact: {self._normalize_memory_reply_perspective(user_query, birth_fact)}")
                if age_sentence:
                    fact_constraints.append(age_sentence)

                creative = self._render_memory_transform_reply(
                    messages=messages,
                    user_query=user_query,
                    fact_lines=fact_constraints,
                    missing_lines=missing,
                )
                if creative:
                    return self._enforce_memory_fact_anchors(creative, anchor_lines)

            return "\n".join(lines)

        if self._is_broad_memory_recall_query(user_query):
            picks = clean_facts[:5]
            if not picks:
                return "Gremlin memory ping: I do not have that in memory yet."

            self._reinforce_memory_texts(picks, add_tags=["reinforced_recall"])

            lines = ["Gremlin memory check, incoming. Here's what I remember:"]
            for p in picks:
                s = self._normalize_memory_reply_perspective(user_query, p)
                s = re.sub(r"[.!?]+$", "", s).strip()
                if not s:
                    continue
                lines.append(f"- {s}.")
            return "\n".join(lines)

        if fact_clause and missing_clause:
            first_sentence = f"Gremlin memory ping: {fact_clause}; {missing_clause_no_dot}."
        elif fact_clause:
            first_sentence = f"Gremlin memory ping: {fact_clause}."
        elif missing_clause:
            first_sentence = f"Gremlin memory ping: {missing_clause}"
        else:
            first_sentence = "Gremlin memory ping: I do not have that in memory yet."

        second_sentence = age_sentence if age_sentence else ""
        merged = " ".join([x for x in [first_sentence, second_sentence] if x]).strip()
        merged = self._normalize_memory_reply_perspective(user_query, merged)
        return merged

    def _generate_file_content_from_intent(
        self,
        user_input: str,
        target_path: str,
    ) -> str:
        """
        Generate file content from a natural-language request when no explicit
        content block is provided by the user.
        """
        try:
            ext = os.path.splitext(target_path or "")[1].lower()
            lang_hint = {
                ".py": "Python",
                ".ps1": "PowerShell",
                ".json": "JSON",
                ".md": "Markdown",
                ".txt": "plain text",
                ".js": "JavaScript",
                ".ts": "TypeScript",
                ".html": "HTML",
                ".css": "CSS",
                ".yml": "YAML",
                ".yaml": "YAML",
            }.get(ext, "text")

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You generate file contents for local coding tasks. "
                        "Return ONLY the file content with no markdown fences, "
                        "no explanations, and no surrounding prose."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_input}\n"
                        f"Target path: {target_path}\n"
                        f"Expected language/type: {lang_hint}\n"
                        "Generate the full file content now."
                    ),
                },
            ]

            reply = self.model.chat(messages=messages, temperature=0.2)
            text = self._extract_text(reply)
            return self._strip_code_fences(text)

        except Exception:
            traceback.print_exc()
            return ""

    def _extract_reply_code_block(self, reply: Dict[str, Any], target_path: str) -> str:
        text = self._extract_text(reply)
        if not text:
            return ""
        return self._extract_code_block_from_text(text, target_path=target_path)

    def _artifact_request_keywords(self, user_input: str, target_path: str) -> List[str]:
        raw = f"{user_input or ''} {os.path.splitext(os.path.basename(target_path or ''))[0]}".lower()
        words = re.findall(r"[a-z]{3,}", raw)
        stop = {
            "mina", "travis", "please", "lets", "let", "make", "create", "build", "write", "save",
            "generate", "program", "script", "file", "named", "called", "with", "that", "this", "into",
            "from", "your", "just", "have", "screen", "character", "characters", "python",
        }
        ordered: List[str] = []
        for word in words:
            if word in stop:
                continue
            if word not in ordered:
                ordered.append(word)
        return ordered[:8]

    def _looks_generic_file_stub(self, target_path: str, content: str) -> bool:
        ext = os.path.splitext(str(target_path or ""))[1].lower()
        low = str(content or "").strip().lower()
        if not low:
            return True
        if ext != ".py":
            return False

        line_count = len([ln for ln in low.splitlines() if ln.strip()])
        generic_markers = [
            "hello world",
            "hello, ",
            "def say_hello",
            "pass",
            "todo",
        ]
        if any(marker in low for marker in generic_markers):
            return True
        if line_count <= 6 and "__name__ == \"__main__\"" in low:
            return True
        return False

    def _matches_python_countdown_request(self, user_input: str, content: str) -> bool:
        request = str(user_input or "").lower()
        body = str(content or "").lower()
        if not request or not body:
            return False

        countdown_signal = (
            "countdown" in request
            or "count down" in request
            or "counts down" in request
            or "from 10 to 0" in request
        )
        if not countdown_signal:
            return False

        has_descending_range = "range(10, -1, -1)" in body
        has_sleep = "time.sleep(" in body
        has_countdown_name = "countdown" in body
        return has_descending_range and (has_sleep or has_countdown_name)

    def _content_matches_artifact_intent(self, user_input: str, target_path: str, content: str) -> bool:
        text = str(content or "").strip()
        if not text:
            return False
        if self._looks_generic_file_stub(target_path, text):
            return False

        ext = os.path.splitext(str(target_path or ""))[1].lower()
        if ext != ".py":
            return True

        if self._matches_python_countdown_request(user_input, text):
            return True

        keywords = self._artifact_request_keywords(user_input, target_path)
        if not keywords:
            return True

        low = text.lower()
        matched = [kw for kw in keywords if kw in low]
        rich_request = len(keywords) >= 3
        if rich_request and not matched:
            return False
        return True

    def _is_save_followup_without_explicit_content(self, user_input: str) -> bool:
        text = str(user_input or "")
        lowered = text.lower()
        if not lowered.strip():
            return False
        if re.search(r"\b(save|write)\b", lowered) is None:
            return False
        if re.search(r"\b(script|file)\b", lowered) is None:
            return False
        if "```" in text:
            return False
        explicit_content_markers = [
            "with content",
            "that says",
            "that prints",
            "print ",
            "contains ",
        ]
        return not any(marker in lowered for marker in explicit_content_markers)

    def _resolve_file_write_content_candidate(
        self,
        user_input: str,
        target_path: str,
        existing_content: str,
        reply: Dict[str, Any],
    ) -> Tuple[str, str]:
        existing = str(existing_content or "").strip()

        stored = self._get_last_draft_artifact()
        if isinstance(stored, dict):
            stored_path = str(stored.get("target_path") or "").strip()
            stored_content = str(stored.get("content") or "")
            stored_request = str(stored.get("user_input") or user_input or "").strip()
            target_name = os.path.basename(str(target_path or "")).strip().lower()
            stored_name = os.path.basename(stored_path).strip().lower()
            same_target = bool(target_name) and target_name == stored_name
            if (
                same_target
                and self._is_save_followup_without_explicit_content(user_input)
                and stored_content.strip()
                and not self._looks_generic_file_stub(target_path, stored_content)
            ):
                return stored_content, "stored_draft"

        if self._content_matches_artifact_intent(user_input, target_path, existing):
            return existing, "existing"

        drafted = self._extract_reply_code_block(reply, target_path)
        if self._content_matches_artifact_intent(user_input, target_path, drafted):
            return drafted, "reply_code"

        if isinstance(stored, dict):
            stored_path = str(stored.get("target_path") or "").strip()
            stored_content = str(stored.get("content") or "")
            stored_request = str(stored.get("user_input") or user_input or "").strip()
            target_name = os.path.basename(str(target_path or "")).strip().lower()
            stored_name = os.path.basename(stored_path).strip().lower()
            same_target = bool(target_name) and target_name == stored_name
            if same_target and self._content_matches_artifact_intent(stored_request, target_path, stored_content):
                return stored_content, "stored_draft"

        remembered = self._search_code_artifact_memory(target_path=target_path, user_input=user_input)
        if isinstance(remembered, dict):
            remembered_request = str(remembered.get("user_input") or user_input or "").strip()
            remembered_content = str(remembered.get("content") or "")
            if self._content_matches_artifact_intent(remembered_request, target_path, remembered_content):
                return remembered_content, "memory_artifact"

        generated = self._generate_file_content_from_intent(
            user_input=user_input,
            target_path=target_path,
        )
        if self._content_matches_artifact_intent(user_input, target_path, generated):
            return generated, "generated"

        return "", "artifact_payload_hijack"

    def _remember_artifact_payload_hijack(
        self,
        user_input: str,
        target_path: str,
    ) -> None:
        try:
            lesson = (
                "Artifact write lesson: when a concrete file request names required behavior or imagery, "
                "do not write a generic stub. Block the write and regenerate content that matches the requested artifact. "
                f"Target={target_path}; request={self._compress_multiline(user_input, max_lines=2, max_chars=260)}"
            )
            self.memory.add_memory(
                lesson,
                kind="note",
                tags=["user_memory", "learning_lesson", "routing_correction", "artifact_payload_hijack"],
            )
        except Exception:
            traceback.print_exc()

    def _normalize_path(self, path: str) -> str:
        p = (path or "").strip().strip('"\'')
        if not p:
            return ""
        p = os.path.expanduser(os.path.expandvars(p))
        if os.path.isabs(p):
            return os.path.abspath(p)

        workspace_root = os.path.abspath(str(getattr(self, "workspace_root", "") or ""))
        project_root = os.path.abspath(str(getattr(self, "project_root", "") or os.path.join(os.path.dirname(__file__), "..")))

        workspace_candidate = os.path.abspath(os.path.join(workspace_root, p)) if workspace_root else ""
        project_candidate = os.path.abspath(os.path.join(project_root, p)) if project_root else ""

        # Guard: if a relative path already exists under either root, resolve to
        # the existing target so reads do not miss project files.
        if workspace_candidate and os.path.exists(workspace_candidate):
            return workspace_candidate
        if project_candidate and os.path.exists(project_candidate):
            return project_candidate

        # Default new relative paths to personal workspace root.
        return workspace_candidate or project_candidate or os.path.abspath(p)

    def _extract_path_from_text(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        quoted = re.findall(r'["\']([^"\']+)["\']', raw)
        for q in quoted:
            if re.search(r'^[A-Za-z]:\\', q) or re.search(r'^[.]{1,2}[\\/]', q):
                return self._normalize_path(q)

        m = re.search(r'([A-Za-z]:\\[^\n\r]+)', raw)
        if m:
            candidate = m.group(1).strip().rstrip('.,;:')
            return self._normalize_path(candidate)

        return ""

    def _remember_active_project_path(self, path: str) -> None:
        p = self._normalize_path(path)
        if not p:
            return
        try:
            if os.path.isfile(p):
                p = os.path.dirname(p)
            if os.path.isdir(p):
                self.last_active_project_path = p
        except Exception:
            traceback.print_exc()

    def _run_project_tests(self, request: str) -> Dict[str, Any]:
        req = (request or "").strip()
        target = self._extract_path_from_text(req)

        low = req.lower()
        if not target and any(x in low for x in ["this project", "current project", "that project"]):
            target = self.last_active_project_path or ""

        if not target:
            target = self._infer_project_path_from_recent_memory()

        if not target:
            maybe_last = self.startup_context.get("last_worked_project") if isinstance(self.startup_context, dict) else None
            if isinstance(maybe_last, dict):
                target = str(maybe_last.get("path") or "").strip()

        target = self._normalize_path(target) if target else ""
        if not target:
            return {"ok": False, "result": None, "error": "no_project_path_detected"}

        if os.path.isfile(target):
            target = os.path.dirname(target)

        if not os.path.isdir(target):
            return {"ok": False, "result": None, "error": f"project_path_not_found: {target}"}

        self._remember_active_project_path(target)

        py_exec = sys.executable if (sys.executable and os.path.isfile(sys.executable)) else "python"
        cmd = [py_exec, "-m", "pytest", "-q"]

        try:
            proc = subprocess.run(
                cmd,
                cwd=target,
                capture_output=True,
                text=True,
                timeout=180,
            )

            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()

            changed_files: List[str] = []
            retried = False

            if proc.returncode != 0 and "fix" in low:
                changed_files = self._attempt_basic_test_autofix(target, stdout, stderr)
                if changed_files:
                    retried = True
                    proc = subprocess.run(
                        cmd,
                        cwd=target,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    stdout = (proc.stdout or "").strip()
                    stderr = (proc.stderr or "").strip()

            return {
                "ok": proc.returncode == 0,
                "result": {
                    "project_path": target,
                    "command": " ".join(cmd),
                    "exit_code": proc.returncode,
                    "retried_after_fix": retried,
                    "changed_files": changed_files,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                "error": None if proc.returncode == 0 else (stderr or "pytest_failed"),
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "result": {
                    "project_path": target,
                    "command": " ".join(cmd),
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                },
                "error": "pytest_timeout",
            }
        except Exception as e:
            return {
                "ok": False,
                "result": None,
                "error": f"pytest_exception: {str(e)}",
            }

    def _attempt_basic_test_autofix(self, project_path: str, stdout: str, stderr: str) -> List[str]:
        changed: List[str] = []
        combined = f"{stdout}\n{stderr}".lower()

        try:
            # Common scaffold mismatch: test imports main() but generated file only prints at import time.
            if "cannot import name 'main' from 'src.main'" in combined:
                main_py = os.path.join(project_path, "src", "main.py")
                if os.path.isfile(main_py):
                    with open(main_py, "r", encoding="utf-8") as f:
                        src = f.read()

                    if re.search(r"^\s*def\s+main\s*\(", src, flags=re.MULTILINE) is None:
                        msg = "'todo cli ready'"
                        m = re.search(r"print\((.+?)\)", src)
                        if m:
                            msg = m.group(1).strip()

                        fixed = (
                            "def main() -> int:\n"
                            f"    print({msg})\n"
                            "    return 0\n\n\n"
                            "if __name__ == '__main__':\n"
                            "    raise SystemExit(main())\n"
                        )

                        with open(main_py, "w", encoding="utf-8") as f:
                            f.write(fixed)

                        changed.append(main_py)
        except Exception:
            traceback.print_exc()

        return changed

    def _repair_escaped_python_file(self, path: str) -> Dict[str, Any]:
        p = self._normalize_path(path)
        if not p:
            return {"ok": False, "result": None, "error": "no_path_provided"}
        if not os.path.isfile(p):
            return {"ok": False, "result": None, "error": f"file_not_found: {p}"}

        try:
            with open(p, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            return {"ok": False, "result": None, "error": f"read_failed: {e}"}

        trimmed = original.strip()
        changed = False
        repaired = original

        def _decode_escaped_source(payload: str) -> str:
            # Decode common escaped source blobs (\n, \t, \"), preserving UTF-8 text.
            return bytes(payload, "utf-8").decode("unicode_escape")

        # Common failure form: an entire Python file saved as one quoted string
        # containing escaped newlines, e.g. 'import os\nprint("hi")'.
        if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
            if any(tok in trimmed for tok in ["\\n", "\\r", "\\t", "\\\""]):
                try:
                    parsed = ast.literal_eval(trimmed)
                    if isinstance(parsed, str) and parsed:
                        repaired = parsed
                        changed = repaired != original
                except Exception:
                    pass

        # Fallback for malformed single-quoted blobs that never closed, e.g.
        # 'import os\nimport tkinter as tk\nfrom tkinter import filedialog
        if not changed and trimmed[:1] in {'"', "'"} and any(tok in trimmed for tok in ["\\n", "\\r", "\\t"]):
            body = trimmed[1:]
            if body and body[-1] in {'"', "'"}:
                body = body[:-1]
            try:
                decoded = _decode_escaped_source(body)
                if decoded.strip():
                    repaired = decoded
                    changed = repaired != original
            except Exception:
                pass

        if not changed:
            return {
                "ok": False,
                "result": {
                    "path": p,
                    "repaired": False,
                },
                "error": "no_escaped_string_wrapper_detected",
            }

        try:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(repaired)
            py_compile.compile(p, doraise=True)
        except Exception as e:
            return {
                "ok": False,
                "result": {
                    "path": p,
                    "repaired": False,
                },
                "error": f"repair_write_or_compile_failed: {e}",
            }

        return {
            "ok": True,
            "result": {
                "path": p,
                "repaired": True,
                "bytes_before": len(original),
                "bytes_after": len(repaired),
            },
            "error": None,
        }

    def _infer_project_path_from_recent_memory(self) -> str:
        candidates: List[str] = []

        try:
            recent = self.memory.recent_memories(top_k=120)
        except Exception:
            recent = []

        for item in recent or []:
            txt = str(item.get("text") or "")
            if not txt:
                continue

            for m in re.finditer(r'([A-Za-z]:\\[^\s\"\']+)', txt):
                p = m.group(1).strip().rstrip('.,;:')
                p = self._normalize_path(p)
                if os.path.isfile(p):
                    p = os.path.dirname(p)
                if not os.path.isdir(p):
                    continue

                has_tests = os.path.isdir(os.path.join(p, "tests"))
                has_src = os.path.isdir(os.path.join(p, "src"))
                if has_tests or has_src:
                    candidates.append(p)

        if not candidates:
            return ""

        # Prefer latest mention; dedupe while preserving order.
        seen = set()
        ordered = []
        for p in reversed(candidates):
            if p in seen:
                continue
            seen.add(p)
            ordered.append(p)

        return ordered[0] if ordered else ""

    def _ensure_workspace_structure(self) -> None:
        try:
            os.makedirs(self.workspace_root, exist_ok=True)
            for d in self.workspace_default_dirs:
                os.makedirs(os.path.join(self.workspace_root, d), exist_ok=True)
        except Exception:
            traceback.print_exc()

    def _projects_root(self) -> str:
        return os.path.join(self.workspace_root, "projects")

    def _autonomy_root(self) -> str:
        return os.path.join(self.workspace_root, "autonomy")

    def _autonomy_audit_path(self) -> str:
        return os.path.join(self._autonomy_root(), "audit.jsonl")

    def _watchdog_root(self) -> str:
        configured = self.config.get(
            "curiosity",
            "watchdog_dir",
            os.path.join(self.workspace_root, "mina_watchdog"),
        )
        return os.path.abspath(os.path.expandvars(os.path.expanduser(str(configured))))

    def _watchdog_candidate_path(self) -> str:
        return os.path.join(self._watchdog_root(), "candidate.json")

    def _write_watchdog_candidate(self, candidate: Dict[str, Any]) -> None:
        root = self._watchdog_root()
        os.makedirs(root, exist_ok=True)
        path = self._watchdog_candidate_path()
        temporary = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        payload = dict(candidate)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
        os.replace(temporary, path)

    def _active_watchdog_candidate(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self._watchdog_candidate_path(), "r", encoding="utf-8") as handle:
                candidate = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(candidate, dict):
            return None
        if str(candidate.get("phase") or "") in {
            "applying",
            "pending_restart",
            "restarting",
            "validating",
            "rolling_back",
            "manual_intervention",
        }:
            return candidate
        return None

    @staticmethod
    def _autonomous_path_within(path: str, root: str) -> bool:
        candidate = os.path.normcase(os.path.abspath(str(path or "")))
        boundary = os.path.normcase(os.path.abspath(str(root or "")))
        if not candidate or not boundary:
            return False
        try:
            return os.path.commonpath([candidate, boundary]) == boundary
        except ValueError:
            return False

    def _autonomous_project_roots(self) -> List[str]:
        configured = self.config.get("curiosity", "project_roots", [os.path.dirname(os.path.dirname(__file__))])
        if not isinstance(configured, list):
            configured = [configured]
        roots: List[str] = []
        for raw in configured:
            value = str(raw or "").strip()
            if not value:
                continue
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(value)))
            if path and os.path.isdir(path) and path not in roots:
                roots.append(path)
        return roots

    def _completed_autonomy_keys(self) -> set:
        path = self._autonomy_audit_path()
        completed = set()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if (
                        bool(row.get("ok"))
                        and self._autonomy_row_has_verified_completion(row)
                        and str(row.get("task_key") or "").strip()
                    ):
                        completed.add(str(row.get("task_key")).strip())
        except FileNotFoundError:
            pass
        return completed

    def _autonomy_row_has_verified_completion(self, row: Dict[str, Any]) -> bool:
        if not isinstance(row, dict):
            return False

        verification = row.get("verification")
        if isinstance(verification, dict):
            if "completion_verified" in verification:
                return bool(verification.get("completion_verified"))

        # Legacy compatibility: only trust legacy rows that recorded a real applied patch.
        patch = row.get("patch")
        if isinstance(patch, dict):
            if bool(patch.get("applied")) and bool(patch.get("ok", True)):
                return True

        return False

    def _autonomous_work_candidates(self) -> List[Dict[str, Any]]:
        completed = self._completed_autonomy_keys()
        candidates: List[Dict[str, Any]] = []
        todo_paths = self.config.get(
            "curiosity",
            "todo_paths",
            [os.path.join(os.path.dirname(os.path.dirname(__file__)), "TODO.md")],
        )
        if not isinstance(todo_paths, list):
            todo_paths = [todo_paths]

        for raw_path in todo_paths:
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(raw_path or "").strip())))
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except Exception:
                continue
            for index, line in enumerate(lines):
                match = re.match(r"^\s*- \[ \]\s+(.+?)\s*$", line)
                if not match:
                    continue
                title = match.group(1).strip().rstrip(":")
                details: List[str] = []
                for following in lines[index + 1:index + 12]:
                    if re.match(r"^\s*- \[[ xX]\]\s+", following) or following.startswith("### "):
                        break
                    detail_match = re.match(r"^\s{2,}-\s+(.+?)\s*$", following)
                    if detail_match:
                        details.append(detail_match.group(1).strip())
                key = "todo:" + hashlib.sha1(f"{path}:{title}".encode("utf-8")).hexdigest()[:16]
                if key not in completed:
                    candidates.append({
                        "key": key,
                        "title": title,
                        "details": details,
                        "source": "todo",
                        "reference": path,
                        "project_root": next(
                            (root for root in self._autonomous_project_roots() if self._autonomous_path_within(path, root)),
                            "",
                        ),
                    })

        tracker = self._load_project_tracker()
        for project in self._projects_to_complete(tracker):
            project_name = str(project.get("name") or "project").strip()
            project_root = str(project.get("path") or "").strip()
            for step in project.get("next_steps", []) if isinstance(project.get("next_steps"), list) else []:
                title = str(step or "").strip()
                if not title:
                    continue
                key = "tracker:" + hashlib.sha1(f"{project_name}:{title}".encode("utf-8")).hexdigest()[:16]
                if key not in completed:
                    candidates.append({
                        "key": key,
                        "title": title,
                        "details": [f"Project: {project_name}"],
                        "source": "project_tracker",
                        "reference": self._project_tracker_path(),
                        "project_root": project_root,
                    })
        return candidates

    def _autonomous_context_files(self, task: Dict[str, Any], project_root: str) -> List[Dict[str, str]]:
        task_text = " ".join([
            str(task.get("title") or ""),
            " ".join(task.get("details", []) if isinstance(task.get("details"), list) else []),
        ]).lower()
        tokens = {
            token for token in re.findall(r"[a-z][a-z0-9_]{3,}", task_text)
            if token not in TOPIC_STOP_WORDS
        }
        scored: List[Tuple[int, str]] = []
        skipped_dirs = {".git", ".gradle", ".venv", "__pycache__", "build", "node_modules", "logs", "tmp"}
        allowed_exts = {".py", ".ps1", ".kt", ".kts", ".json", ".md", ".js", ".jsx", ".ts", ".tsx"}
        scanned = 0
        for current, dirs, files in os.walk(project_root):
            dirs[:] = [name for name in dirs if name.lower() not in skipped_dirs]
            for name in files:
                scanned += 1
                if scanned > 1500:
                    break
                path = os.path.join(current, name)
                if os.path.splitext(name)[1].lower() not in allowed_exts:
                    continue
                relative = os.path.relpath(path, project_root).lower()
                score = sum(2 if token in os.path.basename(relative) else 1 for token in tokens if token in relative)
                if score:
                    scored.append((score, path))
            if scanned > 1500:
                break
        reference = str(task.get("reference") or "")
        if os.path.isfile(reference):
            scored.append((100, reference))
        context: List[Dict[str, str]] = []
        seen = set()
        for _score, path in sorted(scored, key=lambda item: (-item[0], item[1])):
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            inspected = inspect_file(path, max_chars=5000)
            result = inspected.get("result") if isinstance(inspected, dict) else None
            content = str(result.get("content") or "") if isinstance(result, dict) else ""
            if content:
                context.append({"path": path, "content": content})
            if len(context) >= 6:
                break
        return context

    def _parse_autonomous_model_result(self, text: str) -> Dict[str, Any]:
        cleaned = str(text or "").strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else cleaned
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {"summary": cleaned[:4000], "confidence": 0.0, "patch": ""}

    def _validate_autonomous_patch_paths(self, patch: str, project_root: str) -> Tuple[bool, List[str], str]:
        paths = re.findall(r"^\+\+\+\s+(?:b/)?(.+?)\s*$", patch, flags=re.MULTILINE)
        relative_paths = [path.strip() for path in paths if path.strip() != "/dev/null"]
        if not relative_paths:
            return False, [], "patch_has_no_files"
        if re.search(r"^(?:deleted file mode|rename from|rename to|Binary files)", patch, re.MULTILINE):
            return False, [], "destructive_or_binary_patch_not_allowed"
        allowed_exts = {".py", ".ps1", ".kt", ".kts", ".json", ".md", ".js", ".jsx", ".ts", ".tsx"}
        protected_paths = {
            "config/mk1_config.json",
            "mina_watchdog.ps1",
            "start_mk1_api.ps1",
            "stop_mk1_api.ps1",
        }
        for relative in relative_paths:
            normalized_relative = relative.replace("\\", "/").lstrip("./").lower()
            if (
                normalized_relative in protected_paths
                or normalized_relative.startswith("restore/")
                or normalized_relative.startswith("watchdog/")
            ):
                return False, [], f"patch_path_is_recovery_infrastructure:{relative}"
            target = os.path.abspath(os.path.join(project_root, relative))
            try:
                if not self._autonomous_path_within(target, project_root):
                    return False, [], "patch_path_outside_project"
            except ValueError:
                return False, [], "patch_path_outside_project"
            if os.path.splitext(target)[1].lower() not in allowed_exts or os.path.basename(target).lower() in {".env", ".npmrc"}:
                return False, [], "patch_file_type_not_allowed"
            status = subprocess.run(
                ["git", "-C", project_root, "status", "--porcelain", "--", relative],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if status.stdout.strip():
                return False, [], f"patch_target_has_existing_changes:{relative}"
        return True, relative_paths, ""

    def _apply_autonomous_patch(self, patch: str, project_root: str) -> Dict[str, Any]:
        active_candidate = self._active_watchdog_candidate()
        if active_candidate is not None:
            return {
                "ok": False,
                "applied": False,
                "error": "watchdog_candidate_already_active",
                "candidate_id": active_candidate.get("candidate_id"),
                "files": [],
            }
        allowed, relative_paths, error = self._validate_autonomous_patch_paths(patch, project_root)
        if not allowed:
            return {"ok": False, "applied": False, "error": error, "files": relative_paths}
        created_files = [
            relative for relative in relative_paths
            if not os.path.exists(os.path.join(project_root, relative))
        ]
        os.makedirs(self._autonomy_root(), exist_ok=True)
        patch_path = os.path.join(self._autonomy_root(), f"patch_{int(time.time())}_{threading.get_ident()}.diff")
        with open(patch_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(patch)
        try:
            check = subprocess.run(
                ["git", "-C", project_root, "apply", "--check", patch_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if check.returncode != 0:
                return {"ok": False, "applied": False, "error": check.stderr.strip() or "git_apply_check_failed", "files": relative_paths}

            from restore.code_backup import create_code_snapshot

            backup_dir = Path(str(self.config.get(
                "curiosity",
                "code_backup_dir",
                os.path.join(self.workspace_root, "mina_code_backups"),
            )))
            safety_snapshot = create_code_snapshot(
                reason=f"pre_autonomous_patch:{','.join(relative_paths)}",
                project_root=Path(project_root),
                backup_dir=backup_dir,
            )
            if not safety_snapshot.get("ok"):
                return {
                    "ok": False,
                    "applied": False,
                    "error": f"pre_patch_snapshot_failed:{safety_snapshot.get('error')}",
                    "files": relative_paths,
                }
            candidate_id = hashlib.sha256(
                f"{time.time_ns()}:{patch}:{project_root}".encode("utf-8")
            ).hexdigest()[:24]
            candidate = {
                "schema": 1,
                "candidate_id": candidate_id,
                "phase": "applying",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "project_root": os.path.abspath(project_root),
                "snapshot": str(safety_snapshot.get("path") or ""),
                "files": relative_paths,
                "created_files": created_files,
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            }
            self._write_watchdog_candidate(candidate)
            applied = subprocess.run(
                ["git", "-C", project_root, "apply", patch_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if applied.returncode != 0:
                candidate["phase"] = "apply_failed"
                candidate["error"] = applied.stderr.strip() or "git_apply_failed"
                self._write_watchdog_candidate(candidate)
                return {"ok": False, "applied": False, "error": candidate["error"], "files": relative_paths, "safety_snapshot": safety_snapshot.get("path"), "candidate_id": candidate_id}

            validations = []
            diff_check = subprocess.run(
                ["git", "-C", project_root, "diff", "--check", "--", *relative_paths],
                capture_output=True,
                text=True,
                timeout=30,
            )
            validations.append({"name": "git_diff_check", "ok": diff_check.returncode == 0, "output": (diff_check.stdout + diff_check.stderr).strip()[:2000]})
            python_files = [os.path.join(project_root, path) for path in relative_paths if path.lower().endswith(".py")]
            if python_files:
                syntax_script = (
                    "import ast, pathlib, sys; "
                    "[ast.parse(pathlib.Path(p).read_text(encoding='utf-8'), filename=p) for p in sys.argv[1:]]"
                )
                compile_check = subprocess.run(
                    [sys.executable, "-c", syntax_script, *python_files],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                validations.append({"name": "python_ast", "ok": compile_check.returncode == 0, "output": (compile_check.stdout + compile_check.stderr).strip()[:2000]})
            changed_tests = [path for path in relative_paths if path.lower().endswith(".py") and os.path.basename(path).lower().startswith("test_")]
            if changed_tests:
                test_check = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", *changed_tests],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                validations.append({"name": "pytest_changed", "ok": test_check.returncode == 0, "output": (test_check.stdout + test_check.stderr).strip()[-4000:]})
            if not all(item["ok"] for item in validations):
                subprocess.run(["git", "-C", project_root, "apply", "-R", patch_path], capture_output=True, timeout=30)
                candidate["phase"] = "validation_failed_reverted"
                candidate["validations"] = validations
                self._write_watchdog_candidate(candidate)
                return {"ok": False, "applied": False, "error": "validation_failed_patch_reverted", "files": relative_paths, "validations": validations, "safety_snapshot": safety_snapshot.get("path"), "candidate_id": candidate_id}
            candidate["phase"] = "pending_restart"
            candidate["validations"] = validations
            self._write_watchdog_candidate(candidate)
            return {"ok": True, "applied": True, "files": relative_paths, "validations": validations, "patch_path": patch_path, "safety_snapshot": safety_snapshot.get("path"), "candidate_id": candidate_id, "watchdog_phase": "pending_restart"}
        finally:
            if os.path.exists(patch_path) and not self.config.get("curiosity", "keep_patch_files", True):
                os.remove(patch_path)

    def _write_autonomy_audit(self, row: Dict[str, Any]) -> None:
        os.makedirs(self._autonomy_root(), exist_ok=True)
        with open(self._autonomy_audit_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")

    def _autonomous_tool_status(self) -> Dict[str, Any]:
        allowed = ["web_search"]
        loaded = getattr(getattr(self, "tools", None), "tools", {})
        available = [name for name in allowed if name in loaded]
        return {
            "allowed": allowed,
            "available": available,
            "ready": available == allowed,
            "blocked_categories": [
                "code_execution",
                "file_mutation",
                "media_control",
                "messaging",
                "deployment",
                "system_control",
            ],
        }

    def _autonomous_work_status(self) -> Dict[str, Any]:
        curiosity = getattr(self, "curiosity", None)
        curiosity_status: Dict[str, Any] = {}
        try:
            if curiosity is not None and hasattr(curiosity, "status"):
                raw_status = curiosity.status()
                if isinstance(raw_status, dict):
                    curiosity_status = raw_status
        except Exception:
            traceback.print_exc()

        candidates = self._autonomous_work_candidates()
        preview: List[Dict[str, Any]] = []
        for item in candidates[:5]:
            if not isinstance(item, dict):
                continue
            preview.append({
                "key": str(item.get("key") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "reference": str(item.get("reference") or "").strip(),
            })

        return {
            "ok": True,
            "result": {
                "curiosity": curiosity_status,
                "autonomous_tool_status": self._autonomous_tool_status(),
                "pending_candidate_count": len(candidates),
                "pending_candidates": preview,
            },
            "error": None,
        }

    def _run_autonomous_work_once(self) -> Dict[str, Any]:
        candidates = self._autonomous_work_candidates()
        if not candidates:
            return {
                "ok": True,
                "result": {
                    "ran": False,
                    "reason": "no_pending_autonomous_tasks",
                    "pending_candidate_count": 0,
                },
                "error": None,
            }

        task = dict(candidates[0])
        task_result = self._run_autonomous_work_task(task)
        if not isinstance(task_result, dict):
            task_result = {
                "ok": False,
                "summary": str(task_result or "invalid_autonomous_result"),
                "error": "invalid_autonomous_result",
            }

        task_ok = bool(task_result.get("ok"))
        return {
            "ok": task_ok,
            "result": {
                "ran": True,
                "task": {
                    "key": str(task.get("key") or "").strip(),
                    "title": str(task.get("title") or "").strip(),
                    "source": str(task.get("source") or "").strip(),
                    "reference": str(task.get("reference") or "").strip(),
                },
                "task_result": task_result,
                "pending_candidate_count": max(0, len(candidates) - 1),
            },
            "error": None if task_ok else str(task_result.get("error") or "autonomous_work_failed"),
        }

    def _run_autonomous_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name != "web_search":
            return {"ok": False, "result": None, "error": f"autonomous_tool_not_allowed:{name}"}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "result": None, "error": "autonomous_web_search_query_required"}
        safe_args = {
            "query": query[:500],
            "max_results": max(1, min(3, int(args.get("max_results") or 3))),
            "timeout": max(1, min(20, int(args.get("timeout") or 15))),
            "include_content": False,
        }
        return self.tools.run("web_search", safe_args)

    def _run_autonomous_work_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        started_at = time.time()
        project_root = os.path.abspath(str(task.get("project_root") or "").strip())
        allowed_roots = self._autonomous_project_roots()
        if not project_root or not any(
            self._autonomous_path_within(project_root, root) for root in allowed_roots
        ):
            result = {"ok": False, "summary": "Task has no approved project root.", "error": "project_root_not_approved"}
            self._write_autonomy_audit({"ts": time.time(), "task_key": task.get("key"), **result})
            return result

        context_files = self._autonomous_context_files(task, project_root)
        context_text = "\n\n".join(
            f"FILE: {item['path']}\n{item['content']}" for item in context_files
        )
        research_text = ""
        title = str(task.get("title") or "").strip()
        if any(word in title.lower() for word in ["research", "latest", "current", "news"]):
            research = self._run_autonomous_tool("web_search", {"query": title, "max_results": 3, "timeout": 15})
            research_text = str(research)[:5000]

        prompt = (
            "You are Mina doing one bounded autonomous maintenance task while the user is away. "
            "Use only the supplied evidence. Return one JSON object with keys summary (string), confidence (0 to 1), "
            "evidence (array of strings), next_step (string), and patch (a unified git diff string or empty string). "
            "A patch must be narrowly scoped, must not delete or rename files, and must not alter credentials, deployment, accounts, messaging, purchases, or system settings. "
            "Prefer a useful investigation report over a speculative patch.\n\n"
            f"TASK: {title}\nDETAILS: {json.dumps(task.get('details', []))}\nPROJECT: {project_root}\n\n"
            f"SOURCE CONTEXT:\n{context_text[:30000]}\n\nWEB RESEARCH:\n{research_text}"
        )
        reply = self.model.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            tools=None,
            allow_fallback=False,
            allow_model_load=False,
        )
        model_text = self._extract_text(reply)
        if model_text.strip().lower().startswith("(lm studio error:"):
            result = {"ok": False, "summary": model_text.strip()[:1000], "error": "model_unavailable"}
            self._write_autonomy_audit({"ts": time.time(), "task_key": task.get("key"), "task": task, **result})
            return result
        parsed = self._parse_autonomous_model_result(model_text)
        summary = str(parsed.get("summary") or "No useful result produced.").strip()[:4000]
        patch = str(parsed.get("patch") or "").strip()
        patch_result: Dict[str, Any] = {"ok": True, "applied": False, "reason": "no_patch_proposed"}
        if patch and bool(self.config.get("curiosity", "allow_source_edits", False)):
            patch_result = self._apply_autonomous_patch(patch, project_root)
        elif patch:
            patch_result = {"ok": True, "applied": False, "reason": "source_edits_disabled"}

        result = {
            "ok": bool(summary) and bool(patch_result.get("ok")),
            "summary": summary,
            "confidence": parsed.get("confidence"),
            "evidence": parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else [],
            "next_step": str(parsed.get("next_step") or "").strip(),
            "patch": patch_result,
            "context_files": [item["path"] for item in context_files],
            "duration_seconds": round(time.time() - started_at, 3),
        }
        patch_applied = bool(patch_result.get("applied"))
        validations_value = patch_result.get("validations")
        raw_validations: List[Any] = validations_value if isinstance(validations_value, list) else []
        validation_items: List[Dict[str, Any]] = [item for item in raw_validations if isinstance(item, dict)]
        validations_ok = bool(validation_items) and all(bool(item.get("ok")) for item in validation_items)
        pytest_changed_ok = any(
            item.get("name") == "pytest_changed" and bool(item.get("ok"))
            for item in validation_items
        )
        result["verification"] = {
            "policy": "verified_completion_requires_structured_proof",
            "patch_applied": patch_applied,
            "validations_ok": validations_ok,
            "pytest_changed_ok": pytest_changed_ok,
            "live_api_proof": False,
            "completion_verified": patch_applied and bool(patch_result.get("ok", False)),
        }
        self._write_autonomy_audit({
            "ts": time.time(),
            "task_key": task.get("key"),
            "task": task,
            **result,
        })
        return result

    def _project_tracker_path(self) -> str:
        return os.path.join(self._projects_root(), ".mina_project_tracker.json")

    def _load_project_tracker(self) -> Dict[str, Any]:
        path = self._project_tracker_path()
        if not os.path.exists(path):
            return {"updated_at": None, "projects": {}}

        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                projects = data.get("projects", {})
                if not isinstance(projects, dict):
                    data["projects"] = {}
                return data
        except Exception:
            traceback.print_exc()

        return {"updated_at": None, "projects": {}}

    def _save_project_tracker(self, tracker: Dict[str, Any]) -> None:
        path = self._project_tracker_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(tracker if isinstance(tracker, dict) else {"projects": {}}, handle, indent=2, sort_keys=True, ensure_ascii=True)
        except Exception:
            traceback.print_exc()


    def _sync_project_tracker(self, scanned_projects: List[Dict[str, Any]]) -> Dict[str, Any]:
        tracker = self._load_project_tracker()
        existing = tracker.get("projects", {}) if isinstance(tracker.get("projects", {}), dict) else {}
        now_iso = datetime.now().isoformat(timespec="seconds")

        merged: Dict[str, Any] = {}

        for p in scanned_projects:
            name = str(p.get("name") or "").strip()
            if not name:
                continue

            prev = existing.get(name, {}) if isinstance(existing.get(name, {}), dict) else {}

            status = str(prev.get("status") or "").strip().lower()
            if status not in {"todo", "in_progress", "blocked", "complete", "archived"}:
                status = "complete" if p.get("status") == "complete" else "in_progress"

            next_steps = prev.get("next_steps", [])
            if isinstance(next_steps, str):
                next_steps = [next_steps] if next_steps.strip() else []
            if not isinstance(next_steps, list):
                next_steps = []
            next_steps = [str(x).strip() for x in next_steps if str(x).strip()]

            last_touched = p.get("last_modified_iso") or prev.get("last_touched")

            merged[name] = {
                "name": name,
                "path": p.get("path"),
                "status": status,
                "next_steps": next_steps,
                "last_touched": last_touched,
                "last_scanned_at": now_iso,
                "code_files": int(p.get("code_files") or 0),
                "has_readme": bool(p.get("has_readme")),
            }

        for name, prev in existing.items():
            if name in merged:
                continue
            if not isinstance(prev, dict):
                continue

            prev_status = str(prev.get("status") or "archived").strip().lower()
            if prev_status in {"todo", "in_progress", "blocked"}:
                prev_status = "archived"

            merged[name] = {
                "name": name,
                "path": prev.get("path"),
                "status": prev_status,
                "next_steps": prev.get("next_steps") if isinstance(prev.get("next_steps"), list) else [],
                "last_touched": prev.get("last_touched"),
                "last_scanned_at": now_iso,
                "code_files": int(prev.get("code_files") or 0),
                "has_readme": bool(prev.get("has_readme")),
                "missing": True,
            }

        tracker = {
            "updated_at": now_iso,
            "projects": merged,
        }
        self._save_project_tracker(tracker)
        return tracker

    def _projects_to_complete(self, tracker: Dict[str, Any]) -> List[Dict[str, Any]]:
        projects = tracker.get("projects", {}) if isinstance(tracker.get("projects", {}), dict) else {}
        todo_status = {"todo", "in_progress", "blocked"}
        out: List[Dict[str, Any]] = []
        for name, item in projects.items():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status in todo_status:
                out.append(item)

        out.sort(key=lambda x: str(x.get("last_touched") or ""), reverse=True)
        return out

    def _update_project_tracker(
        self,
        project_name: str,
        status: Optional[str] = None,
        next_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = (project_name or "").strip()
        if not name:
            return {"ok": False, "result": None, "error": "no_project_name_provided"}

        tracker = self._load_project_tracker()
        projects = tracker.get("projects", {}) if isinstance(tracker.get("projects", {}), dict) else {}

        target_key = None
        low_name = name.lower()
        for key in projects.keys():
            if key.lower() == low_name:
                target_key = key
                break
        if target_key is None:
            for key in projects.keys():
                if low_name in key.lower():
                    target_key = key
                    break

        if target_key is None:
            return {"ok": False, "result": None, "error": "project_not_found_in_tracker"}

        entry = projects.get(target_key, {}) if isinstance(projects.get(target_key, {}), dict) else {}

        if status:
            status_norm = status.strip().lower().replace(" ", "_")
            if status_norm in {"todo", "in_progress", "blocked", "complete", "archived"}:
                entry["status"] = status_norm

        if next_step:
            steps = entry.get("next_steps", [])
            if not isinstance(steps, list):
                steps = []
            step_clean = next_step.strip()
            if step_clean and step_clean not in steps:
                steps.append(step_clean)
            entry["next_steps"] = steps

        entry["last_scanned_at"] = datetime.now().isoformat(timespec="seconds")
        projects[target_key] = entry
        tracker["projects"] = projects
        tracker["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_project_tracker(tracker)

        return {
            "ok": True,
            "result": {
                "project": target_key,
                "status": entry.get("status"),
                "next_steps": entry.get("next_steps", []),
            },
            "error": None,
        }

    def _extract_project_files_from_request(self, user_input: str) -> List[str]:
        text = (user_input or "").strip()
        out: List[str] = []

        files_clause = re.search(r'\bwith\s+files?\s+(.+?)(?:\bnext\s+steps?\b|\bstatus\b|$)', text, re.IGNORECASE)
        if not files_clause:
            return out

        raw = files_clause.group(1)
        for m in re.finditer(r'([A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,8})', raw):
            fname = m.group(1).strip().strip('"\'')
            if fname and fname not in out:
                out.append(fname)

        return out

    def _bootstrap_project(
        self,
        project_name: str,
        user_input: str,
        status: Optional[str] = None,
        next_steps: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        name = (project_name or "").strip().strip('"\'')
        if not name:
            return {"ok": False, "result": None, "error": "no_project_name_provided"}

        safe_name = re.sub(r'\s+', '_', name)
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', safe_name)
        if not safe_name:
            return {"ok": False, "result": None, "error": "invalid_project_name"}

        project_path = self._normalize_path(os.path.join("projects", safe_name))
        created_files: List[str] = []

        dir_result = self.tools.run("dir_create", {"path": project_path})
        if not dir_result.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": f"project_dir_create_failed: {dir_result.get('error')}",
            }

        requested_files = self._extract_project_files_from_request(user_input)
        if not requested_files:
            requested_files = ["README.md"]

        for rel_file in requested_files:
            rel_file_norm = rel_file.replace("/", os.sep).replace("\\", os.sep).strip(os.sep)
            if not rel_file_norm:
                continue

            abs_file = os.path.join(project_path, rel_file_norm)
            parent = os.path.dirname(abs_file)
            if parent:
                self.tools.run("dir_create", {"path": parent})

            content = self._generate_file_content_from_intent(
                user_input=f"Project bootstrap for {safe_name}. {user_input}",
                target_path=abs_file,
            )
            if not content.strip() and rel_file_norm.lower().startswith("readme"):
                content = f"# {safe_name}\n\nBootstrapped by Mina.\n"

            if not content.strip():
                continue

            write_result = self.tools.run(
                "file_write",
                {
                    "path": abs_file,
                    "content": content,
                    "overwrite": False,
                },
            )
            if write_result.get("ok"):
                wpath = write_result.get("result", {}).get("path")
                if wpath:
                    created_files.append(str(wpath))

        self.startup_context = self._build_startup_context()

        effective_status = (status or "in_progress").strip().lower().replace(" ", "_")
        if effective_status not in {"todo", "in_progress", "blocked", "complete", "archived"}:
            effective_status = "in_progress"

        update_result = self._update_project_tracker(
            project_name=safe_name,
            status=effective_status,
            next_step=None,
        )
        if not update_result.get("ok"):
            return update_result

        for step in (next_steps or []):
            step_clean = str(step).strip()
            if not step_clean:
                continue
            self._update_project_tracker(
                project_name=safe_name,
                status=None,
                next_step=step_clean,
            )

        tracker = self._load_project_tracker()
        projects = tracker.get("projects", {}) if isinstance(tracker.get("projects", {}), dict) else {}
        entry = projects.get(safe_name, {}) if isinstance(projects.get(safe_name, {}), dict) else {}

        return {
            "ok": True,
            "result": {
                "project": safe_name,
                "path": project_path,
                "files_created": created_files,
                "status": entry.get("status", effective_status),
                "next_steps": entry.get("next_steps", []),
            },
            "error": None,
        }

    def _scan_projects(self, limit: int = 50) -> List[Dict[str, Any]]:
        projects_root = self._projects_root()
        out: List[Dict[str, Any]] = []

        if not os.path.isdir(projects_root):
            return out

        code_exts = {".py", ".ps1", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs"}

        try:
            names = [
                n for n in os.listdir(projects_root)
                if os.path.isdir(os.path.join(projects_root, n))
            ]
        except Exception:
            traceback.print_exc()
            return out

        for name in names:
            proj_path = os.path.join(projects_root, name)
            latest_mtime = 0.0
            has_readme = False
            code_file_count = 0

            try:
                for root, _, files in os.walk(proj_path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        try:
                            mtime = os.path.getmtime(fpath)
                            if mtime > latest_mtime:
                                latest_mtime = mtime
                        except Exception:
                            pass

                        low = fname.lower()
                        if low.startswith("readme"):
                            has_readme = True

                        ext = os.path.splitext(fname)[1].lower()
                        if ext in code_exts:
                            code_file_count += 1
            except Exception:
                traceback.print_exc()

            status = "complete" if has_readme and code_file_count > 0 else "incomplete"
            out.append({
                "name": name,
                "path": proj_path,
                "last_modified": latest_mtime,
                "last_modified_iso": datetime.fromtimestamp(latest_mtime).isoformat(timespec="seconds") if latest_mtime else None,
                "has_readme": has_readme,
                "code_files": code_file_count,
                "status": status,
            })

        out.sort(key=lambda x: float(x.get("last_modified") or 0.0), reverse=True)
        return out[:max(1, int(limit))]

    def _build_startup_context(self) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        projects = self._scan_projects(limit=100)
        tracker = self._sync_project_tracker(projects)
        last_project = projects[0] if projects else None
        incomplete = self._projects_to_complete(tracker)

        return {
            "startup_time": now,
            "workspace_root": self.workspace_root,
            "projects_root": self._projects_root(),
            "project_tracker_path": self._project_tracker_path(),
            "turn_clock_started_at": self._turn_clock_started_at,
            "turn_clock_tick": self._turn_clock_tick,
            "last_user_activity_at": self._last_user_activity_at,
            "last_shutdown_at": self._last_shutdown_at,
            "seconds_since_last_shutdown": self._seconds_since_last_shutdown,
            "away_nag_threshold_seconds": self.away_nag_threshold_seconds,
            "away_nag_prompt_pending": self._away_nag_prompt_pending,
            "idle_checkin_interval_seconds": self.idle_checkin_interval_seconds,
            "idle_checkin_pending": self._idle_checkin_pending,
            "idle_checkin_due_since_at": self._idle_checkin_due_since_at,
            "project_count": len(projects),
            "last_worked_project": last_project,
            "incomplete_projects": incomplete,
        }

    def _advance_turn_clock(self) -> Dict[str, Any]:
        with self._turn_clock_lock:
            now_monotonic = time.monotonic()
            elapsed_seconds = max(0.0, now_monotonic - float(self._turn_clock_started_monotonic))
            since_last_seconds = max(0.0, now_monotonic - float(self._turn_clock_last_tick_monotonic))

            self._turn_clock_tick += 1
            self._turn_clock_last_tick_monotonic = now_monotonic
            self._turn_clock_last_tick_at = datetime.now().isoformat(timespec="seconds")
            self._turn_clock_last_elapsed_seconds = elapsed_seconds

            if isinstance(getattr(self, "startup_context", None), dict):
                self.startup_context["turn_clock_tick"] = self._turn_clock_tick
                self.startup_context["turn_clock_started_at"] = self._turn_clock_started_at
                self.startup_context["turn_clock_last_tick_at"] = self._turn_clock_last_tick_at
                self.startup_context["turn_clock_last_elapsed_seconds"] = self._turn_clock_last_elapsed_seconds

            return {
                "tick": self._turn_clock_tick,
                "started_at": self._turn_clock_started_at,
                "current_at": self._turn_clock_last_tick_at,
                "elapsed_seconds": elapsed_seconds,
                "since_last_tick_seconds": since_last_seconds,
            }

    def _start_turn_clock_heartbeat(self) -> None:
        if self._turn_clock_thread and self._turn_clock_thread.is_alive():
            return

        def _apply_curiosity_runtime_gate(curiosity: Any) -> None:
            if not self.curiosity_suspend_during_ostranauts or curiosity is None:
                return

            now_monotonic = time.monotonic()
            if now_monotonic - float(self._curiosity_gate_last_check_monotonic) < 10.0:
                return
            self._curiosity_gate_last_check_monotonic = now_monotonic

            try:
                status = get_ostranauts_status()
                telemetry_online = bool(status.get("telemetry_online"))
                if telemetry_online:
                    telemetry_raw = status.get("telemetry")
                    telemetry: Dict[str, Any] = telemetry_raw if isinstance(telemetry_raw, dict) else {}
                    game_focused = bool(telemetry.get("game_focused"))
                    reason = "ostranauts_active_focused" if game_focused else "ostranauts_active"
                    hold_seconds = max(120, self.curiosity_pause_after_ostranauts_seconds)
                    curiosity.set_pause(hold_seconds, reason=reason)
                    self._curiosity_gate_last_active = True
                    return

                if self._curiosity_gate_last_active and self.curiosity_pause_after_ostranauts_seconds > 0:
                    curiosity.set_pause(
                        self.curiosity_pause_after_ostranauts_seconds,
                        reason="ostranauts_recent_exit",
                    )
                self._curiosity_gate_last_active = False
            except Exception:
                traceback.print_exc()

        def _heartbeat() -> None:
            interval = float(getattr(self, "idle_clock_interval_seconds", 5))
            while not self._turn_clock_stop_event.wait(interval):
                try:
                    self._advance_turn_clock()
                    self._refresh_idle_checkin_state()
                    self._save_runtime_state(reason="heartbeat")
                    idle_seconds = max(0.0, time.monotonic() - float(self._last_user_activity_monotonic))
                    curiosity = getattr(self, "curiosity", None)
                    if curiosity is not None:
                        _apply_curiosity_runtime_gate(curiosity)
                        curiosity.maybe_start(idle_seconds)
                except Exception:
                    traceback.print_exc()

        self._turn_clock_thread = threading.Thread(
            target=_heartbeat,
            name="mk1-turn-clock-heartbeat",
            daemon=True,
        )
        self._turn_clock_thread.start()

    def close(self) -> Dict[str, Any]:
        self._turn_clock_stop_event.set()
        thread = self._turn_clock_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._save_runtime_state(reason="shutdown")
        memory_result: Dict[str, Any] = {"ok": True, "result": None, "error": None}
        try:
            mem = getattr(self, "memory", None)
            if mem is not None:
                if hasattr(mem, "close"):
                    close_result = mem.close(flush_rounds=4, flush_batch_size=200)
                    if isinstance(close_result, dict):
                        memory_result = close_result
                elif hasattr(mem, "flush_pending_memories"):
                    flush_result = mem.flush_pending_memories(max_items=200)
                    if isinstance(flush_result, dict):
                        memory_result = flush_result
        except Exception as e:
            traceback.print_exc()
            memory_result = {"ok": False, "result": None, "error": str(e)}
        return {
            "ok": bool(memory_result.get("ok", False)),
            "result": {"memory": memory_result.get("result")},
            "error": memory_result.get("error"),
        }

    def _prepare_safe_shutdown(
        self,
        force: bool = False,
        max_wait_seconds: int = 45,
        poll_seconds: int = 2,
        flush_max_items: int = 400,
    ) -> Dict[str, Any]:
        mem = getattr(self, "memory", None)
        if mem is None:
            return {
                "ok": True,
                "result": {
                    "buffer_clear": True,
                    "pending": 0,
                    "forced": False,
                    "attempts": 0,
                    "waited_seconds": 0.0,
                },
                "error": None,
            }

        wait_s = max(0, int(max_wait_seconds))
        poll_s = max(1, int(poll_seconds))
        flush_items = max(1, min(2000, int(flush_max_items)))
        started = time.monotonic()
        deadline = started + float(wait_s)
        attempts = 0
        last_pending: Optional[int] = None
        last_flush: Dict[str, Any] = {"flushed": 0, "failed": 0}
        activity: Dict[str, Any] = {}

        if hasattr(mem, "begin_shutdown_quiesce"):
            try:
                quiesce_result = mem.begin_shutdown_quiesce()
                if isinstance(quiesce_result, dict):
                    activity = quiesce_result
            except Exception as e:
                return {"ok": False, "result": None, "error": f"memory_quiesce_failed: {e}"}

        while True:
            attempts += 1

            active_operations = 0
            if hasattr(mem, "memory_activity_status"):
                try:
                    current_activity = mem.memory_activity_status()
                    if isinstance(current_activity, dict):
                        activity = current_activity
                        active_operations = int(activity.get("active_operations") or 0)
                except Exception:
                    traceback.print_exc()

            if active_operations > 0:
                if time.monotonic() >= deadline:
                    break
                remaining = max(0.0, deadline - time.monotonic())
                if hasattr(mem, "wait_for_memory_idle"):
                    try:
                        mem.wait_for_memory_idle(timeout_seconds=min(float(poll_s), remaining))
                    except Exception:
                        traceback.print_exc()
                else:
                    time.sleep(min(float(poll_s), remaining))
                continue

            if hasattr(mem, "flush_pending_memories"):
                try:
                    flush_result = mem.flush_pending_memories(max_items=flush_items)
                    if isinstance(flush_result, dict):
                        rr = flush_result.get("result", {}) if isinstance(flush_result.get("result"), dict) else {}
                        last_flush = {
                            "flushed": int(rr.get("flushed") or 0),
                            "failed": int(rr.get("failed") or 0),
                        }
                except Exception:
                    traceback.print_exc()

            pending = 0
            if hasattr(mem, "buffer_status"):
                try:
                    bs = mem.buffer_status()
                    if isinstance(bs, dict):
                        br = bs.get("result", {}) if isinstance(bs.get("result"), dict) else {}
                        pending = int(br.get("pending") or 0)
                except Exception:
                    traceback.print_exc()

            last_pending = pending
            if hasattr(mem, "memory_activity_status"):
                try:
                    current_activity = mem.memory_activity_status()
                    if isinstance(current_activity, dict):
                        activity = current_activity
                except Exception:
                    traceback.print_exc()

            active_operations = int(activity.get("active_operations") or 0)
            if pending <= 0 and active_operations <= 0:
                waited = max(0.0, time.monotonic() - started)
                return {
                    "ok": True,
                    "result": {
                        "buffer_clear": True,
                        "memory_idle": True,
                        "pending": 0,
                        "active_operations": 0,
                        "active_by_name": {},
                        "quiescing": bool(activity.get("quiescing", False)),
                        "forced": False,
                        "attempts": attempts,
                        "waited_seconds": round(waited, 2),
                        "last_flush": last_flush,
                    },
                    "error": None,
                }

            if time.monotonic() >= deadline:
                break

            time.sleep(float(poll_s))

        waited = max(0.0, time.monotonic() - started)
        active_operations = int(activity.get("active_operations") or 0)
        if active_operations > 0:
            if hasattr(mem, "resume_memory_operations"):
                try:
                    mem.resume_memory_operations()
                except Exception:
                    traceback.print_exc()
            return {
                "ok": False,
                "result": {
                    "buffer_clear": int(last_pending or 0) <= 0,
                    "memory_idle": False,
                    "pending": int(last_pending or 0),
                    "active_operations": active_operations,
                    "active_by_name": activity.get("active_by_name", {}),
                    "forced": False,
                    "attempts": attempts,
                    "waited_seconds": round(waited, 2),
                    "last_flush": last_flush,
                },
                "error": "active_memory_operations_not_complete",
            }

        if force:
            return {
                "ok": True,
                "result": {
                    "buffer_clear": False,
                    "memory_idle": True,
                    "pending": int(last_pending or 0),
                    "active_operations": 0,
                    "active_by_name": {},
                    "quiescing": bool(activity.get("quiescing", False)),
                    "forced": True,
                    "attempts": attempts,
                    "waited_seconds": round(waited, 2),
                    "last_flush": last_flush,
                },
                "error": None,
            }

        if hasattr(mem, "resume_memory_operations"):
            try:
                mem.resume_memory_operations()
            except Exception:
                traceback.print_exc()
        return {
            "ok": False,
            "result": {
                "buffer_clear": False,
                "memory_idle": True,
                "pending": int(last_pending or 0),
                "active_operations": 0,
                "active_by_name": {},
                "forced": False,
                "attempts": attempts,
                "waited_seconds": round(waited, 2),
                "last_flush": last_flush,
            },
            "error": "buffer_not_clear_before_shutdown",
        }

    def _request_safe_shutdown(
        self,
        force: bool = False,
        max_wait_seconds: int = 45,
        poll_seconds: int = 2,
        flush_max_items: int = 400,
        embed_port: int = 8084,
        post_flush_delay_ms: int = 1200,
    ) -> Dict[str, Any]:
        try:
            gate = self._prepare_safe_shutdown(
                force=force,
                max_wait_seconds=max_wait_seconds,
                poll_seconds=poll_seconds,
                flush_max_items=flush_max_items,
            )
            if not gate.get("ok"):
                return gate

            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            script_path = os.path.join(repo_root, "shutdown_mina_safe.ps1")
            if not os.path.isfile(script_path):
                return {
                    "ok": False,
                    "result": None,
                    "error": f"shutdown_script_missing: {script_path}",
                }

            self._save_runtime_state(reason="shutdown_requested")

            port = int(self.config.get("server", "port", 8000) or 8000)
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-Port",
                str(port),
                "-EmbedPort",
                str(max(1, int(embed_port))),
                "-FlushMaxItems",
                str(max(1, min(2000, int(flush_max_items)))),
                "-WaitForBufferSeconds",
                str(max(0, int(max_wait_seconds))),
                "-BufferPollSeconds",
                str(max(1, int(poll_seconds))),
                "-PostFlushDelayMs",
                str(max(0, min(15000, int(post_flush_delay_ms)))),
            ]
            if force:
                cmd.append("-ForceAfterTimeout")

            popen_kwargs: Dict[str, Any] = {"cwd": repo_root}
            if os.name == "nt":
                flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                )
                if flags:
                    popen_kwargs["creationflags"] = flags

            subprocess.Popen(cmd, **popen_kwargs)
            return {
                "ok": True,
                "result": {
                    "script": script_path,
                    "port": port,
                    "embed_port": max(1, int(embed_port)),
                    "status": "shutdown_requested",
                    "gate": gate.get("result"),
                    "post_flush_delay_ms": max(0, min(15000, int(post_flush_delay_ms))),
                },
                "error": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "result": None,
                "error": f"shutdown_request_failed: {str(e)}",
            }

    def _shutdown_gate_status(
        self,
        max_wait_seconds: int = 45,
        poll_seconds: int = 2,
        flush_max_items: int = 400,
    ) -> Dict[str, Any]:
        try:
            mem = getattr(self, "memory", None)
            if mem is None:
                return {
                    "ok": True,
                    "result": {
                        "buffer_available": False,
                        "buffer_ready": True,
                        "pending": 0,
                        "wait_recommendation": "Memory buffer engine not available; shutdown should not block on buffer.",
                        "max_wait_seconds": max(0, int(max_wait_seconds)),
                        "poll_seconds": max(1, int(poll_seconds)),
                        "flush_max_items": max(1, min(2000, int(flush_max_items))),
                    },
                    "error": None,
                }

            pending = 0
            warning = False
            last_flush_result: Dict[str, Any] = {}
            activity: Dict[str, Any] = {}
            if hasattr(mem, "buffer_status"):
                bs = mem.buffer_status()
                if isinstance(bs, dict):
                    br = bs.get("result", {}) if isinstance(bs.get("result"), dict) else {}
                    pending = int(br.get("pending") or 0)
                    warning = bool(br.get("warning", False))
                    last_flush_result = br.get("last_flush_result", {}) if isinstance(br.get("last_flush_result"), dict) else {}

            if hasattr(mem, "memory_activity_status"):
                current_activity = mem.memory_activity_status()
                if isinstance(current_activity, dict):
                    activity = current_activity

            active_operations = int(activity.get("active_operations") or 0)
            ready = pending <= 0 and active_operations <= 0
            recommendation = (
                "Memory is idle and the buffer is clear. Safe shutdown can proceed now."
                if ready
                else "Memory is still active or buffered. Wait for all operations to complete."
            )

            return {
                "ok": True,
                "result": {
                    "buffer_available": True,
                    "buffer_ready": ready,
                    "memory_idle": active_operations <= 0,
                    "pending": int(pending),
                    "active_operations": active_operations,
                    "active_by_name": activity.get("active_by_name", {}),
                    "quiescing": bool(activity.get("quiescing", False)),
                    "closing": bool(activity.get("closing", False)),
                    "warning": warning,
                    "last_flush_result": last_flush_result,
                    "wait_recommendation": recommendation,
                    "max_wait_seconds": max(0, int(max_wait_seconds)),
                    "poll_seconds": max(1, int(poll_seconds)),
                    "flush_max_items": max(1, min(2000, int(flush_max_items))),
                    "force_override_hint": "Say: shutdown mina force",
                },
                "error": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "result": None,
                "error": f"shutdown_status_failed: {str(e)}",
            }

    def _refresh_idle_checkin_state(self) -> None:
        now_monotonic = time.monotonic()
        with self._turn_clock_lock:
            if self._idle_checkin_pending:
                return

            idle_seconds = max(0.0, now_monotonic - float(self._last_user_activity_monotonic))
            if idle_seconds < float(self.idle_checkin_interval_seconds):
                return

            self._idle_checkin_pending = True
            self._idle_checkin_due_since_at = datetime.now().isoformat(timespec="seconds")

            self._record_time_passage_note(idle_seconds=idle_seconds)

            if isinstance(getattr(self, "startup_context", None), dict):
                self.startup_context["idle_checkin_pending"] = True
                self.startup_context["idle_checkin_due_since_at"] = self._idle_checkin_due_since_at

    def _record_time_passage_note(self, idle_seconds: float) -> None:
        try:
            if hasattr(self.memory, "add_time_passage_note"):
                note = (
                    f"Time passage note: {idle_seconds:.0f} seconds elapsed since the last user activity. "
                    f"Turn clock tick {self._turn_clock_tick}."
                )
                mem_id = self.memory.add_time_passage_note(
                    note,
                    tags=["idle_checkin", "time_passage"],
                )
                if mem_id is not None:
                    self._last_time_passage_note_monotonic = time.monotonic()
        except Exception:
            traceback.print_exc()

    def _consume_idle_checkin_prompt(self) -> str:
        with self._turn_clock_lock:
            if not self._idle_checkin_pending:
                return ""

            self._idle_checkin_pending = False
            due_since_at = self._idle_checkin_due_since_at
            self._idle_checkin_due_since_at = None

            if isinstance(getattr(self, "startup_context", None), dict):
                self.startup_context["idle_checkin_pending"] = False
                self.startup_context["idle_checkin_due_since_at"] = None

        if due_since_at:
            return (
                "Idle check-in due: the user has been away for a while. "
                "Briefly check in on them before continuing with the answer."
            )
        return ""

    def _consume_away_nag_prompt(self) -> str:
        if not self._away_nag_prompt_pending:
            return ""

        self._away_nag_prompt_pending = False
        return str(self._away_nag_prompt_text or "").strip()

    def _extract_repeat_seconds_from_tags(self, tags: Any) -> Optional[int]:
        try:
            if not isinstance(tags, list):
                return None
            for tag in tags:
                tag_text = str(tag or "").strip().lower()
                m = re.match(r"repeat_(\d+)s$", tag_text)
                if not m:
                    continue
                value = int(m.group(1))
                if value > 0:
                    return value
        except Exception:
            traceback.print_exc()
        return None

    def _task_is_alarm(self, task: Dict[str, Any]) -> bool:
        try:
            tags = task.get("tags") if isinstance(task, dict) else []
            if isinstance(tags, list):
                lowered = {str(t or "").strip().lower() for t in tags}
                if "alarm_task" in lowered:
                    return True

            text = str((task or {}).get("text") or "").strip().lower()
            return text.startswith("alarm")
        except Exception:
            traceback.print_exc()
            return False

    def _build_due_task_prompt(self, max_tasks: int = 5) -> str:
        try:
            if not hasattr(self.memory, "get_due_tasks"):
                return ""

            tasks = self.memory.get_due_tasks(top_k=max_tasks, include_tags=["scheduled_task"])
        except Exception:
            traceback.print_exc()
            tasks = []

        if not tasks:
            return ""

        lines = ["Due tasks from memory:"]
        for task in tasks[:max(1, int(max_tasks))]:
            text = str(task.get("text") or "").strip()
            due_at = task.get("due_at")
            repeat_seconds = self._extract_repeat_seconds_from_tags(task.get("tags"))
            is_alarm = self._task_is_alarm(task)
            if due_at:
                try:
                    due_text = datetime.fromtimestamp(float(due_at)).isoformat(timespec="seconds")
                except Exception:
                    due_text = str(due_at)
                suffix_parts: List[str] = [f"due {due_text}"]
                if repeat_seconds:
                    repeat_minutes = max(1, int(round(float(repeat_seconds) / 60.0)))
                    suffix_parts.append(f"repeats every {repeat_minutes} min")
                if is_alarm:
                    suffix_parts.append("alarm")
                lines.append(f"- {text} ({', '.join(suffix_parts)})")
            else:
                if is_alarm:
                    lines.append(f"- {text} (alarm)")
                else:
                    lines.append(f"- {text}")

            # Keep recurring tasks alive by moving their due_at forward once surfaced.
            if repeat_seconds and hasattr(self.memory, "snooze_task"):
                try:
                    task_id = int(task.get("id") or 0)
                    if task_id > 0:
                        self.memory.snooze_task(task_id, int(repeat_seconds))
                except Exception:
                    traceback.print_exc()

        lines.append("Pick the highest-priority due task and work on that before unrelated replies.")
        return "\n".join(lines)

    def _register_user_activity(self) -> None:
        now_monotonic = time.monotonic()
        now_text = datetime.now().isoformat(timespec="seconds")
        with self._turn_clock_lock:
            self._last_user_activity_monotonic = now_monotonic
            self._last_user_activity_at = now_text

            if isinstance(getattr(self, "startup_context", None), dict):
                self.startup_context["last_user_activity_at"] = self._last_user_activity_at

        self._save_runtime_state(reason="user_input")

    def _format_turn_clock_line(self, clock_state: Dict[str, Any]) -> str:
        tick = int(clock_state.get("tick") or 0)
        elapsed_seconds = float(clock_state.get("elapsed_seconds") or 0.0)
        since_last = float(clock_state.get("since_last_tick_seconds") or 0.0)
        current_at = str(clock_state.get("current_at") or "")
        idle_due = bool(getattr(self, "_idle_checkin_pending", False))
        return (
            f"Turn clock: tick {tick}, elapsed {elapsed_seconds:.1f}s, "
            f"{since_last:.1f}s since prior turn, now {current_at}; "
            f"idle check-in {'due' if idle_due else 'not due'}"
        )

    def _seed_startup_memory_facts(self) -> None:
        """
        Seed durable environment facts once at startup.
        This avoids repeating the same context every turn while still making
        these facts retrievable via memory tools and normal recall flows.
        """
        try:
            facts = [
                f"Mina workspace root is {self.workspace_root} and it is her full working workspace",
                f"Mina can read and write within the workspace root {self.workspace_root}",
                f"Mina project root is {self.project_root} for this codebase",
                f"Mina can access both personal workspace {self.workspace_root} and project root {self.project_root}",
                f"Mina projects root is {self._projects_root()} for active project work",
                f"Mina scratch root is {os.path.join(self.workspace_root, 'scratch')} for quick notes, reminders, and temporary working files",
                f"Mina templates root is {os.path.join(self.workspace_root, 'templates')} for reusable starting files and patterns",
                f"Mina archive root is {os.path.join(self.workspace_root, 'archive')} for finished or retired workspace items",
                f"Mina memory database path is {getattr(self.memory, 'db_path', '')}",
                f"Mina FAISS small index path is {getattr(self.memory, 'faiss_small_path', '')}",
                f"Mina FAISS base index path is {getattr(self.memory, 'faiss_base_path', '')}",
                f"Mina memory backup directory is {getattr(self.memory, 'backup_dir', '')}",
            ]

            for fact in facts:
                fact_clean = (fact or "").strip()
                if not fact_clean:
                    continue

                exists = False
                if hasattr(self.memory, "find_memory_id_by_text"):
                    exists = self.memory.find_memory_id_by_text(
                        fact_clean,
                        include_kinds=["fact", "procedure"],
                        include_tags=["system_seed"],
                    ) is not None

                if not exists:
                    self.memory.add_memory(
                        fact_clean,
                        kind="procedure",
                        tags=["user_memory", "system_seed", "startup_fact"],
                    )
        except Exception:
            traceback.print_exc()

    def _authoritative_tags_for_fact(self, fact_text: str) -> List[str]:
        low = str(fact_text or "").strip().lower()
        tags = ["user_memory", "profile_auto"]

        if not low:
            return tags

        if any(x in low for x in [
            "my name is",
            "eye color",
            "eyes are",
            "birthdate",
            "birthday",
            "date of birth",
            "favorite color",
            "favourite color",
        ]):
            tags.append("authoritative_profile")

        return tags

    def _seed_authoritative_identity_memory(self) -> None:
        try:
            identity_facts = [
                "Mina is the chaotic gremlin-fox assistant.",
                "Mina is loyal to Travis.",
                "Mina is Travis's ride or die buddy.",
            ]

            for fact in identity_facts:
                fact_clean = (fact or "").strip()
                if not fact_clean:
                    continue

                exists = False
                if hasattr(self.memory, "find_memory_id_by_text"):
                    exists = self.memory.find_memory_id_by_text(
                        fact_clean,
                        include_kinds=["fact", "procedure"],
                        include_tags=["authoritative_identity"],
                    ) is not None

                if not exists:
                    self.memory.add_memory(
                        fact_clean,
                        kind="fact",
                        tags=["user_memory", "system_seed", "startup_fact", "authoritative_identity", "relationship_identity"],
                    )

            for slot in ["name", "eye_color", "birthdate", "favorite_color"]:
                fact = self._lookup_slot_fact(slot, f"what is my {slot.replace('_', ' ')}")
                fact_clean = (fact or "").strip()
                if not fact_clean:
                    continue

                exists = False
                if hasattr(self.memory, "find_memory_id_by_text"):
                    exists = self.memory.find_memory_id_by_text(
                        fact_clean,
                        include_kinds=["fact", "preference", "procedure"],
                        include_tags=["authoritative_profile"],
                    ) is not None

                if not exists:
                    self.memory.add_memory(
                        fact_clean,
                        kind="fact",
                        tags=["user_memory", "profile_auto", "authoritative_profile"],
                    )
        except Exception:
            traceback.print_exc()

    def _build_runtime_capability_snapshot(self) -> Dict[str, Any]:
        tool_names: List[str] = []
        try:
            tool_names = [item["name"] for item in self._usable_tool_inventory()]
        except Exception:
            tool_names = []

        model_switch_allowed = self.config.get("model", "switch_allowed", [])
        if not isinstance(model_switch_allowed, list):
            model_switch_allowed = []

        return {
            "identity": "Mina",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "workspace_root": self.workspace_root,
            "projects_root": self._projects_root(),
            "model": {
                "default_model": self.config.get("model", "default_model", ""),
                "switch_allowed": [str(x) for x in model_switch_allowed],
                "vision_max_tokens": self.config.get("model", "vision_max_tokens", None),
                "vision_reasoning_effort": self.config.get("model", "vision_reasoning_effort", None),
            },
            "memory": {
                "db_path": getattr(self.memory, "db_path", ""),
                "faiss_small_path": getattr(self.memory, "faiss_small_path", ""),
                "faiss_base_path": getattr(self.memory, "faiss_base_path", ""),
                "backup_dir": getattr(self.memory, "backup_dir", ""),
                "auto_backup_every_writes": self.config.get("memory", "auto_backup_every_writes", None),
                "auto_backup_every_seconds": self.config.get("memory", "auto_backup_every_seconds", None),
            },
            "performance": {
                "fast_command_mode": bool(getattr(self, "fast_command_mode", True)),
                "fast_command_skip_context": bool(getattr(self, "fast_command_skip_context", False)),
                "maintenance_min_interval_seconds": int(getattr(self, "maintenance_min_interval_seconds", 20)),
            },
            "reflex": {
                "enabled": bool(getattr(self, "reflex_enabled", True)),
                "requires_prefix": bool(getattr(self, "reflex_requires_prefix", False)),
                "prefixes": list(getattr(self, "reflex_prefixes", [])),
            },
            "tools": {
                "count": len(tool_names),
                "available": tool_names,
            },
        }

    def _usable_tool_inventory(self) -> List[Dict[str, str]]:
        inventory: List[Dict[str, str]] = []
        try:
            schemas = self.tools.get_tool_schemas() if getattr(self, "tools", None) else []
        except Exception:
            schemas = []

        for schema in schemas or []:
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            inventory.append({
                "name": name,
                "description": str(fn.get("description") or "").strip(),
            })

        inventory.sort(key=lambda item: item["name"].lower())
        return inventory

    def _tool_self_test_aliases(self, available: List[str]) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}

        def add_alias(alias: str, tool: str) -> None:
            key = str(alias or "").strip().lower()
            if not key:
                return
            if tool not in available:
                return
            alias_map[key] = tool

        for name in available:
            low = str(name or "").strip().lower()
            if not low:
                continue
            add_alias(low, name)
            add_alias(low.replace("_", " "), name)
            add_alias(low.replace("_", "-"), name)

        common_aliases = {
            "spotify": "spotify_link",
            "spotify tool": "spotify_link",
            "ps": "ps_run",
            "powershell run": "powershell",
            "power shell": "powershell",
            "code exec": "code_execute",
            "execute code": "code_execute",
            "mkdir": "dir_create",
            "list dir": "dir_list",
            "list directory": "dir_list",
            "read memory": "memory_read",
            "write memory": "memory_write",
            "copy file": "safe_copy",
            "fetch web": "web_fetch",
            "search web": "web_search",
            "github": "github_repo",
        }

        for alias, tool in common_aliases.items():
            add_alias(alias, tool)

        return alias_map

    def _tool_self_test_extract_tools(self, text: str, alias_map: Dict[str, str]) -> List[str]:
        src = str(text or "").lower()
        if not src:
            return []

        hits: List[str] = []
        for alias, tool in alias_map.items():
            pat = r"(?<!\\w)" + re.escape(alias) + r"(?!\\w)"
            if re.search(pat, src):
                hits.append(tool)

        out: List[str] = []
        seen = set()
        for tool in hits:
            if tool in seen:
                continue
            seen.add(tool)
            out.append(tool)
        return out

    def _resolve_tool_self_test_selection(self, request: str) -> Dict[str, Any]:
        available = sorted(list(getattr(self.tools, "tools", {}).keys()))
        alias_map = self._tool_self_test_aliases(available)
        text = str(request or "").strip()
        lowered = text.lower()

        only_mentions: List[str] = []
        only_match = re.search(r"\b(?:only|just)\b\s+(.+)$", text, re.IGNORECASE)
        if only_match:
            only_mentions = self._tool_self_test_extract_tools(only_match.group(1), alias_map)

        include_mentions: List[str] = []
        include_match = re.search(r"\b(?:include|including|with)\b\s+(.+)$", text, re.IGNORECASE)
        if include_match:
            include_mentions = self._tool_self_test_extract_tools(include_match.group(1), alias_map)

        exclude_mentions: List[str] = []
        for m in re.finditer(r"\b(?:except|excluding|without|but\s+not)\b\s+(.+?)(?:$|[.;]|\band\b\s+then\b)", text, re.IGNORECASE):
            exclude_mentions.extend(self._tool_self_test_extract_tools(m.group(1), alias_map))

        selected: List[str]
        if only_mentions:
            selected = [x for x in available if x in only_mentions]
        else:
            selected = list(available)
            for tool in include_mentions:
                if tool not in selected and tool in available:
                    selected.append(tool)

        if ("all tools" in lowered or "all your tools" in lowered) and not only_mentions:
            selected = list(available)

        if exclude_mentions:
            selected = [x for x in selected if x not in set(exclude_mentions)]

        selected = sorted(selected)

        unknown_requested = False
        if not selected and (only_mentions or include_mentions):
            unknown_requested = True

        return {
            "available": available,
            "selected": selected,
            "only": sorted(set(only_mentions)),
            "included": sorted(set(include_mentions)),
            "excluded": sorted(set(exclude_mentions)),
            "unknown_requested": unknown_requested,
            "request": text,
        }

    def _run_single_tool_self_test(self, tool_name: str, base_dir: str, stamp: str) -> Dict[str, Any]:
        started = time.time()
        try:
            files_dir = os.path.join(base_dir, "files")
            os.makedirs(files_dir, exist_ok=True)

            write_path = os.path.join(files_dir, "write_target.txt")
            move_src = os.path.join(files_dir, "move_src.txt")
            move_dst = os.path.join(files_dir, "move_dst.txt")
            copy_dst = os.path.join(files_dir, "copy_dst.txt")
            delete_target = os.path.join(files_dir, "delete_target.txt")
            mem_text = f"tool self test marker {stamp}"

            if tool_name == "mina_echo":
                args = {}
            elif tool_name == "dir_create":
                args = {"path": os.path.join(base_dir, "alpha", "beta")}
            elif tool_name == "dir_list":
                args = {"path": base_dir}
            elif tool_name == "file_write":
                args = {"path": write_path, "content": f"tool self test {stamp}", "overwrite": True}
            elif tool_name == "file_append":
                with open(write_path, "w", encoding="utf-8") as handle:
                    handle.write("line1")
                args = {"path": write_path, "content": "\nline2"}
            elif tool_name == "file_read":
                with open(write_path, "w", encoding="utf-8") as handle:
                    handle.write("read check")
                args = {"path": write_path}
            elif tool_name == "file_move":
                with open(move_src, "w", encoding="utf-8") as handle:
                    handle.write("move me")
                if os.path.exists(move_dst):
                    os.remove(move_dst)
                args = {"src": move_src, "dst": move_dst}
            elif tool_name == "file_delete":
                with open(delete_target, "w", encoding="utf-8") as handle:
                    handle.write("delete me")
                args = {"path": delete_target}
            elif tool_name == "safe_copy":
                with open(write_path, "w", encoding="utf-8") as handle:
                    handle.write("copy me")
                if os.path.exists(copy_dst):
                    os.remove(copy_dst)
                args = {"src": write_path, "dst": copy_dst}
            elif tool_name == "powershell":
                args = {"command": 'Get-Date -Format "yyyy-MM-dd"', "timeout": 10}
            elif tool_name == "ps_run":
                args = {"command": 'Get-Date -Format "yyyy-MM-dd"', "timeout": 10}
            elif tool_name == "code_execute":
                args = {"language": "python", "code": "print('tool-self-test-ok')", "timeout": 10}
            elif tool_name == "web_fetch":
                args = {"url": "https://example.com", "timeout": 15}
            elif tool_name == "web_search":
                args = {"query": "Mina MK1", "max_results": 3, "timeout": 20, "include_content": False}
            elif tool_name == "github_repo":
                args = {"action": "repo_info", "owner": "octocat", "repo": "Hello-World"}
            elif tool_name == "memory_write":
                args = {"text": mem_text, "kind": "note", "tags": ["tool_self_test"]}
            elif tool_name == "memory_read":
                args = {"query": mem_text, "top_k": 1}
            elif tool_name == "gremlin_chaos":
                args = {"level": 1}
            elif tool_name == "spotify_link":
                # Status-only probe; avoids changing playback state.
                args = {"action": "connect_status"}
            else:
                return {
                    "tool": tool_name,
                    "ok": False,
                    "error": "no_self_test_plan_for_tool",
                    "duration_ms": int((time.time() - started) * 1000),
                }

            raw = self.tools.run(tool_name, args)
            ok_flag = bool(isinstance(raw, dict) and raw.get("ok"))
            err_text = ""
            if isinstance(raw, dict):
                err_text = str(raw.get("error") or "")
            return {
                "tool": tool_name,
                "ok": ok_flag,
                "error": err_text,
                "duration_ms": int((time.time() - started) * 1000),
                "result": raw,
            }
        except Exception as e:
            return {
                "tool": tool_name,
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "duration_ms": int((time.time() - started) * 1000),
            }

    def _run_tool_self_test(self, request: str) -> Dict[str, Any]:
        selection = self._resolve_tool_self_test_selection(request)
        selected = selection.get("selected", [])
        if not selected:
            if selection.get("unknown_requested"):
                return {
                    "ok": False,
                    "result": {
                        "request": selection.get("request"),
                        "available": selection.get("available", []),
                        "selected": [],
                        "runs": [],
                    },
                    "error": "no_matching_tools_requested",
                }
            selected = list(selection.get("available", []))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = os.path.join(self.workspace_root, "tmp", f"tool_self_test_{stamp}")
        os.makedirs(base_dir, exist_ok=True)

        runs: List[Dict[str, Any]] = []
        for tool_name in selected:
            runs.append(self._run_single_tool_self_test(tool_name=tool_name, base_dir=base_dir, stamp=stamp))

        failed = [r for r in runs if not r.get("ok")]
        summary = {str(r.get("tool")): bool(r.get("ok")) for r in runs}

        report_payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "request": selection.get("request"),
            "available": selection.get("available", []),
            "selected": selected,
            "excluded": selection.get("excluded", []),
            "included": selection.get("included", []),
            "only": selection.get("only", []),
            "summary": summary,
            "failed_count": len(failed),
            "runs": runs,
        }

        report_path = os.path.join(base_dir, "tool_self_test_report.json")
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report_payload, handle, indent=2, ensure_ascii=True)

        return {
            "ok": len(failed) == 0,
            "result": {
                "request": selection.get("request"),
                "selected": selected,
                "excluded": selection.get("excluded", []),
                "included": selection.get("included", []),
                "only": selection.get("only", []),
                "failed_count": len(failed),
                "summary": summary,
                "runs": runs,
                "report_path": report_path,
            },
            "error": None if len(failed) == 0 else "tool_self_test_failures",
        }

    def _scratch_root(self) -> str:
        return os.path.join(self.workspace_root, "scratch")

    def _sanitize_scratch_note_name(self, text: str, max_len: int = 32) -> str:
        raw = re.sub(r"\s+", " ", str(text or "").strip())
        if not raw:
            return "note"
        slug = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
        if not slug:
            slug = "note"
        return slug[:max_len].strip("_") or "note"

    def _write_scratch_note(self, note_text: str, title_hint: str = "note") -> Dict[str, Any]:
        body = str(note_text or "").strip()
        if not body:
            return {"ok": False, "result": None, "error": "no_note_content_provided"}

        scratch_root = self._scratch_root()
        os.makedirs(scratch_root, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        title = self._sanitize_scratch_note_name(title_hint)
        path = os.path.join(scratch_root, f"{stamp}_{title}.md")
        content = (
            f"# Scratch Note\n\n"
            f"Created: {datetime.now().isoformat(timespec='seconds')}\n\n"
            f"{body}\n"
        )

        return self.tools.run(
            "file_write",
            {
                "path": path,
                "content": content,
                "overwrite": True,
            },
        )

    def _snapshot_without_timestamp(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(snapshot or {})
        out.pop("timestamp", None)
        return out

    def _flatten_snapshot(self, value: Any, prefix: str = "") -> Dict[str, str]:
        out: Dict[str, str] = {}
        if isinstance(value, dict):
            for k in sorted(value.keys()):
                key = f"{prefix}.{k}" if prefix else str(k)
                out.update(self._flatten_snapshot(value.get(k), key))
            return out

        if isinstance(value, list):
            out[prefix] = json.dumps(value, sort_keys=True, ensure_ascii=True)
            return out

        out[prefix] = json.dumps(value, ensure_ascii=True)
        return out

    def _capability_delta_lines(self, old_snapshot: Dict[str, Any], new_snapshot: Dict[str, Any]) -> List[str]:
        old_flat = self._flatten_snapshot(self._snapshot_without_timestamp(old_snapshot))
        new_flat = self._flatten_snapshot(self._snapshot_without_timestamp(new_snapshot))

        keys = sorted(set(old_flat.keys()) | set(new_flat.keys()))
        lines: List[str] = []
        for k in keys:
            ov = old_flat.get(k)
            nv = new_flat.get(k)
            if ov == nv:
                continue
            if ov is None:
                lines.append(f"added {k}={nv}")
            elif nv is None:
                lines.append(f"removed {k}")
            else:
                lines.append(f"changed {k}: {ov} -> {nv}")
        return lines

    def _parse_capability_snapshot_record(self, text: str) -> Tuple[str, Dict[str, Any]]:
        src = (text or "").strip()
        m = re.match(r"^mina_capability_snapshot::([a-f0-9]{40})::(\{.*\})$", src, re.IGNORECASE)
        if not m:
            return "", {}
        digest = m.group(1).lower()
        payload_raw = m.group(2)
        try:
            payload = json.loads(payload_raw)
            if isinstance(payload, dict):
                return digest, payload
        except Exception:
            pass
        return digest, {}

    def _seed_runtime_capability_snapshot(self) -> None:
        """
        Persist a startup capability baseline in memory and record a delta fact
        whenever runtime capabilities/config differ from the previous snapshot.
        """
        try:
            snapshot = self._build_runtime_capability_snapshot()
            canonical_payload = self._snapshot_without_timestamp(snapshot)
            canonical = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()

            record_text = f"mina_capability_snapshot::{digest}::{json.dumps(snapshot, sort_keys=True, ensure_ascii=True)}"
            active_tools = snapshot.get("tools", {}).get("available", []) if isinstance(snapshot.get("tools", {}), dict) else []
            active_tools_text = "active_tools::" + ", ".join([str(x) for x in active_tools])

            # Find most recent prior capability snapshot for delta calculation.
            previous_snapshot: Dict[str, Any] = {}
            previous_digest = ""
            recent = self.memory.recent_memories(
                top_k=800,
                include_kinds=["procedure", "fact"],
                include_tags=["capability_snapshot"],
            )
            for item in recent or []:
                txt = str(item.get("text") or "")
                d, snap = self._parse_capability_snapshot_record(txt)
                if d:
                    previous_digest = d
                    previous_snapshot = snap
                    break

            exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    record_text,
                    include_kinds=["procedure"],
                    include_tags=["capability_snapshot"],
                ) is not None

            if not exists:
                self.memory.add_memory(
                    record_text,
                    kind="procedure",
                    tags=["user_memory", "system_seed", "startup_fact", "capability_snapshot"],
                )

            active_tools_exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                active_tools_exists = self.memory.find_memory_id_by_text(
                    active_tools_text,
                    include_kinds=["procedure"],
                    include_tags=["capability_snapshot", "active_tools"],
                ) is not None
            if not active_tools_exists:
                self.memory.add_memory(
                    active_tools_text,
                    kind="procedure",
                    tags=["user_memory", "system_seed", "startup_fact", "capability_snapshot", "active_tools"],
                )

            # If configuration/capability digest changed since the last startup snapshot,
            # store a concise delta fact that Mina can recall for "what changed" queries.
            if previous_digest and previous_digest != digest and previous_snapshot:
                delta_lines = self._capability_delta_lines(previous_snapshot, snapshot)
                if delta_lines:
                    delta_summary = " | ".join(delta_lines[:16])
                    delta_text = f"mina_capability_delta::{previous_digest}->{digest}::{delta_summary}"
                    delta_exists = False
                    if hasattr(self.memory, "find_memory_id_by_text"):
                        delta_exists = self.memory.find_memory_id_by_text(
                            delta_text,
                            include_kinds=["procedure"],
                            include_tags=["capability_delta"],
                        ) is not None
                    if not delta_exists:
                        self.memory.add_memory(
                            delta_text,
                            kind="procedure",
                            tags=["user_memory", "system_seed", "capability_delta", "suggestion_context"],
                        )
        except Exception:
            traceback.print_exc()

    def _seed_tool_inventory_memory(self) -> None:
        """
        Persist a compact inventory of available tools and their descriptions
        so Mina can recall what each tool is for across sessions.
        """
        try:
            inventory = self._usable_tool_inventory()
            if not inventory:
                return

            lines = ["Mina tool inventory:"]
            for item in inventory:
                if item.get("description"):
                    lines.append(f"- {item['name']}: {item['description']}")
                else:
                    lines.append(f"- {item['name']}")

            record_text = "\n".join(lines).strip()
            if not record_text:
                return

            exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    record_text,
                    include_kinds=["procedure"],
                    include_tags=["tool_inventory"],
                ) is not None

            if not exists:
                self.memory.add_memory(
                    record_text,
                    kind="procedure",
                    tags=["user_memory", "system_seed", "startup_fact", "tool_inventory"],
                )
        except Exception:
            traceback.print_exc()

    def _seed_tool_usage_rules_memory(self) -> None:
        """
        Persist durable action-vs-chat and tool-call formatting rules so Mina can
        recall tool usage behavior consistently across sessions.
        """
        try:
            rules_text = "\n".join([
                "Mina tool usage rules:",
                "- Treat requests as ACTION when user asks to run/create/write/read/list/search/fetch/start/stop/check or gives a path/URL/command.",
                "- Treat requests as CHAT when user asks for opinion, explanation, brainstorming, or casual conversation without execution targets.",
                "- If ACTION is likely but required target/args are missing, ask clarifying questions as needed until target/args are unambiguous.",
                "- Never claim a tool executed unless the tool result exists in this turn.",
                "- After each successful tool run, persist follow-up context so short confirmations and pivots can continue the active workflow.",
                "- Continuation context is runtime state (not disk durable) and may reset on service restart, so restate targets when needed.",
                "- Tool call format: <|tool_call>call:tool_name{key: value}<tool_call|>.",
            ]).strip()

            if not rules_text:
                return

            exists = False
            if hasattr(self.memory, "find_memory_id_by_text"):
                exists = self.memory.find_memory_id_by_text(
                    rules_text,
                    include_kinds=["procedure"],
                    include_tags=["tool_usage_rules"],
                ) is not None

            if not exists:
                self.memory.add_memory(
                    rules_text,
                    kind="procedure",
                    tags=["user_memory", "system_seed", "startup_fact", "tool_usage_rules"],
                )
        except Exception:
            traceback.print_exc()

    def _path_alias_record(self, alias: str, path: str) -> str:
        return f"path_alias::{alias.strip().lower()}::{path}"

    def _file_index_path(self) -> str:
        return os.path.join(self.workspace_root, ".mina_file_index.json")

    def _file_index_description(
        self,
        file_name: str,
        rel_path: str,
        root_kind: str,
    ) -> str:
        low_name = str(file_name or "").strip().lower()
        low_rel = str(rel_path or "").strip().replace("\\", "/").lower()

        if low_name in {"core.py", "agent/core.py"} or low_rel.endswith("agent/core.py"):
            return "Main runtime brain: routing, memory, tools, and response logic."
        if low_rel.endswith("agent/file_intents.py"):
            return "Natural-language file and directory intent parsing rules."
        if low_rel.endswith("agent/pending_actions.py"):
            return "Pending confirmations and approval phrase handling."
        if low_rel.endswith("agent/server/mk1_api.py"):
            return "HTTP API entrypoint that serves Mina process endpoints."
        if low_name == "mk1_config.json" or low_rel.endswith("config/mk1_config.json"):
            return "Runtime configuration: workspace, model, tools, and behavior flags."
        if low_name.startswith("start_") and low_name.endswith(".ps1"):
            return "PowerShell start script for a Mina service."
        if low_name.startswith("stop_") and low_name.endswith(".ps1"):
            return "PowerShell stop script for a Mina service."
        if low_name.startswith("launch_") and low_name.endswith(".py"):
            return "Python launcher/orchestration script."
        if low_name == "todo.md":
            return "Project task tracker and active work items."
        if low_name.startswith("test_") and low_name.endswith(".py"):
            return "Automated test module."
        if root_kind == "workspace":
            return "Workspace file under Mina personal root."
        return "Project file in Mina codebase."

    def _build_file_index(self, max_files: int = 6000) -> Dict[str, Any]:
        roots: List[Tuple[str, str]] = []
        workspace_root = os.path.abspath(str(self.workspace_root or ""))
        project_root = os.path.abspath(str(getattr(self, "project_root", "") or ""))

        if workspace_root and os.path.isdir(workspace_root):
            roots.append(("workspace", workspace_root))
        if project_root and os.path.isdir(project_root):
            roots.append(("project", project_root))

        skip_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            "node_modules",
            "build",
            "dist",
            "logs",
            "tmp",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
        allowed_exts = {
            ".py", ".ps1", ".json", ".md", ".txt", ".yaml", ".yml",
            ".toml", ".ini", ".js", ".ts", ".tsx", ".html", ".css",
            ".bat", ".cmd", ".sql", ".csv",
        }

        indexed_paths = set()
        entries: List[Dict[str, Any]] = []

        for root_kind, root_path in roots:
            for current, dirs, files in os.walk(root_path):
                dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
                for name in files:
                    if len(entries) >= max_files:
                        break

                    ext = os.path.splitext(name)[1].lower()
                    if ext and ext not in allowed_exts:
                        continue

                    abs_path = os.path.abspath(os.path.join(current, name))
                    norm = os.path.normcase(abs_path)
                    if norm in indexed_paths:
                        continue

                    indexed_paths.add(norm)
                    rel_path = os.path.relpath(abs_path, root_path).replace("\\", "/")
                    entries.append({
                        "name": name,
                        "name_lower": name.lower(),
                        "path": abs_path,
                        "root_kind": root_kind,
                        "rel_path": rel_path,
                        "description": self._file_index_description(name, rel_path, root_kind),
                    })

                if len(entries) >= max_files:
                    break

        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for entry in entries:
            key = str(entry.get("name_lower") or "").strip()
            if not key:
                continue
            by_name.setdefault(key, []).append(entry)

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "entry_count": len(entries),
            "entries": entries,
            "by_name": by_name,
        }
        return payload

    def _refresh_file_index(self, force: bool = False) -> None:
        index_path = self._file_index_path()

        if not force and os.path.isfile(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)

                entries = raw.get("entries", []) if isinstance(raw, dict) else []
                if isinstance(entries, list) and entries:
                    by_name: Dict[str, List[Dict[str, Any]]] = {}
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        key = str(item.get("name_lower") or str(item.get("name") or "").lower()).strip()
                        if key:
                            by_name.setdefault(key, []).append(item)

                    self._file_index = {
                        "generated_at": str(raw.get("generated_at") or ""),
                        "entries": entries,
                        "by_name": by_name,
                    }
                    return
            except Exception:
                traceback.print_exc()

        try:
            built = self._build_file_index()
            self._file_index = {
                "generated_at": str(built.get("generated_at") or ""),
                "entries": built.get("entries", []),
                "by_name": built.get("by_name", {}),
            }

            save_payload = {
                "generated_at": self._file_index.get("generated_at", ""),
                "entry_count": len(self._file_index.get("entries", []) or []),
                "entries": self._file_index.get("entries", []),
            }
            with open(index_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(save_payload, handle, indent=2, ensure_ascii=True)
                handle.write("\n")
        except Exception:
            traceback.print_exc()

    def _resolve_file_index_path(self, raw_path: str) -> str:
        candidate = str(raw_path or "").strip().strip('"\'')
        if not candidate:
            return ""

        # Only resolve simple bare file names here. Explicit/relative paths are
        # still handled by existing path normalization behavior.
        if os.path.isabs(candidate) or "\\" in candidate or "/" in candidate:
            return ""

        key = candidate.lower()
        by_name = self._file_index.get("by_name", {}) if isinstance(self._file_index, dict) else {}
        if not by_name:
            self._refresh_file_index(force=False)
            by_name = self._file_index.get("by_name", {}) if isinstance(self._file_index, dict) else {}
        matches = by_name.get(key, []) if isinstance(by_name, dict) else []
        if not matches:
            return ""

        if len(matches) == 1:
            return str(matches[0].get("path") or "")

        workspace_root = os.path.normcase(os.path.abspath(str(self.workspace_root or "")))
        project_root = os.path.normcase(os.path.abspath(str(getattr(self, "project_root", "") or "")))

        def score(match: Dict[str, Any]) -> Tuple[int, int]:
            p = os.path.normcase(os.path.abspath(str(match.get("path") or "")))
            s = 0
            if p.startswith(project_root):
                s += 20
            if p.startswith(workspace_root):
                s += 10
            if "\\agent\\" in p:
                s += 15
            depth = len([seg for seg in p.replace("/", "\\").split("\\") if seg])
            return s, -depth

        ranked = sorted(matches, key=score, reverse=True)
        return str(ranked[0].get("path") or "") if ranked else ""

    def _path_alias_deleted_record(self, alias: str) -> str:
        return f"path_alias_deleted::{alias.strip().lower()}"

    def _parse_path_alias_record(self, text: str) -> Tuple[str, str]:
        m = re.match(r'^path_alias::([^:]+)::(.+)$', (text or "").strip(), re.IGNORECASE)
        if not m:
            return "", ""
        return m.group(1).strip().lower(), m.group(2).strip()

    def _parse_path_alias_deleted_record(self, text: str) -> str:
        m = re.match(r'^path_alias_deleted::(.+)$', (text or "").strip(), re.IGNORECASE)
        if not m:
            return ""
        return m.group(1).strip().lower()

    def _get_path_alias_map(self) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        deleted_aliases = set()

        try:
            recent = self.memory.recent_memories(
                top_k=1000,
                include_kinds=["procedure", "fact"],
                include_tags=["user_memory"],
            )

            for item in recent or []:
                text = (item.get("text") or "").strip()
                if not text:
                    continue

                deleted_alias = self._parse_path_alias_deleted_record(text)
                if deleted_alias:
                    if deleted_alias not in alias_map:
                        deleted_aliases.add(deleted_alias)
                    continue

                alias, path = self._parse_path_alias_record(text)
                if not alias or not path:
                    continue

                if alias in deleted_aliases:
                    continue

                if alias not in alias_map:
                    alias_map[alias] = path
        except Exception:
            traceback.print_exc()

        return alias_map

    def _store_path_alias(self, alias: str, path: str) -> Dict[str, Any]:
        alias_clean = (alias or "").strip().lower()
        path_clean = self._normalize_path(path)

        if not alias_clean:
            return {"ok": False, "result": None, "error": "no_alias_provided"}
        if not path_clean:
            return {"ok": False, "result": None, "error": "no_path_provided"}

        text = self._path_alias_record(alias_clean, path_clean)
        try:
            dedupe_id = None
            if hasattr(self.memory, "find_memory_id_by_text"):
                dedupe_id = self.memory.find_memory_id_by_text(
                    text,
                    include_kinds=["procedure"],
                    include_tags=["path_alias"],
                )

            if dedupe_id is None:
                self.memory.add_memory(
                    text,
                    kind="procedure",
                    tags=["user_memory", "path_alias"],
                )

            return {
                "ok": True,
                "result": {
                    "alias": alias_clean,
                    "path": path_clean,
                    "stored": True,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _forget_path_alias(self, alias: str) -> Dict[str, Any]:
        alias_clean = (alias or "").strip().lower()
        if not alias_clean:
            return {"ok": False, "result": None, "error": "no_alias_provided"}

        alias_map = self._get_path_alias_map()
        if alias_clean not in alias_map:
            return {
                "ok": False,
                "result": None,
                "error": "alias_not_found",
            }

        try:
            self.memory.add_memory(
                self._path_alias_deleted_record(alias_clean),
                kind="procedure",
                tags=["user_memory", "path_alias_deleted"],
            )
            return {
                "ok": True,
                "result": {
                    "alias": alias_clean,
                    "forgotten": True,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _list_path_aliases(self) -> Dict[str, Any]:
        alias_map = self._get_path_alias_map()
        entries = [
            {"alias": alias, "path": path}
            for alias, path in sorted(alias_map.items(), key=lambda x: x[0])
        ]
        return {
            "ok": True,
            "result": {
                "aliases": entries,
                "count": len(entries),
            },
            "error": None,
        }

    def _workspace_info(self) -> Dict[str, Any]:
        try:
            self.startup_context = self._build_startup_context()
            root_exists = os.path.isdir(self.workspace_root)
            default_dirs = [
                os.path.join(self.workspace_root, d)
                for d in self.workspace_default_dirs
            ]
            existing_dirs = [d for d in default_dirs if os.path.isdir(d)]
            index_entries = self._file_index.get("entries", []) if isinstance(self._file_index, dict) else []
            index_common = []
            for item in index_entries[:20]:
                if not isinstance(item, dict):
                    continue
                index_common.append({
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or ""),
                    "description": str(item.get("description") or ""),
                })

            last_project = self.startup_context.get("last_worked_project")
            incomplete = self.startup_context.get("incomplete_projects", [])

            return {
                "ok": True,
                "result": {
                    "current_datetime": datetime.now().isoformat(timespec="seconds"),
                    "startup_time": self.startup_context.get("startup_time"),
                    "workspace_root": self.workspace_root,
                    "projects_root": self._projects_root(),
                    "project_tracker_path": self._project_tracker_path(),
                    "root_exists": root_exists,
                    "default_dirs": default_dirs,
                    "existing_default_dirs": existing_dirs,
                    "file_index": {
                        "path": self._file_index_path(),
                        "generated_at": self._file_index.get("generated_at", "") if isinstance(self._file_index, dict) else "",
                        "entry_count": len(index_entries),
                        "common_files": index_common,
                    },
                    "project_count": self.startup_context.get("project_count", 0),
                    "last_worked_project": last_project,
                    "incomplete_projects": incomplete,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _file_index_status(self) -> Dict[str, Any]:
        try:
            entries = self._file_index.get("entries", []) if isinstance(self._file_index, dict) else []
            by_name = self._file_index.get("by_name", {}) if isinstance(self._file_index, dict) else {}

            common = []
            for item in entries[:30]:
                if not isinstance(item, dict):
                    continue
                common.append({
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or ""),
                    "description": str(item.get("description") or ""),
                })

            return {
                "ok": True,
                "result": {
                    "index_path": self._file_index_path(),
                    "generated_at": self._file_index.get("generated_at", "") if isinstance(self._file_index, dict) else "",
                    "entry_count": len(entries),
                    "unique_names": len(by_name) if isinstance(by_name, dict) else 0,
                    "common_files": common,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _file_index_refresh(self) -> Dict[str, Any]:
        try:
            self._refresh_file_index(force=True)
            return self._file_index_status()
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _project_tracker_status(self) -> Dict[str, Any]:
        try:
            self.startup_context = self._build_startup_context()
            tracker = self._load_project_tracker()
            projects = tracker.get("projects", {}) if isinstance(tracker.get("projects", {}), dict) else {}
            incomplete = self._projects_to_complete(tracker)
            return {
                "ok": True,
                "result": {
                    "tracker_path": self._project_tracker_path(),
                    "updated_at": tracker.get("updated_at"),
                    "project_count": len(projects),
                    "incomplete_projects": incomplete,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _memory_status(self) -> Dict[str, Any]:
        try:
            mem_status = self.memory.get_status() if hasattr(self.memory, "get_status") else {}
            integrity = self.memory.verify_integrity() if hasattr(self.memory, "verify_integrity") else {}

            return {
                "ok": True,
                "result": {
                    "workspace_root": self.workspace_root,
                    "memory_db_path": getattr(self.memory, "db_path", None),
                    "faiss_small_path": getattr(self.memory, "faiss_small_path", None),
                    "faiss_base_path": getattr(self.memory, "faiss_base_path", None),
                    "backup_dir": getattr(self.memory, "backup_dir", None),
                    "status": mem_status,
                    "integrity": integrity,
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _memory_tidy(self, dry_run: bool = False) -> Dict[str, Any]:
        try:
            if hasattr(self.memory, "memory_hygiene"):
                out = self.memory.memory_hygiene(dry_run=dry_run, max_delete=500)
                if isinstance(out, dict):
                    ok = bool(out.get("ok", False))
                    return {
                        "ok": ok,
                        "result": out if ok else None,
                        "error": None if ok else str(out.get("error") or "memory_hygiene_failed"),
                    }
            return {"ok": False, "result": None, "error": "memory_hygiene_not_supported"}
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _startup_status(self) -> Dict[str, Any]:
        try:
            self.startup_context = self._build_startup_context()
            return {
                "ok": True,
                "result": {
                    "current_datetime": datetime.now().isoformat(timespec="seconds"),
                    "startup_time": self.startup_context.get("startup_time"),
                    "workspace_root": self.workspace_root,
                    "last_user_activity_at": self.startup_context.get("last_user_activity_at"),
                    "idle_checkin_interval_seconds": self.startup_context.get("idle_checkin_interval_seconds"),
                    "idle_checkin_pending": self.startup_context.get("idle_checkin_pending"),
                    "idle_checkin_due_since_at": self.startup_context.get("idle_checkin_due_since_at"),
                    "last_worked_project": self.startup_context.get("last_worked_project"),
                    "incomplete_projects": self.startup_context.get("incomplete_projects", []),
                },
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "result": None, "error": str(e)}

    def _run_git(self, args: List[str], timeout_sec: int = 60) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            return {
                "ok": proc.returncode == 0,
                "code": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "code": 127,
                "stdout": "",
                "stderr": "git_not_found",
            }
        except Exception as e:
            return {
                "ok": False,
                "code": 1,
                "stdout": "",
                "stderr": str(e),
            }

    def _git_is_repo(self) -> Dict[str, Any]:
        probe = self._run_git(["rev-parse", "--is-inside-work-tree"], timeout_sec=10)
        inside = probe.get("ok") and probe.get("stdout", "").strip().lower() == "true"
        return {
            "ok": bool(inside),
            "probe": probe,
        }

    def _git_init(self) -> Dict[str, Any]:
        existing = self._git_is_repo()
        if existing.get("ok"):
            root = self._run_git(["rev-parse", "--show-toplevel"], timeout_sec=10)
            return {
                "ok": True,
                "result": {
                    "already_initialized": True,
                    "repo_root": root.get("stdout", "") or os.getcwd(),
                },
                "error": None,
            }

        init = self._run_git(["init"], timeout_sec=20)
        if not init.get("ok"):
            return {"ok": False, "result": None, "error": init.get("stderr") or "git_init_failed"}

        root = self._run_git(["rev-parse", "--show-toplevel"], timeout_sec=10)
        return {
            "ok": True,
            "result": {
                "already_initialized": False,
                "repo_root": root.get("stdout", "") or os.getcwd(),
                "git_output": init.get("stdout", "") or init.get("stderr", ""),
            },
            "error": None,
        }

    def _git_status(self) -> Dict[str, Any]:
        state = self._git_is_repo()
        if not state.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": "not_a_git_repository",
            }

        branch = self._run_git(["branch", "--show-current"], timeout_sec=10)
        status = self._run_git(["status", "--short", "--branch"], timeout_sec=15)
        remotes = self._run_git(["remote", "-v"], timeout_sec=10)

        return {
            "ok": status.get("ok", False),
            "result": {
                "branch": branch.get("stdout", ""),
                "status": status.get("stdout", ""),
                "remotes": remotes.get("stdout", ""),
            },
            "error": None if status.get("ok") else (status.get("stderr") or "git_status_failed"),
        }

    def _git_snapshot(self, message: str) -> Dict[str, Any]:
        state = self._git_is_repo()
        if not state.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": "not_a_git_repository",
            }

        presync = self._git_safe_presync()
        if not presync.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": f"presync_failed: {presync.get('error')}",
            }

        add = self._run_git(["add", "-A"], timeout_sec=30)
        if not add.get("ok"):
            return {"ok": False, "result": None, "error": add.get("stderr") or "git_add_failed"}

        staged = self._run_git(["diff", "--cached", "--name-only"], timeout_sec=15)
        changed_files = [ln.strip() for ln in (staged.get("stdout", "") or "").splitlines() if ln.strip()]
        if not changed_files:
            return {
                "ok": True,
                "result": {
                    "snapshot_created": False,
                    "message": "no_changes_to_commit",
                    "presync": presync.get("result", {}),
                    "files_changed": [],
                },
                "error": None,
            }

        msg = (message or "").strip()
        if not msg:
            msg = f"mina snapshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        commit = self._run_git(["commit", "-m", msg, "--no-gpg-sign"], timeout_sec=45)
        if not commit.get("ok"):
            err = commit.get("stderr", "")
            if "Please tell me who you are" in err or "unable to auto-detect email address" in err:
                return {
                    "ok": False,
                    "result": None,
                    "error": (
                        "git_identity_not_configured. Run: "
                        "git config user.name \"Your Name\" ; "
                        "git config user.email \"you@example.com\""
                    ),
                }
            return {"ok": False, "result": None, "error": err or "git_commit_failed"}

        head = self._run_git(["rev-parse", "--short", "HEAD"], timeout_sec=10)
        return {
            "ok": True,
            "result": {
                "snapshot_created": True,
                "message": msg,
                "presync": presync.get("result", {}),
                "commit": head.get("stdout", ""),
                "files_changed": changed_files,
            },
            "error": None,
        }

    def _git_pull(self) -> Dict[str, Any]:
        state = self._git_is_repo()
        if not state.get("ok"):
            return {"ok": False, "result": None, "error": "not_a_git_repository"}

        pulled = self._run_git(["pull", "--ff-only"], timeout_sec=120)
        if not pulled.get("ok"):
            return {"ok": False, "result": None, "error": pulled.get("stderr") or "git_pull_failed"}

        return {
            "ok": True,
            "result": {
                "output": pulled.get("stdout", "") or pulled.get("stderr", ""),
            },
            "error": None,
        }

    def _git_safe_presync(self) -> Dict[str, Any]:
        state = self._git_is_repo()
        if not state.get("ok"):
            return {"ok": False, "result": None, "error": "not_a_git_repository"}

        upstream = self._run_git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            timeout_sec=10,
        )

        if not upstream.get("ok"):
            return {
                "ok": True,
                "result": {
                    "presync": "skipped",
                    "reason": "no_upstream_tracking_branch",
                    "output": "",
                },
                "error": None,
            }

        pulled = self._run_git(["pull", "--ff-only"], timeout_sec=120)
        if not pulled.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": pulled.get("stderr") or "git_presync_pull_failed",
            }

        return {
            "ok": True,
            "result": {
                "presync": "ok",
                "reason": "upstream_synced",
                "output": pulled.get("stdout", "") or pulled.get("stderr", ""),
            },
            "error": None,
        }

    def _git_push(self) -> Dict[str, Any]:
        state = self._git_is_repo()
        if not state.get("ok"):
            return {"ok": False, "result": None, "error": "not_a_git_repository"}

        presync = self._git_safe_presync()
        if not presync.get("ok"):
            return {
                "ok": False,
                "result": None,
                "error": f"presync_failed: {presync.get('error')}",
            }

        pushed = self._run_git(["push"], timeout_sec=120)
        if not pushed.get("ok"):
            return {"ok": False, "result": None, "error": pushed.get("stderr") or "git_push_failed"}

        return {
            "ok": True,
            "result": {
                "presync": presync.get("result", {}),
                "output": pushed.get("stdout", "") or pushed.get("stderr", ""),
            },
            "error": None,
        }

    def _resolve_path_alias(self, query: str) -> Optional[str]:
        q = (query or "").strip().lower()
        if not q:
            return None

        alias_map = self._get_path_alias_map()
        if not alias_map:
            return None

        candidates: List[Tuple[int, str]] = []

        def score_item(alias: str, path: str) -> int:
            score = 0
            if alias and alias in q:
                score += 100

            alias_tokens = [t for t in re.findall(r"[a-z0-9]+", alias) if len(t) > 1]
            for tok in alias_tokens:
                if tok in q:
                    score += 10

            base = os.path.basename(path).lower()
            if base and base in q:
                score += 20

            if any(k in q for k in ["run", "execute", "start", "open"]):
                if base.endswith((".ps1", ".py", ".bat", ".cmd")):
                    score += 5

            return score

        try:
            for alias, path in alias_map.items():
                s = score_item(alias, path)
                if s > 0:
                    candidates.append((s, path))

            if not candidates:
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        except Exception:
            traceback.print_exc()
            return None

    def _extract_json_object(self, text: str) -> str:
        if not text:
            return ""

        raw = self._strip_code_fences(text).strip()
        if raw.startswith("{") and raw.endswith("}"):
            return raw

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return raw[start:end + 1]
        return ""

    def _build_scaffold_plan(self, user_input: str) -> Dict[str, Any]:
        fallback_files: List[Dict[str, str]] = []
        for m in re.finditer(r'([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,8})', user_input):
            p = m.group(1).strip()
            if p:
                fallback_files.append({"path": p, "instructions": user_input})

        messages = [
            {
                "role": "system",
                "content": (
                    "Convert the user request into a workspace scaffold JSON plan. "
                    "Return ONLY JSON with keys: directories (string[]), files ({path,instructions}[]). "
                    "Use relative paths unless absolute path is explicitly requested. "
                    "Do not include markdown fences or commentary."
                ),
            },
            {
                "role": "user",
                "content": user_input,
            },
        ]

        try:
            reply = self.model.chat(messages=messages, temperature=0.1)
            raw = self._extract_text(reply)
            json_text = self._extract_json_object(raw)
            if json_text:
                plan = json.loads(json_text)
                if isinstance(plan, dict):
                    directories = plan.get("directories", [])
                    files = plan.get("files", [])
                    if not isinstance(directories, list):
                        directories = []
                    if not isinstance(files, list):
                        files = []
                    return {
                        "directories": [str(d).strip() for d in directories if str(d).strip()],
                        "files": [
                            {
                                "path": str(f.get("path", "")).strip(),
                                "instructions": str(f.get("instructions", "")).strip() or user_input,
                            }
                            for f in files
                            if isinstance(f, dict) and str(f.get("path", "")).strip()
                        ],
                    }
        except Exception:
            traceback.print_exc()

        return {
            "directories": [],
            "files": fallback_files,
        }

    def _execute_scaffold_request(self, user_input: str) -> Dict[str, Any]:
        plan = self._build_scaffold_plan(user_input)
        directories = plan.get("directories", [])
        files = plan.get("files", [])

        overwrite_existing = bool(re.search(r"\b(overwrite|replace|force|clobber)\b", user_input.lower()))

        if not directories and not files:
            return {
                "ok": False,
                "result": None,
                "error": "no_scaffold_targets_detected",
            }

        dir_created: List[str] = []
        file_written: List[str] = []
        file_skipped: List[str] = []
        errors: List[str] = []

        for d in directories:
            target_dir = self._normalize_path(d)
            r = self.tools.run("dir_create", {"path": target_dir})
            if r.get("ok"):
                p = r.get("result", {}).get("path")
                if p:
                    dir_created.append(p)
            else:
                errors.append(f"dir_create {target_dir}: {r.get('error')}")

        for f in files:
            path = str(f.get("path", "")).strip()
            if not path:
                continue

            path = self._normalize_path(path)

            instructions = str(f.get("instructions", "")).strip() or user_input
            parent = os.path.dirname(path)
            if parent:
                self.tools.run("dir_create", {"path": parent})

            content = self._generate_file_content_from_intent(
                user_input=instructions,
                target_path=path,
            )
            if not content.strip():
                errors.append(f"file_write {path}: failed_to_generate_content")
                continue

            r = self.tools.run(
                "file_write",
                {
                    "path": path,
                    "content": content,
                    "overwrite": overwrite_existing,
                },
            )
            if r.get("ok"):
                p = r.get("result", {}).get("path")
                if p:
                    file_written.append(p)
            else:
                if r.get("error") == "file_exists_no_overwrite":
                    p = r.get("result", {}).get("path") or path
                    file_skipped.append(str(p))
                else:
                    errors.append(f"file_write {path}: {r.get('error')}")

        ok = len(errors) == 0 and (len(dir_created) > 0 or len(file_written) > 0 or len(file_skipped) > 0)

        path_hints = [p for p in dir_created if p] + [os.path.dirname(p) for p in file_written if p]
        if path_hints:
            try:
                root_hint = os.path.commonpath(path_hints)
            except Exception:
                root_hint = path_hints[0]
            self._remember_active_project_path(root_hint)

        return {
            "ok": ok,
            "result": {
                "overwrite_enabled": overwrite_existing,
                "directories_created": dir_created,
                "files_written": file_written,
                "files_skipped_existing": file_skipped,
                "errors": errors,
            },
            "error": None if ok else "scaffold_incomplete",
        }

    def _iter_text_chunks(self, text: str, chunk_size: int = 64):
        value = str(text or "")
        if not value:
            return
        size = max(1, int(chunk_size))
        idx = 0
        while idx < len(value):
            yield value[idx:idx + size]
            idx += size

    def _should_run_reflex(self, user_input: str) -> bool:
        if not bool(getattr(self, "reflex_enabled", True)):
            return False

        text = (user_input or "").strip()
        if not text:
            return False

        if not bool(getattr(self, "reflex_requires_prefix", False)):
            return True

        # If prefix mode is enabled, still allow obvious command-like requests
        # so users don't have to remember special trigger words.
        if self._is_command_like_query(text):
            return True

        low = text.lower()
        prefixes = getattr(self, "reflex_prefixes", ["/tool", "tool:", "run tool"])
        return any(low.startswith(p) for p in prefixes)

    def _extract_reflex_payload(self, user_input: str) -> str:
        text = (user_input or "").strip()
        if not text:
            return ""

        low = text.lower()
        prefixes = getattr(self, "reflex_prefixes", ["/tool", "tool:", "run tool"])
        for p in prefixes:
            if low.startswith(p):
                return text[len(p):].strip()

        return text

    def _maybe_rescue_chat_suggested_action(
        self,
        messages: List[Dict[str, Any]],
        user_input: str,
        assistant_text: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        user_text = str(user_input or "").strip()
        reply_text = str(assistant_text or "").strip()
        if not user_text or not reply_text:
            return None

        action_signal = (
            self._is_command_like_query(user_text)
            or self._is_action_request_like(user_text)
            or self._is_operational_incident_report(user_text)
        )

        has_empty_tool_markup = re.search(
            r"\|tool_call\s*>\s*<\s*tool_call\|>|\|tool_call\s*>\s*</tool_call\|>|<tool_call\|>\s*\|tool_call>",
            reply_text,
            re.IGNORECASE,
        ) is not None

        if has_empty_tool_markup:
            drafted_path = self._infer_draft_target_path(user_text, reply_text)
            drafted_code = self._extract_code_block_from_text(reply_text, target_path=drafted_path)
            if drafted_path and drafted_code.strip():
                synthetic_input = f"save this to {drafted_path}"
                synthetic_reply = {
                    "choices": [
                        {
                            "message": {
                                "content": reply_text,
                            }
                        }
                    ]
                }
                return self._reflex_tools_and_memory(
                    messages=messages,
                    reply=synthetic_reply,
                    user_input=synthetic_input,
                    request_context=request_context,
                )

            # Generic malformed-call rescue: if the model emitted empty tool
            # wrappers, attempt a normal intent/planner tool execution pass.
            try:
                rescued_tool, _rescued_args = self.detect_tool_intent(user_text)
            except Exception:
                rescued_tool = None

            if not rescued_tool:
                planned = self._plan_tool_action(user_text, prior_messages=messages)
                if planned is not None:
                    rescued_tool = planned[0]

            if rescued_tool and rescued_tool != "__clarify_intent__":
                return self._reflex_tools_and_memory(
                    messages=messages,
                    reply={},
                    user_input=user_text,
                    request_context=request_context,
                )

        if not action_signal:
            return None

        suggestion_signal = (
            re.search(r"```(?:powershell|pwsh|cmd|bash|sh)?", reply_text, re.IGNORECASE) is not None
            or re.search(r"\b(?:we can run|i can run|how about we run|shall we run|quick system check|run this command)\b", reply_text, re.IGNORECASE) is not None
            or "I can run that with tools" in reply_text
        )
        if not suggestion_signal:
            return None

        try:
            tool_name, _tool_args = self.detect_tool_intent(user_text)
        except Exception:
            traceback.print_exc()
            return None

        if not tool_name:
            planned = self._plan_tool_action(user_text, prior_messages=messages)
            if planned is None:
                return None
            tool_name, _tool_args = planned

        if tool_name == "__clarify_intent__":
            return None

        return self._reflex_tools_and_memory(
            messages=messages,
            reply={},
            user_input=user_text,
            request_context=request_context,
        )

    def _direct_render_tool_names(self) -> set:
        return self._raw_direct_render_tool_names().union(
            self._grounded_direct_render_tool_names(),
        )

    def _raw_direct_render_tool_names(self) -> set:
        return {
            "__tool_list__",
            "__tool_self_test__",
            "__scratch_note__",
            "__search_mode_set__",
            "__search_mode_get__",
            "__path_alias_set__",
            "__path_alias_list__",
            "__path_alias_forget__",
            "__driving_checkin_stop__",
            "__mina_shutdown__",
            "__task_memory_write__",
            "__learn_play_song_shorthand__",
            "__scaffold__",
            "memory_write",
            "web_search",
            "web_fetch",
            "dir_create",
            "file_write",
            "file_append",
            "file_delete",
            "file_move",
            "safe_copy",
            "spotify_link",
        }

    def _grounded_direct_render_tool_names(self) -> set:
        return {
            "__workspace_info__",
            "__file_index_status__",
            "__file_index_refresh__",
            "__startup_status__",
            "__git_init__",
            "__git_status__",
            "__git_snapshot__",
            "__git_pull__",
            "__git_push__",
            "__project_test_run__",
            "__project_tracker_status__",
            "__project_tracker_update__",
            "__project_bootstrap__",
            "__memory_status__",
            "__memory_tidy__",
            "__mina_shutdown_status__",
            "dir_list",
            "file_read",
            "github_repo",
            "ps_run",
            "code_execute",
        }

    def _truth_first_direct_tool_names(self) -> set:
        return {
            "__startup_status__",
            "__memory_status__",
            "__autonomous_work_status__",
            "__project_tracker_status__",
            "__file_index_status__",
            "__mina_shutdown_status__",
            "code_execute",
            "dir_list",
            "file_read",
        }

    def _should_direct_render_tool(self, tool_name: str) -> bool:
        return str(tool_name or "") in self._direct_render_tool_names()
# ========================================================
# PROCESS
# ========================================================

    def process(
        self,
        user_input: str,
        image_attachment: Optional[Dict[str, Any]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        try:

            user_input = self._correct_contextual_transcript(user_input)

            print("\n========================================")
            print("MK1 PROCESS START")
            print("========================================")
            print("USER INPUT:")
            print(user_input)

            turn_clock = self._advance_turn_clock()
            self._refresh_idle_checkin_state()
            checkin_prompt = self._consume_idle_checkin_prompt()
            away_prompt = self._consume_away_nag_prompt()
            self._register_user_activity()

            # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            # HYBRID MEMORY ENGINE MAINTENANCE TICK
            # Runs scheduled backups, trimming, health checks.
            # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            self._maybe_run_maintenance_tick()

            fast_command_mode = bool(getattr(self, "fast_command_mode", True))
            fast_command_skip_context = bool(getattr(self, "fast_command_skip_context", False))

            command_like = fast_command_mode and self._is_command_like_query(user_input)
            ostranauts_context = build_ostranauts_context()
            live_ostranauts_query = bool(
                ostranauts_context and is_ostranauts_query(user_input)
            )
            if command_like and fast_command_skip_context:
                context = ""
            elif live_ostranauts_query:
                context = self._build_ostranauts_memory_context(user_input)
            else:
                context = self.build_context(user_input)

            working_memory = self._build_turn_working_memory(user_input)

            messages: List[Dict[str, Any]] = []

            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt,
                })

            messages.append({
                "role": "system",
                "content": self._interface_label_line(request_context),
            })

            messages.append({
                "role": "system",
                "content": self._interface_surface_line(request_context),
            })

            if context:
                messages.append({
                    "role": "system",
                    "content": f"Relevant memory context:\n{context}",
                })

            retry_prompt = self._learned_lesson_retry_prompt(context)
            if retry_prompt:
                messages.append({
                    "role": "system",
                    "content": retry_prompt,
                })

            if working_memory:
                messages.append({
                    "role": "system",
                    "content": (
                        f"{working_memory}\n"
                        "Prefer these DB-backed facts over guessing."
                    ),
                })

            if checkin_prompt:
                messages.append({
                    "role": "system",
                    "content": checkin_prompt,
                })

            if away_prompt:
                messages.append({
                    "role": "system",
                    "content": away_prompt,
                })

            due_task_prompt = self._build_due_task_prompt()
            if due_task_prompt:
                messages.append({
                    "role": "system",
                    "content": due_task_prompt,
                })

            messages.append({
                "role": "system",
                "content": self._format_turn_clock_line(turn_clock),
            })

            if str(getattr(self, "action_mode_strictness", "balanced")) in {"balanced", "strict"}:
                messages.append({
                    "role": "system",
                    "content": self._truth_first_system_rules(),
                })
                messages.append({
                    "role": "system",
                    "content": self._tool_usage_decision_rules(),
                })

            # Add tool availability hint if tools are available
            # NOTE: Don't pass tools to model.chat() - LMStudio hangs with tools parameter
            tools_obj = getattr(self, "tools", None)
            tool_schemas = tools_obj.get_tool_schemas() if tools_obj and hasattr(tools_obj, "get_tool_schemas") else []
            if tool_schemas:
                tool_names = [t["function"]["name"] for t in tool_schemas]
                messages.append({
                    "role": "system",
                    "content": (
                        f"You have access to these tools: {', '.join(tool_names)}. "
                        f"Use them proactively when the user asks for information you can fetch, files to read/write, "
                        f"or tasks you can perform. For example, use 'web_fetch' to get content from URLs, "
                        f"'file_read' for files, and 'powershell' or 'ps_run' for PowerShell commands and batches. "
                        f"When you call a tool, format it as: <|tool_call>call:tool_name{{key: value, key2: value2}}<tool_call|>. "
                        f"Never claim a tool was run unless you actually emitted a tool call token in this response. "
                        f"If the user asks to list tools, output the COMPLETE inventory from this list without omissions."
                    ),
                })

            # =================================================
            # TOOL REFLEX FIRST
            # =================================================

            reflex_reply = None
            if self._should_run_reflex(user_input):
                reflex_input = self._extract_reflex_payload(user_input)
                if reflex_input:
                    reflex_reply = self._reflex_tools_and_memory(
                        messages=messages,
                        reply={},
                        user_input=reflex_input,
                        request_context=request_context,
                    )

            if reflex_reply is not None:

                final_text = self._extract_text(
                    reflex_reply
                )

                self._store_turn_memory(
                    user_input=user_input,
                    assistant_text=final_text,
                    retry_guided=bool(retry_prompt),
                )

                return {
                    "reply": final_text
                }

            # =================================================
            # NORMAL MODEL FLOW
            # =================================================

            if ostranauts_context:
                messages.append({
                    "role": "system",
                    "content": ostranauts_context,
                })

            image_meta: Optional[Dict[str, Any]] = None
            if isinstance(image_attachment, dict):
                image_meta = {
                    "name": str(image_attachment.get("name") or "image"),
                    "type": str(image_attachment.get("type") or "image/*"),
                    "size": int(image_attachment.get("size") or 0),
                    "data_url": str(image_attachment.get("data_url") or ""),
                }

            image_forwarded = False
            if image_meta and image_meta.get("data_url"):
                if self.model.supports_vision(allow_probe=True):
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_input,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_meta["data_url"],
                                },
                            },
                        ],
                    })
                    image_forwarded = True
                else:
                    img_hint = (
                        f"[Image staged: {image_meta['name']} "
                        f"({image_meta['type']}, {max(1, image_meta['size'] // 1024)} KB)]"
                    )
                    messages.append({
                        "role": "user",
                        "content": f"{user_input}\n\n{img_hint}",
                    })
            else:
                messages.append({
                    "role": "user",
                    "content": user_input,
                })

            # NOTE: Gemma-4 doesn't support OpenAI-style tools parameter; it hangs if passed
            # Instead, the model will generate tool calls in custom format: <|tool_call>call:name{args}<tool_call|>
            reply = self.model.chat(
                messages=messages,
                tools=None,  # Don't pass tools to avoid LMStudio timeout
                allow_fallback=False,
                allow_model_load=False,
            )

            # Handle tool calls if present
            max_tool_loops = 3
            loop_count = 0
            last_assistant_content = ""
            executed_tool_events: List[Tuple[str, Dict[str, Any]]] = []
            tool_pattern = self._tool_call_pattern()
            
            while reply and loop_count < max_tool_loops:
                loop_count += 1
                
                # Get the message content
                choice = reply.get("choices", [{}])[0] if reply.get("choices") else {}
                message = choice.get("message", {})
                text_content = message.get("content", "") or choice.get("text", "")
                
                # Parse tool format. Some models emit malformed variants like
                # <spotify_link{library:"true", track_selection:"random"}>.
                matches = self._extract_tool_calls_from_text(text_content)
                
                if matches and text_content != last_assistant_content:
                    # Found new tool calls - prevent infinite loops on same content
                    last_assistant_content = text_content
                    cleaned_content = self._strip_tool_call_markup(text_content)
                    
                    messages.append({
                        "role": "assistant",
                        "content": cleaned_content or "[processing...]",
                    })
                    
                    # Execute each tool
                    for tool_name, args_str in matches:
                        tool_args = self._parse_tool_args(args_str)

                        # Support shorthand spotify action calls such as
                        # call:play_track{library:'favorites'} emitted by some models.
                        if tool_name in {
                            "play_track",
                            "play_playlist",
                            "pause",
                            "stop",
                            "shuffle_on",
                            "shuffle_off",
                            "set_shuffle",
                            "favorites_list",
                            "liked_songs",
                            "play_favorite",
                            "search_track",
                            "list_playlists",
                            "playlist_tracks",
                            "current_playback",
                            "create_playlist",
                            "add_track_to_playlist",
                            "add_tracks_to_playlist",
                            "connect_status",
                        } and "spotify_link" in getattr(self.tools, "tools", {}):
                            tool_args.setdefault("action", tool_name)
                            tool_name = "spotify_link"
                        
                        print(f"\n>>> TOOL CALL: {tool_name} {tool_args}")
                        tool_result = self.tools.run(tool_name, tool_args)
                        print(f">>> RESULT: {str(tool_result)[:200]}")

                        tool_result_dict = tool_result if isinstance(tool_result, dict) else {
                            "ok": False,
                            "result": None,
                            "error": "invalid_tool_result",
                        }
                        executed_tool_events.append((tool_name, tool_result_dict))
                        
                        messages.append({
                            "role": "tool",
                            "content": str(tool_result),
                        })
                    
                    # Get model response with tool results
                    try:
                        reply = self.model.chat(
                            messages=messages,
                            tools=None,  # Don't pass tools - LMStudio hangs
                            allow_fallback=False,
                            allow_model_load=False,
                        )
                    except Exception as e:
                        print(f"\n>>> MODEL ERROR: {e}")
                        break
                else:
                    # No new tools found, exit loop
                    break

            final_text = self._handle_model_response(
                messages,
                reply,
            )
            final_text = self._enforce_tool_outcome_truth(final_text, executed_tool_events)

            if not executed_tool_events:
                rescued = self._maybe_rescue_chat_suggested_action(
                    messages=messages,
                    user_input=user_input,
                    assistant_text=final_text,
                    request_context=request_context,
                )
                if rescued is not None:
                    final_text = self._extract_text(rescued)

            if not executed_tool_events:
                # Why: command-like prompts occasionally produced model-only terminal roleplay.
                # Guard: require a real execution path or explicit non-claim fallback.
                verified_fallback = self._maybe_enforce_verified_execution_reply(
                    messages=messages,
                    user_input=user_input,
                    assistant_text=final_text,
                    request_context=request_context,
                )
                if verified_fallback is not None:
                    final_text = verified_fallback

            self._store_turn_memory(
                user_input=user_input,
                assistant_text=final_text,
                retry_guided=bool(retry_prompt),
            )

            response: Dict[str, Any] = {
                "reply": final_text,
            }

            if image_meta is not None:
                response["image"] = {
                    "received": True,
                    "name": image_meta.get("name"),
                    "type": image_meta.get("type"),
                    "size": image_meta.get("size"),
                    "vision_model": bool(image_forwarded),
                    "forwarded_to_model": image_forwarded,
                }

            return response


        except Exception as e:

            traceback.print_exc()

            return {
                "reply": f"(Mina error: {str(e)})"
            }

    def process_stream(
        self,
        user_input: str,
        image_attachment: Optional[Dict[str, Any]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ):

        try:
            turn_clock = self._advance_turn_clock()
            self._refresh_idle_checkin_state()
            checkin_prompt = self._consume_idle_checkin_prompt()
            away_prompt = self._consume_away_nag_prompt()
            self._register_user_activity()
            self._maybe_run_maintenance_tick()

            command_like = self.fast_command_mode and self._is_command_like_query(user_input)
            ostranauts_context = build_ostranauts_context()
            live_ostranauts_query = bool(
                ostranauts_context and is_ostranauts_query(user_input)
            )
            if command_like and self.fast_command_skip_context:
                context = ""
            elif live_ostranauts_query:
                context = self._build_ostranauts_memory_context(user_input)
            else:
                context = self.build_context(user_input)

            working_memory = self._build_turn_working_memory(user_input)

            messages: List[Dict[str, Any]] = []

            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt,
                })

            messages.append({
                "role": "system",
                "content": self._interface_label_line(request_context),
            })

            messages.append({
                "role": "system",
                "content": self._interface_surface_line(request_context),
            })

            if context:
                messages.append({
                    "role": "system",
                    "content": f"Relevant memory context:\n{context}",
                })

            retry_prompt = self._learned_lesson_retry_prompt(context)
            if retry_prompt:
                messages.append({
                    "role": "system",
                    "content": retry_prompt,
                })

            if working_memory:
                messages.append({
                    "role": "system",
                    "content": (
                        f"{working_memory}\n"
                        "Prefer these DB-backed facts over guessing."
                    ),
                })

            if checkin_prompt:
                messages.append({
                    "role": "system",
                    "content": checkin_prompt,
                })

            if away_prompt:
                messages.append({
                    "role": "system",
                    "content": away_prompt,
                })

            due_task_prompt = self._build_due_task_prompt()
            if due_task_prompt:
                messages.append({
                    "role": "system",
                    "content": due_task_prompt,
                })

            messages.append({
                "role": "system",
                "content": self._format_turn_clock_line(turn_clock),
            })

            if str(getattr(self, "action_mode_strictness", "balanced")) in {"balanced", "strict"}:
                messages.append({
                    "role": "system",
                    "content": self._truth_first_system_rules(),
                })
                messages.append({
                    "role": "system",
                    "content": self._tool_usage_decision_rules(),
                })

            tool_schemas = self.tools.get_tool_schemas()
            if tool_schemas:
                tool_names = [t["function"]["name"] for t in tool_schemas]
                messages.append({
                    "role": "system",
                    "content": (
                        f"You have access to these tools: {', '.join(tool_names)}. "
                        f"Use them proactively when the user asks for information you can fetch, files to read/write, "
                        f"or tasks you can perform. For example, use 'web_fetch' to get content from URLs, "
                        f"'file_read' for files, and 'powershell' or 'ps_run' for PowerShell commands and batches. "
                        f"When you call a tool, format it as: <|tool_call>call:tool_name{{key: value, key2: value2}}<tool_call|>. "
                        f"Never claim a tool was run unless you actually emitted a tool call token in this response. "
                        f"If the user asks to list tools, output the COMPLETE inventory from this list without omissions."
                    ),
                })

            reflex_reply = None
            if self._should_run_reflex(user_input):
                reflex_input = self._extract_reflex_payload(user_input)
                if reflex_input:
                    reflex_reply = self._reflex_tools_and_memory(
                        messages=messages,
                        reply={},
                        user_input=reflex_input,
                        request_context=request_context,
                    )
            if reflex_reply is not None:
                final_text = self._extract_text(reflex_reply)
                self._store_turn_memory(
                    user_input=user_input,
                    assistant_text=final_text,
                )
                for chunk in self._iter_text_chunks(final_text):
                    yield chunk
                return

            if ostranauts_context:
                messages.append({
                    "role": "system",
                    "content": ostranauts_context,
                })

            image_meta: Optional[Dict[str, Any]] = None
            if isinstance(image_attachment, dict):
                image_meta = {
                    "name": str(image_attachment.get("name") or "image"),
                    "type": str(image_attachment.get("type") or "image/*"),
                    "size": int(image_attachment.get("size") or 0),
                    "data_url": str(image_attachment.get("data_url") or ""),
                }

            if image_meta and image_meta.get("data_url") and self.model.supports_vision(allow_probe=True):
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_input,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_meta["data_url"],
                            },
                        },
                    ],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": user_input,
                })

            tool_preflight = (
                str(getattr(self, "action_mode_strictness", "balanced") or "balanced") in {"balanced", "strict"}
                and (
                    self._is_command_like_query(user_input)
                    or self._is_action_request_like(user_input)
                    or self._is_operational_incident_report(user_input)
                )
            )

            if tool_preflight:
                try:
                    reply = self.model.chat(
                        messages=messages,
                        tools=None,
                        allow_fallback=False,
                        allow_model_load=False,
                    )

                    max_tool_loops = 3
                    loop_count = 0
                    last_assistant_content = ""
                    executed_tool_events: List[Tuple[str, Dict[str, Any]]] = []

                    while reply and loop_count < max_tool_loops:
                        loop_count += 1

                        choice = reply.get("choices", [{}])[0] if reply.get("choices") else {}
                        message = choice.get("message", {})
                        text_content = message.get("content", "") or choice.get("text", "")

                        matches = self._extract_tool_calls_from_text(text_content)

                        if matches and text_content != last_assistant_content:
                            last_assistant_content = text_content
                            cleaned_content = self._strip_tool_call_markup(text_content)

                            messages.append({
                                "role": "assistant",
                                "content": cleaned_content or "[processing...]",
                            })

                            for tool_name, args_str in matches:
                                tool_args = self._parse_tool_args(args_str)

                                if tool_name in {
                                    "play_track",
                                    "play_playlist",
                                    "pause",
                                    "stop",
                                    "shuffle_on",
                                    "shuffle_off",
                                    "set_shuffle",
                                    "favorites_list",
                                    "liked_songs",
                                    "play_favorite",
                                    "search_track",
                                    "list_playlists",
                                    "playlist_tracks",
                                    "current_playback",
                                    "create_playlist",
                                    "add_track_to_playlist",
                                    "add_tracks_to_playlist",
                                    "connect_status",
                                } and "spotify_link" in getattr(self.tools, "tools", {}):
                                    tool_args.setdefault("action", tool_name)
                                    tool_name = "spotify_link"

                                tool_result = self.tools.run(tool_name, tool_args)
                                tool_result_dict = tool_result if isinstance(tool_result, dict) else {
                                    "ok": False,
                                    "result": None,
                                    "error": "invalid_tool_result",
                                }
                                executed_tool_events.append((tool_name, tool_result_dict))

                                messages.append({
                                    "role": "tool",
                                    "content": str(tool_result),
                                })

                            reply = self.model.chat(
                                messages=messages,
                                tools=None,
                                allow_fallback=False,
                                allow_model_load=False,
                            )
                        else:
                            break

                    if executed_tool_events:
                        final_text = self._handle_model_response(messages, reply)
                        final_text = self._enforce_tool_outcome_truth(final_text, executed_tool_events)
                        self._store_turn_memory(
                            user_input=user_input,
                            assistant_text=final_text,
                        )
                        for chunk in self._iter_text_chunks(final_text):
                            yield chunk
                        return

                    preflight_text = self._handle_model_response(messages, reply)
                    rescued = self._maybe_rescue_chat_suggested_action(
                        messages=messages,
                        user_input=user_input,
                        assistant_text=preflight_text,
                        request_context=request_context,
                    )
                    if rescued is not None:
                        preflight_text = self._extract_text(rescued)
                    # Why: preflight can still return unverified transcript-like text with no tool events.
                    # Guard: force verified execution fallback before returning preflight response.
                    verified_fallback = self._maybe_enforce_verified_execution_reply(
                        messages=messages,
                        user_input=user_input,
                        assistant_text=preflight_text,
                        request_context=request_context,
                    )
                    if verified_fallback is not None:
                        preflight_text = verified_fallback
                    if preflight_text:
                        self._store_turn_memory(
                            user_input=user_input,
                            assistant_text=preflight_text,
                        )
                        for chunk in self._iter_text_chunks(preflight_text):
                            yield chunk
                        return
                except Exception:
                    traceback.print_exc()

            parts: List[str] = []
            for piece in self.model.chat_stream(
                messages=messages,
                tools=None,
                allow_fallback=False,
                allow_model_load=False,
            ):
                if not piece:
                    continue
                parts.append(piece)
                yield piece

            final_text = self._clean_response_text("".join(parts)).strip()
            final_text = self._strip_tool_call_markup(final_text)

            if not final_text:
                # Fallback in case backend didn't emit stream chunks.
                reply = self.model.chat(
                    messages=messages,
                    tools=None,
                    allow_fallback=False,
                    allow_model_load=False,
                )
                final_text = self._handle_model_response(messages, reply)
                for chunk in self._iter_text_chunks(final_text):
                    yield chunk

            rescued = self._maybe_rescue_chat_suggested_action(
                messages=messages,
                user_input=user_input,
                assistant_text=final_text,
                request_context=request_context,
            )
            if rescued is not None:
                final_text = self._extract_text(rescued)

            # Why: streamed replies can emit pseudo terminal blocks without tool execution.
            # Guard: enforce proof-backed execution or explicit non-claim fallback.
            verified_fallback = self._maybe_enforce_verified_execution_reply(
                messages=messages,
                user_input=user_input,
                assistant_text=final_text,
                request_context=request_context,
            )
            if verified_fallback is not None:
                final_text = verified_fallback

            self._store_turn_memory(
                user_input=user_input,
                assistant_text=final_text,
            )

        except Exception as e:
            traceback.print_exc()
            yield f"(Mina error: {str(e)})"
    # ========================================================
    # MEMORY CONTEXT
    # ========================================================

    def _is_command_like_query(self, text: str) -> bool:
        q = (text or "").strip().lower()
        if not q:
            return False

        # Strong signals first: explicit CLI/tool invocations and git commands.
        strong_patterns = [
            r"^git\s+\w+",
            r"^(?:run|execute)\s+(?:ps|powershell|script|pytest)\b",
            r"^(?:mkdir|cd|ls|dir|pwd|cat|type)\b",
            r"\b(?:file_read|file_write|file_append|file_delete|ps_run|web_fetch|web_search)\b",
        ]
        if any(re.search(p, q) for p in strong_patterns):
            return True

        # Natural-language command forms, but only when they clearly target tools/files.
        if re.search(r"\b(read|open|show)\s+(?:the\s+)?(?:file|folder|directory|path|repo)\b", q):
            return True
        if re.search(r"\b(write|append|delete|remove|create)\s+(?:a\s+)?(?:file|folder|directory)\b", q):
            return True
        if re.search(r"\b(run|start|stop|restart)\s+(?:the\s+)?(?:server|api|script|service|test|tests)\b", q):
            return True

        # System fact checks should route through verified tools, not chat-only prose.
        if re.search(r"\b(what|which|how\s+much|how\s+many|current|local)\b", q) and re.search(
            r"\b(cpu|gpu|ram|memory|vram|video\s+memory|system|os|platform|specs?|time|date)\b",
            q,
        ):
            return True

        # Media control commands should be treated as operational intents.
        if re.search(r"\b(play|pause|resume|continue|unpause|stop)\b", q) and re.search(
            r"\b(music|spotify|song|songs|track|tracks|playlist|playlists|liked\s+songs?|saved\s+songs?|favorites?|favourites?)\b",
            q,
        ):
            return True

        # Path-looking strings are usually operational commands.
        if re.search(r"([a-z]:\\|[.]{1,2}[\\/]|\\\w+\\|\.(ps1|py|bat|cmd)\b)", q, re.IGNORECASE):
            return True

        # Bare PowerShell style commands should be treated as executable requests.
        if re.search(r"^(?:get|select|where|set|write|new|remove|invoke|start|stop|echo|format)-[a-z]+(?:\s|$)", q, re.IGNORECASE):
            return True
        if re.search(r"\b(?:get|select|where|invoke|write|new|remove|set|echo|format)-[a-z]+\b", q, re.IGNORECASE):
            return True

        return False

    def _maybe_run_maintenance_tick(self) -> None:
        now = time.monotonic()
        min_gap = max(0, int(getattr(self, "maintenance_min_interval_seconds", 20)))
        last_tick = float(getattr(self, "_last_maintenance_tick_ts", 0.0))
        if (now - last_tick) < min_gap:
            return

        self.memory.maintenance_tick()
        self._last_maintenance_tick_ts = now

    def _is_turn_working_memory_record(self, text: str) -> bool:
        low = (text or "").strip().lower()
        if not low:
            return False

        if low.startswith("tool '") and "called with args=" in low:
            return True

        if any(low.startswith(p) for p in [
            "hardware_snapshot::",
            "test_run_result::",
            "code_run_result::",
            "mina_capability_snapshot::",
            "mina_capability_delta::",
            "path_alias::",
        ]):
            return True

        return False

    def _authoritative_working_memory_lines(self, query: str, max_lines: int = 6) -> List[str]:
        lines: List[str] = []
        seen = set()
        include_tags = ["authoritative_identity", "authoritative_profile", "relationship_identity"]

        try:
            recent = self.memory.recent_memories(
                top_k=24,
                include_kinds=["fact", "procedure", "preference"],
                include_tags=include_tags,
            )
        except Exception:
            recent = []

        for item in recent or []:
            item_tags = item.get("tags") or []
            if not any(tag in item_tags for tag in include_tags):
                continue
            compact = self._compact_working_memory_line(str(item.get("text") or ""))
            if not compact:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(compact)
            if len(lines) >= max_lines:
                return lines

        try:
            sem = self.memory.search(
                query,
                top_k=max_lines,
                include_kinds=["fact", "procedure", "preference"],
                include_tags=include_tags,
            )
        except Exception:
            sem = []

        for item in sem or []:
            item_tags = item.get("tags") or []
            if not any(tag in item_tags for tag in include_tags):
                continue
            compact = self._compact_working_memory_line(str(item.get("text") or ""))
            if not compact:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(compact)
            if len(lines) >= max_lines:
                break

        return lines

    def _compact_working_memory_line(self, text: str, max_chars: int = 280) -> str:
        s = " ".join(str(text or "").strip().split())
        if not s:
            return ""
        if len(s) <= max_chars:
            return s
        return s[:max_chars].rstrip() + "..."

    def _learned_lesson_retry_prompt(self, context: str) -> str:
        text = str(context or "")
        if "Learned lessons:" not in text:
            return ""

        lowered = text.lower()
        failure_markers = [
            "retry note:",
            "prior attempt failed",
            "failed_decision",
        ]
        if not any(marker in lowered for marker in failure_markers):
            return ""

        return (
            "A recalled lesson indicates a prior failed attempt. Do not repeat the same blocked response. "
            "Try a different safe approach first, and if that is not possible, explain the next concrete alternative."
        )

    def _learned_lesson_results(self, query: str, top_k: int = 6) -> List[Dict[str, Any]]:
        try:
            lesson_semantic = self.memory.search(
                query,
                top_k=max(1, int(top_k)),
                include_kinds=["note"],
                include_tags=["learning_lesson"],
            )
        except Exception:
            lesson_semantic = []

        return sorted(
            lesson_semantic,
            key=lambda item: (
                0 if "retry_attempt" in (item.get("tags") or []) else 1,
                -float(item.get("timestamp") or 0.0),
            ),
        )

    def _build_learned_lesson_context(self, query: str, top_k: int = 6) -> str:
        lesson_semantic = self._learned_lesson_results(query, top_k=top_k)
        if not lesson_semantic:
            return ""

        lines = ["Learned lessons:"]
        seen = set()
        for item in lesson_semantic:
            txt = str(item.get("text") or "").strip()
            if not txt:
                continue
            key = txt.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {txt}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _build_ostranauts_memory_context(self, query: str, top_k: int = 6) -> str:
        learned = self._build_learned_lesson_context(query, top_k=top_k)

        try:
            tagged = self.memory.search(
                query,
                top_k=max(1, int(top_k)),
                include_kinds=["note", "procedure", "fact", "interaction"],
                include_tags=["ostranauts"],
            )
        except Exception:
            tagged = []

        lines = []
        seen = set()

        if learned:
            lines.append(learned)
            for line in learned.splitlines()[1:]:
                cleaned = str(line).strip()
                if cleaned.startswith("-"):
                    seen.add(cleaned.lstrip("- ").lower())

        ostr_lines: List[str] = []
        for item in tagged or []:
            txt = str(item.get("text") or "").strip()
            if not txt:
                continue
            key = txt.lower()
            if key in seen:
                continue
            seen.add(key)
            ostr_lines.append(f"- {txt}")

        if ostr_lines:
            if lines:
                lines.append("")
            lines.append("Ostranauts memory:")
            lines.extend(ostr_lines[:max(1, int(top_k))])

        return "\n".join(lines).strip()

    def _build_turn_working_memory(self, query: str) -> str:
        """
        Build a compact, authoritative per-turn working-memory block from DB.
        This complements semantic recall and helps prevent same-turn detail loss.
        """
        try:
            recent = self.memory.recent_memories(
                top_k=240,
                include_kinds=["tool", "procedure", "fact"],
            )
        except Exception:
            recent = []

        lines: List[str] = self._authoritative_working_memory_lines(query=query, max_lines=6)
        seen = {ln.lower() for ln in lines}

        for item in recent or []:
            txt = str(item.get("text") or "").strip()
            if not txt:
                continue
            if not self._is_turn_working_memory_record(txt):
                continue

            compact = self._compact_working_memory_line(txt)
            if not compact:
                continue

            k = compact.lower()
            if k in seen:
                continue
            seen.add(k)
            lines.append(compact)

            if len(lines) >= 10:
                break

        try:
            sem = self.memory.search(
                query,
                top_k=5,
                include_kinds=["tool", "procedure", "fact"],
                include_tags=["suggestion_context", "tool_result", "capability_delta", "capability_snapshot"],
            )
        except Exception:
            sem = []

        for item in sem or []:
            txt = str(item.get("text") or "").strip()
            compact = self._compact_working_memory_line(txt)
            if not compact:
                continue

            k = compact.lower()
            if k in seen:
                continue
            seen.add(k)
            lines.append(compact)

            if len(lines) >= 12:
                break

        if not lines:
            return ""

        out = ["Turn working memory (authoritative DB facts):"]
        for ln in lines[:12]:
            out.append(f"- {ln}")

        return "\n".join(out)

    def build_context(self, query: str) -> str:
        try:
            recent_turns = self.memory.recent_memories(
                top_k=6,
                include_kinds=["interaction"],
                include_tags=["short_term"],
            )

            short_semantic = self.memory.search(
                query,
                top_k=4,
                include_kinds=["interaction"],
                include_tags=["short_term"],
            )

            lesson_semantic = self._learned_lesson_results(query, top_k=6)

            long_semantic = self.memory.search(
                query,
                top_k=4,
                include_kinds=["fact", "tool", "preference", "procedure"],
            )

            if not recent_turns and not short_semantic and not lesson_semantic and not long_semantic:
                return ""

            lines: List[str] = []
            seen = set()

            short_items = recent_turns + short_semantic
            if short_items:
                lines.append("Short-term recall:")
                for r in short_items:
                    txt = r.get("text", "").strip()
                    if not txt or txt in seen:
                        continue
                    seen.add(txt)
                    lines.append(f"- {txt}")

            if long_semantic:
                if lines:
                    lines.append("")
                lines.append("Long-term recall:")
                for r in long_semantic:
                    txt = r.get("text", "").strip()
                    if not txt or txt in seen:
                        continue
                    seen.add(txt)
                    lines.append(f"- {txt}")

            if lesson_semantic:
                if lines:
                    lines.append("")
                lines.append("Learned lessons:")
                for r in lesson_semantic:
                    txt = r.get("text", "").strip()
                    if not txt or txt in seen:
                        continue
                    seen.add(txt)
                    lines.append(f"- {txt}")

            if not lines:
                return ""

            return "\n".join(lines[:28])

        except Exception:
            traceback.print_exc()
            return ""

    # ========================================================
    # FORMAT TOOL RESULT
    # ========================================================

    def _result_payload(self, result: Dict[str, Any]) -> Dict[str, Any]:
        payload = result.get("result", {})
        return payload if isinstance(payload, dict) else {}

    def _format_simple_path_result(
        self,
        result: Dict[str, Any],
        fail_prefix: str,
        ok_prefix: str,
        ok_key: str,
        path_key: str = "path",
        fallback_ok: str = "Operation completed.",
    ) -> str:
        if not result.get("ok"):
            return f"{fail_prefix}: {result.get('error')}"
        payload = self._result_payload(result)
        if payload.get(ok_key):
            return f"{ok_prefix}: {payload.get(path_key)}"
        return fallback_ok

    def _format_generic_tool_failure(self, tool_name: str, result: Dict[str, Any]) -> str:
        if not isinstance(result, dict):
            return f"{tool_name} failed"

        payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
        error_text = str(
            result.get("error")
            or payload.get("error")
            or payload.get("message")
            or payload.get("detail")
            or ""
        ).strip()
        stderr = str(payload.get("stderr") or "").strip()
        stdout = str(payload.get("stdout") or "").strip()

        if stderr:
            if error_text and error_text.lower() != stderr.lower():
                return f"{tool_name} failed: {error_text}\n\nSTDERR:\n{stderr}"
            return f"{tool_name} failed:\n\nSTDERR:\n{stderr}"

        if error_text:
            return f"{tool_name} failed: {error_text}"

        if stdout:
            return f"{tool_name} failed:\n\nSTDOUT:\n{stdout}"

        status = str(payload.get("status") or "").strip()
        if status:
            return f"{tool_name} failed: {status}"

        return f"{tool_name} failed"

    def _format_tool_result(
        self,
        tool_name: str,
        result: Dict[str, Any],
    ) -> str:

        if not isinstance(result, dict):
            return str(result)

        if tool_name == "__tool_list__":
            inventory = result.get("inventory", [])
            if not inventory:
                tools = result.get("tools", [])
                if not tools:
                    return "No tools available."
                lines = ["Available tools:", ""]
                for t in tools:
                    lines.append(f"- {t}")
                return "\n".join(lines)

            lines = ["Available tools:", ""]
            for item in inventory:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    desc = str(item.get("description") or "").strip()
                    if not name:
                        continue
                    if desc:
                        lines.append(f"- {name}: {desc}")
                    else:
                        lines.append(f"- {name}")
                else:
                    lines.append(f"- {item}")
            return "\n".join(lines)

        if tool_name == "__tool_self_test__":
            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            selected = payload.get("selected", []) if isinstance(payload.get("selected", []), list) else []
            excluded = payload.get("excluded", []) if isinstance(payload.get("excluded", []), list) else []
            report_path = str(payload.get("report_path") or "").strip()
            runs = payload.get("runs", []) if isinstance(payload.get("runs", []), list) else []
            failed_count = int(payload.get("failed_count") or 0)

            lines: List[str] = []
            lines.append("Tool self-test completed." if result.get("ok") else "Tool self-test completed with failures.")
            if selected:
                lines.append("Selected tools: " + ", ".join([str(x) for x in selected]))
            if excluded:
                lines.append("Excluded tools: " + ", ".join([str(x) for x in excluded]))
            lines.append(f"Failed count: {failed_count}")

            for row in runs:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("tool") or "")
                ok_flag = bool(row.get("ok"))
                err = str(row.get("error") or "")
                duration = int(row.get("duration_ms") or 0)
                if ok_flag:
                    lines.append(f"- PASS {name} ({duration}ms)")
                else:
                    lines.append(f"- FAIL {name} ({duration}ms): {err}")

            if report_path:
                lines.append(f"Report: {report_path}")

            return "\n".join(lines)

        if tool_name == "__scratch_note__":
            if not result.get("ok"):
                return f"Scratch note failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            path = payload.get("path") or payload.get("written_path") or payload.get("dst") or ""
            if path:
                return f"Scratch note saved: {path}"
            return "Scratch note saved."

        if tool_name == "file_read":
            if not result.get("ok"):
                return f"File read failed: {result.get('error')}"
            content = result.get("result", {}).get("content", "")
            return content or "(empty file)"

        if tool_name == "dir_list":
            if not result.get("ok"):
                return f"Directory listing failed: {result.get('error')}"
            files = result.get("result", {}).get("items", [])
            if not files:
                base = "(directory empty)"
            else:
                base = "\n".join(files)

            if str(result.get("_followup_hint") or "") == "focus_prompt":
                target = str(result.get("_followup_target") or "").strip()
                if target:
                    return (
                        f"{base}\n\n"
                        f"I scanned this directory. I can focus on {target} next unless you want another file. "
                        "How should I proceed?"
                    )
                return (
                    f"{base}\n\n"
                    "I scanned this directory. Which file should I focus on next, and how should I proceed?"
                )

            return base

        if tool_name == "dir_create":
            if not result.get("ok"):
                return f"Directory create failed: {result.get('error')}"
            path = result.get("result", {}).get("path")
            already_exists = result.get("result", {}).get("already_exists")
            if already_exists:
                return f"Directory already exists: {path}"
            return f"Directory created: {path}"

        if tool_name == "__scaffold__":
            payload = result.get("result", {})
            if not payload and not result.get("ok"):
                return f"Scaffold failed: {result.get('error')}"

            dirs = payload.get("directories_created", [])
            files = payload.get("files_written", [])
            skipped = payload.get("files_skipped_existing", [])
            errors = payload.get("errors", [])
            overwrite_enabled = payload.get("overwrite_enabled", False)

            lines = [
                "Scaffold completed." if result.get("ok") else "Scaffold finished with issues.",
                f"Overwrite mode: {'ON' if overwrite_enabled else 'SAFE (skip existing files)'}",
            ]
            if dirs:
                lines.append("Directories:")
                lines.extend([f"- {d}" for d in dirs])
            if files:
                lines.append("Files:")
                lines.extend([f"- {f}" for f in files])
            if skipped:
                lines.append("Skipped existing files:")
                lines.extend([f"- {s}" for s in skipped])
            if errors:
                lines.append("Errors:")
                lines.extend([f"- {e}" for e in errors])
            return "\n".join(lines)

        if tool_name == "__path_alias_set__":
            if not result.get("ok"):
                return f"Path alias save failed: {result.get('error')}"
            alias = result.get("result", {}).get("alias")
            path = result.get("result", {}).get("path")
            return f"Path alias saved: '{alias}' -> {path}"

        if tool_name == "__path_alias_list__":
            if not result.get("ok"):
                return f"Path alias list failed: {result.get('error')}"
            aliases = result.get("result", {}).get("aliases", [])
            if not aliases:
                return "No saved path aliases."
            lines = ["Saved path aliases:"]
            for item in aliases:
                lines.append(f"- {item.get('alias')}: {item.get('path')}")
            return "\n".join(lines)

        if tool_name == "__path_alias_forget__":
            if not result.get("ok"):
                return f"Path alias forget failed: {result.get('error')}"
            alias = result.get("result", {}).get("alias")
            return f"Path alias removed: '{alias}'"

        if tool_name == "__workspace_info__":
            if not result.get("ok"):
                return f"Workspace info failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = [
                f"Workspace root: {payload.get('workspace_root')}",
                f"Current datetime: {payload.get('current_datetime')}",
                f"Root exists: {payload.get('root_exists')}",
            ]
            last_project = payload.get("last_worked_project")
            if isinstance(last_project, dict):
                lines.append(
                    f"Last worked project: {last_project.get('name')} ({last_project.get('path')})"
                )

            incomplete = payload.get("incomplete_projects", [])
            if incomplete:
                lines.append("Incomplete projects:")
                for p in incomplete[:10]:
                    lines.append(f"- {p.get('name')}: {p.get('path')}")

            dirs = payload.get("default_dirs", [])
            if dirs:
                lines.append("Default directories:")
                lines.extend([f"- {d}" for d in dirs])
            return "\n".join(lines)

        if tool_name == "__startup_status__":
            if not result.get("ok"):
                return f"Startup status failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = [
                f"Current datetime: {payload.get('current_datetime')}",
                f"Startup time: {payload.get('startup_time')}",
                f"Workspace root: {payload.get('workspace_root')}",
            ]

            last_project = payload.get("last_worked_project")
            if isinstance(last_project, dict):
                lines.append(
                    f"Last worked project: {last_project.get('name')} ({last_project.get('path')})"
                )

            incomplete = payload.get("incomplete_projects", [])
            if incomplete:
                lines.append("Projects to complete:")
                for p in incomplete[:10]:
                    lines.append(f"- {p.get('name')}: {p.get('path')}")
            else:
                lines.append("Projects to complete: none detected")

            return "\n".join(lines)

        if tool_name == "__autonomous_work_status__":
            if not result.get("ok"):
                return f"Autonomous status failed: {result.get('error')}"

            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            cur = payload.get("curiosity", {}) if isinstance(payload.get("curiosity", {}), dict) else {}
            tool_state = payload.get("autonomous_tool_status", {}) if isinstance(payload.get("autonomous_tool_status", {}), dict) else {}
            pending = int(payload.get("pending_candidate_count") or 0)
            lines = [
                f"Autonomous enabled: {cur.get('enabled')}",
                f"Worker running: {cur.get('running')}",
                f"Autonomous paused: {cur.get('paused')}",
                f"Pending tasks: {pending}",
                f"Allowed autonomous tools: {', '.join(tool_state.get('allowed', []) or []) or '(none)'}",
                f"Available autonomous tools: {', '.join(tool_state.get('available', []) or []) or '(none)'}",
            ]
            preview = payload.get("pending_candidates", []) if isinstance(payload.get("pending_candidates", []), list) else []
            if preview:
                lines.append("Next tasks:")
                for item in preview[:5]:
                    if not isinstance(item, dict):
                        continue
                    lines.append(f"- {item.get('title')} [{item.get('source')}]")
            return "\n".join(lines)

        if tool_name == "__autonomous_work_run__":
            if not result.get("ok"):
                return f"Autonomous work run failed: {result.get('error')}"

            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            if not bool(payload.get("ran", False)):
                return "No pending autonomous tasks to run right now."

            task = payload.get("task", {}) if isinstance(payload.get("task", {}), dict) else {}
            task_result = payload.get("task_result", {}) if isinstance(payload.get("task_result", {}), dict) else {}
            lines = [
                f"Autonomous task ran: {task.get('title')}",
                f"Source: {task.get('source')}",
                f"Result: {'ok' if task_result.get('ok') else 'failed'}",
            ]
            summary = str(task_result.get("summary") or "").strip()
            if summary:
                lines.append(f"Summary: {summary}")
            patch = task_result.get("patch", {}) if isinstance(task_result.get("patch", {}), dict) else {}
            if patch:
                lines.append(f"Patch: applied={patch.get('applied')} reason={patch.get('reason')}")
            lines.append(f"Remaining queued tasks: {payload.get('pending_candidate_count')}")
            return "\n".join(lines)

        if tool_name == "__git_init__":
            if not result.get("ok"):
                return f"Git init failed: {result.get('error')}"
            payload = result.get("result", {})
            if payload.get("already_initialized"):
                return f"Git is already initialized at: {payload.get('repo_root')}"
            return f"Git initialized at: {payload.get('repo_root')}"

        if tool_name == "__git_status__":
            if not result.get("ok"):
                return f"Git status failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = [
                f"Branch: {payload.get('branch') or '(unknown)'}",
                "Status:",
                payload.get("status") or "(clean)",
            ]
            remotes = (payload.get("remotes") or "").strip()
            if remotes:
                lines.extend(["Remotes:", remotes])
            return "\n".join(lines)

        if tool_name == "__git_snapshot__":
            if not result.get("ok"):
                return f"Git snapshot failed: {result.get('error')}"
            payload = result.get("result", {})
            if not payload.get("snapshot_created"):
                lines = ["No changes to commit. Working tree is clean."]
                presync = payload.get("presync", {}) if isinstance(payload.get("presync", {}), dict) else {}
                if presync.get("presync") == "ok":
                    lines.append("Pre-sync: fast-forward pull succeeded.")
                elif presync.get("presync") == "skipped":
                    lines.append(f"Pre-sync: skipped ({presync.get('reason')}).")
                return "\n".join(lines)
            lines = [
                f"Snapshot created: {payload.get('commit')}",
                f"Message: {payload.get('message')}",
            ]
            presync = payload.get("presync", {}) if isinstance(payload.get("presync", {}), dict) else {}
            if presync.get("presync") == "ok":
                lines.append("Pre-sync: fast-forward pull succeeded.")
            elif presync.get("presync") == "skipped":
                lines.append(f"Pre-sync: skipped ({presync.get('reason')}).")
            changed = payload.get("files_changed", [])
            if changed:
                lines.append("Files:")
                lines.extend([f"- {p}" for p in changed[:50]])
            return "\n".join(lines)

        if tool_name == "__git_pull__":
            if not result.get("ok"):
                return f"Git pull failed: {result.get('error')}"
            payload = result.get("result", {})
            return payload.get("output") or "Git pull completed."

        if tool_name == "__git_push__":
            if not result.get("ok"):
                return f"Git push failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = []
            presync = payload.get("presync", {}) if isinstance(payload.get("presync", {}), dict) else {}
            if presync.get("presync") == "ok":
                lines.append("Pre-sync: fast-forward pull succeeded.")
            elif presync.get("presync") == "skipped":
                lines.append(f"Pre-sync: skipped ({presync.get('reason')}).")
            lines.append(payload.get("output") or "Git push completed.")
            return "\n".join(lines)

        if tool_name == "__project_test_run__":
            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            lines = [
                f"Project: {payload.get('project_path')}",
                f"Command: {payload.get('command')}",
                f"Exit code: {payload.get('exit_code')}",
            ]
            if payload.get("retried_after_fix"):
                lines.append("Auto-fix retry: attempted")
            changed = payload.get("changed_files", [])
            if changed:
                lines.append("Changed files:")
                lines.extend([f"- {p}" for p in changed])

            if not result.get("ok") and result.get("error"):
                lines.append(f"Result: failed ({result.get('error')})")

            stdout = (payload.get("stdout") or "").strip()
            stderr = (payload.get("stderr") or "").strip()
            if stdout:
                lines.extend(["STDOUT:", stdout])
            if stderr:
                lines.extend(["STDERR:", stderr])
            return "\n".join(lines)

        if tool_name == "__project_tracker_update__":
            if not result.get("ok"):
                return f"Project tracker update failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            return (
                f"Project updated: {payload.get('project')}\n"
                f"Status: {payload.get('status')}\n"
                f"Next steps: {payload.get('next_steps')}"
            )

        if tool_name == "__memory_status__":
            if not result.get("ok"):
                return f"Memory status failed: {result.get('error')}"

            payload = result.get("result", {})
            status = payload.get("status", {}) if isinstance(payload.get("status", {}), dict) else {}
            integrity = payload.get("integrity", {}) if isinstance(payload.get("integrity", {}), dict) else {}

            lines = [
                f"Workspace root: {payload.get('workspace_root')}",
                f"Memory DB: {payload.get('memory_db_path')}",
                f"FAISS small: {payload.get('faiss_small_path')}",
                f"FAISS base: {payload.get('faiss_base_path')}",
                f"Backup dir: {payload.get('backup_dir')}",
                f"Memory OK: {status.get('ok')}",
                f"DB OK: {status.get('db_ok')}",
                f"Embed small/base OK: {status.get('embed_small_ok')}/{status.get('embed_base_ok')}",
            ]

            if integrity:
                lines.append(
                    f"Indexes loaded (small/base): {integrity.get('small_index_loaded')}/{integrity.get('base_index_loaded')}"
                )
                lines.append(
                    f"Index totals (small/base): {integrity.get('small_index_ntotal')}/{integrity.get('base_index_ntotal')}"
                )

            return "\n".join(lines)

        if tool_name == "__memory_tidy__":
            if not result.get("ok"):
                return f"Memory tidy failed: {result.get('error')}"

            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            lines = [
                f"Mode: {'DRY RUN' if payload.get('dry_run') else 'APPLY'}",
                f"Total memory rows scanned: {payload.get('total_rows')}",
                f"Rows flagged for cleanup: {payload.get('delete_count')}",
            ]
            samples = payload.get("samples", []) if isinstance(payload.get("samples", []), list) else []
            if samples:
                lines.append("Sample cleaned items:")
                lines.extend([f"- {s}" for s in samples[:10]])
            return "\n".join(lines)

        if tool_name == "__project_bootstrap__":
            if not result.get("ok"):
                return f"Project bootstrap failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = [
                f"Project created: {payload.get('project')}",
                f"Path: {payload.get('path')}",
                f"Status: {payload.get('status')}",
                f"Next steps: {payload.get('next_steps')}",
            ]
            files = payload.get("files_created", [])
            if files:
                lines.append("Files created:")
                lines.extend([f"- {f}" for f in files])
            return "\n".join(lines)

        if tool_name == "github_repo":
            if not result.get("ok"):
                return f"GitHub request failed: {result.get('error')}"
            payload = result.get("result")
            if payload is None:
                return "No GitHub data returned."
            try:
                return json.dumps(payload, indent=2, ensure_ascii=False)
            except Exception:
                return str(payload)

        if tool_name == "memory_read":
            if not result.get("ok"):
                return f"Memory read failed: {result.get('error')}"
            entries = result.get("results", [])
            if not entries:
                return "No matching memories found."
            lines = []
            seen = set()
            for item in entries:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "") or "").strip()
                if not text:
                    continue
                norm = " ".join(text.lower().split())
                if norm in seen:
                    continue
                seen.add(norm)
                lines.append(text)
            return "\n".join(lines) if lines else "No matching memories found."

        if tool_name == "file_write":
            return self._format_simple_path_result(
                result=result,
                fail_prefix="File write failed",
                ok_prefix="File written to",
                ok_key="written",
                fallback_ok="File write completed.",
            )

        if tool_name == "file_append":
            return self._format_simple_path_result(
                result=result,
                fail_prefix="File append failed",
                ok_prefix="Content appended to",
                ok_key="appended",
                fallback_ok="File append completed.",
            )

        if tool_name == "file_move":
            if not result.get("ok"):
                return f"File move failed: {result.get('error')}"
            moved = result.get("result", {}).get("moved")
            src = result.get("result", {}).get("src")
            dst = result.get("result", {}).get("dst")
            if moved:
                return f"File moved from {src} to {dst}"
            return "File move completed."

        if tool_name == "safe_copy":
            if not result.get("ok"):
                return f"Safe copy failed: {result.get('error')}"
            payload = result.get("result", {})
            progress = payload.get("progress", [])
            if progress:
                return "\n".join(progress)
            copied = payload.get("copied")
            dst = payload.get("dst")
            if copied and dst:
                return f"File copied to: {dst}"
            return "File copy completed."

        if tool_name == "file_delete":
            return self._format_simple_path_result(
                result=result,
                fail_prefix="File delete failed",
                ok_prefix="File deleted",
                ok_key="deleted",
                fallback_ok="File delete completed.",
            )

        if tool_name == "memory_write":
            if not result.get("ok"):
                return f"Memory write failed: {result.get('error')}"
            stored = result.get("stored")
            if stored:
                return f"Stored memory: {stored}"
            return "Memory stored successfully."

        if tool_name == "__task_memory_write__":
            if not result.get("ok"):
                return f"Task memory write failed: {result.get('error')}"
            stored = result.get("stored")
            if stored:
                return f"Scheduled task stored: {stored}"
            return "Scheduled task stored successfully."

        if tool_name == "__learn_play_song_shorthand__":
            if not result.get("ok"):
                return f"Play shorthand learning failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            phrase = str(payload.get("phrase") or "").strip()
            track = str(payload.get("track_query") or "").strip()
            if phrase and track:
                return f"Learned: '{phrase}' means play the song '{track}'."
            return "Play shorthand learned."

        if tool_name == "web_fetch":
            if not result.get("ok"):
                return f"Web fetch failed: {result.get('error')}"

            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            content = str(payload.get("content") or "").strip()
            if not content:
                return "Web fetch succeeded but returned empty content."

            # Turn HTML-heavy pages into readable plain text so Mina can summarize naturally.
            text = content
            if "<" in text and ">" in text:
                text = re.sub(r"<script[\\s\\S]*?</script>", " ", text, flags=re.IGNORECASE)
                text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = html.unescape(text)
                text = re.sub(r"\\s+", " ", text).strip()

            if len(text) > 1800:
                text = text[:1800].rstrip() + "..."

            return text

        if tool_name == "web_search":
            if not result.get("ok"):
                return f"Web search failed: {result.get('error')}"

            payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
            items = payload.get("results", []) if isinstance(payload.get("results", []), list) else []
            if not items:
                q = payload.get("query") or "(no query)"
                mode = str(payload.get("mode") or "balanced")
                return f"No web results found for: {q} (mode={mode})"

            def _extract_gpu_model(query_text: str) -> str:
                qtxt = str(query_text or "")
                m = re.search(r"\b(rx|rtx|gtx)\s*(\d{3,4})\s*(xt|ti)?\b", qtxt, re.IGNORECASE)
                if m:
                    brand = m.group(1).upper()
                    number = m.group(2)
                    suffix = (m.group(3) or "").upper()
                    if suffix:
                        return f"{brand} {number} {suffix}"

                m2 = re.search(r"\b(radeon|geforce)\s+([A-Za-z0-9\-\s]{3,30})\b", qtxt, re.IGNORECASE)
                if m2:
                    return f"{m2.group(1).title()} {m2.group(2).strip()}"
                return ""

            def _extract_vram_gb(text_value: str) -> Optional[int]:
                src = str(text_value or "")
                if not src:
                    return None

                patterns = [
                    r"\b(\d{1,2})\s*gb\s*(?:of\s+)?(?:gddr\d+\s+)?(?:video\s+)?(?:v)?ram\b",
                    r"\b(?:vram|video\s+memory|memory)\s*[:\-]?\s*(\d{1,2})\s*gb\b",
                    r"\b(\d{1,2})\s*gb\s*gddr\d+\b",
                ]
                for pat in patterns:
                    m = re.search(pat, src, re.IGNORECASE)
                    if m:
                        try:
                            value = int(m.group(1))
                        except Exception:
                            value = 0
                        if 1 <= value <= 64:
                            return value
                return None

            query_text = str(payload.get("query") or "")
            gpu_spec_q = (
                re.search(r"\b(rx|rtx|gtx|radeon|geforce)\b", query_text, re.IGNORECASE) is not None
                and re.search(r"\b(ram|vram|video\s+memory|memory)\b", query_text, re.IGNORECASE) is not None
            )

            if gpu_spec_q:
                evidence_values: List[int] = []
                source_urls: List[str] = []

                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    source_url = str(item.get("url") or "").strip()
                    if source_url and source_url not in source_urls:
                        source_urls.append(source_url)

                    scan_fields = [
                        str(item.get("title") or ""),
                        str(item.get("snippet") or ""),
                        str(item.get("content_excerpt") or ""),
                    ]
                    for field in scan_fields:
                        v = _extract_vram_gb(field)
                        if v is not None:
                            evidence_values.append(v)

                if evidence_values:
                    counts: Dict[int, int] = {}
                    for value in evidence_values:
                        counts[value] = counts.get(value, 0) + 1
                    best_value = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    gpu_model = _extract_gpu_model(query_text)
                    label = gpu_model if gpu_model else "That GPU"

                    lines = [f"{label} has {best_value} GB of VRAM."]
                    if source_urls:
                        lines.append("Sources:")
                        for idx, link in enumerate(source_urls[:3], start=1):
                            lines.append(f"{idx}. {link}")
                    return "\n".join(lines)

            lines = []
            q = payload.get("query")
            if q:
                lines.append(f"Web results for: {q}")
                lines.append("")

            for idx, item in enumerate(items[:8], start=1):
                title = str(item.get("title") or "(untitled)").strip()
                url = str(item.get("url") or "").strip()
                snippet = str(item.get("snippet") or "").strip()

                lines.append(f"{idx}. {title}")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet}")

            return "\n".join(lines)

        if tool_name == "__search_mode_set__":
            if not result.get("ok"):
                payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
                current = payload.get("current_mode")
                return f"Search mode update failed: {result.get('error')} (current={current})"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            return f"Web search mode set to: {payload.get('mode')}"

        if tool_name == "__search_mode_get__":
            if not result.get("ok"):
                return f"Search mode check failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            mode = payload.get("mode")
            allowed = payload.get("allowed") or ["balanced", "strict", "wide"]
            return f"Current web search mode: {mode} (allowed: {', '.join([str(x) for x in allowed])})"

        if tool_name == "__driving_checkin_stop__":
            if not result.get("ok"):
                return f"Driving check-in stop failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            stopped = int(payload.get("stopped_count") or 0)
            if stopped <= 0:
                return "No active driving check-ins were found."
            return f"Stopped {stopped} driving check-in reminder(s)."

        if tool_name == "__driving_checkin_start__":
            if not result.get("ok"):
                return f"Driving check-in start failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            repeat_seconds = int(payload.get("repeat_seconds") or 1200)
            repeat_minutes = max(1, int(round(float(repeat_seconds) / 60.0)))
            reset_count = int(payload.get("reset_count") or 0)
            due_at = payload.get("due_at")

            due_text = "soon"
            if due_at is not None:
                try:
                    due_text = datetime.fromtimestamp(float(due_at)).strftime("%a %b %d %I:%M %p")
                except Exception:
                    due_text = str(due_at)

            if reset_count > 0:
                return (
                    f"Driving check-ins started every {repeat_minutes} minutes. "
                    f"Next due at {due_text}. Reset {reset_count} older driving reminder(s)."
                )
            return f"Driving check-ins started every {repeat_minutes} minutes. Next due at {due_text}."

        if tool_name == "__mina_shutdown_status__":
            if not result.get("ok"):
                return f"Shutdown status check failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            ready = bool(payload.get("buffer_ready", False))
            pending = int(payload.get("pending") or 0)
            recommendation = str(payload.get("wait_recommendation") or "").strip()
            last_flush = payload.get("last_flush_result", {}) if isinstance(payload.get("last_flush_result"), dict) else {}
            flushed = int(last_flush.get("flushed") or 0)
            failed = int(last_flush.get("failed") or 0)
            force_hint = str(payload.get("force_override_hint") or "").strip()
            return (
                f"Shutdown ready={ready}; pending buffer items={pending}; "
                f"last flush flushed={flushed} failed={failed}. {recommendation}"
                + (f" {force_hint}" if force_hint else "")
            )

        if tool_name == "__mina_shutdown__":
            if not result.get("ok"):
                return f"Safe shutdown failed: {result.get('error')}"
            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            script_path = str(payload.get("script") or "").strip()
            port = payload.get("port")
            gate = payload.get("gate", {}) if isinstance(payload.get("gate"), dict) else {}
            waited = gate.get("waited_seconds")
            pending = gate.get("pending")
            forced = bool(gate.get("forced", False))
            if script_path:
                return (
                    f"Safe shutdown requested. Running {script_path} on port {port}. "
                    f"Buffer wait={waited}s pending={pending} forced={forced}."
                )
            return "Safe shutdown requested."

        if tool_name == "spotify_link":
            if not result.get("ok"):
                err = str(result.get("error") or "spotify_error")
                payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
                status_code = payload.get("status_code")
                path = str(payload.get("path") or "").strip().lower()

                if (
                    err == "spotify_forbidden_scope_or_premium_required"
                    and status_code == 403
                    and "/playlists" in path
                ):
                    return (
                        "Spotify denied playlist write access (403 Forbidden). "
                        "Mina sent the request correctly, but Spotify blocked create/add for this app/account right now. "
                        "Check Spotify Developer app access or try again later while their dashboard changes settle."
                    )

                hint = str(payload.get("self_heal_hint") or "").strip()
                if hint:
                    return f"Spotify request failed: {err}. {hint}"
                return f"Spotify request failed: {err}"

            payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
            status = str(payload.get("status") or "").strip().lower()

            def _artist_text(value: Any) -> str:
                if not isinstance(value, list):
                    return ""
                return ", ".join([str(a).strip() for a in value if str(a).strip()])

            playlists_raw = payload.get("playlists")

            if status in {"dj_started", "dj_already_running", "dj_stopping", "dj_not_running", "dj_running", "dj_stopped"}:
                dj_raw = payload.get("dj")
                dj: Dict[str, Any] = dj_raw if isinstance(dj_raw, dict) else {}
                running = bool(dj.get("running", False))
                tracks_played = int(dj.get("tracks_played") or 0)
                last_track = dj.get("last_track") if isinstance(dj.get("last_track"), dict) else {}
                last_name = str((last_track or {}).get("name") or "").strip()

                if status in {"dj_started", "dj_already_running"}:
                    if last_name:
                        return f"Favorites DJ is on. I will auto-switch tracks from your liked songs. Last pick: {last_name}."
                    return "Favorites DJ is on. I will auto-switch tracks from your liked songs when each song ends."
                if status == "dj_stopping":
                    return "Favorites DJ is stopping now."
                if status == "dj_not_running":
                    return "Favorites DJ is not running."
                if status == "dj_running" or running:
                    if last_name:
                        return f"Favorites DJ is running. Tracks switched so far: {tracks_played}. Current pick: {last_name}."
                    return f"Favorites DJ is running. Tracks switched so far: {tracks_played}."
                return "Favorites DJ is stopped."

            if "playlists" in payload and isinstance(playlists_raw, list):
                playlists = playlists_raw
                if not playlists:
                    return "No playlists found in your Spotify account."
                lines = ["Spotify playlists:"]
                for idx, row in enumerate(playlists[:30], start=1):
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "(untitled playlist)").strip()
                    total = row.get("tracks_total")
                    suffix = f" ({total} tracks)" if isinstance(total, int) else ""
                    lines.append(f"{idx}. {name}{suffix}")
                return "\n".join(lines)

            tracks_raw = payload.get("tracks")
            if "tracks" in payload and isinstance(tracks_raw, list):
                tracks = tracks_raw
                if not tracks:
                    if payload.get("playlist_id"):
                        return "No tracks found in that playlist."
                    return "No liked songs found."

                if payload.get("playlist_id"):
                    header = "Playlist tracks:"
                else:
                    shown = len(tracks)
                    total_available = payload.get("total_available")
                    try:
                        total_num = int(total_available) if total_available is not None else shown
                    except Exception:
                        total_num = shown
                    if total_num > shown:
                        header = f"Liked songs (showing {shown} of {total_num}):"
                    else:
                        header = f"Liked songs ({shown} total):"
                lines = [header]

                if not payload.get("playlist_id"):
                    def _track_line(idx: int, row: Dict[str, Any]) -> str:
                        name = str(row.get("name") or "(untitled track)").strip()
                        artist_text = _artist_text(row.get("artists"))
                        if artist_text:
                            return f"{idx}. {name} - {artist_text}"
                        return f"{idx}. {name}"

                    entries: List[str] = []
                    for idx, row in enumerate(tracks, start=1):
                        if not isinstance(row, dict):
                            continue
                        entries.append(_track_line(idx, row))

                    if len(entries) <= 1:
                        lines.extend(entries)
                        return "\n".join(lines)

                    mid = (len(entries) + 1) // 2
                    left_col = entries[:mid]
                    right_col = entries[mid:]
                    left_width = min(90, max((len(x) for x in left_col), default=0) + 3)

                    for i in range(mid):
                        left = left_col[i]
                        right = right_col[i] if i < len(right_col) else ""
                        if right:
                            lines.append(f"{left.ljust(left_width)}{right}")
                        else:
                            lines.append(left)
                    return "\n".join(lines)

                for idx, row in enumerate(tracks[:50], start=1):
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "(untitled track)").strip()
                    artist_text = _artist_text(row.get("artists"))
                    if artist_text:
                        lines.append(f"{idx}. {name} - {artist_text}")
                    else:
                        lines.append(f"{idx}. {name}")
                return "\n".join(lines)

            # Most common happy-path payload for favorites/search play.
            played = payload.get("played") if isinstance(payload.get("played"), dict) else None
            if isinstance(played, dict):
                name = str(played.get("name") or "").strip()
                artist_text = _artist_text(played.get("artists"))
                source = str(payload.get("source") or "").strip().lower()
                if status == "already_playing":
                    if name and artist_text:
                        return f"Already playing {name} by {artist_text}."
                    if name:
                        return f"Already playing {name}."
                if source == "favorites_library":
                    if name and artist_text:
                        return f"Playing {name} by {artist_text} from your liked songs."
                    if name:
                        return f"Playing {name} from your liked songs."
                if source == "search_fallback_after_favorites_miss":
                    if name and artist_text:
                        return f"Playing {name} by {artist_text} as the closest Spotify match."
                    if name:
                        return f"Playing {name} as the closest Spotify match."
                if name and artist_text:
                    return f"Playing {name} by {artist_text}."
                if name:
                    return f"Playing {name}."

            track = payload.get("track") if isinstance(payload.get("track"), dict) else None
            if isinstance(track, dict):
                name = str(track.get("name") or "").strip()
                artist_text = _artist_text(track.get("artists"))
                if name and artist_text:
                    return f"Now playing {name} by {artist_text}."
                if name:
                    return f"Now playing {name}."

            if payload.get("connected") is True:
                return "Spotify is connected."

            if "devices" in payload and isinstance(payload.get("devices"), list):
                return f"Found {len(payload.get('devices') or [])} Spotify device(s)."

            if status in {"play_started", "playing"}:
                return "Playback command was sent to Spotify, but no confirmed track details were returned yet."
            if status in {"paused"}:
                return "Spotify playback paused."

            return "Spotify command completed."

        if tool_name == "ps_run":
            if not result.get("ok"):
                error_text = str(result.get("error") or "").strip()
                payload = result.get("result", {})
                stderr = ""
                if isinstance(payload, dict):
                    stderr = str(payload.get("stderr") or "").strip()
                if stderr:
                    if error_text and error_text.lower() != stderr.lower():
                        return f"PowerShell execution failed: {error_text}\n\nSTDERR:\n{stderr}"
                    return f"PowerShell execution failed:\n\nSTDERR:\n{stderr}"
                return f"PowerShell execution failed: {error_text or 'unknown_error'}"
            payload = result.get("result", {})
            stdout = payload.get("stdout", "")
            stderr = payload.get("stderr", "")
            exit_code = payload.get("exit_code")
            if stdout:
                if stderr:
                    return f"{stdout}\n\nSTDERR:\n{stderr}"
                return stdout
            if stderr:
                return stderr
            return f"PowerShell exited with code {exit_code}."

        if tool_name == "code_execute":
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            exit_code = result.get("exit_code")
            warning = str(result.get("warning") or "").strip()

            lines: List[str] = []
            if warning:
                lines.append(warning)

            if stdout.strip():
                lines.append("STDOUT:")
                lines.append(stdout.rstrip())

            if stderr.strip():
                lines.append("STDERR:")
                lines.append(stderr.rstrip())

            if not lines:
                if exit_code is None:
                    return "Code executed with no output."
                return f"Code exited with code {exit_code} and no output."

            if exit_code is not None:
                lines.append(f"EXIT CODE: {exit_code}")

            return "\n".join(lines)

        if not result.get("ok"):
            return self._format_generic_tool_failure(tool_name, result)

        return json.dumps(result, indent=2)

    def _is_hardware_like_query(self, text: str) -> bool:
        t = (text or "").lower()
        if not t:
            return False
        hardware_terms = [
            "cpu",
            "gpu",
            "ram",
            "memory",
            "system",
            "os",
            "platform",
            "hardware",
            "spec",
            "motherboard",
            "disk",
            "storage",
        ]
        return any(term in t for term in hardware_terms)

    def _compress_multiline(self, text: str, max_lines: int = 8, max_chars: int = 900) -> str:
        src = (text or "").strip()
        if not src:
            return ""
        lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
        if not lines:
            return ""
        picked = lines[:max(1, int(max_lines))]
        out = " | ".join(picked)
        out = re.sub(r"\s+", " ", out).strip()
        if len(out) > max_chars:
            out = out[:max_chars].rstrip() + "..."
        return out

    def _remember_tool_output(
        self,
        tool_name: str,
        user_input: str,
        tool_args: Dict[str, Any],
        result: Dict[str, Any],
        formatted: str,
    ) -> None:
        try:
            if not isinstance(result, dict):
                return

            low_query = (user_input or "").strip().lower()

            if tool_name == "ps_run" and result.get("ok") and self._is_hardware_like_query(user_input):
                snapshot = self._compress_multiline(formatted, max_lines=10, max_chars=1000)
                if snapshot:
                    mem_text = f"hardware_snapshot::{low_query} => {snapshot}"
                    self.memory.add_memory(
                        mem_text,
                        kind="fact",
                        tags=["tool_result", "hardware_snapshot", "suggestion_context", "user_memory"],
                    )
                return

            if tool_name == "__project_test_run__":
                payload = result.get("result", {}) if isinstance(result.get("result", {}), dict) else {}
                cmd = str(payload.get("command") or "").strip()
                project_path = str(payload.get("project_path") or "").strip()
                exit_code = payload.get("exit_code")
                ok_flag = bool(result.get("ok"))
                error_text = str(result.get("error") or "").strip()
                changed_files = payload.get("changed_files", []) if isinstance(payload.get("changed_files", []), list) else []

                stdout_short = self._compress_multiline(str(payload.get("stdout") or ""), max_lines=6, max_chars=420)
                stderr_short = self._compress_multiline(str(payload.get("stderr") or ""), max_lines=6, max_chars=420)

                parts: List[str] = []
                if cmd:
                    parts.append(f"cmd={cmd}")
                if project_path:
                    parts.append(f"project={project_path}")
                parts.append(f"ok={ok_flag}")
                parts.append(f"exit_code={exit_code}")
                if error_text:
                    parts.append(f"error={error_text}")
                if changed_files:
                    parts.append("changed_files=" + ",".join([str(p) for p in changed_files[:8]]))
                if stdout_short:
                    parts.append(f"stdout={stdout_short}")
                if stderr_short:
                    parts.append(f"stderr={stderr_short}")

                if parts:
                    mem_text = "test_run_result::" + " ; ".join(parts)
                    self.memory.add_memory(
                        mem_text,
                        kind="tool",
                        tags=["tool_result", "test_result", "suggestion_context"],
                    )
                return

            if tool_name == "code_execute" and result.get("ok"):
                code = str(tool_args.get("code") or "").strip()
                lang = str(tool_args.get("language") or "").strip() or "unknown"
                stdout = self._compress_multiline(str(result.get("stdout") or ""), max_lines=4, max_chars=300)
                stderr = self._compress_multiline(str(result.get("stderr") or ""), max_lines=4, max_chars=300)
                exit_code = result.get("exit_code")

                if code:
                    code_short = code if len(code) <= 180 else (code[:180].rstrip() + "...")
                    mem_text = f"code_run_result::lang={lang}; exit_code={exit_code}; code={code_short}"
                    if stdout:
                        mem_text += f"; stdout={stdout}"
                    if stderr:
                        mem_text += f"; stderr={stderr}"

                    self.memory.add_memory(
                        mem_text,
                        kind="tool",
                        tags=["tool_result", "code_result", "suggestion_context"],
                    )
        except Exception:
            traceback.print_exc()

    # ========================================================
    # REFLEX LAYER
    # ========================================================

    def _reflex_tools_and_memory(
        self,
        messages: List[Dict[str, Any]],
        reply: Dict[str, Any],
        user_input: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:

        tool_name, tool_args = self.detect_tool_intent(user_input)
        if not tool_name:
            return None

        followup_request = self._is_context_followup_request(user_input)
        followup_hint = str(tool_args.pop("__followup_hint__", "") or "").strip()
        followup_target = str(tool_args.pop("__followup_target__", "") or "").strip()
        if tool_name == "dir_list" and followup_request and not followup_hint:
            followup_hint = "focus_prompt"
            path_for_hint = str(tool_args.get("path") or "").strip()
            followup_target = os.path.basename(path_for_hint) if path_for_hint else ""

        print("\n========================================")
        print("TOOL DETECTED")
        print("========================================")
        print(tool_name)

        print("\n========================================")
        print("TOOL ARGS")
        print("========================================")
        print(tool_args)

        identity_reply = self._handle_identity_reflex(
            tool_name=tool_name,
            tool_args=tool_args,
            request_context=request_context,
        )
        if identity_reply is not None:
            return identity_reply

        direct_reply = self._handle_direct_response_reflex(
            tool_name=tool_name,
            tool_args=tool_args,
            user_input=user_input,
        )
        if direct_reply is not None:
            return direct_reply

        directory_scan_reply = self._handle_directory_scan_reflex(
            tool_name=tool_name,
            tool_args=tool_args,
            user_input=user_input,
        )
        if directory_scan_reply is not None:
            return directory_scan_reply

        external_write_reply = self._handle_external_write_reflex(tool_name=tool_name)
        if external_write_reply is not None:
            return external_write_reply

        if tool_name == "file_append":
            existing = str(tool_args.get("content", "") or "").strip()
            target_path = str(tool_args.get("path", "") or "").strip()

            if target_path and not existing:
                generated = self._generate_file_content_from_intent(
                    user_input=user_input,
                    target_path=target_path,
                )
                if generated.strip():
                    tool_args["content"] = generated

        if tool_name == "file_write":
            target_path = str(tool_args.get("path", "") or "").strip()
            if target_path:
                chosen_content, content_source = self._resolve_file_write_content_candidate(
                    user_input=user_input,
                    target_path=target_path,
                    existing_content=str(tool_args.get("content", "") or ""),
                    reply=reply,
                )
                if content_source == "artifact_payload_hijack":
                    self._remember_artifact_payload_hijack(user_input=user_input, target_path=target_path)
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": (
                                        "I blocked that file write because the payload no longer matched the requested artifact. "
                                        "I need to regenerate the file content for the same target instead of writing a generic stub."
                                    ),
                                }
                            }
                        ]
                    }
                if chosen_content.strip():
                    tool_args["content"] = chosen_content

        if tool_name in ("file_read", "file_write", "file_append", "file_delete", "dir_create", "dir_list"):
            path_val = str(tool_args.get("path", "") or "").strip()
            if path_val:
                if tool_name == "file_read":
                    indexed_path = self._resolve_file_index_path(path_val)
                    if indexed_path:
                        path_val = indexed_path
                tool_args["path"] = self._normalize_path(path_val)
            else:
                resolved = self._resolve_path_alias(user_input)
                if resolved:
                    tool_args["path"] = resolved

        if tool_name == "file_move":
            src_val = str(tool_args.get("src", "") or "").strip()
            dst_val = str(tool_args.get("dst", "") or "").strip()
            if src_val:
                tool_args["src"] = self._normalize_path(src_val)
            else:
                resolved_src = self._resolve_path_alias(user_input)
                if resolved_src:
                    tool_args["src"] = resolved_src
            if dst_val:
                tool_args["dst"] = self._normalize_path(dst_val)
            else:
                resolved_dst = self._resolve_path_alias(user_input + " destination")
                if resolved_dst:
                    tool_args["dst"] = resolved_dst

        if tool_name == "ps_run":
            script_val = str(tool_args.get("script", "") or tool_args.get("command", "") or "").strip()
            has_explicit_path = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\\[^\s]+\.(ps1|py|bat|cmd))', script_val, re.IGNORECASE) is not None
            if not has_explicit_path:
                resolved_script = self._resolve_path_alias(user_input)
                if resolved_script:
                    tool_args["script"] = f'& "{resolved_script}"'

        if tool_name == "web_search":
            mode = str(tool_args.get("mode") or "").strip().lower()
            if mode not in {"balanced", "strict", "wide"}:
                tool_args["mode"] = self.web_search_mode

        gate_prompt = self._identity_gate_decision(
            tool_name=tool_name,
            tool_args=tool_args,
            request_context=request_context,
        )
        if gate_prompt:
            return {
                "choices": [
                    {
                        "message": {
                            "content": gate_prompt,
                        }
                    }
                ]
            }

        if tool_name in self._write_auth_tool_names():
            already_authorized = bool(tool_args.get("authorized_write", False))
            if not already_authorized:
                # Auto-authorize if: no request_context (local call), or client is trusted, or recently verified
                is_local_call = request_context is None
                is_trusted = self._is_trusted_client(request_context)
                is_verified = self._is_client_recently_verified(request_context)
                if is_local_call or is_trusted or is_verified:
                    tool_args["authorized_write"] = True

        if tool_name == "__tool_list__":
            inventory = self._usable_tool_inventory()
            result = {
                "ok": True,
                "tools": [item["name"] for item in inventory],
                "inventory": inventory,
                "status": self.tools.get_status(),
            }
        elif tool_name == "__scratch_note__":
            result = self._write_scratch_note(
                note_text=str(tool_args.get("text", "") or ""),
                title_hint=str(tool_args.get("title", "note") or "note"),
            )
        elif tool_name == "__path_alias_set__":
            result = self._store_path_alias(
                alias=str(tool_args.get("alias", "") or ""),
                path=str(tool_args.get("path", "") or ""),
            )
        elif tool_name == "__path_alias_list__":
            result = self._list_path_aliases()
        elif tool_name == "__path_alias_forget__":
            result = self._forget_path_alias(
                alias=str(tool_args.get("alias", "") or ""),
            )
        elif tool_name == "__workspace_info__":
            result = self._workspace_info()
        elif tool_name == "__file_index_status__":
            result = self._file_index_status()
        elif tool_name == "__file_index_refresh__":
            result = self._file_index_refresh()
        elif tool_name == "__startup_status__":
            result = self._startup_status()
        elif tool_name == "__autonomous_work_status__":
            result = self._autonomous_work_status()
        elif tool_name == "__autonomous_work_run__":
            result = self._run_autonomous_work_once()
        elif tool_name == "__git_init__":
            result = self._git_init()
        elif tool_name == "__git_status__":
            result = self._git_status()
        elif tool_name == "__git_snapshot__":
            result = self._git_snapshot(
                message=str(tool_args.get("message", "") or ""),
            )
        elif tool_name == "__git_pull__":
            result = self._git_pull()
        elif tool_name == "__git_push__":
            result = self._git_push()
        elif tool_name == "__project_test_run__":
            result = self._run_project_tests(
                request=str(tool_args.get("request", user_input) or user_input),
            )
        elif tool_name == "__repair_python_file__":
            result = self._repair_escaped_python_file(
                path=str(tool_args.get("path", "") or ""),
            )
        elif tool_name == "__project_tracker_status__":
            result = self._project_tracker_status()
        elif tool_name == "__project_tracker_update__":
            result = self._update_project_tracker(
                project_name=str(tool_args.get("project", "") or ""),
                status=(str(tool_args.get("status", "") or "").strip() or None),
                next_step=(str(tool_args.get("next_step", "") or "").strip() or None),
            )
        elif tool_name == "__memory_status__":
            result = self._memory_status()
        elif tool_name == "__memory_tidy__":
            result = self._memory_tidy(
                dry_run=bool(tool_args.get("dry_run", False)),
            )
        elif tool_name == "__search_mode_set__":
            result = self._set_web_search_mode(str(tool_args.get("mode") or ""))
        elif tool_name == "__search_mode_get__":
            result = self._get_web_search_mode()
        elif tool_name == "__driving_checkin_start__":
            result = self._start_driving_checkins(
                repeat_seconds=int(tool_args.get("repeat_seconds") or 1200),
            )
        elif tool_name == "__driving_checkin_stop__":
            result = self._stop_driving_checkins()
        elif tool_name == "__mina_shutdown_status__":
            result = self._shutdown_gate_status(
                max_wait_seconds=int(tool_args.get("max_wait_seconds", 45) or 45),
                poll_seconds=int(tool_args.get("poll_seconds", 2) or 2),
                flush_max_items=int(tool_args.get("flush_max_items", 400) or 400),
            )
        elif tool_name == "__mina_shutdown__":
            result = self._request_safe_shutdown(
                force=bool(tool_args.get("force", False)),
                max_wait_seconds=int(tool_args.get("max_wait_seconds", 45) or 45),
                poll_seconds=int(tool_args.get("poll_seconds", 2) or 2),
                flush_max_items=int(tool_args.get("flush_max_items", 400) or 400),
                embed_port=int(tool_args.get("embed_port", 8084) or 8084),
                post_flush_delay_ms=int(tool_args.get("post_flush_delay_ms", 1200) or 1200),
            )
        elif tool_name == "__task_memory_write__":
            write_tags = tool_args.get("tags", ["user_memory"])
            if not isinstance(write_tags, list):
                write_tags = ["user_memory"]
            memory_id = self.memory.add_task_memory(
                str(tool_args.get("text", "") or ""),
                due_at=tool_args.get("due_at"),
                tags=write_tags,
                status=str(tool_args.get("status") or "pending"),
            )
            result = {
                "ok": memory_id is not None,
                "stored": str(tool_args.get("text", "") or ""),
                "result": {
                    "memory_id": memory_id,
                    "text": str(tool_args.get("text", "") or ""),
                    "due_at": tool_args.get("due_at"),
                    "status": str(tool_args.get("status") or "pending"),
                    "tags": write_tags,
                },
            }
        elif tool_name == "__learn_play_song_shorthand__":
            phrase = self._normalize_user_intent_text(str(tool_args.get("phrase") or "")).lower().strip()
            track_query = str(tool_args.get("track_query") or "").strip()
            if not phrase or not track_query:
                result = {
                    "ok": False,
                    "result": None,
                    "error": "play_song_shorthand_invalid",
                }
            else:
                record_text = self._play_song_shorthand_record(phrase, track_query)
                exists = False
                if hasattr(self.memory, "find_memory_id_by_text"):
                    exists = self.memory.find_memory_id_by_text(
                        record_text,
                        include_kinds=["procedure"],
                        include_tags=["play_song_shorthand"],
                    ) is not None
                if not exists:
                    self.memory.add_memory(
                        record_text,
                        kind="procedure",
                        tags=["user_memory", "play_song_shorthand"],
                    )

                if not isinstance(getattr(self, "_play_song_shorthand_cache", None), dict):
                    self._play_song_shorthand_cache = {}
                self._play_song_shorthand_cache[phrase] = track_query

                result = {
                    "ok": True,
                    "stored": record_text,
                    "result": {
                        "phrase": phrase,
                        "track_query": track_query,
                    },
                    "error": None,
                }
        elif tool_name == "__project_bootstrap__":
            result = self._bootstrap_project(
                project_name=str(tool_args.get("project", "") or ""),
                user_input=str(tool_args.get("request", user_input) or user_input),
                status=(str(tool_args.get("status", "") or "").strip() or None),
                next_steps=tool_args.get("next_steps", []) if isinstance(tool_args.get("next_steps", []), list) else [],
            )
        elif tool_name == "__scaffold__":
            result = self._execute_scaffold_request(user_input)
        else:
            result = self.tools.run(tool_name, tool_args)

        tool_name, tool_args, result, _recovered = self._recover_failed_tool_once(
            user_input=user_input,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
        )

        if isinstance(result, dict) and followup_hint:
            result["_followup_hint"] = followup_hint
            if followup_target:
                result["_followup_target"] = followup_target

        self._set_last_tool_event(
            tool_name=tool_name,
            tool_args=tool_args,
            user_input=user_input,
            result=result if isinstance(result, dict) else None,
        )
        self._set_context_continuation(
            tool_name=tool_name,
            tool_args=tool_args,
            user_input=user_input,
            result=result if isinstance(result, dict) else None,
        )

        if tool_name == "file_write" and isinstance(result, dict) and bool(result.get("ok", False)):
            self._remember_code_artifact_memory(
                target_path=str(tool_args.get("path", "") or "").strip(),
                content=str(tool_args.get("content", "") or ""),
                user_input=user_input,
                source_tag="written_artifact",
            )
            # Log to memory for future reference (e.g., "read back the file you created")
            file_path = tool_args.get("path", "")
            if file_path:
                try:
                    self.memory.store(
                        category="recent file operations",
                        text=f"Created file: {file_path}",
                        metadata={"operation": "write", "path": file_path, "turn_input": user_input},
                    )
                except Exception:
                    pass

        if tool_name == "file_read" and isinstance(result, dict) and bool(result.get("ok", False)):
            # Log file reads to memory for context
            file_path = tool_args.get("path", "")
            if file_path:
                try:
                    self.memory.store(
                        category="recent file operations",
                        text=f"Read file: {file_path}",
                        metadata={"operation": "read", "path": file_path, "turn_input": user_input},
                    )
                except Exception:
                    pass

        blocked_external_write = (
            tool_name in self._write_auth_tool_names()
            and isinstance(result, dict)
            and not bool(result.get("ok", True))
            and "write_not_authorized_outside_workspace" in str(result.get("error") or "")
        )
        if blocked_external_write:
            self._set_pending_write_authorization(
                tool_name=tool_name,
                tool_args=tool_args,
                user_input=user_input,
                error=str(result.get("error") or ""),
            )
            target = self._pending_write_target(tool_name, tool_args)
            prompt = (
                "This write is outside E:\\workspace and needs your approval. "
                f"Target: {target}. "
                "Reply with 'authorize write' to approve this one action, or 'cancel' to deny."
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": prompt,
                        }
                    }
                ]
            }

        try:
            summary = (
                f"Tool '{tool_name}' "
                f"called with args={tool_args}, "
                f"result_ok={result.get('ok', None)}"
            )
            self.memory.add_memory(
                summary,
                kind="tool",
                tags=["tool_call", tool_name],
            )
        except Exception:
            traceback.print_exc()

        self._maybe_learn_intent_alias(
            user_input=user_input,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
        )

        formatted = self._format_tool_result(tool_name, result)
        if not formatted.strip():
            formatted = "(tool returned no output)"

        self._remember_tool_output(
            tool_name=tool_name,
            user_input=user_input,
            tool_args=tool_args,
            result=result,
            formatted=formatted,
        )

        if self._should_direct_render_tool(tool_name):
            if tool_name == "spotify_link" or tool_name in self._truth_first_direct_tool_names():
                content = formatted
            else:
                content = self._render_tool_response_as_mina(
                    messages=messages,
                    user_input=user_input,
                    tool_name=tool_name,
                    formatted=formatted,
                    tool_ok=bool(result.get("ok", True)),
                )
            return {
                "choices": [
                    {
                        "message": {
                            "content": content,
                        }
                    }
                ]
            }

        messages.append({
            "role": "user",
            "content": user_input,
        })

        if tool_name == "memory_read":
            cleaned = self._build_memory_read_reply(
                messages=messages,
                user_query=user_input,
                formatted=formatted,
            )

            return {
                "choices": [
                    {
                        "message": {
                            "content": cleaned or formatted,
                        }
                    }
                ]
            }

        messages.append({
            "role": "assistant",
            "content": f"""A tool was executed.

Tool Name:
{tool_name}

Verified Tool Output:
{formatted}

IMPORTANT RULES:
- ONLY use the verified tool output above.
- DO NOT invent files, folders, summaries, or examples.
- DO NOT hallucinate directory contents.
- If the tool output is empty, say so plainly.
- Stay conversational as Mina.
- Keep the response concise and accurate.
""",
        })

        reply = self.model.chat(messages)
        return reply

    # ========================================================
    # TOOL DETECTION
    # ========================================================

    def _detect_file_directory_intent(
        self,
        text: str,
        raw_text: str,
        t: str,
    ) -> Optional[Tuple[Optional[str], Dict[str, Any]]]:
        def extract_directory_path(raw: str) -> str:
            quoted = re.findall(r'["\']([^"\']+)["\']', raw)
            for q in quoted:
                candidate = q.strip()
                if candidate and not re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                    return candidate

            patterns = [
                r'\b(?:in|at|to)\s+([A-Za-z]:\\[^\s,;]+)',
                r'\b(?:in|at|to)\s+([.]{1,2}[\\/][^\s,;]+)',
                r'\b(?:in|at|to)\s+([^\s,;]+[\\/][^\s,;]+)',
                r'\b(?:named|called)\s+([^\s,;]+)',
                r'\b(?:folder|directory|dir)\s+([^\s,;]+)',
            ]

            for pattern in patterns:
                m = re.search(pattern, raw, re.IGNORECASE)
                if m:
                    candidate = m.group(1).strip().strip('"\'')
                    if candidate and not re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                        return candidate

            return ""

        def extract_file_write_path(raw: str) -> str:
            quoted = re.findall(r'["\']([^"\']+)["\']', raw)
            for q in quoted:
                candidate = q.strip()
                if re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                    return candidate.rstrip('.,;:')

            patterns = [
                r'\bsave\s+(?:it|this|that|script|file)\s+as\s+([^\s,;:]+\.[A-Za-z0-9]{1,8})',
                r'\bfile\s+([^\s,;:]+\.[A-Za-z0-9]{1,8})',
                r'\b(?:at|to|in)\s+([A-Za-z]:\\[^\s,;]+)',
                r'\b(?:at|to|in)\s+([.]{1,2}[\\/][^\s,;]+)',
                r'\b(?:at|to|in)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})',
                r'\b(?:named|called)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})',
            ]

            for pattern in patterns:
                m = re.search(pattern, raw, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip('"\'').rstrip('.,;:')

            return ""

        def extract_file_write_content(raw: str, lowered: str) -> str:
            fence = re.search(r'```(?:[A-Za-z0-9_+-]+)?\n([\s\S]*?)```', raw)
            if fence:
                return fence.group(1).strip("\n")

            quoted = re.findall(r'["\']([^"\']+)["\']', raw)
            if quoted:
                for q in reversed(quoted):
                    if not re.search(r'\.[A-Za-z0-9]{1,8}$', q.strip()):
                        return q.strip()

            content_patterns = [
                r'\bwith\s+content\s*:\s*([\s\S]+)$',
                r'\bwith\s+content\s+([\s\S]+)$',
                r'\bthat\s+(?:says|prints|contains)\s+([\s\S]+)$',
                r'\bprint(?:s)?\s+(["\'][^"\']+["\'])',
            ]

            for pattern in content_patterns:
                m = re.search(pattern, raw, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip('"\'')

            if "hello world" in lowered:
                return 'print("hello world")'

            return ""

        scaffold_intent = (
            re.search(r'\b(create|make|build|scaffold|setup)\b', t) is not None
            and (
                re.search(r'\b(folder|folders|directory|directories|dir)\b', t) is not None
                and (
                    re.search(r'\b(file|files|script|scripts|module|modules)\b', t) is not None
                    or " with " in t
                    or " inside " in t
                )
            )
        )

        if not scaffold_intent:
            scaffold_intent = (
                "project scaffold" in t
                or (
                    "create project" in t
                    and re.search(r'\b(src|tests?)\b', t) is not None
                    and re.search(r'\b(file|files|directory|directories|folder|folders)\b', t) is not None
                )
            )

        if scaffold_intent:
            return "__scaffold__", {"request": raw_text}

        directory_create_intent = (
            re.search(r'\b(create|make|add|new)\b', t) is not None
            and re.search(r'\b(folder|directory|dir)\b', t) is not None
        ) or any(x in t for x in [
            "mkdir ",
            "make folder",
            "create folder",
            "create directory",
            "new folder",
            "new directory",
        ])

        if directory_create_intent:
            path = extract_directory_path(raw_text)
            if path:
                return "dir_create", {"path": path}

        if self._is_recursive_directory_read_request(raw_text, t):
            path = self._extract_explicit_windows_path(raw_text)
            if path:
                return "__inspect_directory_tree__", {"path": path, "mode": self._extract_directory_inspection_filter(raw_text)}

        sqlite_table_match = re.search(
            r'\b(?:read|show|preview|inspect|describe)\s+(?:the\s+)?table\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:from|in|inside)\s+(.+)$',
            raw_text,
            re.IGNORECASE,
        )
        if sqlite_table_match:
            table_name = str(sqlite_table_match.group(1) or "").strip()
            db_path = self._extract_explicit_windows_path(str(sqlite_table_match.group(2) or ""))
            if table_name and db_path:
                return "__inspect_sqlite_table__", {
                    "path": db_path,
                    "table": table_name,
                    "row_limit": 8,
                }

        # Natural bare-file phrasing support, e.g.:
        # "read core.py", "open agent/core.py", "show ./notes.txt"
        bare_file_read = re.search(
            r'^\s*(?:(?:travis|mina)\s+)?(?:please\s+)?(?:read|open|show|view|cat)\s+'
            r'(["\']?[^"\'\s]+\.[A-Za-z0-9]{1,8}["\']?)\s*[?.!,:;]*\s*$',
            raw_text,
            re.IGNORECASE,
        )
        if bare_file_read:
            path = str(bare_file_read.group(1) or "").strip().strip('"\'')
            if path:
                return "file_read", {"path": path}

        if any(x in t for x in ["read file", "open file", "show file"]):
            m = re.search(r'["\']([^"\']+)["\']', text)
            if not m:
                m = re.search(r'[“‘]([^”’]+)[”’]', text)
            if not m:
                m = re.search(r'file\s+([^\s]+)', text, re.IGNORECASE)

            path = m.group(1).strip() if m else ""
            return "file_read", {"path": path}

        if any(x in t for x in [
            "delete file", "remove file", "delete script", "remove script",
            "delete the file", "remove the file", "delete this file", "remove this file",
        ]):
            path = ""
            quoted = re.findall(r'["\']([^"\']+)["\']', text)
            for q in quoted:
                if re.match(r'^[A-Za-z]:\\', q):
                    path = q.strip()
                    break

            if not path:
                m = re.search(r'\bat\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\bfrom\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'([A-Za-z]:\\[^\s,]+\.(?:py|txt|md|json|csv))', text, re.IGNORECASE)
                if m:
                    path = m.group(1).strip()

            return "file_delete", {"path": path}

        if any(x in t for x in [
            "move file", "move script", "move this file", "move the file",
            "rename file", "rename script", "rename this file", "rename the file",
            "rename to", "move to",
        ]) or (("move" in t or "rename" in t) and re.search(r'[A-Za-z]:\\', text) and " to " in t):
            src = ""
            dst = ""
            quoted = re.findall(r'["\']([^"\']+)["\']', text)
            quoted_paths = [q.strip() for q in quoted if re.match(r'^[A-Za-z]:\\', q)]
            if len(quoted_paths) >= 2:
                src, dst = quoted_paths[0], quoted_paths[1]
            elif len(quoted_paths) == 1:
                src = quoted_paths[0]

            if not src:
                m = re.search(r'\b(?:move|rename)\s+(?:the\s+)?file\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\bfrom\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if m:
                    src = m.group(1).strip()

            if not dst:
                m = re.search(r'\bto\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\brename\s+[A-Za-z]:\\[^\s,]+\s+to\s+([^\s,]+)', text, re.IGNORECASE)
                if m:
                    dst = m.group(1).strip()

            # Guard against false positives from generic prompts such as
            # "create and save a program to rename files..." with no concrete paths.
            if not src or not dst:
                return None

            if src and dst and not os.path.isabs(dst):
                dst = os.path.join(os.path.dirname(src), dst)

            return "file_move", {"src": src, "dst": dst}

        if any(x in t for x in [
            "append file", "append to file", "add to file", "add content to file",
            "append text to", "append this", "append the following",
        ]) or ("append" in t and re.search(r'[A-Za-z]:\\', text) and any(x in t for x in ["to", "with", "content", "text", "line"])):
            path = ""
            content = ""

            quoted = re.findall(r'["\']([^"\']+)["\']', text)
            for q in quoted:
                if re.match(r'^[A-Za-z]:\\', q):
                    path = q.strip()

            if not path:
                m = re.search(r'\bfile\s+([^\s,;:]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\bat\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\bto\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'([A-Za-z]:\\[^\s,]+\.[A-Za-z0-9]+)', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'\b(?:at|to)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'([^\s,;]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
                if m:
                    path = m.group(1).strip().rstrip('.,;:')

            if not content:
                m = re.search(r'\bwith\s+content\s*:\s*([\s\S]+)$', text, re.IGNORECASE)
                if not m:
                    m = re.search(r'(?:append|add).*?\bto\s+[^\s]+\s+(.+)', text, re.IGNORECASE)
                if m:
                    content = m.group(1).strip()
                elif quoted:
                    content = quoted[-1].strip()

            return "file_append", {"path": path, "content": content}

        file_write_intent = any(x in t for x in [
            "create file", "write file", "write to file", "write this to file", "save file", "make file",
            "create a python script", "create a script", "write a python script", "save a python script",
            "make a python script", "create script", "make script", "write script", "save script",
        ]) or (("at " in t or "to " in t) and any(x in t for x in ["print", "prints", "code", "content", "with content"])) or (
            re.search(r'\b(create|write|save|make|generate|build)\b', t) is not None
            and (re.search(r'\b(file|script|module)\b', t) is not None or re.search(r'\.[A-Za-z0-9]{1,8}\b', raw_text) is not None)
        )

        if file_write_intent:
            path = extract_file_write_path(raw_text)
            if not path:
                return None, {}

            if not re.search(r'\.[A-Za-z0-9]{1,8}$', path) and (
                "project scaffold" in t
                or re.search(r'\b(make|create)\b.*\b(project|structure)\b', t) is not None
                or re.search(r'\b(src|tests?)\b', t) is not None
            ):
                return "__scaffold__", {"request": raw_text}

            content = extract_file_write_content(raw_text, t)

            if path.lower().endswith('.py') and content:
                if not content.strip().startswith('print') and ('print' in t or 'prints' in t or 'python script' in t):
                    content = f'print({json.dumps(content)})'

            return "file_write", {"path": path, "content": content}

        dir_list_triggers = [
            "list directory", "list files", "show files", "show directory", "directory contents",
            "folder contents", "what files", "what's in this folder", "contents of",
            "list the contents", "list contents", "show contents", "what is in", "toy box", "toybox",
        ]

        if any(x in t for x in dir_list_triggers) or re.search(r'\blist\b\s+[a-zA-Z]:\\', t) or re.search(r'\bcontents\b.*[a-zA-Z]:\\', t):
            workspace_root = self.workspace_root
            path = workspace_root

            m = re.search(r'in\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'on\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'of\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'(?:list\s+files\s+)?([a-zA-Z]:\\[^\n\r]*)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'(?:list\s+)?([a-zA-Z]:\\?)(?:\s|$)', text, re.IGNORECASE)

            if m:
                path = m.group(1).strip()
            elif any(x in t for x in ["toy box", "toybox"]):
                path = os.path.join(workspace_root, "toys")

            return "dir_list", {"path": path}

        return None

    def detect_tool_intent(
        self,
        text: str,
    ) -> Tuple[Optional[str], Dict[str, Any]]:

        if not text:
            return None, {}

        original_text = text.strip()
        raw_text = self._normalize_user_intent_text(original_text)
        t = raw_text.lower().strip()
        action_like_request = self._is_action_request_like(raw_text)
        intent_signals = self._intent_signal_profile(raw_text)

        if self._get_pending_write_authorization() is not None:
            if self._is_write_authorization_confirmation(raw_text):
                return "__confirm_external_write__", {}
            if self._is_write_authorization_cancel(raw_text):
                return "__cancel_external_write__", {}

        pending_alias = self._get_pending_alias_replay()
        if isinstance(pending_alias, dict):
            if self._is_generic_confirmation(raw_text):
                self._clear_pending_alias_replay()
                tool_name = str(pending_alias.get("tool_name") or "").strip()
                tool_args = pending_alias.get("tool_args", {}) if isinstance(pending_alias.get("tool_args", {}), dict) else {}
                if tool_name and (tool_name in self._tool_registry()):
                    return tool_name, dict(tool_args)
            if self._is_generic_cancellation(raw_text):
                self._clear_pending_alias_replay()
                return "__clarify_intent__", {
                    "question": "Okay, I will not use that learned shortcut. Tell me exactly what you want me to do."
                }
            # Any new non-confirmation input cancels the pending alias replay.
            self._clear_pending_alias_replay()

        draft_save_intent = self._resolve_draft_save_intent(raw_text)
        if draft_save_intent is not None:
            return draft_save_intent

        continuation_intent = self._resolve_continuation_intent(raw_text)
        if continuation_intent is not None:
            return continuation_intent

        draft_execution_intent = self._resolve_draft_execution_intent(raw_text)
        if draft_execution_intent is not None:
            return draft_execution_intent

        contextual_followup = self._resolve_contextual_followup_intent(raw_text)
        if contextual_followup is not None:
            return contextual_followup

        enroll_match = re.search(
            r"\b(?:set|enroll|register|save)\s+(?:my\s+)?voice\s+(?:passphrase|phrase|code)\s*(?:to|as|:)?\s*(.+)$",
            raw_text,
            re.IGNORECASE,
        )
        if enroll_match:
            phrase = str(enroll_match.group(1) or "").strip().strip('"\'')
            if phrase:
                return "__voice_passphrase_enroll__", {"phrase": phrase}

        verify_match = re.search(r"^\s*(?:mina\s+)?verify\s+voice\s+(.+)$", raw_text, re.IGNORECASE)
        if verify_match:
            phrase = str(verify_match.group(1) or "").strip().strip('"\'')
            if phrase:
                return "__voice_identity_verify__", {"phrase": phrase}

        if re.search(r"\b(?:voice\s+auth\s+status|voice\s+passphrase\s+status|identity\s+status)\b", t):
            return "__voice_auth_status__", {}

        shorthand_rule = self._extract_play_song_shorthand_rule(raw_text)
        if shorthand_rule:
            return "__learn_play_song_shorthand__", shorthand_rule

        play_shorthand_track = self._resolve_play_song_shorthand(raw_text)
        if play_shorthand_track and "spotify_link" in self._tool_registry():
            return "spotify_link", {
                "action": "play_track",
                "track_query": play_shorthand_track,
            }

        ambiguity_prompt = self._maybe_build_ambiguity_prompt(raw_text, intent_signals=intent_signals)
        if ambiguity_prompt:
            return "__clarify_intent__", {"question": ambiguity_prompt}

        recipient_pattern = r"(?:him|her|them|my\s+(?:brother|sister|mom|dad|friend|contact)|(?:brother|sister|mom|dad|friend|contact))"
        external_message_intent = (
            re.search(rf"\b(?:text|sms|call|phone|message)\s+(?:to\s+)?{recipient_pattern}\b", t) is not None
            or re.search(rf"\bsend\b[^.?!\n]{{0,100}}\b(?:text|sms|message)\b[^.?!\n]{{0,100}}\b(?:to\s+)?{recipient_pattern}\b", t) is not None
            or re.search(rf"\bsend\s+{recipient_pattern}\b[^.?!\n]{{0,100}}\b(?:text|sms|message)\b", t) is not None
        )
        if external_message_intent:
            tool_names = set(self._tool_registry().keys())
            has_messaging_tool = any(name in tool_names for name in ["sms_send", "send_text", "phone_text", "twilio_sms"])
            if not has_messaging_tool:
                return "__capability_limit__", {
                    "message": (
                        "I cannot send real texts or calls directly from this setup yet. "
                        "I can draft the exact message for you and keep a follow-up reminder."
                    )
                }

        bulk_read_match = re.search(r"\bread\s+all\s+files\s+in\b", raw_text, re.IGNORECASE)
        if bulk_read_match:
            folder = self._extract_explicit_windows_path(raw_text)
            if folder and os.path.isdir(folder):
                return "__inspect_directory_tree__", {
                    "path": folder,
                    "mode": self._extract_directory_inspection_filter(raw_text),
                }

        def _infer_ps_command(text_value: str) -> str:
            query = str(text_value or "").strip()
            lowered_query = query.lower()

            query = re.sub(
                r"^(?:[a-z0-9_]+\s+)?(?:hey|hi|hello|yo|sup)\b[\s,.:!-]*",
                "",
                query,
                flags=re.IGNORECASE,
            ).strip()
            query = re.sub(
                r"^(?:mina|travis|assistant|buddy|hottie)\b[\s,.:!-]*",
                "",
                query,
                flags=re.IGNORECASE,
            ).strip()
            lowered_query = query.lower()

            if lowered_query in {"hardware info", "list hardware", "list the current hardware"}:
                return "Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer"

            if lowered_query in {"gpu info", "graphics info"}:
                return "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM"

            # Why: count-style hardware questions should use the same execution path as other system queries.
            # Guard: keeps "how many ..." prompts from falling back to chat while avoiding per-phrase branching.
            if re.search(r"\bhow many\b.*\b(gpu|gpus|graphics|video controller)\b", lowered_query) or re.search(r"\b(gpu|gpus|graphics|video controller)\b.*\bhow many\b", lowered_query):
                return "(Get-CimInstance Win32_VideoController).Count"

            if re.search(r"\b(number of|count of|how many|how much)\b.*\b(hard drive|hard drives|disk drive|disk drives|drive|drives)\b", lowered_query) or re.search(r"\b(hard drive|hard drives|disk drive|disk drives|drive|drives)\b.*\b(number of|count of|how many|how much)\b", lowered_query):
                return "(Get-CimInstance Win32_DiskDrive).Count"

            if lowered_query in {"cpu info", "processor info"}:
                return "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed"

            if lowered_query in {"memory info", "ram info"}:
                return "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory"

            if lowered_query in {"what time is it", "current time", "local time", "time now"}:
                return 'Get-Date -Format "yyyy-MM-dd HH:mm:ss"'

            if lowered_query in {"what date is it", "current date", "today\'s date", "todays date"}:
                return 'Get-Date -Format "yyyy-MM-dd"'

            if re.search(r"\b(probe|check|inspect|query|scan)\b.*\b(cpu|processor)\b", lowered_query) or re.search(r"\b(cpu|processor)\b.*\b(probe|check|inspect|query|scan)\b", lowered_query):
                return "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed"

            if re.search(r"\b(what|current|local)\b.*\btime\b", lowered_query):
                return 'Get-Date -Format "yyyy-MM-dd HH:mm:ss"'

            if re.search(r"\b(what|current)\b.*\bdate\b", lowered_query):
                return 'Get-Date -Format "yyyy-MM-dd"'

            if re.search(r"\bwhat\b.*\bcpu\b", lowered_query):
                return "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed"

            if re.search(r"\bwhat\b.*\bgpu\b", lowered_query):
                return "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM"

            if re.search(r"\bhow many\b.*\b(gpu|gpus|graphics|video controller)\b", lowered_query) or re.search(r"\b(gpu|gpus|graphics|video controller)\b.*\bhow many\b", lowered_query):
                return "(Get-CimInstance Win32_VideoController).Count"

            if re.search(r"\b(number of|count of|how many|how much)\b.*\b(hard drive|hard drives|disk drive|disk drives|drive|drives)\b", lowered_query) or re.search(r"\b(hard drive|hard drives|disk drive|disk drives|drive|drives)\b.*\b(number of|count of|how many|how much)\b", lowered_query):
                return "(Get-CimInstance Win32_DiskDrive).Count"

            if re.search(r"\bwhat\b.*\b(ram|memory)\b", lowered_query):
                return "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory"

            if re.search(r"\bwhat\b.*\b(system|spec|specs|os|platform|hardware)\b", lowered_query):
                return "Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer"

            if re.search(r"\b(run|execute|show|list|get)\b", lowered_query):
                if "powershell" in lowered_query or "ps" in lowered_query:
                    query = re.sub(r"\bpowershell\b", "", query, count=1, flags=re.IGNORECASE).strip()
                    query = re.sub(r"\bps\b", "", query, count=1, flags=re.IGNORECASE).strip()

            if query.startswith('"') and query.endswith('"'):
                query = query[1:-1]

            try:
                from tools.ps_run import _normalize_command as _normalize_ps_command
                normalized = _normalize_ps_command(query)
                if normalized:
                    return normalized
            except Exception:
                traceback.print_exc()

            return query

        # ====================================================
        # DIRECT TOOL MARKUP (USER-PASTED)
        # ====================================================
        pasted_tool = re.search(
            r"\|>\s*([A-Za-z_][\w.-]*)\s*\{([\s\S]*?)\}\s*(?:<\|tool_call\>|<tool_call\|>)?",
            raw_text,
            flags=re.IGNORECASE,
        )
        if pasted_tool:
            tool_name = str(pasted_tool.group(1) or "").strip()
            args = self._parse_tool_args(str(pasted_tool.group(2) or ""))
            if tool_name in self._tool_registry():
                return tool_name, args

        # Bare PowerShell-style commands should still execute directly when the user
        # pastes or types them as operational input.
        if re.search(r"^(?:get|select|where|set|write|new|remove|invoke|start|stop|echo|format)-[A-Za-z]+", raw_text, re.IGNORECASE):
            if "ps_run" in self._tool_registry():
                return "ps_run", {"script": _infer_ps_command(raw_text)}

        # Route configurable tool self-test requests early so mentions like
        # "spotify" or "github" don't get hijacked by other intent handlers.
        if self._is_tool_self_test_request(t):
            available_tools = sorted(list(self._tool_registry().keys()))
            alias_map = self._tool_self_test_aliases(available_tools)
            mentions = self._tool_self_test_extract_tools(raw_text, alias_map)
            if re.search(r"\btools?\b", t) is not None or bool(mentions):
                return "__tool_self_test__", {"request": raw_text}

        # Route pasted runtime errors to a concrete file inspection path when possible.
        runtime_error_report = (
            "traceback (most recent call last)" in t
            or "syntaxerror" in t
            or re.search(r"\b(it\s+errors?\s+out|error\s+out|throws?\s+an\s+error|exception)\b", t) is not None
        )
        if runtime_error_report:
            script_path = ""

            m = re.search(r'File\s+"([^"]+\.py)"', raw_text, re.IGNORECASE)
            if m:
                script_path = str(m.group(1) or "").strip()

            if not script_path:
                m = re.search(r'\bpython(?:\.exe)?\s+([\w./\\:-]+\.py)\b', raw_text, re.IGNORECASE)
                if m:
                    script_path = str(m.group(1) or "").strip().strip('"\'')

            if script_path:
                return "__repair_python_file__", {"path": script_path}

        # ====================================================
        # TRAVEL PRICE SEARCH (DIRECT ROUTING)
        # ====================================================
        if "web_search" in self._tool_registry():
            asks_price = re.search(r"\b(how\s+much|price|cost|fare|cheap|cheapest|budget)\b", t) is not None
            travel_intent = re.search(r"\b(flight|flights|airfare|airline|airport|ticket|route|layover|itinerary|laos|vientiane|luang\s+prabang)\b", t) is not None
            if asks_price and travel_intent:
                search_query = raw_text

                m = re.search(r"\b(?:cheap\s+)?flights?\s+to\s+(.+?)\s+(?:cost|price)\b", raw_text, re.IGNORECASE)
                if m:
                    dest = re.sub(r"\s+", " ", m.group(1)).strip(" ?.!,:;-")
                    if dest:
                        search_query = f"cheap flights to {dest} price"
                else:
                    m2 = re.search(r"\bto\s+([A-Za-z][A-Za-z\s-]{2,40})\b", raw_text, re.IGNORECASE)
                    if m2:
                        dest2 = re.sub(r"\s+", " ", m2.group(1)).strip(" ?.!,:;-")
                        search_query = f"cheap flights to {dest2} price"
                    else:
                        normalized = re.sub(r"\bhow\s+much\s+does\b", "", raw_text, flags=re.IGNORECASE)
                        normalized = re.sub(r"\bcost\b\??", "price", normalized, flags=re.IGNORECASE)
                        normalized = re.sub(r"\s+", " ", normalized).strip(" ?.!,:;-")
                        search_query = f"{normalized} price" if normalized else raw_text

                return "web_search", {
                    "query": search_query,
                    "max_results": 5,
                }

        # ====================================================
        # SPOTIFY INTENT (DIRECT ROUTING)
        # ====================================================
        spotify_available = "spotify_link" in self._tool_registry()
        if spotify_available:
            ostranauts_query = bool(intent_signals.get("ostranauts_query"))
            explicit_spotify = bool(intent_signals.get("explicit_spotify"))
            music_signal = re.search(
                r"\b(music|song|songs|track|tracks|playlist|playlists|spotify|favorites?|favourites?)\b",
                raw_text,
                re.IGNORECASE,
            ) is not None
            if ostranauts_query and not explicit_spotify and not music_signal:
                spotify_available = False

        t_spotify = re.sub(r"\bplay\s+list(s)?\b", r"playlist\1", t, flags=re.IGNORECASE)
        quoted_parts = re.findall(r'[^\w]?["\']([^"\']+)["\']', f" {raw_text}")

        def _extract_playlist_name(src: str) -> str:
            s = str(src or "").strip()
            if not s:
                return ""

            candidates: List[str] = []

            m = re.search(r"\b(?:tracks?|songs?)\s+(?:from|in)\s+(.+?)\s+playlist\b", s, re.IGNORECASE)
            if m:
                candidates.append(m.group(1).strip())

            m = re.search(r"\bplaylist\s+(.+?)\s+(?:tracks?|songs?)\b", s, re.IGNORECASE)
            if m:
                candidates.append(m.group(1).strip())

            m = re.search(r"\bfrom\s+(.+?)\s+playlist\b", s, re.IGNORECASE)
            if m:
                candidates.append(m.group(1).strip())

            for cand in candidates:
                cleaned = re.sub(
                    r"\b(?:my|the|a|an|favorite|favourite|favorites|favourites|play\s+list|playlist)\b",
                    " ",
                    cand,
                    flags=re.IGNORECASE,
                )
                cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,:;.-")
                if cleaned:
                    return cleaned

            return ""

        def _clean_track_query(src: str) -> str:
            s = str(src or "").strip()
            if not s:
                return ""

            s = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", s, flags=re.IGNORECASE)

            # Voice dictation often appends explanation text after the song request.
            # Keep the primary query and drop trailing commentary.
            split_markers = [
                r"\bit(?:'s| is)\b",
                r"\bits\b",
                r"\b(?:from|in|on)\s+my\b",
                r"\b(?:there|because|since|please)\b",
            ]
            for marker in split_markers:
                m = re.search(marker, s, re.IGNORECASE)
                if m and m.start() > 0:
                    s = s[:m.start()].strip()
                    break

            s = re.sub(r"\s+", " ", s).strip(" ,:;.-\"'")
            return s

        if spotify_available:
            # Capability questions should not trigger playback actions.
            # Example: "can you tell how long a song plays..."
            capability_q = (
                re.search(r"^\s*(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}[,\s:;-]+){0,2}(?:can|could|would|will)\s+you\b", original_text, re.IGNORECASE) is not None
                and re.search(r"\b(how\s+long|duration|when\s+one\s+song\s+ends?|switch\s+them\s+up|auto(?:matically)?\s+switch|dj)\b", original_text, re.IGNORECASE) is not None
                and re.search(r"\b(song|track|favorites?|favourites?|liked\s+songs?|liked\s+list)\b", original_text, re.IGNORECASE) is not None
            )
            if capability_q:
                return "__spotify_dj_capability__", {}

            # Favorites DJ mode controls.
            if re.search(r"\b(stop|disable|turn\s+off|cancel|end)\b", t_spotify) and re.search(r"\b(dj|auto\s*switch|continuous|hands?\s*free\s*music)\b", t_spotify):
                return "spotify_link", {"action": "stop_favorites_dj"}

            if re.search(r"\b(status|state|running)\b", t_spotify) and re.search(r"\b(dj|auto\s*switch)\b", t_spotify):
                return "spotify_link", {"action": "favorites_dj_status"}

            start_dj = (
                re.search(r"\b(start|enable|turn\s+on|run|be)\b", t_spotify) is not None
                and re.search(r"\b(dj|auto\s*switch|continuous|hands?\s*free\s*music)\b", t_spotify) is not None
                and re.search(r"\b(favorites?|favourites?|liked\s+songs?|liked\s+list)\b", t_spotify) is not None
            )
            if start_dj:
                offset_match = re.search(r"\b(?:switch|change)\s+(?:about\s+)?(\d{1,2})\s*(?:sec|secs|seconds?)\s+before\b", t_spotify, re.IGNORECASE)
                offset_seconds = 2
                if offset_match:
                    try:
                        offset_seconds = max(0, min(12, int(offset_match.group(1))))
                    except Exception:
                        offset_seconds = 2
                return "spotify_link", {
                    "action": "start_favorites_dj",
                    "switch_offset_seconds": offset_seconds,
                }

            list_verb = re.search(r"\b(list|show|get)\b", t_spotify) is not None
            liked_query = re.search(r"\b(liked\s+songs?|liked\s+list|saved\s+songs?|favorites?(?:\s+library)?|favourites?(?:\s+library)?)\b", t_spotify) is not None
            playlist_words = re.search(r"\bplaylists?\b", t_spotify) is not None
            track_words = re.search(r"\b(tracks?|songs?)\b", t_spotify) is not None

            # Create playlist intent, optionally seeded with one or more track queries.
            conversational_create_playlist_match = re.search(
                r"\b(?:we\s+need|i\s+(?:want|need)|make)\s+(?:an?\s+)?(.+?)\s+play\s*list\s*,?\s*"
                r"(?:can|could|would)\s+you\s+create\s+(?:one|it)(?:\s+with\s+(.+?))?\s*[?.!]*$",
                raw_text,
                re.IGNORECASE,
            )
            if conversational_create_playlist_match:
                playlist_name = str(conversational_create_playlist_match.group(1) or "").strip(" ,:;.-\"'")
                seed_query = str(conversational_create_playlist_match.group(2) or "").strip(" ,:;.-\"'")
                if playlist_name:
                    args: Dict[str, Any] = {
                        "action": "create_playlist",
                        "playlist_name": playlist_name,
                    }
                    if seed_query:
                        args["track_queries"] = seed_query
                    return "spotify_link", args

            create_playlist_match = re.search(
                r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:spotify\s+)?playlist\s+(?:called|named)?\s*[\"']?([^\"'.,]+?)[\"']?(?:\s+(?:and|then)\s+add\s+(?:song|track)\s+(.+))?$",
                raw_text,
                re.IGNORECASE,
            )
            if create_playlist_match:
                playlist_name = str(create_playlist_match.group(1) or "").strip(" ,:;.-\"'")
                seed_query = str(create_playlist_match.group(2) or "").strip()
                if playlist_name:
                    args: Dict[str, Any] = {
                        "action": "create_playlist",
                        "playlist_name": playlist_name,
                    }
                    if seed_query:
                        args["track_queries"] = seed_query
                    return "spotify_link", args

            # Add a requested number of search results to an existing playlist.
            add_tracks_match = re.search(
                r"\badd\s+(\d{1,2})\s+(.+?)\s+tracks?\s+to\s+(?:my\s+)?(?:spotify\s+)?"
                r"[\"']?(.+?)\s+play\s*list[\"']?\s*[?.!]*$",
                raw_text,
                re.IGNORECASE,
            )
            if add_tracks_match:
                limit = max(1, min(50, int(add_tracks_match.group(1))))
                track_query = str(add_tracks_match.group(2) or "").strip(" ,:;.-\"'")
                playlist_name = str(add_tracks_match.group(3) or "").strip(" ,:;.-\"'")
                if track_query and playlist_name:
                    return "spotify_link", {
                        "action": "add_tracks_to_playlist",
                        "playlist_name": playlist_name,
                        "track_query": track_query,
                        "limit": limit,
                    }

            # Add track to existing playlist by name.
            add_track_match = re.search(
                r"\badd\s+(?:song|track)\s+(.+?)\s+to\s+(?:my\s+)?(?:spotify\s+)?playlist\s+[\"']?([^\"'.,]+)[\"']?\b",
                raw_text,
                re.IGNORECASE,
            )
            if add_track_match:
                track_query = str(add_track_match.group(1) or "").strip(" ,:;.-\"'")
                playlist_name = str(add_track_match.group(2) or "").strip(" ,:;.-\"'")
                if track_query and playlist_name:
                    return "spotify_link", {
                        "action": "add_track_to_playlist",
                        "playlist_name": playlist_name,
                        "track_query": track_query,
                    }

            # Support commands like "play #50 Scissorhands" after a liked-songs listing,
            # even when users omit the word "Spotify".
            index_play = re.search(r"\b(?:play|place|paly)\s*(?:song\s*)?#\s*(\d{1,3})(?:\s+(.+))?", raw_text, re.IGNORECASE)
            if index_play:
                try:
                    track_index = int(index_play.group(1))
                except Exception:
                    track_index = 0
                if track_index > 0:
                    tail = str(index_play.group(2) or "").strip()
                    tail = re.sub(
                        r"\b(?:from|in|on)\s+(?:my\s+)?(?:liked\s+songs?|liked\s+list|saved\s+songs?|favorites?|favourites?|library|spotify)\b",
                        " ",
                        tail,
                        flags=re.IGNORECASE,
                    )
                    tail = re.sub(r"\s+", " ", tail).strip(" ,:;.-\"'")
                    return "spotify_link", {
                        "action": "play_favorite",
                        "track_index": track_index,
                        "track_query": tail,
                        "track_selection": f"#{track_index}",
                    }

            if liked_query and re.search(r"\b(play|place|paly)\b", t_spotify):
                m_play_tail = re.search(r"\b(?:play|place|paly)\s+(.+)$", raw_text, re.IGNORECASE)
                play_tail = str((m_play_tail.group(1) if m_play_tail else "") or "").strip()
                play_tail = re.sub(r"^song\s+", "", play_tail, flags=re.IGNORECASE)
                play_tail = re.sub(
                    r"\b(?:from|in|on)\s+(?:my\s+)?(?:liked\s+songs?|liked\s+list|saved\s+songs?|favorites?|favourites?|library|spotify)\b",
                    " ",
                    play_tail,
                    flags=re.IGNORECASE,
                )
                play_tail = _clean_track_query(play_tail)
                random_liked = re.search(
                    r"\b(random|randome|shuffle|anything|whatever|surprise\s+me|any\s+(?:song|track)|a\s+(?:song|track))\b",
                    play_tail,
                    re.IGNORECASE,
                ) is not None
                return "spotify_link", {
                    "action": "play_favorite",
                    "track_query": "" if random_liked else play_tail,
                    "track_selection": "random" if random_liked or not play_tail else "exact",
                }

            if liked_query and (list_verb or track_words):
                return "spotify_link", {"action": "liked_songs", "limit": 500}

            if playlist_words and track_words:
                playlist_name = quoted_parts[0].strip() if quoted_parts else _extract_playlist_name(raw_text)
                if playlist_name:
                    return "spotify_link", {
                        "action": "playlist_tracks",
                        "playlist_name": playlist_name,
                        "limit": 50,
                    }
                if re.search(r"\b(favorites?|favourites?)\b", t_spotify):
                    return "spotify_link", {"action": "liked_songs", "limit": 500}
                return "spotify_link", {"action": "list_playlists", "limit": 50}

            if list_verb and playlist_words:
                return "spotify_link", {"action": "list_playlists", "limit": 50}

            # Fallback: direct song-like play requests without explicitly saying "Spotify".
            # Examples: "paly On My Knees - RUFUS DU SOL", "play song blinding lights".
            loose_play = re.search(r"\b(?:play|place|paly)\s+(.+)$", raw_text, re.IGNORECASE)
            if loose_play:
                tail = str(loose_play.group(1) or "").strip()
                tail = re.sub(r"^(?:song|track)\s+", "", tail, flags=re.IGNORECASE)
                tail = re.sub(
                    r"\b(?:on|in|from)\s+(?:my\s+)?(?:spotify|liked\s+songs?|liked\s+list|saved\s+songs?|favorites?|favourites?|library)\b",
                    " ",
                    tail,
                    flags=re.IGNORECASE,
                )
                tail = _clean_track_query(tail)

                # Keep normal conversational "play with me" style requests out of music routing.
                conversational_tail = re.search(
                    r"\b(with\s+me|with\s+you|with\s+us|with\s+them|with\s+him|with\s+her|"
                    r"a\s+game|the\s+game|something|anything|around|along|together)\b",
                    tail,
                    re.IGNORECASE,
                ) is not None

                song_like = bool(
                    re.search(r"\b(song|track)\b", t_spotify)
                    or " - " in tail
                    or re.search(r"\bby\b", tail, re.IGNORECASE)
                )
                looks_like_short_title = len([w for w in tail.split() if w.strip()]) <= 8
                obviously_not_song = re.search(r"\b(game|steam|ostranauts?|ostanauts?|ostronauts?|ostranaughts?|video|movie|show|series|app|program|code|script)\b", tail, re.IGNORECASE) is not None
                if tail and not conversational_tail and (song_like or (looks_like_short_title and not obviously_not_song)):
                    return "spotify_link", {
                        "action": "play_track",
                        "track_query": tail,
                    }

        spotify_mentioned = re.search(r"\b(?:spotify|spotiy|sportify|spotfiy|spotfy|spotifi)\b", t) is not None
        if spotify_mentioned and spotify_available:
            quoted_track = quoted_parts[0].strip() if quoted_parts else ""

            def _extract_track_query(src: str) -> str:
                s = str(src or "").strip()
                if not s:
                    return ""
                # play <track> on spotify
                m = re.search(r"\b(?:play|paly)\s+(.+?)\s+on\s+(?:spotify|sportify|spotfiy|spotfy|spotifi)\b", s, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip('"\'')
                # place <track> on spotify (ASR typo)
                m = re.search(r"\bplace\s+(.+?)\s+on\s+(?:spotify|sportify|spotfiy|spotfy|spotifi)\b", s, re.IGNORECASE)
                if m:
                    return m.group(1).strip().strip('"\'')
                # play <track>
                m = re.search(r"\b(?:play|paly)\s+(.+)$", s, re.IGNORECASE)
                if m:
                    tail = m.group(1)
                    tail = re.sub(r"^song\s+", "", tail, flags=re.IGNORECASE)
                    tail = re.sub(r"\bon\s+(?:spotify|sportify|spotfiy|spotfy|spotifi)\b", "", tail, flags=re.IGNORECASE).strip()
                    return tail.strip('"\'')
                return ""

            if re.search(r"\b(what\s+song|what\s+is\s+playing|what\'?s\s+playing|now\s+playing|current\s+song|what\'?s\s+on\s+(?:spotify|spotiy|sportify|spotfiy|spotfy|spotifi)|what\s+is\s+on\s+(?:spotify|spotiy|sportify|spotfiy|spotfy|spotifi))\b", t):
                return "spotify_link", {"action": "current_playback"}

            if re.search(r"\b(pause|stop)\b", t):
                return "spotify_link", {"action": "pause"}

            if re.search(r"\b(resume|continue|unpause)\b", t):
                return "spotify_link", {"action": "resume"}

            if re.search(r"\b(play|place|paly)\b", t):
                track_query = quoted_track or _extract_track_query(raw_text)
                if re.search(r"\b(favorites?|favourites?|liked|library)\b", t):
                    return "spotify_link", {
                        "action": "play_favorite",
                        "track_query": track_query,
                        "track_selection": "exact" if track_query else "random",
                    }
                if track_query:
                    return "spotify_link", {
                        "action": "play_track",
                        "track_query": track_query,
                    }
                return "spotify_link", {"action": "play"}

        # Broad music fallback without explicit "Spotify" mention.
        if spotify_available and not spotify_mentioned:
            has_music_context = re.search(
                r"\b(music|songs?|tracks?|playlist|playlists|liked\s+songs?|saved\s+songs?|favorites?|favourites?)\b",
                t,
            ) is not None

            if has_music_context and re.search(r"\b(pause|stop)\b", t):
                return "spotify_link", {"action": "pause"}

            if has_music_context and re.search(r"\b(resume|continue|unpause)\b", t):
                return "spotify_link", {"action": "resume"}

            if re.search(r"\b(play|paly|place)\b", t) and re.search(r"\b(?:some\s+)?music\b", t):
                return "spotify_link", {"action": "play"}

        # ====================================================
        # IDENTITY REFLEX
        # ====================================================
        identity_patterns = [
            r"\bwho\s+are\s+you\b",
            r"\bwhat\s+are\s+you\b",
            r"\bwhat\s+is\s+your\s+name\b",
            r"\bare\s+you\s+mina\b",
            r"\byour\s+name\s+mina\b",
        ]
        if any(re.search(p, t) for p in identity_patterns):
            return "__mina_identity__", {}

        # ====================================================
        # COMBINED MEMORY REFLEX
        # ====================================================

        def extract_memory_write_text(raw_text: str) -> str:
            cleaned = (raw_text or "").strip()
            lowered = cleaned.lower()

            leading_triggers = [
                "remember that ",
                "remember this ",
                "remember ",
                "store this ",
                "save this ",
                "keep this ",
                "note that ",
                "note ",
                "add to memory ",
                "save to memory ",
                "write this down ",
                "keep that ",
            ]

            for trigger in leading_triggers:
                if lowered.startswith(trigger):
                    cleaned = cleaned[len(trigger):].strip()
                    lowered = cleaned.lower()
                    break

            cleaned = re.sub(
                r"\b(?:please\s+)?(?:save|store|remember|keep)\s+(?:this|that)\b[.!?]*$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()

            cleaned = re.sub(
                r"[,;:\-]?\s*(?:save|store|remember|keep)(?:\s+(?:this|that))?(?:\s+please)?[.!?]*$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()

            cleaned = re.sub(r"^[\s\-:]+|[\s\-:]+$", "", cleaned)

            return cleaned or raw_text.strip()

        scheduled_task_payload: Optional[Dict[str, Any]] = None

        def detect_memory_intent(t: str):
            nonlocal scheduled_task_payload

            def extract_scheduled_task(raw_text: str) -> Optional[Dict[str, Any]]:
                cleaned = (raw_text or "").strip()
                if not cleaned:
                    return None

                # Users often preface commands with one or more names (e.g., "Travis mina ...").
                # Keep a normalized variant so scheduling patterns still match.
                normalized = re.sub(
                    r"^\s*(?:(?:hey|yo)\s+)?(?:[A-Za-z][A-Za-z0-9_-]{1,30}[,\s:;-]+){0,3}(?=(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of|set|create|start|schedule)\b|in\s+\d+\s*(?:seconds?|minutes?|hours?)\b)",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                ).strip()

                parse_inputs: List[str] = [cleaned]
                if normalized and normalized != cleaned:
                    parse_inputs.append(normalized)

                weekday_aliases = {
                    "mon": 0,
                    "monday": 0,
                    "tue": 1,
                    "tues": 1,
                    "tuesday": 1,
                    "wed": 2,
                    "wednesday": 2,
                    "thu": 3,
                    "thur": 3,
                    "thurs": 3,
                    "thursday": 3,
                    "fri": 4,
                    "friday": 4,
                    "sat": 5,
                    "saturday": 5,
                    "sun": 6,
                    "sunday": 6,
                }

                def _clock_due_at(
                    hour_text: str,
                    minute_text: Optional[str],
                    ampm_text: Optional[str],
                    day_text: Optional[str] = None,
                ) -> Optional[float]:
                    try:
                        hour = int(hour_text)
                        minute = int(minute_text or "0")
                    except Exception:
                        return None

                    if minute < 0 or minute > 59:
                        return None

                    ampm = (ampm_text or "").strip().lower().replace(".", "")
                    if ampm:
                        if hour < 1 or hour > 12:
                            return None
                        if ampm.startswith("p") and hour != 12:
                            hour += 12
                        if ampm.startswith("a") and hour == 12:
                            hour = 0
                    elif hour < 0 or hour > 23:
                        return None

                    now_local = datetime.now()
                    day_token = str(day_text or "").strip().lower().rstrip(".?!,")

                    if day_token == "tomorrow":
                        base_dt = now_local + timedelta(days=1)
                        due_dt = base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    elif day_token in weekday_aliases:
                        target_weekday = weekday_aliases[day_token]
                        days_ahead = (target_weekday - now_local.weekday()) % 7
                        due_dt = (now_local + timedelta(days=days_ahead)).replace(
                            hour=hour,
                            minute=minute,
                            second=0,
                            microsecond=0,
                        )
                        if due_dt <= now_local:
                            due_dt = due_dt + timedelta(days=7)
                    else:
                        due_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if due_dt <= now_local:
                            due_dt = due_dt + timedelta(days=1)

                    return due_dt.timestamp()

                schedule_patterns = [
                    r"^(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:in\s+)?(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^in\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)\s+(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:in\s+)?(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?remind\s+me\s+(?:to\s+)?(?P<task>.+?)\s+in\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)$",
                ]

                alarm_relative_patterns = [
                    r"^(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)\s+(?:to\s+)?(?:go\s+off\s+)?(?:in|for)\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)(?:\s+from\s+now)?(?:\s+(?:(?:for|about|to|called|named|labelled|labeled)\s+)?(?P<task>.+))?$",
                    r"^in\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)\s+(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)(?:\s+(?:(?:for|about|to|called|named|labelled|labeled)\s+)?(?P<task>.+))?$",
                ]

                for parse_text in parse_inputs:
                    for pat in schedule_patterns:
                        m = re.search(pat, parse_text, re.IGNORECASE)
                        if not m:
                            continue

                        task = str(m.group("task") or "").strip().rstrip(".?!")
                        if not task:
                            continue

                        amount = int(m.group("num") or 0)
                        unit = str(m.group("unit") or "").strip().lower()
                        if amount <= 0:
                            continue

                        if unit.startswith("second"):
                            delta = amount
                        elif unit.startswith("minute"):
                            delta = amount * 60
                        else:
                            delta = amount * 3600

                        matched = str(m.group(0) or "").lower()
                        is_reminder = ("remind me" in matched) or ("check in" in matched)
                        tags = ["user_memory"]
                        if is_reminder:
                            tags.extend(["recurring_task", "repeat_300s", "reminder_task"])

                        return {
                            "text": task,
                            "due_at": time.time() + float(delta),
                            "tags": tags,
                        }

                    for pat in alarm_relative_patterns:
                        m = re.search(pat, parse_text, re.IGNORECASE)
                        if not m:
                            continue

                        amount = int(m.group("num") or 0)
                        unit = str(m.group("unit") or "").strip().lower()
                        if amount <= 0:
                            continue

                        if unit.startswith("second"):
                            delta = amount
                        elif unit.startswith("minute"):
                            delta = amount * 60
                        else:
                            delta = amount * 3600

                        task = str(m.groupdict().get("task") or "").strip().rstrip(".?!")
                        task_text = f"Alarm: {task}" if task else "Alarm"

                        return {
                            "text": task_text,
                            "due_at": time.time() + float(delta),
                            "tags": ["user_memory", "recurring_task", "repeat_120s", "alarm_task", "beep"],
                        }

                absolute_patterns = [
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:to\s+)?(?P<task>.+)$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+?)\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+)$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+?)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?$",
                    r"^(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:to\s+)?(?P<task>.+)$",
                    r"^at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?\s+(?:(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}\s+)?(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\s+(?:to\s+)?(?P<task>.+)$",
                ]

                alarm_absolute_patterns = [
                    r"^(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)\s+(?:for|at)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?(?:\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun))?(?:\s*(?:for|about|to)\s+(?P<task>.+))?$",
                    r"^(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)\s+(?:on\s+)?(?P<day>tomorrow|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>(?:a|p)\.?m\.?)?(?:\s*(?:for|about|to)\s+(?P<task>.+))?$",
                ]

                for parse_text in parse_inputs:
                    for pat in absolute_patterns:
                        m = re.search(pat, parse_text, re.IGNORECASE)
                        if not m:
                            continue

                        task = str(m.group("task") or "").strip().rstrip(".?!")
                        if not task:
                            continue

                        due_at = _clock_due_at(
                            str(m.group("hour") or ""),
                            m.group("minute"),
                            m.group("ampm"),
                            m.groupdict().get("day"),
                        )
                        if due_at is None:
                            continue

                        matched = str(m.group(0) or "").lower()
                        is_reminder = ("remind me" in matched) or ("check in" in matched)
                        tags = ["user_memory"]
                        if is_reminder:
                            tags.extend(["recurring_task", "repeat_300s", "reminder_task"])

                        return {
                            "text": task,
                            "due_at": float(due_at),
                            "tags": tags,
                        }

                    for pat in alarm_absolute_patterns:
                        m = re.search(pat, parse_text, re.IGNORECASE)
                        if not m:
                            continue

                        due_at = _clock_due_at(
                            str(m.group("hour") or ""),
                            m.group("minute"),
                            m.group("ampm"),
                            m.groupdict().get("day"),
                        )
                        if due_at is None:
                            continue

                        task = str(m.groupdict().get("task") or "").strip().rstrip(".?!")
                        task_text = f"Alarm: {task}" if task else "Alarm"
                        return {
                            "text": task_text,
                            "due_at": float(due_at),
                            "tags": ["user_memory", "recurring_task", "repeat_120s", "alarm_task", "beep"],
                        }

                return None

            # MEMORY READ (questions)
            read_triggers = [
                "what do you know",
                "what do you remember",
                "search memory",
                "memory search",
                "do you remember",
                "what is my",
                "what was my",
                "recall ",
                "find memory",
                "look up memory",
                "search my memory",
                "what memories",
                "what did i tell you",
                "what have i told you",
            ]

            if any(t.startswith(x) for x in read_triggers):
                return "memory_read"

            if re.match(r"^what\b.*\bmy\b", t):
                return "memory_read"

            if "do you remember" in t or "can you remember" in t:
                return "memory_read"

            if t.startswith("recall") and not t.startswith("recall this"):
                return "memory_read"

            if "memory" in t and t.startswith("find"):
                return "memory_read"

            if re.search(r"\b(how old am i|what color are my eyes?|my birthdate|my birthday|date of birth)\b", t):
                return "memory_read"

            if re.search(r"\bwho am i\b", t):
                return "memory_read"

            if re.search(r"\btell me about my\s+(name|eyes|eye color|birthdate|birthday|favorite color|favourite color)\b", t):
                return "memory_read"

            if re.search(r"\bdescribe my\s+(eyes|eye color|name)\b", t):
                return "memory_read"

            if ("how old" in t and " i " in f" {t} ") or ("eye color" in t and "my" in t):
                return "memory_read"

            if re.search(r"\b(favorite|favourite)\s+color\b", t):
                return "memory_read"

            # MEMORY WRITE (commands)
            write_triggers = [
                "remember ",
                "remember that ",
                "remember this ",
                "remember my ",
                "store this ",
                "save this ",
                "keep this ",
                "note that ",
                "note ",
                "add to memory ",
                "save to memory ",
                "write this down ",
                "keep that ",
            ]

            if any(t.startswith(x) for x in write_triggers):
                return "memory_write"

            scheduled_task = extract_scheduled_task(raw_text)
            if scheduled_task:
                scheduled_task_payload = scheduled_task
                return "__task_memory_write__"

            # Allow more natural phrasing that includes memory commands later in the sentence
            suffix_write_triggers = [
                "please store that",
                "please remember that",
                "please save that",
                "please keep that",
                "store that",
                "remember that",
                "save that",
                "keep that",
                "please store this",
                "please remember this",
                "please save this",
                "please keep this",
                "store this",
                "save this",
                "keep this",
            ]

            if any(x in t for x in suffix_write_triggers):
                return "memory_write"

            if "favorite color" in t and any(x in t for x in ["remember", "store", "save", "keep", "note"]):
                return "memory_write"

            if t.startswith("remember") and not t.startswith("remember what"):
                return "memory_write"

            return None

        # ====================================================
        # PATH ALIAS MEMORY
        # ====================================================

        quoted = re.findall(r'["\']([^"\']+)["\']', raw_text)

        alias_patterns = [
            r'\bremember\s+file\s+(.+?)\s+as\s+(.+)$',
            r'\bremember\s+path\s+(.+?)\s+as\s+(.+)$',
            r'\bremember\s+that\s+(.+?)\s+is\s+(.+)$',
        ]

        for pat in alias_patterns:
            m = re.search(pat, raw_text, re.IGNORECASE)
            if not m:
                continue

            left = m.group(1).strip().strip('"\'')
            right = m.group(2).strip().strip('"\'')

            left_is_path = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.[A-Za-z0-9]{1,8}$)', left) is not None
            right_is_path = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.[A-Za-z0-9]{1,8}$)', right) is not None

            if left_is_path and not right_is_path:
                return "__path_alias_set__", {"path": left, "alias": right}
            if right_is_path and not left_is_path:
                return "__path_alias_set__", {"path": right, "alias": left}

        if len(quoted) >= 2:
            q0 = quoted[0].strip()
            q1 = quoted[1].strip()
            q0_is_path = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.[A-Za-z0-9]{1,8}$)', q0) is not None
            q1_is_path = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.[A-Za-z0-9]{1,8}$)', q1) is not None
            if ("remember" in t or "save" in t or "store" in t) and ("alias" in t or "path" in t or "file" in t):
                if q0_is_path and not q1_is_path:
                    return "__path_alias_set__", {"path": q0, "alias": q1}
                if q1_is_path and not q0_is_path:
                    return "__path_alias_set__", {"path": q1, "alias": q0}

        if any(x in t for x in [
            "list aliases",
            "show aliases",
            "what aliases",
            "show saved aliases",
            "list path aliases",
            "show path aliases",
        ]):
            return "__path_alias_list__", {}

        if any(x in t for x in [
            "workspace root",
            "current workspace",
            "show workspace",
            "where is the workspace",
        ]):
            return "__workspace_info__", {}

        if any(x in t for x in [
            "file index",
            "index files",
            "indexed files",
            "common files",
            "where files are",
        ]):
            if any(y in t for y in ["refresh", "rebuild", "rescan", "update index", "reindex"]):
                return "__file_index_refresh__", {}
            return "__file_index_status__", {}

        set_search_mode_match = re.search(
            r"\b(?:set|use|switch|change)\s+(?:the\s+)?(?:web\s+)?search\s+mode\s+(?:to\s+)?(balanced|strict|wide)\b",
            raw_text,
            re.IGNORECASE,
        )
        if set_search_mode_match:
            return "__search_mode_set__", {
                "mode": set_search_mode_match.group(1).strip().lower(),
            }

        if re.search(r"\b(?:what(?:'s|\s+is)\s+(?:the\s+)?)?(?:current\s+)?(?:web\s+)?search\s+mode\b", t):
            return "__search_mode_get__", {}

        if re.search(r"\b(?:stop|disable|turn\s+off|cancel)\s+(?:my\s+)?(?:driving|road)\s+(?:check\s*-?ins?|companion)\b", t):
            return "__driving_checkin_stop__", {}

        driving_checkin_match = re.search(
            r"\b(?:start|enable|turn\s+on|set|begin)\s+(?:my\s+)?(?:driving|road)\s+(?:check\s*-?ins?|companion)(?:\s+(?:every|each)\s+(?P<num>\d{1,3})\s*(?P<unit>minutes?|hours?))?\b",
            t,
            re.IGNORECASE,
        )
        if not driving_checkin_match:
            driving_checkin_match = re.search(
                r"\bcheck\s*in\s+(?:with\s+me\s+)?(?:every|each)\s+(?P<num>\d{1,3})\s*(?P<unit>minutes?|hours?)\b.*\b(driv(?:e|ing)|road|trip)\b",
                t,
                re.IGNORECASE,
            )

        if not driving_checkin_match:
            if ("driving" in t or "road" in t) and ("check-in" in t or "check in" in t or "chat partner" in t):
                fallback = re.search(r"\b(?:every|each)\s+(?P<num>\d{1,3})\s*(?P<unit>minutes?|hours?)\b", t, re.IGNORECASE)
                if fallback:
                    driving_checkin_match = fallback

        if driving_checkin_match:
            num_val = driving_checkin_match.groupdict().get("num") if hasattr(driving_checkin_match, "groupdict") else None
            unit_val = driving_checkin_match.groupdict().get("unit") if hasattr(driving_checkin_match, "groupdict") else None

            try:
                amount = int(num_val) if num_val else 20
            except Exception:
                amount = 20
            amount = max(5, min(180, amount))

            unit_text = str(unit_val or "minutes").lower()
            repeat_seconds = amount * 60 if unit_text.startswith("minute") else amount * 3600
            repeat_seconds = max(300, min(6 * 3600, repeat_seconds))

            return "__driving_checkin_start__", {
                "repeat_seconds": int(repeat_seconds),
            }

        shutdown_phrase = re.search(r"\b(?:shutdown|shut\s*down|power\s*down|go\s+offline|safe\s+shutdown|stop)\b", t)
        shutdown_targeted = (
            re.search(r"\b(?:mina|mk1|api|server|yourself)\b", t) is not None
            or re.search(r"\b(?:mina|mk1|api|server)\b", original_text.lower()) is not None
            or t.startswith("shutdown")
            or t.startswith("safe shutdown")
        )
        if shutdown_phrase and shutdown_targeted:
            if re.search(r"\b(status|state|ready|readiness|waiting|blocked|clear|safe\s+to\s+shutdown)\b", t):
                return "__mina_shutdown_status__", {
                    "max_wait_seconds": 45,
                    "poll_seconds": 2,
                    "flush_max_items": 400,
                }

            force_shutdown = re.search(r"\b(force|override|even\s+if|dont\s+wait|do\s+not\s+wait)\b", t) is not None
            return "__mina_shutdown__", {
                "force": force_shutdown,
                "max_wait_seconds": 45,
                "poll_seconds": 2,
                "flush_max_items": 400,
                "embed_port": 8084,
                "post_flush_delay_ms": 1200,
            }

        if re.search(r"\b(anything new|see anything new|what(?:'s|\s+is) new|any updates?)\b", t):
            return "__git_status__", {}

        if any(x in t for x in [
            "git init",
            "init git",
            "initialize git",
            "setup git",
            "set up git",
            "create git repo",
        ]):
            return "__git_init__", {}

        if any(x in t for x in [
            "git status",
            "repo status",
            "repository status",
            "status of repo",
        ]):
            return "__git_status__", {}

        if any(x in t for x in [
            "git pull",
            "pull latest",
            "pull changes",
            "update from git",
            "update from github",
        ]):
            return "__git_pull__", {}

        if any(x in t for x in [
            "git push",
            "push changes",
            "publish changes",
            "push to github",
        ]):
            return "__git_push__", {}

        if (
            re.search(r'\b(run|execute|start)\b.*\b(test|tests|pytest)\b', t) is not None
            or re.search(r'\b(run|execute)\s+pytest\b', t) is not None
        ):
            return "__project_test_run__", {"request": raw_text}

        if any(x in t for x in [
            "save snapshot",
            "snapshot this",
            "commit changes",
            "save progress",
            "git commit",
            "commit this",
        ]):
            msg = ""
            msg_match = re.search(r'\b(?:message|msg)\s*[:=]\s*(.+)$', raw_text, re.IGNORECASE)
            if msg_match:
                msg = msg_match.group(1).strip().strip('"\'')
            else:
                quoted_msg = re.findall(r'["\']([^"\']+)["\']', raw_text)
                if quoted_msg:
                    msg = quoted_msg[-1].strip()
            return "__git_snapshot__", {"message": msg}

        if any(x in t for x in [
            "startup status",
            "startup report",
            "last worked project",
            "projects to complete",
            "list projects to complete",
            "what was i working on",
        ]):
            return "__startup_status__", {}

        if self._is_operational_incident_report(raw_text):
            return "__startup_status__", {}

        if any(x in t for x in [
            "autonomous status",
            "curiosity status",
            "self fix status",
            "self-fix status",
            "self repair status",
            "self-repair status",
        ]):
            return "__autonomous_work_status__", {}

        if any(x in t for x in [
            "fix yourself",
            "fix your self",
            "self fix",
            "self-fix",
            "self repair",
            "self-repair",
            "run autonomous work",
            "run one autonomous task",
            "run one self fix",
            "run one self repair",
        ]):
            return "__autonomous_work_run__", {}

        is_scheduled_phrase = re.search(
            r"\b(remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\b.*\b(?:in\s+\d+\s*(seconds?|minutes?|hours?)|at\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
            t,
            re.IGNORECASE,
        ) is not None

        if (not is_scheduled_phrase) and any(x in t for x in [
            "memory status",
            "memory health",
            "how is your memory",
            "how is your memory working",
            "how's your memory",
            "how is the memory",
            "how's the memory",
            "is your memory working",
            "check memory",
        ]):
            return "__memory_status__", {}

        if any(x in t for x in [
            "tidy memory",
            "clean memory",
            "cleanup memory",
            "prune memory",
            "memory cleanup",
            "memory tidy",
            "manage memory",
            "clear memory trash",
        ]):
            dry_run = any(x in t for x in ["preview", "dry run", "dry-run", "show only", "what would"])
            return "__memory_tidy__", {"dry_run": dry_run}

        explicit_script_path = self._extract_explicit_windows_path(raw_text)
        if explicit_script_path and explicit_script_path.lower().endswith(".py"):
            path_handoff_signal = re.search(
                r"\b(path\s+provided|use\s+the\s+path|is\s+the\s+code\s+generator|that(?:'|\s+)?s\s+the\s+generator|code\s+generator)\b",
                t,
            ) is not None
            run_signal = re.search(r"\b(run|execute|launch|start)\b", t) is not None

            if path_handoff_signal or run_signal:
                # Keep execution follow-ups grounded even when the user provides
                # only a file path and then says "run it" in the next turn.
                self._set_last_draft_artifact(
                    target_path=explicit_script_path,
                    content="",
                    user_input=raw_text,
                )
                if run_signal:
                    return "ps_run", {
                        "script": f'python "{explicit_script_path}"',
                    }
                return "file_read", {"path": explicit_script_path}

        create_project_match = re.search(
            r'\bcreate\s+project\s+["\']?([^"\']+?)["\']?(?:\s|$)',
            raw_text,
            re.IGNORECASE,
        )
        if create_project_match:
            project = create_project_match.group(1).strip()

            status_match = re.search(
                r'\b(?:status\s+|as\s+)(todo|in\s*progress|blocked|complete|archived)\b',
                raw_text,
                re.IGNORECASE,
            )
            status = status_match.group(1).strip().lower().replace(" ", "_") if status_match else "in_progress"

            next_steps: List[str] = []
            next_steps_match = re.search(r'\bnext\s+steps?\s*:\s*(.+)$', raw_text, re.IGNORECASE)
            if next_steps_match:
                raw_steps = next_steps_match.group(1)
                for token in re.split(r';|\|', raw_steps):
                    step = token.strip().strip('"\'')
                    if step:
                        next_steps.append(step)

            return "__project_bootstrap__", {
                "project": project,
                "status": status,
                "next_steps": next_steps,
                "request": raw_text,
            }

        mark_project_match = re.search(
            r'\bmark\s+project\s+["\']?([^"\']+?)["\']?\s+as\s+(todo|in\s*progress|blocked|complete|archived)\b',
            raw_text,
            re.IGNORECASE,
        )
        if mark_project_match:
            project = mark_project_match.group(1).strip()
            status = mark_project_match.group(2).strip().lower().replace(" ", "_")
            return "__project_tracker_update__", {
                "project": project,
                "status": status,
            }

        next_step_match = re.search(
            r'\bnext\s+step\s+for\s+["\']?([^"\']+?)["\']?\s*:\s*(.+)$',
            raw_text,
            re.IGNORECASE,
        )
        if next_step_match:
            project = next_step_match.group(1).strip()
            next_step = next_step_match.group(2).strip()
            return "__project_tracker_update__", {
                "project": project,
                "next_step": next_step,
            }

        if (
            re.search(r'\bproject\s+tracker\b', t) is not None
            or "tracker status" in t
            or "show project list" in t
            or "show tracked projects" in t
        ):
            return "__project_tracker_status__", {}

        forget_alias_match = re.search(
            r'\b(?:forget|remove|delete|clear)\s+(?:alias\s+)?(?:named\s+|called\s+)?["\']?([^"\']+?)["\']?\s*$',
            raw_text,
            re.IGNORECASE,
        )
        if forget_alias_match and ("alias" in t or "remembered" in t):
            alias = forget_alias_match.group(1).strip().lower()
            if alias:
                return "__path_alias_forget__", {"alias": alias}

        update_alias_match = re.search(
            r'\b(?:update|set|change)\s+alias\s+["\']?([^"\']+?)["\']?\s+to\s+["\']?([^"\']+)["\']?\s*$',
            raw_text,
            re.IGNORECASE,
        )
        if update_alias_match:
            alias = update_alias_match.group(1).strip()
            path = update_alias_match.group(2).strip()
            if alias and path:
                return "__path_alias_set__", {"alias": alias, "path": path}

        intent = detect_memory_intent(t)

        explicit_file_write_target = (
            re.search(r"[A-Za-z]:\\[^\r\n]+\.[A-Za-z0-9]{1,8}\b", raw_text) is not None
            and re.search(r"\b(save|write|create|make|generate|build)\b", t) is not None
        )
        if intent == "memory_write" and explicit_file_write_target:
            intent = None

        if intent == "memory_read":
            q_read = raw_text
            t_read = t
            if re.search(r"\bhow old\b|\bage\b", t_read) and not re.search(r"\b(birthdate|birthday|date of birth|dob)\b", t_read):
                q_read = raw_text + " birthdate"
            return "memory_read", {
                "query": q_read,
                "top_k": 3,
            }

        if intent == "memory_write":
            return "memory_write", {
                "text": extract_memory_write_text(raw_text),
                "kind": "fact",
                "tags": ["user_memory"],
            }

        if intent == "__task_memory_write__":
            return "__task_memory_write__", {
                "text": str((scheduled_task_payload or {}).get("text") or raw_text.strip()),
                "due_at": float((scheduled_task_payload or {}).get("due_at") or time.time()),
                "status": "pending",
                "tags": (scheduled_task_payload or {}).get("tags", ["user_memory"]),
            }

        # ====================================================
        # CODE EXECUTION
        # ====================================================

        def _extract_code_payload(raw: str) -> Tuple[str, str]:
            def _trim_trailing_advisory(code_body: str) -> str:
                text = (code_body or "").strip()
                if not text:
                    return ""

                advisory_patterns = [
                    r"\s+and\s+explain\b.*$",
                    r"\s+and\s+tell\s+me\b.*$",
                    r"\s+and\s+describe\b.*$",
                    r"\s+and\s+suggest\b.*$",
                    r"\s+then\s+explain\b.*$",
                    r"\s+then\s+tell\s+me\b.*$",
                    r"\s+then\s+describe\b.*$",
                    r"\s+then\s+suggest\b.*$",
                ]

                for pat in advisory_patterns:
                    trimmed = re.sub(pat, "", text, flags=re.IGNORECASE).strip()
                    if trimmed != text and trimmed:
                        return trimmed

                return text

            # Prefer fenced code blocks if present.
            fence = re.search(r"```([A-Za-z0-9_+-]*)\n([\s\S]*?)```", raw)
            if fence:
                lang_hint = (fence.group(1) or "").strip().lower()
                code_body = _trim_trailing_advisory((fence.group(2) or "").strip("\n"))
                if lang_hint in {"ps", "powershell", "pwsh"}:
                    return "powershell", code_body
                if lang_hint in {"python", "py"}:
                    return "python", code_body
                return "python", code_body

            # Inline formats like: "execute this code: print(2+2)"
            inline_patterns = [
                r"\b(?:execute|run)\s+(?:this\s+)?code\s*:\s*([\s\S]+)$",
                r"\b(?:execute|run)\s+(?:this\s+)?python\s*:\s*([\s\S]+)$",
                r"\b(?:execute|run)\s+(?:this\s+)?powershell\s*:\s*([\s\S]+)$",
                r"\b(?:powershell|ps)\s*:\s*([\s\S]+)$",
                r"\bpython\s*:\s*([\s\S]+)$",
            ]

            for pat in inline_patterns:
                m = re.search(pat, raw, re.IGNORECASE)
                if not m:
                    continue
                code_body = _trim_trailing_advisory((m.group(1) or "").strip())
                if not code_body:
                    continue

                low_raw = raw.lower()
                if "powershell" in low_raw or re.search(r"\bps\s*:", low_raw):
                    return "powershell", code_body
                return "python", code_body

            return "", ""

        exec_trigger = (
            re.search(r"\b(execute|run)\s+(?:this\s+)?(code|python|powershell)\b", t) is not None
            or "```" in raw_text
            or re.search(r"\b(?:python|powershell|ps)\s*:", t) is not None
        )

        if exec_trigger:
            language, code = _extract_code_payload(raw_text)
            if code:
                if not language:
                    language = "python"
                return "code_execute", {
                    "code": code,
                    "language": language,
                    "timeout": 30,
                    "check_dangerous": True,
                }

        # ====================================================
        # WEB FETCH
        # ====================================================

        url_match = re.search(r'https?://[^\s"\']+', raw_text, re.IGNORECASE)
        if url_match:
            url = url_match.group(0).strip().rstrip('.,;)]}')
            gh_match = re.search(r'https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/tree/([A-Za-z0-9_.\-/]+))?', url, re.IGNORECASE)
            if gh_match:
                owner = gh_match.group(1)
                repo = gh_match.group(2).removesuffix('.git')
                branch = gh_match.group(3) if gh_match.group(3) else 'main'
                commit_sha_match = re.search(r'latest\s+commit\s*:?\s*([0-9a-f]{7,40})', raw_text, re.IGNORECASE)
                review_terms = ["review", "architecture", "implementation", "design", "decisions"]

                action = 'repo_info'
                if 'open issues' in t or 'issues' in t:
                    action = 'open_issues'
                elif 'release' in t:
                    action = 'list_releases'
                elif 'commit' in t or 'latest commit' in t:
                    action = 'latest_commit'
                elif 'branch' in t:
                    action = 'list_branches'

                # If the prompt includes an explicit latest commit SHA, query that commit directly.
                if commit_sha_match:
                    action = 'latest_commit'
                    branch = commit_sha_match.group(1)

                # Review prompts should not get stuck on branch listing metadata.
                if action == 'list_branches' and any(term in t for term in review_terms):
                    action = 'repo_info'

                return 'github_repo', {
                    'action': action,
                    'owner': owner,
                    'repo': repo,
                    'branch': branch,
                    'per_page': 20,
                }

            fetch_triggers = [
                "fetch",
                "open url",
                "read url",
                "check url",
                "get from",
                "get content",
                "summarize",
                "summarise",
                "web",
                "website",
                "page",
            ]
            # Review-style requests that provide a repo/page URL should always fetch,
            # even when users do not explicitly say "fetch".
            review_triggers = [
                "review",
                "architecture",
                "implementation",
                "decision",
                "decisions",
                "audit",
                "assess",
                "analyze",
                "analyse",
            ]
            repo_context_triggers = [
                "repo:",
                "repository:",
                "branch:",
                "latest commit",
                "commit:",
            ]
            if any(trigger in t for trigger in fetch_triggers):
                timeout = 10
                timeout_match = re.search(r'\btimeout\s*(?:=|:)?\s*(\d{1,2})\b', t)
                if timeout_match:
                    try:
                        timeout = max(1, min(30, int(timeout_match.group(1))))
                    except Exception:
                        timeout = 10
                return "web_fetch", {
                    "url": url,
                    "timeout": timeout,
                }

            if any(trigger in t for trigger in review_triggers) or any(trigger in t for trigger in repo_context_triggers):
                return "web_fetch", {
                    "url": url,
                    "timeout": 15,
                }

        # ====================================================
        # WEB SEARCH
        # ====================================================

        web_search_triggers = [
            "search web",
            "web search",
            "search online",
            "on the web",
            "find online",
            "look up",
            "latest news",
            "current news",
            "weather in",
            "weather for",
            "what is the weather",
        ]

        if any(trigger in t for trigger in web_search_triggers):
            query = raw_text

            # Remove leading command words so query is cleaner for the search tool.
            query = re.sub(
                r'^(?:please\s+)?(?:search\s+(?:the\s+)?web\s+for|search\s+online\s+for|find\s+online|look\s+up|web\s+search)\s+',
                "",
                query,
                flags=re.IGNORECASE,
            ).strip()

            query = query.strip(" \t\n\r.?!")
            if not query:
                query = raw_text.strip()

            return "web_search", {
                "query": query,
                "max_results": 5,
                "timeout": 12,
                "include_content": False,
            }

        # External hardware spec questions (e.g., GPU model VRAM) should use
        # web lookup, not local shell execution.
        gpu_model_spec_query = (
            re.search(r"\b(rx|rtx|gtx|radeon|geforce)\b", t) is not None
            and re.search(r"\b\d{3,4}\b", t) is not None
            and re.search(r"\b(ram|vram|video\s+memory|memory)\b", t) is not None
            and re.search(r"\b(how\s+much|how\s+many|what|physical)\b", t) is not None
            and re.search(r"\b(my|this\s+pc|this\s+computer|on\s+this\s+system|your\s+system)\b", t) is None
        )
        if gpu_model_spec_query and "web_search" in self._tool_registry():
            query = re.sub(r"\brx\s+(\d{3,4})\s+tx\b", r"RX \1 XT", raw_text, flags=re.IGNORECASE)
            query = query.strip() or raw_text
            return "web_search", {
                "query": query,
                "max_results": 5,
                "timeout": 12,
                "include_content": True,
                "content_chars": 1200,
            }

        # ====================================================
        # POWERSHELL SCRIPT EXECUTION (ps_run)
        # ====================================================

        system_query_terms = [
            "what system",
            "what machine",
            "what host",
            "what computer",
            "what os",
            "what operating system",
            "what platform",
            "what hardware",
            "system specs",
            "system info",
            "hardware info",
            "list hardware",
            "list the current hardware",
            "gpu info",
            "cpu info",
            "memory info",
            "specs",
            "specification",
            "show system",
            "show specs",
            "what time is it",
            "current time",
            "local time",
            "time now",
            "what date is it",
            "current date",
            "today's date",
            "todays date",
        ]

        # Why: hardware-count phrasing is a stable system-query class, not a one-off prompt hack.
        # Guard: routes "how many ..." hardware questions through the same ps_run branch as other system intents.
        hardware_count_query = (
            re.search(r"\bhow many\b.*\b(cpu|gpu|gpus|graphics|ram|memory|disk|storage|hard drive|hard drives|drive|drives)\b", t) is not None
            or re.search(r"\b(cpu|gpu|gpus|graphics|ram|memory|disk|storage|hard drive|hard drives|drive|drives)\b.*\bhow many\b", t) is not None
            or re.search(r"\b(number of|count of|how many|how much)\b.*\b(cpu|gpu|gpus|graphics|ram|memory|disk|storage|hard drive|hard drives|drive|drives)\b", t) is not None
            or re.search(r"\b(cpu|gpu|gpus|graphics|ram|memory|disk|storage|hard drive|hard drives|drive|drives)\b.*\b(number of|count of|how many|how much)\b", t) is not None
        )

        if any(x in t for x in [
            "run ps",
            "run powershell",
            "execute ps",
            "execute powershell",
            "powershell:",
            "ps:",
            "run script",
            "execute script",
            "run this script",
            "run this ps",
            "run this powershell",
        ]) or any(x in t for x in system_query_terms) or hardware_count_query or (
            re.search(r"\bwhat\b.*\b(cpu|gpu|ram|memory|system|os|platform|specs?)\b", t) is not None
        ) or (
            re.search(r"\b(what|current|local)\b.*\b(time|date)\b", t) is not None
        ) or (
            re.search(r"\b(tell\s+me\s+about|about|show\s+me)\b", t) is not None
            and re.search(r"\b(cpu|gpu|ram|memory|system|hardware|os|platform|specs?)\b", t) is not None
        ) or (
            (t.startswith("get ") or t.startswith("show ") or t.startswith("list "))
            and any(x in t for x in ["cpu", "gpu", "memory", "ram", "hardware", "system", "spec", "os", "platform"])
        ) or (
            re.search(r"\b(probe|check|inspect|query|scan)\b.*\b(cpu|gpu|ram|memory|system|hardware|os|platform|specs?)\b", t) is not None
            or re.search(r"\b(cpu|gpu|ram|memory|system|hardware|os|platform|specs?)\b.*\b(probe|check|inspect|query|scan)\b", t) is not None
        ) or (
            re.search(r"\bping\b", t) is not None
            and re.search(r"\b(cpu|gpu|ram|memory|system|hardware|specs?)\b", t) is not None
        ):
            return "ps_run", {
                "script": _infer_ps_command(raw_text)
            }

        if re.match(r'^(run|execute|start)\b', t):
            path_like = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.(ps1|py|bat|cmd)\b)', raw_text, re.IGNORECASE) is not None
            alias_hit = self._resolve_path_alias(raw_text) is not None
            if path_like or alias_hit:
                return "ps_run", {
                    "script": _infer_ps_command(raw_text)
                }

        if any(x in t for x in [
            "file index",
            "index files",
            "indexed files",
            "common files",
            "where files are",
        ]):
            if any(y in t for y in ["refresh", "rebuild", "rescan", "update index", "reindex"]):
                return "__file_index_refresh__", {}
            return "__file_index_status__", {}

        file_dir_intent = self._detect_file_directory_intent(
            text=original_text,
            raw_text=raw_text,
            t=t,
        )
        if file_dir_intent is not None:
            return file_dir_intent

        # ====================================================
        # GITHUB REPO TOOL
        # ====================================================

        github_related = (
            "github" in t
            or ("repo" in t and re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text))
        )
        if github_related:
            action = "repo_info"
            commit_sha_match = re.search(r'latest\s+commit\s*:?\s*([0-9a-f]{7,40})', raw_text, re.IGNORECASE)
            review_terms = ["review", "architecture", "implementation", "design", "decisions"]
            if "open issues" in t or "issues" in t:
                action = "open_issues"
            elif "release" in t:
                action = "list_releases"
            elif "commit" in t or "latest commit" in t:
                action = "latest_commit"
            elif "branch" in t:
                action = "list_branches"
            elif (
                "list repos" in t
                or "github repos" in t
                or "repos for" in t
                or "repos by" in t
                or "repositories" in t
            ):
                action = "list_repos"

            owner = ""
            repo = ""
            if action == "list_repos":
                m = re.search(r'github\s+user\s+([A-Za-z0-9_.-]+)', t)
                if m:
                    owner = m.group(1)
                else:
                    m = re.search(r'repos\s+(?:for|by)\s+([A-Za-z0-9_.-]+)', t)
                    if m:
                        owner = m.group(1)
            else:
                m = re.search(r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)', text)
                if m:
                    owner = m.group(1)
                    repo = m.group(2)

            if commit_sha_match:
                action = "latest_commit"

            if action == "list_branches" and any(term in t for term in review_terms):
                action = "repo_info"

            branch_value = commit_sha_match.group(1) if commit_sha_match else "main"

            return "github_repo", {
                "action": action,
                "owner": owner,
                "repo": repo,
                "branch": branch_value,
                "per_page": 20,
            }

        # ====================================================
        # TOOL LIST
        # ====================================================

        if any(x in t for x in [
            "tool list",
            "list tools",
            "list your tools",
            "list all tools",
            "show tools",
            "show your tools",
            "available tools",
            "what tools",
            "what tools do you have",
            "what can you do",
            "what do you have",
            "show me your tools",
        ]):
            return "__tool_list__", {}

        if self._is_tool_self_test_request(t):
            available_tools = sorted(list(self._tool_registry().keys()))
            alias_map = self._tool_self_test_aliases(available_tools)
            mentions = self._tool_self_test_extract_tools(raw_text, alias_map)
            if re.search(r"\btools?\b", t) is not None or bool(mentions):
                return "__tool_self_test__", {"request": raw_text}

        scratch_note_triggers = [
            "put this in scratch",
            "save this in scratch",
            "note this in scratch",
            "keep this in scratch",
            "add this to scratch",
            "write this to scratch",
            "scratch note",
        ]

        if any(trigger in t for trigger in scratch_note_triggers):
            note_text = raw_text.strip()
            note_text = re.sub(
                r'^(?:please\s+)?(?:put|save|note|keep|add|write)\s+(?:this|that|it|the following)\s+(?:in\s+)?scratch\s*[:,-]?\s*',
                "",
                note_text,
                flags=re.IGNORECASE,
            ).strip()

            if not note_text:
                note_text = raw_text.strip()

            return "__scratch_note__", {
                "text": note_text,
                "title": self._sanitize_scratch_note_name(note_text),
            }

        planner_triggers = [
            "search",
            "find",
            "look up",
            "latest",
            "news",
            "weather",
            "web",
            "website",
            "release notes",
            "docs",
            "documentation",
            "http://",
            "https://",
        ]

        if any(token in t for token in planner_triggers):
            planned = self._plan_tool_action(raw_text)
            if planned is not None:
                return planned

        alias_hit = self._resolve_intent_alias(raw_text)
        if alias_hit is not None:
            alias_tool, alias_args = alias_hit
            if alias_tool == "spotify_link":
                ostranauts_query = bool(intent_signals.get("ostranauts_query"))
                explicit_spotify = bool(intent_signals.get("explicit_spotify"))
                music_signal = re.search(
                    r"\b(music|song|songs|track|tracks|playlist|playlists|spotify|favorites?|favourites?)\b",
                    raw_text,
                    re.IGNORECASE,
                ) is not None
                if ostranauts_query and not explicit_spotify and not music_signal:
                    alias_hit = None

            if alias_hit is not None:
                confidence = self._alias_replay_confidence(raw_text, alias_tool, alias_args)
                if confidence >= 0.8:
                    return alias_tool, alias_args

                self._set_pending_alias_replay(
                    phrase=raw_text,
                    tool_name=alias_tool,
                    tool_args=alias_args,
                    confidence=confidence,
                )
                return "__clarify_intent__", {
                    "question": "I have a learned shortcut for that phrase. Do you want me to run it now? Reply yes or no."
                }

        if action_like_request:
            planned = self._plan_tool_action(raw_text)
            if planned is not None:
                return planned
            return "__clarify_intent__", {
                "question": (
                    "I can run that with tools, but I need one detail first: "
                    "what exact target should I act on (path, URL, command, or app)?"
                )
            }

        return None, {}

    def _planner_candidate_tools(self) -> List[str]:
        preferred = [
            "web_search",
            "web_fetch",
            "memory_read",
            "file_read",
            "dir_list",
            "powershell",
            "ps_run",
        ]
        return [name for name in preferred if name in self._tool_registry()]

    def _strip_think_tags(self, text: str) -> str:
        """Remove qwen3.5 <think>...</think> reasoning blocks from response text."""
        if not text or "<think>" not in text:
            return text
        # Strip all think blocks; keep everything after the last closing tag
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        return cleaned.strip()

    def _extract_json_dict(self, text: str) -> Optional[Dict[str, Any]]:
        raw = self._strip_think_tags((text or "").strip())
        if not raw:
            return None

        # Try fenced JSON first
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, re.IGNORECASE)
        if fenced:
            try:
                parsed = json.loads(fenced.group(1).strip())
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                pass

        # Try first {...} block in the text (handles raw JSON or JSON after preamble)
        m = re.search(r"(\{[\s\S]*\})", raw)
        if m:
            try:
                parsed = json.loads(m.group(1).strip())
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                pass

        return None

    def _sanitize_planned_tool_args(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        clean = dict(args or {})

        if tool_name == "web_search":
            q = str(clean.get("query") or "").strip()
            clean["query"] = q

            try:
                clean["max_results"] = int(clean.get("max_results", 5))
            except Exception:
                clean["max_results"] = 5
            clean["max_results"] = max(1, min(10, clean["max_results"]))

            try:
                clean["timeout"] = int(clean.get("timeout", 12))
            except Exception:
                clean["timeout"] = 12
            clean["timeout"] = max(1, min(30, clean["timeout"]))

            clean["include_content"] = bool(clean.get("include_content", False))

            mode_val = str(clean.get("mode", "balanced") or "balanced").strip().lower()
            if mode_val not in {"balanced", "strict", "wide"}:
                mode_val = "balanced"
            clean["mode"] = mode_val

        elif tool_name == "web_fetch":
            clean["url"] = str(clean.get("url") or "").strip()
            try:
                clean["timeout"] = int(clean.get("timeout", 10))
            except Exception:
                clean["timeout"] = 10
            clean["timeout"] = max(1, min(30, clean["timeout"]))

        elif tool_name == "memory_read":
            clean["query"] = str(clean.get("query") or "").strip()
            try:
                clean["top_k"] = int(clean.get("top_k", 3))
            except Exception:
                clean["top_k"] = 3
            clean["top_k"] = max(1, min(8, clean["top_k"]))

        return clean

    def _plan_tool_action(self, user_text: str, prior_messages: Optional[List[Dict[str, Any]]] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
        candidates = self._planner_candidate_tools()
        if not candidates:
            return None

        system_prompt = (
            "You are a tool planner for Mina. "
            "Pick the best single tool and arguments for the user request. "
            "Use context provided (e.g., recent file operations) to resolve ambiguous references like 'that file' or 'the one I just created'. "
            "Return ONLY strict JSON with keys: tool, args, confidence, reason. "
            "tool must be one of: " + ", ".join(candidates) + ". "
            "confidence is 0.0-1.0. "
            "If no suitable tool exists, set tool to null and confidence to 0.0."
        )

        # Enrich context with recent memory if user is referencing something just created/done
        user_text_lower = user_text.lower()
        context_addition = ""
        if any(phrase in user_text_lower for phrase in ["created", "just made", "the file you", "that file", "read back", "what did you"]):
            try:
                recent = self.memory.recall("recent file operations", limit=3)
                if recent and recent.get("results"):
                    context_lines = [str(r.get("text", "")) for r in recent.get("results", [])[:2]]
                    if context_lines:
                        context_addition = "\n\nRecent context: " + " | ".join(context_lines)
            except Exception:
                pass

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        # Include last 2 turns of conversation so planner can resolve references like "run it", "read it back"
        if prior_messages:
            non_system = [m for m in prior_messages if str(m.get("role", "")).lower() != "system"]
            for m in non_system[-4:]:  # last 2 user+assistant pairs
                messages.append({"role": m["role"], "content": str(m.get("content") or "")[:800]})

        messages.append({"role": "user", "content": user_text + context_addition})

        try:
            plan_reply = self.model.chat(messages=messages, tools=None, temperature=0.1)
            plan_text = self._extract_text(plan_reply)
            plan = self._extract_json_dict(plan_text)
            if not plan:
                return None

            tool_name_raw = plan.get("tool")
            if tool_name_raw is None:
                return None

            tool_name = str(tool_name_raw).strip()
            if tool_name not in candidates:
                return None

            confidence = plan.get("confidence", 0.0)
            try:
                conf_val = float(confidence)
            except Exception:
                conf_val = 0.0

            if conf_val < self._tool_plan_confidence_threshold():
                return None

            args_raw = plan.get("args", {})
            if not isinstance(args_raw, dict):
                args_raw = {}

            args = self._sanitize_planned_tool_args(tool_name, args_raw)

            # Minimum required args for key tools.
            if tool_name == "web_search" and not str(args.get("query") or "").strip():
                return None
            if tool_name == "web_fetch" and not str(args.get("url") or "").strip():
                return None
            if tool_name == "memory_read" and not str(args.get("query") or "").strip():
                return None

            return tool_name, args
        except Exception:
            traceback.print_exc()
            return None

        
    
    # ========================================================
    # HANDLE MODEL RESPONSE
    # ========================================================

    def _handle_model_response(
        self,
        messages: List[Dict[str, str]],
        reply: Dict[str, Any],
    ) -> str:

        text = self._clean_response_text(self._extract_text(reply))

        if (
            "<remember>" in text
            and "</remember>" in text
        ):

            mem = (
                text
                .split("<remember>", 1)[1]
                .split("</remember>", 1)[0]
                .strip()
            )

            if mem:

                try:

                    self.memory.add_memory(
                        mem,
                        kind="note",
                        tags=["model"],
                    )

                except Exception:

                    traceback.print_exc()

            text = text.replace(
                f"<remember>{mem}</remember>",
                "",
            ).strip()

        text = self._strip_tool_call_markup(text)
        return text or ""

    def _enforce_tool_outcome_truth(
        self,
        assistant_text: str,
        tool_events: List[Tuple[str, Dict[str, Any]]],
    ) -> str:
        text = str(assistant_text or "").strip()
        if not isinstance(tool_events, list) or not tool_events:
            return text

        last_failed: Optional[Tuple[str, Dict[str, Any]]] = None
        for event in reversed(tool_events):
            if not isinstance(event, tuple) or len(event) != 2:
                continue
            tool_name, tool_result = event
            if isinstance(tool_result, dict) and not bool(tool_result.get("ok", True)):
                last_failed = (str(tool_name or "").strip(), tool_result)
                break

        if last_failed is None:
            return text

        failed_tool, failed_result = last_failed
        failure_summary = self._format_tool_result(failed_tool, failed_result).strip()
        if not failure_summary:
            failure_summary = f"Tool '{failed_tool}' failed: {failed_result.get('error') or 'unknown_error'}"

        if not text:
            return failure_summary
        if failure_summary.lower() in text.lower():
            return text

        return f"{failure_summary}\n\n{text}"

    def _is_execution_request_like(self, text: str) -> bool:
        q = str(text or "").strip().lower()
        if not q:
            return False

        if re.search(r"\b(run|execute|start|launch)\b", q) is None:
            return False

        if re.search(r"\b(script|command|powershell|python|pytest|test|tests|api|service|server)\b", q):
            return True

        if re.search(r"([a-z]:\\|[.]{1,2}[\\/]|\.(ps1|py|bat|cmd)\b)", q, re.IGNORECASE):
            return True

        return False

    def _looks_like_unverified_execution_claim(self, text: str) -> bool:
        body = str(text or "")
        if not body.strip():
            return False

        if re.search(r"```(?:powershell|pwsh|cmd|bash|sh|python)?", body, re.IGNORECASE):
            return True

        if re.search(r"^\s*PS\s+[^\r\n>]+>\s*", body, re.MULTILINE):
            return True

        if re.search(r"\b(exited with code|exit code|stdout:|stderr:)\b", body, re.IGNORECASE):
            return True

        return False

    def _maybe_enforce_verified_execution_reply(
        self,
        messages: List[Dict[str, Any]],
        user_input: str,
        assistant_text: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not self._is_execution_request_like(user_input):
            return None

        if not self._looks_like_unverified_execution_claim(assistant_text):
            return None

        try:
            tool_name, _tool_args = self.detect_tool_intent(user_input)
        except Exception:
            traceback.print_exc()
            tool_name = None

        if not tool_name:
            planned = self._plan_tool_action(user_input)
            if planned is not None:
                tool_name = planned[0]

        if not tool_name or tool_name == "__clarify_intent__":
            return (
                "I did not run that command yet, so I cannot claim output. "
                "Give me the exact command or file path and I will run it for real."
            )

        rescued = self._reflex_tools_and_memory(
            messages=messages,
            reply={},
            user_input=user_input,
            request_context=request_context,
        )
        if rescued is None:
            return (
                "I did not run that command yet, so I cannot claim output. "
                "Give me the exact command or file path and I will run it for real."
            )

        return self._extract_text(rescued)

    # ========================================================
    # EXTRACT TEXT
    # ========================================================

    def _extract_text(
        self,
        reply: Dict[str, Any],
    ) -> str:

        if (
            "choices" in reply
            and reply["choices"]
        ):

            msg = (
                reply["choices"][0]
                .get("message", {})
            )

            # Never allow hidden reasoning fields to leak into user-visible text.
            if isinstance(msg, dict):
                msg.pop("reasoning_content", None)

            content = msg.get("content", "") if isinstance(msg, dict) else ""

            # Handle OpenAI-compatible multimodal/content-part formats.
            if isinstance(content, list):
                parts: List[str] = []
                for part in content:
                    if isinstance(part, dict):
                        txt = part.get("text")
                        if isinstance(txt, str) and txt.strip():
                            parts.append(txt)
                    elif isinstance(part, str) and part.strip():
                        parts.append(part)
                return "\n".join(parts).strip()

            # Some backends may nest content as an object.
            if isinstance(content, dict):
                content.pop("reasoning_content", None)
                if isinstance(content.get("content"), str):
                    return content.get("content", "")
                if isinstance(content.get("text"), str):
                    return content.get("text", "")
                return ""

            text = str(content or "")

            # If content is a serialized JSON object with content/reasoning_content,
            # parse and keep only the user-visible content field.
            stripped = text.strip()
            if stripped.startswith("{") and stripped.endswith("}") and '"content"' in stripped:
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        parsed.pop("reasoning_content", None)
                        inner = parsed.get("content")
                        if isinstance(inner, str):
                            return self._strip_think_tags(inner)
                except Exception:
                    pass

            return self._strip_think_tags(text)

        return ""

# ========================================================
# CORE STATUS
# ========================================================

    def get_core_status(self) -> Dict[str, Any]:

        try:

            now_monotonic = time.monotonic()
            seconds_since_last_input = max(
                0.0,
                now_monotonic - float(self._last_user_activity_monotonic),
            )
            seconds_since_shutdown: Optional[float] = self._seconds_since_last_shutdown
            if self._last_shutdown_at:
                try:
                    shutdown_dt = datetime.fromisoformat(str(self._last_shutdown_at))
                    seconds_since_shutdown = max(0.0, (datetime.now() - shutdown_dt).total_seconds())
                    self._seconds_since_last_shutdown = seconds_since_shutdown
                except Exception:
                    pass

            return {
                "core_temp": "STABLE",
                "memory_bus": "ONLINE",
                "neural_cache": "ACTIVE",
                "io_channels": "CLEAR",
                "core_online": True,
                "model_loaded": True,
                "tool_count": len(self._tool_registry()),
                "memory_enabled": self.memory is not None,
                "personality_loaded": bool(self.system_prompt),
                "idle_checkin_pending": self._idle_checkin_pending,
                "idle_checkin_interval_seconds": self.idle_checkin_interval_seconds,
                "last_user_activity_at": self._last_user_activity_at,
                "seconds_since_last_user_input": round(seconds_since_last_input, 1),
                "heartbeat_interval_seconds": self.idle_clock_interval_seconds,
                "heartbeat_tick": self._turn_clock_tick,
                "heartbeat_last_tick_at": self._turn_clock_last_tick_at,
                "heartbeat_elapsed_seconds": round(float(self._turn_clock_last_elapsed_seconds), 1),
                "runtime_state_last_written_at": self._runtime_state_last_written_at,
                "last_shutdown_at": self._last_shutdown_at,
                "seconds_since_last_shutdown": seconds_since_shutdown,
                "away_nag_threshold_seconds": self.away_nag_threshold_seconds,
                "away_nag_pending": self._away_nag_prompt_pending,
                "memory_engine": self.memory.verify_integrity(),
                "level": "NORMAL",
            }

        except Exception as e:

            traceback.print_exc()

            return {
                "core_temp": "ERROR",
                "memory_bus": "ERROR",
                "neural_cache": "ERROR",
                "io_channels": "ERROR",
                "error": str(e),
                "level": "CRITICAL",
            }
    # ========================================================
    # DATABASE STATUS
    # ========================================================

    def get_db_status(self) -> Dict[str, Any]:

        try:

            return {
                "db_link": "CONNECTED",
                "db_sync": "IN_SYNC",
                "db_latency": "LOW",
                "active_connections": "1",
                "read_ops": "OK",
                "write_ops": "OK",
                "cache_state": "WARM",
                "last_commit": time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "memory_db": getattr(
                    self.memory,
                    "db_path",
                    "unknown"
                ),
                "level": "NORMAL",
            }

        except Exception as e:

            traceback.print_exc()

            return {
                "db_link": "ERROR",
                "db_sync": "ERROR",
                "db_latency": "UNKNOWN",
                "active_connections": "0",
                "read_ops": "FAIL",
                "write_ops": "FAIL",
                "cache_state": "COLD",
                "last_commit": "FAILED",
                "error": str(e),
                "level": "CRITICAL",
            }

    # ========================================================
    # AUTO-PROMOTED MEMORY VIEW
    # ========================================================

    def get_auto_promoted_memories(
        self,
        limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            items = self.memory.get_auto_promoted_memories(limit=limit)
            return {
                "ok": True,
                "count": len(items),
                "items": items,
            }

        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "count": 0,
                "items": [],
                "error": str(e),
            }

    # ========================================================
    # DIRECT TOOL RUN
    # ========================================================

    def run_tool(
        self,
        name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:

        return self.tools.run(
            name,
            args,
        )


# ============================================================
# DIRECT TEST MODE
# ============================================================

if __name__ == "__main__":

    core = MK1Core()

    result = core.process(
        "list tools"
    )

    print("\n========================================")
    print("FINAL RESULT")
    print("========================================")
    print(result)

