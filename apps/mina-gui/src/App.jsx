import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { invoke } from "@tauri-apps/api/core";
import "./crt.css";
import avatarIdle from "./assets/avatar_idle.png";
import avatarTalk from "./assets/avatar_talk.png";
import avatarTalk2 from "./assets/avatar_talk_2.png";
import avatarSmirk from "./assets/avatar_smirk.png";
import OstranautsCompanion from "./components/OstranautsCompanion";
import { formatActionReports } from "./actionReports";

const API_BASE = "http://127.0.0.1:8000";
const GUI_VOICE_HINT = "en-US-AnaNeural";
const USER_DISPLAY_NAME = "TRAVIS";

// Live VAD waveform for the audio tab. Draws input RMS over time with the
// adaptive threshold as a reference line, so you can see speech spikes vs the
// ambient floor in real time.
function WaveformCanvas({ history, threshold }) {
    const canvasRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        const pts = Array.isArray(history) ? history : [];
        if (!pts.length) {
            ctx.fillStyle = "#2a4a44";
            ctx.font = "11px monospace";
            ctx.fillText("no signal — start voice-in", 8, h / 2);
            return;
        }

        const maxVal = Math.max(threshold * 2, ...pts.map((p) => Number(p.rms) || 0), 0.001);
        const n = pts.length;
        const step = w / Math.max(1, n - 1);

        // Threshold reference line.
        const ty = h - (Math.min(threshold, maxVal) / maxVal) * h;
        ctx.strokeStyle = "#ff4a5e";
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(0, ty);
        ctx.lineTo(w, ty);
        ctx.stroke();
        ctx.setLineDash([]);

        // RMS waveform.
        ctx.strokeStyle = "#2cfaa2";
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        pts.forEach((p, i) => {
            const x = i * step;
            const y = h - (Math.min(Number(p.rms) || 0, maxVal) / maxVal) * h;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }, [history, threshold]);

    return <canvas ref={canvasRef} className="mk1-waveform" width={560} height={90} />;
}

const COMMANDS = [
    "/help",
    "/diag",
    "/clear",
    "/system",
    "/core",
    "/echo",
    "/time",
    "/about",
];

function startSerialPoll(poll, intervalMs) {
    let stopped = false;
    let timeoutId = null;

    async function run() {
        try {
            await poll();
        } catch (err) {
            console.error("Polling task failed", err);
        } finally {
            if (!stopped) {
                timeoutId = setTimeout(run, intervalMs);
            }
        }
    }

    void run();

    return () => {
        stopped = true;
        if (timeoutId) clearTimeout(timeoutId);
    };
}

