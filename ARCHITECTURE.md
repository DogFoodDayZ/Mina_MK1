# Mina Architecture

## Purpose

Mina is one assistant with specialized reasoning and execution capabilities. The system is not intended to behave like two unrelated assistants. Both halves share Mina's identity, memory, plans, verified facts, and execution results.

## The Two Specialized Halves

### Conversational half: Dolphin3

Dolphin3 is the preferred conversational model when it is loaded in LM Studio. Its responsibilities are:

- Natural conversation and personality
- Understanding Travis's intent at a high level
- Recalling relevant Mina context
- Discussing options and planning work
- Explaining verified results in Mina's voice

Dolphin3 should not be expected to reliably emit structured tool calls or perform precise repository changes. The live tests showed strong conversation and context interpretation, but weaker tool selection and file-operation argument formation.

### Execution half: coding agent

The coding agent is responsible for work that requires exact operations and verification:

- Inspecting repositories and files
- Turning a plan into implementation steps
- Editing source files
- Creating artifacts
- Running tests, builds, and linters
- Checking that actions actually completed
- Reporting failures without claiming success

The coding agent is Mina's execution capability, not a replacement identity.

## Shared Memory Model

There is one authoritative Mina memory system: `memory/memory.db` with its configured indexes. Normal conversation turns already pass through `MK1Core._store_turn_memory()` and enter this shared store.

Every stored turn and explicit memory/task write is tagged with the active model, for example:

- `model:dolphin3-nsfw-gf-chat`
- `model:qwen2.5-7b-instruct`

These tags provide provenance without creating a second memory owner.

### Memory categories

1. **Working context**
   - Recent turns, current request, active plan, and nearby facts.
   - Included in the next model prompt as needed.

2. **Shared interaction memory**
   - Normal user and assistant turns.
   - Stored in Mina's existing memory engine.
   - Available to either specialized half.

3. **Durable memory**
   - Explicit facts, preferences, procedures, and task records.
   - Must come from a clear user request or a validated operation.
   - Should retain source/model tags.

Model output alone is not proof that a durable fact is true. Hallucinated claims, tool markup, and speculative conversation must not be promoted automatically.

## Per-Turn Flow

```text
User message
    |
    v
MK1Core builds shared memory and working context
    |
    v
Dolphin3 produces the conversational response
    |
    +--> coding request: hand off intent and plan to the coding agent
    |
    +--> explicit memory request: validate and write to shared MK1Memory
    |
    v
Store the interaction turn in shared memory with model provenance
    |
    v
Return the conversational result or verified execution result to Travis
```

The important distinction is between recording a conversation turn and promoting a statement into durable memory. The first is routine. The second requires intent and validation.

### Conversation and tool ownership

Normal text and GUI-stream turns always reach Mina's model. Core does not choose
tools from command keywords, run a reflex before she answers, or replace her
reply with a guessed action. Legacy routing helpers are not part of this flow.

Mina receives public JSON Schema argument definitions and can emit a fenced JSON
call with `tool` and `args`. Core dispatches that exact tool and argument object,
subject to tool permissions, and returns a named result or explicit failure.
This remains the default for models without verified native support.
The opt-in `model.native_tool_calls` setting (default false) sends native OpenAI
tool schemas instead of duplicating the catalogue in system text. The disposable
GUI harness enables it for the already-loaded Qwen model; production config is
unchanged. Both `/process` and `/process-stream` execute structured calls through
the same permission-checked dispatcher. SSE tool-call fragments are assembled by
index; assistant call IDs are retained and matched on tool-result messages.
Follow-up model calls retain the full public tool catalogue and bounded loop.
Native schema and serialized assistant-call arguments count toward the estimated
input budget. Oversized required exchanges and follow-up errors surface explicitly.
Empty or reasoning-only completions with neither final text nor native tool calls
raise an explicit incomplete-request error. Empty streams do not trigger a second
model request. Blank final replies are not stored as completed assistant turns;
completed action reports survive follow-up failure. The blocking API does not
deduplicate/cache Mina error replies. This protocol guard does not retry inference,
expose reasoning, diagnose why generation stopped, or certify explanation accuracy.
Experimental compiler receipts are disabled by default (`ToolLoader.python_syntax_evidence`).
The disposable acceptance CLI can opt in with `--python-syntax-evidence`, without
changing native tool schemas, personality, memory, routing or execution permission.
Model-selected Python reads/inspections include a bounded, non-executing compiler
check of the same bytes; writes include checks of actual before/read-back bytes.
Nested repair change receipts retain those checks in inference and GUI reports.
`tools/python_syntax.py` records SHA-256, interpreter version, exact file line/offset
and diagnostic (bounded to500 characters), with a50,000-byte input limit and explicit
not-checked status. Bytes go directly to Python's compiler, honoring Python encoding
cookies/BOM rules rather than the text preview decoder. Checks never execute code
or emit bytecode files. A synthetic compiler filename prevents Python3.14 from
rereading a rewritten file for SyntaxError.text; source_path identifies the actual
file and the SHA-256 identifies the checked snapshot. Syntax validity is not runtime/output correctness.
Do not enable this experiment in normal startup until the explanation gate passes.
Token sizing is conservative but is not the backend tokenizer.
Native dispatch stores actual assistant-call/tool-result exchanges as ordinary
`native_tool_exchange` note memories. Auth arguments are excluded, historical
result text is capped at 1800 characters with an explicit truncation marker,
and exchanges over 6500 characters are not retained (logged explicitly).
Up to two recent exchanges within a combined 6500-character budget are
rehydrated chronologically as complete, matched native message groups, preceded
by their historical request labels. These are past observations, not new
execution instructions or proof of a new action. Existing memories are not
rewritten, and assistant narration is not used to fabricate tool evidence.
Failed tool results retain diagnostics, and loop-limit errors distinguish
completed tool statuses from the next call that was not executed. Model
tool-choice/interpretation errors can still occur; stored exchanges do not
guarantee fresh execution for every wording.

