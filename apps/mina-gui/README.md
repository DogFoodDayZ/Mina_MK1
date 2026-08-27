# Mina GUI (New Chat Shell)

The overall Mina architecture, including the Dolphin3 conversational half, shared memory, model switching, and coding-agent handoff, is documented in [ARCHITECTURE.md](../../ARCHITECTURE.md).

## Goal
Create a single desktop window that unifies:
- Live avatar panel (current overlay at http://localhost:4311)
- Voice loop controls + transcript history
- Command console mirror **with interactive input**
- Quick toggles for reminders, mode switches, status indicators
- Independent health indicators for OpenClaw gateway and avatar overlay

## Constraints & Decisions
- **Platform:** Windows 11 host (WSL for helpers)
- **Rendering:** Desktop app using Tauri + React
- **Avatar:** Embed existing overlay via iframe/webview; expose separate health ping
- **Voice Loop:** Control start/stop via existing Windows scripts (mina-windows-loop)
- **Transcript:** Show rolling log from session history/log file
- **Console Pane:** Interactive shell + log mirror (WSL command execution via Tauri)
- **Notes:** Surface `dashboard-notes.md` for reference
- **Scope:** Local-only (no remote control requirement for now)

## Architecture Outline
```
projects/mina-gui/
  src/
    main.ts
    App.tsx
    components/
      StatusPanel.tsx      # Gateway/Avatar health pings
      AvatarPanel.tsx      # iframe + overlay controls
      VoiceControls.tsx
      TranscriptPane.tsx
      ConsolePane.tsx      # Log + interactive input
      NotesPane.tsx
  public/
    icon.png
  package.json
  tauri.conf.json
```

## Next Steps
1. Run `pnpm create tauri-app` inside `projects/mina-gui`.
2. Implement layout scaffold with panes + health widget.
3. Hook AvatarPanel iframe + SSE health check.
4. Wire VoiceControls to PowerShell scripts.
5. Build transcript + console panes.
6. Style to match concept (dark neon).

## Open Questions
- Where should transcript data persist (log file vs. direct session file)?
- Any additional quick toggles needed (e.g., smirk/annoyed states)?
