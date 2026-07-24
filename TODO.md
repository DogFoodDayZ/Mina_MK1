# TODO

Last updated: 2026-07-24

- [ ] Wire GUI alarm ringing in C:/dev/mina-gui:
  - Detect due alarm tasks from `/tasks/list` using `alarm`, `beep`, and `due` fields.
  - Call `POST /tasks/ring_due_alarm` when a new due alarm appears.
  - Add a short cooldown/debounce to avoid repeated beeps every poll cycle.
- [ ] Show recurrence metadata in GUI task cards (`repeat_minutes` / `repeat_seconds`).
- [ ] Optional: add per-task toggle in GUI for auto-ring alarms.
- [ ] Optional: add API tests for alarm ring endpoint and recurring task tags.
- [ ] Tune long-thread memory recall behavior (without changing persistence): increase recent window + semantic recall breadth so Mina feels more aware of older context in ongoing conversations.
- [ ] Add archive drill-down for file inspector:
  - Support commands like `list files inside archive.zip` and `read readme.txt inside archive.zip`.
  - Keep reads grounded with entry-size limits and safe path handling.
- [ ] Add scoped read-only SQLite query support:
  - Support prompts like `show 20 rows from people where active=1` with strict read-only safeguards.
  - Keep table preview as the default safer path.
- [ ] Expand binary/media metadata support:
  - Add better MP3/MP4 metadata (duration, bitrate, codec when available).
  - Add image EXIF/basic photo metadata and stronger PDF extraction when feasible.
- [ ] Add guardrails and regression coverage for file inspector:
  - Add automated tests for UTF-16, CP1252, SQLite, ZIP, PDF, PNG, WAV, MP3, MP4, and directory filter flows.
  - Add clearer size/depth limits and partial-read messaging for huge files, huge archives, and large directory trees.