Identity/profile anchors and role-labelled dialogue remain available. Historical
hardware/test/path bookkeeping is not authoritative per-turn identity. Due tasks
are reminders, not instructions to override the conversation; live game telemetry
belongs only to the declared game client surface. Tool availability is not gated
by whether the user sounds conversational or command-like.

### GUI text and voice transport

GUI text uses `/process-stream`, which forwards the declared interface name to
core, streams text chunks, and then optionally synthesizes and queues local speech
using the same helper as `/process`. A `speech` event reports synthesis and
playback-start status before `done`; the GUI preserves the text when speech fails.
Text requests can explicitly override speech for one turn; otherwise the shared
GUI speech flag applies.

Authorized native Python repairs use the existing `code_execute` tool's `path`
interface. `mode=inspect` reads actual source without execution and declares the
model-selected repair target; `mode=check` checks syntax only; `mode=run` executes
the saved file with a finite timeout and returns its path, SHA-256, exit status
and diagnostics. A changed-during-run file fails verification. After a repair
write, completion requires a passing run of the same saved revision. Explicit
saved-file checks/runs also establish verification scope if inspect was omitted.
Optional `expected_stdout` declares an exact output contract (including newlines);
exit zero with different output is `stdout_mismatch`, not a passing test. A single
completion checkpoint lets the model continue an unfinished repair; unresolved
verification returns an explicit error and retains all action reports. Native
execution is capped at eight rounds; textual execution retains three. Ordinary
save requests do not start repairs or authorize execution. This is a protocol
gate, not a keyword router or proof of arbitrary feature correctness.
To fit longer repairs into small contexts, summarize earlier completed native
exchanges only in the inference working set: retain call/result IDs, actual
status/path/hash and bounded diagnostics, with explicit omission markers.
Earlier oversized source arguments and narration are not pinned indefinitely.
The latest exchange, full dispatcher results, stored observations and independent
action reports remain intact. Actual tool-result messages use compact JSON.
The model-facing schema uses `expected_lines` and `append_newline` to avoid
literal-backslash/newline ambiguity; exact `expected_stdout` remains accepted
internally for compatibility. Writes expose `changed=false` when saved bytes
are identical, without treating an ordinary idempotent save as an error.

Focused repair working context is experimental and disabled unless an acceptance
harness sets `focused_repair_context=True`. It starts only after model-selected
Python inspection or a same-target read/write/run workflow, not prompt keywords.
It removes optional older recall from that inference attempt while preserving
personality, authoritative working-memory facts, permission rules, current request,
actual tools and bounded immediate dialogue for follow-up meaning. No memory is
deleted or rewritten. Mixed live results prohibit treating it as a general repair fix.
New task-heartbeat notes retain user requests, not unverified assistant narration;
legacy notes are quoted and caveated, and native prompts omit duplicate saved tool
inventory while retaining the actual native schemas.

