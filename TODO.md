
Stop The Churn Spec v1
Owner intent: Mina must work for human phrasing first, not for internal intent labels.

1) Non-Negotiable Product Contract

Natural language first.
No magic phrasing required.
No hidden mode switches from keyword traps.
If uncertain, ask one specific clarification question.
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

Last updated: 2026-07-30

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

## Release Hygiene

- [ ] Review the current uncommitted backend, Android, memory, and fancy-GUI changes as one integration batch.
- [ ] Run final targeted backend, GUI build, and Android build checks before committing.
