# MK1 Core Reference Map

Maintainer note:
This file is a human-readable map of `agent/core.py`.
Use it before making changes so you touch the correct zone first.

## Purpose

`core.py` is the runtime orchestrator for Mina. It handles:
- model IO
- intent detection
- tool routing and execution
- memory write/read behavior
- shutdown safety
- autonomous idle work
- project tracker and scaffolding helpers

Maintainer rule:
Memory is Mina. Do not replace normal learned behavior with hardcoded chat or routing shortcuts. Use hardcoded logic only for stable protocol and safety invariants.

## File Scale Snapshot

- Function count: 368
- Main classes:
  - `LMStudioClient`
  - `MK1Core`
- High-risk paths:
  - tool routing and execution
  - memory mutation
  - shutdown and process control
  - autonomous patch application

## Section Map

### 1) LM Studio Client Layer
- Approx lines: 1-758
- Responsibility:
  - build/submit chat payloads
  - handle model fallback and role normalization
  - stream responses
- Critical functions:
  - `chat`
  - `chat_stream`
  - `_flatten_system_messages`
  - `_coalesce_system_messages`

Maintainer comment:
Do not change message role flattening without testing both standard and strict templates.

### 2) Core Boot and Runtime State
- Approx lines: 759-1340
- Responsibility:
  - initialize memory/tools/model/config
  - seed startup memory facts
  - load identity/runtime/search mode state
- Critical functions:
  - `__init__`
  - `_load_runtime_state`
  - `_save_runtime_state`
  - `_load_identity_state`

Maintainer comment:
Initialization order matters. Avoid moving memory/tool/model init sequence.

### 3) Identity, Trust, and Safety Gates
- Approx lines: 1341-1790
- Responsibility:
  - verify trusted clients and voice passphrase
  - gate high-risk actions
- Critical functions:
  - `_is_high_risk_tool`
  - `_identity_gate_decision`
  - `_is_voice_phrase_match`

Maintainer comment:
Any gate relaxation must include tests and explicit rationale in TODO issue template.

### 4) Intent Refinement and Alias Learning
- Approx lines: 1890-2141
- Responsibility:
  - normalize noisy user language
  - learn and resolve reusable intent aliases
- Critical functions:
  - `_normalize_user_intent_text`
  - `_maybe_learn_intent_alias`
  - `_resolve_intent_alias`

Maintainer comment:
Alias learning can create drift. Keep allow-list narrow and explicit.

### 5) Pending Actions and Reflex Layer
- Approx lines: 2168-2406
- Responsibility:
  - pending write/directory confirmations
  - directory/write/identity reflex handlers
- Critical functions:
  - `_set_pending_write_authorization`
  - `_handle_directory_scan_reflex`
  - `_handle_external_write_reflex`

Maintainer comment:
This is a common churn zone. Keep confirmation logic deterministic.

### 6) Turn Memory and Response Shaping
- Approx lines: 2443-3880
- Responsibility:
  - store interaction memory
  - sanitize assistant text
  - grounded tool response rendering
  - memory question answering
- Critical functions:
  - `_store_turn_memory`
  - `_render_tool_response_as_mina`
  - `_build_memory_read_reply`

Maintainer comment:
Functionality first: if tool output exists, prefer truthful output over stylistic generation.

### 7) Project/Test/Path Utilities
- Approx lines: 3882-4148
- Responsibility:
  - infer project paths
  - run tests
  - infer recent workspace context
- Critical functions:
  - `_run_project_tests`
  - `_attempt_basic_test_autofix`

### 8) Autonomous Work Engine
- Approx lines: 4151-4712
- Responsibility:
  - discover backlog
  - run bounded autonomous tasks
  - validate/apply guarded patches
- Critical functions:
  - `_autonomous_work_candidates`
  - `_run_autonomous_work_task`
  - `_validate_autonomous_patch_paths`
  - `_apply_autonomous_patch`

Maintainer comment:
Never weaken patch path validation without replacement controls.

### 9) Project Tracker and Bootstrap
- Approx lines: 4713-5115
- Responsibility:
  - track project state and next steps
  - bootstrap projects from user requests
- Critical functions:
  - `_sync_project_tracker`
  - `_bootstrap_project`
  - `_scan_projects`

### 10) Lifecycle, Shutdown, and Heartbeat
- Approx lines: 5116-5868
- Responsibility:
  - background heartbeat
  - safe shutdown and memory drain
  - idle checkin behavior
- Critical functions:
  - `_start_turn_clock_heartbeat`
  - `close`
  - `_prepare_safe_shutdown`
  - `_request_safe_shutdown`

Maintainer comment:
Shutdown changes must prove memory durability is preserved.

### 11) Tool Inventory, Diagnostics, Git Ops
- Approx lines: 5869-7069
- Responsibility:
  - tool self-tests
  - runtime status endpoints
  - git helpers
  - alias map for paths
- Critical functions:
  - `_run_tool_self_test`
  - `_git_snapshot`
  - `_git_push`

### 12) Main Request Pipelines
- Approx lines: 7155-7664
- Responsibility:
  - request processing (`process`, `process_stream`)
  - reflex entry and orchestration
- Critical functions:
  - `process`
  - `process_stream`

Maintainer comment:
Most production regressions land here. Keep changes tiny and test after each one.

### 13) Context, Formatting, and Tool Output
- Approx lines: 7665-9344
- Responsibility:
  - command-like detection
  - context assembly
  - result formatting
  - memorying tool outputs
- Critical functions:
  - `_is_command_like_query`
  - `build_context`
  - `_format_tool_result`

### 14) Intent Router and Planner
- Approx lines: 9345-11730
- Responsibility:
  - detect tool intent from natural language
  - parse payloads
  - planner-assisted tool selection
  - final response handling
- Critical functions:
  - `detect_tool_intent`
  - `_detect_file_directory_intent`
  - `_plan_tool_action`
  - `_handle_model_response`
  - `run_tool`

Maintainer comment:
Any routing changes require transcript-grounded regression prompts in `TODO.md`.
For move/rename routing, require explicit `src` and `dst` paths before returning `file_move`.

## Known Churn and Risk Zones

- Tool-execution truthfulness:
  - `_render_tool_response_as_mina`
  - `_handle_model_response`
- Command routing:
  - `detect_tool_intent`
  - `_is_command_like_query`
- Memory side effects:
  - `_store_turn_memory`
  - `_remember_tool_output`
- Autonomy patch safety:
  - `_validate_autonomous_patch_paths`
  - `_apply_autonomous_patch`

## Safe Editing Rules (Quick)

- Keep edits localized to one zone at a time.
- Add or update tests in same PR/change set.
- Verify action paths with real prompts, not only synthetic unit names.
- Prefer deterministic behavior over creative response generation for operational commands.

## Update Routine

When `core.py` changes:
1. Refresh `tmp/core_functions.txt`.
2. Update section notes here if responsibilities moved.
3. Update `agent/core_index.json` risk and ownership metadata.
4. Update `agent/core_self_fix_playbook.md` if process rules changed.