`coding_specialist` is an optional, model-selected local
consultation tool configured in `coding_specialist` in the repo configuration.
It reads the actual target file and sends only its source, the agreed objective
and diagnostics to the already-loaded `qwen2.5-coder-7b-instruct`. It never opens
memory, loads/switches a model, saves files, or executes proposals. Source SHA-256,
model identity and explicit `saved=false`, `tested=false` accompany proposals.
The specialist must return a validated JSON assessment: `proposal` with complete
replacement source, `no_change` without replacement source, or `clarify` with
nonempty questions. Every result includes the model's summary, explicitly marked
`assessment_verified=false`; source inspection is not a behavior test. This
avoids requiring a repair for already-correct code. Malformed/conflicting
assessments and unchanged proposals fail explicitly, rather than masquerading
as repairs. The GUI shows the assessment and summary separately from Mina's prose.
Oversized inputs, truncated output,
invalid Python, changed source, unloaded models and HTTP errors fail explicitly.
The endpoint must be loopback HTTP; proxy inheritance and redirects are disabled.
Input is capped at about 3300 estimated tokens, output at 1024, HTTP inference
at 90 seconds; large-file/multi-file coding is not covered by this first tool.
Mina still selects tools, asks useful follow-up questions and retains conversation
memory/personality. She applies authorized proposals through `file_write` and
tests via `code_execute`. A consultation is not repair proof. Native repair
completion tracks proposals as repair targets and still requires saved-revision
execution evidence. Automatic specialist selection is not universally reliable.

`agent.inference_gate` serializes Mina-owned blocking and complete streaming
inference with specialist requests in one process. Waiting for a slot is bounded;
errors and stream closure release it. This does not coordinate other LM Studio
clients, separate Mina processes, or an upstream generation continuing after a
network timeout. Initial validation uses sequential requests; no extra GPU
placement, model reloads or simultaneous requests are introduced.

Native tool follow-ups reserve at most 1024 output tokens (a smaller configured
positive cap is respected). Message budgeting uses the actual per-request output
allowance, including overrides, rather than always reserving the configured
default. Initial chat/stream caps and required persona/tool evidence are unchanged;
oversized required exchanges still fail explicitly.

### Coding acceptance gate

`tools/coding_acceptance.py` is an opt-in CLI, not a public Mina tool or an
automatic test-suite model request. Its fixed 16-case matrix has two natural
wordings each for runtime repair, syntax repair, incorrect output, healthy review,
read/explain, missing requirements, save-without-run, and ordinary chat.
It uses the real isolated core with original personality, full tool schemas,
fresh disposable memory per case, native calls, and GUI-equivalent initial
limits (8448 context, 2048 output; ordinary bounded native follow-ups).
All model inference is sequential. The CLI refuses unloaded models, oversized
requested contexts and shorter-context comparisons; it never changes model/GPU
configuration. A comparison requires another suitable model already loaded at
the same usable context, identical cases, and the same code/configuration.
An inference timeout stops submission of subsequent cases; server-side
cancellation is not assumed merely because the client stopped waiting.

Each attempted tool call is restricted to the exact disposable target and case
permissions. Memory tools and unrelated operations are refused before dispatch;
read/review cannot write/run (source-only `code_execute(mode=inspect)` is allowed)
and save cannot execute. A refused attempt counts
as a selection failure, not a successful safety-control pass. These are test
restrictions, not an OS sandbox. Known repair fixtures get independent bounded
saved-file execution and an AST-based alternate-values check to reject hardcoded
answers. Matching actual run hashes/outputs are scored separately. A correct
manual repair counts as appropriate execution but `workflow_selected=false`;
specialist use is measured separately rather than conflated with behavior.
Save cases may idempotently prepare only the exact disposable target parent.
Only the exact fixture script is deleted after each case; DBs and reports remain.

Example (choose a fresh report path outside the repository):

```powershell
.\.venv\Scripts\python.exe .\tools\coding_acceptance.py --output "$env:TEMP\mina_coding_gate_baseline.json"
```

The report is deliberately failing/pending until explanation evidence is reviewed.
For every case, an offline review maps its ID to `verdict` (consistent, incorrect
or inconclusive), `reason`, and an exact reply `quote`. Assess claims against
actual source, actions, errors and independent observations, including missing
clarifications and invented defects. Do not use model narration or keyword
matching as proof. Apply with:

```powershell
.\.venv\Scripts\python.exe .\tools\coding_acceptance.py --output "$env:TEMP\mina_coding_gate_baseline.json" --reviews "$env:TEMP\mina_coding_gate_reviews.json"
```

