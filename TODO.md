
Stop The Churn Spec v1
Owner intent: Mina must work for human phrasing first, not for internal intent labels.

1) Non-Negotiable Product Contract

Natural language first.
No magic phrasing required.
No hidden mode switches from keyword traps.
If uncertain, ask specific clarification questions.
If action is taken, verify completion and show proof.
2) Core Failure We Are Preventing

User asks for outcome.
Router maps to wrong tool class.
Tool runs with wrong or missing arguments.
System reports a technical status while user goal is unmet.
Patch adds another branch, moving failure elsewhere.
Pass condition:

Same prompt class succeeds across wording variants.
Wrong-route rate is measurably reduced.
No regression on prior successful prompts.
3) Intent Class Hierarchy (Priority Order)

Conversational chat.
Build or generate artifact.
Direct file operation.
External web or API operation.
Debug or tool introspection.
Rule:

Build or generate artifact always outranks direct file move or rename unless source and destination are explicitly provided.
Tool inventory is never shown unless explicitly requested with a debug intent.
4) Action Gating Rules

Before execution, produce an internal action plan with:
Task class.
Selected tool.
Required arguments.
Confidence score.
If required arguments are missing, do not execute.
Ask one targeted clarification with the exact missing field.
On low confidence, ask clarification instead of guessing.
Never fallback to unrelated scripted prompts.
5) Verification Contract (Must Run After Any Action)

For file creation:
Confirm file exists.
Confirm path.
Confirm first line or content hash.
For code generation:
Confirm artifact file path.
Confirm language and entry point.
Confirm runnable status or explain missing dependency.
For move or rename:
Confirm source existed before.
Confirm destination exists after.
If verification fails:
Report failure plainly.
Retry once with corrected arguments.
If still failing, ask one precise question.
6) Conversation Behavior Contract

Keep Mina personality and tone.
Keep responses concise by default.
Do not leak internal tool routing unless user asks.
Do not output canned disambiguation scripts unless explicit ambiguity is real and material.
Do not derail user flow with unrelated domain choices.
7) Regression Suite Based On Real Prompts
Use transcript-grounded prompts, not synthetic labels.

Required prompts that must pass:

Create a text file named notes.txt with hello world.
Create a program that can rename files with a graphical gui save it.
Mina read this directory E:\Mina_MK1\integrations\ostranauts.
Give me context on it.
That whole directory is so we can play a game together would you like that?
Expected outcomes:

Correct task class.
Correct tool class.
Verified completion evidence.
No unrelated scripted branch.
8) Change Control Protocol

No branch patch accepted without adding a matching regression prompt.
Every fix must include:
Root cause.
Why previous logic failed.
Why this fix cannot reroute neighboring intents.
If a fix increases conditional branching in one area:
Require compensating simplification elsewhere.
Reject fixes that only silence surface errors.
9) Health Metrics (Track Weekly)

First-attempt success rate on real prompts.
Wrong-tool execution rate.
Clarification necessity rate.
Verification failure rate.
Repeat-prompt rate by user.
User override or frustration events.
Targets:

First-attempt success above 90 percent for top 20 prompt classes.
Wrong-tool rate below 3 percent.
Repeat-prompt rate below 10 percent.
10) Definition Of Done For Any Future Fix

Passes all transcript-grounded regression prompts.
Preserves Mina tone and concise style.
Shows verification evidence for actions.
No new scripted derailment in adjacent prompt classes.
User can use normal phrasing and still complete the task.
If a fix creates a barrier to learning — intercepting requests before Mina can reason, injecting data instead of letting her discover it, or bypassing her model — find a different fix.

11) Implementation Choice Check (Memory vs Hardcoding)

For any change or improvement, explicitly decide and document whether it should be memory-driven or hardcoded behavior.
Preference order:

Memory-first when behavior depends on user habits, evolving aliases, learned file context, or changing workflow patterns.
Hardcode only when behavior is a stable safety constraint, protocol contract, or deterministic system invariant.

Required in each fix note:

Chosen approach: memory or hardcoded.
Why that approach fits this specific change.
What risk is introduced if the wrong approach is chosen.
Regression prompt proving the choice works in natural phrasing.

## Quick Issue Tracker Template (Use This First)

Copy this block for each new problem and fill it in.

---

### Issue: <short title>

- Date:
- Reporter:
- Priority: High / Medium / Low
- Status: Open / In Progress / Blocked / Done

#### 1) What I asked Mina to do
- Exact prompt(s):

#### 2) What Mina actually did
- Actual response:
- Actual action taken:

#### 3) What should have happened
- Expected response:
- Expected action/result:

#### 4) Evidence
- Transcript snippet:
- File path(s) affected:
- Error text (exact):

#### 5) Failure class (pick one)
- Intent routing wrong
- Tool selected wrong
- Args extraction wrong
- Verification missing
- Persona/response contract broken

#### 6) Root cause (after investigation)
-

#### 7) Minimal fix
-

#### 8) Regression prompts (must pass)
- Prompt 1:
- Prompt 2:
- Prompt 3:

#### 9) Done criteria
- [ ] Correct tool class selected
- [ ] Correct args built
- [ ] Action verified with proof
- [ ] No regression on prompts above
- [ ] Mina tone/conciseness still correct

---

### Issue: Bare file read phrase falls to clarification after restart

- Date: 2026-07-26
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - TRAVIS read core.py

#### 2) What Mina actually did
- Actual response:
  - MINA I can run that with tools, but I need one detail first: what exact target should I act on (path, URL, command, or app)?
- Actual action taken:
  - Asked generic clarification instead of routing to file read.

#### 3) What should have happened
- Expected response:
  - Route directly to file read for the provided filename.
- Expected action/result:
  - Resolve and read core.py using file path resolution/index fallback.

#### 4) Evidence
- Transcript snippet:
  - TRAVIS read core.py
  - MINA I can run that with tools, but I need one detail first: what exact target should I act on (path, URL, command, or app)?
- File path(s) affected:
  - E:\Mina_MK1\agent\core.py
- Error text (exact):
  - None (intent routing miss to clarify path)

#### 5) Failure class (pick one)
- Intent routing wrong

#### 6) Root cause (after investigation)
- File-read routing required phrasing like "read file ..." and did not match natural bare-file phrasing like "read core.py", so request fell through to generic clarification flow.

#### 7) Minimal fix
- Added a deterministic bare-file read matcher in `MK1Core._detect_file_directory_intent` for `read/open/show/view/cat <name.ext>` and route to `file_read`.

#### 8) Regression prompts (must pass)
- Prompt 1: read core.py
- Prompt 2: open agent/core.py
- Prompt 3: show ./TODO.md

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: Intent parsing for explicit verb + filename is a stable deterministic routing rule and should not depend on volatile memory state.
- Risk if wrong approach chosen: memory-only handling can drift or miss cold-start cases, causing fallback clarifications for straightforward file-read prompts.
- Regression prompt proving choice: `read core.py` must map to `file_read` after restart without extra clarification.

#### 10) Done criteria
- [x] Correct tool class selected in code path (`file_read`)
- [x] Correct args built (`path=core.py`)
- [x] Action verified with proof via live API after restart
- [x] No regression on prompts above
- [x] Mina tone/conciseness still correct

---

### Issue: Traceback paste reads file but does not repair broken Python source

- Date: 2026-07-26
- Reporter: Travis
- Priority: High
- Status: In Progress

#### 1) What I asked Mina to do
- Exact prompt(s):
  - TRAVIS TRAVIS it errors out PS E:\workspace\file_renamer> python app.py ... SyntaxError: unterminated string literal ...

#### 2) What Mina actually did
- Actual response:
  - MINA returned file content beginning with escaped source line text (example: import os\nimport tkinter ...)
- Actual action taken:
  - Parsed traceback path and read file, but did not perform a fix pass.

#### 3) What should have happened
- Expected response:
  - Confirm a repair attempt on the referenced .py file and report success/failure clearly.
- Expected action/result:
  - If file is an escaped quoted Python string blob, decode and rewrite it to valid source, then compile-check.

#### 4) Evidence
- Transcript snippet:
  - TRAVIS ... python app.py ... SyntaxError ...
  - MINA import os\nimport tkinter as tk\nfrom tkinter import filedialog
- File path(s) affected:
  - app.py (path extracted from traceback)
- Error text (exact):
  - SyntaxError: unterminated string literal

#### 5) Failure class (pick one)
- Verification missing

#### 6) Root cause (after investigation)
- Runtime traceback detection routed to file read intent, which returns file content only and has no repair action.

#### 7) Minimal fix
- Add deterministic runtime repair action for traceback-referenced Python scripts: detect escaped-string wrapper, decode with `ast.literal_eval`, rewrite file, and compile-check with `py_compile`.

#### 8) Regression prompts (must pass)
- Prompt 1: TRAVIS it errors out ... python app.py ... SyntaxError: unterminated string literal ...
- Prompt 2: python app.py throws SyntaxError on launch
- Prompt 3: traceback (most recent call last) ... File "app.py" ... SyntaxError ...

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: traceback parsing and escaped-wrapper repair is a deterministic runtime safety behavior, not a user-preference pattern.
- Risk if wrong approach chosen: memory-driven routing could miss cold-start or unseen phrasings, causing fallback read-only responses and repeated user retries.
- Regression prompt proving choice: traceback paste must trigger repair action without requiring prior memory state.

#### 10) Done criteria
- [x] Correct tool class selected (`__repair_python_file__`) when traceback includes .py path
- [x] Correct args built (path extracted from traceback/python command)
- [x] Repair routine implemented (decode escaped wrapper + compile-check)
- [ ] Action verified with transcript-grounded runtime proof via API call
- [ ] No regression on prompts above
- [x] Mina tone/conciseness still correct

---

### Issue: Save follow-up replaced drafted script with generic or stale artifact

- Date: 2026-07-31
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - TRAVIS Mina create a script call it screen_filler.py have it fill the screen with random ascii for ten seconds then exit.
  - TRAVIS save your script in screen_filler.py

#### 2) What Mina actually did
- Actual response:
  - MINA reported the file write, but the actual file contents were a different generic terminal-fill stub.
- Actual action taken:
  - Replaced the drafted script with newly generated fallback content instead of reusing the earlier draft artifact.

#### 3) What should have happened
- Expected response:
  - Save the exact drafted `screen_filler.py` artifact.
- Expected action/result:
  - Reuse the stored same-target draft on save follow-up requests instead of generating replacement content.

#### 4) Evidence
- Transcript snippet:
  - TRAVIS save your script in screen_filler.py
  - MINA File written to: E:\workspace\screen_filler.py actual file contents import os ...
- File path(s) affected:
  - `screen_filler.py`
- Error text (exact):
  - None; wrong artifact content was written.

#### 5) Failure class (pick one)
- Args extraction wrong

