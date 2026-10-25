# Core Self-Fix Playbook

Maintainer note:
This playbook governs how Mina should perform safe self-repairs in `core.py`.
Use this before applying any autonomous or semi-autonomous change.

## Priority Contract

1. Function first.
2. Truthful execution proof second.
3. Memory learning third.
4. Personality after successful execution reporting.

## Non-Negotiable Rules

- Memory is Mina. Learned memory and user context are the default source of truth for normal behavior.
- Do not bypass Mina's memory with hardcoded chat or routing rules for ordinary prompts.
- Do not claim an action was run unless tool output for that action exists in the same turn.
- If a required argument is missing, ask one targeted question and stop.
- If user confirms run now, execute now. Do not re-explain.
- Verification evidence is required for every mutating action.
- Coding specialist proposals are not saves/tests. Cover actual file reading, missing goal/path, bounded inputs, unloaded model refusal without auto-load, local-only nonredirecting transport, malformed/truncated output, source revision races and clarification results. Native completion must still require a passing saved-file run after applying a proposal. Verify a real two-model handoff, not just correct code from Mina's own model.
- Serialize blocking and streaming inference with specialist requests in the same API process. Test that an open stream holds the slot and that failure/closure releases it. Do not claim coordination with independent LM Studio clients or model cancellation after a network timeout.
- Empty-response acceptance must reject absent/null/whitespace or reasoning-only completions without a native tool call on both transports. Do not retry an empty stream or expose private reasoning as a final answer. Preserve real tool calls, nonempty replies and partial action reports; do not store blank completed turns or cache error replies. Use the transcript read/explain failure and a captured response replay, not another unbounded model probe. This protocol guard is not a diagnosis-reliability fix.
- Experimental compiler evidence stays disabled by default in ToolLoader; enable only for disposable acceptance. Preserve model-owned selection/schemas and execution permissions. Test exact read/before/read-back byte hashes, original file line numbers, CRLF/BOM/encoding-cookie handling, bounded input/diagnostics and explicit not-checked failures. Compile only, never execute returned code or emit pyc. A read can succeed while syntax is invalid; syntax success cannot establish runtime/output correctness. Retain facts through native inference, partial repair changes, action reports and GUI rendering; require full explanation-gate review before normal enablement.
- Authorized single-file Python repairs with permission to edit AND run should use model-selected `code_repair`, which owns consult/save/test with one diagnostic-driven retry. Review-only or edit-without-execution permission uses proposal-only `coding_specialist`. Test authorization before consultation, source-hash preconditions, actual saved bytes, wrong output, source changes during tests, timeout stop, questions without writes, no-change results and partial failure receipts. No OS sandbox is implied; do not use this workflow for persistent/interactive programs.
- Actual diagnostics are optional, not a reason to re-ask already supplied requirements. Preserve specialist no_change/clarify/proposal distinctions; never treat its summary as a verified diagnosis. Test healthy-code negative controls and natural repair wording variants, measuring delegation separately from actual behavior. Direct `code_execute(mode="inspect")` still declares a repair target without execution. Ordinary save/generate requests must not activate repair or gain execution permission. After a repair write, require a passing `mode="run"` result for the identical SHA-256 revision, including nested workflow receipts. Syntax-only checks, other files, old revisions, inline substitute snippets, timeouts, and later rewrites do not establish completion.
- Explicit saved-file check/run calls also establish verification scope for that file, including writes earlier in the same turn; skipping inspect cannot bypass a failing run. When console output is known, declare exact expected_stdout including newlines. Test extra/missing newlines: exit zero is insufficient, and the initial declared output cannot be weakened by later inspect/run calls within the turn.
- Advertise line-oriented output assertions as expected_lines plus append_newline, avoiding model confusion between a literal backslash-n and an actual newline in native JSON. Keep exact expected_stdout accepted internally for compatibility; never silently reinterpret raw strings. Idempotent writes remain successful saves but must expose changed=false and explicitly state that no code change was applied.
- Experimental focused repair working context requires explicit `focused_repair_context=True` in acceptance; do not enable normal startup until live reliability is established. Preserve personality, authoritative facts, permissions, current tool exchange and immediate follow-up meaning. Test that chat, simple inspection and ordinary save do not activate it from wording alone.
- Task heartbeat notes must not promote unverified assistant replies into asserted task state. Keep new notes as user requests; quote/caveat legacy notes rather than rewriting them. Tool capability inventory stored in memory must not duplicate native schemas in the assembled prompt.
- Repair completion gets at most one evidence checkpoint per turn. Native calls have eight bounded rounds for inspect/reproduce/edit/retest; textual transport retains three. An unverified repaired file raises an explicit incomplete-verification error with partial action reports, never accepts a repeated success draft. Preserve authorization, intended behavior, original personality, memory budgets and model choice of tools. Persistent/interactive programs may remain explicitly unverified; never remove their behavior to force exit zero.
- On longer native repairs, summarize earlier completed tool exchanges in the inference working set only, with explicit source/output omission markers and matching call IDs. Keep the latest exchange verbatim and full dispatcher results/action reports intact; store actual observations before any working-set compaction. Test a measurable context threshold, preservation of latest diagnostics, and unchanged stored evidence. Serialize actual tool results as compact JSON, not Python repr.
- Action-report regressions must cover model prose falsely claiming success, failed execution after successful write, no-tool turns, and partial results surviving model failure. Reports must come from dispatcher results, not language parsing; a zero-exit wrapper is not application-liveness proof. Keep reports separate from speech and stored assistant dialogue.
- Ground explanation acceptance in actual edit diffs and byte facts, not unverified specialist summaries. Cover unchanged indentation in a missing-colon repair, matching before/read-back hashes, bounded/truncated diffs, unavailable non-UTF-8 text, final-newline differences, all retry attempts surviving nested-receipt omission, and GUI transport. Read regressions must distinguish decoder_used from actual BOM signatures (UTF-32 before UTF-16 matching, null for plain/empty UTF-8). Preserve natural replies; do not replace explanations with keyword templates. Review live exact replies independently from correct saved-file behavior.
- Read/run follow-up acceptance must verify the observed filename/path survives across turns and that execution has a real tool result. A remembered receipt or model-printed OUTPUT block is not execution proof. Preserve failure stderr/exit codes; do not increase loop limits to conceal lost context.
- Preserve native tool history only as complete call/result groups with matching IDs and historical request labels. Keep observation storage bounded, mark truncated results, exclude auth arguments, and never replay stored tool calls as new execution. Test recreation and live follow-ups against actual diagnostics.
- Native-tool transport acceptance must cover fragmented SSE arguments, matching assistant/result call IDs, full public schemas without auth fields, schema-aware budgets, blocking and streaming execution, and a live GUI read with an accurate content-based reply. Enable only for a verified model; preserve the textual default for unverified models. Do not infer execution from narration.
- Context-budget regressions must preserve the latest user request with all its tool results. Before coalescing system messages, evict optional retrieved history/recall before recent dialogue, then remove whole oldest turns rather than individual messages. Reject an oversized required exchange explicitly. Never solve overflow by dropping tool contracts or rewriting personality/memory. Passing payload tests does not prove that the model executed a tool: live acceptance must verify execution and an accurate result-based answer.
- Budget against the actual request output cap, including overrides. Native follow-up requests cap output at1024 while respecting smaller configured positive caps; initial chat/stream caps remain unchanged. Reproduce the deployed GUI saved-write-before-test overflow and verify both payload allowance and preservation of persona/schemas/current evidence, not just a fresh smaller-output standalone run.
- Run the opt-in `tools/coding_acceptance.py` matrix to measure natural selection, independent disk behavior and evidence-based explanations separately. Use fresh disposable memory, full persona/schemas and GUI-equivalent limits, sequential inference, exact-path permissions and changed-input checks. Count rejected unauthorized attempts as failures. A correct manual repair is not a broken program just because it skipped the preferred workflow. Keep raw results; require exact-quote offline review before accepting explanations. Do not silently load/switch a comparison model or shrink its context/persona to make comparison fit.

