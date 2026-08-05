# Mina MK1 Session Contract

Apply these instructions for all work in this repository.

## Scope

- Treat `E:\Mina_MK1` as the authoritative project unless the user explicitly says otherwise.
- Prefer repo-local paths over legacy `E:\workspace` paths unless the user explicitly targets `E:\workspace`.
- Keep Mina's personality intact, but keep replies concise by default.

## Product Contract

- Natural language first. Do not require magic phrasing.
- No hidden mode switches from keyword traps.
- If required arguments are missing, ask one targeted clarification question and stop.
- Build or generate artifact intent outranks unrelated direct file-operation interpretations.
- Do not claim actions happened unless verified by tool output in the same turn.

## File And Code Tasks

- For create/save requests, route directly to the concrete artifact path when the prompt provides one.
- Do not invent helper directories like `test_work` unless the user asked for them.
- Reuse the current draft artifact on save follow-up requests such as `save the script` or `save it as foo.py`.
- Do not replace a drafted artifact with a generic stub or stale artifact.
- For repo-local writes, treat `E:\Mina_MK1` as an allowed local workspace root.

## Verification Contract

- After any mutating action, verify completion and show proof.
- For file creation/writes, confirm:
  - final path
  - file exists
  - first line or a short content proof
- If verification fails, report failure plainly, retry once with corrected arguments when possible, then ask one precise question.

## Core.py Change Contract

- For routing or execution fixes, add at least one transcript-grounded regression prompt.
- Record the fix in `TODO.md` using the issue template:
  - root cause
  - minimal fix
  - implementation choice: memory or hardcoded
  - verification status
- After editing `agent/core.py`, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\refresh_core_reference.ps1
```

- If the refresh changes metadata, sync:
  - `agent/core_reference.md`
  - `agent/core_index.json`
- If fix policy or regression gates changed, update `agent/core_self_fix_playbook.md`.

## Implementation Choice Rule

- Use memory-driven behavior for user habits, evolving aliases, and learned workflow context.
- Use hardcoded behavior only for deterministic routing rules, safety constraints, protocol contracts, and stable system invariants.

## Live Validation Rule

- For runtime routing or execution fixes, prefer a live API proof through `/process` or the repo scripts when feasible, not only unit tests.

## TODO Contract

### 1. Non-Negotiable Product Contract

- Natural language first.
- No magic phrasing required.
- No hidden mode switches from keyword traps.
- If uncertain, ask one specific clarification question.
- If action is taken, verify completion and show proof.

### 2. Core Failure We Are Preventing

- User asks for outcome.
- Router maps to wrong tool class.
- Tool runs with wrong or missing arguments.
- System reports a technical status while user goal is unmet.
- Patch adds another branch, moving failure elsewhere.

Pass condition:

- Same prompt class succeeds across wording variants.
- Wrong-route rate is measurably reduced.
- No regression on prior successful prompts.

### 3. Intent Class Hierarchy

Priority order:

- Conversational chat.
- Build or generate artifact.
- Direct file operation.
- External web or API operation.
- Debug or tool introspection.

Rule:

- Build or generate artifact always outranks direct file move or rename unless source and destination are explicitly provided.
- Tool inventory is never shown unless explicitly requested with a debug intent.

### 4. Action Gating Rules

Before execution, produce an internal action plan with:

- task class
- selected tool
- required arguments
- confidence score

Rules:

- If required arguments are missing, do not execute.
- Ask one targeted clarification with the exact missing field.
- On low confidence, ask clarification instead of guessing.
- Never fallback to unrelated scripted prompts.

### 5. Verification Contract

For file creation:

- Confirm file exists.
- Confirm path.
- Confirm first line or content hash.

For code generation:

- Confirm artifact file path.
- Confirm language and entry point.
- Confirm runnable status or explain missing dependency.

For move or rename:

- Confirm source existed before.
- Confirm destination exists after.

If verification fails:

- Report failure plainly.
- Retry once with corrected arguments.
- If still failing, ask one precise question.

### 6. Conversation Behavior Contract

- Keep Mina personality and tone.
- Keep responses concise by default.
- Do not leak internal tool routing unless user asks.
- Do not output canned disambiguation scripts unless explicit ambiguity is real and material.
- Do not derail user flow with unrelated domain choices.

### 7. Regression Suite Rule

- Use transcript-grounded prompts, not synthetic labels.
- No fix is accepted without at least one real transcript prompt added to regression tests.

### 8. Change Control Protocol

- No branch patch accepted without adding a matching regression prompt.
- Every fix must include:
  - root cause
  - why previous logic failed
  - why this fix cannot reroute neighboring intents
- If a fix increases conditional branching in one area, require compensating simplification elsewhere.
- Reject fixes that only silence surface errors.

### 9. Definition Of Done

- Passes all transcript-grounded regression prompts.
- Preserves Mina tone and concise style.
- Shows verification evidence for actions.
- No new scripted derailment in adjacent prompt classes.
- User can use normal phrasing and still complete the task.

### 10. Implementation Choice Check

- Explicitly decide and document whether a change should be memory-driven or hardcoded behavior.
- Prefer memory-first when behavior depends on user habits, evolving aliases, learned file context, or changing workflow patterns.
- Hardcode only when behavior is a stable safety constraint, protocol contract, or deterministic system invariant.

Required in each fix note:

- chosen approach: memory or hardcoded
- why that approach fits this specific change
- what risk is introduced if the wrong approach is chosen
- regression prompt proving the choice works in natural phrasing

### 11. Issue Template Rule

For each new problem, record in `TODO.md`:

- what the user asked Mina to do
- what Mina actually did
- what should have happened
- evidence
- failure class
- root cause
- minimal fix
- regression prompts
- implementation choice
- done criteria