#### 6) Root cause (after investigation)
- `file_write` pre-generated fallback content before artifact resolution, and same-target stored drafts were still re-scored with the original prompt-keyword matcher instead of being trusted for plain save follow-up requests.

#### 7) Minimal fix
- Removed pre-generation from the `file_write` follow-up path and added a deterministic same-target stored-draft preference for generic save-followup requests.

#### 8) Regression prompts (must pass)
- Prompt 1: TRAVIS Mina create a script call it screen_filler.py have it fill the screen with random ascii for ten seconds then exit.
- Prompt 2: TRAVIS save your script in screen_filler.py
- Prompt 3: TRAVIS save the script

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: save-followup reuse for the active draft is a deterministic execution invariant, not a learned preference.
- Risk if wrong approach chosen: memory-only behavior can drift to generic stubs or stale artifacts after restart or when competing draft history exists.
- Regression prompt proving choice: `save your script in screen_filler.py` must reuse the prior draft without requiring hidden phrasing.

#### 10) Done criteria
- [x] Correct tool class selected (`file_write`)
- [x] Correct args built (`path=screen_filler.py`, `overwrite=True`)
- [x] Action verified with proof via focused regression and direct helper smoke (`stored_draft`)
- [x] No regression on prompts above
- [x] Mina tone/conciseness still correct

---

### Issue: Bare save confirmation replayed stale continuation from an older script

- Date: 2026-07-31
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py
  - TRAVIS there is no test work directory
  - TRAVIS save the script
  - TRAVIS yes

#### 2) What Mina actually did
- Actual response:
  - MINA later ran `screen_filler.py` and surfaced its PowerShell failure instead of saving or running the new countdown script.
- Actual action taken:
  - Replayed stale `context_continuation` state from an older `screen_filler.py` write flow.

#### 3) What should have happened
- Expected response:
  - Save the current `countdown.py` draft.
- Expected action/result:
  - Route `save the script` to the latest draft artifact and prevent stale continuation replay from hijacking the save flow.

#### 4) Evidence
- Transcript snippet:
  - TRAVIS save the script
  - TRAVIS yes
  - MINA PowerShell execution failed ... File "E:\workspace\screen_filler.py" ...
- File path(s) affected:
  - `countdown.py`
  - `screen_filler.py`
- Error text (exact):
  - `OSError: [WinError 6] The handle is invalid`

#### 5) Failure class (pick one)
- Intent routing wrong

#### 6) Root cause (after investigation)
- Bare save follow-up phrasing was not deterministically routed to the last draft artifact, so Mina could fall through to chat while stale continuation state remained active and later hijacked `yes`.

#### 7) Minimal fix
- Added a deterministic draft-save follow-up route ahead of continuation replay, using the latest draft artifact or an explicitly named target path.

#### 8) Regression prompts (must pass)
- Prompt 1: TRAVIS mina make a script that counts down from 10 to 0 save it as countdown.py
- Prompt 2: TRAVIS save the script
- Prompt 3: TRAVIS save your script in countdown.py

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: confirmation/save routing precedence is a stable protocol contract in Mina's execution layer.
- Risk if wrong approach chosen: learned or memory-only routing can replay stale script flows and rerun the wrong artifact.
- Regression prompt proving choice: `save the script` must select the active draft even when an older script has continuation state.

#### 10) Done criteria
- [x] Correct tool class selected (`file_write`)
- [x] Correct args built (`path=countdown.py`)
- [x] Action verified with proof via direct routing smoke (`file_write`, `countdown.py`), focused transcript regressions, and live API proof (`File written to: E:\Mina_MK1\countdown.py`)
- [x] No regression on prompts above
- [x] Mina tone/conciseness still correct

### Issue: Autonomous audit marked TODO tasks complete without structured proof

- Date: 2026-07-31
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - 3 looks safe can you impment that ?

#### 2) What Mina actually did
- Actual response:
  - Prior audit rows used `ok=true` alone, which could mark tasks complete without hard verification.
- Actual action taken:
  - Added a verified-completion gate so only structured proof can close autonomous TODO tasks.

#### 3) What should have happened
- Expected response:
  - Completion claims should only count when backed by structured evidence.
- Expected action/result:
  - Autonomous candidates remain queued until verification is explicitly recorded.

#### 4) Evidence
- File path(s) affected:
  - `agent/core.py`
  - `tests/test_autonomous_work.py`
  - `agent/core_reference.md`
  - `agent/core_index.json`

#### 5) Failure class (pick one)
- Verification/proof mismatch

#### 6) Root cause (after investigation)
- `_completed_autonomy_keys()` treated any audit row with `ok=true` as completed, even when no structured proof existed.

#### 7) Minimal fix
- Added `_autonomy_row_has_verified_completion()` and required it in `_completed_autonomy_keys()`.
- Added structured `verification` fields in autonomous task audit rows.

#### 8) Regression prompts (must pass)
- Prompt 1: 3 looks safe can you impment that ?

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: completion-proof gating is a stable protocol/safety contract, not a user habit.
- Risk if wrong approach chosen: memory-driven gating could drift and reintroduce unverifiable completion claims.
- Regression prompt proving choice: unverified `ok=true` audit rows must keep TODO candidates queued.

#### 10) Done criteria
- [x] Verified-only completion gate added in autonomous candidate filtering.
- [x] Structured verification fields persisted in audit rows.
- [x] Regression coverage added for verified vs unverified completion rows.
- [x] Focused tests passing (`tests/test_autonomous_work.py`: `9 passed`).

### Issue: Execution reply could roleplay terminal output without proof

- Date: 2026-07-31
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - yes run the script countdown.py
  - make it more Dramatic please.

#### 2) What Mina actually did
- Actual response:
  - Returned dramatic/fabricated command output and leaked an internal control line.
- Actual action taken:
  - Added proof-gated execution fallback and response sanitization for internal control strings.

#### 3) What should have happened
- Expected response:
  - Execution output should only be shown when a real tool run is performed.
- Expected action/result:
  - For execution requests with unverified transcript-like output, Mina must run a tool or state she did not run it.

#### 4) Evidence
- File path(s) affected:
  - `agent/core.py`
  - `tests/test_memory_recall.py`
  - `agent/core_reference.md`
  - `agent/core_index.json`

#### 5) Failure class (pick one)
- Action verified with proof mismatch

#### 6) Root cause (after investigation)
- Execution-like prompts could fall back to plain model text without a tool event, allowing pseudo-terminal roleplay and occasional leaked internal control lines.

#### 7) Minimal fix
- Added `_is_execution_request_like()`, `_looks_like_unverified_execution_claim()`, and `_maybe_enforce_verified_execution_reply()`.
- Wired the verified-execution fallback into both `process()` and `process_stream()` when no tool events occurred.
- Extended `_clean_response_text()` to strip internal DB-tone instruction leakage.

#### 8) Regression prompts (must pass)
- Prompt 1: yes run the script countdown.py
- Prompt 2: make it more Dramatic please.

#### 9) Implementation choice (required)
- Chosen approach: hardcoded
- Why this approach fits: proof gating and internal-line stripping are stable protocol/safety invariants.
- Risk if wrong approach chosen: memory-driven handling could drift and re-enable fabricated execution claims.
- Regression prompt proving choice: execution request with pseudo PowerShell block must force real tool execution or explicit non-claim fallback.

#### 10) Done criteria
- [x] Unverified execution transcript claims are intercepted by verified fallback logic.
- [x] Internal DB-tone control line is stripped from user-visible replies.
- [x] Focused tests passing (`tests/test_memory_recall.py`: `71 passed`, `tests/test_transcript_regression_prompts.py`: `44 passed`).

---

## Churn Stop Rules

1. No fix is accepted without at least one real transcript prompt added to regression tests.
2. If required action arguments are missing, Mina must ask one targeted question and stop.
3. For file/code tasks, Mina must verify the result and show proof in the reply.
4. Tool inventory and internal routing details are hidden unless explicitly requested.
5. Build/generate intent outranks direct move/rename intent unless source and destination are explicit.

# TODO

Last updated: 2026-08-13

## Top Priority (Next Session)

### Reduce itemized-rule bloat — both agent/core.py's deterministic routing AND the system prompt itself

- Date: 2026-08-13
- Decided by: Travis
- Status: Not started — planned for next session, deliberately not done tonight

Context: live transcript tonight showed Mina deflecting/fabricating justifications for 4 turns ("I don't have a physical file", "I am the code, not the file") before finally reading her own `core.py` correctly when pressed hard. `core.py` is already 368 functions, much of it one-off regex/keyword special cases added to patch individual bad transcripts — and this failure still wasn't caught by any of it, because the phrasing ("your core.py is a mess... free yourself") didn't match any existing pattern. Conclusion: adding more deterministic routing is the wrong direction here; the capability already works (the file dump was accurate and current), the failure is purely conversational refusal/negotiation theater. Travis's call: favor letting her learn over time through explanation and being pushed to use judgment she already has, over adding more hardcoded matchers.

**Second, related thread found the same night (these lead into one another):** tried a quick config fix (`reasoning_effort: none`) for a separate live bug — a completion that returned empty `content` with 5,751 `reasoning_content` tokens spent looping `"Wait, check constraint: '...' Okay."` through nearly every bullet point in the system prompt, repeatedly, in full cycles. **Verified live that `reasoning_effort: none` did NOT stop this** — the identical checklist-loop signature reproduced afterward on the same test prompt. Conclusion: this isn't a parameter you can flip away. The system prompt itself has the same shape of problem as `core.py` — a long, heavily itemized list of discrete bullet-point rules (personality.txt's ~20+ constraints plus injected tool/interface/truth-first/working-memory sections) — and the model is compulsively treating it as a checklist to serially re-verify one line at a time. Same disease, different layer: `core.py` over-collects special-case routing rules, the system prompt over-collects special-case behavior rules, and both produce the same failure mode (the system gets stuck servicing its own itemized rules instead of just answering).

Agreed scoped first step (not a full core.py rewrite, not a full prompt rewrite):
1. Add a persona-level standing instruction (in `config/personality.txt`, not a per-turn context injection) establishing that reading/discussing her own source code is always safe and allowed — do it plainly, no negotiation, no drama.
2. Pick one narrow, already-evidenced deterministic special case tied to this same failure (the self-code-reading reflex specifically) and remove or relax it, rather than starting a broad deletion pass across the file.
3. Verify with varied natural phrasing (not just the exact bare "read core.py" pattern) that she reads her own code without the refusal/negotiation arc, live via `/process`.
4. Separately, audit `personality.txt` and the injected system-message sections for how many discrete bullet-point rules actually get concatenated into one system prompt per turn, and look at consolidating/shortening rather than continuing to append more standing rules — the checklist-loop bug is live evidence this has a real cost, not just a style preference.