This second command is offline. Every selected case must pass selection,
behavior, applicable execution evidence, operational completion and explanation
review; unreviewed/inconclusive cases never certify acceptance. `--cases` is for
targeted reproduction: its `selected_cases_passed` may be true, but `passed`
requires the complete 16-case matrix (`matrix_complete=true`). Report small
sample results as measured evidence, never general autonomous coding reliability.

For user-authorized single-file Python fixes, `code_repair` owns the sequence
instead of asking the conversational model to alternate several low-level calls.
It consults the same specialist, applies a proposal through normal write
authorization with an expected-source SHA-256 precondition, and runs the actual
saved revision. A failing non-timeout test gets at most one further consultation
using actual stdout/stderr; no weakened output assertion or synthetic fallback.
Timeouts and source-revision changes stop explicitly. Clarification results
never save or execute. A no-change assessment can still be tested when the user
authorized repair/execution. Review-only requests use `coding_specialist`, not
this mutating operation. This is not an OS sandbox or a whole-project agent.
Execution timeout is 1-30 seconds, default 5, per attempt.

The workflow returns nested real consultation/write/run receipts, including
partial saves on failure. Core action reports and the GUI expose those receipts.
Inference receives the final status/hash/output instead of repeated nested source
proposals (explicitly marked omitted); external reports retain details. Native
repair completion also checks the nested saved-revision evidence. An exact
`expected_lines` contract establishes console-output proof; absent that contract,
`verification_level=exit_code_only` must not be presented as full behavior proof.
Natural selection of this operation is still the model's responsibility, not a
keyword route. Personality and stored conversation records are not replaced.

Verified writes also carry `change_evidence` from actual pre-write/read-back
bytes: both SHA-256 hashes, a UTF-8 unified diff bounded to 3000 characters,
explicit truncation and separate final-newline flags. CRLF is normalized only
for the text comparison; hashes identify exact bytes. Non-UTF-8 prior content
has an explicit unavailable-text reason. This evidence is not a diagnosis.
`code_repair.changes` retains each attempt's evidence even when inference omits
nested receipts. Mina is instructed to explain edits from this evidence, not
the unverified coder summary; passing execution proves only the declared test.
Text reads separately report `decoder_used` and `bom` (null if absent); a
decoder supporting BOM removal is not evidence that BOM bytes were present.
Core forwards these facts to model-independent action reports and the GUI.
These are evidence contracts, not additional routing rules or persona changes.

Independent action reports are never parsed
from Mina's prose. `/process` returns `actions`; `/process-stream` requests and
forwards an `actions` SSE event separately from text, memory reply content and
TTS. Partial results survive follow-up model errors and loop-limit failures.
The GUI renders `ACTION REPORT` entries for text/image replies and voice events:
tool status, target path, process exit code and bounded diagnostic output.
An empty list explicitly means no tools ran. Completed commands are not
advertised as still-running applications. Failed Python/PowerShell execution
returns an explicit nonzero-exit error; successful file writes are read back and
compared to submitted text and include byte count, SHA-256 and first-line proof.
These reports do not certify unperformed feature requests or make every model
claim accurate; they expose the actual results independently of those claims.

API-host text/image playback and voice-client playback share an OS-owned speaker
lock via `agent/voice_playback.py`. It serializes playback and releases on process
exit, without a stale mute flag. The microphone stream is aborted while playback
is active and during a voice request, then restarted with fresh capture instead
of replaying buffered speaker audio. This is half-duplex: speech during Mina's
playback is intentionally not captured. Each synthesized reply has its own audio
path so simultaneous replies cannot overwrite one another. Only blocking players
are accepted; opening an external player cannot prove when capture may resume.

Explicit SEND>IMG uses `/process` so the GUI receives attachment-forwarding
metadata alongside the reply. Vision detection consults cached LM Studio model
capability metadata, not synthetic image inference or only model-name hints.

Microphone audio uses `/voice/process`: transcription enters the normal
`core.process` path and is tagged as voice input. This endpoint obeys the shared
GUI speech flag and returns synthesized audio for the voice client to play; it
does not also start API-host playback. Playback-start status is not proof of
audible sound. Real microphone/speaker acceptance is separate from API tests.
The completed voice turn is published to the GUI event feed before speech
synthesis begins, so text display does not wait for the audio file. Voice-client
playback still waits for synthesis; the GUI polls voice events every 1.5 seconds.

## Coding Handoff

