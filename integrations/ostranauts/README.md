# Mina Ostranauts Companion

Read-only BepInEx bridge for Mina's Ostranauts companion view.

## Project continuity

- Current implementation, verified facts, decisions, and restart instructions: [BUILD_STATUS.md](BUILD_STATUS.md)
- External mod references and review policy: [MOD_REFERENCES.md](MOD_REFERENCES.md)
- Installed base-game item data findings: [ITEM_DATA_REFERENCE.md](ITEM_DATA_REFERENCE.md)

Read both files before proposing telemetry or GUI changes.

## Installed layout

- Game: `G:\SteamLibrary\steamapps\common\Ostranauts`
- Loader: BepInExPack Ostranauts 5.4.2305
- Plugin: `BepInEx\plugins\MinaOstranauts.dll`
- Telemetry: `BepInEx\config\MinaOstranauts\telemetry.json`
- Mina API: `GET /games/ostranauts/status`

Bridge v0.6.1 reports loader health, game and Unity versions, active scene, session uptime, objectives, and read-only player, crew, and ship state when the game exposes it. It also feeds structured Ostranauts action drafts into Mina's queue API for later review, and adds opportunistic `ship_layout`, `ship_systems`, and `nearby_interactables` telemetry blocks when runtime fields are available. Expensive scene-wide fallback sweeps for UI/device discovery are now disabled by default so routine snapshots stay anchored to direct runtime references. It does not patch methods, issue commands, edit saves, or mutate game state.

## Build and install

```powershell
.\integrations\ostranauts\build_plugin.ps1
.\integrations\ostranauts\install_companion.ps1
```

The installer refuses to run while Ostranauts is open and records package and plugin hashes in `install_record.json`.

## Remove

Close Ostranauts, then remove `BepInEx\plugins\MinaOstranauts.dll`. Removing the whole BepInEx installation also requires deleting the top-level Doorstop files and `BepInEx` directory; keep that as a deliberate manual operation so other installed mods are not removed accidentally.

## Next telemetry phase

Classify character life state and raw conditions into verified, human-readable groups before adding them to the GUI. Unknown or version-mismatched fields must remain unavailable rather than inferred.

## Next control phase

Use the structured action envelope in [ACTION_SCHEMA.md](ACTION_SCHEMA.md) as the only handoff between Mina's planning layer and any future in-game command bridge.