## Allowed Self-Fix Scope

Allowed without human approval:
- Routing bug fixes inside intent parsing and dispatch.
- Response contract fixes that improve truthfulness and verification.
- Test additions and updates for transcript-grounded prompts.
- Non-destructive logging and observability improvements.

Requires explicit human approval:
- Any destructive file operation logic changes.
- Identity/safety gate relaxation.
- Autonomous patch application rule changes.
- Memory retention or deletion policy changes.

Prompt deduplication must operate on the assembled request, not stored memories.
Keep chronological repeated turns, corrections, authoritative anchors, personality,
and complete public tool schemas. Regression tests must check both removal of
duplicate recall and preservation of distinct facts and retry guidance.

## High-Risk Zones

- Main pipeline:
  - `process`
  - `process_stream`
- Router:
  - `detect_tool_intent`
  - `_plan_tool_action`
- Tool response rendering:
  - `_render_tool_response_as_mina`
  - `_handle_model_response`
- Safety/identity:
  - `_identity_gate_decision`
- Shutdown:
  - `_prepare_safe_shutdown`
  - `_request_safe_shutdown`

## Required Commenting Standard for Future Changes

For every changed block in `core.py`, add a short comment directly above it:
- `# Why:` one line root cause
- `# Guard:` one line describing what regression this prevents

