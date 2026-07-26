using System;
using System.Collections;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Mina.Ostranauts
{
    internal static class TelemetryReader
    {
        private const int MaxCrew = 16;
        private const int MaxConditions = 32;
        private const int MaxInventory = 48;
        private const int MaxObjectives = 24;

        internal static void Populate(JObject snapshot)
        {
            var capabilities = snapshot["capabilities"] as JArray ?? new JArray();
            var diagnostics = new JArray();
            snapshot["capabilities"] = capabilities;
            snapshot["diagnostics"] = diagnostics;

            var crewSim = ReadStatic(FindType("CrewSim"), "objInstance");
            if (crewSim == null)
            {
                diagnostics.Add("crew_sim_unavailable");
                return;
            }

            var ship = ReadMember(crewSim, "shipPlayerOwned", "shipCurrentLoaded", "jsonShip");
            if (ship == null)
            {
                ship = FindPlayerShip(ReadMember(crewSim, "aLoadedShips"));
            }

            var crew = ReadMember(crewSim, "aCrew");
            if (Count(crew) == 0 && ship != null)
            {
                crew = ReadMember(ship, "aPeople", "People");
            }

            var player = ReadMember(crewSim, "coPlayer", "visPlayer");
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

            var result = new JObject
            {
                ["id"] = Text(ReadMember(owner, "strID", "_strID")),
                ["name"] = Text(ReadMember(owner, "FriendlyName", "strNameFriendly", "strName")),
                ["short_name"] = Text(ReadMember(owner, "ShortName", "strNameShort")),
                ["type"] = Text(ReadMember(owner, "strType")),
                ["alive"] = Boolean(ReadMember(owner, "bAlive")),
                ["destroyed"] = Boolean(ReadMember(owner, "bDestroyed")),
                ["conditions"] = SummarizeConditions(ReadMember(owner, "mapConds"))
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

                var condition = new JObject { ["name"] = Text(entry.Key) };
                var value = entry.Value;
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
