# TODO

Last updated: 2026-07-18

- [ ] Wire GUI alarm ringing in C:/dev/mina-gui:
  - Detect due alarm tasks from `/tasks/list` using `alarm`, `beep`, and `due` fields.
  - Call `POST /tasks/ring_due_alarm` when a new due alarm appears.
  - Add a short cooldown/debounce to avoid repeated beeps every poll cycle.
- [ ] Show recurrence metadata in GUI task cards (`repeat_minutes` / `repeat_seconds`).
- [ ] Optional: add per-task toggle in GUI for auto-ring alarms.
- [ ] Optional: add API tests for alarm ring endpoint and recurring task tags.
