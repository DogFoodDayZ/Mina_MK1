import React, { useEffect, useRef, useState } from "react";
import "./OstranautsCompanion.css";

const API_BASE = "http://127.0.0.1:8000";
const SHOULDER_CHECKIN_MS = 45000;
const SHOULDER_STATE_CHANGE_COOLDOWN_MS = 12000;
const TELEMETRY_POLL_MS = 2000;
const TELEMETRY_MISS_TOLERANCE = 3;
const TELEMETRY_OFFLINE_NOTIFY_GRACE_MS = 10000;
const TELEMETRY_PAGES = [
    { id: "overview", label: "OVR" },
    { id: "objectives", label: "OBJ" },
    { id: "crew", label: "CREW" },
    { id: "systems", label: "SYS" },
    { id: "interact", label: "INT" },
    { id: "diag", label: "DIAG" },
];
const MUSIC_REQUEST = /\b(spotify|music|song|track|album|artist|playlist|play\s+list)\b/i;
const ACTION_REQUEST = /\b(action|plan|next step|what should i do|do next|move|inspect|check|approach|talk|use|dock|undock|explore|scan|interact|operate|pilot|navigate)\b/i;
const OSTRANAUTS_REQUEST = /\b(ostranauts|ship|crew|objective|objectives|scene|telemetry|diagnostic|sensor|focus|station|hold|bay|engine|dock|cargo|mission)\b/i;
const MEMORY_REQUEST = /\b(remember|recall|learn|learned|history|what do you know|what do you remember|what have you learned)\b/i;

function compactText(value, maxLength = 140) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function buildGameBrief(status) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object" ? status.telemetry : {};
    const objectives = Array.isArray(telemetry.objectives)
        ? telemetry.objectives.filter((item) => item && typeof item === "object" && !item.complete)
        : [];
    const crew = Array.isArray(telemetry.crew)
        ? telemetry.crew.filter((item) => item && typeof item === "object")
        : [];
    const diagnostics = Array.isArray(telemetry.diagnostics)
        ? telemetry.diagnostics.filter(Boolean)
        : [];

    if (!status?.telemetry_online) {
        return [
            "Telemetry is offline. Launch Ostranauts or bring it to the foreground so Mina can read live ship data.",
            "Once the bridge is live, Mina will use scene, objective, crew, and diagnostic data for short tactical guidance.",
        ];
    }

    const lines = [];
    const scene = compactText(telemetry.scene || "unknown", 60);
    const player = telemetry.player && typeof telemetry.player === "object"
        ? compactText(telemetry.player.name || "player", 48)
        : "player";
    const focus = telemetry.game_focused == null ? "unknown" : telemetry.game_focused ? "active" : "background";

    lines.push(`Scene: ${scene}.`);
    lines.push(`Focus: ${focus}.`);
    lines.push(`Player: ${player}.`);
    lines.push(`Open objectives: ${objectives.length || 0}.`);

    if (objectives.length) {
        const firstObjective = compactText(objectives[0].name || objectives[0].description || "Unnamed objective", 120);
        lines.push(`Lead objective: ${firstObjective}.`);
    }

    if (crew.length) {
        const crewNames = crew.slice(0, 6).map((member) => compactText(member.name || "crew", 32));
        lines.push(`Crew: ${crewNames.join(", ")}.`);
    }

    if (diagnostics.length) {
        lines.push(`Unavailable data: ${diagnostics.slice(0, 5).map((item) => compactText(item, 42)).join(", ")}.`);
    }

    const calibration = status?.condition_calibration && typeof status.condition_calibration === "object"
        ? status.condition_calibration
        : null;
    const primaryEntity = Array.isArray(calibration?.entities) ? calibration.entities[0] : null;
    if (primaryEntity && typeof primaryEntity === "object") {
        const counts = primaryEntity.counts && typeof primaryEntity.counts === "object" ? primaryEntity.counts : null;
        if (counts) {
            const topCounts = Object.entries(counts)
                .sort((a, b) => Number(b[1]) - Number(a[1]))
                .slice(0, 3)
                .map(([key, value]) => `${key}=${value}`);
            if (topCounts.length) {
                lines.push(`Condition scan: ${topCounts.join(", ")}.`);
            }
        }
    }

    lines.push("Mina should answer only from live telemetry and clearly say when something is unavailable.");
    return lines;
}