Example:
- `# Why: user said run now but previous flow re-explained instead of executing`
- `# Guard: forces execution branch when explicit confirmation is present`

Keep comments short and practical. Do not narrate obvious code behavior.

## Execution Proof Format

For command-like tools, return:
- tool name
- command/script executed
- exit code
- stdout summary
- stderr summary when present

## Memory Learning Loop

After failures, write compact records containing:
- user phrase
- expected tool
- selected tool
- fix applied
- outcome

Use these records to improve routing confidence for repeated phrases.

## Regression Gate (Must Pass)

At minimum, these prompt classes must pass before considering a self-fix complete:
- create a text file named notes.txt with hello world
- mina close edge webinterface
- use the correct tool to close edge leave spotify running
- please run the command
- yes
- yes run the script countdown.py (must return real tool-backed output, never roleplay terminal transcripts)

## Conversation Continuity Validation

- For message-assembly changes, run `tests/test_conversation_continuity.py` with disposable SQLite storage and a recording model.
- Preserve chronological user/assistant roles, the latest request, bounded history, and exclusion of tool bookkeeping; do not turn prior assistant replies into system instructions or automatically execute them.
- The isolated harness must block normal runtime startup, network access, background workers, and tool execution. Do not use live memory or repeated live prompts as test data.
- Passing these checks proves message assembly and object-recreation continuity only. Real-model repetition checks and a full isolated process restart remain separate acceptance steps.
- Older-recall changes must also preserve speaker attribution, neighboring context and chronology after details leave recent history; test later corrections, recent/older deduplication, bounded output, and read-only neighborhood access without executing quoted tool examples.

## Tool Ownership Validation

- Mina decides tool use in `process` and `process_stream`, regardless of legacy reflex settings. Neither path may call the keyword router, pre-model reflex, or post-reply rescue. Legacy helper tests are not evidence that those helpers belong in normal conversation.
- Run `tests/test_model_decides_tools.py`, `tests/test_process_stream_tools.py`, and `tests/test_conversation_tool_contract.py` for changes to the process tool loop or prompt. They must prove both paths preserve her choice, do not rewrite tool names/arguments for hardware wording, and return tool failures rather than substituting actions.
- Advertise complete public argument schemas (required fields, types, defaults and constraints); never hide tools behind greeting/action keyword gates or expose authorization credentials as model arguments.
- Keep identity/profile anchors and chronological conversation. Do not promote old hardware/test/path bookkeeping to authoritative identity, replace the current request with a due task, or inject game telemetry into another client surface.
- For bounded live acceptance, use separate storage, an already loaded model with explicit reasoning control, capped requests, and a confined read-only executor. Never overlap inference probes or write test turns to production memory.
- Do not reconnect automatic pre/post-model routing without Travis's explicit approval.

## Rollback Trigger

Rollback or halt if any of these occur:
- action claimed without tool output
- wrong tool selected on known regression prompts
- repeat-prompt frustration pattern appears again

## Fast Triage Workflow

1. Reproduce with transcript-grounded prompt.
2. Identify exact failed branch in router or response layer.
3. Apply minimal localized fix.
4. Re-run regression prompts.
5. Record result and rationale in TODO issue template.
