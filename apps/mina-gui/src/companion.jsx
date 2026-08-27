import React from "react";
import ReactDOM from "react-dom/client";
import OstranautsCompanion from "./components/OstranautsCompanion";
import "./crt.css";
import avatarIdle from "./assets/avatar_idle.png";
import avatarTalk from "./assets/avatar_talk.png";
import avatarTalk2 from "./assets/avatar_talk_2.png";
import avatarSmirk from "./assets/avatar_smirk.png";

const API_BASE = "http://127.0.0.1:8000";
const GUI_VOICE_HINT = "en-US-AnaNeural";

async function requestJson(path, options = {}) {
    const { timeoutMs = 15000, ...fetchOptions } = options;
    const controller = new AbortController();
    let timedOut = false;
    const timeoutId = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);

    try {
        const response = await fetch(`${API_BASE}${path}`, {
            ...fetchOptions,
            signal: controller.signal,
        });
        const text = await response.text();
        let data = null;

        if (text) {
            try {
                data = JSON.parse(text);
            } catch {
                data = text;
            }
        }

        if (!response.ok) {
            const message = typeof data === "string"
                ? data
                : data?.detail ?? data?.error ?? `HTTP ${response.status}`;
            throw new Error(message);
        }

        return data;
    } catch (error) {
        if (timedOut) {
            throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

async function sendToCore(userText, options = {}) {
    try {
        const interfaceName = String(options.interfaceName || "Ostranauts Companion").trim() || "Ostranauts Companion";
        const payload = {
            input: userText,
            speak_response: false,
            voice_hint: GUI_VOICE_HINT,
            input_source: "gui",
            interface_name: interfaceName,
        };

        const reply = await requestJson("/process", {
            method: "POST",
            timeoutMs: 180000,
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });

        let replyText = "(no reply)";
        if (reply) {
            if (typeof reply.reply === "string") {
                replyText = reply.reply;
            } else if (typeof reply.reply === "object") {
                replyText = JSON.stringify(reply.reply, null, 2);
            } else if (typeof reply.output === "string") {
                replyText = reply.output;
            } else if (reply.choices?.[0]?.message?.content) {
                replyText = reply.choices[0].message.content;
            }
        }

        return {
            text: replyText,
            image: reply?.image ?? null,
            raw: reply,
        };
    } catch (error) {
        console.error(error);
        return {
            text: `(error contacting Mina API: ${error.message})`,
            image: null,
            raw: null,
        };
    }
}

async function closeCompanionWindow() {
    if (typeof window !== "undefined" && window.__TAURI_INTERNALS__) {
        try {
            const { invoke } = await import("@tauri-apps/api/core");
            await invoke("close_ostranauts_companion");
            return;
        } catch (error) {
            console.error("Companion window close failed", error);
        }
    }

    if (typeof window !== "undefined") {
        window.close();
    }
}

const rootElement = document.getElementById("root");

if (rootElement) {
    const root = ReactDOM.createRoot(rootElement);
    root.render(
        <React.StrictMode>
            <OstranautsCompanion
                avatarSrc={avatarIdle}
                askMina={(text) => sendToCore(text, { interfaceName: "Ostranauts GUI" })}
                onClose={closeCompanionWindow}
            />
        </React.StrictMode>
    );
}
