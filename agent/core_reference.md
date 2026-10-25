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

- Function count: 409
- Opt-in compiler evidence is implemented in tool receipts, not routing: ToolLoader defaults off, acceptance enables python_syntax_evidence explicitly. Actual read/before/read-back byte checks flow through unchanged result serialization and nested changes; _tool_action_report retains python_syntax_check for independent GUI reporting. Syntax success never substitutes for saved-file run/output proof. Normal GUI startup remains off pending acceptance.
- Empty-response protocol: blocking/SSE completions with no final text or native tool calls raise EmptyModelResponseError. Core reports an incomplete request, keeps partial action reports, and does not store a blank completed turn. Empty streams no longer trigger blocking inference; the API does not cache Mina error replies. No private-reasoning fallback, EOS diagnosis, new route or personality/memory rewrite.
- Observed edit evidence: actual before/read-back byte hashes and bounded text diffs flow through `code_repair.changes`, compact inference and independent GUI reports. Specialist summaries remain unverified; text reads distinguish decoder_used from actual bom bytes (null when absent). Native explanation contract forbids turning decoder choice or a passing test into unsupported diagnosis. No new route or memory/personality edits.
- Request budgeting now honors actual per-request output caps; native follow-ups use at most1024 output tokens (respect smaller positive config), while initial chat/stream defaults remain unchanged. This fixes a deployed GUI repair saved-write/context-overflow before testing without deleting persona, schemas or current evidence. Live /process code_repair exact-output and independent alternate-values proofs passed; tool selection and specialist explanations are still not universally reliable.
- Approved `code_repair` workflow owns authorized single-file Python consult/save/run and one actual-diagnostic retry. Nested receipts remain in action reports and repair-completion checks; inference omits explicitly marked nested details, retaining final hashes/output. `file_write.expected_sha256` rejects stale edits. `expected_output` and `exit_code_only` proof scopes remain distinct; review-only still uses proposal-only specialist. Selection is model-owned.
- Specialist assessments now have validated proposal/no_change/clarify outcomes and an unverified summary surfaced in independent action reports. Native instructions no longer require a separate inspect before consulting: the specialist reads actual source; diagnostics are optional. Healthy-source negative controls must remain unchanged, and natural delegation must be measured rather than inferred from passing file tests.
- Coding specialist: `tools/coding_specialist.py` exposes an optional local read/propose-only tool, enabled for installed Qwen2.5-Coder in configuration. It checks the model is already loaded; no memory access, model loading, writes or execution. Source-hash proposals still require authorized save/run proof. `agent/inference_gate.py` serializes blocking/streaming model requests and specialist inference in the API process; independent external clients are not coordinated.
- Experimental focused repair context: disabled unless a test harness explicitly sets `focused_repair_context=True`. After model-selected inspect or a same-target Python read/write/run workflow, retain original personality, authoritative facts, permissions, current request/results and a bounded immediate conversation reference; omit optional older recall for the attempt only. Existing memory is never removed. Live acceptance remains mixed, so this is not enabled in normal GUI startup.
- Task-note provenance: new heartbeat notes contain user task requests only, not assistant narration. Existing notes are quoted and labeled historical/unverified. Native recall skips duplicate saved tool inventory while retaining preferences; current complete schemas remain available.
- Native Python repair completion: model-selected `code_execute` inspect declares the repair target without execution; explicit path check/run also establishes verification scope. After a write, require a passing saved-file run of the same SHA-256 and optional exact expected_stdout contract; syntax-only checks, wrong output and stale runs do not pass. Eight bounded native rounds, one completion checkpoint, explicit incomplete error with partial action reports. Ordinary save requests do not authorize execution; textual transport retains three rounds. Compact schema-cost serialization and concise protocol wording preserve context headroom without hiding tools.
- Independent action reports: tool-loop results feed blocking `actions` responses and opt-in streaming action events, including partial results on errors. Reports do not come from model prose and do not certify application liveness. File-write receipts carry readback/hash proof; execution failures retain explicit errors and diagnostics.
- Current native tool-loop working set: before follow-up inference, summarize only earlier completed exchanges (bounded actual status/path/hash/output, matching call IDs and explicit omission markers); omit their oversized source arguments and old narration. Preserve the latest exchange verbatim, full dispatcher results/action reports and stored observations. Tool results use compact JSON rather than Python dictionary repr. Required context still fails explicitly if it cannot fit.
- Native tool history: bounded `native_tool_exchange` note memories preserve actual assistant calls, matching results and historical requests across turns. Rehydrate at most two complete groups within 6500 characters; historical result text over 1800 characters is explicitly marked truncated. Auth arguments are excluded. Historical evidence is not proof of current execution. Failed results retain diagnostics; loop-limit errors identify executed statuses and the unexecuted pending call.
- Native tool transport: opt-in `model.native_tool_calls`, disabled by default. Blocking and streaming paths dispatch structured calls with matched call IDs; native schemas and assistant-call arguments are included in input estimates. Disposable GUI harness enables Qwen acceptance without changing production config.
- Context budgeting: before system coalescing, preserve current user/tool exchange; evict optional older retrieved history/recall before recent dialogue, then evict whole oldest turns. Reject oversized required context. UTF-8 token sizing remains approximate; payload correctness does not certify model tool selection.
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
  - honor configured model tuning and output limits; streaming uses the configured socket timeout, not a total generation deadline
  - send explicitly disabled reasoning as `reasoning_effort: none`; omission retains the backend default
  - decode backend SSE as UTF-8 even if its response omits a charset
  - reject empty/reasoning-only completions without retrying; preserve valid tool-only calls and nonempty content parts
  - resolve unfamiliar vision models from LM Studio capability metadata without synthetic inference probes
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
- Approx lines: 9832-10519
- Responsibility:
  - request processing (`process`, `process_stream`)
  - model-owned tool execution in both paths, regardless of legacy reflex settings; no automatic keyword/reflex/rescue dispatch or hardware-based rewriting of her tool and arguments
  - shared message assembly with bounded persisted user/assistant dialogue
  - separately quoted older conversation excerpts with speaker attribution and neighboring context
  - `process_stream` (the GUI path) runs emitted calls through `_run_process_tool_loop`, independent of user wording; tool markup is held back via `_tool_markup_start`
  - complete public JSON Schema tool arguments, excluding authorization credentials; no wording-based tool catalogue selection
- Critical functions:
  - `process`
  - `process_stream`
  - `_build_process_messages`
  - `_deduplicate_recall_context`: remove exact repeated recall bullets at assembly; authoritative facts and chronological dialogue retain their own roles
  - `_build_recent_conversation_messages`
  - `_build_older_conversation_context`

Maintainer comment:
Most production regressions land here. Keep changes tiny and test after each one.

### 13) Context, Formatting, and Tool Output
- Approx lines: 10520-12909
- Responsibility:
  - common memory lane for normal conversation; legacy classifiers are not execution gates
  - identity anchors without unconditional runtime-log replay; query-scoped task recall and advisory reminders
  - game telemetry scoped to its declared client surface, not user keywords
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
