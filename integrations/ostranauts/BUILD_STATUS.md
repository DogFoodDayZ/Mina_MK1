# Mina Ostranauts Companion - Build Status

Last updated: 2026-07-28

This file is the restart point for future work. Read it before inspecting code or proposing UI changes.

## Goal

Build a read-only Mina companion for Ostranauts that presents verified live game state and lets Mina answer grounded questions without changing the game, saves, display settings, or running processes.

## Current build

### Game bridge

- Ostranauts: `0.15.1.20`
- Unity: `6000.3.10f1`
- BepInEx: `5.4.23.5` / Thunderstore pack `5.4.2305`
- Live Mina plugin: `0.6.0`
- Live telemetry schema: `6`
- Deployed plugin DLL SHA-256: `c883cf3e0d3cacfd3c99fe01d14c353dd0a0d81403f4e3497eb9949755aec97a`
- Plugin source: `integrations/ostranauts/MinaOstranauts/`
- Live file: `G:\SteamLibrary\steamapps\common\Ostranauts\BepInEx\config\MinaOstranauts\telemetry.json`
- API endpoint: `GET /games/ostranauts/status`

The bridge is read-only. It does not patch game methods, execute console commands, edit saves, or mutate game state.

### Verified live telemetry

The schema currently reports:

- game, bridge, scene, focus, and session state
- current player
- entries returned by the game's crew collection
- current player-owned/loaded ship
- character conditions and inventory when exposed
- objectives
- ship fuel, damage, docking, navigation, and warning fields when exposed

Verified during backup #3:

- player: Blake Blair
- vessel: It's Not A Pejorative (`O-741L`, Testudo Mesa Mk. I)
- Luke Arias and Redeem Griffin are corpses, not living crew
- telemetry diagnostics were empty after the v0.3 static-field fix
- backend context now classifies explicit `alive`/`destroyed` values as living, deceased/remains, destroyed, or unknown
- live API verification reported Blake as living and Luke/Redeem as deceased/remains with empty diagnostics

### Interpretation rules

- Presence in `CrewSim.aCrew` does not mean living crew.
- Separate the player, living crew, deceased/remains, and unknown-state characters.
- Never infer life state, role, location, health, inventory, or relationship from a name or collection membership.
- Preserve raw telemetry facts and label unavailable fields honestly.
- Do not present raw condition arrays as a useful status panel. Conditions need verified classification and human-readable labels first.

### Desktop GUI ownership

The active desktop GUI is the Tauri app in `C:\dev\mina-gui`.

- Base GUI: `C:\dev\mina-gui\src\App.jsx`
- Companion component: `C:\dev\mina-gui\src\components\OstranautsCompanion.jsx`
- Companion styles: `C:\dev\mina-gui\src\components\OstranautsCompanion.css`
- Tauri window creation: `C:\dev\mina-gui\src-tauri\src\lib.rs`
- Companion route: `index.html?view=ostranauts`
- Window title: `Mina // Ostranauts Companion`

The `OPEN OSTRANAUTS COMPANION` button in Mina's base GUI opens/toggles the second Tauri window. Both Mina windows can coexist with Ostranauts and switch normally.

Do not treat `agent/gui/mina_gui.py` or the root `mina_gui.py` shim as the active interface.

## Completed

- [x] Discovered the game and Steam library.
- [x] Installed BepInEx and the read-only Mina bridge.
- [x] Added the companion Tauri window and base-GUI toggle.
- [x] Added objective telemetry.
- [x] Fixed player, crew-collection, and current-ship discovery by reading static `CrewSim` fields correctly.
- [x] Built and deployed plugin v0.3.0/schema 3.
- [x] Built and deployed plugin v0.4.0/schema 4.
- [x] Built and installed plugin v0.5.0/schema 5 with `ship_layout`, `ship_systems`, and `nearby_interactables` telemetry capture (pending next-launch live verification).
- [x] Verified player, corpse entries, current vessel, and empty diagnostics in a live save.
- [x] Added conservative backend life-state formatting from explicit telemetry booleans.
- [x] Focused backend tests passed: `tests/test_ostranauts.py` (6 tests at last verification).
- [x] Prepared and compiled bridge v0.4/schema 4 with condition counts, truncation reporting, a 128-entry bound, and game-defined condition display metadata.
- [x] Added backend `source_freshness` metadata for telemetry/source provenance and freshness checks, with focused regression coverage in `tests/test_ostranauts.py`.
- [x] Added plugin-side interface scan metadata (component type, game object, hierarchy path, token-hit scoring) for `nearby_interactables`, `ui_candidates`, `open_ui`, and `pda_devices`; plugin compiles successfully and awaits next-launch live verification.
- [x] Reduced broadened-scan hitch risk by caching and reusing heavy UI/PDA world scans in `TelemetryReader` instead of rescanning every telemetry write.
- [x] Added sparse-scene fallback seeding for kiosk/people discovery (`likely_target_fallback` and `likely_ui_fallback`) so interaction lists are not empty when direct arrays are missing.
- [x] Deployed latest compiled plugin DLL to live game plugin path after-hours.

## Current design stage

No new companion layout has been approved. Do not replace the existing center conversation area or introduce tabs/panels until the desired information hierarchy is agreed with the user.

Reference directions are documented in `MOD_REFERENCES.md`. Installed base-game item findings are documented in `ITEM_DATA_REFERENCE.md`.

## Next work

1. Validate the structured Ostranauts action queue against a live save once the next game session is running.
2. Classify live conditions using the game's display flags and metadata, with CharacterSidebar as the verified reference.
3. Identify which conditions represent learned skills, training progress, traits, needs, mood, injuries, and transient internal mechanics.
4. Locate gameplay item definitions and join them to the verified `strName` visual definitions.
5. Decide with the user what belongs in the existing companion layout before editing it.
6. Add focused telemetry tests for condition normalization.
7. Implement the smallest approved GUI slice and validate it in the actual Tauri companion route.
8. Expand source/freshness metadata beyond telemetry (save files, logs, configuration, installed mods).
9. Validate sparse-scene fallback live at kiosks/people clusters and tune candidate scoring thresholds if false positives dominate.

## Safety and change control

- Do not launch, close, restart, move, or reconfigure Ostranauts without explicit permission.
- Do not change monitors, display modes, registry values, overlays, power plans, or running processes without explicit permission.
- Do not install or download mods for inspection without explicit permission.
- Do not add console execution or game-changing actions to Mina's bridge.
- Inspect public documentation/source first; use downloaded packages only when documentation is insufficient and the user approves.
- Before GUI edits, confirm the exact component and intended behavior with the user.

## Known environment findings

- Disabling the Razer in-game/FPS overlay improved the earlier display problem.
- Mina's base and companion Tauri windows are not themselves reproducing the monitor-switch issue.
- The game still has Steam and AMD rendering modules, and Razer Cortex may still switch power plans.
- These display findings are separate from companion feature development.