export default function App() {
    const isOstranautsCompanion = new URLSearchParams(window.location.search).get("view") === "ostranauts";
    const [activeTab, setActiveTab] = useState("chat");
    const [input, setInput] = useState("");
    const [messages, setMessages] = useState([
        {
            from: "SYSTEM",
            text: "MK1 LINK STABILIZED. LOCAL CORE AND MEMORY ENDPOINTS ARE LIVE.",
        },
    ]);
    const [statusNote, setStatusNote] = useState("");

    const [showEmoji, setShowEmoji] = useState(false);
    const [emojiPos, setEmojiPos] = useState({ x: 0, y: 0 });

    const [systemStatus, setSystemStatus] = useState(null);
    const [dbStatus, setDbStatus] = useState(null);
    const [corePullout, setCorePullout] = useState({
        online: false,
        heartbeatTick: 0,
        heartbeatElapsedSeconds: 0,
        secondsSinceInput: null,
        lastUserActivityAt: "",
        runtimeStateWrittenAt: "",
        memoryEngine: "unknown",
        toolCount: 0,
        idleCheckinPending: false,
        awayNagPending: false,
        voiceRunning: false,
        voicePid: null,
        queueDueCount: 0,
        queueCount: 0,
        queueAvailable: false,
        lastSync: "",
    });
    const [curiosityPullout, setCuriosityPullout] = useState({
        online: false,
        enabled: false,
        running: false,
        paused: false,
        queuedWorkCount: 0,
        eventCount: 0,
        currentTask: "",
        lastSync: "",
    });
    const [curiosityStatus, setCuriosityStatus] = useState(null);
    const [autonomyEvents, setAutonomyEvents] = useState([]);
    const [approvedAutonomyTasks, setApprovedAutonomyTasks] = useState([]);
    const [autonomyTaskBusy, setAutonomyTaskBusy] = useState(false);
    const [autonomyDraft, setAutonomyDraft] = useState({
        title: "",
        proof_target: "",
        allowed_action: "read_only",
        done_when: "",
        source_task_key: "",
    });
    const [imageAttachments, setImageAttachments] = useState([]);
    const [activeImageIndex, setActiveImageIndex] = useState(0);
    const [imageStatus, setImageStatus] = useState("Paste, drop, or choose an image.");
    const [modelStatus, setModelStatus] = useState({
        model: "unknown",
        supportsVision: false,
        mode: "TEXT_ONLY",
        ready: false,
        allowedModels: [],
    });
    const [isCoreSpeaking, setIsCoreSpeaking] = useState(false);
    const [talkFrameAlt, setTalkFrameAlt] = useState(false);
    const [avatarMood, setAvatarMood] = useState("idle");
    const [apiServiceUp, setApiServiceUp] = useState(true);
    const [voiceServiceUp, setVoiceServiceUp] = useState(false);
    const [voiceMetrics, setVoiceMetrics] = useState(null);
    const [voiceMetricsHistory, setVoiceMetricsHistory] = useState([]);
    const [voiceDevices, setVoiceDevices] = useState([]);
    const [voiceCurrentDevice, setVoiceCurrentDevice] = useState(null);
    const [voiceDeviceLevels, setVoiceDeviceLevels] = useState({});
    const [isProbingDevices, setIsProbingDevices] = useState(false);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [isAskingSummary, setIsAskingSummary] = useState(false);
    const [isStartingApi, setIsStartingApi] = useState(false);
    const [isStartingVoice, setIsStartingVoice] = useState(false);
    const [voiceOutputOn, setVoiceOutputOn] = useState(false);
    const [isOpeningRestore, setIsOpeningRestore] = useState(false);
    const [isSwitchingModel, setIsSwitchingModel] = useState(false);
    const [altModelActive, setAltModelActive] = useState(false);
    const [isSwitchingAltModel, setIsSwitchingAltModel] = useState(false);
    const [taskWindow, setTaskWindow] = useState({
        tasks: [],
        dueCount: 0,
        upcomingCount: 0,
        online: false,
        error: "",
        lastSync: "",
    });
    const [taskActionBusy, setTaskActionBusy] = useState("");
    const [selectedTaskId, setSelectedTaskId] = useState(null);
    const [taskDraft, setTaskDraft] = useState({ text: "", dueAt: "", windowEnd: "", repeatMinutes: "25", alarm: true });
    const [taskCreateBusy, setTaskCreateBusy] = useState(false);

    const consoleRef = useRef(null);
    const emojiBtnRef = useRef(null);
    const emojiPanelRef = useRef(null);
    const imageInputRef = useRef(null);
    const lastVoiceEventIdRef = useRef(
        Number(window.sessionStorage.getItem("mina:lastVoiceEventId") || 0)
    );
    const lastCuriosityEventIdRef = useRef(0);
    const speakTimeoutRef = useRef(null);
    const attachmentsRef = useRef([]);
    const dueTaskPrimedRef = useRef(false);
    const seenDueTaskIdsRef = useRef(new Set());
    const coreStreamMsgIdRef = useRef(null);

    useEffect(() => {
        attachmentsRef.current = imageAttachments;
    }, [imageAttachments]);

    useEffect(() => {
        return () => {
            for (const item of attachmentsRef.current || []) {
                if (item?.objectUrl) {
                    URL.revokeObjectURL(item.objectUrl);
                }
            }
        };
    }, []);

    useEffect(() => {
        return () => {
            if (speakTimeoutRef.current) {
                clearTimeout(speakTimeoutRef.current);
            }
        };
    }, []);

    useEffect(() => {
        if (!isCoreSpeaking) return;

        const id = setInterval(() => {
            setTalkFrameAlt((prev) => !prev);
        }, 220);

        return () => clearInterval(id);
    }, [isCoreSpeaking]);

    async function requestJson(path, options = {}) {
        const { timeoutMs = 15000, ...fetchOptions } = options;
        const controller = new AbortController();
        let timedOut = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeoutMs);

        try {
            const res = await fetch(`${API_BASE}${path}`, {
                ...fetchOptions,
                signal: controller.signal,
            });
            const text = await res.text();
            let data = null;

            if (text) {
                try {
                    data = JSON.parse(text);
                } catch {
                    data = text;
                }
            }

            if (!res.ok) {
                const message =
                    typeof data === "string"
                        ? data
                        : data?.detail ?? data?.error ?? `HTTP ${res.status}`;

                throw new Error(message);
            }

            return data;
        } catch (err) {
            if (timedOut) {
                throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
            }
            throw err;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    async function invokeTauri(command, args = {}) {
        try {
            return await invoke(command, args);
        } catch {
            return null;
        }
    }

    function appendMessage(from, text) {
        setMessages((prev) => [...prev, { from, text }]);
    }

    function triggerAvatarSpeechPulse(ms = 2200) {
        if (speakTimeoutRef.current) {
            clearTimeout(speakTimeoutRef.current);
        }
        setAvatarMood("talk");
        setIsCoreSpeaking(true);
        speakTimeoutRef.current = setTimeout(() => {
            setIsCoreSpeaking(false);
            setAvatarMood("smirk");
            setTimeout(() => setAvatarMood("idle"), 900);
        }, ms);
    }

    function clearImageAttachment(indexToRemove = activeImageIndex) {
        setImageAttachments((prev) => {
            if (!prev.length) return prev;
            const idx = Math.max(0, Math.min(indexToRemove, prev.length - 1));
            const removed = prev[idx];
            if (removed?.objectUrl) {
                URL.revokeObjectURL(removed.objectUrl);
            }
            const next = prev.filter((_, i) => i !== idx);
            const nextActive = Math.max(0, Math.min(idx, next.length - 1));
            setActiveImageIndex(nextActive);
            if (!next.length) {
                setImageStatus("Paste, drop, or choose an image.");
            } else {
                setImageStatus(`Active image: ${next[nextActive].name}`);
            }
            return next;
        });
    }

    async function fileToAttachment(file) {
        if (!file || !file.type?.startsWith("image/")) {
            throw new Error("Please use an image file.");
        }

        const objectUrl = URL.createObjectURL(file);
        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();

            reader.onload = () => resolve(String(reader.result || ""));
            reader.onerror = () => reject(new Error("Could not read image file."));
            reader.readAsDataURL(file);
        });

        return {
            name: file.name || "pasted-image",
            type: file.type || "image/*",
            size: file.size || 0,
            objectUrl,
            dataUrl,
        };
    }

    async function acceptImageFiles(fileList) {
        const files = Array.from(fileList || []);
        if (!files.length) return;

        const imageFiles = files.filter((f) => f?.type?.startsWith("image/"));
        if (!imageFiles.length) {
            setImageStatus("Please use image files.");
            return;
        }

        try {
            const currentCount = imageAttachments.length;
            const availableSlots = Math.max(0, 3 - currentCount);
            if (availableSlots <= 0) {
                setImageStatus("Image slots full (3/3). Remove one to add another.");
                return;
            }

            const selected = imageFiles.slice(0, availableSlots);
            const built = [];
            for (const file of selected) {
                built.push(await fileToAttachment(file));
            }

            if (!built.length) return;

            setImageAttachments((prev) => {
                const next = [...prev, ...built].slice(0, 3);
                setActiveImageIndex(next.length - 1);
                return next;
            });

            const loadedNames = built.map((x) => x.name).join(", ");
            const total = Math.min(3, currentCount + built.length);
            setImageStatus(`Loaded ${built.length} image(s): ${loadedNames}. Slots used: ${total}/3`);
            appendMessage("SYSTEM", `Image staged: ${loadedNames}`);
        } catch (err) {
            setImageStatus(`Image load failed: ${err.message}`);
        }
    }

    function handleImageFiles(files) {
        if (!files?.length) return;
        acceptImageFiles(files);
    }

    function handleImagePaste(event) {
        const clipboard = event.clipboardData;
        const plainText = String(clipboard?.getData("text/plain") || "").trim();
        const htmlText = String(clipboard?.getData("text/html") || "").trim();

        // If clipboard includes text (including emoji glyph text), do not auto-stage images.
        if (plainText || htmlText) {
            return;
        }

        const items = clipboard?.items;
        if (!items?.length) return;

        for (const item of items) {
            if (item.type?.startsWith("image/")) {
                const file = item.getAsFile();
                if (file) {
                    event.preventDefault();
                    acceptImageFiles([file]);
                    return;
                }
            }
        }
    }

    function handleImageDrop(event) {
        event.preventDefault();
        const files = event.dataTransfer?.files;
        if (!files?.length) return;
        handleImageFiles(files);
    }

    function openImagePicker() {
        imageInputRef.current?.click();
    }

    function setActivity(text) {
        setStatusNote(text);
    }

    function mapCorePullout(data) {
        const voice = data?.voice_monitor && typeof data.voice_monitor === "object" ? data.voice_monitor : {};
        const queue = data?.task_queue && typeof data.task_queue === "object" ? data.task_queue : {};
        const memoryEngine = data?.memory_engine;
        const memoryStatus = memoryEngine?.status && typeof memoryEngine.status === "object" ? memoryEngine.status : {};

        return {
            online: Boolean(data?.core_online),
            modelLoaded: Boolean(data?.model_loaded),
            personalityLoaded: Boolean(data?.personality_loaded),
            heartbeatTick: Number(data?.heartbeat_tick || 0),
            heartbeatElapsedSeconds: Number(data?.heartbeat_elapsed_seconds || 0),
            secondsSinceInput: Number.isFinite(Number(data?.seconds_since_last_user_input))
                ? Number(data?.seconds_since_last_user_input)
                : null,
            lastUserActivityAt: String(data?.last_user_activity_at || ""),
            runtimeStateWrittenAt: String(data?.runtime_state_last_written_at || ""),
            memoryEngine: memoryEngine && typeof memoryEngine === "object"
                ? (memoryEngine?.status?.ok === false ? "DEGRADED" : "ONLINE")
                : String(memoryEngine || "unknown"),
            memoryDbPresent: Boolean(memoryEngine?.db_exists),
            smallIndexLoaded: Boolean(memoryEngine?.small_index_loaded),
            baseIndexLoaded: Boolean(memoryEngine?.base_index_loaded),
            smallIndexCount: Number(memoryEngine?.small_index_ntotal || 0),
            baseIndexCount: Number(memoryEngine?.base_index_ntotal || 0),
            memoryBufferPending: Number(memoryStatus?.buffer_pending_count || 0),
            memoryLastBackup: memoryStatus?.last_backup ?? null,
            memoryLastMaintenance: memoryStatus?.last_maintenance ?? null,
            toolCount: Number(data?.tool_count || 0),
            idleCheckinPending: Boolean(data?.idle_checkin_pending),
            awayNagPending: Boolean(data?.away_nag_pending),
            voiceRunning: Boolean(voice?.running),
            voicePid: voice?.pid ?? null,
            queueDueCount: Number(queue?.due_count || 0),
            queueCount: Number(queue?.queued_count || 0),
            queueAvailable: Boolean(queue?.available),
            lastSync: new Date().toLocaleTimeString(),
        };
    }

    function formatElapsed(seconds) {
        const value = Number(seconds);
        if (!Number.isFinite(value)) return "unknown";
        if (value < 60) return `${Math.round(value)}s ago`;
        if (value < 3600) return `${Math.floor(value / 60)}m ago`;
        return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m ago`;
    }

    function formatUnixTime(seconds) {
        const value = Number(seconds);
        return Number.isFinite(value) && value > 0
            ? new Date(value * 1000).toLocaleString()
            : "not recorded";
    }

    function mapCuriosityPullout(data) {
        return {
            online: true,
            enabled: Boolean(data?.enabled),
            running: Boolean(data?.running),
            paused: Boolean(data?.paused),
            queuedWorkCount: Number(data?.queued_work_count || 0),
            eventCount: Number(data?.event_count || 0),
            currentTask: String(data?.current_task?.description || data?.current_task?.name || ""),
            lastSync: new Date().toLocaleTimeString(),
        };
    }

    useEffect(() => {
        if (consoleRef.current) {
            consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
        }
    }, [messages]);

    useEffect(() => {
        function handleOutside(e) {
            if (!showEmoji) return;
            if (emojiBtnRef.current?.contains(e.target)) return;
            if (emojiPanelRef.current?.contains(e.target)) return;
            setShowEmoji(false);
        }

        document.addEventListener("mousedown", handleOutside);

        return () => {
            document.removeEventListener("mousedown", handleOutside);
        };
    }, [showEmoji]);

    async function refreshSystemStatus() {
        try {
            const data = await requestJson("/status");

            setSystemStatus({
                coreTemp: data.core_temp ?? "UNKNOWN",
                memoryBus: data.memory_bus ?? "UNKNOWN",
                neuralCache: data.neural_cache ?? "UNKNOWN",
                ioChannels: data.io_channels ?? "UNKNOWN",
                level: data.level ?? "NORMAL",
            });
            setCorePullout(mapCorePullout(data));

            setActivity("Mina telemetry refreshed.");
        } catch (err) {
            setSystemStatus((prev) =>
                prev ?? {
                    coreTemp: "WAITING FOR MINA...",
                    memoryBus: "WAITING FOR MINA...",
                    neuralCache: "WAITING FOR MINA...",
                    ioChannels: "WAITING FOR MINA...",
                    level: "WARN",
                }
            );
            setCorePullout((prev) => ({
                ...prev,
                online: false,
            }));
            setActivity(`Status refresh failed: ${err.message}`);
        }
    }

    async function refreshDbStatus() {
        try {
            const data = await requestJson("/db/status");

            setDbStatus({
                link: data.db_link ?? "UNKNOWN",
                sync: data.db_sync ?? "UNKNOWN",
                latency: data.db_latency ?? "UNKNOWN",
                activeConnections: data.active_connections ?? "UNKNOWN",
                readOps: data.read_ops ?? "UNKNOWN",
                writeOps: data.write_ops ?? "UNKNOWN",
                cacheState: data.cache_state ?? "UNKNOWN",
                lastCommit: data.last_commit ?? "UNKNOWN",
                level: data.level ?? "NORMAL",
            });

            setActivity("Database telemetry refreshed.");
        } catch (err) {
            setDbStatus((prev) =>
                prev ?? {
                    link: "WAITING FOR DB...",
                    sync: "WAITING FOR DB...",
                    latency: "WAITING FOR DB...",
                    activeConnections: "WAITING FOR DB...",
                    readOps: "WAITING FOR DB...",
                    writeOps: "WAITING FOR DB...",
                    cacheState: "WAITING FOR DB...",
                    lastCommit: "WAITING FOR DB...",
                    level: "WARN",
                }
            );
            setActivity(`DB refresh failed: ${err.message}`);
        }
    }

    async function refreshAllStatus() {
        if (isRefreshing) return;
        setIsRefreshing(true);
        try {
            await Promise.all([refreshSystemStatus(), refreshDbStatus()]);
        } finally {
            setIsRefreshing(false);
        }
    }

    useEffect(() => {
        let cancelled = false;

        async function pollStatus() {
            try {
                const data = await requestJson("/status");

                if (cancelled) return;

                setSystemStatus({
                    coreTemp: data.core_temp ?? "UNKNOWN",
                    memoryBus: data.memory_bus ?? "UNKNOWN",
                    neuralCache: data.neural_cache ?? "UNKNOWN",
                    ioChannels: data.io_channels ?? "UNKNOWN",
                    level: data.level ?? "NORMAL",
                });
                setCorePullout(mapCorePullout(data));
                setApiServiceUp(true);

                // Sync the voice-output toggle from the API's shared speak flag so
                // the indicator always reflects reality (tray auto-speak, etc.).
                syncVoiceOutputFromApi();
            } catch {
                if (cancelled) return;

                setSystemStatus((prev) =>
                    prev ?? {
                        coreTemp: "WAITING FOR MINA...",
                        memoryBus: "WAITING FOR MINA...",
                        neuralCache: "WAITING FOR MINA...",
                        ioChannels: "WAITING FOR MINA...",
                        level: "WARN",
                    }
                );
                setCorePullout((prev) => ({
                    ...prev,
                    online: false,
                }));
                setApiServiceUp(false);
            }
        }

        const stopPolling = startSerialPoll(pollStatus, 2000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    function mapTaskQueue(data) {
        function normalizeTaskText(value) {
            const raw = String(value ?? "");
            const cleaned = raw
                .replace(/[\u0000-\u001F\u007F-\u009F\u200B-\u200D\u2060\uFEFF]/g, "")
                .trim();
            return cleaned || "(untitled task)";
        }

        function fmtDue(ts) {
            if (!ts) return "no due time";
            try {
                return new Date(Number(ts) * 1000).toLocaleString([], {
                    weekday: "short",
                    month: "short",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                });
            } catch {
                return "invalid time";
            }
        }

        const preview = Array.isArray(data?.tasks)
            ? data.tasks
            : Array.isArray(data?.queue?.preview)
                ? data.queue.preview
                : [];

        const tasks = preview.map((t) => ({
            id: t?.id,
            text: normalizeTaskText(t?.text),
            due: Boolean(t?.due),
            dueAtText: fmtDue(t?.due_at),
            status: String(t?.status || (t?.due ? "due" : "pending")),
            alarm: Boolean(t?.alarm),
            repeatMinutes: Number(t?.repeat_minutes || 0),
            tags: Array.isArray(t?.tags) ? t.tags : [],
        }));

        return {
            tasks,
            dueCount: Number(data?.due_count ?? data?.queue?.due_count ?? 0),
            upcomingCount: Number(data?.upcoming_count ?? Math.max(0, tasks.length - Number(data?.due_count ?? 0))),
            recurringCount: tasks.filter((task) => task.repeatMinutes > 0).length,
            online: Boolean(data?.ok || data?.queue?.available),
            error: "",
            lastSync: new Date().toLocaleTimeString(),
        };
    }

    async function refreshTaskWindow() {
        try {
            const data = await requestJson("/tasks/list?limit=100");
            setTaskWindow(mapTaskQueue(data));
            return true;
        } catch (err) {
            setTaskWindow((prev) => ({
                ...prev,
                online: false,
                error: String(err?.message || "task feed unavailable"),
            }));
            return false;
        }
    }

    async function submitTaskCreate(event) {
        event.preventDefault();
        if (taskCreateBusy) return;

        const dueAtValue = taskDraft.dueAt ? new Date(taskDraft.dueAt).getTime() / 1000 : Number.NaN;
        const windowEndValue = taskDraft.windowEnd ? new Date(taskDraft.windowEnd).getTime() / 1000 : Number.NaN;
        const repeatMinutes = Math.max(1, Number(taskDraft.repeatMinutes || 0));

        if (!taskDraft.text.trim() || !Number.isFinite(dueAtValue)) {
            setActivity("Reminder needs text and a valid future start time.");
            return;
        }
        if (taskDraft.windowEnd && (!Number.isFinite(windowEndValue) || windowEndValue < dueAtValue)) {
            setActivity("Reminder end time must be after the start time.");
            return;
        }

        setTaskCreateBusy(true);
        try {
            const result = await requestJson("/tasks/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: taskDraft.text.trim(),
                    due_at: dueAtValue,
                    window_end: taskDraft.windowEnd ? windowEndValue : null,
                    repeat_minutes: repeatMinutes,
                    alarm: Boolean(taskDraft.alarm),
                }),
            });
            if (!result?.ok) throw new Error(result?.error || "task_create_failed");
            setTaskDraft({ text: "", dueAt: "", windowEnd: "", repeatMinutes: "25", alarm: true });
            setActivity(`Reminder created: ${result.task?.text || "task"} (${result.count || 1} slots).`);
            await refreshTaskWindow();
        } catch (err) {
            setActivity(`Reminder creation failed: ${err.message}`);
        } finally {
            setTaskCreateBusy(false);
        }
    }

    async function refreshApprovedAutonomyTasks() {
        try {
            const data = await requestJson("/autonomy/tasks");
            setApprovedAutonomyTasks(Array.isArray(data?.tasks) ? data.tasks : []);
        } catch {
            setApprovedAutonomyTasks([]);
        }
    }

    function promoteDeferredTask(task) {
        setAutonomyDraft({
            title: String(task?.title || ""),
            proof_target: "",
            allowed_action: "read_only",
            done_when: "",
            source_task_key: String(task?.key || ""),
        });
        setActivity(`Preparing approval for: ${String(task?.title || "task")}`);
    }

    async function submitAutonomyTask(event) {
        event.preventDefault();
        if (autonomyTaskBusy) return;
        setAutonomyTaskBusy(true);
        try {
            const result = await requestJson("/autonomy/tasks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(autonomyDraft),
            });
            if (!result?.ok) {
                throw new Error(result?.error || "task_approval_failed");
            }
            setAutonomyDraft({ title: "", proof_target: "", allowed_action: "read_only", done_when: "", source_task_key: "" });
            setActivity(`Approved autonomous task: ${result.task?.title || "task"}`);
            await refreshApprovedAutonomyTasks();
        } catch (err) {
            setActivity(`Task approval failed: ${err.message}`);
        } finally {
            setAutonomyTaskBusy(false);
        }
    }

    async function handleTaskAction(taskId, action, minutes = 0) {
        const id = Number(taskId || 0);
        if (!id) return;

        const busyKey = `${action}:${id}`;
        if (taskActionBusy) return;
        setTaskActionBusy(busyKey);

        try {
            if (action === "done") {
                await requestJson(`/tasks/mark_done?task_id=${id}`, { method: "POST" });
                setActivity(`Task ${id} marked done.`);
            } else if (action === "snooze") {
                const safeMinutes = Math.max(1, Number(minutes || 10));
                await requestJson(`/tasks/snooze?task_id=${id}&minutes=${safeMinutes}`, { method: "POST" });
                setActivity(`Task ${id} snoozed ${safeMinutes} minutes.`);
            }

            await refreshTaskWindow();
        } catch (err) {
            setActivity(`Task action failed: ${err.message}`);
        } finally {
            setTaskActionBusy("");
        }
    }

    useEffect(() => {
        let cancelled = false;

        async function pollCuriosityStatus() {
            try {
                const data = await requestJson("/curiosity/status");
                if (cancelled) return;
                setCuriosityPullout(mapCuriosityPullout(data));
                setCuriosityStatus(data);
            } catch {
                if (cancelled) return;
                setCuriosityPullout((prev) => ({
                    ...prev,
                    online: false,
                    running: false,
                }));
            }
        }

        const stopPolling = startSerialPoll(pollCuriosityStatus, 5000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    useEffect(() => {
        const stopPolling = startSerialPoll(refreshApprovedAutonomyTasks, 5000);
        return stopPolling;
    }, []);

    useEffect(() => {
        let cancelled = false;

        function handleDueTaskAnnouncements(mapped) {
            const dueTasks = (mapped?.tasks || []).filter(
                (t) => Boolean(t?.due) && String(t?.status || "pending").toLowerCase() !== "done"
            );
            const dueIds = dueTasks.map((t) => Number(t?.id || 0)).filter((id) => id > 0);

            if (!dueTaskPrimedRef.current) {
                seenDueTaskIdsRef.current = new Set(dueIds);
                dueTaskPrimedRef.current = true;
                return;
            }

            const newlyDue = dueTasks.filter((t) => {
                const id = Number(t?.id || 0);
                return id > 0 && !seenDueTaskIdsRef.current.has(id);
            });

            seenDueTaskIdsRef.current = new Set(dueIds);

            if (newlyDue.length === 0) return;

            const first = newlyDue[0];
            const firstText = String(first?.text || "(untitled task)");
            const suffix = newlyDue.length > 1 ? ` (+${newlyDue.length - 1} more)` : "";
            appendMessage("MINA", `Reminder due: ${firstText}${suffix}`);
            triggerAvatarSpeechPulse(1500);
        }

        async function pollTasks() {
            try {
                const data = await requestJson("/tasks/queue_status?limit=8");
                if (cancelled) return;
                const mapped = mapTaskQueue(data);
                setTaskWindow(mapped);
                handleDueTaskAnnouncements(mapped);
                setSelectedTaskId((prev) => {
                    const ids = mapped.tasks.map((t) => Number(t.id || 0));
                    if (prev && ids.includes(Number(prev))) {
                        return Number(prev);
                    }
                    return ids.length ? ids[0] : null;
                });
            } catch (err) {
                if (cancelled) return;
                setTaskWindow((prev) => ({
                    ...prev,
                    online: false,
                    error: String(err?.message || "task feed unavailable"),
                }));
            }
        }

        const stopPolling = startSerialPoll(pollTasks, 5000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    useEffect(() => {
        let cancelled = false;

        async function pollVoiceLoopStatus() {
            const local = await invokeTauri("service_voice_loop_status");
            if (cancelled) return;

            if (local && typeof local.running === "boolean") {
                setVoiceServiceUp(Boolean(local.running));
                return;
            }

            try {
                const remote = await requestJson("/voice/status");
                if (cancelled) return;
                const running =
                    remote?.running ??
                    remote?.voice_monitor?.running ??
                    remote?.voice_monitor?.voice_running ??
                    false;
                setVoiceServiceUp(Boolean(running));
            } catch {
                // Best-effort only; leave current indicator unchanged.
            }
        }

        const stopPolling = startSerialPoll(pollVoiceLoopStatus, 2200);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    // Live VAD metrics polling for the Audio tab (only while the tab is open).
    useEffect(() => {
        if (activeTab !== "audio") return undefined;
        let cancelled = false;
        loadVoiceDevices();

        async function pollVoiceMetrics() {
            try {
                const data = await requestJson("/voice/metrics?history=60", { timeoutMs: 4000 });
                if (cancelled) return;
                if (data?.ok) {
                    setVoiceMetrics(data.latest || null);
                    setVoiceMetricsHistory(Array.isArray(data.history) ? data.history : []);
                    if (data?.monitor && typeof data.monitor.running === "boolean") {
                        setVoiceServiceUp(data.monitor.running);
                    }
                }
            } catch {
                // Best-effort; leave meters unchanged on a missed poll.
            }
        }

        const stopPolling = startSerialPoll(pollVoiceMetrics, 400);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, [activeTab]);

    useEffect(() => {
        let cancelled = false;

        async function pollVoiceEvents() {
            try {
                const requestedSinceId = lastVoiceEventIdRef.current;
                const events = await requestJson(
                    `/events/recent?since_id=${requestedSinceId}&source=voice&limit=25`
                );

                if (cancelled || !Array.isArray(events) || events.length === 0) {
                    return;
                }

                const returnedIds = events
                    .map((event) => Number(event?.id || 0))
                    .filter((eventId) => eventId > 0);
                const maxReturnedId = returnedIds.length ? Math.max(...returnedIds) : 0;
                const eventStreamReset = requestedSinceId > 0 && maxReturnedId > 0 && maxReturnedId < requestedSinceId;

                if (eventStreamReset) {
                    lastVoiceEventIdRef.current = 0;
                }

                for (const ev of events) {
                    const evId = Number(ev?.id || 0);
                    if (evId > lastVoiceEventIdRef.current) {
                        lastVoiceEventIdRef.current = evId;
                    }

                    const said = String(ev?.input_text || "").trim();
                    const reply = String(ev?.reply || "").trim();
                    const toolOutput = String(ev?.tool_output || "").trim();

                    if (said) {
                        appendMessage("VOICE", said);
                    }
                    if (reply) {
                        appendMessage("MINA", reply);
                        triggerAvatarSpeechPulse();
                    }
                    if (toolOutput) {
                        appendMessage("TOOL", toolOutput);
                    }
                    const actionReport = formatActionReports(ev?.actions);
                    if (actionReport) appendMessage("ACTION REPORT", actionReport);
                }

                window.sessionStorage.setItem(
                    "mina:lastVoiceEventId",
                    String(lastVoiceEventIdRef.current)
                );
            } catch {
                // Keep polling quietly; voice sync is best-effort.
            }
        }

        const stopPolling = startSerialPoll(pollVoiceEvents, 1500);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    useEffect(() => {
        let cancelled = false;

        async function pollCuriosityEvents() {
            try {
                const events = await requestJson(
                    `/curiosity/events?since_id=${lastCuriosityEventIdRef.current}&limit=10`
                );
                if (cancelled || !Array.isArray(events)) return;

                for (const event of events) {
                    const eventId = Number(event?.id || 0);
                    if (eventId > lastCuriosityEventIdRef.current) {
                        lastCuriosityEventIdRef.current = eventId;
                    }
                    const message = String(event?.message || "").trim();
                    setAutonomyEvents((prev) => [
                        ...prev,
                        {
                            id: eventId,
                            source: String(event?.source || "autonomy"),
                            message: message.slice(0, 420),
                            reference: String(event?.reference || ""),
                            outcome: String(event?.outcome || (event?.ok ? "verified_complete" : "")),
                        },
                    ].slice(-20));
                    if (message) {
                        appendMessage("MINA", message);
                        triggerAvatarSpeechPulse(1800);
                    }
                }
            } catch {
                // Curiosity is optional and may be unavailable during API restarts.
            }
        }

        const stopPolling = startSerialPoll(pollCuriosityEvents, 5000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    useEffect(() => {
        let cancelled = false;

        async function pollModelStatus() {
            try {
                const hasStagedImages = (imageAttachments?.length || 0) > 0;
                const qs = hasStagedImages ? "?check_vision=true" : "?check_vision=false";
                const data = await requestJson(`/model/status${qs}`);
                if (cancelled) return;

                setModelStatus({
                    model: data?.model ?? "unknown",
                    supportsVision: Boolean(data?.supports_vision),
                    mode: data?.mode ?? "TEXT_ONLY",
                    ready: true,
                    allowedModels: Array.isArray(data?.allowed_switch_models)
                        ? data.allowed_switch_models
                        : [],
                });
                setAltModelActive(String(data?.model || "").toLowerCase() === "dolphin3-nsfw-gf-chat");
            } catch {
                if (cancelled) return;

                setModelStatus((prev) => ({
                    ...prev,
                    ready: false,
                }));
            }
        }

        const stopPolling = startSerialPoll(pollModelStatus, 5000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, [imageAttachments.length]);

    useEffect(() => {
        let cancelled = false;

        async function pollDbStatus() {
            try {
                const data = await requestJson("/db/status");

                if (cancelled) return;

                setDbStatus({
                    link: data.db_link ?? "UNKNOWN",
                    sync: data.db_sync ?? "UNKNOWN",
                    latency: data.db_latency ?? "UNKNOWN",
                    activeConnections: data.active_connections ?? "UNKNOWN",
                    readOps: data.read_ops ?? "UNKNOWN",
                    writeOps: data.write_ops ?? "UNKNOWN",
                    cacheState: data.cache_state ?? "UNKNOWN",
                    lastCommit: data.last_commit ?? "UNKNOWN",
                    level: data.level ?? "NORMAL",
                });
            } catch {
                if (cancelled) return;

                setDbStatus((prev) =>
                    prev ?? {
                        link: "WAITING FOR DB...",
                        sync: "WAITING FOR DB...",
                        latency: "WAITING FOR DB...",
                        activeConnections: "WAITING FOR DB...",
                        readOps: "WAITING FOR DB...",
                        writeOps: "WAITING FOR DB...",
                        cacheState: "WAITING FOR DB...",
                        lastCommit: "WAITING FOR DB...",
                        level: "WARN",
                    }
                );
            }
        }

        const stopPolling = startSerialPoll(pollDbStatus, 2000);

        return () => {
            cancelled = true;
            stopPolling();
        };
    }, []);

    async function sendToCore(userText, options = {}) {
        try {
            const interfaceName = String(options.interfaceName || "Mina Mk1 GUI").trim() || "Mina Mk1 GUI";
            const payload = {
                input: userText,
                speak_response: Boolean(voiceOutputOn),
                voice_hint: GUI_VOICE_HINT,
                input_source: "gui",
                interface_name: interfaceName,
            };

            const includeImage = Boolean(options.includeImage);

            const activeAttachment = imageAttachments[activeImageIndex] || imageAttachments[0] || null;
            if (includeImage && activeAttachment?.dataUrl) {
                payload.image_attachment = {
                    name: activeAttachment.name,
                    type: activeAttachment.type,
                    size: activeAttachment.size,
                    data_url: activeAttachment.dataUrl,
                };
            }

            // Why: the GUI used blocking /process and only rendered after LM
            // Studio finished. Stream from /process-stream so Mina's text renders
            // token-by-token as it is generated.
            const onChunk = typeof options.onChunk === "function" ? options.onChunk : null;

            if (includeImage) {
                const result = await requestJson("/process", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    timeoutMs: 180000,
                });
                if (result?.error) throw new Error(result.error);
                if (String(result?.reply || "").startsWith("(LM Studio error:")) {
                    throw new Error(result.reply);
                }
                const actionReport = formatActionReports(result?.actions);
                if (actionReport) appendMessage("ACTION REPORT", actionReport);
                return { text: result.reply || "(no reply)", image: result.image || null, raw: result };
            }

            const controller = new AbortController();
            const timeoutMs = 180000;
            let timedOut = false;
            const timeoutId = setTimeout(() => {
                timedOut = true;
                controller.abort();
            }, timeoutMs);

            try {
                const res = await fetch(`${API_BASE}/process-stream`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    signal: controller.signal,
                });

                if (!res.ok || !res.body) {
                    const errText = await res.text().catch(() => "");
                    throw new Error(errText || `HTTP ${res.status}`);
                }

                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                let replyText = "";
                let streamError = null;
                let speechError = null;

                for (; ;) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    // SSE frames are separated by a blank line.
                    const frames = buffer.split("\n\n");
                    buffer = frames.pop() ?? "";

                    for (const frame of frames) {
                        const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
                        if (!dataLine) continue;
                        const dataStr = dataLine.slice(5).trim();
                        if (!dataStr) continue;
                        let evt = null;
                        try {
                            evt = JSON.parse(dataStr);
                        } catch {
                            continue;
                        }
                        if (evt?.type === "chunk" && typeof evt.content === "string") {
                            replyText += evt.content;
                            if (onChunk) onChunk(replyText);
                        } else if (evt?.type === "error") {
                            streamError = evt.error || "stream error";
                        } else if (evt?.type === "actions") {
                            const actionReport = formatActionReports(evt.actions);
                            if (actionReport) appendMessage("ACTION REPORT", actionReport);
                        } else if (evt?.type === "speech") {
                            if (evt.tts?.ok === false) {
                                speechError = evt.tts.error || "speech synthesis failed";
                            } else if (evt.tts_playback?.ok === false) {
                                speechError = evt.tts_playback.error || "audio playback failed";
                            }
                        }
                    }
                }

                if (streamError) {
                    throw new Error(streamError);
                }

                if (speechError) {
                    replyText += `\n\n(Voice output failed: ${speechError})`;
                    if (onChunk) onChunk(replyText);
                }
                replyText = replyText.trim() || "(no reply)";

                return {
                    text: replyText,
                    image: null,
                    raw: null,
                };
            } finally {
                clearTimeout(timeoutId);
            }
        } catch (err) {
            console.error(err);
            return {
                text: `(error contacting Mina API: ${err.message})`,
                error: err.message,
                image: null,
                raw: null,
            };
        }
    }

    function streamCoreReply(text, opts = {}) {
        const streamId = opts.id ?? `core-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const replace = Boolean(opts.replace);

        setAvatarMood("talk");
        setIsCoreSpeaking(true);

        if (replace) {
            setMessages((prev) => prev.map((m) => (m.id === streamId ? { ...m, text: String(text ?? "") } : m)));
        } else {
            setMessages((prev) => [...prev, { id: streamId, from: "MINA", text: String(text ?? "") }]);
        }

        if (opts.done !== false) {
            setTimeout(() => {
                setIsCoreSpeaking(false);
                setAvatarMood("smirk");
                setTimeout(() => setAvatarMood("idle"), 900);
            }, 80);
        }

        return streamId;
    }

    function handleCommand(cmd) {
        const parts = cmd.split(" ");
        const base = parts[0].toLowerCase();
        const arg = parts.slice(1).join(" ");

        switch (base) {
            case "/help":
                return {
                    from: "CMD",
                    text:
                        "Available commands:\n" +
                        "/help - show commands\n" +
                        "/diag - refresh telemetry snapshot\n" +
                        "/clear - clear console\n" +
                        "/system <msg> - local status note\n" +
                        "/core <msg> - send prompt to Mina\n" +
                        "/echo <msg> - echo text\n" +
                        "/time - show local time\n" +
                        "/about - system info\n" +
                        "Memory controls live in the right panel.",
                };

            case "/diag":
                return {
                    from: "CMD",
                    text: "Telemetry is read from the live status panels.",
                };

            case "/clear":
                setMessages([]);
                return null;

            case "/system":
                return {
                    from: "SYSTEM",
                    text: arg || "(empty)",
                };

            case "/core":
                return null;

            case "/echo":
                return {
                    from: "CMD",
                    text: arg || "(empty)",
                };

            case "/time":
                return {
                    from: "CMD",
                    text: new Date().toLocaleString(),
                };

            case "/about":
                return {
                    from: "CMD",
                    text: "MK1 Bunker Terminal v7 - Gremlin Haven Systems",
                };

            default:
                return {
                    from: "CMD",
                    text: "Unknown command. Type /help",
                };
        }
    }

    function triggerCommand(cmd) {
        setMessages((prev) => [...prev, { from: USER_DISPLAY_NAME, text: cmd }]);

        const result = handleCommand(cmd);

        if (result) {
            setMessages((prev) => [...prev, result]);
        }
    }

    async function askCoreSummary() {
        if (isAskingSummary) return;
        setIsAskingSummary(true);
        try {
            const coreReply = await sendToCore(
                "Give me a concise current status summary for the core, database, and memory state."
            );
            streamCoreReply(coreReply.text);
            setActivity("Requested a live core summary.");
        } finally {
            setIsAskingSummary(false);
        }
    }

    async function openRestoreGui() {
        if (isOpeningRestore) return;
        setIsOpeningRestore(true);
        try {
            const result = await requestJson("/restore/open", {
                method: "POST",
            });

            if (result?.ok) {
                appendMessage("SYSTEM", "Restore utility launched.");
                setActivity("Restore utility opened in a new window.");
            } else {
                const err = result?.error || "unknown_error";
                appendMessage("SYSTEM", `Restore utility failed to launch: ${err}`);
                setActivity(`Restore launch failed: ${err}`);
            }
        } catch (err) {
            appendMessage("SYSTEM", `Restore utility failed to launch: ${err.message}`);
            setActivity(`Restore launch failed: ${err.message}`);
        } finally {
            setIsOpeningRestore(false);
        }
    }

    async function toggleApiService() {
        if (isStartingApi) return;
        setIsStartingApi(true);

        if (apiServiceUp) {
            const localStop = await invokeTauri("service_stop_api_local");
            if (localStop?.ok) {
                setApiServiceUp(false);
                appendMessage("SYSTEM", "API safe shutdown completed via local launcher (verified).");
                setActivity("API safe shutdown verified.");
                setTimeout(() => {
                    refreshSystemStatus();
                    refreshDbStatus();
                }, 900);
            } else {
                appendMessage("SYSTEM", `API stop failed: ${localStop?.error || "unknown_error"}`);
                setActivity("API stop failed.");
            }
            setIsStartingApi(false);
            return;
        }

        const local = await invokeTauri("service_start_api_local");
        if (local?.ok) {
            appendMessage("SYSTEM", "API start requested via local launcher.");
            setActivity("API start requested.");

            // Allow startup script to bring the API up before next status poll.
            setTimeout(() => {
                refreshSystemStatus();
                refreshDbStatus();
            }, 1400);
            setIsStartingApi(false);
            return;
        }

        try {
            const result = await requestJson("/service/start_api", {
                method: "POST",
            });

            if (result?.ok) {
                setApiServiceUp(true);
                appendMessage("SYSTEM", "API start signal sent.");
                setActivity("API start requested.");
            } else {
                const err = result?.error || "unknown_error";
                appendMessage("SYSTEM", `API start failed: ${err}`);
                setActivity(`API start failed: ${err}`);
            }
        } catch (err) {
            setApiServiceUp(false);
            appendMessage("SYSTEM", `API start failed: ${err.message}`);
            setActivity(`API start failed: ${err.message}`);
        } finally {
            setIsStartingApi(false);
        }
    }

    async function toggleVoiceLoopService() {
        if (isStartingVoice) return;
        setIsStartingVoice(true);

        if (voiceServiceUp) {
            const localStop = await invokeTauri("service_stop_voice_loop_local");
            if (localStop?.ok) {
                setVoiceServiceUp(false);
                appendMessage("SYSTEM", "Voice loop stop requested via local launcher.");
                setActivity("Voice loop stop requested.");
            } else {
                appendMessage("SYSTEM", `Voice loop stop failed: ${localStop?.error || "unknown_error"}`);
                setActivity("Voice loop stop failed.");
            }
            setIsStartingVoice(false);
            return;
        }

        const local = await invokeTauri("service_start_voice_loop_local");
        if (local?.ok) {
            appendMessage("SYSTEM", `Voice loop launch requested via local launcher. PID: ${local.pid ?? "unknown"}`);
            setActivity("Voice loop start requested.");

            setVoiceServiceUp(true);
            setTimeout(async () => {
                const verified = await invokeTauri("service_voice_loop_status");
                if (verified && typeof verified.running === "boolean") {
                    setVoiceServiceUp(Boolean(verified.running));
                    appendMessage("SYSTEM", verified.detail || (verified.running ? "Voice loop running." : "Voice loop stopped."));
                }
            }, 900);
            setIsStartingVoice(false);
            return;
        }

        setVoiceServiceUp(false);
        appendMessage("SYSTEM", `Voice loop start failed: ${local?.detail || local?.error || "local launcher unavailable"}`);
        setActivity("Voice loop start failed.");
        setIsStartingVoice(false);
    }

    async function syncVoiceOutputFromApi() {
        try {
            const data = await requestJson("/voice/speak", { timeoutMs: 5000 });
            if (data && typeof data.speak !== "undefined") {
                setVoiceOutputOn(Boolean(data.speak));
            }
        } catch {
            // best-effort; leave the toggle as-is if the API is unreachable
        }
    }

    async function toggleVoiceOutput() {
        const next = !voiceOutputOn;
        setVoiceOutputOn(next);
        try {
            await requestJson("/voice/speak", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ speak: next }),
                timeoutMs: 5000,
            });
        } catch {
            // the toggle state still applies locally; the API write is best-effort
        }
        if (next) {
            appendMessage("SYSTEM", "Voice output enabled for desktop replies.");
            setActivity("Voice output ON.");
        } else {
            appendMessage("SYSTEM", "Voice output disabled for desktop replies.");
            setActivity("Voice output OFF.");
        }
    }

    async function loadVoiceDevices() {
        try {
            const data = await requestJson("/voice/devices", { timeoutMs: 6000 });
            if (data?.ok) {
                setVoiceDevices(Array.isArray(data.devices) ? data.devices : []);
                setVoiceCurrentDevice(data.current_device ?? null);
            }
        } catch {
            // best-effort
        }
    }

    async function probeVoiceDevices() {
        if (isProbingDevices) return;
        setIsProbingDevices(true);
        setActivity("Probing input devices (1s each)...");
        try {
            const data = await requestJson("/voice/probe?device=-1&seconds=1.0", { timeoutMs: 30000 });
            if (data?.ok && Array.isArray(data.levels)) {
                const map = {};
                for (const l of data.levels) {
                    map[l.index] = l.rms;
                }
                setVoiceDeviceLevels(map);
                setActivity("Device probe complete. Speak into your headset to see which one lights up.");
            }
        } catch (err) {
            setActivity(`Device probe failed: ${err.message}`);
        } finally {
            setIsProbingDevices(false);
        }
    }

    async function selectVoiceDevice(idx) {
        try {
            // Single write path: the Tauri voice_set_input_device writes the
            // shared config/.voice_device.json that the monitor reads.
            const data = await invokeTauri("voice_set_input_device", { inputDevice: idx });
            if (data && typeof data.input_device !== "undefined") {
                setVoiceCurrentDevice(Number(data.input_device));
                appendMessage("SYSTEM", `Voice input device set to ${data.input_device}. Restart voice-in to apply.`);
                setActivity(`Voice device set to ${data.input_device}.`);
            } else {
                appendMessage("SYSTEM", "Voice device change failed: no confirmation from device config.");
            }
        } catch (err) {
            appendMessage("SYSTEM", `Voice device change failed: ${err.message}`);
        }
    }

    function compactModelName(name) {
        const raw = String(name || "").trim();
        if (!raw) return "MODEL";
        if (raw.length <= 20) return raw;
        const slash = raw.includes("/") ? raw.split("/").pop() : raw;
        if (slash && slash.length <= 20) return slash;
        return `${raw.slice(0, 17)}...`;
    }

    async function switchModel(modelName) {
        const target = String(modelName || "").trim();
        if (!target || isSwitchingModel) return false;
        if (target.toLowerCase() === String(modelStatus.model || "").toLowerCase()) {
            setActivity(`Model already active: ${target}`);
            return true;
        }

        setIsSwitchingModel(true);
        try {
            const data = await requestJson("/model/select", {
                method: "POST",
                timeoutMs: 120000,
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    model: target,
                    autoload: true,
                    persist: true,
                    force_reload: false,
                }),
            });

            if (!data?.ok) {
                const err = data?.error || "model_switch_failed";
                appendMessage("SYSTEM", `Model switch failed: ${err}`);
                setActivity(`Model switch failed: ${err}`);
                return false;
            }

            setModelStatus((prev) => ({
                ...prev,
                model: String(data.active_model || target),
                ready: true,
            }));

            appendMessage("SYSTEM", `Model active: ${data.active_model || target}`);
            setActivity(`Model switched to ${data.active_model || target}`);
            return true;
        } catch (err) {
            appendMessage("SYSTEM", `Model switch failed: ${err.message}`);
            setActivity(`Model switch failed: ${err.message}`);
            return false;
        } finally {
            setIsSwitchingModel(false);
        }
    }

    async function switchAltModel() {
        if (isSwitchingAltModel) return;

        setIsSwitchingAltModel(true);
        try {
            if (altModelActive) {
                // Switching back to Mina
                // Switch back to the first Mina model (qwen2.5-7b-instruct)
                const switched = await switchModel("qwen2.5-7b-instruct");
                if (!switched) return;
                setAltModelActive(false);
                appendMessage("SYSTEM", "Alt model disabled. Switched back to Mina core.");
                setActivity("Switched to Mina core.");
            } else {
                // Switching to dolphin3
                // First switch the actual model in LM Studio
                const switched = await switchModel("dolphin3-nsfw-gf-chat");
                if (!switched) return;

                const createResp = await requestJson("/alt_model/memory_session/create", {
                    method: "POST",
                    timeoutMs: 30000,
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ model_name: "dolphin3" }),
                });

                if (!createResp?.ok) {
                    const err = createResp?.error || "alt_model_session_failed";
                    appendMessage("SYSTEM", `Alt model session failed: ${err}`);
                    setActivity(`Alt model session failed: ${err}`);
                    return;
                }

                setAltModelActive(true);
                appendMessage("SYSTEM", "Alt model (dolphin3) activated. Isolated memory session created.");
                setActivity("Switched to dolphin3 (isolated sandbox).");
            }
        } catch (err) {
            appendMessage("SYSTEM", `Alt model switch failed: ${err.message}`);
            setActivity(`Alt model switch failed: ${err.message}`);
        } finally {
            setIsSwitchingAltModel(false);
        }
    }

    function handleAutocomplete() {
        const trimmed = input.trim();

        if (!trimmed.startsWith("/")) return;

        const parts = trimmed.split(" ");
        const base = parts[0];

        const match = COMMANDS.find((c) => c.startsWith(base) && c !== base);

        if (match) {
            parts[0] = match;

            const rest = parts.slice(1).join(" ");

            const newVal = rest ? `${match} ${rest}` : `${match} `;

            setInput(newVal);
        }
    }

    async function handleSubmit() {
        const payload = input.trim();

        if (!payload) return;

        setMessages((prev) => [
            ...prev,
            {
                from: USER_DISPLAY_NAME,
                text: payload,
            },
        ]);

        setInput("");

        if (payload.startsWith("/core")) {
            const corePrompt = payload.slice(5).trim();
            const coreReply = await sendToCore(
                corePrompt || "Respond briefly with the current core summary."
            );
            streamCoreReply(coreReply.text);
            setActivity("Sent a direct prompt to the core.");
            return;
        }

        if (payload.startsWith("/")) {
            const result = handleCommand(payload);

            if (result) {
                setMessages((prev) => [...prev, result]);
            }

            return;
        }

        const coreReply = await sendToCore(payload, {
            onChunk: (partial) => {
                // Stream Mina's reply into a single live message as tokens arrive.
                if (!coreStreamMsgIdRef.current) {
                    coreStreamMsgIdRef.current = `core-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
                    setAvatarMood("talk");
                    setIsCoreSpeaking(true);
                    setMessages((prev) => [...prev, { id: coreStreamMsgIdRef.current, from: "MINA", text: partial }]);
                } else {
                    const id = coreStreamMsgIdRef.current;
                    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, text: partial } : m)));
                }
            },
        });

        // Finalize the streamed message state.
        if (coreStreamMsgIdRef.current) {
            coreStreamMsgIdRef.current = null;
            setTimeout(() => {
                setIsCoreSpeaking(false);
                setAvatarMood("smirk");
                setTimeout(() => setAvatarMood("idle"), 900);
            }, 80);
        } else {
            // No chunks arrived (error path); fall back to the returned text.
            streamCoreReply(coreReply.text);
        }

        if (imageAttachments.length) {
            const activeAttachment = imageAttachments[activeImageIndex] || imageAttachments[0];
            if (activeAttachment) {
                setImageStatus(`Active image kept ready: ${activeAttachment.name}`);
            }
        }
    }

    async function handleSendImage() {
        const activeAttachment = imageAttachments[activeImageIndex] || imageAttachments[0] || null;
        if (!activeAttachment?.dataUrl) {
            setImageStatus("Stage an image first, then use SEND>IMG.");
            appendMessage("SYSTEM", "No staged image available for SEND>IMG.");
            return;
        }

        const textPrompt = input.trim();
        const composedPrompt = textPrompt || "Analyze the staged image and summarize key details.";

        setMessages((prev) => [
            ...prev,
            {
                from: USER_DISPLAY_NAME,
                text: `[IMG] ${composedPrompt}`,
            },
        ]);

        if (textPrompt) {
            setInput("");
        }

        setImageStatus(`Sending image: ${activeAttachment.name}`);
        appendMessage("SYSTEM", `Dispatching staged image: ${activeAttachment.name}`);

        const coreReply = await sendToCore(composedPrompt, { includeImage: true });
        streamCoreReply(coreReply.text);
        if (coreReply.error) {
            setImageStatus(`Image send failed: ${coreReply.error}`);
            return;
        }

        const imageMeta = coreReply.image;
        if (imageMeta?.received) {
            setModelStatus((prev) => ({
                ...prev,
                supportsVision: Boolean(imageMeta.vision_model),
                mode: imageMeta.vision_model ? "VISION_READY" : "TEXT_ONLY",
                ready: true,
            }));

            if (imageMeta.forwarded_to_model) {
                appendMessage("SYSTEM", `Image sent to vision model: ${imageMeta.name}`);
                setImageStatus(`Image sent: ${imageMeta.name}`);
            } else {
                appendMessage("SYSTEM", "Image received by backend. Active model appears text-only; upgrade to a vision model for pixel-level analysis.");
                setImageStatus("Image reached backend; active model is text-only.");
            }
        } else {
            appendMessage("SYSTEM", "Image dispatch completed, but backend did not confirm attachment metadata.");
        }
    }

    function handleKeyDown(e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        } else if (e.key === "Tab") {
            e.preventDefault();
            handleAutocomplete();
        }
    }

    function insertEmoji(emoji) {
        setInput((prev) => prev + emoji);
        setShowEmoji(false);
    }

    const emojiList = [
        "🙂",
        "😀",
        "😄",
        "😁",
        "😅",
        "😂",
        "😉",
        "😊",
        "😍",
        "😘",
        "😎",
        "🤔",
        "🙃",
        "😐",
        "😮",
        "😬",
        "😤",
        "😈",
        "😭",
        "🤯",
        "🥳",
        "🤝",
        "👍",
        "👎",
        "👏",
        "🙏",
        "❤️",
        "💚",
        "💙",
        "💛",
        "✨",
        "⭐",
        "🌙",
        "☀️",
        "☕",
        "🤖",
        "⚠️",
        "🔥",
        "💀",
        "🚨",
        "📡",
        "🔧",
        "🛠️",
        "💾",
        "📁",
        "📦",
        "🔒",
        "🔓",
        "🧪",
        "⚙️",
        "🛰️",
        "🧱",
        "🕳️",
        "🗜️",
        "🧲",
        "🛡️",
        "⚡",
        "💡",
        "📜",
        "🧰",
        "🕹️",
        "📟",
        "🖥️",
        "🔭",
        "📡",
        "🧯",
        "🧵",
        "🪛",
        "🔩",
        "🔨",
        "🪓",
        "🧱",
        "🧬",
        "🧫",
        "🧠",
        "🫡",
        "🗨️",
        "💬",
        "📢",
        "📝",
        "✅",
        "❌",
        "❓",
        "❗",
        "🚀",
        "🛸",
        "🌌",
        "🎮",
        "🎵",
        "🎧",
        "🧭",
        "🧪",
        "🔬",
        "🧷",
        "🪄",
        "💻",
        "⌨️",
        "🖱️",
        "📈",
        "📉",
        "🔋",
        "🪫",
    ];

    function toggleEmoji() {
        if (!emojiBtnRef.current) return;

        const rect = emojiBtnRef.current.getBoundingClientRect();

        setEmojiPos({
            x: rect.left + rect.width / 2,
            y: rect.top,
        });

        setShowEmoji((prev) => !prev);
    }

    const trimmed = input.trim();
    const currentCmd = trimmed.startsWith("/") ? trimmed.split(" ")[0] : "";
    const suggestion = currentCmd
        ? COMMANDS.find((c) => c.startsWith(currentCmd) && c !== currentCmd)
        : null;

    function getColorForSender(from) {
        if (from === "SYSTEM") return "#ffb000";
        if (from === "MINA") return "#00ff88";
        if (from === "CMD") return "#00e0ff";
        if (from === "MEMORY") return "#ff8c00";
        if (from === "VOICE") return "#9ad1ff";
        if (from === "TOOL") return "#ffd27d";
        if (from === USER_DISPLAY_NAME) return "#e0e0e0";
        if (from === "YOU") return "#e0e0e0";

        return "#e0e0e0";
    }

    function getStatusColor(level) {
        switch (level) {
            case "CRITICAL":
                return "#ff0033";

            case "ERROR":
                return "#ff3300";

            case "WARN":
                return "#ffb000";

            case "NORMAL":
            default:
                return "#00ff88";
        }
    }

    const dbLinkText = dbStatus?.link ?? "WAITING FOR DB...";
    const dbSyncText = dbStatus?.sync ?? "WAITING FOR DB...";
    const dbLatencyText = dbStatus?.latency ?? "WAITING FOR DB...";
    const dbActiveConnText = dbStatus?.activeConnections ?? "WAITING FOR DB...";
    const dbReadOpsText = dbStatus?.readOps ?? "WAITING FOR DB...";
    const dbWriteOpsText = dbStatus?.writeOps ?? "WAITING FOR DB...";
    const dbCacheStateText = dbStatus?.cacheState ?? "WAITING FOR DB...";
    const dbLastCommitText = dbStatus?.lastCommit ?? "WAITING FOR DB...";
    const dbLevel = dbStatus?.level ?? "WARN";
    const dbColor = getStatusColor(dbLevel);
    const hasVisionCapability = modelStatus.ready && modelStatus.supportsVision;
    const textOnlyBadgeClass = hasVisionCapability
        ? "mk1-vision-badge mk1-vision-muted"
        : "mk1-vision-badge mk1-vision-off";
    const imageCapableBadgeClass = hasVisionCapability
        ? "mk1-vision-badge mk1-vision-on"
        : "mk1-vision-badge mk1-vision-muted";
    const apiShutdownPending = isStartingApi && apiServiceUp;
    const apiServiceClass = apiShutdownPending
        ? "mk1-service-badge mk1-service-stop-pending"
        : (apiServiceUp ? "mk1-service-badge mk1-service-up" : "mk1-service-badge mk1-service-down");
    const apiButtonClass = apiShutdownPending
        ? "mk1-mini-action mk1-mini-action-stop-pending"
        : "mk1-mini-action";
    const voiceServiceClass = voiceServiceUp ? "mk1-service-badge mk1-service-up" : "mk1-service-badge mk1-service-down";
    const avatarSrc = isCoreSpeaking
        ? (talkFrameAlt ? avatarTalk2 : avatarTalk)
        : (avatarMood === "smirk" ? avatarSmirk : avatarIdle);
    const selectedTask = taskWindow.tasks.find((t) => Number(t.id || 0) === Number(selectedTaskId || 0)) || null;
    const taskActionTarget = selectedTask || taskWindow.tasks[0] || null;
    const coreQueueSummary = corePullout.queueAvailable
        ? `${corePullout.queueDueCount} due / ${corePullout.queueCount} queued`
        : "queue offline";
    const curiosityTask = curiosityPullout.currentTask || "idle";
    const decisionSummary = curiosityStatus?.decision_summary || {};
    const deferredByReason = decisionSummary?.deferred_by_reason || {};
    const deferredPreview = Array.isArray(decisionSummary?.deferred_preview) ? decisionSummary.deferred_preview : [];
    const tabs = [
        ["chat", "CHAT"],
        ["images", "IMAGES"],
        ["status", "STATUS"],
        ["audio", "AUDIO"],
        ["autonomy", "AUTONOMY"],
        ["tasks", "TASKS"],
        ["system", "SYSTEM"],
        ["ostranauts", "OSTRANAUTS"],
    ];

    if (isOstranautsCompanion) {
        return (
            <OstranautsCompanion
                avatarSrc={avatarSrc}
                askMina={(text) => sendToCore(text, { interfaceName: "Ostranauts GUI" })}
                onClose={() => invokeTauri("close_ostranauts_companion")}
            />
        );
    }

    return (
        <>
            <div className={`mk1-root mk1-tab-${activeTab}`}>
                <nav className="mk1-tab-rail" aria-label="Mina workspace views">
                    {tabs.map(([id, label]) => (
                        <button
                            key={id}
                            className={`mk1-tab-button ${activeTab === id ? "is-active" : ""}`}
                            type="button"
                            onClick={() => setActiveTab(id)}
                            aria-pressed={activeTab === id}
                        >
                            {label}
                        </button>
                    ))}
                </nav>

                <main className="mk1-main">
                    <div className="mk1-left-stack mk1-view-visuals">
                        <section
                            className="mk1-panel mk1-crt-amber mk1-image-panel"
                            onPaste={handleImagePaste}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={handleImageDrop}
                        >
                            <div className="mk1-panel-title">IMAGE STAGING</div>

                            <div className="mk1-image-model-row">
                                <div className="mk1-image-mode-badges">
                                    <span className={textOnlyBadgeClass}>TEXT ONLY</span>
                                    <span className={imageCapableBadgeClass}>IMAGE CAPABLE</span>
                                </div>
                                <span className="mk1-image-model-name">{modelStatus.model}</span>
                            </div>

                            <div className="mk1-panel-body mk1-image-body">
                                <input
                                    ref={imageInputRef}
                                    type="file"
                                    accept="image/*"
                                    multiple
                                    className="mk1-hidden-file"
                                    onChange={(e) => handleImageFiles(e.target.files)}
                                />

                                <div className="mk1-image-actions">
                                    <button className="mk1-file-btn" type="button" onClick={openImagePicker}>
                                        CHOOSE IMAGE
                                    </button>

                                    <button
                                        className="mk1-send-img-btn"
                                        type="button"
                                        onClick={handleSendImage}
                                        disabled={!hasVisionCapability}
                                        title={hasVisionCapability ? "Send staged image to model" : "Load a vision-capable model to enable SEND>IMG"}
                                    >
                                        SEND&gt;IMG
                                    </button>
                                </div>

                                {imageAttachments.length ? (
                                    <div className="mk1-image-grid">
                                        {imageAttachments.map((att, idx) => {
                                            const isActive = idx === activeImageIndex;
                                            return (
                                                <div
                                                    key={att.objectUrl || `${att.name}-${idx}`}
                                                    className={`mk1-image-slot ${isActive ? "is-active" : ""}`}
                                                    onClick={() => {
                                                        setActiveImageIndex(idx);
                                                        setImageStatus(`Active image: ${att.name}`);
                                                    }}
                                                    role="button"
                                                    tabIndex={0}
                                                    onKeyDown={(e) => {
                                                        if (e.key === "Enter" || e.key === " ") {
                                                            e.preventDefault();
                                                            setActiveImageIndex(idx);
                                                            setImageStatus(`Active image: ${att.name}`);
                                                        }
                                                    }}
                                                    title={isActive ? "Currently selected for SEND>IMG" : "Click to set as SEND>IMG target"}
                                                >
                                                    <img className="mk1-image-slot-preview" src={att.objectUrl} alt={att.name} />
                                                    <div className="mk1-image-slot-name">{att.name}</div>
                                                    <div className="mk1-image-slot-meta">{Math.max(1, Math.round(att.size / 1024))} KB</div>
                                                    <div className="mk1-image-slot-row">
                                                        {isActive && <span className="mk1-image-slot-active">ACTIVE</span>}
                                                        <button
                                                            className="mk1-image-slot-remove"
                                                            type="button"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                clearImageAttachment(idx);
                                                            }}
                                                        >
                                                            REMOVE
                                                        </button>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <button
                                        className="mk1-image-empty-target"
                                        type="button"
                                        onClick={openImagePicker}
                                        title="Choose, paste, or drop an image"
                                    >
                                        <span className="mk1-image-empty-title">No image loaded.</span>
                                        <span className="mk1-image-empty-copy">Paste or drop anywhere in this panel, or click here to choose an image.</span>
                                    </button>
                                )}

                                <div className="mk1-panel-note">{imageStatus}</div>
                            </div>
                        </section>

                        <aside className="mk1-task-window mk1-view-tasks" aria-label="Mina task queue">
                            <div className="mk1-task-window-title">TASK QUEUE</div>
                            <div className="mk1-task-window-meta">
                                <span>DUE {taskWindow.dueCount}</span>
                                <span>UPCOMING {taskWindow.upcomingCount}</span>
                                <span>RECURRING {taskWindow.recurringCount || 0}</span>
                                <span>{taskWindow.online ? "ONLINE" : "OFFLINE"}</span>
                            </div>

                            <div className="mk1-task-window-body">
                                {taskWindow.tasks.length === 0 && (
                                    <div className="mk1-task-empty">No queued tasks in memory.</div>
                                )}

                                {taskWindow.tasks.map((task) => (
                                    <button
                                        key={String(task.id)}
                                        className={`mk1-task-item ${task.due ? "is-due" : ""} ${Number(selectedTaskId || 0) === Number(task.id || 0) ? "is-selected" : ""}`}
                                        type="button"
                                        onClick={() => setSelectedTaskId(Number(task.id || 0))}
                                    >
                                        <div className="mk1-task-text">{task.text}</div>
                                        <div className="mk1-task-sub">
                                            {task.due ? "DUE" : "UPCOMING"} at {task.dueAtText}
                                            {task.repeatMinutes > 0 ? ` | REPEATS ${task.repeatMinutes}m` : ""}
                                            {task.alarm ? " | ALARM" : ""}
                                        </div>
                                    </button>
                                ))}
                            </div>

                            <div className="mk1-task-actions-inline mk1-tasks-tab-actions">
                                <div className="mk1-task-rail-title">SELECTED TASK</div>
                                <div className="mk1-task-actions-rail mk1-task-actions-rail-inline">
                                    <button className="mk1-task-btn" type="button" disabled={!taskActionTarget || Boolean(taskActionBusy)} onClick={() => taskActionTarget && handleTaskAction(taskActionTarget.id, "done")}>DONE</button>
                                    <button className="mk1-task-btn" type="button" disabled={!taskActionTarget || Boolean(taskActionBusy)} onClick={() => taskActionTarget && handleTaskAction(taskActionTarget.id, "snooze", 10)}>+10m</button>
                                    <button className="mk1-task-btn" type="button" disabled={!taskActionTarget || Boolean(taskActionBusy)} onClick={() => taskActionTarget && handleTaskAction(taskActionTarget.id, "snooze", 30)}>+30m</button>
                                </div>
                                <div className="mk1-task-rail-meta">{taskActionTarget ? `ID ${taskActionTarget.id} | ${taskActionTarget.tags.join(", ") || "no tags"}` : "NO TASK SELECTED"}</div>
                            </div>

                            <form className="mk1-task-create" onSubmit={submitTaskCreate}>
                                <div className="mk1-task-rail-title">CREATE REMINDER</div>
                                <input value={taskDraft.text} onChange={(event) => setTaskDraft((draft) => ({ ...draft, text: event.target.value }))} placeholder="Drink water" required />
                                <input type="datetime-local" value={taskDraft.dueAt} onChange={(event) => setTaskDraft((draft) => ({ ...draft, dueAt: event.target.value }))} required />
                                <input type="number" min="1" step="1" value={taskDraft.repeatMinutes} onChange={(event) => setTaskDraft((draft) => ({ ...draft, repeatMinutes: event.target.value }))} placeholder="25" />
                                <input type="datetime-local" value={taskDraft.windowEnd} onChange={(event) => setTaskDraft((draft) => ({ ...draft, windowEnd: event.target.value }))} placeholder="End at" />
                                <label className="mk1-task-alarm-toggle"><input type="checkbox" checked={taskDraft.alarm} onChange={(event) => setTaskDraft((draft) => ({ ...draft, alarm: event.target.checked }))} /> ALARM / BEEP</label>
                                <button className="mk1-btn" type="submit" disabled={taskCreateBusy}>{taskCreateBusy ? "CREATING..." : "CREATE REMINDER"}</button>
                            </form>

                            <div className="mk1-task-window-footer">
                                {taskWindow.error ? `Task feed: ${taskWindow.error}` : `Desktop API ${taskWindow.online ? "ONLINE" : "OFFLINE"} | Phone delivery not reported | Last sync ${taskWindow.lastSync}`}
                            </div>
                        </aside>
                    </div>

                    <div className="mk1-center-stack mk1-view-chat">
                        <section className="mk1-panel mk1-crt-green mk1-console-panel">
                            <div className="mk1-console-header">
                                <div className="mk1-chat-title-row">
                                    <div>
                                        <span className="mk1-chat-label">MINA MK1 GUI</span>
                                        <span className="mk1-chat-text">
                                            {systemStatus?.level === "NORMAL" && " MINA ONLINE"}
                                            {systemStatus?.level === "WARN" && " MINA DEGRADED"}
                                            {systemStatus?.level === "ERROR" && " MINA ERROR"}
                                            {systemStatus?.level === "CRITICAL" && " MINA FAILURE"}
                                            {!systemStatus?.level && " WAITING FOR MINA..."}
                                        </span>
                                    </div>
                                </div>
                            </div>

                            <div className="mk1-chat-console-body">
                                <aside className="mk1-chat-avatar-rail" title={`Mina is ${isCoreSpeaking ? "speaking" : avatarMood}`}>
                                    <img src={avatarSrc} alt="Mina" />
                                    <div className="mk1-chat-avatar-label">MINA // {isCoreSpeaking ? "TALK" : avatarMood.toUpperCase()}</div>
                                </aside>
                                <div className="mk1-console-scroll" ref={consoleRef}>
                                    {messages.map((m, idx) => (
                                        <div className="mk1-chat-line" key={idx}>
                                            <span
                                                className="mk1-chat-label"
                                                style={{
                                                    color: getColorForSender(m.from),
                                                }}
                                            >
                                                {m.from}
                                            </span>

                                            <span className="mk1-chat-text"> {m.text}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </section>

                        <section className="mk1-panel mk1-chat-input-panel">
                            <div className="mk1-chat-tool-row">
                                <button
                                    ref={emojiBtnRef}
                                    className="mk1-emoji-toggle"
                                    type="button"
                                    onClick={toggleEmoji}
                                >
                                    EMOJI
                                </button>
                            </div>

                            <div className="mk1-input-wrap">
                                <div className="mk1-lcd-shell">
                                    <textarea
                                        className="mk1-input"
                                        value={input}
                                        onChange={(e) => setInput(e.target.value)}
                                        onKeyDown={handleKeyDown}
                                        placeholder="> ENTER COMMAND..."
                                    />

                                    {suggestion && (
                                        <div
                                            className="mk1-chat-label"
                                            style={{
                                                opacity: 0.6,
                                                marginTop: 2,
                                            }}
                                        >
                                            Suggest: {suggestion} (Tab)
                                        </div>
                                    )}
                                </div>

                                <button className="mk1-send-btn" type="button" onClick={handleSubmit}>
                                    SEND
                                </button>
                            </div>
                        </section>

                    </div>

                    <section className="mk1-right-stack">
                        <div className="mk1-panel mk1-crt-amber mk1-right-top mk1-actions-dock mk1-view-system">
                            <div className="mk1-panel-title">MINA MK1 GUI</div>

                            <div className="mk1-panel-body">
                                <button className="mk1-btn" onClick={refreshAllStatus} disabled={isRefreshing}>
                                    {isRefreshing ? "REFRESHING..." : "REFRESH STATUS"}
                                </button>

                                <button className="mk1-btn" onClick={askCoreSummary} disabled={isAskingSummary}>
                                    {isAskingSummary ? "ASKING MINA..." : "ASK MINA FOR SUMMARY"}
                                </button>

                                <button className="mk1-btn" onClick={() => invokeTauri("open_ostranauts_companion")}>
                                    OPEN OSTRANAUTS COMPANION
                                </button>

                                <div className="mk1-mini-field">
                                    <div className="mk1-mini-label">Model switch</div>
                                    <div className="mk1-model-chip-row">
                                        {(modelStatus.allowedModels || []).map((name) => {
                                            const active = String(name).toLowerCase() === String(modelStatus.model || "").toLowerCase();
                                            return (
                                                <button
                                                    key={name}
                                                    className={`mk1-model-chip ${active ? "is-active" : ""}`}
                                                    type="button"
                                                    disabled={isSwitchingModel || active}
                                                    onClick={() => switchModel(name)}
                                                    title={name}
                                                >
                                                    {compactModelName(name)}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>

                                <div className="mk1-mini-field">
                                    <div className="mk1-mini-label">AI Mode</div>
                                    <div className="mk1-model-chip-row">
                                        <button
                                            className={`mk1-ai-mode-toggle ${altModelActive ? "is-alt-active" : "is-mina-active"}`}
                                            type="button"
                                            disabled={isSwitchingAltModel}
                                            onClick={switchAltModel}
                                            title={altModelActive ? "Dolphin3 active (isolated sandbox)" : "Mina core (main agent)"}
                                        >
                                            {isSwitchingAltModel ? "SWITCHING..." : (altModelActive ? "DOLPHIN3" : "MINA CORE")}
                                        </button>
                                    </div>
                                </div>

                                <div className="mk1-service-row">
                                    <span className={apiServiceClass}>{apiShutdownPending ? "API SHUTTING DOWN" : (apiServiceUp ? "API ACTIVE" : "API DOWN")}</span>
                                    <button className={apiButtonClass} type="button" onClick={toggleApiService} disabled={isStartingApi}>
                                        {isStartingApi ? (apiServiceUp ? "SAFE STOPPING..." : "STARTING...") : (apiServiceUp ? "STOP API" : "START API")}
                                    </button>
                                </div>

                                <button className="mk1-btn" onClick={() => triggerCommand("/time")}>
                                    SHOW LOCAL TIME
                                </button>

                                <button className="mk1-btn" onClick={() => triggerCommand("/clear")}>
                                    CLEAR CONSOLE
                                </button>

                                <div className="mk1-pullout-card" aria-label="Core state pullouts">
                                    <div className="mk1-pullout-title">CORE PULLOUTS</div>
                                    <div className="mk1-pullout-grid">
                                        <div className="mk1-pullout-chip">
                                            <b>HEART</b>
                                            <span>T{corePullout.heartbeatTick} / {Math.round(corePullout.heartbeatElapsedSeconds || 0)}s</span>
                                        </div>
                                        <div className="mk1-pullout-chip">
                                            <b>IDLE</b>
                                            <span>{corePullout.secondsSinceInput == null ? "--" : `${Math.round(corePullout.secondsSinceInput)}s`}</span>
                                        </div>
                                        <div className="mk1-pullout-chip">
                                            <b>VOICE</b>
                                            <span>{corePullout.voiceRunning ? `UP ${corePullout.voicePid ?? ""}` : "DOWN"}</span>
                                        </div>
                                        <div className="mk1-pullout-chip">
                                            <b>QUEUE</b>
                                            <span>{coreQueueSummary}</span>
                                        </div>
                                        <div className="mk1-pullout-chip">
                                            <b>CURIOUS</b>
                                            <span>{curiosityPullout.enabled ? (curiosityPullout.running ? "RUN" : "IDLE") : "OFF"}</span>
                                        </div>
                                        <div className="mk1-pullout-chip">
                                            <b>EVENTS</b>
                                            <span>{curiosityPullout.eventCount}</span>
                                        </div>
                                    </div>
                                    <div className="mk1-pullout-lines">
                                        <div>ENGINE: {corePullout.memoryEngine}</div>
                                        <div>TOOLS: {corePullout.toolCount} | GREMLIN TASK: {curiosityTask}</div>
                                        <div>FLAGS: idle={corePullout.idleCheckinPending ? "Y" : "N"} away={corePullout.awayNagPending ? "Y" : "N"} pause={curiosityPullout.paused ? "Y" : "N"}</div>
                                        <div>SYNC: core {corePullout.lastSync || "--"} | curious {curiosityPullout.lastSync || "--"}</div>
                                    </div>
                                </div>

                                <div className="mk1-recovery-actions">
                                    <div className="mk1-mini-label">RECOVERY</div>
                                    <button className="mk1-restore-btn" type="button" onClick={openRestoreGui} disabled={isOpeningRestore}>
                                        {isOpeningRestore ? "OPENING..." : "OPEN RESTORE"}
                                    </button>
                                </div>

                                {statusNote && <div className="mk1-panel-note">{statusNote}</div>}
                            </div>
                        </div>

                        <div className="mk1-panel mk1-crt-green mk1-view-audio">
                            <div className="mk1-panel-title">AUDIO INPUT</div>
                            <div className="mk1-panel-body mk1-system-body">
                                <div className="mk1-service-row">
                                    <span className={voiceServiceClass}>{voiceServiceUp ? "VOICE INPUT ON" : "VOICE INPUT OFF"}</span>
                                    <button className="mk1-mini-action" type="button" onClick={toggleVoiceLoopService} disabled={isStartingVoice}>
                                        {isStartingVoice ? (voiceServiceUp ? "STOPPING..." : "STARTING...") : (voiceServiceUp ? "STOP VOICE IN" : "START VOICE IN")}
                                    </button>
                                </div>

                                <div className="mk1-service-row">
                                    <span className={voiceOutputOn ? "mk1-service-badge mk1-service-up" : "mk1-service-badge mk1-service-down"}>
                                        {voiceOutputOn ? "VOICE OUTPUT ON" : "VOICE OUTPUT OFF"}
                                    </span>
                                    <button className="mk1-mini-action" type="button" onClick={toggleVoiceOutput}>
                                        {voiceOutputOn ? "STOP VOICE OUT" : "START VOICE OUT"}
                                    </button>
                                </div>

                                {/* Live waveform drawn from the VAD metrics ring buffer */}
                                <div className="mk1-mini-field">
                                    <div className="mk1-mini-label">Waveform (input over time)</div>
                                    <WaveformCanvas history={voiceMetricsHistory} threshold={Number(voiceMetrics?.threshold ?? 0)} />
                                </div>

                                {/* Input device picker with live per-device level bars */}
                                <div className="mk1-mini-field">
                                    <div className="mk1-mini-label">Input device</div>
                                    <div className="mk1-device-actions">
                                        <button className="mk1-mini-action" type="button" onClick={loadVoiceDevices}>
                                            REFRESH DEVICES
                                        </button>
                                        <button className="mk1-mini-action" type="button" onClick={probeVoiceDevices} disabled={isProbingDevices}>
                                            {isProbingDevices ? "PROBING..." : "PROBE LEVELS"}
                                        </button>
                                    </div>
                                    <div className="mk1-device-list">
                                        {voiceDevices.length === 0 && <div className="mk1-panel-note">No devices loaded — hit REFRESH DEVICES.</div>}
                                        {voiceDevices.map((d) => {
                                            const lvl = voiceDeviceLevels[d.index];
                                            const active = Number(voiceCurrentDevice) === Number(d.index);
                                            const lvlPct = lvl == null ? 0 : Math.min(100, Math.round((lvl / 0.05) * 100));
                                            return (
                                                <button
                                                    key={d.index}
                                                    type="button"
                                                    className={`mk1-device-row ${active ? "is-active" : ""}`}
                                                    onClick={() => selectVoiceDevice(d.index)}
                                                    title={d.name}
                                                >
                                                    <span className="mk1-device-name">[{d.index}] {d.name}</span>
                                                    <span className="mk1-device-meter"><span className="mk1-device-meter-fill" style={{ width: `${lvlPct}%` }} /></span>
                                                    <span className="mk1-device-rms">{lvl == null ? "--" : lvl.toFixed(4)}</span>
                                                </button>
                                            );
                                        })}
                                    </div>
                                    <div className="mk1-panel-note">Pick your headset by watching which device lights up when you talk. Change applies on next voice-in restart.</div>
                                </div>

                                {(() => {
                                    const m = voiceMetrics;
                                    const live = Boolean(m) && voiceServiceUp;
                                    const rms = Number(m?.rms ?? 0);
                                    const floor = Number(m?.noise_floor ?? 0);
                                    const thresh = Number(m?.threshold ?? 0);
                                    const capturing = Boolean(m?.capturing);
                                    const muted = Boolean(m?.muted);
                                    const maxLevel = Math.max(thresh * 2, rms, floor, 0.001);
                                    const pct = (v) => `${Math.min(100, Math.round((v / maxLevel) * 100))}%`;
                                    return (
                                        <>
                                            <div className="mk1-panel-note">
                                                {muted
                                                    ? "CAPTURE MUTED (privacy gate)"
                                                    : live
                                                        ? (capturing ? "LISTENING — speech detected" : "LISTENING — ambient")
                                                        : "NO LIVE METER (voice loop off or not streaming)"}
                                            </div>

                                            <div className="mk1-mini-field">
                                                <div className="mk1-mini-label">Input level (RMS)</div>
                                                <div className="mk1-meter"><div className="mk1-meter-fill mk1-meter-rms" style={{ width: pct(rms) }} /></div>
                                                <div className="mk1-kv">rms={rms.toFixed(4)}</div>
                                            </div>

                                            <div className="mk1-mini-field">
                                                <div className="mk1-mini-label">Noise floor (ambient — Spotify/room)</div>
                                                <div className="mk1-meter"><div className="mk1-meter-fill mk1-meter-floor" style={{ width: pct(floor) }} /></div>
                                                <div className="mk1-kv">floor={floor.toFixed(4)}</div>
                                            </div>

                                            <div className="mk1-mini-field">
                                                <div className="mk1-mini-label">Trigger threshold (adaptive)</div>
                                                <div className="mk1-meter"><div className="mk1-meter-fill mk1-meter-thresh" style={{ width: pct(thresh) }} /></div>
                                                <div className="mk1-kv">thresh={thresh.toFixed(4)} | snr×{m?.snr_ratio ?? "--"} | floor α={m?.noise_floor_alpha ?? "--"}</div>
                                            </div>

                                            <div className="mk1-panel-note">
                                                When input sits at the noise floor (music), threshold rides above it and nothing triggers. Speech spikes over the floor and trips. If music still triggers, raise the trigger gap (snr-ratio) or lower mic gain.
                                            </div>
                                        </>
                                    );
                                })()}
                            </div>
                        </div>

                        <div className="mk1-panel mk1-crt-amber mk1-right-bottom mk1-view-status">
                            <div className="mk1-panel-title">LIVE STATUS</div>

                            <div className="mk1-panel-body mk1-system-body">
                                <div className="mk1-panel-note">CORE RUNTIME</div>
                                <div className="mk1-kv">
                                    <span className="mk1-k">API CORE</span>

                                    <span className="mk1-v" style={{ color: corePullout.online ? "#00ff88" : "#ff6f66" }}>
                                        {corePullout.online ? "ONLINE" : "OFFLINE"}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">MODEL</span>

                                    <span className="mk1-v" style={{ color: corePullout.modelLoaded ? "#00ff88" : "#ff6f66" }}>
                                        {corePullout.modelLoaded ? "LOADED" : "UNAVAILABLE"}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">TOOLS REGISTERED</span>

                                    <span className="mk1-v">{corePullout.toolCount}</span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">HEARTBEAT</span>

                                    <span className="mk1-v">T{corePullout.heartbeatTick} / {Math.round(corePullout.heartbeatElapsedSeconds)}s</span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">LAST USER INPUT</span>

                                    <span className="mk1-v">{formatElapsed(corePullout.secondsSinceInput)}</span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">VOICE MONITOR</span>

                                    <span className="mk1-v" style={{ color: corePullout.voiceRunning ? "#00ff88" : "#ffb000" }}>
                                        {corePullout.voiceRunning ? `RUNNING ${corePullout.voicePid || ""}` : "OFF"}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">IDLE CHECK-IN</span>

                                    <span className="mk1-v">{corePullout.idleCheckinPending ? "PENDING" : "CLEAR"}</span>
                                </div>

                                <div className="mk1-panel-note">MEMORY ENGINE</div>

                                <div className="mk1-kv"><span className="mk1-k">MEMORY DATABASE</span><span className="mk1-v">{corePullout.memoryDbPresent ? "PRESENT" : "MISSING"}</span></div>
                                <div className="mk1-kv"><span className="mk1-k">FAISS SMALL</span><span className="mk1-v">{corePullout.smallIndexLoaded ? `${corePullout.smallIndexCount} LOADED` : "UNAVAILABLE"}</span></div>
                                <div className="mk1-kv"><span className="mk1-k">FAISS BASE</span><span className="mk1-v">{corePullout.baseIndexLoaded ? `${corePullout.baseIndexCount} LOADED` : "UNAVAILABLE"}</span></div>
                                <div className="mk1-kv"><span className="mk1-k">PENDING WRITES</span><span className="mk1-v">{corePullout.memoryBufferPending}</span></div>
                                <div className="mk1-kv"><span className="mk1-k">LAST BACKUP</span><span className="mk1-v">{formatUnixTime(corePullout.memoryLastBackup)}</span></div>

                                <div className="mk1-panel-note">DATABASE</div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">DB LINK</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbLinkText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">DB SYNC</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbSyncText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">DB LATENCY</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbLatencyText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">ACTIVE CONNECTIONS</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbActiveConnText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">READ OPS</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbReadOpsText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">WRITE OPS</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbWriteOpsText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">CACHE STATE</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbCacheStateText}
                                    </span>
                                </div>

                                <div className="mk1-kv">
                                    <span className="mk1-k">LAST COMMIT</span>

                                    <span className="mk1-v" style={{ color: dbColor }}>
                                        {dbLastCommitText}
                                    </span>
                                </div>
                            </div>

                        </div>
                    </section>
                </main>

                <section className="mk1-tab-workspace mk1-autonomy-workspace" aria-label="Autonomy status">
                    <div className="mk1-panel mk1-crt-green mk1-tab-panel">
                        <div className="mk1-panel-title">AUTONOMOUS WORK</div>
                        <div className="mk1-autonomy-grid">
                            <div className="mk1-autonomy-stat"><span>ELIGIBLE</span><b>{Number(decisionSummary.eligible_count || 0)}</b></div>
                            <div className="mk1-autonomy-stat"><span>DEFERRED</span><b>{Number(decisionSummary.deferred_count || 0)}</b></div>
                            <div className="mk1-autonomy-stat"><span>LAST OUTCOME</span><b>{decisionSummary.last_outcome || "none"}</b></div>
                            <div className="mk1-autonomy-stat"><span>MODEL BLOCKS</span><b>{Number(decisionSummary.model_unavailable_count || 0)}</b></div>
                        </div>
                        <div className="mk1-autonomy-columns">
                            <div>
                                <div className="mk1-panel-note">WHY WORK IS DEFERRED</div>
                                {Object.keys(deferredByReason).length ? Object.entries(deferredByReason).map(([reason, count]) => (
                                    <div className="mk1-kv" key={reason}><span className="mk1-k">{reason.replaceAll("_", " ")}</span><span className="mk1-v">{count}</span></div>
                                )) : <div className="mk1-panel-note">No deferred work.</div>}
                                <div className="mk1-panel-note">WATCHDOG</div>
                                <div className="mk1-kv"><span className="mk1-k">READY</span><span className="mk1-v">{decisionSummary.watchdog?.ready ? "YES" : "NO"}</span></div>
                                <div className="mk1-kv"><span className="mk1-k">SOURCE EDITS</span><span className="mk1-v">{decisionSummary.watchdog?.source_edits_permitted ? "PERMITTED" : "DISABLED"}</span></div>
                                <div className="mk1-panel-note">DEFERRED WORK</div>
                                <div className="mk1-deferred-list">
                                    {deferredPreview.length ? deferredPreview.map((task) => (
                                        <div className="mk1-deferred-item" key={task.key}>
                                            <b>{task.title}</b>
                                            <span>{String(task.reason || "deferred").replaceAll("_", " ")}</span>
                                            <button className="mk1-mini-action" type="button" onClick={() => promoteDeferredTask(task)}>PROMOTE</button>
                                        </div>
                                    )) : <div className="mk1-panel-note">No deferred tasks.</div>}
                                </div>
                            </div>
                            <div>
                                <div className="mk1-panel-note">RECENT AUTONOMY EVENTS</div>
                                <div className="mk1-autonomy-events">
                                    {autonomyEvents.length ? autonomyEvents.slice().reverse().map((event) => (
                                        <div className="mk1-autonomy-event" key={`${event.id}-${event.message}`}>
                                            <b>{event.source.toUpperCase()}</b> {event.message}
                                            {event.reference && <span>{event.reference}</span>}
                                        </div>
                                    )) : <div className="mk1-panel-note">No autonomy events in this session.</div>}
                                </div>
                            </div>
                        </div>
                        <form className="mk1-autonomy-intake" onSubmit={submitAutonomyTask}>
                            <div className="mk1-panel-note">APPROVE OR ADD BOUNDED WORK</div>
                            <input value={autonomyDraft.title} onChange={(event) => setAutonomyDraft((draft) => ({ ...draft, title: event.target.value }))} placeholder="Task title" required />
                            <input value={autonomyDraft.proof_target} onChange={(event) => setAutonomyDraft((draft) => ({ ...draft, proof_target: event.target.value }))} placeholder="Proof target: GET /status or tests/test_example.py" required />
                            <select value={autonomyDraft.allowed_action} onChange={(event) => setAutonomyDraft((draft) => ({ ...draft, allowed_action: event.target.value }))}>
                                <option value="read_only">Read-only review</option>
                                <option value="test_run">Test run</option>
                                <option value="proposed_patch">Proposed patch</option>
                            </select>
                            <input value={autonomyDraft.done_when} onChange={(event) => setAutonomyDraft((draft) => ({ ...draft, done_when: event.target.value }))} placeholder="Done when: observable result" required />
                            <button className="mk1-btn" type="submit" disabled={autonomyTaskBusy}>{autonomyTaskBusy ? "APPROVING..." : "APPROVE FOR MINA"}</button>
                        </form>
                        <div className="mk1-approved-task-list">
                            <div className="mk1-panel-note">USER-APPROVED TASKS</div>
                            {approvedAutonomyTasks.filter((task) => task?.status === "pending").map((task) => (
                                <div className="mk1-approved-task" key={task.id}><b>{task.title}</b><span>{task.allowed_action.replaceAll("_", " ")} | {task.proof_target}</span></div>
                            ))}
                            {!approvedAutonomyTasks.some((task) => task?.status === "pending") && <div className="mk1-panel-note">No approved tasks pending.</div>}
                        </div>
                    </div>
                </section>

                <section className="mk1-tab-workspace mk1-ostranauts-workspace" aria-label="Ostranauts integration status">
                    <div className="mk1-panel mk1-crt-amber mk1-tab-panel">
                        <div className="mk1-panel-title">OSTRANAUTS INTEGRATION</div>
                        <div className="mk1-hold-state">ON HOLD</div>
                        <p className="mk1-panel-note">The companion remains read-only and paused while Mina's core reliability work is completed.</p>
                        <button className="mk1-btn" type="button" onClick={() => invokeTauri("open_ostranauts_companion")}>OPEN READ-ONLY COMPANION</button>
                    </div>
                </section>

            </div>

            {showEmoji &&
                createPortal(
                    <div
                        ref={emojiPanelRef}
                        className="mk1-emoji-slideup animated-slideup"
                        onMouseDown={(e) => e.stopPropagation()}
                        style={{
                            left: emojiPos.x,
                            top: emojiPos.y,
                            transform: "translate(-50%, -100%)",
                        }}
                    >
                        {emojiList.map((emoji, idx) => (
                            <button
                                key={idx}
                                className="mk1-emoji-item"
                                type="button"
                                onClick={() => insertEmoji(emoji)}
                            >
                                {emoji}
                            </button>
                        ))}
                    </div>,
                    document.body
                )}
        </>
    );
}