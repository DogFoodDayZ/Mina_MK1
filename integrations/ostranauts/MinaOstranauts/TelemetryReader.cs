using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Mina.Ostranauts
{
    internal static class TelemetryReader
    {
        private const int MaxCrew = 16;
        private const int MaxConditions = 128;
        private const int MaxInventory = 48;
        private const int MaxObjectives = 24;
        private const int MaxLayoutNodes = 96;
        private const int MaxDockPorts = 24;
        private const int MaxDockedShips = 16;
        private const int MaxInteractables = 48;
        private const int MaxDeviceCandidates = 24;
        private const int MaxDeviceFields = 48;
        private const int MaxDeviceEntries = 24;
        private const int MaxFallbackWorldObjects = 1024;
        private const int MaxSignalFields = 48;
        private const int MaxUiCandidates = 24;
        private const float HeavyScanCacheSeconds = 30f;
        private const bool EnableSceneSweepFallbacks = false;

        private static readonly object ScanCacheLock = new object();
        private static float cachedNearbyScanAt = -999f;
        private static JArray cachedNearbyScan = new JArray();
        private static float cachedUiScanAt = -999f;
        private static JArray cachedUiScan = new JArray();
        private static float cachedUiCandidateScanAt = -999f;
        private static readonly List<object> cachedUiCandidateScan = new List<object>();
        private static float cachedDeviceScanAt = -999f;
        private static readonly List<object> cachedDeviceScan = new List<object>();

        private static readonly string[] InteractionSignalTokens =
        {
            "seat", "chair", "console", "interact", "use", "menu", "panel", "ui",
            "screen", "target", "selected", "focus", "pilot", "helm", "nav", "comm",
            "sensor", "reactor", "battery", "power", "thrust", "vector", "dock", "airlock", "station",
            "talk", "dialog", "dialogue", "conversation", "vendor", "merchant", "trade", "shop", "market",
            "blackmarket", "contact", "spacebook", "pda", "toon", "npc", "character", "hailing", "comms",
            "kiosk", "stall", "store", "storefront", "counter", "booth"
            , "kiosk", "stall", "store", "storefront", "counter", "booth"
        };

        private static readonly string[] DeviceCandidateTokens =
        {
            "pda", "spacebook", "contact", "contacts", "filesystem", "file", "files", "slot", "slots",
            "expansion", "datapad", "terminal", "book", "directory", "mail", "address", "comm", "device",
            "vendor", "merchant", "trade", "blackmarket", "toon", "npc", "dialog", "conversation", "phone",
            "kiosk", "stall", "store", "storefront", "counter", "booth"
            , "kiosk", "stall", "store", "storefront", "counter", "booth"
        };

        private static readonly string[] DeviceFieldTokens =
        {
            "pda", "spacebook", "contact", "contacts", "filesystem", "file", "files", "slot", "slots",
            "expansion", "directory", "mail", "address", "book", "entry", "records", "lookup",
            "vendor", "merchant", "trade", "shop", "deal", "dialog", "conversation", "phone", "hailing", "toon",
            "kiosk", "stall", "store", "storefront", "counter", "booth"
            , "kiosk", "stall", "store", "storefront", "counter", "booth"
        };

        internal static void Populate(JObject snapshot)
        {
            var capabilities = snapshot["capabilities"] as JArray ?? new JArray();
            var diagnostics = new JArray();
            snapshot["capabilities"] = capabilities;
            snapshot["diagnostics"] = diagnostics;

            var crewSimType = FindType("CrewSim");
            var crewSim = ReadStatic(crewSimType, "objInstance");
            if (crewSim == null)
            {
                diagnostics.Add("crew_sim_unavailable");
                return;
            }

            var ship = ReadStatic(crewSimType, "shipPlayerOwned", "shipCurrentLoaded", "jsonShip");
            if (ship == null)
            {
                ship = FindPlayerShip(ReadStatic(crewSimType, "aLoadedShips"));
            }

            var crew = ReadStatic(crewSimType, "aCrew");
            if (Count(crew) == 0 && ship != null)
            {
                crew = ReadMember(ship, "aPeople", "People");
            }

            var player = ReadStatic(crewSimType, "coPlayer") ?? ReadMember(crewSim, "visPlayer");
            if (player == null)
            {
                player = FindPlayerCrew(crew);
            }

            AddSection(snapshot, capabilities, diagnostics, "player", "player_conditions", () =>
                SummarizeCondOwner(player, true));
            AddSection(snapshot, capabilities, diagnostics, "crew", "crew_roster", () =>
                SummarizeCrew(crew));
            AddSection(snapshot, capabilities, diagnostics, "ship", "player_ship", () =>
                SummarizeShip(ship));
            AddSection(snapshot, capabilities, diagnostics, "ship_layout", "ship_layout", () =>
                SummarizeShipLayout(ship));
            AddSection(snapshot, capabilities, diagnostics, "ship_systems", "ship_systems", () =>
                SummarizeShipSystems(ship));
            AddSection(snapshot, capabilities, diagnostics, "dock_network", "dock_network", () =>
                SummarizeDockNetwork(ship));
            AddSection(snapshot, capabilities, diagnostics, "nearby_interactables", "nearby_interactables", () =>
                SummarizeNearbyInteractables(crewSim, player, ship));
            AddSection(snapshot, capabilities, diagnostics, "interaction_context", "interaction_context", () =>
                SummarizeInteractionContext(crewSim, player, ship));
            AddSection(snapshot, capabilities, diagnostics, "pda_devices", "pda_devices", () =>
                SummarizePdaDevices(crewSim, player, ship));
            AddSection(snapshot, capabilities, diagnostics, "objectives", "objectives", SummarizeObjectives);
        }

        private static object FindPlayerShip(object ships)
        {
            foreach (var ship in Enumerate(ships, 256))
            {
                if (InvokeBoolean(ship, "IsPlayerShip") == true)
                {
                    return ship;
                }
            }
            return null;
        }

        private static object FindPlayerCrew(object crew)
        {
            foreach (var item in Enumerate(crew, MaxCrew))
            {
                var owner = ResolveCondOwner(item);
                if (owner != null && ReadMember(owner, "CTPlayerCrew", "_ctPlayerCrew") != null)
                {
                    return owner;
                }
            }
            return null;
        }

        private static void AddSection(
            JObject snapshot,
            JArray capabilities,
            JArray diagnostics,
            string key,
            string capability,
            Func<JToken> reader)
        {
            try
            {
                var value = reader();
                if (value == null || value.Type == JTokenType.Null)
                {
                    diagnostics.Add(key + "_unavailable");
                    return;
                }
                snapshot[key] = value;
                capabilities.Add(capability);
            }
            catch (Exception exception)
            {
                diagnostics.Add(key + "_read_failed:" + exception.GetType().Name);
            }
        }

        private static JObject SummarizeCondOwner(object source, bool includeInventory)
        {
            var owner = ResolveCondOwner(source);
            if (owner == null)
            {
                return null;
            }

            var conditions = ReadMember(owner, "mapConds");
            var conditionCount = Count(conditions);

            var result = new JObject
            {
                ["id"] = Text(ReadMember(owner, "strID", "_strID")),
                ["name"] = Text(ReadMember(owner, "FriendlyName", "strNameFriendly", "strName")),
                ["short_name"] = Text(ReadMember(owner, "ShortName", "strNameShort")),
                ["type"] = Text(ReadMember(owner, "strType")),
                ["alive"] = Boolean(ReadMember(owner, "bAlive")),
                ["destroyed"] = Boolean(ReadMember(owner, "bDestroyed")),
                ["condition_count"] = conditionCount,
                ["conditions_truncated"] = conditionCount > MaxConditions,
                ["conditions"] = SummarizeConditions(conditions)
            };

            if (includeInventory)
            {
                var container = ResolveCondOwner(ReadMember(owner, "objContainer"));
                result["inventory"] = SummarizeInventory(container ?? owner);
            }
            return result;
        }

        private static JArray SummarizeCrew(object source)
        {
            var result = new JArray();
            foreach (var item in Enumerate(source, MaxCrew))
            {
                var crew = SummarizeCondOwner(item, false);
                if (crew != null)
                {
                    result.Add(crew);
                }
            }
            return result;
        }

        private static JArray SummarizeConditions(object source)
        {
            var result = new JArray();
            var map = source as IDictionary;
            if (map == null)
            {
                return result;
            }

            var count = 0;
            foreach (DictionaryEntry entry in map)
            {
                if (count++ >= MaxConditions)
                {
                    break;
                }

                var value = entry.Value;
                var condition = new JObject
                {
                    ["name"] = Text(entry.Key),
                    ["friendly_name"] = Text(ReadMember(value, "strNameFriendly")),
                    ["description"] = Text(ReadMember(value, "strDesc")),
                    ["track_condition"] = Text(ReadMember(value, "strTrackCond")),
                    ["display_self"] = Number(ReadMember(value, "nDisplaySelf")),
                    ["display_other"] = Number(ReadMember(value, "nDisplayOther")),
                    ["color"] = Text(ReadMember(value, "strColor")),
                    ["negative"] = Boolean(ReadMember(value, "bNegative")),
                    ["fatal"] = Boolean(ReadMember(value, "bFatal")),
                    ["knockout"] = Boolean(ReadMember(value, "bKO"))
                };
                if (IsSimple(value))
                {
                    condition["value"] = SafeValue(value);
                }
                else if (value != null)
                {
                    condition["count"] = Number(ReadMember(value, "fCount"));
                    condition["age_seconds"] = Number(ReadMember(value, "fAge"));
                }
                result.Add(condition);
            }
            return result;
        }

        private static JArray SummarizeInventory(object owner)
        {
            var result = new JArray();
            foreach (var item in Enumerate(ReadMember(owner, "aStack", "StackAsList"), MaxInventory))
            {
                var entry = ResolveCondOwner(item);
                if (entry == null)
                {
                    continue;
                }
                result.Add(new JObject
                {
                    ["id"] = Text(ReadMember(entry, "strID", "_strID")),
                    ["name"] = Text(ReadMember(entry, "FriendlyName", "strNameFriendly", "strName")),
                    ["count"] = Number(ReadMember(entry, "StackCount"))
                });
            }
            return result;
        }

        private static JObject SummarizeShip(object ship)
        {
            if (ship == null)
            {
                return null;
            }

            var situ = ReadMember(ship, "objSS");
            return new JObject
            {
                ["registration_id"] = Text(ReadMember(ship, "strRegID")),
                ["name"] = Text(ReadMember(ship, "publicName", "strName", "strTemplateName")),
                ["make"] = Text(ReadMember(ship, "make")),
                ["model"] = Text(ReadMember(ship, "model")),
                ["year"] = SafeValue(ReadMember(ship, "year")),
                ["destroyed"] = Boolean(ReadMember(ship, "bDestroyed")),
                ["damage_status"] = SafeValue(ReadMember(ship, "DMGStatus", "LatestStatus")),
                ["mass"] = Number(ReadMember(ship, "fShallowMass", "_mass")),
                ["rcs_fuel"] = Number(ReadMember(ship, "fShallowRCSRemass")),
                ["rcs_fuel_max"] = Number(ReadMember(ship, "fShallowRCSRemassMax")),
                ["fusion_fuel"] = Number(ReadMember(ship, "fShallowFusionRemain")),
                ["proximity_warning"] = Boolean(ReadMember(ship, "_proximityWarning")),
                ["track_warning"] = Boolean(ReadMember(ship, "_trackWarning")),
                ["docked_ship_count"] = Count(ReadMember(ship, "aDocked", "CachedAllDockedShips")),
                ["primary_docking_port_id"] = Text(ReadMember(ship, "strPrimaryDockingPortID")),
                ["position"] = Vector(ReadMember(situ, "vPos", "vShipPos")),
                ["velocity"] = Vector(ReadMember(situ, "vVel")),
                ["rotation"] = Number(ReadMember(situ, "RotationNormalized", "fRot")),
                ["grounded"] = Boolean(ReadMember(situ, "bGrounded")),
                ["orbit_locked"] = Boolean(ReadMember(situ, "bOrbitLocked")),
                ["navigation_active"] = new JValue(ReadMember(situ, "NavData") != null)
            };
        }

        private static JObject SummarizeShipLayout(object ship)
        {
            if (ship == null)
            {
                return null;
            }

            var compartments = new JArray();
            foreach (var item in Enumerate(ReadMember(ship, "aCompartments", "Compartments", "aRooms", "Rooms", "aModules", "Modules"), MaxLayoutNodes))
            {
                if (item == null)
                {
                    continue;
                }

                compartments.Add(new JObject
                {
                    ["id"] = StableId(item),
                    ["name"] = BestName(item),
                    ["type"] = TypeName(item),
                    ["powered"] = Boolean(ReadMember(item, "bPowered", "Powered")),
                    ["damaged"] = Boolean(ReadMember(item, "bDamaged", "Damaged", "IsDamaged")),
                    ["pressurized"] = Boolean(ReadMember(item, "bPressurized", "Pressurized", "IsPressurized")),
                    ["position"] = Position(item),
                    ["link_count"] = Count(ReadMember(item, "aLinks", "Links", "aNeighbors", "Neighbors", "ConnectedNodes")),
                });
            }

            var dockingPorts = new JArray();
            foreach (var item in Enumerate(ReadMember(ship, "aDockPorts", "DockPorts", "aPorts", "Ports"), MaxDockPorts))
            {
                if (item == null)
                {
                    continue;
                }

                dockingPorts.Add(new JObject
                {
                    ["id"] = StableId(item),
                    ["name"] = BestName(item),
                    ["type"] = TypeName(item),
                    ["occupied"] = Boolean(ReadMember(item, "bOccupied", "Occupied", "IsOccupied")),
                    ["enabled"] = Boolean(ReadMember(item, "bEnabled", "Enabled", "IsEnabled")),
                    ["position"] = Position(item),
                });
            }

            return new JObject
            {
                ["module_count"] = compartments.Count,
                ["dock_port_count"] = dockingPorts.Count,
                ["compartments"] = compartments,
                ["docking_ports"] = dockingPorts,
                ["note"] = "Conservative ship layout projection from exposed runtime fields; missing modules indicate unavailable game metadata.",
            };
        }

        private static JObject SummarizeShipSystems(object ship)
        {
            if (ship == null)
            {
                return null;
            }

            var situ = ReadMember(ship, "objSS");
            var powerSystem = ReadMember(ship, "objPower", "Power", "objPowerSys", "PowerSystem", "objReactor", "Reactor");
            var lifeSupportSystem = ReadMember(ship, "objLifeSupport", "LifeSupport", "objAtmos", "Atmosphere", "objEnv", "Environment");

            var propulsion = new JObject
            {
                ["rcs_fuel"] = Number(ReadMember(ship, "fShallowRCSRemass")),
                ["rcs_fuel_max"] = Number(ReadMember(ship, "fShallowRCSRemassMax")),
                ["fusion_fuel"] = Number(ReadMember(ship, "fShallowFusionRemain")),
                ["velocity"] = Vector(ReadMember(situ, "vVel")),
                ["grounded"] = Boolean(ReadMember(situ, "bGrounded")),
                ["orbit_locked"] = Boolean(ReadMember(situ, "bOrbitLocked")),
                ["torch_drive_thrusting"] = Boolean(ReadMember(ship, "bTorchDriveThrusting", "TorchDriveThrusting", "bFusionThrusting")),
                ["fusion_thrust_max"] = Number(ReadMember(ship, "fFusionThrustMax", "FusionThrustMax", "TorchDriveThrustMax")),
            };

            var navigation = new JObject
            {
                ["active"] = new JValue(ReadMember(situ, "NavData") != null),
                ["position"] = Vector(ReadMember(situ, "vPos", "vShipPos")),
                ["rotation"] = Number(ReadMember(situ, "RotationNormalized", "fRot")),
                ["proximity_warning"] = Boolean(ReadMember(ship, "_proximityWarning")),
                ["track_warning"] = Boolean(ReadMember(ship, "_trackWarning")),
                ["nav_ai_manned"] = Boolean(ReadMember(ship, "NavAIManned", "bNavAIManned", "AINavManned")),
                ["nav_player_manned"] = Boolean(ReadMember(ship, "NavPlayerManned", "bNavPlayerManned", "PlayerNavManned")),
                ["nav_count"] = Number(ReadMember(ship, "NavCount", "iNavCount", "NavigationCount")),
            };

            var structure = new JObject
            {
                ["destroyed"] = Boolean(ReadMember(ship, "bDestroyed")),
                ["damage_status"] = SafeValue(ReadMember(ship, "DMGStatus", "LatestStatus")),
                ["mass"] = Number(ReadMember(ship, "fShallowMass", "_mass")),
                ["docked_ship_count"] = Count(ReadMember(ship, "aDocked", "CachedAllDockedShips")),
                ["primary_docking_port_id"] = Text(ReadMember(ship, "strPrimaryDockingPortID")),
            };

            var power = new JObject
            {
                ["reactor_online"] = Boolean(
                    ReadMember(ship, "bReactorOn", "ReactorOnline", "bFusionOn", "bPowerOn", "bFusionReactorRunning") ??
                    ReadMember(powerSystem, "bReactorOn", "ReactorOnline", "bFusionOn", "bPowerOn", "Online", "IsOnline", "Active")
                ),
                ["battery_level"] = Number(
                    ReadMember(ship, "fBattery", "BatteryLevel", "PowerLevel", "fPowerStored") ??
                    ReadMember(powerSystem, "fBattery", "BatteryLevel", "PowerLevel", "fPowerStored", "StoredPower", "Charge")
                ),
                ["grid_load"] = Number(
                    ReadMember(ship, "fGridLoad", "GridLoad", "PowerLoad", "fPowerDraw", "fPowerConsumption") ??
                    ReadMember(powerSystem, "fGridLoad", "GridLoad", "PowerLoad", "fPowerDraw", "fPowerConsumption", "Load", "CurrentLoad")
                ),
                ["check_power"] = Boolean(ReadMember(ship, "bCheckPower", "CheckPower", "PowerCheckActive")),
            };

            var atmosphere = ReadMember(ship, "objAtmos", "objLifeSupport", "LifeSupport", "Atmosphere") ??
                ReadMember(lifeSupportSystem, "objAtmos", "Atmosphere", "Atmos");
            var lifeSupport = new JObject
            {
                ["oxygen_level"] = Number(
                    ReadMember(ship, "fOxygenLevel", "OxygenLevel", "fO2", "fOxygen", "fAtmoO2", "AtmoO2", "fShallowO2Remain") ??
                    ReadMember(lifeSupportSystem, "fOxygenLevel", "OxygenLevel", "fO2", "fOxygen", "CurrentO2")
                ),
                ["co2_level"] = Number(
                    ReadMember(ship, "fCO2Level", "CO2Level", "fCO2", "fAtmoCO2", "AtmoCO2") ??
                    ReadMember(lifeSupportSystem, "fCO2Level", "CO2Level", "fCO2", "CurrentCO2")
                ),
                ["temperature"] = Number(
                    ReadMember(ship, "fTemperature", "Temperature", "fAtmoTemp", "AtmoTemperature") ??
                    ReadMember(lifeSupportSystem, "fTemperature", "Temperature", "CabinTemperature")
                ),
                ["pressure"] = Number(
                    ReadMember(ship, "fPressure", "Pressure", "fAtmoPressure", "AtmoPressure", "fCabinPressure") ??
                    ReadMember(lifeSupportSystem, "fPressure", "Pressure", "CabinPressure")
                ),
                ["oxygen_level_atmosphere"] = Number(
                    ReadMember(atmosphere, "fO2", "fOxygen", "OxygenLevel", "AtmoO2", "CurrentO2")
                ),
                ["co2_level_atmosphere"] = Number(
                    ReadMember(atmosphere, "fCO2", "CO2Level", "AtmoCO2", "CurrentCO2")
                ),
                ["temperature_atmosphere"] = Number(
                    ReadMember(atmosphere, "fTemperature", "Temperature", "AtmoTemperature", "CurrentTemperature")
                ),
                ["pressure_atmosphere"] = Number(
                    ReadMember(atmosphere, "fPressure", "Pressure", "AtmoPressure", "CurrentPressure")
                ),
            };

            return new JObject
            {
                ["propulsion"] = propulsion,
                ["navigation"] = navigation,
                ["structure"] = structure,
                ["power"] = power,
                ["life_support"] = lifeSupport,
                ["note"] = "Subsystem fields are opportunistic; null values mean the runtime did not expose that metric.",
            };
        }

        private static JObject SummarizeDockNetwork(object ship)
        {
            if (ship == null)
            {
                return null;
            }

            var dockedShips = new JArray();
            foreach (var item in Enumerate(ReadMember(ship, "aDocked", "CachedAllDockedShips", "AllDockedShips"), MaxDockedShips))
            {
                if (item == null)
                {
                    continue;
                }

                dockedShips.Add(new JObject
                {
                    ["id"] = StableId(item),
                    ["registration_id"] = Text(ReadMember(item, "strRegID")),
                    ["name"] = Text(ReadMember(item, "publicName", "strName", "strTemplateName")),
                    ["make"] = Text(ReadMember(item, "make")),
                    ["model"] = Text(ReadMember(item, "model")),
                    ["year"] = SafeValue(ReadMember(item, "year")),
                    ["destroyed"] = Boolean(ReadMember(item, "bDestroyed")),
                    ["damage_status"] = SafeValue(ReadMember(item, "DMGStatus", "LatestStatus")),
                    ["docked_ship_count"] = Count(ReadMember(item, "aDocked", "CachedAllDockedShips")),
                    ["primary_docking_port_id"] = Text(ReadMember(item, "strPrimaryDockingPortID")),
                });
            }

            var dockingPorts = new JArray();
            foreach (var port in Enumerate(ReadMember(ship, "aDockPorts", "DockPorts", "aPorts", "Ports"), MaxDockPorts))
            {
                if (port == null)
                {
                    continue;
                }

                var attached = ReadMember(port, "objDockedTo", "DockedTo", "objDockedShip", "DockedShip", "objAttached", "Attached");
                dockingPorts.Add(new JObject
                {
                    ["id"] = StableId(port),
                    ["name"] = BestName(port),
                    ["occupied"] = Boolean(ReadMember(port, "bOccupied", "Occupied", "IsOccupied")),
                    ["enabled"] = Boolean(ReadMember(port, "bEnabled", "Enabled", "IsEnabled")),
                    ["attached_ship_id"] = StableId(attached),
                    ["attached_ship_name"] = Text(ReadMember(attached, "publicName", "strName", "strTemplateName")),
                    ["attached_ship_registration_id"] = Text(ReadMember(attached, "strRegID")),
                });
            }

            return new JObject
            {
                ["ship_id"] = StableId(ship),
                ["ship_registration_id"] = Text(ReadMember(ship, "strRegID")),
                ["ship_name"] = Text(ReadMember(ship, "publicName", "strName", "strTemplateName")),
                ["ship_primary_docking_port_id"] = Text(ReadMember(ship, "strPrimaryDockingPortID")),
                ["docked_ship_count"] = dockedShips.Count,
                ["docked_ships"] = dockedShips,
                ["docking_ports"] = dockingPorts,
                ["note"] = "Dock network is best-effort reflection from exposed ship/port runtime fields and updates with live rename/undock state.",
            };
        }

        private static JObject SummarizeNearbyInteractables(object crewSim, object player, object ship)
        {
            var rows = new JArray();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var nearbySources = new[]
            {
                ReadMember(player, "aNearby", "Nearby", "aInteractables", "Interactables"),
                ReadMember(player, "aUseTargets", "UseTargets", "aTargetables", "Targetables", "aContextTargets", "ContextTargets"),
                ReadMember(player, "aTalkTargets", "TalkTargets", "aConversationTargets", "ConversationTargets", "aDialogTargets", "DialogTargets"),
                ReadMember(player, "aVendorTargets", "VendorTargets", "aTradeTargets", "TradeTargets", "aContactTargets", "ContactTargets"),
                ReadMember(crewSim, "aNearby", "Nearby", "aUsables", "Usables", "aInteractionTargets"),
                ReadMember(crewSim, "aTalkTargets", "TalkTargets", "aConversationTargets", "ConversationTargets", "aDialogTargets", "DialogTargets"),
                ReadMember(crewSim, "aVendorTargets", "VendorTargets", "aTradeTargets", "TradeTargets", "aContactTargets", "ContactTargets"),
                ReadMember(ship, "aNearby", "Nearby", "aUsables", "Usables", "aInteractables", "Interactables"),
                ReadMember(crewSim, "aConsoles", "Consoles", "aStations", "Stations", "aWorldItems", "WorldItems"),
                ReadMember(ship, "aConsoles", "Consoles", "aStations", "Stations", "aWorldItems", "WorldItems", "aStack", "StackAsList")
            };

            foreach (var source in nearbySources)
            {
                foreach (var item in Enumerate(source, MaxInteractables))
                {
                    if (item == null)
                    {
                        continue;
                    }

                    AddInteractableRow(rows, seen, item, Number(ReadMember(item, "fDistance", "Distance", "distance")), null);
                }
            }

            var directCandidates = new[]
            {
                ReadMember(player, "objUseTarget", "UseTarget", "objInteractTarget", "InteractTarget", "objSelected", "SelectedTarget", "objSeat", "Seat"),
                ReadMember(player, "objTalkTarget", "TalkTarget", "objConversationTarget", "ConversationTarget", "objDialogTarget", "DialogTarget"),
                ReadMember(player, "objVendorTarget", "VendorTarget", "objTradeTarget", "TradeTarget", "objContactTarget", "ContactTarget"),
                ReadMember(crewSim, "objUseTarget", "UseTarget", "objSelected", "SelectedTarget", "objFocused", "FocusedObject"),
                ReadMember(crewSim, "objTalkTarget", "TalkTarget", "objConversationTarget", "ConversationTarget", "objDialogTarget", "DialogTarget"),
                ReadMember(crewSim, "objVendorTarget", "VendorTarget", "objTradeTarget", "TradeTarget", "objContactTarget", "ContactTarget"),
                ReadMember(ship, "objNavConsole", "NavConsole", "objHelm", "Helm", "objPrimaryConsole", "PrimaryConsole")
            };

            foreach (var item in directCandidates)
            {
                AddInteractableRow(rows, seen, item, JValue.CreateNull(), null);
            }

            // If scene lists are empty, seed with best-effort live targets so nearby does not return empty.
            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                var likelyTarget = FindLikelyInteractionTarget(player);
                AddInteractableRow(rows, seen, likelyTarget, JValue.CreateNull(), "likely_target_fallback");

                var likelyUi = FindLikelyOpenUi(player);
                AddInteractableRow(rows, seen, likelyUi, JValue.CreateNull(), "likely_ui_fallback");
            }

            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                AppendFallbackInteractables(rows, seen, player);
            }

            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                AppendCachedUnfilteredNearby(rows, seen, player);
            }

            var note = rows.Count >= MaxInteractables
                ? "Nearby interaction candidates are bounded and may be partial."
                : "Nearby interaction candidates are opportunistic and depend on scene/runtime exposure.";

            if (rows.Count > 0 && ReadMember(player, "aNearby", "Nearby", "aInteractables", "Interactables") == null)
            {
                note += " Fallback world-object probing was used.";
            }

            return new JObject
            {
                ["count"] = rows.Count,
                ["items"] = rows,
                ["note"] = note,
            };
        }

        private static JObject SummarizeInteractionContext(object crewSim, object player, object ship)
        {
            var seat = ReadMember(player, "objSeat", "Seat", "objCurrentSeat", "CurrentSeat", "objChair", "CurrentChair", "objStation", "CurrentStation");
            var target = ReadMember(player, "objUseTarget", "UseTarget", "objInteractTarget", "InteractTarget", "objSelected", "SelectedTarget") ??
                ReadMember(player, "objTalkTarget", "TalkTarget", "objConversationTarget", "ConversationTarget", "objDialogTarget", "DialogTarget") ??
                ReadMember(player, "objVendorTarget", "VendorTarget", "objTradeTarget", "TradeTarget", "objContactTarget", "ContactTarget") ??
                ReadMember(crewSim, "objUseTarget", "UseTarget", "objSelected", "SelectedTarget", "objFocused", "FocusedObject") ??
                ReadMember(crewSim, "objTalkTarget", "TalkTarget", "objConversationTarget", "ConversationTarget", "objDialogTarget", "DialogTarget") ??
                ReadMember(crewSim, "objVendorTarget", "VendorTarget", "objTradeTarget", "TradeTarget", "objContactTarget", "ContactTarget") ??
                ReadMember(ship, "objCommTarget", "CommTarget", "objHailTarget", "HailTarget", "objTradeTarget", "TradeTarget");
            var openUi = ReadMember(crewSim, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent", "objHUD", "HUD") ??
                ReadMember(player, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent", "objHUD", "HUD") ??
                ReadMember(crewSim, "objConversationUI", "ConversationUI", "objDialogUI", "DialogUI", "objTradeUI", "TradeUI", "objVendorUI", "VendorUI") ??
                ReadMember(player, "objConversationUI", "ConversationUI", "objDialogUI", "DialogUI", "objTradeUI", "TradeUI", "objVendorUI", "VendorUI") ??
                ReadMember(crewSim, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI") ??
                ReadMember(player, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI");

            if (EnableSceneSweepFallbacks && openUi == null)
            {
                openUi = FindLikelyOpenUi(player);
            }

            if (EnableSceneSweepFallbacks && target == null)
            {
                target = FindLikelyInteractionTarget(player);
            }

            return new JObject
            {
                ["seat"] = seat == null ? JValue.CreateNull() : new JObject
                {
                    ["id"] = StableId(seat),
                    ["name"] = BestName(seat),
                    ["type"] = TypeName(seat),
                },
                ["selected_target"] = target == null ? JValue.CreateNull() : new JObject
                {
                    ["id"] = StableId(target),
                    ["name"] = BestName(target),
                    ["type"] = TypeName(target),
                },
                ["open_ui"] = openUi == null ? JValue.CreateNull() : new JObject
                {
                    ["id"] = StableId(openUi),
                    ["name"] = BestName(openUi),
                    ["type"] = TypeName(openUi),
                    ["component_type"] = ComponentTypeName(openUi),
                    ["game_object"] = GameObjectName(openUi),
                    ["hierarchy_path"] = HierarchyPath(openUi),
                },
                ["interaction_mode"] = Text(ReadMember(player, "strInteractMode", "InteractionMode", "CurrentMode", "Mode")),
                ["submenu_open"] = Boolean(ReadMember(player, "bSubMenuOpen", "SubMenuOpen", "bUseMenuOpen", "UseMenuOpen", "bInteractionMenuOpen", "InteractionMenuOpen", "bDialogOpen", "DialogOpen", "bTradeOpen", "TradeOpen")),
                ["signals"] = ProbeInteractionSignals(player, crewSim, ship, seat, target, openUi),
                ["ui_candidates"] = SummarizeUiCandidates(player, crewSim, ship),
                ["note"] = "Context fields are best-effort reflection from pilot/use UI roots and may vary by scene.",
            };
        }

        private static object FindLikelyOpenUi(object player)
        {
            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));
            var best = (object)null;
            var bestScore = int.MinValue;

            foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (gameObject == null || !gameObject.activeInHierarchy)
                {
                    continue;
                }

                var distance = DistanceFrom(origin, GetWorldPosition(gameObject));
                if (distance.HasValue && distance.Value > 16f)
                {
                    continue;
                }

                var score = ScoreUiCandidate(gameObject.name ?? string.Empty);
                if (score <= 0)
                {
                    continue;
                }

                if (score > bestScore)
                {
                    bestScore = score;
                    best = gameObject;
                }
            }

            return best;
        }

        private static object FindLikelyInteractionTarget(object player)
        {
            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));
            var best = (object)null;
            var bestScore = int.MinValue;

            foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (gameObject == null || !gameObject.activeInHierarchy)
                {
                    continue;
                }

                var distance = DistanceFrom(origin, GetWorldPosition(gameObject));
                if (distance.HasValue && distance.Value > 10f)
                {
                    continue;
                }

                var lowered = (gameObject.name ?? string.Empty).ToLowerInvariant();
                var score = 0;
                if (lowered.Contains("power") || lowered.Contains("battery") || lowered.Contains("panel"))
                {
                    score += 8;
                }
                if (lowered.Contains("vendor") || lowered.Contains("trade") || lowered.Contains("kiosk") || lowered.Contains("terminal"))
                {
                    score += 6;
                }
                if (lowered.Contains("toon") || lowered.Contains("npc") || lowered.Contains("contact") || lowered.Contains("dialog"))
                {
                    score += 4;
                }

                if (score <= 0)
                {
                    continue;
                }

                if (distance.HasValue)
                {
                    score += Math.Max(0, 6 - (int)Math.Round(distance.Value));
                }

                if (score > bestScore)
                {
                    bestScore = score;
                    best = gameObject;
                }
            }

            return best;
        }

        private static int ScoreUiCandidate(string name)
        {
            var lowered = (name ?? string.Empty).ToLowerInvariant();
            var score = 0;

            if (lowered.Contains("ui")) score += 3;
            if (lowered.Contains("menu") || lowered.Contains("panel") || lowered.Contains("overlay")) score += 4;
            if (lowered.Contains("power") || lowered.Contains("battery") || lowered.Contains("reactor")) score += 7;
            if (lowered.Contains("trade") || lowered.Contains("vendor") || lowered.Contains("kiosk") || lowered.Contains("shop")) score += 6;
            if (lowered.Contains("dialog") || lowered.Contains("contact") || lowered.Contains("pda") || lowered.Contains("spacebook")) score += 5;

            return score;
        }

        private static JArray SummarizePdaDevices(object crewSim, object player, object ship)
        {
            var rows = new JArray();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));

            var directSources = new[]
            {
                ReadMember(player, "aStack", "StackAsList", "aInventory", "Inventory", "aItems", "Items"),
                ReadMember(ship, "aStack", "StackAsList", "aInventory", "Inventory", "aItems", "Items"),
                ReadMember(crewSim, "aStack", "StackAsList", "aInventory", "Inventory", "aItems", "Items"),
                ReadMember(player, "objPDA", "PDA", "objDatapad", "Datapad", "objCommDevice", "CommDevice", "objPhone", "Phone"),
                ReadMember(crewSim, "objPDA", "PDA", "objDatapad", "Datapad", "objCommDevice", "CommDevice", "objPhone", "Phone"),
                ReadMember(ship, "objPDA", "PDA", "objDatapad", "Datapad", "objCommDevice", "CommDevice", "objPhone", "Phone"),
                ReadMember(player, "aContacts", "Contacts", "ContactList", "Spacebook", "objSpacebook"),
                ReadMember(ship, "aContacts", "Contacts", "ContactList", "Spacebook", "objSpacebook"),
                ReadMember(crewSim, "aContacts", "Contacts", "ContactList", "Spacebook", "objSpacebook"),
                ReadMember(player, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent"),
                ReadMember(crewSim, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent"),
                ReadMember(player, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI"),
                ReadMember(crewSim, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI"),
                ReadMember(player, "objConversation", "Conversation", "objDialog", "Dialog", "objTrade", "Trade", "objVendor", "Vendor"),
                ReadMember(crewSim, "objConversation", "Conversation", "objDialog", "Dialog", "objTrade", "Trade", "objVendor", "Vendor"),
            };

            foreach (var source in directSources)
            {
                foreach (var item in Enumerate(source, MaxDeviceCandidates))
                {
                    if (item == null || !LooksDeviceCandidate(item))
                    {
                        continue;
                    }

                    AppendDeviceRow(rows, seen, item, "direct_source", DistanceFrom(origin, GetWorldPosition(item)));
                }
            }

            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                foreach (var item in GetCachedDeviceCandidates(origin))
                {
                    if (rows.Count >= MaxDeviceCandidates)
                    {
                        break;
                    }

                    AppendDeviceRow(rows, seen, item, "device_cached_scan", DistanceFrom(origin, GetWorldPosition(item)));
                }
            }

            return new JArray(rows);
        }

        private static JArray ProbeInteractionSignals(params object[] roots)
        {
            var signals = new JArray();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var root in roots)
            {
                if (root == null)
                {
                    continue;
                }

                var type = root.GetType();
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

                foreach (var field in type.GetFields(flags))
                {
                    if (signals.Count >= MaxSignalFields)
                    {
                        return signals;
                    }
                    if (!ContainsSignalToken(field.Name))
                    {
                        continue;
                    }

                    object value;
                    try
                    {
                        value = field.GetValue(root);
                    }
                    catch
                    {
                        continue;
                    }

                    if (!IsSimple(value) || value == null)
                    {
                        continue;
                    }

                    var key = type.Name + "." + field.Name;
                    if (!seen.Add(key))
                    {
                        continue;
                    }

                    signals.Add(new JObject
                    {
                        ["source"] = type.Name,
                        ["name"] = field.Name,
                        ["value"] = SafeValue(value),
                    });
                }

                foreach (var property in type.GetProperties(flags))
                {
                    if (signals.Count >= MaxSignalFields)
                    {
                        return signals;
                    }
                    if (property.GetIndexParameters().Length != 0 || !ContainsSignalToken(property.Name))
                    {
                        continue;
                    }

                    object value;
                    try
                    {
                        value = property.GetValue(root, null);
                    }
                    catch
                    {
                        continue;
                    }

                    if (!IsSimple(value) || value == null)
                    {
                        continue;
                    }

                    var key = type.Name + "." + property.Name;
                    if (!seen.Add(key))
                    {
                        continue;
                    }

                    signals.Add(new JObject
                    {
                        ["source"] = type.Name,
                        ["name"] = property.Name,
                        ["value"] = SafeValue(value),
                    });
                }
            }

            return signals;
        }

        private static void AddInteractableRow(JArray rows, HashSet<string> seen, object item, JToken distanceOverride, string source)
        {
            if (item == null || rows.Count >= MaxInteractables)
            {
                return;
            }

            var id = StableId(item);
            var idText = id.Type == JTokenType.Null ? string.Empty : Convert.ToString(id);
            if (!string.IsNullOrEmpty(idText) && !seen.Add(idText))
            {
                return;
            }

            var row = new JObject
            {
                ["id"] = id,
                ["name"] = BestName(item),
                ["type"] = TypeName(item),
                ["distance"] = distanceOverride ?? Number(ReadMember(item, "fDistance", "Distance", "distance")),
                ["position"] = Position(item),
                ["enabled"] = Boolean(ReadMember(item, "bEnabled", "Enabled", "IsEnabled")),
                ["component_type"] = ComponentTypeName(item),
                ["game_object"] = GameObjectName(item),
                ["hierarchy_path"] = HierarchyPath(item),
            };

            var nameSignal = string.Format(
                "{0} {1} {2}",
                Convert.ToString(BestName(item)) ?? string.Empty,
                Convert.ToString(TypeName(item)) ?? string.Empty,
                GameObjectName(item)
            );
            var hits = TokenHits(nameSignal, InteractionSignalTokens, 8);
            row["signal_token_hits"] = hits;
            row["signal_score"] = hits.Count;

            if (!string.IsNullOrEmpty(source))
            {
                row["source"] = source;
            }

            rows.Add(row);
        }

        private static void AppendFallbackInteractables(JArray rows, HashSet<string> seen, object player)
        {
            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));
            var allObjects = Resources.FindObjectsOfTypeAll<GameObject>();
            var scanned = 0;

            foreach (var gameObject in allObjects)
            {
                if (rows.Count >= MaxInteractables || scanned++ >= MaxFallbackWorldObjects)
                {
                    return;
                }
                if (gameObject == null || !gameObject.activeInHierarchy)
                {
                    continue;
                }
                if (!LooksInteractable(gameObject))
                {
                    continue;
                }

                var targetPos = GetWorldPosition(gameObject);
                JToken distance = JValue.CreateNull();
                if (origin.HasValue && targetPos.HasValue)
                {
                    var meters = Vector3.Distance(origin.Value, targetPos.Value);
                    if (meters > 8f)
                    {
                        continue;
                    }
                    distance = new JValue(Math.Round(meters, 2));
                }

                AddInteractableRow(rows, seen, gameObject, distance, "world_object_fallback");
            }
        }

        private static JArray SummarizeUiCandidates(object player, object crewSim, object ship)
        {
            var rows = new JArray();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));

            AppendUiCandidate(rows, seen, ReadMember(player, "objSeat", "Seat", "objCurrentSeat", "CurrentSeat", "objStation", "CurrentStation"), "player_direct");
            AppendUiCandidate(rows, seen, ReadMember(crewSim, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent", "objHUD", "HUD"), "crewsim_direct");
            AppendUiCandidate(rows, seen, ReadMember(ship, "objNavConsole", "NavConsole", "objHelm", "Helm", "objPrimaryConsole", "PrimaryConsole"), "ship_direct");
            AppendUiCandidate(rows, seen, ReadMember(player, "objOpenUI", "OpenUI", "objMenuCurrent", "CurrentMenu", "menuCurrent", "objHUD", "HUD"), "player_ui_direct");
            AppendUiCandidate(rows, seen, ReadMember(player, "objConversationUI", "ConversationUI", "objDialogUI", "DialogUI", "objTradeUI", "TradeUI", "objVendorUI", "VendorUI"), "player_conversation_ui");
            AppendUiCandidate(rows, seen, ReadMember(crewSim, "objConversationUI", "ConversationUI", "objDialogUI", "DialogUI", "objTradeUI", "TradeUI", "objVendorUI", "VendorUI"), "crewsim_conversation_ui");
            AppendUiCandidate(rows, seen, ReadMember(player, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI"), "player_contacts_ui");
            AppendUiCandidate(rows, seen, ReadMember(crewSim, "objContactsUI", "ContactsUI", "objSpacebookUI", "SpacebookUI", "objPdaUI", "PdaUI", "objCommUI", "CommUI"), "crewsim_contacts_ui");

            // Keep at least one actionable surface candidate in sparse scenes.
            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                AppendUiCandidate(rows, seen, FindLikelyOpenUi(player), "likely_open_ui");
                AppendUiCandidate(rows, seen, FindLikelyInteractionTarget(player), "likely_interaction_target");
            }

            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                foreach (var item in GetCachedUiCandidates(origin))
                {
                    if (rows.Count >= MaxUiCandidates)
                    {
                        break;
                    }

                    AppendUiCandidate(rows, seen, item, "ui_cached_scan", DistanceFrom(origin, GetWorldPosition(item)));
                }
            }

            if (EnableSceneSweepFallbacks && rows.Count == 0)
            {
                AppendCachedUnfilteredUiCandidates(rows, seen, origin);
            }

            return rows;
        }

        private static void AppendCachedUnfilteredNearby(JArray rows, HashSet<string> seen, object player)
        {
            var cached = GetCachedNearbyScan(player);
            foreach (var item in cached)
            {
                if (rows.Count >= MaxInteractables)
                {
                    return;
                }

                AddInteractableRow(rows, seen, item, JValue.CreateNull(), "world_object_unfiltered_cache");
            }
        }

        private static IEnumerable GetCachedUiCandidates(Vector3? origin)
        {
            lock (ScanCacheLock)
            {
                if (Time.realtimeSinceStartup - cachedUiCandidateScanAt < HeavyScanCacheSeconds && cachedUiCandidateScan.Count > 0)
                {
                    return cachedUiCandidateScan.ToArray();
                }
            }

            var result = new List<object>(MaxUiCandidates);
            var scannedObjects = 0;
            foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (result.Count >= MaxUiCandidates || scannedObjects++ >= MaxFallbackWorldObjects)
                {
                    break;
                }
                if (gameObject == null || !gameObject.activeInHierarchy || !LooksUiCandidate(gameObject))
                {
                    continue;
                }

                var worldPos = GetWorldPosition(gameObject);
                var distance = DistanceFrom(origin, worldPos);
                if (distance.HasValue && distance.Value > 12f)
                {
                    continue;
                }

                result.Add(gameObject);
            }

            if (result.Count < MaxUiCandidates)
            {
                var scannedComponents = 0;
                foreach (var component in Resources.FindObjectsOfTypeAll<Component>())
                {
                    if (result.Count >= MaxUiCandidates || scannedComponents++ >= MaxFallbackWorldObjects)
                    {
                        break;
                    }
                    if (component == null || component.gameObject == null || !component.gameObject.activeInHierarchy || !LooksUiCandidate(component))
                    {
                        continue;
                    }

                    var worldPos = GetWorldPosition(component);
                    var distance = DistanceFrom(origin, worldPos);
                    if (distance.HasValue && distance.Value > 12f)
                    {
                        continue;
                    }

                    result.Add(component);
                }
            }

            lock (ScanCacheLock)
            {
                cachedUiCandidateScan.Clear();
                cachedUiCandidateScan.AddRange(result);
                cachedUiCandidateScanAt = Time.realtimeSinceStartup;
                return cachedUiCandidateScan.ToArray();
            }
        }

        private static IEnumerable GetCachedDeviceCandidates(Vector3? origin)
        {
            lock (ScanCacheLock)
            {
                if (Time.realtimeSinceStartup - cachedDeviceScanAt < HeavyScanCacheSeconds && cachedDeviceScan.Count > 0)
                {
                    return cachedDeviceScan.ToArray();
                }
            }

            var result = new List<object>(MaxDeviceCandidates);
            var scannedObjects = 0;
            foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (result.Count >= MaxDeviceCandidates || scannedObjects++ >= MaxFallbackWorldObjects)
                {
                    break;
                }
                if (gameObject == null || !gameObject.activeInHierarchy || !LooksDeviceCandidate(gameObject))
                {
                    continue;
                }

                var worldPos = GetWorldPosition(gameObject);
                var distance = DistanceFrom(origin, worldPos);
                if (distance.HasValue && distance.Value > 18f)
                {
                    continue;
                }

                result.Add(gameObject);
            }

            if (result.Count < MaxDeviceCandidates)
            {
                var scannedComponents = 0;
                foreach (var component in Resources.FindObjectsOfTypeAll<Component>())
                {
                    if (result.Count >= MaxDeviceCandidates || scannedComponents++ >= MaxFallbackWorldObjects)
                    {
                        break;
                    }
                    if (component == null || component.gameObject == null || !component.gameObject.activeInHierarchy || !LooksDeviceCandidate(component))
                    {
                        continue;
                    }

                    var worldPos = GetWorldPosition(component);
                    var distance = DistanceFrom(origin, worldPos);
                    if (distance.HasValue && distance.Value > 18f)
                    {
                        continue;
                    }

                    result.Add(component);
                }
            }

            lock (ScanCacheLock)
            {
                cachedDeviceScan.Clear();
                cachedDeviceScan.AddRange(result);
                cachedDeviceScanAt = Time.realtimeSinceStartup;
                return cachedDeviceScan.ToArray();
            }
        }

        private static void AppendCachedUnfilteredUiCandidates(JArray rows, HashSet<string> seen, Vector3? origin)
        {
            var cached = GetCachedUiScan(origin);
            foreach (var item in cached)
            {
                if (rows.Count >= MaxUiCandidates)
                {
                    return;
                }

                AppendUiCandidate(rows, seen, item, "ui_unfiltered_cache");
            }
        }

        private static IEnumerable GetCachedNearbyScan(object player)
        {
            lock (ScanCacheLock)
            {
                if (Time.realtimeSinceStartup - cachedNearbyScanAt < HeavyScanCacheSeconds && cachedNearbyScan.Count > 0)
                {
                    return cachedNearbyScan;
                }

                cachedNearbyScan = new JArray();
                cachedNearbyScanAt = Time.realtimeSinceStartup;
            }

            var origin = GetWorldPosition(player) ?? GetWorldPosition(ResolveCondOwner(player));
            var radii = new[] { 3f, 6f, 12f, 24f };
            var result = new JArray();

            foreach (var radius in radii)
            {
                var scanned = 0;
                foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
                {
                    if (result.Count >= MaxInteractables || scanned++ >= MaxFallbackWorldObjects)
                    {
                        goto finish;
                    }
                    if (gameObject == null || !gameObject.activeInHierarchy)
                    {
                        continue;
                    }

                    var targetPos = GetWorldPosition(gameObject);
                    JToken distance = JValue.CreateNull();
                    if (origin.HasValue && targetPos.HasValue)
                    {
                        var meters = Vector3.Distance(origin.Value, targetPos.Value);
                        if (meters > radius)
                        {
                            continue;
                        }
                        distance = new JValue(Math.Round(meters, 2));
                    }

                    result.Add(gameObject);
                }

                if (result.Count > 0)
                {
                    goto finish;
                }
            }

        finish:
            lock (ScanCacheLock)
            {
                cachedNearbyScan = result;
                cachedNearbyScanAt = Time.realtimeSinceStartup;
                return cachedNearbyScan;
            }
        }

        private static IEnumerable GetCachedUiScan(Vector3? origin)
        {
            lock (ScanCacheLock)
            {
                if (Time.realtimeSinceStartup - cachedUiScanAt < HeavyScanCacheSeconds && cachedUiScan.Count > 0)
                {
                    return cachedUiScan;
                }

                cachedUiScan = new JArray();
                cachedUiScanAt = Time.realtimeSinceStartup;
            }

            var radii = new[] { 3f, 6f, 12f, 24f };
            var result = new JArray();

            foreach (var radius in radii)
            {
                var scannedObjects = 0;
                foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
                {
                    if (result.Count >= MaxUiCandidates || scannedObjects++ >= MaxFallbackWorldObjects)
                    {
                        goto finish;
                    }
                    if (gameObject == null || !gameObject.activeInHierarchy)
                    {
                        continue;
                    }

                    var worldPos = GetWorldPosition(gameObject);
                    var distance = DistanceFrom(origin, worldPos);
                    if (distance.HasValue && distance.Value > radius)
                    {
                        continue;
                    }

                    result.Add(gameObject);
                }

                var scannedComponents = 0;
                foreach (var component in Resources.FindObjectsOfTypeAll<Component>())
                {
                    if (result.Count >= MaxUiCandidates || scannedComponents++ >= MaxFallbackWorldObjects)
                    {
                        goto finish;
                    }
                    if (component == null || component.gameObject == null || !component.gameObject.activeInHierarchy)
                    {
                        continue;
                    }

                    var worldPos = GetWorldPosition(component);
                    var distance = DistanceFrom(origin, worldPos);
                    if (distance.HasValue && distance.Value > radius)
                    {
                        continue;
                    }

                    result.Add(component);
                }

                if (result.Count > 0)
                {
                    goto finish;
                }
            }

        finish:
            lock (ScanCacheLock)
            {
                cachedUiScan = result;
                cachedUiScanAt = Time.realtimeSinceStartup;
                return cachedUiScan;
            }
        }

        private static void AppendDeviceRow(JArray rows, HashSet<string> seen, object item, string source, float? distance)
        {
            if (item == null || rows.Count >= MaxDeviceCandidates)
            {
                return;
            }

            var id = StableId(item);
            var idText = id.Type == JTokenType.Null ? string.Empty : Convert.ToString(id);
            var uniqueKey = !string.IsNullOrEmpty(idText)
                ? idText + "|" + source
                : BestName(item) + "|" + TypeName(item) + "|" + source;
            if (!seen.Add(uniqueKey))
            {
                return;
            }

            var row = new JObject
            {
                ["id"] = id,
                ["name"] = BestName(item),
                ["type"] = TypeName(item),
                ["source"] = source,
                ["position"] = Position(item),
                ["distance"] = distance.HasValue ? new JValue(Math.Round(distance.Value, 2)) : JValue.CreateNull(),
                ["active"] = Boolean(ReadMember(item, "activeInHierarchy", "activeSelf", "isActiveAndEnabled", "enabled")),
                ["component_type"] = ComponentTypeName(item),
                ["game_object"] = GameObjectName(item),
                ["hierarchy_path"] = HierarchyPath(item),
                ["interesting_fields"] = ProbeInterestingFields(item, DeviceFieldTokens, MaxDeviceFields),
            };

            var nameSignal = string.Format(
                "{0} {1} {2}",
                Convert.ToString(BestName(item)) ?? string.Empty,
                Convert.ToString(TypeName(item)) ?? string.Empty,
                GameObjectName(item)
            );
            var hits = TokenHits(nameSignal, DeviceCandidateTokens, 8);
            row["device_token_hits"] = hits;
            row["device_score"] = hits.Count;

            var pdaId = ReadMember(item, "PdaId", "pdaId", "PDAID", "strPDAID", "idPDA", "pda_id");
            if (pdaId != null)
            {
                row["pda_id"] = SafeValue(pdaId);
            }

            var filesystem = ReadMember(item, "filesystem", "FileSystem", "fs", "_fs", "objFilesystem", "files", "Files");
            if (filesystem != null)
            {
                row["filesystem"] = SummarizeInspectableObject(filesystem, 1);
            }

            var expansionSlots = ReadMember(item, "expansionSlots", "ExpansionSlots", "slots", "Slots", "aSlots", "SlotList", "aExpansionSlots");
            if (expansionSlots != null)
            {
                row["expansion_slots"] = SummarizeCollection(expansionSlots, MaxDeviceEntries);
            }

            var contacts = ReadMember(item, "contacts", "Contacts", "aContacts", "ContactList", "phonebook", "PhoneBook", "addressBook", "AddressBook");
            if (contacts != null)
            {
                row["contacts"] = SummarizeCollection(contacts, MaxDeviceEntries);
            }

            rows.Add(row);
        }

        private static JObject SummarizeInspectableObject(object value, int depth)
        {
            if (value == null)
            {
                return null;
            }

            var result = new JObject
            {
                ["id"] = StableId(value),
                ["name"] = BestName(value),
                ["type"] = TypeName(value),
            };

            var fields = ProbeInterestingFields(value, DeviceFieldTokens, MaxDeviceFields);
            if (fields.Count > 0)
            {
                result["interesting_fields"] = fields;
            }

            if (depth > 0)
            {
                var nestedCollections = new JObject();
                var nestedNames = new[] { "filesystem", "FileSystem", "files", "Files", "contacts", "Contacts", "slots", "Slots", "expansionSlots", "ExpansionSlots" };
                foreach (var nestedName in nestedNames)
                {
                    var nested = ReadMember(value, nestedName);
                    if (nested == null)
                    {
                        continue;
                    }

                    nestedCollections[nestedName] = SummarizeCollection(nested, MaxDeviceEntries);
                }

                if (nestedCollections.Count > 0)
                {
                    result["collections"] = nestedCollections;
                }
            }

            return result;
        }

        private static JObject SummarizeCollection(object source, int limit)
        {
            if (source == null)
            {
                return null;
            }

            var result = new JObject
            {
                ["count"] = Count(source),
                ["truncated"] = Count(source) > limit,
            };

            var sample = new JArray();
            var enumerated = 0;
            foreach (var item in Enumerate(source, limit))
            {
                enumerated++;
                if (item == null)
                {
                    continue;
                }

                if (IsSimple(item))
                {
                    sample.Add(new JValue(SafeValue(item)));
                    continue;
                }

                sample.Add(new JObject
                {
                    ["id"] = StableId(item),
                    ["name"] = BestName(item),
                    ["type"] = TypeName(item),
                    ["value"] = ProbeInterestingFields(item, DeviceFieldTokens, 12),
                });
            }

            if (sample.Count > 0)
            {
                result["sample"] = sample;
            }

            if (enumerated > 0)
            {
                result["sample_count"] = enumerated;
            }

            return result;
        }

        private static JArray ProbeInterestingFields(object root, string[] tokens, int maxFields)
        {
            var fields = new JArray();
            if (root == null)
            {
                return fields;
            }

            var type = root.GetType();
            var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var field in type.GetFields(flags))
            {
                if (fields.Count >= maxFields)
                {
                    break;
                }
                if (!ContainsAnyToken(field.Name, tokens))
                {
                    continue;
                }

                object value;
                try
                {
                    value = field.GetValue(root);
                }
                catch
                {
                    continue;
                }

                if (value == null)
                {
                    continue;
                }

                var key = type.Name + "." + field.Name;
                if (!seen.Add(key))
                {
                    continue;
                }

                fields.Add(new JObject
                {
                    ["source"] = type.Name,
                    ["name"] = field.Name,
                    ["value"] = SummarizeValue(value, tokens, 6),
                });
            }

            foreach (var property in type.GetProperties(flags))
            {
                if (fields.Count >= maxFields)
                {
                    break;
                }
                if (property.GetIndexParameters().Length != 0 || !ContainsAnyToken(property.Name, tokens))
                {
                    continue;
                }

                object value;
                try
                {
                    value = property.GetValue(root, null);
                }
                catch
                {
                    continue;
                }

                if (value == null)
                {
                    continue;
                }

                var key = type.Name + "." + property.Name;
                if (!seen.Add(key))
                {
                    continue;
                }

                fields.Add(new JObject
                {
                    ["source"] = type.Name,
                    ["name"] = property.Name,
                    ["value"] = SummarizeValue(value, tokens, 6),
                });
            }

            return fields;
        }

        private static JToken SummarizeValue(object value, string[] tokens, int sampleLimit)
        {
            if (value == null)
            {
                return JValue.CreateNull();
            }

            if (IsSimple(value))
            {
                return new JValue(SafeValue(value));
            }

            var enumerable = value as IEnumerable;
            if (enumerable != null && value is string == false)
            {
                var collection = SummarizeCollection(value, sampleLimit);
                return collection != null ? (JToken)collection : JValue.CreateNull();
            }

            var inspectable = SummarizeInspectableObject(value, 0);
            return inspectable != null ? (JToken)inspectable : new JValue(SafeValue(value));
        }

        private static bool LooksDeviceCandidate(object value)
        {
            if (value == null)
            {
                return false;
            }

            var typeName = value.GetType().Name.ToLowerInvariant();
            var bestName = Convert.ToString(BestName(value)) ?? string.Empty;
            var gameObjectName = string.Empty;

            if (value is Component component)
            {
                gameObjectName = component.gameObject != null ? component.gameObject.name ?? string.Empty : string.Empty;
            }
            else if (value is GameObject gameObject)
            {
                gameObjectName = gameObject.name ?? string.Empty;
            }

            var loweredName = bestName.ToLowerInvariant();
            var loweredGameObjectName = gameObjectName.ToLowerInvariant();

            return ContainsAnyToken(typeName, DeviceCandidateTokens) ||
                ContainsAnyToken(loweredName, DeviceCandidateTokens) ||
                ContainsAnyToken(loweredGameObjectName, DeviceCandidateTokens);
        }

        private static bool ContainsAnyToken(string name, string[] tokens)
        {
            var lowered = (name ?? string.Empty).ToLowerInvariant();
            foreach (var token in tokens)
            {
                if (lowered.Contains(token))
                {
                    return true;
                }
            }
            return false;
        }

        private static void AppendUiCandidate(JArray rows, HashSet<string> seen, object item, string source)
        {
            AppendUiCandidate(rows, seen, item, source, null);
        }

        private static void AppendUiCandidate(JArray rows, HashSet<string> seen, object item, string source, float? distance)
        {
            if (item == null || rows.Count >= MaxUiCandidates)
            {
                return;
            }

            var id = StableId(item);
            var idText = id.Type == JTokenType.Null ? string.Empty : Convert.ToString(id);
            var uniqueKey = !string.IsNullOrEmpty(idText)
                ? idText + "|" + source
                : BestName(item) + "|" + TypeName(item) + "|" + source;
            if (!seen.Add(uniqueKey))
            {
                return;
            }

            var row = new JObject
            {
                ["id"] = id,
                ["name"] = BestName(item),
                ["type"] = TypeName(item),
                ["source"] = source,
                ["position"] = Position(item),
                ["distance"] = distance.HasValue ? new JValue(Math.Round(distance.Value, 2)) : JValue.CreateNull(),
                ["active"] = Boolean(ReadMember(item, "activeInHierarchy", "activeSelf", "isActiveAndEnabled", "enabled")),
                ["component_type"] = ComponentTypeName(item),
                ["game_object"] = GameObjectName(item),
                ["hierarchy_path"] = HierarchyPath(item),
            };

            var nameSignal = string.Format(
                "{0} {1} {2}",
                Convert.ToString(BestName(item)) ?? string.Empty,
                Convert.ToString(TypeName(item)) ?? string.Empty,
                GameObjectName(item)
            );
            var hits = TokenHits(nameSignal, InteractionSignalTokens, 8);
            row["ui_token_hits"] = hits;
            row["ui_score"] = hits.Count;

            rows.Add(row);
        }

        private static string ComponentTypeName(object value)
        {
            var component = value as Component;
            return component != null ? component.GetType().Name : null;
        }

        private static string GameObjectName(object value)
        {
            var gameObject = ResolveGameObject(value);
            return gameObject != null ? gameObject.name : null;
        }

        private static string HierarchyPath(object value)
        {
            var gameObject = ResolveGameObject(value);
            if (gameObject == null)
            {
                return null;
            }

            var chain = new List<string>();
            var current = gameObject.transform;
            var depth = 0;
            while (current != null && depth++ < 24)
            {
                chain.Add(current.name ?? string.Empty);
                current = current.parent;
            }

            chain.Reverse();
            return string.Join("/", chain.ToArray());
        }

        private static GameObject ResolveGameObject(object value)
        {
            if (value == null)
            {
                return null;
            }

            var component = value as Component;
            if (component != null)
            {
                return component.gameObject;
            }

            return value as GameObject;
        }

        private static JArray TokenHits(string text, string[] tokens, int maxHits)
        {
            var result = new JArray();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var lowered = (text ?? string.Empty).ToLowerInvariant();

            foreach (var token in tokens)
            {
                if (result.Count >= maxHits)
                {
                    break;
                }

                if (!string.IsNullOrWhiteSpace(token) && lowered.Contains(token) && seen.Add(token))
                {
                    result.Add(token);
                }
            }

            return result;
        }

        private static float? DistanceFrom(Vector3? origin, Vector3? target)
        {
            if (!origin.HasValue || !target.HasValue)
            {
                return null;
            }
            return Vector3.Distance(origin.Value, target.Value);
        }

        private static Vector3? GetWorldPosition(object value)
        {
            if (value == null)
            {
                return null;
            }

            var posValue = ReadMember(value, "vPos", "Position", "vShipPos", "position", "vWorldPos", "WorldPosition");
            var converted = ConvertVector3(posValue);
            if (converted.HasValue)
            {
                return converted;
            }

            var component = value as Component;
            if (component != null)
            {
                return component.transform.position;
            }

            var gameObject = value as GameObject;
            if (gameObject != null)
            {
                return gameObject.transform.position;
            }

            return null;
        }

        private static Vector3? ConvertVector3(object value)
        {
            if (value == null)
            {
                return null;
            }
            if (value is Vector3 vector3)
            {
                return vector3;
            }

            var x = ToSingle(ReadMember(value, "x"));
            var y = ToSingle(ReadMember(value, "y"));
            var z = ToSingle(ReadMember(value, "z"));
            if (x.HasValue && y.HasValue && z.HasValue)
            {
                return new Vector3(x.Value, y.Value, z.Value);
            }
            return null;
        }

        private static float? ToSingle(object value)
        {
            if (value == null)
            {
                return null;
            }
            try
            {
                return Convert.ToSingle(value);
            }
            catch
            {
                return null;
            }
        }

        private static bool LooksInteractable(GameObject gameObject)
        {
            var name = (gameObject.name ?? string.Empty).ToLowerInvariant();
            foreach (var token in InteractionSignalTokens)
            {
                if (name.Contains(token))
                {
                    return true;
                }
            }

            foreach (var component in gameObject.GetComponents<Component>())
            {
                if (component == null)
                {
                    continue;
                }
                var typeName = component.GetType().Name.ToLowerInvariant();
                foreach (var token in InteractionSignalTokens)
                {
                    if (typeName.Contains(token))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        private static bool LooksUiCandidate(object value)
        {
            if (value == null)
            {
                return false;
            }

            var typeName = value.GetType().Name.ToLowerInvariant();
            var bestName = Convert.ToString(BestName(value)) ?? string.Empty;
            var gameObjectName = string.Empty;

            if (value is Component component)
            {
                gameObjectName = component.gameObject != null ? component.gameObject.name ?? string.Empty : string.Empty;
            }
            else if (value is GameObject gameObject)
            {
                gameObjectName = gameObject.name ?? string.Empty;
            }

            var loweredName = bestName.ToLowerInvariant();
            var loweredGameObjectName = gameObjectName.ToLowerInvariant();

            foreach (var token in InteractionSignalTokens)
            {
                if (typeName.Contains(token) || loweredName.Contains(token) || loweredGameObjectName.Contains(token))
                {
                    return true;
                }
            }

            return false;
        }

        private static bool ContainsSignalToken(string name)
        {
            var lowered = (name ?? string.Empty).ToLowerInvariant();
            foreach (var token in InteractionSignalTokens)
            {
                if (lowered.Contains(token))
                {
                    return true;
                }
            }
            return false;
        }

        private static JArray SummarizeObjectives()
        {
            var result = new JArray();
            var trackerType = FindType("Ostranauts.Objectives.ObjectiveTracker");
            if (trackerType == null)
            {
                return result;
            }

            var trackers = Resources.FindObjectsOfTypeAll(trackerType);
            if (trackers == null || trackers.Length == 0)
            {
                return result;
            }

            foreach (var item in Enumerate(ReadMember(trackers[0], "AllObjectives", "_allObjectives"), MaxObjectives))
            {
                result.Add(new JObject
                {
                    ["name"] = Text(ReadMember(item, "strDisplayName")),
                    ["description"] = Text(ReadMember(item, "strDisplayDesc")),
                    ["plot"] = Text(ReadMember(item, "strPlotName")),
                    ["complete"] = Boolean(ReadMember(item, "Complete", "Finished")),
                    ["tutorial"] = Boolean(ReadMember(item, "bTutorial")),
                    ["started_at"] = Number(ReadMember(item, "fTimeStart"))
                });
            }
            return result;
        }

        private static object ResolveCondOwner(object value)
        {
            if (value == null)
            {
                return null;
            }
            if (value.GetType().Name == "CondOwner")
            {
                return value;
            }

            var nested = ReadMember(value, "CO", "co", "_co", "objCO");
            if (nested != null && nested.GetType().Name == "CondOwner")
            {
                return nested;
            }

            var condOwnerType = FindType("CondOwner");
            var gameObject = value as GameObject;
            var component = value as Component;
            if (gameObject == null && component != null)
            {
                gameObject = component.gameObject;
            }
            return gameObject != null && condOwnerType != null ? gameObject.GetComponent(condOwnerType) : null;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(fullName, false);
                if (type != null)
                {
                    return type;
                }
            }
            return null;
        }

        private static object ReadStatic(Type type, params string[] names)
        {
            return type == null ? null : ReadMember(type, names);
        }

        private static object ReadMember(object target, params string[] names)
        {
            if (target == null)
            {
                return null;
            }

            var staticType = target as Type;
            var type = staticType ?? target.GetType();
            var instance = staticType == null ? target : null;
            var flags = BindingFlags.Public | BindingFlags.NonPublic |
                (instance == null ? BindingFlags.Static : BindingFlags.Instance);

            foreach (var name in names)
            {
                try
                {
                    var field = type.GetField(name, flags);
                    if (field != null)
                    {
                        var value = field.GetValue(instance);
                        if (value != null)
                        {
                            return value;
                        }
                        continue;
                    }
                    var property = type.GetProperty(name, flags);
                    if (property != null && property.GetIndexParameters().Length == 0)
                    {
                        var value = property.GetValue(instance, null);
                        if (value != null)
                        {
                            return value;
                        }
                        continue;
                    }
                    var getter = type.GetMethod("get_" + name, flags, null, Type.EmptyTypes, null);
                    if (getter != null)
                    {
                        var value = getter.Invoke(instance, null);
                        if (value != null)
                        {
                            return value;
                        }
                    }
                }
                catch
                {
                }
            }
            return null;
        }

        private static bool? InvokeBoolean(object target, string methodName)
        {
            if (target == null)
            {
                return null;
            }
            try
            {
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                var method = target.GetType().GetMethod(methodName, flags, null, Type.EmptyTypes, null);
                if (method == null)
                {
                    return null;
                }
                return Convert.ToBoolean(method.Invoke(target, null));
            }
            catch
            {
                return null;
            }
        }

        private static IEnumerable Enumerate(object source, int limit)
        {
            var enumerable = source as IEnumerable;
            if (enumerable == null || source is string)
            {
                yield break;
            }

            var count = 0;
            foreach (var item in enumerable)
            {
                if (count++ >= limit)
                {
                    yield break;
                }
                yield return item;
            }
        }

        private static int Count(object source)
        {
            var collection = source as ICollection;
            if (collection != null)
            {
                return collection.Count;
            }
            var count = 0;
            foreach (var ignored in Enumerate(source, 256))
            {
                count++;
            }
            return count;
        }

        private static JToken Vector(object value)
        {
            if (value == null)
            {
                return JValue.CreateNull();
            }
            return new JObject
            {
                ["x"] = Number(ReadMember(value, "x")),
                ["y"] = Number(ReadMember(value, "y")),
                ["z"] = Number(ReadMember(value, "z"))
            };
        }

        private static JToken Position(object value)
        {
            var direct = Vector(ReadMember(value, "vPos", "Position", "vShipPos", "position"));
            if (direct.Type == JTokenType.Object)
            {
                return direct;
            }

            var component = value as Component;
            if (component != null)
            {
                return new JObject
                {
                    ["x"] = component.transform.position.x,
                    ["y"] = component.transform.position.y,
                    ["z"] = component.transform.position.z,
                };
            }

            var gameObject = value as GameObject;
            if (gameObject != null)
            {
                return new JObject
                {
                    ["x"] = gameObject.transform.position.x,
                    ["y"] = gameObject.transform.position.y,
                    ["z"] = gameObject.transform.position.z,
                };
            }

            return JValue.CreateNull();
        }

        private static JToken StableId(object value)
        {
            var direct = ReadMember(value, "strID", "ID", "id", "Guid", "guid", "strGuid", "GUID");
            if (direct != null)
            {
                return Text(direct);
            }

            var component = value as Component;
            if (component != null)
            {
                return new JValue(component.GetInstanceID().ToString());
            }

            var gameObject = value as GameObject;
            if (gameObject != null)
            {
                return new JValue(gameObject.GetInstanceID().ToString());
            }

            return value == null ? JValue.CreateNull() : new JValue(value.GetHashCode().ToString());
        }

        private static JToken BestName(object value)
        {
            var direct = ReadMember(value, "FriendlyName", "strNameFriendly", "strName", "publicName", "DisplayName", "Name", "name");
            if (direct != null)
            {
                return Text(direct);
            }

            var component = value as Component;
            if (component != null)
            {
                return new JValue(component.gameObject.name);
            }

            var gameObject = value as GameObject;
            if (gameObject != null)
            {
                return new JValue(gameObject.name);
            }

            return value == null ? JValue.CreateNull() : new JValue(value.GetType().Name);
        }

        private static JToken TypeName(object value)
        {
            var direct = ReadMember(value, "strType", "TypeName", "Category", "strCategory");
            if (direct != null)
            {
                return Text(direct);
            }
            return value == null ? JValue.CreateNull() : new JValue(value.GetType().Name);
        }

        private static JToken SafeValue(object value)
        {
            if (value == null)
            {
                return JValue.CreateNull();
            }
            return IsSimple(value) ? JToken.FromObject(value) : new JValue(value.ToString());
        }

        private static bool IsSimple(object value)
        {
            if (value == null)
            {
                return true;
            }
            var type = value.GetType();
            return type.IsPrimitive || type.IsEnum || value is decimal || value is string;
        }

        private static JToken Text(object value)
        {
            return value == null ? JValue.CreateNull() : new JValue(Convert.ToString(value));
        }

        private static JToken Number(object value)
        {
            if (value == null)
            {
                return JValue.CreateNull();
            }
            try
            {
                return new JValue(Convert.ToDouble(value));
            }
            catch
            {
                return JValue.CreateNull();
            }
        }

        private static JToken Boolean(object value)
        {
            if (value == null)
            {
                return JValue.CreateNull();
            }
            try
            {
                return new JValue(Convert.ToBoolean(value));
            }
            catch
            {
                return JValue.CreateNull();
            }
        }
    }
}