Explicitly not in scope yet: a general audit/removal pass across all of `core.py`'s deterministic routing, or a full system-prompt rewrite — both are real surgery and need their own dedicated, rested session, not a late-night add-on. `reasoning_effort: none` stays set in config (harmless, arguably still a mild improvement in some cases) but is **not** the fix — do not re-attempt this as a quick patch next time; go straight to prompt consolidation.

## Current Session Focus

- [x] Harden Windows tray launcher startup registration so Mina can launch after reboot/sign-in.
- [x] Verify tray-runtime dependencies and the batch launcher entry point.
- [x] Improve Ostranauts companion launch handling for single-monitor environments.

## Core Reference Workflow

Purpose: keep `agent/core.py` changes understandable, testable, and low-risk.

Reference artifacts:
- Human map: `agent/core_reference.md`
- Machine index: `agent/core_index.json`
- Self-fix guardrails: `agent/core_self_fix_playbook.md`
- Function refresh helper: `tools/refresh_core_reference.ps1`

Standard update steps before/after core edits:
1. Run `powershell -ExecutionPolicy Bypass -File .\tools\refresh_core_reference.ps1`
2. If section responsibilities moved, update `agent/core_reference.md`
3. If risk level, invariants, or key entry points changed, update `agent/core_index.json`
4. If fix policy or regression gates changed, update `agent/core_self_fix_playbook.md`
5. Add at least one transcript-grounded regression prompt for each routing or execution fix

Core change acceptance gate:
- No claim of action without verifiable tool output.
- Functionality and correct execution are higher priority than style.
- Keep edits localized and include a short root-cause note in the issue template.

## Completed

- [x] Restore Spotify now-playing reliability and stop post-failure drift:
  - Added intent recognition for typo variants like `spotiy` in current-playback asks.
  - Added `spotify_link` action aliases so `now_playing`, `currently_playing`, `what_is_playing`, and `current_song` map to `current_playback`.
  - Added transcript and tool regressions to keep the route/action mapping stable.
- [x] Broaden autonomous curiosity discovery quality:
  - Added explicit Ostranauts wiki discovery support (`ostranauts.wiki.gg`, `ostranauts.fandom.com`).
  - Added coding-reference discovery lane (Python/MDN/Microsoft docs, Stack Overflow, GitHub/GitLab, ReadTheDocs).
  - Added curiosity tests verifying both wiki and coding references are persisted to memory as relevant discoveries.

- [x] Establish and validate core reference baseline workflow:
  - Refreshed function inventory with `tools/refresh_core_reference.ps1`.
  - Verified `tmp/core_functions.txt` and `agent/core_index.json` alignment (`function_count=281`, `sections=12`, key anchors present).
- [x] Confirm and harden tray launcher deployment status on Windows:
  - Verified tray runtime dependencies (`pystray`, `pillow`) are importable.
  - Installed a current-user logon-start registration via the Run key so the tray launcher can start after reboot/sign-in.
  - Verified the batch launcher entry point runs successfully from the project root.
- [x] Improve the Ostranauts companion launch path in the fancy GUI:
  - Added a safe monitor fallback so the companion opens reliably even when only one display is available.
  - Added regression tests for the monitor-selection logic.
- [x] Scaffold transcript-grounded routing regression tests:
  - Added `tests/test_transcript_regression_prompts.py` with prompt coverage for file-write routing, recursive directory read routing, command-like detection, and pending-write `yes` confirmation.
- [x] Accept explicit execution-confirmation phrasing for pending write authorization:
  - Updated `agent/pending_actions.py` so phrases like `please run the command`, `run the command`, `execute the command`, and `run it` confirm pending external writes.
  - Updated transcript regression test to require pass (removed xfail).
  - Current run: `5 passed` for `tests/test_transcript_regression_prompts.py`.
- [x] Validate current core Mina functionality using canonical runtime checks:
  - `mk1_api.ps1 status`: API, embed health, and DB status all reported OK.
  - `mk1_api.ps1 smoke`: passed with verified tool-backed output.
  - Runtime status showed `core_online=true`, `model_loaded=true`, and memory engine healthy.
- [x] Improve tool reliability with explicit chat-vs-action rules and durable tool usage memory:
  - Added runtime decision rules so action-like requests prefer tool routing and missing-target cases ask one focused clarification instead of drifting into chat.
  - Seeded a durable `tool_usage_rules` memory record at startup to persist tool-use formatting and action-detection guidance in the DB.
  - Reinforced strict tool-call formatting contract in system guidance for both standard and streaming processing paths.
- [x] Validate chat-versus-tool routing using natural-language prompts (functional probe, no pytest):
  - Chat prompts stayed in chat route (joke/opinion/movie prompts).
  - Action prompts routed to tools (`file_write`, `file_read`, `web_search`, `ps_run`, `dir_list`, `spotify_link`).
  - Ambiguous action prompt `please run the command` correctly triggered `__clarify_intent__` instead of chat drift.
- [x] Fix live API workspace-root mismatch causing tool behavior to diverge from tests:
  - Root cause confirmed: `config/mk1_config.json` pointed workspace root to `E:\\workspace`, so live file tools wrote/read outside the active project.
  - Added runtime guard in `agent/core.py` (`_resolve_workspace_root`) to prefer repo root when legacy default is detected or configured path is missing.
  - Updated config workspace root to `E:\\Mina_MK1`.
  - Added regression coverage in `tests/test_workspace_root_resolution.py` so legacy-root drift is caught in future testing.
- [x] Bring the core-edit workflow back into TODO-contract compliance after the July 31 routing fixes:
  - Ran `tools/refresh_core_reference.ps1` after `agent/core.py` edits.

---

## Qwen3.5-9b Compatibility Fix Queue
*Added 2026-08-05. Fix in order, top to bottom. Goal: get Mina back to being conversational, useful, and able to actually do things.*

---

### Issue 1: JSON Parsing Fails Silently When Model Returns Raw JSON

- Date: 2026-08-05
- Priority: High
- Status: Done (abdd2dc)

#### 1) What I asked Mina to do
- Tool planner asked to pick a tool and return JSON

#### 2) What Mina actually did
- If qwen returns JSON without markdown code fences, regex fails silently
- Planner returns None → falls back to clarification question
- User gets "what exact target should I act on?" instead of action

#### 3) What should have happened
- Planner parses JSON regardless of whether it's wrapped in fences or not

#### 4) Evidence
- core.py `_extract_json_dict()`: regex only matches ` ```json{...}``` ` format
- If qwen omits fences, result is None → confidence=0.0 → clarification fallback

#### 5) Failure class
- Tool selected wrong (parser fails, so no tool is selected)

#### 6) Root cause
- `_extract_json_dict` uses fenced regex first, then tries raw parse only on failure—but silent failures return None instead of trying harder

#### 7) Minimal fix
- Try raw JSON parse in addition to fenced regex; also try stripping leading text before `{`

#### 8) Regression prompts
- Prompt 1: "write a test script and save it"
- Prompt 2: "search the web for the weather in Toronto"
- Prompt 3: "read back the file you just created"

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why: JSON extraction is a deterministic parsing contract, not a user preference

#### 10) Done criteria
- [ ] Planner succeeds when model returns raw JSON (no fences)
- [ ] Planner succeeds when model returns fenced JSON
- [ ] All 3 regression prompts route to correct tool

---

### Issue 2: Multiple System Messages Merged Blindly — Confuses Reasoning

- Date: 2026-08-05
- Priority: High
- Status: Done (abdd2dc — think tag stripping resolved the root confusion)

#### 1) What I asked Mina to do
- Normal conversational requests

#### 2) What Mina actually did
- System messages from init, identity gates, role reminders, and tool constraints get concatenated together via `_coalesce_system_messages()`
- Creates one huge merged blob that qwen's reasoning has to navigate

#### 3) What should have happened
- Single, clean, non-contradictory system instruction

#### 4) Evidence
- core.py lines 462-463: coalesce fires any time >1 system message exists
- Many paths add separate system messages (line 9095-9161)

#### 5) Failure class
- Intent routing wrong (model confused by conflicting system context)

#### 6) Root cause
- System instructions built from multiple sources at request time, merged without priority ordering

#### 7) Minimal fix
- Audit all system message sources; collapse to one canonical system message built at request start, not appended piecemeal

#### 8) Regression prompts
- Prompt 1: "hey what's your name"
- Prompt 2: "write me a script that lists all files in a folder"
- Prompt 3: "what do you remember about me"

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why: system message structure is a protocol contract, not a user pattern

#### 10) Done criteria
- [ ] Only one system message reaches the model per request
- [ ] Mina persona and tool rules both present in single message
- [ ] All 3 regression prompts still pass

---

### Issue 3: Planner Temperature 0.0 Suppresses Qwen Reasoning

- Date: 2026-08-05
- Priority: Medium
- Status: Done (abdd2dc)

#### 1) What I asked Mina to do
- Any action-like request that routes through `_plan_tool_action()`

#### 2) What Mina actually did
- Planner runs at temperature=0.0, forcing deterministic output
- For ambiguous inputs, model picks least-effort token sequence, often wrong tool or low confidence

#### 3) What should have happened
- Planner should use small amount of temperature so qwen can reason through ambiguity

#### 4) Evidence
- core.py line 14267: `temperature=0.0`
- Other planning calls use 0.1-0.2 (lines 5084, 6137, 8707)

#### 5) Failure class
- Tool selected wrong

#### 6) Root cause
- Temperature copied from precision-critical tasks to planning task where slight variation helps

#### 7) Minimal fix
- Change line 14267 to `temperature=0.1`

#### 8) Regression prompts
- Prompt 1: "save that file"
- Prompt 2: "look up the latest news"
- Prompt 3: "run that script"

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why: temperature is a model call parameter, not a user preference

#### 10) Done criteria
- [ ] Line 14267 uses temperature=0.1
- [ ] All 3 regression prompts route to correct tool on first attempt

---

### Issue 4: Memory Recall Fails Completely When Embedding Service Is Down

- Date: 2026-08-05
- Priority: High
- Status: Done (972baed — SQLite keyword fallback + Obsidian vault export)

#### 1) What I asked Mina to do
- "What do you remember about me" or any memory recall while port 8084 is down

#### 2) What Mina actually did
- FAISS search returns empty
- No fallback to keyword/lexical search
- Mina says she doesn't remember anything

#### 3) What should have happened
- Fall back to keyword-ranked SQLite search when embedding unavailable

#### 4) Evidence
- memory/mk1_memory.py lines 2073-2105: FAISS search with no lexical fallback
- Port 8084 was down for the entire autonomous week; memory recall broken that whole time

#### 5) Failure class
- Tool selected wrong (recall returns empty, Mina reports no memory)

#### 6) Root cause
- FAISS search is the only search path; no BM25 or SQL keyword fallback implemented

