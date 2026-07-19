# core.py

import os
import time
import json
import hashlib
import html
import atexit
import traceback
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.config_loader import load_config
from tools.tool_loader import ToolLoader
from memory.mk1_memory import MK1Memory


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

    def _has_system_messages(self, messages: List[Dict[str, Any]]) -> bool:
        return any(str(m.get("role", "")).strip().lower() == "system" for m in (messages or []))

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
        # Build a valid 1x1 RGB PNG in-memory so the probe does not depend on
        # external files or potentially invalid hardcoded image bytes.
        try:
            import base64
            import binascii
            import struct
            import zlib

            width = 1
            height = 1
            bit_depth = 8
            color_type = 2  # Truecolor RGB

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

            # One scanline: filter byte (0) + one red pixel (255, 0, 0)
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
            # Fallback if local generation fails for any reason.
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

        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Vision calls are slower; cap response length to keep UI snappy.
        if has_image and self.vision_max_tokens > 0:
            payload["max_tokens"] = self.vision_max_tokens
        if has_image and self.vision_reasoning_effort:
            payload["reasoning_effort"] = self.vision_reasoning_effort

        print("\n========================================")
        print("MK1 → LM STUDIO REQUEST")
        print("========================================")
        print("URL:")
        print(self.chat_url)

        try:

            resp = requests.post(
                self.chat_url,
                json=payload,
                timeout=120,
            )

            # Some OpenAI-compatible backends may not accept reasoning_effort.
            if resp.status_code >= 400 and "reasoning_effort" in payload:
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
                    retry_payload = dict(payload)
                    retry_payload.pop("reasoning_effort", None)
                    resp = requests.post(
                        self.chat_url,
                        json=retry_payload,
                        timeout=120,
                    )

            # Some templates reject system role entirely (e.g. specific GGUF chat templates).
            if resp.status_code >= 400 and self._has_system_messages(payload.get("messages", [])):
                body = ""
                try:
                    body = resp.text or ""
                except Exception:
                    body = ""

                if self._is_system_role_unsupported_error(body):
                    retry_payload = dict(payload)
                    retry_payload["messages"] = self._flatten_system_messages(payload.get("messages", []))
                    resp = requests.post(
                        self.chat_url,
                        json=retry_payload,
                        timeout=120,
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

            return data

        except Exception as e:

            print("\n========================================")
            print("LM STUDIO ERROR")
            print("========================================")

            traceback.print_exc()

            return {
                "choices": [
                    {
                        "message": {
                            "content": f"(LM Studio error: {str(e)})"
                        }
                    }
                ]
            }

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
    ):

        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        attempts = [dict(payload)]
        if self._has_system_messages(payload.get("messages", [])):
            fallback_payload = dict(payload)
            fallback_payload["messages"] = self._flatten_system_messages(payload.get("messages", []))
            attempts.append(fallback_payload)

        last_http_error: Optional[Exception] = None

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

                return
            except Exception as e:
                last_http_error = e
            finally:
                resp.close()

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

            "default_model": self.config.get(
                "model",
                "default_model",
                "local-model",
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
                True,
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
        self._last_time_passage_note_monotonic: float = self._turn_clock_started_monotonic
        self._idle_checkin_pending: bool = False
        self._idle_checkin_due_since_at: Optional[str] = None

        workspace_root_cfg = self.config.get(
            "workspace",
            "root",
            r"E:\workspace",
        )
        self.workspace_root = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(
                    str(workspace_root_cfg or r"E:\workspace")
                )
            )
        )

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
        self.startup_context = self._build_startup_context()
        self._seed_startup_memory_facts()
        self._seed_authoritative_identity_memory()
        self._seed_runtime_capability_snapshot()
        self.last_active_project_path: Optional[str] = None

        self._validate_required_tool_routes()
        self._start_turn_clock_heartbeat()
        atexit.register(self.close)

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
    ) -> None:
        try:
            self.memory.add_memory(
                user_input,
                kind="interaction",
                tags=["short_term", "user_turn"],
            )

            self.memory.add_memory(
                assistant_text,
                kind="interaction",
                tags=["short_term", "assistant_turn"],
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
            r'(?:<\|tool_call\>|<\|[\w.-]+\|)\s*call:(\w+)\{([\s\S]*?)\}(?:<tool_call\|>|\|>|>)',
            flags=re.IGNORECASE,
        )

    def _strip_tool_call_markup(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        return self._tool_call_pattern().sub("", cleaned).strip()

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
        convo = list(messages)
        convo.append({
            "role": "user",
            "content": user_input,
        })
        convo.append({
            "role": "system",
            "content": (
                "You are answering from verified tool output. "
                "First sentence must state one concrete fact from VERIFIED OUTPUT. "
                "After that, you may give concise suggestions, interpretation, or next steps. "
                "Do not invent missing data, file contents, failures, or tool results. "
                "If the output shows an error, say the error plainly first, then suggest what to do next. "
                "Keep it concise and in Mina voice."
            ),
        })
        convo.append({
            "role": "assistant",
            "content": f"VERIFIED OUTPUT:\n```text\n{formatted}\n```",
        })

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
        if tool_name == "__tool_list__":
            return formatted

        if tool_name == "file_read" and not self._wants_grounded_tool_analysis(user_input):
            return formatted

        if tool_name in {"file_read", "ps_run", "code_execute", "__project_test_run__"} and self._wants_grounded_tool_analysis(user_input):
            return self._render_grounded_tool_analysis(
                messages=messages,
                user_input=user_input,
                tool_name=tool_name,
                formatted=formatted,
                tool_ok=tool_ok,
            )

        convo = list(messages)
        convo.append({
            "role": "user",
            "content": user_input,
        })

        status = "success" if tool_ok else "failure"
        convo.append({
            "role": "system",
            "content": (
                "Write exactly one short Mina-style line with no quotes, no labels, no markdown, and no emojis. "
                "Do not include technical details; those are handled separately."
            ),
        })

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

    def _normalize_path(self, path: str) -> str:
        p = (path or "").strip().strip('"\'')
        if not p:
            return ""
        p = os.path.expanduser(os.path.expandvars(p))
        if not os.path.isabs(p):
            p = os.path.join(self.workspace_root, p)
        return os.path.abspath(p)

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

        def _heartbeat() -> None:
            interval = float(getattr(self, "idle_clock_interval_seconds", 5))
            while not self._turn_clock_stop_event.wait(interval):
                try:
                    self._advance_turn_clock()
                    self._refresh_idle_checkin_state()
                except Exception:
                    traceback.print_exc()

        self._turn_clock_thread = threading.Thread(
            target=_heartbeat,
            name="mk1-turn-clock-heartbeat",
            daemon=True,
        )
        self._turn_clock_thread.start()

    def close(self) -> None:
        self._turn_clock_stop_event.set()
        thread = self._turn_clock_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

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
                "fast_command_skip_context": bool(getattr(self, "fast_command_skip_context", True)),
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

    def _path_alias_record(self, alias: str, path: str) -> str:
        return f"path_alias::{alias.strip().lower()}::{path}"

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
                    "project_count": self.startup_context.get("project_count", 0),
                    "last_worked_project": last_project,
                    "incomplete_projects": incomplete,
                },
                "error": None,
            }
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

