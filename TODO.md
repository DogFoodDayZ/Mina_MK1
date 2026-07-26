# TODO

Last updated: 2026-07-25
## Completed

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

- [x] Discover the installed Ostranauts and Steam library paths without hard-coding drive letters.
- [x] Install the Ostranauts BepInEx pack and deploy Mina's read-only telemetry bridge.
- [x] Add a switchable Ostranauts companion view to the authoritative fancy GUI.
- [ ] Build read-only context from game files, saves, logs, configuration, and installed mods.
- [ ] Let Mina answer voice questions about the current game, crew, ship, inventory, objectives, and mod setup.
- [ ] Add focused Ostranauts knowledge retrieval with source paths and freshness timestamps so Mina does not invent game state.
- [x] Keep the initial bridge read-only with no save edits, console commands, patches, or game-changing actions.
- [ ] Add an easy refresh command for rescanning changed saves, logs, and mods during a play session.

## Release Hygiene

- [ ] Review the current uncommitted backend, Android, memory, and fancy-GUI changes as one integration batch.
- [ ] Run final targeted backend, GUI build, and Android build checks before committing.
