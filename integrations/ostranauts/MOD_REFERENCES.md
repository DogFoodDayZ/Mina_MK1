# Ostranauts Mod References

Last reviewed: 2026-07-25

These are design and technical references for Mina's read-only companion. Exact package versions were downloaded for offline inspection into `integrations/ostranauts/reference_packages/`. They were not installed into Ostranauts and are not approved dependencies.

## Download record

| Package | Version | Archive SHA-256 |
| --- | --- | --- |
| CharacterSidebar | `0.1.2` | `b3cc06f7b3d756b6e1de60edbc81746a2e61f31a24613002e6952aaac1ef1200` |
| LaunchControl | `1.0.5` | `93b82df3d74fe167008253188c5dde77a3c3d82bfea58331c85106fbc29bd9cd` |
| FFU Console | `0.6.1` | `18aacd81c4e25bcafb197e6a4dc43e3e3a2265c7b4dedaa42b7dd742406f87d7` |

None of the archives bundled C# source or a license file. DLL metadata was inspected with Mono.Cecil without loading or executing mod code.

## CharacterSidebar

- Package: [CraxyMaxy/CharacterSidebar](https://thunderstore.io/c/ostranauts/p/CraxyMaxy/CharacterSidebar/)
- Reviewed version: `0.1.2`
- License: MIT
- Manual DLL: `OstranautsSidebar.dll`
- Config: `BepInEx/config/com.ostranauts.sidebar.cfg`

### Relevant behavior

CharacterSidebar provides a persistent right-side character panel with:

- learned skills
- skills in training with live progress
- polarity-colored traits
- traits/physical conditioning in training
- active needs, mood, and physical conditions
- rising/falling arrows for changing conditions
- descriptions on hover
- a side-tab visibility toggle
- resolution-aware placement above the bottom HUD

### Use for Mina

Use this as the primary presentation reference for translating raw condition telemetry into glanceable character state. The useful pattern is progressive disclosure: concise rows first, descriptions on demand.

Do not copy its assumption that the currently selected character is living crew. Mina must classify player, living crew, corpses/remains, and unknown-state entities before presentation.

### Verified assembly surface

`OstranautsSidebar.dll` contains `CharacterDataReader.ReadPlayer()` and separate snapshot lists for learned skills, training skills, training traits, traits, and active conditions. It also contains helpers for training detection, active-status and need classification, trait polarity, tone, trend, friendly names, descriptions, and name cleanup.

This confirms CharacterSidebar is a strong technical reference for condition classification and presentation. It does not establish Mina's corpse/living classification rules.

### Questions for source/package review

- How are raw condition names classified into skills, training, traits, and active conditions?
- How are display names and descriptions resolved from game data?
- How are rising/falling trends calculated?
- Which conditions are hidden as internal implementation details?
- How does it identify the active character and life state?

## LaunchControl

- Package: [bitMods/LaunchControl](https://thunderstore.io/c/ostranauts/p/bitMods/LaunchControl/)
- Source: [bitMuse-Ostranauts/LaunchControl](https://github.com/bitMuse-Ostranauts/LaunchControl)
- Reviewed version: `1.0.5`

### Relevant systems

LaunchControl is a shared C# toolkit that provides:

- command registration
- MegaTooltip extension panels
- AI pledge/job helpers
- task data persisted through saves
- persistent mod data
- item-category matching
- game-sprite lookup
- `DumpUI` inspection for clicked interface elements

### Use for Mina

LaunchControl is a technical reference for established Ostranauts modding patterns, especially UI inspection, tooltip integration, sprite lookup, and persistent data boundaries.

Mina currently uses a separate external Tauri companion and a read-only BepInEx telemetry bridge. Do not add LaunchControl as a dependency unless a specific approved feature justifies it.

`DumpUI` may be useful during a future, explicitly approved game-UI research session. It is not needed for the current external companion.

### Verified assembly surface

`LaunchControl.dll` contains item-category management and matching, MegaTooltip item/person module registration, sprite and prefab lookup/cache utilities, persistent-data and task-data handlers, and command, interaction, pledge, and JSON patch systems.

The item-category and tooltip surfaces are relevant references. Taking LaunchControl as a dependency would also add patching and command systems, broadening Mina's risk and compatibility surface.

## FFU Console

- Package: [FFU_Group/FFU_Console](https://thunderstore.io/c/ostranauts/p/FFU_Group/FFU_Console/)
- Releases: [FFU-BR releases](https://git.soon-tm.info/warstalker/FFU-BR/-/releases)
- Reviewed version: `0.6.1`

### Relevant inspection capabilities

FFU Console documents useful views into Ostranauts condition structures:

- `getcond [them] *` lists all stats
- recursive parent inspection
- `*coParents` lists condition-owner hierarchy
- `*coRules` lists attached condition rules and stat information
- `*coTickers` lists tickers and timers
- `findcondcos` locates templates by conditions
- `triggerinfo` describes condition-trigger rules

Its package also describes separate core, console, extended, fixes, quality, and superiority assemblies.

### Use for Mina

Use FFU's command descriptions as a map of game concepts and relationships: condition owners, parent chains, rules, tickers, triggers, and templates. This can guide read-only reflection and offline assembly inspection.

Do not execute FFU commands through Mina and do not copy repair, inventory-opening, trigger-firing, superiority, or other game-changing behavior. Mina's bridge remains read-only.

### Verified assembly surface

`Assembly-CSharp.FFU_BR_Console.mm.dll` is a MonoMod patch assembly. Its `patch_ConsoleResolver` includes condition inspection methods such as `KeywordGetCond`, `KeywordFindCondCOs`, and `KeywordCondTrigInfo`, alongside mutating methods such as `KeywordOpenInventory`, `KeywordRepairShip`, and trigger testing.

Condition concepts are useful reference material, but this console patch must not be integrated into Mina's read-only bridge.

## Review/download policy

Public documentation and public source repositories come first. The approved package review is offline and records:

- package URL and exact version
- archive SHA-256
- manifest and dependencies
- DLL names and assembly references
- source/license availability
- relevant types/methods discovered
- whether any code patches or mutates game state

Do not install a reference package into the live Ostranauts directory merely to inspect it.
