# Ostranauts Base-Game Item Data

Last inspected: 2026-07-25
Game version: `0.15.1.20`

## Installed path

`G:\SteamLibrary\steamapps\common\Ostranauts\Ostranauts_Data\StreamingAssets\data\items\`

This path was inspected read-only. It currently contains:

| File | Bytes |
| --- | ---: |
| `items.json` | 827,634 |
| `items_chargers.json` | 27,597 |
| `items_mining.json` | 37,728 |

All three files parsed successfully as top-level JSON arrays containing 1,027 total entries.

## Verified schema at this layer

Every observed entry contains:

- `strName`
- `strImg`
- `strImgNorm`
- `nCols`
- `aSocketAdds`
- `aSocketForbids`
- `aSocketReqs`

Common optional fields include:

- `strDmgColor`
- `strImgDamaged`
- `fZScale`
- `aLights`
- damage rendering fields such as `fDmgComplexity`, `nDmgMode`, `fDmgTrim`, and `fDmgCut`
- sprite-sheet and shadow-box fields

## Identifier finding

For this installed game build, the item files at this exact path are sprite/visual definitions keyed by `strName`.

Across all 1,027 entries:

- entries with `strName`: 1,027
- entries with `CodeDef`: 0
- entries with `Name`: 0
- entries with `Category`: 0
- entries with `Mass`: 0
- entries with `BasePrice`: 0

Example identifiers include `ItmArcadeGame01`, `ItmToolDrillMining01`, and `ItmChargerBattDrill01`.

The supplied `CodeDef`/`Name`/`Category`/`Mass`/`BasePrice` schema may exist in another Ostranauts data layer, game version, modding export, or condition-owner definition set. Do not assume these sprite files contain gameplay economics or mass. Locate and cross-reference the authoritative gameplay-definition source before building item lookup.

A follow-up search covered all 440 JSON files (178.36 MB) under this installed build's `StreamingAssets/data` tree:

- `CodeDef`: no exact JSON key matches
- `BasePrice`: no exact JSON key matches
- `Category`: no exact JSON key matches
- `Mass`: one localization-text match in `strings/strings.json`, not a gameplay field

This rules out those exact keys in the installed base-game JSON data. They likely describe a different game version, export format, or API representation.

## Companion implications

- `strName` can provide a stable join candidate for images/visual definitions.
- Human-readable names, categories, mass, prices, condition owners, and inventory state require another verified source.
- Mina should retain source path and game version with every indexed definition.
- Duplicate or variant IDs such as off, damaged, full, and loose states must not automatically be treated as distinct player-facing item types.

## Next item-data work

1. Search the remaining `StreamingAssets/data` tree for exact keys `CodeDef`, `BasePrice`, and `Mass`.
2. Identify joins between gameplay definitions, `strName`, condition-owner templates, localization, and inventory telemetry.
3. Compare LaunchControl's `ItemManager`, `ItemCategoryManager`, and `ItemMatcher` behavior with the discovered files.
4. Build a read-only index only after identifiers and joins are verified.
5. Add tests covering duplicate IDs, visual variants, missing localization, and version/source metadata.
