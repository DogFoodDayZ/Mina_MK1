# Live Plugin Data Points

Updated: 2026-07-28
Source endpoint: GET /games/ostranauts/status
Plugin version observed: 0.6.0
Schema version observed: 6
Read-only mode: true

## Tonight checkpoint
- In-session open UI observed: `prefabQuadPowerUI(Clone)`
- In-session open UI hierarchy observed: `PlayState/OKLG_MES/ItmDoor01ClosedOn/prefabQuadPowerUI(Clone)`
- In-session selected target observed: `prefabQuadPowerUI(Clone)`
- In-session nearby interactables count observed: 0 (sparse context at sample moment)
- In-session UI candidates count observed: 0 (sparse context at sample moment)
- In-session PDA device entries observed: 24, but all were UI-adjacent candidates and none exposed `pda_id`/filesystem/slots/contacts yet
- Live sample source for PDA candidates after caching update: `device_cached_scan`

## Telemetry top-level fields (live)
- bridge
- capabilities
- crew
- diagnostics
- game_focused
- game_product
- game_version
- interaction_context
- nearby_interactables
- objectives
- player
- plugin_version
- read_only
- scene
- scene_build_index
- schema_version
- session_uptime_seconds
- ship
- ship_layout
- ship_systems
- unity_version
- written_at_unix

## Player fields (live)
- id
- name
- short_name
- type
- alive
- destroyed
- condition_count
- conditions_truncated
- conditions
- inventory

## Crew fields (live)
- crew list present
- each crew member follows the same conservative character summary shape used by player

## Ship fields (live)
- registration_id
- name
- make
- model
- year
- destroyed
- damage_status
- mass
- rcs_fuel
- rcs_fuel_max
- fusion_fuel
- proximity_warning
- track_warning
- docked_ship_count
- primary_docking_port_id
- position
- velocity
- rotation
- grounded
- orbit_locked
- navigation_active

## Ship systems block (live)
Top-level:
- propulsion
- navigation
- structure
- power
- life_support
- note

propulsion:
- rcs_fuel
- rcs_fuel_max
- fusion_fuel
- velocity
- grounded
- orbit_locked
- torch_drive_thrusting
- fusion_thrust_max

navigation:
- active
- position
- rotation
- proximity_warning
- track_warning
- nav_ai_manned
- nav_player_manned
- nav_count

structure:
- destroyed
- damage_status
- mass
- docked_ship_count
- primary_docking_port_id

power:
- reactor_online
- battery_level
- grid_load
- check_power

life_support:
- oxygen_level
- co2_level
- temperature
- pressure
- oxygen_level_atmosphere
- co2_level_atmosphere
- temperature_atmosphere
- pressure_atmosphere

## Ship layout block (live)
Top-level:
- module_count
- dock_port_count
- compartments
- docking_ports
- note

Current observed counts:
- module_count: 11
- dock_port_count: 0

compartments item fields:
- id
- name
- type
- powered
- damaged
- pressurized
- position
- link_count

docking_ports item fields:
- id
- name
- type
- occupied
- enabled
- position

## Nearby interactables block (live)
Top-level:
- count
- items
- note

Current observed:
- items_count: 5

items fields:
- id
- name
- type
- source
- distance
- enabled
- position

Observed in current plugin output:
- component_type
- game_object
- hierarchy_path
- signal_token_hits
- signal_score

## Interaction context block (live)
- interaction_mode
- open_ui
- selected_target
- seat
- submenu_open
- signals
- ui_candidates
- note

Observed in current plugin output:
- open_ui.id
- open_ui.component_type
- open_ui.game_object
- open_ui.hierarchy_path

## PDA devices block (live)
Top-level:
- pda_devices (array)

Observed in current snapshot:
- pda_devices count: 24
- all rows sourced from: device_gameobject_scan
- pda_id present: 0
- filesystem present: 0
- expansion_slots present: 0
- contacts present: 0

Sample names:
- ShipUIBtnPDAClick02
- ShipUIBtnPDAClick01
- PDAObjectivePlotListElement(Clone)
- PDAObjectivePlotContainer(Clone)

Interpretation:
- Current PDA discovery is finding PDA-related UI objects but not yet resolving a concrete PDA data model instance with IDs/filesystem/slots.

Observed in current plugin output:
- component_type
- game_object
- hierarchy_path
- device_token_hits
- device_score

## Objectives block (live)
Current observed:
- total: 24
- open: 13
- done: 11

Sample open objectives:
- Matchmaker
- The Ceres Affair
- Technical Help
- Family Issues
- Caught in a Racket

## Condition calibration summary (live)
- Blake Blair: living, condition_total=128
  - skills=1
  - training=2
  - needs_mood=35
  - injuries_health=14
  - identity_flags=29
  - plot_flags=2
  - unknown=45
- Rachel Wells: living, condition_total=128
  - skills=5
  - needs_mood=38
  - injuries_health=14
  - identity_flags=26
  - unknown=45

## Notes
- The plugin emits opportunistic fields for some subsystems; null/missing means runtime did not expose that metric at sample time.
- This report captures fields observed live in the current session and should be refreshed after major game/plugin updates.