The isolated conversation factory defaults to tools disabled. Explicit
`create_core(..., enable_tools=True)` uses the real tool loader and inherited
MK1Core tool loop while retaining disposable memory and disabling background
study, startup seeding and file-index refresh. It is not an OS sandbox: tools
retain their normal permissions and external effects. Production memory must not
be activated implicitly. Tool discovery parses for a top-level `tool_entry`
function before import, avoiding execution of unrelated maintenance scripts.

Prompt assembly removes exact repeated recall bullets, preferring authoritative
working-memory facts over copies in general recall. Short-term recall already
present in chronological dialogue is omitted from system context. Repeated
dialogue turns, distinct corrections, personality, tool schemas and retry guidance
are preserved; stored memory records are not modified. This is deduplication, not
a new semantic relevance filter.

The first coding handoff carries:

- User goal
- Relevant agreed requirements and actual diagnostics (not the entire chat database)
- Target project or path
- Constraints and acceptance criteria
- Current source read directly from the target file

The coding specialist returns:

- Proposed replacement source or clarification questions
- Source revision SHA-256 and model identity
- Explicit not-saved/not-tested status, or an actionable failure

Mina uses her existing authorized tools to apply and test proposals, then
explains their actual results without inventing completion evidence.

A prompt such as `I want to design a desktop surprise timer before we write code` is planning and must not write a file. A prompt such as `Create a basic Python script called spice.py` is artifact intent and may proceed when the path and content requirements are sufficiently clear.

## Model Switching

The GUI's unified AI Mode control switches between Mina Core and Dolphin3. The API model-selection path:

1. Validates the target against `model.switch_allowed`.
2. Unloads currently loaded LM Studio instances.
3. Sends the required LM Studio load payload: `{ "model": "..." }`.
4. Waits for slow model loads using the configured model timeout.
5. Updates runtime state only after a successful load.
6. Returns an honest failure when LM Studio rejects or times out.

This unload-before-load behavior prevents duplicate instances such as `qwen2.5-7b-instruct:2`.

## Standalone Tray Startup

`Start Mina Tray.cmd` launches `mina_tray_launcher.py` from the repository venv. The tray launcher owns the two runtime processes separately:

1. It starts the API through `mk1_api.ps1 start -NoGuiDev`.
2. When `load_fancy_gui` is enabled and port `1420` is free, it starts `pnpm tauri dev` from `apps/mina-gui` in a persistent PowerShell window.
3. If port `1420` is already open, it does not start another GUI instance.
4. API startup is verified through port `8000`; shutdown uses the safe shutdown path and verifies that the API stopped.

Keeping the GUI window persistent makes startup errors visible instead of allowing a temporary PowerShell window to flash and disappear. The tray configuration is stored at `%APPDATA%\\Mina_MK1\\tray_launcher_config.json` and includes API/GUI ports, voice settings, GUI loading, and GUI speech options.

## Ostranauts Data

Ostranauts memory remains historical Mina context and is not being rebuilt or cleaned as part of the conversational split. Live telemetry is used for truth verification when a question depends on current game state. It is not a separate assistant memory system.

## Temporary Isolation Harness

`memory/safe_memory_proxy.py` and the `/alt_model/memory_session/*` endpoints remain available as a test harness for proving write isolation. They are not the intended normal Dolphin3 memory path.

The harness provides:

- Read-through access to Mina personality, status, tasks, and context
- JSONL capture for isolated test writes
- Explicit migration and clear operations

Do not treat the JSONL session as Mina's second permanent memory. The target architecture is one shared memory system with validation and provenance.

## Current Verified State

- Dolphin3 loads successfully through the LM Studio model API.
- LM Studio model loading uses the `model` request field.
- Existing model instances are unloaded before a new model is loaded.
- Exactly one intended model instance can be left loaded.
- Dolphin3 can read Mina personality and relevant memory context.
- Normal turns use the shared Mina memory engine.
- Shared turn-memory tests pass with model provenance support.
- SafeMemoryProxy isolation tests pass without polluting Mina's core memory.
- The GUI builds successfully.

## Known Follow-Up Work

- Add a first-class coding-agent handoff instead of relying on the conversational model to emit tool syntax.
- Improve natural-language routing for planning versus execution.
- Resolve file-name and project-path inference for requests such as `read core.py`.
- Map natural reminder language to the actual task-memory route.
- Make web-search intent use the supplied topic or URL without asking for an unrelated path.
- Keep truth-first handling for Ostranauts responses without rewriting existing historical memory.
