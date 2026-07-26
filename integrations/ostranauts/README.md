# Mina Ostranauts Companion

Read-only BepInEx bridge for Mina's Ostranauts companion view.

## Installed layout

- Game: `G:\SteamLibrary\steamapps\common\Ostranauts`
- Loader: BepInExPack Ostranauts 5.4.2305
- Plugin: `BepInEx\plugins\MinaOstranauts.dll`
- Telemetry: `BepInEx\config\MinaOstranauts\telemetry.json`
- Mina API: `GET /games/ostranauts/status`

Bridge v0.3 reports loader health, game and Unity versions, active scene, session uptime, objectives, and read-only player, crew, and ship state when the game exposes it. It does not patch methods, issue commands, edit saves, or mutate game state.

## Build and install

```powershell
.\integrations\ostranauts\build_plugin.ps1
.\integrations\ostranauts\install_companion.ps1
```

The installer refuses to run while Ostranauts is open and records package and plugin hashes in `install_record.json`.

## Remove

Close Ostranauts, then remove `BepInEx\plugins\MinaOstranauts.dll`. Removing the whole BepInEx installation also requires deleting the top-level Doorstop files and `BepInEx` directory; keep that as a deliberate manual operation so other installed mods are not removed accidentally.

## Next telemetry phase

Inspect live `Assembly-CSharp.dll` types and add narrowly verified adapters for crew, ship, inventory, conditions, objectives, and docking state. Unknown or version-mismatched fields must remain unavailable rather than inferred.