# Two-Model Split: Findings for Next Session

Date: 2026-09-16 (read-only pass, no changes made)

## Goal
Mina (Dolphin, chat) on one GPU; a coder model (qwen2.5-coder-7b-instruct) on the
second GPU. Chat half = conversational voice; coder half = exact execution.

## Current state (what's actually in the code)

### One model lane
- `agent/core.py` has ONE `LMStudioClient` (self.model). Every call — chat, code
  generation, tool syntax, autonomy, scaffold planning — goes through it.
- `LMStudioClient.chat()` hardcodes `"model": self.default_model` (line ~535). No
  per-call model override exists.

### Where the model is called (14 sites)
- Conversational (keep on Dolphin): ~4145, ~4192, ~4328, ~4517 (reply cleanup /
  grounded analysis), ~10044 (main process), ~10071 (followup), ~10216, ~10276
  (process_stream), ~12820 (reflex followup), ~9895 (process tool loop)
- Coder-lane candidates (route to coder model when split lands):
  - ~5393 — `_generate_file_content_from_intent` (file content generation)
  - ~6728 — autonomous patch/summary JSON (curiosity lane)
  - ~9357 — `_build_scaffold_plan` (scaffold JSON planner)
  - ~14799 — `_plan_tool_action` (tool planner)

### Config (config/mk1_config.json)
- `default_model: "dolphin3-nsfw-gf-chat"` (chat)
- `switch_allowed` includes `qwen2.5-coder-7b-instruct`? — NO. Current list:
  qwen2.5-7b-instruct, mistral-nemo-heretic, qwen/qwen3.5-9b, dolphin3-nsfw-gf-chat.
  The coder model is NOT in switch_allowed yet.
- No `coder_model` field exists.

## What the split needs (not built yet)
1. A `model_override` param on `LMStudioClient.chat()` so a caller can target the
   coder model instead of default_model.
2. A `coder_model` config field (e.g. "qwen2.5-coder-7b-instruct").
3. Route the four coder-lane call sites (5393, 6728, 9357, 14799) to the coder
   model via the override.
4. The coder model must be loadable in LM Studio alongside Dolphin (the two-GPU
   setup: Dolphin on the 6700 XT, coder on the 3060). LM Studio multi-GPU is
   per-model load config — manual, but workable.
5. Memory provenance already tags turns with `model:<name>` — extend that to the
   coder turns so both halves stay in one shared memory.

## Constraints / lessons from this session
- Test fakes: several tests stub `chat(self, messages, tools=None, allow_fallback=...,
  allow_model_load=...)` with explicit signatures. A new `model_override` kwarg must
  be optional and those fakes updated (some use **_kwargs, some don't).
- Boot guard: `_validate_required_tool_routes` requires the startup probe to route
  to ps_run. Don't touch that path when adding the override.
- This session proved: do NOT consolidate/patch without reading the whole path
  first. Read all four coder-lane call sites and their tests before editing.

## Hardware note
- 6700 XT (12GB) Vulkan/ROCm for Dolphin; 3060 (12GB) CUDA for the coder. Both
  12GB; the 3060's CUDA path is the better coder host. Lower context to 8192 on
  both to avoid the KV-cache spill that caused the 40s pre-stream delay.
