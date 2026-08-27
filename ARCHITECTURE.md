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

## Coding Handoff

A future coding handoff should carry a structured request containing:

- User goal
- Relevant conversation context
- Target project or path
- Constraints and acceptance criteria
- Current plan
- Required authorization, when applicable

The coding agent returns:

- Files changed or created
- Commands/tests run
- Verification evidence
- Any unresolved blocker

Mina then explains that result without inventing completion evidence.

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