function buildQuickPrompts(status) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object" ? status.telemetry : {};
    const objectives = Array.isArray(telemetry.objectives)
        ? telemetry.objectives.filter((item) => item && typeof item === "object" && !item.complete)
        : [];

    const prompts = [
        "What do you remember about this run?",
        "What should I do next?",
        "Summarize the live ship state.",
        "Who needs attention right now?",
        "What is unavailable or unknown?",
    ];

    if (objectives.length) {
        const firstObjective = compactText(objectives[0].name || objectives[0].description || "active objective", 80);
        prompts.unshift(`Focus on: ${firstObjective}`);
    }

    return prompts.slice(0, 3);
}

function extractQuestionLine(text) {
    const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const tagged = lines.find((line) => /^QUESTION\s*:/i.test(line));
    if (tagged) {
        return tagged.replace(/^QUESTION\s*:\s*/i, "").trim();
    }
    return lines[0] || "What should we do next right now?";
}

function buildActionPrompt(question, status) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object"
        ? JSON.stringify(status.telemetry)
        : "unavailable";

    return [
        "You are Mina. Keep your normal Mina personality, but answer as an action planner rather than an observer.",
        "Use only live Ostranauts telemetry and say clearly when a needed detail is unavailable.",
        "Propose one safest next action, not a list of vague ideas.",
        "Format your answer as:",
        "NEXT ACTION: ...",
        "WHY: ...",
        "RISK: ...",
        "NEEDED INFO: ...",
        "CONFIRMATION: yes or no",
        `Verified telemetry: ${telemetry}`,
        `Question: ${question}`,
    ].join("\n");
}

function inferActionIntent(question) {
    const text = String(question || "").toLowerCase();
    if (/\bcrew\b|\btalk\b|\bwho needs attention\b/.test(text)) {
        return { intent: "crew_interaction", verb: "interact", target: "crew" };
    }
    if (/\bship\b|\binspect\b|\bscan\b|\bdiagnostic\b/.test(text)) {
        return { intent: "ship_inspection", verb: "inspect", target: "ship" };
    }
    if (/\bobjective\b|\bmission\b|\bnext step\b|\bdo next\b|\bsafest next action\b/.test(text)) {
        return { intent: "objective_step", verb: "advance", target: "objective" };
    }
    return { intent: "next_action", verb: "plan", target: "run" };
}

function buildTelemetrySummary(status) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object" ? status.telemetry : {};
    return {
        schema_version: telemetry.schema_version ?? 4,
        scene: telemetry.scene || null,
        game_focused: telemetry.game_focused ?? null,
        session_uptime_seconds: telemetry.session_uptime_seconds ?? null,
        objective_count: Array.isArray(telemetry.objectives) ? telemetry.objectives.filter((item) => item && typeof item === "object" && !item.complete).length : 0,
        crew_count: Array.isArray(telemetry.crew) ? telemetry.crew.filter((item) => item && typeof item === "object").length : 0,
        diagnostics: Array.isArray(telemetry.diagnostics) ? telemetry.diagnostics.filter(Boolean).slice(0, 8) : [],
    };
}