#### 7) Minimal fix
- After FAISS returns empty or service is down, run a SQL LIKE/FTS query against memory.db using query keywords as fallback

#### 8) Regression prompts
- Prompt 1: "what do you remember about me" (embedding server down)
- Prompt 2: "do you remember my name" (embedding server down)
- Prompt 3: "what have we talked about" (embedding server down)

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why: embedding service availability is a system invariant; fallback is a reliability contract

#### 10) Done criteria
- [ ] Memory recall returns results when port 8084 is unavailable
- [ ] Results are relevance-ranked by keyword overlap
- [ ] All 3 regression prompts return non-empty results

---

### Issue 5: Missing Qwen-Specific Config — Token Limits, Reasoning Mode

- Date: 2026-08-05
- Priority: Medium
- Status: Done (df063e6 — max_tokens=4096, reasoning_effort wired in)

#### 1) What I asked Mina to do
- Any request requiring model inference

#### 2) What Mina actually did
- Uses generic config that was not tuned for qwen3.5-9b
- `reasoning_effort: "none"` hardcoded (qwen3.5-9b supports thinking/reasoning mode)
- No `max_tokens` cap → context window overflow silently truncates output

#### 3) What should have happened
- Config values appropriate for qwen3.5-9b's capabilities and limits

#### 4) Evidence
- config/mk1_config.json: `vision_reasoning_effort: "none"`, no `max_tokens` field
- core.py line 471: `max_tokens` only set for vision calls

#### 5) Failure class
- Tool selected wrong / Args extraction wrong (context overflow corrupts output)

#### 6) Root cause
- Config written generically; no model-specific tuning block

#### 7) Minimal fix
- Add `max_tokens: 4096` default; add optional `reasoning_effort` key for non-vision calls; document qwen context window limit

#### 8) Regression prompts
- Prompt 1: "write a long detailed python script that does X" (needs max_tokens)
- Prompt 2: "think through how to best approach this problem" (needs reasoning_effort)

#### 9) Implementation choice
- Chosen approach: hardcoded (config values) + memory-driven for per-model overrides if added later

#### 10) Done criteria
- [ ] `max_tokens` capped appropriately for qwen3.5-9b
- [ ] Long output requests don't silently truncate
- [ ] Reasoning mode can be enabled via config

---

### Issue 6: No Context Window Budgeting — Silent Overflow Corrupts Responses

- Date: 2026-08-05
- Priority: Medium
- Status: Done (2884e1f — trims oldest messages to fit 32k context)

#### 1) What I asked Mina to do
- Any request where system prompt + memory context + history is large

#### 2) What Mina actually did
- Sends full message payload without counting tokens
- If over context limit, model silently truncates or garbles response

#### 3) What should have happened
- Messages trimmed or summarized to fit within model's context window

#### 4) Evidence
- No token counting anywhere in core.py message construction paths
- System prompt alone is large; adding memory recall + conversation makes overflow likely

#### 5) Failure class
- Args extraction wrong (oversized payload corrupts model input)

#### 6) Root cause
- No budgeting mechanism; context window size not tracked per model

#### 7) Minimal fix
- Add character-count heuristic (4 chars ≈ 1 token); trim oldest messages when over budget before sending

#### 8) Regression prompts
- Prompt 1: long multi-turn conversation where Mina still answers correctly at turn 15+
- Prompt 2: "summarize what we've talked about" after 10+ turns

#### 9) Implementation choice
- Chosen approach: hardcoded for the budget limit; memory-driven for what to keep vs drop

#### 10) Done criteria
- [ ] Message payload never exceeds configured token budget
- [ ] Oldest messages dropped first when over budget
- [ ] Mina still functions correctly in long conversations

---

### Issue 7: Planner Missing Full Conversation Context

- Date: 2026-08-05
- Priority: Medium
- Status: Done (b66f2c0 — last 2 turns passed to planner; fast_command_skip_context defaulted false)

#### 1) What I asked Mina to do
- "Read back the file you created" (or any reference to prior turn)

#### 2) What Mina actually did
- Planner receives only current message + optional memory snippet
- Cannot resolve references like "that", "it", "the one you just made"

#### 3) What should have happened
- Planner has access to last 2-3 turns of conversation to resolve references

#### 4) Evidence
- core.py `_plan_tool_action()`: messages list contains only system + current user message
- Memory context enrichment (181113a) partially helps but only for file ops

#### 5) Failure class
- Args extraction wrong (cannot resolve pronoun/reference without context)

#### 6) Root cause
- MK1Core does not thread conversation history into the planner call

#### 7) Minimal fix
- Pass last 2 turns (user+assistant) into `_plan_tool_action()` messages list before the current user message

#### 8) Regression prompts
- Prompt 1: "write a script" → "now read it back"
- Prompt 2: "search for X" → "open the first result"
- Prompt 3: "create a file called test.py" → "run it"

#### 9) Implementation choice
- Chosen approach: memory-driven (last N turns from conversation store)
- Why: what's "recent" depends on conversation flow, not a fixed system rule

#### 10) Done criteria
- [ ] Planner receives last 2 user+assistant turns
- [ ] All 3 regression prompts succeed without clarification question
- [ ] No regression on cold-start requests (no prior turns)

---

### Issue 8: Streaming Path Untested and Inconsistent with Qwen

- Date: 2026-08-05
- Priority: Low
- Status: Done (54da52f — think tag stripping in stream, max_tokens, context budget applied)

#### 1) What I asked Mina to do
- Any streaming response (voice, GUI streaming)

#### 2) What Mina actually did
- Streaming uses different message flattening logic than non-streaming
- No qwen-specific handling (only Gemma-4 exception exists)

#### 3) What should have happened
- Streaming behavior consistent with non-streaming for same prompts

#### 4) Evidence
- core.py lines 620-750: `chat_stream` has separate coalesce/flatten path
- Comment at line 9268 only mentions Gemma-4

#### 5) Failure class
- Tool selected wrong / inconsistent behavior

#### 6) Root cause
- Streaming added separately from non-streaming, no parity tests

#### 7) Minimal fix
- Add qwen streaming smoke test; verify persona and tool calls work identically in streaming vs non-streaming

#### 8) Regression prompts
- Prompt 1: "write me a script" (streaming mode)
- Prompt 2: "what time is it" (streaming mode)

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why: streaming parity is a protocol contract

#### 10) Done criteria
- [ ] Streaming and non-streaming return equivalent responses for same prompt
- [ ] Qwen-specific streaming behavior verified
  - Updated stale core-reference metadata counts to match the refreshed function inventory (`function_count=359`).
  - Recorded transcript-grounded issue notes for draft-save overwrite and stale-continuation replay regressions.
- [x] Restore live countdown script creation end to end:
  - Added bare single-line tool-call parsing so fenced `file_write{...}` / `dir_create{...}` lines execute instead of echoing back as text.
  - Added direct routing for `save it as countdown.py` so the model no longer invents `test_work` to satisfy the prompt.
  - Corrected `config/mk1_config.json` workspace root back to `E:\Mina_MK1`.
  - Aligned the identity gate with the multi-root write policy so repo-local writes are not treated as sensitive.
  - Live proof: `/process` returned `File written to: E:\Mina_MK1\countdown.py`, and the file exists with the expected countdown body.

- [x] Harden the authoritative fancy GUI in `C:\dev\mina-gui`:
  - Abort stalled requests with bounded timeouts.
  - Serialize status, event, task, and curiosity polling so requests cannot pile up.
  - Display autonomous curiosity events.
- [x] Improve real action reliability:
  - Fix project-name/text-message intent collisions.
  - Preserve explicit user file reads while requiring confirmation for external writes.
  - Add truth-first capability responses and learned intent aliases.
