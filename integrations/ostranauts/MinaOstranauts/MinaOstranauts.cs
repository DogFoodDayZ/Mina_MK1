using System;
using System.IO;
using BepInEx;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Mina.Ostranauts
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class MinaOstranautsPlugin : BaseUnityPlugin
    {
        public const string PluginGuid = "com.mina.ostranauts.bridge";
        public const string PluginName = "Mina Ostranauts Bridge";
        public const string PluginVersion = "0.6.1";

        private string telemetryDirectory;
        private string telemetryPath;
        private float nextWriteAt;

        private void Awake()
        {
            telemetryDirectory = Path.Combine(Paths.ConfigPath, "MinaOstranauts");
            telemetryPath = Path.Combine(telemetryDirectory, "telemetry.json");
            Directory.CreateDirectory(telemetryDirectory);
            WriteSnapshot("online");
            Logger.LogInfo("Mina read-only telemetry bridge online.");
        }

        private void Update()
        {
            if (Time.realtimeSinceStartup < nextWriteAt)
            {
                return;
            }

            nextWriteAt = Time.realtimeSinceStartup + 5f;
            WriteSnapshot("online");
        }

        private void OnDestroy()
        {
            WriteSnapshot("stopped");
        }

        private void WriteSnapshot(string bridgeState)
        {
            try
            {
                var scene = SceneManager.GetActiveScene();
                var snapshot = new JObject
                {
                    ["schema_version"] = 6,
                    ["written_at_unix"] = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds,
                    ["bridge"] = bridgeState,
                    ["read_only"] = true,
                    ["plugin_version"] = PluginVersion,
                    ["game_product"] = Application.productName,
                    ["game_version"] = Application.version,
                    ["unity_version"] = Application.unityVersion,
                    ["scene"] = scene.IsValid() ? scene.name : "unknown",
                    ["scene_build_index"] = scene.IsValid() ? scene.buildIndex : -1,
                    ["game_focused"] = Application.isFocused,
                    ["session_uptime_seconds"] = Math.Round(Time.realtimeSinceStartup, 1),
                    ["capabilities"] = new JArray(
                        "bridge_health",
                        "active_scene",
                        "session_uptime"
                    )
                };

                TelemetryReader.Populate(snapshot);

                WriteAtomically(telemetryPath, snapshot.ToString(Formatting.Indented));
            }
            catch (Exception exc)
            {
                Logger.LogWarning("Mina telemetry write failed: " + exc.Message);
            }
        }

        private static void WriteAtomically(string destination, string content)
        {
            var temporary = destination + ".tmp";
            File.WriteAllText(temporary, content);

            if (!File.Exists(destination))
            {
                File.Move(temporary, destination);
                return;
            }

            try
            {
                File.Replace(temporary, destination, null);
            }
            catch (PlatformNotSupportedException)
            {
                File.Delete(destination);
                File.Move(temporary, destination);
            }
            catch (IOException)
            {
                File.Delete(destination);
                File.Move(temporary, destination);
            }
        }
    }
}