function buildActionEnvelope(question, status, planText) {
    const intentInfo = inferActionIntent(question);
    const telemetry = buildTelemetrySummary(status);

    return {
        action_id: `ost-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        schema_version: 1,
        source: "ostranauts_companion",
        kind: "ostranauts_action",
        intent: intentInfo.intent,
        verb: intentInfo.verb,
        target: intentInfo.target,
        parameters: {},
        request_text: question,
        plan_text: String(planText || "").trim(),
        telemetry,
        requires_confirmation: true,
        confidence: 0.5,
        safety: "review_required",
        notes: "Drafted from the action-training companion.",
        status: "queued",
        approved: false,
    };
}

async function queueActionDraft(envelope) {
    const response = await fetch(`${API_BASE}/games/ostranauts/actions/propose`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(envelope),
    });

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }

    return response.json();
}

function buildRequestPrompt(question, status) {
    const isGameQuestion = OSTRANAUTS_REQUEST.test(question) || MEMORY_REQUEST.test(question);
    const telemetry = status?.telemetry && typeof status.telemetry === "object"
        ? JSON.stringify(status.telemetry)
        : "unavailable";

    if (!isGameQuestion) {
        return [
            "Answer naturally and directly. Do not force a game briefing when the user is just talking.",
            `Question: ${question}`,
        ].join("\n");
    }

    return [
        "Use live telemetry when the question depends on current game state, and remember what you learn for the run.",
        buildGameBrief(status).join("\n"),
        `Verified telemetry: ${telemetry}`,
        `Question: ${question}`,
    ].join("\n");
}

function formatAge(value) {
    const age = Number(value);
    if (!Number.isFinite(age)) return "NO SIGNAL";
    return age < 1 ? "LIVE" : `${Math.round(age)}s AGO`;
}

function buildWarmupMessage(online, label) {
    if (!online) {
        return `No ${label} telemetry reported.`;
    }
    return `${label} telemetry is warming up. Keep flying and this panel will populate as runtime fields become available.`;
}

function buildShoulderSnapshot(status) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object" ? status.telemetry : {};
    const objectives = Array.isArray(telemetry.objectives)
        ? telemetry.objectives.filter((item) => item && typeof item === "object" && !item.complete)
        : [];
    const crew = Array.isArray(telemetry.crew)
        ? telemetry.crew.filter((item) => item && typeof item === "object")
        : [];
    const diagnostics = Array.isArray(telemetry.diagnostics)
        ? telemetry.diagnostics.filter(Boolean)
        : [];
    const nearby = telemetry.nearby_interactables && typeof telemetry.nearby_interactables === "object"
        ? telemetry.nearby_interactables
        : null;
    const interactables = Array.isArray(nearby?.items)
        ? nearby.items.filter((item) => item && typeof item === "object")
        : [];

    return {
        online: Boolean(status?.telemetry_online),
        scene: compactText(telemetry.scene || "unknown", 60),
        focus: telemetry.game_focused == null ? "unknown" : telemetry.game_focused ? "active" : "background",
        objective_count: objectives.length,
        lead_objective: compactText(objectives[0]?.name || objectives[0]?.description || "none", 96),
        crew_count: crew.length,
        interactable_count: interactables.length,
        diagnostics: diagnostics.slice(0, 4).map((item) => compactText(item, 40)),
    };
}

function buildShoulderCoachPrompt(status, reason) {
    const telemetry = status?.telemetry && typeof status.telemetry === "object" ? status.telemetry : {};
    const snapshot = buildShoulderSnapshot(status);
    const reasonText = reason === "telemetry_lock"
        ? "telemetry lock acquired"
        : reason === "state_change"
            ? "state changed"
            : "periodic check-in";

    const telemetrySummary = {
        scene: telemetry.scene || null,
        game_focused: telemetry.game_focused ?? null,
        objective_count: snapshot.objective_count,
        lead_objective: snapshot.lead_objective,
        crew_count: snapshot.crew_count,
        interactable_count: snapshot.interactable_count,
        diagnostics: snapshot.diagnostics,
    };

    return [
        "You are Mina, actively shoulder-copiloting Ostranauts with Travis in real time.",
        "Be proactive and practical. You are not waiting to be asked.",
        "Use only verified live telemetry. If data is missing, say exactly what is missing.",
        "Respond in exactly 3 short lines:",
        "OBSERVATION: ...",
        "SUGGESTION: ...",
        "QUESTION: ...",
        `Trigger: ${reasonText}`,
        `Verified telemetry: ${JSON.stringify(telemetrySummary)}`,
    ].join("\n");
}

export default function OstranautsCompanion({
    avatarSrc,
    askMina,
    onClose,
}) {
    const [status, setStatus] = useState(null);
    const [input, setInput] = useState("");
    const [busy, setBusy] = useState(false);
    const [telemetryPage, setTelemetryPage] = useState("overview");
    const [pendingCopilotPrompt, setPendingCopilotPrompt] = useState(null);
    const [messages, setMessages] = useState([
        { from: "MINA", text: "Companion station ready. Launch Ostranauts to establish live telemetry." },
    ]);
    const logRef = useRef(null);
    const inputRef = useRef(null);
    const lastShoulderDigestRef = useRef("");
    const lastShoulderAtRef = useRef(0);
    const shoulderInFlightRef = useRef(false);
    const hadLiveTelemetryRef = useRef(false);
    const offlineNotifiedRef = useRef(false);
    const telemetryMissesRef = useRef(0);
    const lastGoodStatusRef = useRef(null);
    const offlineSinceRef = useRef(0);

    useEffect(() => {
        let stopped = false;
        let timer = null;

        async function poll() {
            try {
                const response = await fetch(`${API_BASE}/games/ostranauts/status`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json();
                if (!stopped) {
                    telemetryMissesRef.current = 0;
                    if (payload?.telemetry_online) {
                        lastGoodStatusRef.current = payload;
                    }
                    setStatus(payload);
                }
            } catch (error) {
                if (!stopped) {
                    telemetryMissesRef.current += 1;
                    const canHoldLastGood =
                        telemetryMissesRef.current < TELEMETRY_MISS_TOLERANCE &&
                        lastGoodStatusRef.current &&
                        lastGoodStatusRef.current.telemetry_online;

                    if (canHoldLastGood) {
                        setStatus((current) => ({
                            ...(current && typeof current === "object" ? current : lastGoodStatusRef.current),
                            error: `poll_transient:${error.message}`,
                        }));
                    } else {
                        setStatus((current) => ({
                            ...(current && typeof current === "object" ? current : {}),
                            ok: false,
                            error: error.message,
                            telemetry_online: false,
                        }));
                    }
                }
            } finally {
                if (!stopped) timer = setTimeout(poll, TELEMETRY_POLL_MS);
            }
        }

        void poll();
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
    }, []);

    useEffect(() => {
        if (logRef.current) {
            logRef.current.scrollTop = logRef.current.scrollHeight;
        }
    }, [messages]);

    useEffect(() => {
        const online = Boolean(status?.telemetry_online);

        if (!online) {
            if (!offlineSinceRef.current) {
                offlineSinceRef.current = Date.now();
            }

            const offlineForMs = Date.now() - offlineSinceRef.current;
            if (hadLiveTelemetryRef.current && !offlineNotifiedRef.current && offlineForMs >= TELEMETRY_OFFLINE_NOTIFY_GRACE_MS) {
                offlineNotifiedRef.current = true;
                setMessages((current) => [
                    ...current,
                    { from: "SYSTEM", text: "Telemetry signal dropped. Mina will resume live shoulder callouts when the bridge is fresh again." },
                ]);
            }
            return;
        }

        if (offlineNotifiedRef.current) {
            setMessages((current) => [
                ...current,
                { from: "SYSTEM", text: "Telemetry lock restored. Live shoulder callouts resumed." },
            ]);
        }
        offlineNotifiedRef.current = false;
        offlineSinceRef.current = 0;

        if (busy || shoulderInFlightRef.current || pendingCopilotPrompt) {
            return;
        }

        const snapshot = buildShoulderSnapshot(status);
        const digest = JSON.stringify(snapshot);
        const now = Date.now();
        const sinceLast = now - Number(lastShoulderAtRef.current || 0);

        let trigger = "";
        if (!hadLiveTelemetryRef.current) {
            trigger = "telemetry_lock";
        } else if (digest !== lastShoulderDigestRef.current && sinceLast >= SHOULDER_STATE_CHANGE_COOLDOWN_MS) {
            trigger = "state_change";
        } else if (sinceLast >= SHOULDER_CHECKIN_MS) {
            trigger = "heartbeat";
        }

        hadLiveTelemetryRef.current = true;

        if (!trigger) {
            return;
        }

        lastShoulderDigestRef.current = digest;
        lastShoulderAtRef.current = now;
        shoulderInFlightRef.current = true;

        void (async () => {
            try {
                const request = buildShoulderCoachPrompt(status, trigger);
                const result = await askMina(request);
                const replyText = String(result?.text || "").trim() || "OBSERVATION: Awaiting clear telemetry details.\nSUGGESTION: Re-center on your objective panel and crew state.\nQUESTION: What are you trying to accomplish in the next 60 seconds?";
                setPendingCopilotPrompt({
                    id: `copilot-${Date.now()}`,
                    text: replyText,
                    trigger,
                    createdAt: Date.now(),
                });
            } catch (error) {
                setMessages((current) => [...current, { from: "SYSTEM", text: `Shoulder copilot check-in failed: ${error.message}` }]);
            } finally {
                shoulderInFlightRef.current = false;
            }
        })();
    }, [status, busy, askMina, pendingCopilotPrompt]);

    async function submit(prompt = input) {
        const question = String(prompt || "").trim();
        if (!question || busy) return;

        setInput("");
        setBusy(true);
        setMessages((current) => [...current, { from: "TRAVIS", text: question }]);

        const request = MUSIC_REQUEST.test(question)
            ? question
            : ACTION_REQUEST.test(question)
                ? buildActionPrompt(question, status)
                : buildRequestPrompt(question, status);

        try {
            const result = await askMina(request);
            const replyText = result?.text || "No response received.";
            setMessages((current) => [...current, { from: "MINA", text: replyText }]);

            if (ACTION_REQUEST.test(question)) {
                try {
                    const queued = await queueActionDraft(buildActionEnvelope(question, status, replyText));
                    const actionId = queued?.action?.action_id || queued?.action?.id || "unknown";
                    setMessages((current) => [...current, { from: "SYSTEM", text: `Queued Ostranauts action draft: ${actionId}` }]);
                } catch (queueError) {
                    setMessages((current) => [...current, { from: "SYSTEM", text: `Action queue failed: ${queueError.message}` }]);
                }
            }
        } catch (error) {
            setMessages((current) => [...current, { from: "SYSTEM", text: `Companion link failed: ${error.message}` }]);
        } finally {
            setBusy(false);
        }
    }

    function acknowledgePendingPrompt() {
        if (!pendingCopilotPrompt) return;
        setMessages((current) => [
            ...current,
            { from: "MINA", text: pendingCopilotPrompt.text },
            { from: "SYSTEM", text: "Copilot prompt acknowledged." },
        ]);
        setPendingCopilotPrompt(null);
    }

    function dismissPendingPrompt() {
        if (!pendingCopilotPrompt) return;
        setPendingCopilotPrompt(null);
    }

    function stagePendingFollowUp() {
        if (!pendingCopilotPrompt) return;
        const question = extractQuestionLine(pendingCopilotPrompt.text);
        setInput(question);
        if (inputRef.current) {
            inputRef.current.focus();
        }
    }

    const telemetry = status?.telemetry || {};
    const online = Boolean(status?.telemetry_online);
    const capabilities = Array.isArray(telemetry.capabilities) ? telemetry.capabilities : [];
    const expectedCapabilities = [
        "player_conditions",
        "crew_roster",
        "player_ship",
        "ship_layout",
        "ship_systems",
        "dock_network",
        "nearby_interactables",
        "interaction_context",
        "objectives",
    ];
    const pendingCapabilities = expectedCapabilities.filter((capability) => !capabilities.includes(capability));
    const quickPrompts = buildQuickPrompts(status);
    const objectiveRows = Array.isArray(telemetry.objectives)
        ? telemetry.objectives.filter((item) => item && typeof item === "object")
        : [];
    const activeObjectives = objectiveRows.filter((item) => !item.complete);
    const crewRows = Array.isArray(telemetry.crew)
        ? telemetry.crew.filter((item) => item && typeof item === "object")
        : [];
    const diagnosticsRows = Array.isArray(telemetry.diagnostics)
        ? telemetry.diagnostics.filter(Boolean)
        : [];
    const missionBrief = buildGameBrief(status);
    const focusLine = missionBrief[0] || "Scene: unknown.";
    const objectiveLine = missionBrief.find((line) => String(line).startsWith("Lead objective:")) || missionBrief[3] || "Open objectives: unknown.";
    const shipLayout = telemetry.ship_layout && typeof telemetry.ship_layout === "object" ? telemetry.ship_layout : null;
    const shipSystems = telemetry.ship_systems && typeof telemetry.ship_systems === "object" ? telemetry.ship_systems : null;
    const interactables = telemetry.nearby_interactables && typeof telemetry.nearby_interactables === "object" ? telemetry.nearby_interactables : null;
    const interactableRows = Array.isArray(interactables?.items)
        ? interactables.items.filter((item) => item && typeof item === "object")
        : [];

    return (
        <main className="ost-root">
            <header className="ost-header">
                <div className="ost-wordmark">
                    <span className="ost-kicker">SALVAGE COMPANION // MK1</span>
                    <h1>MINA // OSTRANAUTS</h1>
                </div>
                <div className="ost-header-actions">
                    <span className={`ost-link-state ${online ? "is-online" : ""}`}>
                        {online ? "TELEMETRY LOCK" : "AWAITING GAME"}
                    </span>
                    <button className="ost-exit ost-mfd-btn" type="button" onClick={onClose} title="Close companion and return to Mina">
                        CLOSE COMPANION
                    </button>
                </div>
            </header>

            <section className="ost-workspace">
                <aside className="ost-mina-bay">
                    <div className="ost-avatar-frame">
                        <img src={avatarSrc} alt="Mina companion avatar" />
                        <div className="ost-scanline" />
                    </div>
                    <div className="ost-ident">
                        <span>CO-PILOT</span>
                        <strong>MINA</strong>
                        <small>{online ? "LINKED TO VESSEL DATA" : "STANDING BY"}</small>
                    </div>
                    <div className="ost-orbit-display" aria-label="Local orbital plot">
                        <span className="ost-orbit ost-orbit-one" />
                        <span className="ost-orbit ost-orbit-two" />
                        <span className="ost-orbit-core" />
                        <span className="ost-sweep" />
                    </div>
                </aside>

                <section className="ost-conversation">
                    <div className="ost-section-label">OPEN COMMS</div>
                    <div className="ost-subpanel ost-briefing-panel">
                        <span>LIVE SHOULDER COPILOT (QUEUE: 1)</span>
                        <div className="ost-brief-text">
                            <div>{focusLine}</div>
                            <div>{objectiveLine}</div>
                        </div>
                    </div>
                    <div className="ost-copilot-queue" aria-live="polite">
                        <div className="ost-copilot-head">
                            <span>PENDING COPILOT PROMPT</span>
                            <b>{pendingCopilotPrompt ? "WAITING FOR YOUR ACTION" : "NO PENDING PROMPT"}</b>
                        </div>
                        {pendingCopilotPrompt ? (
                            <>
                                <p>{pendingCopilotPrompt.text}</p>
                                <div className="ost-copilot-actions">
                                    <button className="ost-mfd-btn" type="button" onClick={acknowledgePendingPrompt}>I ACTED ON THIS</button>
                                    <button className="ost-mfd-btn" type="button" onClick={stagePendingFollowUp}>STAGE FOLLOW-UP</button>
                                    <button className="ost-mfd-btn" type="button" onClick={dismissPendingPrompt}>DISMISS</button>
                                </div>
                            </>
                        ) : (
                            <p>Mina will hold the next comment here until you respond or clear it.</p>
                        )}
                    </div>
                    <div className="ost-log" ref={logRef}>
                        {messages.map((message, index) => (
                            <div className={`ost-message is-${message.from.toLowerCase()}`} key={`${message.from}-${index}`}>
                                <span>{message.from}</span>
                                <p>{message.text}</p>
                            </div>
                        ))}
                    </div>
                    <div className="ost-softkey-bank">
                        <div className="ost-softkey-label">MFD SOFTKEYS</div>
                        <div className="ost-quick-row">
                            {quickPrompts.map((prompt) => (
                                <button className="ost-mfd-btn" key={prompt} type="button" onClick={() => submit(prompt)} disabled={busy}>
                                    {prompt}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="ost-input-row">
                        <textarea
                            ref={inputRef}
                            value={input}
                            onChange={(event) => setInput(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter" && !event.shiftKey) {
                                    event.preventDefault();
                                    void submit();
                                }
                            }}
                            placeholder="Ask Mina about the current run..."
                        />
                        <button className="ost-mfd-btn" type="button" onClick={() => submit()} disabled={busy || !input.trim()}>
                            {busy ? "THINKING" : "TRANSMIT"}
                        </button>
                    </div>
                </section>

                <aside className="ost-telemetry">
                    <div className="ost-section-label">VESSEL DATA // MFD</div>
                    <div className="ost-telemetry-nav">
                        {TELEMETRY_PAGES.map((page) => (
                            <button
                                key={page.id}
                                type="button"
                                className={`ost-mfd-btn ${telemetryPage === page.id ? "is-active" : ""}`}
                                onClick={() => setTelemetryPage(page.id)}
                            >
                                {page.label}
                            </button>
                        ))}
                    </div>

                    <div className="ost-telemetry-page">
                        {telemetryPage === "overview" && (
                            <>
                                <dl>
                                    <div><dt>GAME</dt><dd>{status?.installed ? "FOUND" : "NOT FOUND"}</dd></div>
                                    <div><dt>BEPINEX</dt><dd>{status?.bepinex_installed ? "READY" : "MISSING"}</dd></div>
                                    <div><dt>MINA BRIDGE</dt><dd>{status?.plugin_installed ? "INSTALLED" : "MISSING"}</dd></div>
                                    <div><dt>SIGNAL</dt><dd className={online ? "is-good" : "is-warn"}>{formatAge(status?.telemetry_age_seconds)}</dd></div>
                                    <div><dt>SCENE</dt><dd>{telemetry.scene || "UNKNOWN"}</dd></div>
                                    <div><dt>GAME VERSION</dt><dd>{telemetry.game_version || "--"}</dd></div>
                                    <div><dt>SESSION</dt><dd>{telemetry.session_uptime_seconds != null ? `${telemetry.session_uptime_seconds}s` : "--"}</dd></div>
                                    <div><dt>FOCUS</dt><dd>{telemetry.game_focused == null ? "--" : telemetry.game_focused ? "ACTIVE" : "BACKGROUND"}</dd></div>
                                </dl>
                                <div className="ost-capabilities">
                                    <span>ACTIVE SENSORS</span>
                                    {capabilities.length ? capabilities.map((item) => <b key={item}>{String(item).replaceAll("_", " ")}</b>) : <em>NO LIVE SENSORS</em>}
                                    {online && pendingCapabilities.length > 0 && (
                                        <small>
                                            WARMING UP: {pendingCapabilities.slice(0, 4).map((item) => item.replaceAll("_", " ")).join(", ")}
                                            {pendingCapabilities.length > 4 ? " ..." : ""}
                                        </small>
                                    )}
                                </div>
                            </>
                        )}

                        {telemetryPage === "objectives" && (
                            <div className="ost-subpanel">
                                <span>ACTIVE OBJECTIVES</span>
                                {activeObjectives.length ? (
                                    <ul>
                                        {activeObjectives.slice(0, 8).map((objective, index) => (
                                            <li key={`${objective.name || index}-${index}`}>
                                                <strong>{compactText(objective.name || "Unnamed objective", 72)}</strong>
                                                {objective.description && <div className="ost-obj-desc">{compactText(objective.description, 180)}</div>}
                                            </li>
                                        ))}
                                    </ul>
                                ) : (
                                    <em>{buildWarmupMessage(online, "objectives")}</em>
                                )}
                            </div>
                        )}

                        {telemetryPage === "crew" && (
                            <div className="ost-subpanel">
                                <span>CREW STATUS</span>
                                {crewRows.length ? (
                                    <ul>
                                        {crewRows.slice(0, 10).map((member, index) => (
                                            <li key={`${member.name || index}-${index}`}>
                                                <div className="ost-sub-kv">
                                                    <b>{compactText(member.name || "crew", 24)}</b>
                                                    <i>{compactText(member.life_state || member.state || "unknown", 52)}</i>
                                                </div>
                                            </li>
                                        ))}
                                    </ul>
                                ) : (
                                    <em>{buildWarmupMessage(online, "crew")}</em>
                                )}
                            </div>
                        )}

                        {telemetryPage === "systems" && (
                            <>
                                <div className="ost-subpanel">
                                    <span>SHIP LAYOUT</span>
                                    {shipLayout ? (
                                        <ul>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>Modules</b>
                                                    <i>{shipLayout.module_count ?? "--"}</i>
                                                </div>
                                            </li>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>Dock ports</b>
                                                    <i>{shipLayout.dock_port_count ?? "--"}</i>
                                                </div>
                                            </li>
                                        </ul>
                                    ) : (
                                        <em>{buildWarmupMessage(online, "ship layout")}</em>
                                    )}
                                </div>
                                <div className="ost-subpanel">
                                    <span>SYSTEM STATUS</span>
                                    {shipSystems ? (
                                        <ul>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>Navigation</b>
                                                    <i>{shipSystems.navigation?.active ? "ACTIVE" : "IDLE"}</i>
                                                </div>
                                            </li>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>RCS fuel</b>
                                                    <i>{shipSystems.propulsion?.rcs_fuel ?? "--"}</i>
                                                </div>
                                            </li>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>Fusion fuel</b>
                                                    <i>{shipSystems.propulsion?.fusion_fuel ?? "--"}</i>
                                                </div>
                                            </li>
                                            <li>
                                                <div className="ost-sub-kv">
                                                    <b>Damage</b>
                                                    <i>{compactText(shipSystems.structure?.damage_status ?? "unknown", 32)}</i>
                                                </div>
                                            </li>
                                        </ul>
                                    ) : (
                                        <em>{buildWarmupMessage(online, "ship systems")}</em>
                                    )}
                                </div>
                            </>
                        )}

                        {telemetryPage === "interact" && (
                            <div className="ost-subpanel">
                                <span>INTERACTABLES</span>
                                {interactableRows.length ? (
                                    <ul>
                                        {interactableRows.slice(0, 10).map((item, index) => (
                                            <li key={`${item.id || item.name || index}-${index}`}>
                                                <div className="ost-sub-kv">
                                                    <b>{compactText(item.name || item.type || "interaction", 28)}</b>
                                                    <i>{compactText(item.type || "unknown", 24)}</i>
                                                </div>
                                            </li>
                                        ))}
                                    </ul>
                                ) : (
                                    <em>{buildWarmupMessage(online, "nearby interactables")}</em>
                                )}
                            </div>
                        )}

                        {telemetryPage === "diag" && (
                            <div className="ost-subpanel">
                                <span>DIAGNOSTICS</span>
                                {diagnosticsRows.length ? (
                                    <ul>
                                        {diagnosticsRows.slice(0, 12).map((item, index) => (
                                            <li key={`${item}-${index}`}>{compactText(item, 90)}</li>
                                        ))}
                                    </ul>
                                ) : (
                                    <em>No unavailable data reported.</em>
                                )}
                            </div>
                        )}
                    </div>

                    <div className="ost-path">{status?.game_dir || status?.error || "Scanning Steam libraries..."}</div>
                </aside>
            </section>
        </main>
    );
}