# ========================================================
# PROCESS
# ========================================================

    def process(
        self,
        user_input: str,
        image_attachment: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        try:

            print("\n========================================")
            print("MK1 PROCESS START")
            print("========================================")
            print("USER INPUT:")
            print(user_input)

            turn_clock = self._advance_turn_clock()
            self._refresh_idle_checkin_state()
            checkin_prompt = self._consume_idle_checkin_prompt()
            self._register_user_activity()

            # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            # HYBRID MEMORY ENGINE MAINTENANCE TICK
            # Runs scheduled backups, trimming, health checks.
            # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            self._maybe_run_maintenance_tick()

            fast_command_mode = bool(getattr(self, "fast_command_mode", True))
            fast_command_skip_context = bool(getattr(self, "fast_command_skip_context", True))

            command_like = fast_command_mode and self._is_command_like_query(user_input)
            if command_like and fast_command_skip_context:
                context = ""
            else:
                context = self.build_context(user_input)

            working_memory = self._build_turn_working_memory(user_input)

            messages: List[Dict[str, Any]] = []

            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt,
                })

            if context:
                messages.append({
                    "role": "system",
                    "content": f"Relevant memory context:\n{context}",
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
                    )

            if reflex_reply is not None:

                final_text = self._extract_text(
                    reflex_reply
                )

                self._store_turn_memory(
                    user_input=user_input,
                    assistant_text=final_text,
                )

                return {
                    "reply": final_text
                }

            # =================================================
            # NORMAL MODEL FLOW
            # =================================================

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
            )

            # Handle tool calls if present
            max_tool_loops = 3
            loop_count = 0
            last_assistant_content = ""
            tool_pattern = self._tool_call_pattern()
            
            while reply and loop_count < max_tool_loops:
                loop_count += 1
                
                # Get the message content
                choice = reply.get("choices", [{}])[0] if reply.get("choices") else {}
                message = choice.get("message", {})
                text_content = message.get("content", "") or choice.get("text", "")
                
                # Parse Gemma custom tool format
                matches = re.findall(tool_pattern, text_content)
                
                if matches and text_content != last_assistant_content:
                    # Found new tool calls - prevent infinite loops on same content
                    last_assistant_content = text_content
                    cleaned_content = tool_pattern.sub("", text_content).strip()
                    
                    messages.append({
                        "role": "assistant",
                        "content": cleaned_content or "[processing...]",
                    })
                    
                    # Execute each tool
                    for tool_name, args_str in matches:
                        tool_args = {}
                        try:
                            for pair in args_str.split(", "):
                                if ":" in pair:
                                    k, v = pair.split(":", 1)
                                    v = v.strip().strip('"\'')
                                    tool_args[k.strip()] = v
                        except:
                            pass
                        
                        print(f"\n>>> TOOL CALL: {tool_name} {tool_args}")
                        tool_result = self.tools.run(tool_name, tool_args)
                        print(f">>> RESULT: {str(tool_result)[:200]}")
                        
                        messages.append({
                            "role": "tool",
                            "content": str(tool_result),
                        })
                    
                    # Get model response with tool results
                    try:
                        reply = self.model.chat(
                            messages=messages,
                            tools=None,  # Don't pass tools - LMStudio hangs
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

            self._store_turn_memory(
                user_input=user_input,
                assistant_text=final_text,
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
    ):

        try:
            turn_clock = self._advance_turn_clock()
            self._refresh_idle_checkin_state()
            checkin_prompt = self._consume_idle_checkin_prompt()
            self._register_user_activity()
            self._maybe_run_maintenance_tick()

            command_like = self.fast_command_mode and self._is_command_like_query(user_input)
            if command_like and self.fast_command_skip_context:
                context = ""
            else:
                context = self.build_context(user_input)

            working_memory = self._build_turn_working_memory(user_input)

            messages: List[Dict[str, Any]] = []

            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt,
                })

            if context:
                messages.append({
                    "role": "system",
                    "content": f"Relevant memory context:\n{context}",
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

            parts: List[str] = []
            for piece in self.model.chat_stream(
                messages=messages,
                tools=None,
            ):
                if not piece:
                    continue
                parts.append(piece)
                yield piece

            final_text = self._clean_response_text("".join(parts)).strip()
            final_text = re.sub(
                r'<\|tool_call\>call:[^\n]*?<tool_call\|>\.?',
                '',
                final_text,
                flags=re.IGNORECASE,
            ).strip()
            final_text = re.sub(
                r'(?:<\|[^|>\n]+\|)\s*call:[^\n]*?\|>\.?',
                '',
                final_text,
                flags=re.IGNORECASE,
            ).strip()

            if not final_text:
                # Fallback in case backend didn't emit stream chunks.
                reply = self.model.chat(messages=messages, tools=None)
                final_text = self._handle_model_response(messages, reply)
                for chunk in self._iter_text_chunks(final_text):
                    yield chunk

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

        # Path-looking strings are usually operational commands.
        if re.search(r"([a-z]:\\|[.]{1,2}[\\/]|\\\w+\\|\.(ps1|py|bat|cmd)\b)", q, re.IGNORECASE):
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

            long_semantic = self.memory.search(
                query,
                top_k=4,
                include_kinds=["fact", "tool", "preference", "procedure"],
            )

            if not recent_turns and not short_semantic and not long_semantic:
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

            if not lines:
                return ""

            return "\n".join(lines[:18])

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
                return "(directory empty)"
            return "\n".join(files)

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

        if tool_name == "__project_tracker_status__":
            if not result.get("ok"):
                return f"Project tracker status failed: {result.get('error')}"
            payload = result.get("result", {})
            lines = [
                f"Tracker file: {payload.get('tracker_path')}",
                f"Updated at: {payload.get('updated_at')}",
                f"Tracked projects: {payload.get('project_count')}",
            ]
            incomplete = payload.get("incomplete_projects", [])
            if incomplete:
                lines.append("Projects to complete:")
                for p in incomplete[:20]:
                    steps = p.get("next_steps", []) if isinstance(p.get("next_steps", []), list) else []
                    lines.append(f"- {p.get('name')} [{p.get('status')}]")
                    if steps:
                        lines.append(f"  next: {steps[0]}")
            else:
                lines.append("Projects to complete: none detected")
            return "\n".join(lines)

        if tool_name == "__project_tracker_update__":
            if not result.get("ok"):
                return f"Project tracker update failed: {result.get('error')}"
            payload = result.get("result", {})
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
                text = item.get("text", "").strip()
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
                return f"No web results found for: {q}"

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

        if tool_name == "ps_run":
            if not result.get("ok"):
                return f"PowerShell execution failed: {result.get('error')}"
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
                        kind="tool",
                        tags=["tool_result", "hardware_snapshot", "suggestion_context"],
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
    ) -> Optional[Dict[str, Any]]:

        tool_name, tool_args = self.detect_tool_intent(user_input)
        if not tool_name:
            return None

        print("\n========================================")
        print("TOOL DETECTED")
        print("========================================")
        print(tool_name)

        print("\n========================================")
        print("TOOL ARGS")
        print("========================================")
        print(tool_args)

        if tool_name == "__mina_identity__":
            return {
                "choices": [
                    {
                        "message": {
                            "content": "I am Mina, your chaotic gremlin-fox assistant.",
                        }
                    }
                ]
            }

        if tool_name in ("file_write", "file_append"):
            existing = str(tool_args.get("content", "") or "").strip()
            target_path = str(tool_args.get("path", "") or "").strip()

            if target_path and not existing:
                generated = self._generate_file_content_from_intent(
                    user_input=user_input,
                    target_path=target_path,
                )
                if generated.strip():
                    tool_args["content"] = generated

        if tool_name in ("file_read", "file_write", "file_append", "file_delete", "dir_create", "dir_list"):
            path_val = str(tool_args.get("path", "") or "").strip()
            if path_val:
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
        elif tool_name == "__startup_status__":
            result = self._startup_status()
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

        if tool_name in (
            "__tool_list__",
            "__scratch_note__",
            "__path_alias_set__",
            "__path_alias_list__",
            "__path_alias_forget__",
            "__workspace_info__",
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
            "__scaffold__",
            "memory_write",
            "web_search",
            "web_fetch",
            "dir_list",
            "dir_create",
            "file_read",
            "file_write",
            "file_append",
            "file_delete",
            "file_move",
            "safe_copy",
            "github_repo",
            "ps_run",
            "code_execute",
        ):
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

    def detect_tool_intent(
        self,
        text: str,
    ) -> Tuple[Optional[str], Dict[str, Any]]:

        if not text:
            return None, {}

        raw_text = text.strip()
        t = raw_text.lower().strip()

        # Users often prefix requests with vocatives like "you" or "mina".
        # Strip these so intent routing still matches tool/memory patterns.
        raw_text = re.sub(r'^\s*(?:hey\s+)?(?:you|mina)[,\s:;-]+', '', raw_text, flags=re.IGNORECASE).strip()
        raw_text = re.sub(
            r'^\s*(?:hey\s+)?[A-Za-z][A-Za-z0-9_-]{1,30}[,\s:;-]+(?=(?:please\s+)?(?:remind\s+me|check\s+in|do|work\s+on|handle|take\s+care\s+of)\b|in\s+\d+\s*(?:seconds?|minutes?|hours?)\b)',
            '',
            raw_text,
            flags=re.IGNORECASE,
        ).strip()
        t = raw_text.lower().strip()

        if t.startswith("please "):
            t = t[7:].strip()

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
                    r"^(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)\s+(?:to\s+)?(?:go\s+off\s+)?(?:in|for)\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)(?:\s*(?:from\s+now)?)?(?:\s*(?:for|about|to)\s+(?P<task>.+))?$",
                    r"^in\s+(?P<num>\d+)\s*(?P<unit>seconds?|minutes?|hours?)\s+(?:please\s+)?(?:set|create|start|schedule)\s+(?:an?\s+)?(?:alarm|timer)(?:\s*(?:for|about|to)\s+(?P<task>.+))?$",
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
        ]) or any(x in t for x in system_query_terms) or (
            re.search(r"\bwhat\b.*\b(cpu|gpu|ram|memory|system|os|platform|specs?)\b", t) is not None
        ) or (
            re.search(r"\b(what|current|local)\b.*\b(time|date)\b", t) is not None
        ) or (
            (t.startswith("get ") or t.startswith("show ") or t.startswith("list "))
            and any(x in t for x in ["cpu", "gpu", "memory", "ram", "hardware", "system", "spec", "os", "platform"])
        ):
            return "ps_run", {
                "script": raw_text
            }

        if re.match(r'^(run|execute|start)\b', t):
            path_like = re.search(r'([A-Za-z]:\\|[.]{1,2}[\\/]|\.(ps1|py|bat|cmd)\b)', raw_text, re.IGNORECASE) is not None
            alias_hit = self._resolve_path_alias(raw_text) is not None
            if path_like or alias_hit:
                return "ps_run", {
                    "script": raw_text
                }

        # ====================================================
        # DIRECTORY CREATE
        # ====================================================

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

        
        # ====================================================
        # FILE READ
        # ====================================================

        if any(x in t for x in [
            "read file",
            "open file",
            "show file",
        ]):
            m = re.search(r'["\']([^"\']+)["\']', text)
            if not m:
                m = re.search(r'[“‘]([^”’]+)[”’]', text)
            if not m:
                m = re.search(r'file\s+([^\s]+)', text, re.IGNORECASE)

            path = m.group(1).strip() if m else ""
            return "file_read", {"path": path}

        # ====================================================
        # FILE DELETE
        # ====================================================

        if any(x in t for x in [
            "delete file",
            "remove file",
            "delete script",
            "remove script",
            "delete the file",
            "remove the file",
            "delete this file",
            "remove this file",
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

        # ====================================================
        # FILE MOVE / RENAME
        # ====================================================

        if any(x in t for x in [
            "move file",
            "move script",
            "move this file",
            "move the file",
            "rename file",
            "rename script",
            "rename this file",
            "rename the file",
            "rename to",
            "move to",
        ]) or (
            ("move" in t or "rename" in t)
            and re.search(r'[A-Za-z]:\\', text)
            and " to " in t
        ):
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

            if src and dst and not os.path.isabs(dst):
                dst = os.path.join(os.path.dirname(src), dst)

            return "file_move", {"src": src, "dst": dst}

        # ====================================================
        # FILE APPEND
        # ====================================================

        if any(x in t for x in [
            "append file",
            "append to file",
            "add to file",
            "add content to file",
            "append text to",
            "append this",
            "append the following",
        ]) or (
            "append" in t
            and re.search(r'[A-Za-z]:\\', text)
            and any(x in t for x in ["to", "with", "content", "text", "line"])
        ):
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

        # ====================================================
        # FILE WRITE
        # ====================================================

        def extract_file_write_path(raw: str) -> str:
            quoted = re.findall(r'["\']([^"\']+)["\']', raw)
            for q in quoted:
                candidate = q.strip()
                if re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                    return candidate.rstrip('.,;:')

            patterns = [
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

        file_write_intent = any(x in t for x in [
            "create file",
            "write file",
            "write to file",
            "write this to file",
            "save file",
            "make file",
            "create a python script",
            "create a script",
            "write a python script",
            "save a python script",
            "make a python script",
            "create script",
            "make script",
            "write script",
            "save script",
        ]) or (
            ("at " in t or "to " in t)
            and any(x in t for x in ["print", "prints", "code", "content", "with content"])
        ) or (
            re.search(r'\b(create|write|save|make|generate|build)\b', t) is not None
            and (
                re.search(r'\b(file|script|module)\b', t) is not None
                or re.search(r'\.[A-Za-z0-9]{1,8}\b', raw_text) is not None
            )
        )

        if file_write_intent:
            path = extract_file_write_path(raw_text)
            if not path:
                return None, {}

            # Guardrail: if target looks like a directory path in a scaffold-style request,
            # route to scaffold logic instead of creating an extensionless file.
            if (
                not re.search(r'\.[A-Za-z0-9]{1,8}$', path)
                and (
                    "project scaffold" in t
                    or re.search(r'\b(make|create)\b.*\b(project|structure)\b', t) is not None
                    or re.search(r'\b(src|tests?)\b', t) is not None
                )
            ):
                return "__scaffold__", {"request": raw_text}

            content = extract_file_write_content(raw_text, t)

            if path.lower().endswith('.py') and content:
                if not content.strip().startswith('print') and ('print' in t or 'prints' in t or 'python script' in t):
                    content = f'print({json.dumps(content)})'

            return "file_write", {"path": path, "content": content}

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
        # DIRECTORY LIST
        # ====================================================

        dir_list_triggers = [
            "list directory",
            "list files",
            "show files",
            "show directory",
            "directory contents",
            "folder contents",
            "what files",
            "what's in this folder",
            "contents of",
            "list the contents",
            "list contents",
            "show contents",
            "what is in",
            "toy box",
            "toybox",
        ]

        if (
            any(x in t for x in dir_list_triggers)
            or re.search(r'\blist\b\s+[a-zA-Z]:\\', t)
            or re.search(r'\bcontents\b.*[a-zA-Z]:\\', t)
        ):
            workspace_root = self.workspace_root
            path = workspace_root
            
            # Try to extract path after "in " (e.g., "list files in c:\")
            m = re.search(r'in\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            
            # If not found, try "on " (e.g., "list files on c:\")
            if not m:
                m = re.search(r'on\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            
            # If not found, try "of " (e.g., "list the contents of c:\")
            if not m:
                m = re.search(r'of\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
            
            # If still not found, try bare path pattern (e.g., "list c:\" or "list files c:\")
            if not m:
                m = re.search(r'(?:list\s+files\s+)?([a-zA-Z]:\\[^\n\r]*)', text, re.IGNORECASE)
            
            # If still not found, try just a drive letter (e.g., "list e:\" or "list files e:\")
            if not m:
                m = re.search(r'(?:list\s+)?([a-zA-Z]:\\?)(?:\s|$)', text, re.IGNORECASE)
            
            if m:
                path = m.group(1).strip()
            elif any(x in t for x in ["toy box", "toybox"]):
                path = os.path.join(workspace_root, "toys")

            return "dir_list", {"path": path}

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
        return [name for name in preferred if name in self.tools.tools]

    def _extract_json_dict(self, text: str) -> Optional[Dict[str, Any]]:
        raw = (text or "").strip()
        if not raw:
            return None

        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()

        if not (raw.startswith("{") and raw.endswith("}")):
            m = re.search(r"(\{[\s\S]*\})", raw)
            if not m:
                return None
            raw = m.group(1).strip()

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
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

    def _plan_tool_action(self, user_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        candidates = self._planner_candidate_tools()
        if not candidates:
            return None

        system_prompt = (
            "You are a tool planner for Mina. "
            "Pick the best single tool and arguments for the user request. "
            "Return ONLY strict JSON with keys: tool, args, confidence, reason. "
            "tool must be one of: " + ", ".join(candidates) + ". "
            "confidence is 0.0-1.0. "
            "If no suitable tool exists, set tool to null and confidence to 0.0."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

        try:
            plan_reply = self.model.chat(messages=messages, tools=None, temperature=0.0)
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

            if conf_val < 0.55:
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
                            return inner
                except Exception:
                    pass

            return text

        return ""

# ========================================================
# CORE STATUS
# ========================================================

    def get_core_status(self) -> Dict[str, Any]:

        try:

            return {
                "core_temp": "STABLE",
                "memory_bus": "ONLINE",
                "neural_cache": "ACTIVE",
                "io_channels": "CLEAR",
                "core_online": True,
                "model_loaded": True,
                "tool_count": len(self.tools.tools),
                "memory_enabled": self.memory is not None,
                "personality_loaded": bool(self.system_prompt),
                "idle_checkin_pending": self._idle_checkin_pending,
                "idle_checkin_interval_seconds": self.idle_checkin_interval_seconds,
                "last_user_activity_at": self._last_user_activity_at,
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