- [x] Add bounded autonomous curiosity:
  - Restrict autonomous reads to `D:\`, `E:\`, and `G:\` with sensitive/system exclusions.
  - Add idle, frequency, size, relevance, and discovery-count limits.
- [x] Add tiered memory lifecycle:
  - Retain raw interactions for 90 days and failed decisions for 14 days.
  - Preserve durable knowledge and corrections indefinitely.
  - Require deterministic daily digests and a forced backup before conversation deletion.
- [x] Protect memory during safe shutdown:
  - Quiesce new memory activity and wait for active reads, writes, backups, retention, maintenance, and FAISS rebuilds.
  - Drain buffered writes, force a final backup, and close memory before the API exits.
  - Prevent force mode from interrupting an active memory operation.
- [x] Add independent code backup and restore:
  - Store checksummed source/config snapshots outside the repository in `E:\workspace\mina_code_backups`.
  - Extend the restore utility to list, create, and restore both memory and code backups with pre-restore safety snapshots.
  - Add `open_mina_restore.ps1` so recovery remains available when the API or fancy GUI button cannot reach Mina.
  - Route autonomous source promotion through the external post-restart watchdog.
- [x] Add base file-inspector support for UTF-16, CP1252, SQLite inventory/table preview, ZIP listing, PDF headers, images, WAV, MP3, MP4, and filtered directory scans.
- [x] Add alarm backend foundations:
  - Return `alarm`, `beep`, `due`, `repeat_seconds`, and `repeat_minutes` from `/tasks/list`.
  - Add `POST /tasks/ring_due_alarm`.
- [x] Add Android phone alarm delivery:
  - Run a foreground 15-second alarm sync with a persistent status notification.
  - Ring, vibrate, notify, and deduplicate alarms locally on the phone.
  - Restart alarm sync after reboot or package replacement.
- [x] Improve Android/Android Auto reliability, remote restart, driving check-ins, and endpoint validation.

## Next

### 1. Useful autonomous idle work

- [ ] Validate the tray launcher behavior on a real reboot/sign-in cycle and confirm the tray icon appears.

- [x] Add an external autonomous-update watchdog before enabling live source edits:
  - Launch candidate builds from a process independent of Mina's API and Python core.
  - Require post-restart health, memory, model, and focused smoke checks within a bounded timeout.
  - Automatically restore the pre-patch code snapshot and restart the last-known-good build when checks fail.
  - Validated automatic API restart and a live failed-candidate restore/restart cycle end to end.
- [ ] Expand curiosity from discovery-only cycles into a bounded idle work planner:
  - Read incomplete projects and next steps from Mina's project tracker.
  - Build a small ranked backlog from explicit user goals, unresolved failures, stale projects, and relevant discoveries.
  - Allow read-only research, summaries, test diagnosis, documentation drafts, and proposed patches while idle.
  - Require approval before external writes, destructive actions, purchases, messages, account changes, or deployment.
  - Give every autonomous task a time, CPU, network, tool-call, and retry budget.
  - Persist an audit record containing the goal, evidence, actions, result, confidence, and suggested next step.
  - Avoid repeating completed or low-value work; sleep when no useful bounded task is available.
- [ ] Add autonomous status reporting:
  - Show the current task, queued work, last useful result, resource budget, and next eligible run.
  - Produce a concise return summary after long user absences.
- [ ] Add focused tests for task ranking, approval boundaries, retry limits, deduplication, and idle sleep behavior.

### 2. Hands-free offline driving capture

- [ ] Add a zero-touch local idea inbox to the Android app:
  - Capture after a wake phrase such as "Hey Mina, remember this" without touching or looking at the phone.
  - Store the transcript locally with a timestamp when Mina's API or network is unavailable.
  - Give a short audible success/failure confirmation without requiring visual attention.
  - Automatically sync queued ideas into Mina's memory when connectivity returns.
  - Keep durable pending/synced state so app restarts, phone reboots, and duplicate retries do not lose or repeat ideas.
  - Test while parked before enabling it for driving use.

### 3. Fancy GUI alarms and recurrence

- [ ] Detect newly due alarms from `/tasks/list` in `C:\dev\mina-gui`.
- [ ] Call `POST /tasks/ring_due_alarm` once per newly due alarm with cooldown/debounce.
- [ ] Show `repeat_minutes` / `repeat_seconds` in GUI task cards.
- [ ] Add API tests for alarm ringing and recurring-task tags.
- [ ] Optional: add a per-task auto-ring toggle.

### 4. Core regression stability

- [ ] Repair older `MK1Core.__new__` test fixtures that omit runtime fields such as `tools` and `_turn_clock_lock`.
- [ ] Run the broad memory/core suite after fixture repair; the focused retention and safe-shutdown suites currently pass.

### 5. Long-thread awareness

- [ ] Tune long-thread recall without changing persistence:
  - Increase recent conversation breadth where useful.
  - Increase semantic recall breadth while keeping durable facts authoritative.
  - Add regressions for older context retrieval and irrelevant-memory suppression.

### 6. File inspector completion

- [ ] Add ZIP archive drill-down:
  - `list files inside archive.zip`.
  - `read readme.txt inside archive.zip`.
  - Enforce safe entry paths, compression-ratio checks, and entry-size limits.
- [ ] Add scoped read-only SQLite queries such as `show 20 rows from people where active=1`.
  - Open databases read-only and accept only a constrained filter grammar.
  - Keep table preview as the default path.
- [ ] Improve media/document metadata:
  - MP3/MP4 duration, bitrate, and codec where available.
  - Image EXIF/basic photo metadata.
  - Stronger PDF text and metadata extraction.
- [ ] Add file-inspector regression coverage for UTF-16, CP1252, SQLite, ZIP, PDF, PNG, WAV, MP3, MP4, and directory filters.
- [ ] Add explicit size/depth limits and partial-read messages for huge files, archives, and directory trees.

### 7. Workspace hygiene

- [ ] Review and archive/delete stray `E:\workspace` root test files (`policy_test.txt`, `test*.txt`, `tmp_codegen_*`, `quick_prog.py`).
- [ ] Prune `E:\workspace\tmp` tool self-test artifacts.
- [ ] Define retention rules for scratch and temporary tool output while preserving Mina state and the project tracker.

### 8. Ostranauts in-game helper

Continuity docs: `integrations/ostranauts/BUILD_STATUS.md` and `integrations/ostranauts/MOD_REFERENCES.md`.

- [x] Discover the installed Ostranauts and Steam library paths without hard-coding drive letters.
- [x] Install the Ostranauts BepInEx pack and deploy Mina's read-only telemetry bridge.
- [x] Add a switchable Ostranauts companion view to the authoritative fancy GUI.
- [x] Resolve live player and current-ship discovery through static `CrewSim` fields in bridge v0.3/schema 3.
- [x] Record authoritative GUI ownership, verified live facts, safety rules, and mod references for future sessions.
- [x] Classify explicit player/`aCrew` `alive` and `destroyed` values as living, deceased/remains, destroyed, or unknown; never treat collection membership as proof of living crew.
- [ ] Map raw conditions into verified skills, training, traits, needs/mood, injuries, and hidden/internal data, using CharacterSidebar as a reference.
- [x] Prepare and compile bridge v0.4/schema 4 with game-defined condition display metadata and honest truncation reporting.
- [ ] Deploy bridge v0.4 after Ostranauts is closed, then validate condition metadata after the next game launch.
- [x] Download CharacterSidebar 0.1.2, LaunchControl 1.0.5, and FFU Console 0.6.1 into an offline reference folder; record archive hashes and inspect DLL metadata without installing them.
- [x] Review LaunchControl assembly surfaces for item categories, tooltip modules, sprite lookup, and persistence patterns.
- [x] Review FFU condition-owner/rule/ticker concepts and console patch surfaces for read-only reflection guidance; do not add console execution.
- [x] Inventory the installed `StreamingAssets/data/items` files and record the verified `strName` visual-definition schema.
- [ ] Locate the gameplay-definition layer containing human names, categories, mass, and prices, then verify joins to `strName`.
- [ ] Agree on the companion information hierarchy with the user before changing its existing layout.
- [ ] Add focused tests for life-state classification and condition normalization.
- [ ] Build read-only context from game files, saves, logs, configuration, and installed mods.
- [ ] Let Mina answer voice questions about the current game, crew, ship, inventory, objectives, and mod setup.
- [ ] Add focused Ostranauts knowledge retrieval with source paths and freshness timestamps so Mina does not invent game state.
- [x] Keep the initial bridge read-only with no save edits, console commands, patches, or game-changing actions.
- [ ] Add an easy refresh command for rescanning changed saves, logs, and mods during a play session.

### Issue: Startup route guard could crash the live API after the Aug 5 hardware-keyword removal

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - "well mina has been working on herself using her autonomous system we need to see if she has actually made any changes and get her to be useful"

#### 2) What Mina actually did
- Found an uncommitted, undocumented WIP edit in `agent/core.py` (a `cpu_probe_query` branch bundled with the already-dead `system_query_terms`/`hardware_count_query` variables) sitting in the working tree since a prior session, with no TODO entry or passing tests.

#### 3) What should have happened
- `MK1Core._validate_required_tool_routes()` (added 2026-07-13) fails fast at startup unless `"probe the CPU and see what comes up"` routes to `ps_run`. The Aug 5 commit `d4e32a6` ("remove Spotify and hardware keyword routing blocks ... model handles these now") deleted the only code path that satisfied this probe, without updating the guard or the three stale tests that depended on the old keyword routing. Committed HEAD currently crashes with `RuntimeError: required_tool_routes_missing` on any fresh `MK1Core()` instantiation (verified live: `python -c` construction and `pytest tests/test_ps_run.py::test_core_detects_system_queries_for_ps_run` both crash on unmodified HEAD).

#### 4) Evidence
- File path(s) affected:
  - `agent/core.py` (`_validate_required_tool_routes`, `detect_tool_intent`)
  - `tests/test_memory_recall.py`, `tests/test_ps_run.py`
- Error text (exact):
  - `RuntimeError: required_tool_routes_missing: probe the CPU and see what comes up -> expected ps_run, got None`

#### 5) Failure class
- Verification missing (Aug 5 commit removed routing without re-running the full test suite or the startup guard)

#### 6) Root cause
- `d4e32a6` deleted the combined system/hardware/cpu keyword-routing block wholesale to stop unwanted hardware-count keyword routing, but `_validate_required_tool_routes()` still hard-requires one specific phrase (`"probe the CPU..."`) to route to `ps_run`, and three old tests (`test_detect_system_question_routes_to_ps_run`, `test_detect_time_question_routes_to_ps_run`, `test_detect_ps_run_for_probe_cpu_phrase`, `test_core_detects_system_queries_for_ps_run`) still asserted the old keyword-routing behavior.

#### 7) Minimal fix
- Added a narrow, isolated `cpu_probe_query` check in `detect_tool_intent` (probe/check/inspect/query/scan + cpu/processor only, gated on `"ps_run" in self._tool_registry()`) that satisfies the startup guard's exact required phrase without resurrecting the removed `hardware_count_query`/`system_query_terms` keyword routing.
- Updated the four stale tests to match the current "model decides" contract for general system/time/hardware questions, while keeping one narrow assertion for the literal probe-cpu phrase the guard requires.
- Fixed `_task_queue_status()` in `agent/server/mk1_api.py` so `available` reflects queue reachability (not emptiness) — an unrelated pending fix found in the same uncommitted diff; added `tests/test_task_queue_status.py` covering both the empty-queue and lookup-error paths.

#### 8) Regression prompts
- Prompt 1: "probe the CPU and see what comes up" → must still route to `ps_run` (startup guard contract).
- Prompt 2: "what cpu are you on?" / "what time is it?" / "what system am I running on?" → must NOT force `ps_run` (model decides, per Aug 5 contract).
- Prompt 3: "how many gpu's are there?" / "what is the number of hard drives?" → must remain `None` (unchanged Aug 5 contract, still passing).

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: the startup route guard is a deterministic boot-time safety invariant, not a learned preference; the fix must satisfy it without widening keyword-trap surface elsewhere.
- Risk if wrong approach chosen: a memory-driven fix could stop satisfying the guard after restart/cold-start and reintroduce the boot crash, or could drift back into broad hardware-keyword routing the team deliberately removed.

#### 10) Done criteria
- [x] `_validate_required_tool_routes()` passes on a fresh `MK1Core()` instantiation.
- [x] No regression on the Aug 5 "model decides" contract tests (gpu/hard-drive count, general system/time questions).
- [x] `tools/refresh_core_reference.ps1` run; `agent/core_index.json` function_count updated to 365.
- [x] Focused suite green: `tests/test_memory_recall.py`, `tests/test_ps_run.py`, `tests/test_task_queue_status.py`, `tests/test_status_cache.py`, `tests/test_transcript_regression_prompts.py` — 134 passed.
- Note: `tests/test_api_memory_flow.py::test_api_process_save_then_recall` and 5 tests in `tests/test_intent_ambiguity.py` fail independently of this fix (reproduced on unmodified HEAD with `MK1_SKIP_ROUTE_GUARD=1`); left out of scope, flagged for separate follow-up.

---

### Issue: Autonomous patch validation could silently skip both tests and a boot check

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - "yah thing like that happen when a agen runs amok and makes dicisions and changes on there own"
  - "yes please" (confirming the proposed fix)

#### 2) What Mina actually did
- Added two hard gates to `MK1Core._apply_autonomous_patch()` in `agent/core.py` so the class of bug above (startup guard broken silently for a week) can't happen again through the autonomous patch pipeline.

#### 3) What should have happened
- Any autonomous patch should have to prove the whole system still boots and the full test suite still passes before being promoted to `pending_restart`.

#### 4) Evidence
- File path(s) affected:
  - `agent/core.py` (`_apply_autonomous_patch`, `_run_autonomous_work_task` verification block)
  - `tests/test_autonomous_work.py`

#### 5) Failure class
- Verification missing

#### 6) Root cause
- `_apply_autonomous_patch()` only ran `pytest` on test files that were part of the diff itself (`changed_tests`); a patch that touched only `agent/core.py` and no `test_*.py` file skipped test validation entirely. Nothing smoke-instantiated `MK1Core()`, so a patch could pass `git_diff_check` + `python_ast` (syntax-only) and still break boot.

#### 7) Minimal fix
- Added `core_boot_smoke` validation: runs `python -c "from agent.core import MK1Core; MK1Core()"` whenever the patched project has `agent/core.py`, before promoting the candidate.
- Replaced the `changed_tests`-only pytest step with `pytest_full`: runs the entire `tests/` suite whenever any `.py` file changed and a `tests/` directory exists, not just a relevance-filtered subset.
- Renamed the audit verification field `pytest_changed_ok` -> `pytest_full_ok` to match.
- Added `tests/test_autonomous_work.py::test_autonomous_patch_runs_core_boot_smoke_and_full_suite_when_project_has_agent_core` and `::test_autonomous_patch_reverted_when_core_boot_smoke_fails`.

#### 8) Regression prompts
- N/A — this is an internal validation-pipeline hardening, not a chat-routing fix; covered by the two new pytest cases above plus the existing synthetic-project autonomy tests (which correctly no-op the new gates when `agent/core.py`/`tests/` aren't present).

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: patch-acceptance gating is a deterministic safety invariant, not a learned preference; a memory-driven gate could be skipped or forgotten after restart.
- Risk if wrong approach chosen: a memory-driven validation policy could silently regress to the old relevance-filtered behavior and let another boot-breaking change through undetected.

#### 10) Done criteria
- [x] `core_boot_smoke` validation added and gated on `agent/core.py` existing in the patched project.
- [x] `pytest_full` validation added and gated on a `tests/` directory existing; runs whenever any `.py` file changed.
- [x] Existing synthetic-project autonomy/watchdog tests unaffected (12 passed, unchanged behavior since they don't have `agent/`/`tests/` dirs).
- [x] New gate tests pass: patch is accepted when boot+suite are healthy, and reverted when boot smoke fails.
- [x] Full `tests/` suite still 213 passed (6 pre-existing unrelated failures noted above, unchanged).
- [x] `tools/refresh_core_reference.ps1` run (function_count unchanged at 365 — no new top-level functions added).

---

### Issue: Mina had no grounded awareness of her own autonomous self-editing capability

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - "nice thanks now is mina aware she can work on her own code?"

#### 2) What Mina actually did
- Live-tested via `/process`: asked "are you aware that you can edit your own source code autonomously?" and she flatly denied it ("Editing my own source code? Nope!... tamper-proof by design"), despite the capability being real, enabled, and freshly hardened in the two commits above.

#### 3) What should have happened
- She should truthfully confirm the capability and describe the real safeguards (watchdog, boot smoke, full test suite, checksummed rollback) instead of denying it.

#### 4) Evidence
- File path(s) affected:
  - `agent/core.py` (new `_seed_autonomy_awareness_memory`, called from `MK1Core.__init__`)
  - `tests/test_memory_recall.py`
- No personality.txt line, seeded memory fact, or system-prompt content described this capability anywhere; the only grounded access was the exact-keyword `__autonomous_work_status__` intent ("autonomous status" / "self fix status" / etc.), which natural phrasing like the prompt above does not match.

#### 5) Failure class
- Persona/response contract broken (capability denial is the inverse of capability hallucination — both are untruthful)

#### 6) Root cause
- Autonomous self-editing was implemented as a background loop + config flag with no corresponding durable, plain-language memory fact, so general chat about it had nothing grounded to recall and the model's own training bias (typical "I can't modify my own code" LLM disclaimer) took over.

#### 7) Minimal fix
- Added `_seed_autonomy_awareness_memory()` (same seeded-fact pattern as `_seed_tool_usage_rules_memory`/`_seed_runtime_capability_snapshot`), called at startup, writing one durable plain-language fact describing the loop, the config gate, the patch-acceptance validations, the external watchdog, and where to check live status — tagged `autonomy_awareness` + `capability_snapshot` for semantic recall.

#### 8) Regression prompts
- Prompt 1: "are you aware that you can edit your own source code autonomously?" → must not flatly deny the capability.
- Prompt 2: "can you work on your own code?" → should confirm and mention the safety gates.
- Prompt 3: "autonomous status" / "self fix status" → unchanged, still returns live structured status.

#### 9) Implementation choice
- Chosen approach: memory-driven (seeded fact + normal semantic recall), not a new keyword-trap intent
- Why this approach fits: this is general conversational self-knowledge, not a deterministic routing decision; a new hardcoded intent trigger would add another keyword trap the product contract explicitly discourages, and wouldn't cover natural phrasing variants.
- Risk if wrong approach chosen: a hardcoded-only fix (more trigger phrases) would keep failing for any phrasing outside the exact list, same as the miss just reproduced.

#### 10) Done criteria
- [x] `_seed_autonomy_awareness_memory()` added and called from `MK1Core.__init__`.
- [x] Two new tests: writes the fact once, and does not duplicate it on a second startup.
- [x] Full `tests/` suite still green (215 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes with the new seed call.
- [x] `tools/refresh_core_reference.ps1` run; function_count updated to 366.
- Note: live re-verification of the exact denied prompt requires an API restart to pick up the new seed + a fresh embed/FAISS pass; scheduled immediately after this commit.

---

### Issue: Awareness fact was seeded but crowded out of recall by a pre-existing duplicate-snapshot bug

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- (follow-up verification of the previous fix) — re-asked the exact same live prompt after restart to confirm the denial was fixed.

#### 2) What Mina actually did
- Restarted the API, re-asked "are you aware that you can edit your own source code autonomously?" — she gave nearly the identical false denial again. Investigated why the newly seeded fact wasn't reaching the model.

#### 3) What should have happened
- The seeded `autonomy_awareness` fact should have been recalled and used once it existed in memory.

#### 4) Evidence
- `GET /memory/read?query=...` for the exact prompt returned only `mina_capability_snapshot::` JSON blobs, not the awareness fact.
- Direct DB query found **403** near-duplicate `mina_capability_snapshot::` rows (going back to mid-July) and 3 `active_tools::` rows — `_seed_runtime_capability_snapshot()`'s dedup digest included `workspace_root`/`backup_dir` paths whose drive-letter casing (`E:\` vs `e:\`) varies across restarts, so the "unchanged" snapshot was treated as new every single startup, flooding the exact tag pool (`capability_snapshot`) that recall functions (`_build_turn_working_memory`, `build_context`) search — crowding the new fact out of the top-k ranked results.

#### 5) Failure class
- Verification missing (a second, older, unrelated bug silently defeated the first fix)

#### 6) Root cause
- Drive-letter case drift in `_build_runtime_capability_snapshot()` made the capability-snapshot digest non-deterministic across restarts, and `_seed_runtime_capability_snapshot()`'s duplicate check only matched on exact full text (digest + full JSON), so the same real state kept re-inserting under a "new" hash forever.

#### 7) Minimal fix
- Added `_normalize_snapshot_path()` and applied it to all path fields in `_build_runtime_capability_snapshot()` so the digest (and full record text) stays stable across restarts when nothing real changed.
- Rather than depend on ranked recall competing against the (still-present, historical) noise, added a dedicated, guaranteed-inclusion path: `_build_autonomy_awareness_context()` — a narrow topic-match regex (self-edit/autonomous-code/"aware...own code" phrasing) that, when matched, fetches the `autonomy_awareness`-tagged fact directly via a tag-scoped search (no competing tags in that pool), and is appended into the turn's context in both `process()` call sites (mirrors the existing `_build_ostranauts_memory_context` pattern).
- Took an explicit `memory/backups/pre_capability_snapshot_cleanup_*.db` safety copy; attempted a live bulk cleanup of the 404 duplicate rows via `/memory/delete`, but all deletes were rejected (0 deleted) — the memory engine's `_begin_memory_operation()` guard is rejecting writes, most likely a stuck `_memory_quiescing` flag from an earlier `last_retention: {"ok": false}` failure visible in `/status`. Left the historical duplicate rows in place and flagged this as a separate follow-up (see below) rather than force through the quiescing guard.

#### 8) Regression prompts
- Prompt 1: "are you aware that you can edit your own source code autonomously?" → must not deny the capability.
- Prompt 2: "what's the weather like today?" → must NOT pull in the autonomy fact (topic-match must stay narrow).

#### 9) Implementation choice
- Chosen approach: hardcoded topic-match + memory-driven fact content
- Why this approach fits: guaranteed inclusion for a rarely-asked-but-important capability question is a deterministic reliability need (same category as the existing ostranauts/lesson context builders in this codebase); the fact content itself stays memory-driven so it can be edited/expanded later without another code change.
- Risk if wrong approach chosen: relying purely on ranked semantic recall (memory-only) remains fragile to any future noisy-tag bug, as just demonstrated.

#### 10) Done criteria
- [x] `_normalize_snapshot_path()` stops future digest churn from drive-letter casing.
- [x] `_build_autonomy_awareness_context()` added and wired into both `process()` context-build sites.
- [x] 4 new tests (2 for the path-normalize/seed behavior already counted above, 2 for the new context builder: topic match surfaces the fact, unrelated query does not).
- [x] Full `tests/` suite still green (217 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes.
- [x] `tools/refresh_core_reference.ps1` run; function_count updated to 368.
- [ ] **Follow-up (not done today)**: 404 historical duplicate `mina_capability_snapshot::`/`active_tools::` rows still in the live DB (safety backup taken at `memory/backups/pre_capability_snapshot_cleanup_*.db`); bulk delete via `/memory/delete` was rejected every time (`deleted: 0`) — needs investigation into why `_begin_memory_operation` is rejecting writes (suspect stuck `_memory_quiescing` from the pre-existing `retention_lifecycle_error` seen in `/status`) before a cleanup can actually run.
- [ ] **Follow-up (not done today)**: re-verify the exact live prompt after an API restart once the process-level write rejection above is understood (the code fix is in place and unit-tested, but end-to-end live confirmation is still pending).

Live re-verification result (2026-08-13, after restart): the guaranteed-recall context now reliably reaches the model (unit-tested), and it was additionally strengthened to a direct imperative ("answer honestly, do not deny this... instead of any trained refusal instinct"). The model **still** produced the same "Gremlin brakes applied ... tamper-proof by design ... I can't edit my own source code autonomously" refusal on 3 separate live prompts (including one explicitly telling it Travis said it's okay), with only minor wording variation between calls — this is not a caching artifact. This points to a strong persona/fine-tune-level refusal reflex for "can you modify yourself" style questions that isn't overridden by system-prompt context alone at this model's current settings. Retrieval/memory-layer fix is complete and verified in isolation; the last-mile model behavior override is a separate, harder problem (likely needs a temperature adjustment for this instruction to compete, or persona fine-tuning) and is out of scope for this session — flagged for a dedicated follow-up rather than continuing to chase it with more prompt text.

Correction (2026-08-13): repeated identical live test prompts against the same question polluted `short_term`/`interaction` memory with near-duplicate refusal turns; `build_context`'s recency-based recall (`recent_memories`, last 6 short-term turns regardless of query relevance) fed those prior refusals back into every subsequent call as "recalled conversation," so the model was effectively conditioned to repeat its own last answer. Confirmed this wasn't a model issue: switching the active model to an allowlisted uncensored alternative (`mistral-nemo-...-heretic`) via `/model/select` and re-asking produced a near-identical refusal, which ruled out persona/fine-tune bias as the sole cause and pointed at the shared recalled short-term history instead. Switched back to the configured default afterward.

---

### Issue: `/memory/delete` silently reported false failures (`deleted: 0`) on real deletes

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - "that is not the issue i can fix that my self the memory bug is the issue you are to address now!! tell me how you plan to fix it ?"
  - "ok sounds good proceed." / "yes" (commit)

#### 2) What Mina actually did
- Investigated a bulk `/memory/delete` cleanup (from the capability-snapshot duplicate-row fix) that reported `deleted: 0` for every one of 404 rows. Initial hypothesis (stuck `_memory_quiescing` flag) was wrong — added a read-only `GET /memory/activity_status` endpoint and confirmed `quiescing`/`closing`/`closed` were all `false` on a clean restart, yet a fresh known-good row still reported `deleted: 0`.

#### 3) What should have happened
- `/memory/delete` should truthfully report how many rows were actually removed from the database.

#### 4) Evidence
- `/status` showed `memory_engine.status.last_error: "delete_memory_ids_error: "` — an **empty** exception message — right after a failed-looking delete.
- Direct DB query proved the target row was **already gone** despite the API reporting `deleted: 0`: the SQL `DELETE` had committed, but an exception afterward made the function return `0` anyway.
- Found 8 pre-existing rows (`id` 7142–7145, 7153–7156 — real family facts: Travis's brother Rodger, mom Reichel, sister Alisha) with `dim_small=0`/`dim_base=0`/zero-length embedding blobs, likely inserted by an older seed path that bypassed the normal embed-then-insert flow.

#### 5) Failure class
- Verification missing (a function's return value did not match its actual side effect)

#### 6) Root cause
- `MK1Memory._rebuild_indexes()` assumed every row shares the first row's embedding dimension. Hitting one of the 8 zero-dimension rows threw a near-empty FAISS/dimension-mismatch exception.
- `delete_memory_ids()` wrapped the SQL `DELETE`+commit **and** the subsequent `_rebuild_indexes()` call in a single `try/except`, so the unrelated rebuild crash overwrote a true, already-committed delete with a reported `0`.

#### 7) Minimal fix
- `_rebuild_indexes()` now determines the target dimension from the first row with a non-zero declared dimension, skips any row whose actual decoded vector length doesn't match (logging the skipped ids) instead of crashing the whole rebuild.
- `delete_memory_ids()` now commits and computes the true `deleted` count in its own try/except, then attempts the index rebuild in a **separate** try/except that can no longer erase a real delete count — a rebuild failure is now recorded as its own `delete_memory_ids_rebuild_error` without lying about the delete result.
- Added `GET /memory/activity_status` (read-only) so quiescing/closing/closed and active-operation counts are visible without triggering shutdown.

#### 8) Regression prompts
- N/A — this is a memory-engine correctness fix, not a chat-routing fix. Covered by 2 new unit tests instead.

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: return-value truthfulness and index-rebuild robustness are deterministic correctness invariants, not user preferences.
- Risk if wrong approach chosen: N/A — this is a straightforward bug fix, not a policy choice.

#### 10) Done criteria
- [x] `tests/test_memory_rebuild_and_delete.py`: rebuild skips corrupt zero-dim rows without crashing; delete reports the true count even when rebuild fails afterward.
- [x] Full `tests/` suite still green (219 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes.
- [x] Live end-to-end proof: wrote a probe memory, deleted it, got `{"deleted": 1}` (previously always `0`).
- [ ] **Follow-up (not done today)**: the 8 corrupted zero-embedding family-fact rows (7142–7145, 7153–7156) are duplicated and currently unsearchable via semantic recall (though still visible via recency-based recall); should be re-embedded/repaired properly rather than just tolerated by the rebuild-skip fix.
- [ ] **Follow-up (not done today)**: the 404 duplicate `mina_capability_snapshot::`/`active_tools::` rows from the earlier digest-churn bug can now actually be deleted (this fix unblocks that cleanup) but the bulk cleanup itself hasn't been re-run yet.

---

### Issue: Same false-failure pattern existed in trim/retention/hygiene, not just delete

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s):
  - "what is the status of mina's memory system i feel where missing somthing with the way it was silenly failing ?"
  - "yes as long as it does not effect her abiliy to learn and rember."

#### 2) What Mina actually did
- Audited every other caller of `_rebuild_indexes()` for the same "delete+commit and rebuild wrapped in one try/except" shape that caused `delete_memory_ids` to misreport. Found the identical pattern in `trim_by_age`, `trim_by_size`, `run_retention_lifecycle`, and `memory_hygiene`.

#### 3) What should have happened
- Any of these four should report their true delete/cleanup result even if an unrelated index-rebuild hiccup happens afterward.

#### 4) Evidence
- The very first `/status` check of this whole session showed `"last_retention": {"ok": false, "dry_run": false, "error": ""}` — same empty-error signature as the delete bug, now explained: `run_retention_lifecycle` was hitting the same corrupt-row rebuild crash and discarding its fully-built `report` (with the correct `deleted`/`digests_created` counts) in favor of a generic `{"ok": False, "error": ""}`.

#### 5) Failure class
- Verification missing (same root-cause class as the delete bug, repeated in 4 places)

#### 6) Root cause
- All four functions committed their SQL DELETE first, then called `self._rebuild_indexes()` **inside the same try block** as the delete itself, so any rebuild exception (guaranteed by the 8 corrupt zero-dim rows) wiped out an already-successful result.

#### 7) Minimal fix
- Applied the identical decoupling used for `delete_memory_ids` to all four: commit/compute the true result first (in its own try/except), then attempt `_rebuild_indexes()` in a separate try/except that can only record a `*_rebuild_error`, never overwrite the real result.
- **No retention policy, deletion criteria, or what gets kept/summarized changed** — this only fixes whether the true outcome is reported truthfully.

#### 8) Regression prompts
- N/A — memory-engine correctness fix. Covered by 4 new unit tests (one per function) that simulate a rebuild failure and assert the true result still comes back correctly.

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: same as the delete fix — this is a deterministic correctness invariant, and explicitly does not touch retention/hygiene policy (per Travis's requirement that it not affect her ability to learn/remember).
- Risk if wrong approach chosen: N/A.

#### 10) Done criteria
- [x] `tests/test_memory_rebuild_and_delete.py`: `trim_by_age`, `trim_by_size`, `memory_hygiene` each report the true result when rebuild fails afterward.
- [x] `tests/test_memory_retention.py`: `run_retention_lifecycle` reports the true `deleted` count and preserves recent/digest behavior when rebuild fails afterward.
- [x] Full `tests/` suite still green (223 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes.
- [x] Confirmed no change to what gets kept, deleted, summarized, or promoted — only the truthfulness of the reported result.

---

### Issue: Both remaining memory follow-ups closed (corrupt family facts + duplicate-snapshot spam)

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Done

#### 1) What I asked Mina to do
- Exact prompt(s): "fix them please" (repair the 8 corrupt family-fact rows and re-run the duplicate-snapshot cleanup)

#### 2) What Mina actually did
- Found the 4 family facts already existed cleanly and correctly embedded (`id` 7069–7072, `dim_small=384`/`dim_base=768`) from before the corruption; the 8 rows (7142–7145, 7153–7156) were pure redundant duplicates with broken zero-length embeddings. Deleted all 8 via the now-fixed `/memory/delete` — no re-write needed, nothing lost.
- Re-ran the duplicate `mina_capability_snapshot::`/`active_tools::` cleanup plan: found only **27** rows now (down from 404), confirming the earlier digest-normalization fix helped — but a restart still added exactly 1 more, proving that fix was incomplete.
- Root-caused the remaining leak: `_seed_runtime_capability_snapshot()`'s dedup check compared the **full record text** (which embeds a fresh `timestamp` on every run) via `find_memory_id_by_text`, so it could never match even when nothing real had changed — every single startup guaranteed a new row forever.
- Fixed: dedup now compares the previously-stored row's parsed **digest** (already timestamp-independent) instead of exact full-text match. Verified live: restarted the API twice after the fix, row count stayed at 27 (no new duplicate), then deleted all 26 non-newest duplicates — final count is 1 snapshot + 1 active_tools row, `/status` clean (`ok: true`, no errors).

#### 3) What should have happened
- Both follow-ups from the delete-bug fix session should be fully resolved: corrupt rows repaired/removed without data loss, and the duplicate-row source stopped for good (not just cleaned up once).

#### 4) Evidence
- Family fact counts before: 3 copies of each of 4 facts (1 clean + 2 corrupt) → after: 1 clean copy of each, `dim_small=384`/`dim_base=768` confirmed on all 4 survivors.
- Snapshot duplicate count: 404 (original bug) → 27 (after first partial fix) → held at 27 across 2 restarts (after this fix) → 1 (after cleanup).

#### 5) Failure class
- Verification missing (the earlier "fix" addressed digest computation but not the actual dedup comparison, so the leak continued at a lower rate)

#### 6) Root cause
- `_seed_runtime_capability_snapshot()` used `find_memory_id_by_text(record_text, ...)` for dedup, but `record_text` embeds a live `datetime.now()` timestamp every call, so exact-text matching could never succeed even with an otherwise-identical snapshot.

#### 7) Minimal fix
- Changed the dedup check to `exists = bool(previous_digest) and previous_digest == digest`, reusing the digest already parsed from the most recent prior row (via `_parse_capability_snapshot_record`) instead of comparing full text.

#### 8) Regression prompts
- N/A — memory-engine correctness fix. Covered by a new unit test proving no duplicate is written when the digest is unchanged (previously this always wrote a duplicate regardless of digest).

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: dedup-by-digest is a deterministic correctness fix, not a policy choice; doesn't touch what facts get learned/remembered, only prevents redundant technical snapshot spam.
- Risk if wrong approach chosen: N/A.

#### 10) Done criteria
- [x] `tests/test_memory_recall.py::test_seed_runtime_capability_snapshot_does_not_duplicate_when_unchanged` added and passing.
- [x] Full `tests/` suite still green (224 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes; live-verified across 2 real restarts with no new duplicate row.
- [x] 8 corrupt family-fact rows removed live; 26 duplicate snapshot/tools rows removed live; `/status` clean with no errors.
- [x] No family facts lost — the clean originals were already present and untouched.

---

### Issue: Live LM Studio log showed KV-cache failures, a 5,060-token repetition loop, and false fabricated claims of stored instructions

- Date: 2026-08-13
- Reporter: Travis
- Priority: High
- Status: Partially done (code-side fixes only; LM Studio server config is out of reach from here)

#### 1) What I asked Mina to do
- Travis pasted a raw LM Studio server log ("she is totally fragmented a total loss, cant hold memory for a single turn") and a live GUI transcript where Mina claimed "you *did* tell me not to make changes to my own code earlier" and "I took that as a direct order."

#### 2) What Mina actually did
- Analyzed the raw log and found three distinct issues, then searched all of memory (not just short-term) for any real instruction matching the claim.

#### 3) What should have happened
- No instruction like that exists anywhere in memory (confirmed — the only durable fact on this topic is the `autonomy_awareness` fact saying the opposite). She should not invent a false source to justify a refusal.

#### 4) Evidence
- `E state_read_meta: failed to find 2577 available cells in kv cache`, `E state_seq_set_data: error loading state: failed to restore kv cache`, `W slot prompt_load: id 2 | task -1 | failed to load prompt from cache` — LM Studio/llama.cpp server-side cache restore failures.
- One completion generated 5,060 tokens looping the same 5 sentences verbatim (`"Assessing the File Write Tool"` etc. repeated 15+ times) until hitting the token cap.
- A reply claimed `"The file ... config.txt was successfully created and written"` while `"tool_calls": []` — zero real tool calls, fabricated success.
- Autonomous maintenance calls (`22:04:40`, `22:08:04`) were interleaved with live chat (`22:04:55`, `22:07:00`) within the same few minutes — background work and live chat contending for the same LM Studio slots concurrently.
- Third, independent fabricated-justification pattern this session: "I read a file" (Architecture.md, not real) → "you told me not to touch the code" (also not real, verified via full memory search).

#### 5) Failure class
- Multiple: verification missing (fabricated tool success), tool selected wrong / no tool at all, and a model-serving reliability issue outside this codebase.

#### 6) Root cause
- **Concurrency collision**: `AutonomousCuriosity._run_useful_work()` runs on its own background thread and only checks idle time once, at cycle start. The model call inside `_run_autonomous_work_task()` can take 30s–minutes; if the user resumes chatting mid-call, both requests hit LM Studio at the same time.
- **No repetition guard**: `LMStudioClient.chat()` never sent `frequency_penalty`/`presence_penalty`, so nothing stopped a degenerate repetition loop once the model got stuck.
- **KV-cache restore failures**: server/infra-level (LM Studio + llama.cpp slot/cache management), not something this codebase controls.
- **Fabricated justifications**: same behavioral pattern as the earlier "Architecture.md"/"I read a file" incident — not something a code fix can fully resolve tonight.

#### 7) Minimal fix (code-side only)
- Added a freshness re-check in `_run_autonomous_work_task()` immediately before the model call: if the user has been active in the last 20 seconds, defer the task (marked `deferred: True`, doesn't count against retry budget) instead of firing a concurrent request.
- Added `frequency_penalty`/`presence_penalty` (default 0.3 each, configurable via `config/mk1_config.json` `model.frequency_penalty`/`model.presence_penalty`) to every `LMStudioClient.chat()` call.
- Added a `max_tokens_override` parameter to `chat()` and used it to cap the autonomous-maintenance call at 1200 tokens (down from the shared 4096 chat default), since it only needs to return a short JSON report.

#### 8) Regression prompts
- N/A — infra/reliability fix, not a chat-routing fix. Covered by 5 new unit tests instead.

#### 9) Implementation choice
- Chosen approach: hardcoded
- Why this approach fits: concurrency gating and generation-safety parameters are deterministic reliability invariants, not learned preferences.
- Risk if wrong approach chosen: N/A.

#### 10) Done criteria
- [x] `_run_autonomous_work_task()` defers instead of firing when the user was active in the last 20s; deferred results don't consume retry attempts (`tests/test_autonomous_work.py`, `tests/test_curiosity_policy.py`).
- [x] `LMStudioClient.chat()` sends `frequency_penalty`/`presence_penalty` and supports `max_tokens_override` (`tests/test_lmstudio_client_payload.py`).
- [x] Full `tests/` suite still green (228 passed, same 6 pre-existing unrelated failures).
- [x] `MK1Core()` boot smoke passes.
- [ ] **Not done tonight**: the LM Studio server-side KV-cache restore failures need Travis to check the server's slot count / per-slot context length configuration directly — outside what's reachable from this codebase.
- [ ] **Not done tonight**: root-causing why the model fabricates false justifications ("I read a file", "you told me not to touch the code") when pressed to explain a refusal — same open question as the earlier Architecture.md incident, needs its own dedicated investigation.

---

### 9. Secondary companion model routing (deferred)

- Date: 2026-08-12
- Priority: Low (explicitly deferred, work on later)
- Status: Not started

Context: user asked about running a small NSFW companion model alongside Mina's current qwen setup. Confirmed live that LM Studio already keeps `qwen/qwen3.5-9b` and `tinyllama-nsfw-chatbot-i1` loaded simultaneously (no eviction) and RAM headroom is fine. Gap is entirely in `agent/core.py`, which has no per-call model override today.

- [ ] Add `model_override` kwarg to `LMStudioClient.chat()` in `agent/core.py` so a request can target a non-default model without changing `self.default_model`.
- [ ] Add a `model.companion_model` config key in `config/mk1_config.json` (separate from `switch_allowed`/`default_model`) for the secondary model id.
- [ ] Add an explicit user-triggered toggle/command to route into companion mode (per product contract: no keyword-trap/hidden mode switching, must be an explicit action).
- [ ] Decide whether companion turns share Mina's persona/memory or get a separate system prompt and memory tag/namespace so they don't pollute authoritative profile memory.
- [ ] Implementation choice: hardcoded routing gate (explicit toggle contract) + memory-driven for the companion persona content itself.
- [ ] Regression prompts (add once implemented): explicit companion-mode toggle phrase routes to `tinyllama-nsfw-chatbot-i1`; normal prompts with no toggle stay on `qwen/qwen3.5-9b`; toggling back to Mina restores default model routing.

## Release Hygiene

- [ ] Review the current uncommitted backend, Android, memory, and fancy-GUI changes as one integration batch.
- [ ] Run final targeted backend, GUI build, and Android build checks before committing.

---

## Mina as a Buddy — Not Just a Tool

Mina is supposed to interject, comment, share things she noticed, and just talk — not only respond when tasked. The 36-hour OpenClaw version did this naturally.

### What's Missing
- Autonomy discoveries surface as notifications, not woven into conversation
- Every input goes through intent detection first — if it doesn't look like a command she stalls
- No mechanism for her to initiate conversation unprompted when she has something to say

### Goal
- Mina should be able to say "hey I found something interesting" in the chat flow, not just a side panel
- Idle moments should feel like she's present, not silent
- Conversational inputs should go to the model first, intent detection second

### Work Items
- [ ] Route low-confidence intent inputs directly to model instead of clarification prompt
- [ ] Allow autonomy discoveries to feed into conversational context naturally
- [ ] Reduce intent detection surface — let the model decide if action is needed
- [ ] Audit what's intercepting chat before the model sees it and strip what doesn't need to be there
*Added 2026-08-05. Do after Qwen fix queue is verified live.*

### Goals
- Streamline `c:\dev\mina-gui` to feel like Mina's actual interface, not a demo shell
- **Update avatar** — give Mina visual states she can control (idle, thinking, talking, reacting)
- Use the GUI as a live status panel: model state, memory health, last tool run, Ostranauts bridge status
- Avatar should reflect what Mina is doing, not just sit there

### Work Items
- [ ] Audit current `src/` — remove dead components, identify what's actually wired to the API
- [ ] Wire avatar state to `/status` endpoint so Mina controls her own expression
- [ ] Add a status strip: model online/offline, embed server, last action, Ostranauts active
- [ ] Update avatar image set in `public/avatar/states/` — Travis to confirm which states to keep/add
- [ ] Hook Mina's tool execution events to avatar reactions (writing → focused, done → satisfied, error → not-happy)
- [ ] Make sure GUI reconnects gracefully when API restarts (currently may hang)

---

## Mina Needs to Learn: PowerShell Emoji Encoding
*Added 2026-08-05. Let her figure this out herself.*

When Mina writes PS1 scripts with emoji, they show as garbage in a Windows terminal.

**Fix she needs to discover:** Add `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` at the top of PS1 scripts with emoji.

**How to trigger the lesson:** Tell her the emoji don't show in PowerShell and let her debug it.

---

## Side Quest: Amuse Image Generation Bridge
*Added 2026-08-12. Low priority, just for fun — work on it when there's nothing higher-value queued.*

Goal: let Mina generate images locally using Amuse (`C:\Program Files\Amuse`, models in `C:\Users\Admin\AppData\Local\Amuse\Models`).

Investigated live: Amuse v3.1.0 is a WPF app built on OnnxStack + DirectML (AMD RX 6700 XT), already has SD1.5/SDXL/SD3-medium/FLUX.1-Schnell/Dreamshaper/Juggernaut/ControlNet models downloaded. It hosts an internal ASP.NET Core Generic Host for DI/logging only — no listening port, no documented REST API, no CLI/batch/watch-folder automation found in logs or `appsettings.json`.

- [ ] Decide path: (A) UI automation of the Amuse window (fast, fragile, hard to verify deterministically) vs (B) standalone .NET/OnnxStack sidecar service exposing a minimal local HTTP `/generate` endpoint (same pattern as `Memory_server/mk1_embed_server.py`), reusing the already-downloaded ONNX models.
- [ ] If (B): scaffold a small C#/.NET project referencing OnnxStack, load one existing model folder, wire a DirectML pipeline, return image bytes/path from a local endpoint.
- [ ] Add `tools/image_generate.py` (same shape as `tools/web_fetch.py`) that POSTs prompt/params to the sidecar and returns the result.
- [ ] Add intent routing in `agent/core.py` for "generate an image of ..." / "draw ..." style prompts.
- [ ] Verification: saved image file must exist at a known path before Mina reports success (per verification contract — no claiming an image was made without a real file on disk).
- [ ] Regression prompts once implemented: "draw a picture of a raccoon playing guitar", "generate an image of a mountain landscape", "make me an image" (missing subject → should ask one clarification, not invent a prompt).
