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

- Do not claim an action was run unless tool output for that action exists in the same turn.
- If a required argument is missing, ask one targeted question and stop.
- If user confirms run now, execute now. Do not re-explain.
- Verification evidence is required for every mutating action.

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
