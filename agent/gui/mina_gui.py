"""
MK1 / Mina GUI

A small real desktop front end that:
- asks for user input
- sends chat requests to the core
- polls /status and /db/status for actual health data
- can read/write/delete memory directly through the API

Run:
  python mina_gui.py
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from typing import Any, Dict, Optional

import requests


API_BASE = "http://127.0.0.1:8000"
POLL_MS = 2000
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(APP_DIR, "config", "mk1_config.json")
DEFAULT_START_SCRIPT = os.path.join(APP_DIR, "start_mk1_api.ps1")
DEFAULT_STOP_SCRIPT = os.path.join(APP_DIR, "stop_mk1_api.ps1")
DEFAULT_VOICE_MONITOR_SCRIPT = os.path.join(APP_DIR, "start_mina_voice_monitor.ps1")
VOICE_MONITOR_PID_FILE = os.path.join(APP_DIR, ".mk1_voice_monitor.pid")


class MinaGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mina Front End")
        self.geometry("1080x760")
        self.minsize(900, 640)

        self.api_base_var = tk.StringVar(value=API_BASE)
        self.status_var = tk.StringVar(value="Connecting to core...")
        self.db_var = tk.StringVar(value="Connecting to DB...")
        self.name_var = tk.StringVar(value="")
        self.memory_query_var = tk.StringVar(value="what is my name")
        self.user_input_var = tk.StringVar(value="")
        self.server_start_script_var = tk.StringVar(value=DEFAULT_START_SCRIPT)
        self.server_stop_script_var = tk.StringVar(value=DEFAULT_STOP_SCRIPT)
        self.voice_input_state_var = tk.StringVar(value="Voice input: unknown")
        self.voice_output_state_var = tk.StringVar(value="Voice output: OFF")
        self.voice_hint_var = tk.StringVar(value="en-US-AnaNeural")
        self.voice_device_var = tk.StringVar(value="-1")
        self.voice_output_enabled = tk.BooleanVar(value=False)

        self._load_server_settings()

        self._build_styles()
        self._build_layout()
        self.after(100, self._poll_status)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10))
        style.configure("Small.TLabel", font=("Segoe UI", 9))
        style.configure("Action.TButton", padding=(10, 6))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")

        ttk.Label(header, text="Mina", style="Header.TLabel").pack(side="left")
        ttk.Label(header, text="Real front end with live core + memory access", style="Small.TLabel").pack(side="left", padx=(10, 0))

        api_frame = ttk.LabelFrame(root, text="Core Endpoint", padding=10)
        api_frame.pack(fill="x", pady=(12, 10))
        ttk.Entry(api_frame, textvariable=self.api_base_var).pack(side="left", fill="x", expand=True)
        ttk.Button(api_frame, text="Refresh", style="Action.TButton", command=self.refresh_all).pack(side="left", padx=(10, 0))

        server_frame = ttk.LabelFrame(root, text="Server Control", padding=10)
        server_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(server_frame, text="Start script", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(server_frame, textvariable=self.server_start_script_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(server_frame, text="Browse", command=lambda: self._pick_script_path(self.server_start_script_var)).grid(row=0, column=2, sticky="ew")

        ttk.Label(server_frame, text="Stop script", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(server_frame, textvariable=self.server_stop_script_var).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        ttk.Button(server_frame, text="Browse", command=lambda: self._pick_script_path(self.server_stop_script_var)).grid(row=1, column=2, sticky="ew", pady=(8, 0))

        controls = ttk.Frame(server_frame)
        controls.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(controls, text="Save Paths", style="Action.TButton", command=self.save_server_settings).pack(side="left")
        ttk.Button(controls, text="Start Server", style="Action.TButton", command=self.start_server).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Stop Server", style="Action.TButton", command=self.stop_server).pack(side="left", padx=(8, 0))

        voice_frame = ttk.LabelFrame(root, text="Voice Control", padding=10)
        voice_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(voice_frame, text="Voice hint", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(voice_frame, textvariable=self.voice_hint_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(voice_frame, text="Input device (-1 default)", style="Small.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(voice_frame, textvariable=self.voice_device_var, width=8).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        voice_btns = ttk.Frame(voice_frame)
        voice_btns.grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(voice_btns, text="Start Voice Input", style="Action.TButton", command=self.start_voice_input).pack(side="left")
        ttk.Button(voice_btns, text="Stop Voice Input", style="Action.TButton", command=self.stop_voice_input).pack(side="left", padx=(8, 0))
        ttk.Button(voice_btns, text="Start Voice Output", style="Action.TButton", command=self.start_voice_output).pack(side="left", padx=(8, 0))
        ttk.Button(voice_btns, text="Stop Voice Output", style="Action.TButton", command=self.stop_voice_output).pack(side="left", padx=(8, 0))

        ttk.Label(voice_frame, textvariable=self.voice_input_state_var, style="Small.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(voice_frame, textvariable=self.voice_output_state_var, style="Small.TLabel").grid(row=2, column=2, columnspan=2, sticky="w", pady=(8, 0))

        voice_frame.columnconfigure(1, weight=1)
        voice_frame.columnconfigure(3, weight=1)

        server_frame.columnconfigure(1, weight=1)

        top = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        top.pack(fill="both", expand=True)

        left = ttk.Frame(top, padding=(0, 0, 10, 0))
        right = ttk.Frame(top)
        top.add(left, weight=3)
        top.add(right, weight=1)

        chat_box = ttk.LabelFrame(left, text="Conversation", padding=10)
        chat_box.pack(fill="both", expand=True)

        self.chat_text = tk.Text(chat_box, wrap="word", height=22, padx=10, pady=10, bg="#111318", fg="#e8e8e8", insertbackground="#ffffff")
        self.chat_text.pack(fill="both", expand=True)
        self.chat_text.tag_configure("user", foreground="#9ad1ff")
        self.chat_text.tag_configure("mina", foreground="#ffd27d")
        self.chat_text.tag_configure("meta", foreground="#9aa3ad")
        self.chat_text.configure(state="disabled")

        input_frame = ttk.Frame(left)
        input_frame.pack(fill="x", pady=(10, 0))
        ttk.Entry(input_frame, textvariable=self.user_input_var).pack(side="left", fill="x", expand=True)
        ttk.Button(input_frame, text="Send", style="Action.TButton", command=self.send_message).pack(side="left", padx=(8, 0))

        mem_frame = ttk.LabelFrame(left, text="Memory", padding=10)
        mem_frame.pack(fill="x", pady=(10, 0))
        ttk.Entry(mem_frame, textvariable=self.memory_query_var).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Button(mem_frame, text="Read", style="Action.TButton", command=self.read_memory).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(mem_frame, text="Store", style="Action.TButton", command=self.store_memory).grid(row=1, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(mem_frame, text="Delete", style="Action.TButton", command=self.delete_memory).grid(row=1, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(mem_frame, text="Promoted", style="Action.TButton", command=self.show_promoted).grid(row=1, column=3, sticky="ew")
        mem_frame.columnconfigure(0, weight=1)
        mem_frame.columnconfigure(1, weight=1)
        mem_frame.columnconfigure(2, weight=1)
        mem_frame.columnconfigure(3, weight=1)

        status_box = ttk.LabelFrame(right, text="Live Status", padding=10)
        status_box.pack(fill="both", expand=True)

        ttk.Label(status_box, textvariable=self.status_var, style="Status.TLabel", justify="left", wraplength=260).pack(anchor="w", fill="x")
        ttk.Label(status_box, textvariable=self.db_var, style="Status.TLabel", justify="left", wraplength=260).pack(anchor="w", fill="x", pady=(8, 0))
        ttk.Separator(status_box).pack(fill="x", pady=10)
        ttk.Label(status_box, text="Memory facts", style="Header.TLabel").pack(anchor="w")
        ttk.Label(status_box, textvariable=self.name_var, style="Status.TLabel", justify="left", wraplength=260).pack(anchor="w", fill="x", pady=(6, 0))
        ttk.Button(status_box, text="What is my name?", style="Action.TButton", command=self.ask_name).pack(anchor="w", pady=(10, 0))
        ttk.Button(status_box, text="Poll Now", style="Action.TButton", command=self.refresh_all).pack(anchor="w", pady=(8, 0))

    def _append_chat(self, who: str, text: str, tag: str) -> None:
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", f"{who}: {text}\n\n", tag)
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def _append_chat_async(self, who: str, text: str, tag: str) -> None:
        self.after(0, lambda: self._append_chat(who, text, tag))

    def _load_server_settings(self) -> None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return

        gui_cfg = cfg.get("gui", {}) if isinstance(cfg, dict) else {}
        server_cfg = gui_cfg.get("server", {}) if isinstance(gui_cfg, dict) else {}

        start_script = server_cfg.get("start_script") if isinstance(server_cfg, dict) else None
        stop_script = server_cfg.get("stop_script") if isinstance(server_cfg, dict) else None

        if isinstance(start_script, str) and start_script.strip():
            self.server_start_script_var.set(start_script)
        if isinstance(stop_script, str) and stop_script.strip():
            self.server_stop_script_var.set(stop_script)

    def save_server_settings(self) -> None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        if not isinstance(cfg, dict):
            cfg = {}

        gui_cfg = cfg.get("gui")
        if not isinstance(gui_cfg, dict):
            gui_cfg = {}

        server_cfg = gui_cfg.get("server")
        if not isinstance(server_cfg, dict):
            server_cfg = {}

        server_cfg["start_script"] = self.server_start_script_var.get().strip()
        server_cfg["stop_script"] = self.server_stop_script_var.get().strip()
        gui_cfg["server"] = server_cfg
        cfg["gui"] = gui_cfg

        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")
            self._append_chat("GUI", "Saved server script paths.", "meta")
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save server settings: {e}")

    def _pick_script_path(self, target_var: tk.StringVar) -> None:
        initial_dir = APP_DIR
        current = target_var.get().strip()
        if current:
            if os.path.isdir(current):
                initial_dir = current
            elif os.path.isfile(current):
                initial_dir = os.path.dirname(current)

        chosen = filedialog.askopenfilename(
            parent=self,
            title="Select PowerShell script",
            initialdir=initial_dir,
            filetypes=[("PowerShell scripts", "*.ps1"), ("All files", "*.*")],
        )
        if chosen:
            target_var.set(chosen)

    def _run_server_script(self, script_path: str, action_name: str) -> None:
        script = script_path.strip()
        if not script:
            messagebox.showwarning("Missing script", f"Set a script path for {action_name.lower()} first.")
            return
        if not os.path.isfile(script):
            messagebox.showerror("Script not found", f"{action_name} script not found:\n{script}")
            return

        def worker() -> None:
            cmd = [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    cwd=os.path.dirname(script) or APP_DIR,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
            except Exception as e:
                self._append_chat_async("Server", f"{action_name} failed: {e}", "meta")
                return

            output = (result.stdout or "").strip()
            err = (result.stderr or "").strip()

            if result.returncode == 0:
                msg = f"{action_name} complete."
                if output:
                    msg += f"\n{output}"
                self._append_chat_async("Server", msg, "meta")
                self.after(0, self.refresh_all)
            else:
                msg = f"{action_name} failed (code {result.returncode})."
                if output:
                    msg += f"\n{output}"
                if err:
                    msg += f"\n{err}"
                self._append_chat_async("Server", msg, "meta")

        threading.Thread(target=worker, daemon=True).start()

    def _voice_monitor_script_path(self) -> str:
        return DEFAULT_VOICE_MONITOR_SCRIPT

    def _voice_device_index(self) -> int:
        raw = self.voice_device_var.get().strip()
        try:
            return int(raw)
        except Exception:
            return -1

    def start_voice_input(self) -> None:
        script = self._voice_monitor_script_path()
        if not os.path.isfile(script):
            messagebox.showerror("Script not found", f"Voice monitor script not found:\n{script}")
            return

        api_url = self.api_base_var.get().strip() or API_BASE
        voice_hint = self.voice_hint_var.get().strip() or "en-US-AnaNeural"
        device_idx = self._voice_device_index()

        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
            "-ApiUrl",
            api_url,
            "-VoiceDevice",
            str(device_idx),
            "-VoiceHint",
            voice_hint,
        ]

        try:
            kwargs: Dict[str, Any] = {
                "cwd": APP_DIR,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

            subprocess.Popen(cmd, **kwargs)
            self._append_chat("Voice", "Voice input monitor launch requested.", "meta")
            self.after(500, self.refresh_all)
        except Exception as e:
            messagebox.showerror("Voice start failed", str(e))

    def stop_voice_input(self) -> None:
        pid = None
        try:
            if os.path.isfile(VOICE_MONITOR_PID_FILE):
                with open(VOICE_MONITOR_PID_FILE, "r", encoding="utf-8") as f:
                    raw = (f.read() or "").strip()
                    pid = int(raw) if raw else None
        except Exception:
            pid = None

        try:
            if pid:
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            if os.path.isfile(VOICE_MONITOR_PID_FILE):
                os.remove(VOICE_MONITOR_PID_FILE)
            self._append_chat("Voice", "Voice input monitor stop requested.", "meta")
            self.after(500, self.refresh_all)
        except Exception as e:
            messagebox.showerror("Voice stop failed", str(e))

    def start_voice_output(self) -> None:
        self.voice_output_enabled.set(True)
        self.voice_output_state_var.set("Voice output: ON")
        self._append_chat("Voice", "Voice output enabled for /process replies.", "meta")

    def stop_voice_output(self) -> None:
        self.voice_output_enabled.set(False)
        self.voice_output_state_var.set("Voice output: OFF")
        self._append_chat("Voice", "Voice output disabled for /process replies.", "meta")

    def start_server(self) -> None:
        self.save_server_settings()
        self._run_server_script(self.server_start_script_var.get(), "Start server")

    def stop_server(self) -> None:
        self.save_server_settings()
        self._run_server_script(self.server_stop_script_var.get(), "Stop server")

    def _endpoint(self, path: str) -> str:
        return self.api_base_var.get().rstrip("/") + path

    def _get_json(self, path: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(self._endpoint(path), timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self._append_chat("GUI", f"GET {path} failed: {e}", "meta")
            return None

    def _post_json(self, path: str, payload: Dict[str, Any], timeout: int = 30) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.post(self._endpoint(path), json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self._append_chat("GUI", f"POST {path} failed: {e}", "meta")
            return None

    def refresh_all(self) -> None:
        status = self._get_json("/status", timeout=5)
        if isinstance(status, dict):
            vm = status.get("voice_monitor") if isinstance(status.get("voice_monitor"), dict) else {}
            vm_running = bool(vm.get("running"))
            vm_pid = vm.get("pid")
            if vm_running:
                self.voice_input_state_var.set(f"Voice input: ON (pid {vm_pid})")
            else:
                self.voice_input_state_var.set("Voice input: OFF")

            self.status_var.set(
                "CORE STATUS\n"
                f"temp: {status.get('core_temp', 'UNKNOWN')}\n"
                f"memory bus: {status.get('memory_bus', 'UNKNOWN')}\n"
                f"neural cache: {status.get('neural_cache', 'UNKNOWN')}\n"
                f"io: {status.get('io_channels', 'UNKNOWN')}\n"
                f"voice input: {'ON' if vm_running else 'OFF'}\n"
                f"level: {status.get('level', 'WARN')}"
            )
        else:
            self.status_var.set("WAITING FOR CORE…")
            self.voice_input_state_var.set("Voice input: unknown")

        db = self._get_json("/db/status", timeout=5)
        if isinstance(db, dict):
            self.db_var.set(
                "DB STATUS\n"
                f"link: {db.get('db_link', 'UNKNOWN')}\n"
                f"sync: {db.get('db_sync', 'UNKNOWN')}\n"
                f"latency: {db.get('db_latency', 'UNKNOWN')}\n"
                f"cache: {db.get('cache_state', 'UNKNOWN')}\n"
                f"level: {db.get('level', 'WARN')}"
            )
        else:
            self.db_var.set("WAITING FOR DB…")

        promoted = self._get_json("/memory/promoted?limit=1", timeout=5)
        if isinstance(promoted, list) and promoted:
            first = promoted[0]
            self.name_var.set(f"Latest promoted memory: {first.get('text', '')}")
        else:
            self.name_var.set("No promoted memories loaded.")

    def _poll_status(self) -> None:
        self.refresh_all()
        self.after(POLL_MS, self._poll_status)

    def send_message(self) -> None:
        text = self.user_input_var.get().strip()
        if not text:
            return
        self._append_chat("You", text, "user")
        self.user_input_var.set("")

        def worker() -> None:
            payload = {
                "input": text,
                "speak_response": bool(self.voice_output_enabled.get()),
                "voice_hint": self.voice_hint_var.get().strip() or "en-US-AnaNeural",
                "input_source": "text",
            }
            result = self._post_json("/process", payload, timeout=180)
            if not result:
                self._append_chat("Mina", "(error contacting core)", "mina")
                return

            reply = ""
            if isinstance(result, dict):
                if isinstance(result.get("reply"), str):
                    reply = result.get("reply", "")
                elif isinstance(result.get("output"), dict):
                    reply = str(result.get("output", {}).get("reply", ""))
                elif isinstance(result.get("choices"), list):
                    try:
                        reply = result["choices"][0]["message"]["content"]
                    except Exception:
                        reply = ""

            reply = reply or "(no reply)"
            self.after(0, lambda: self._append_chat("Mina", reply, "mina"))

        threading.Thread(target=worker, daemon=True).start()

    def read_memory(self) -> None:
        query = self.memory_query_var.get().strip() or "what is my name"

        def worker() -> None:
            result = self._get_json(f"/memory/read?query={requests.utils.quote(query)}&top_k=5", timeout=20)
            if not result or not isinstance(result, dict):
                self.after(0, lambda: self._append_chat("Memory", "(memory read failed)", "meta"))
                return
            lines = [item.get("text", "") for item in result.get("results", []) if item.get("text")]
            if not lines:
                lines = ["No matching memories found."]
            self.after(0, lambda: self._append_chat("Memory", "\n".join(lines), "meta"))

        threading.Thread(target=worker, daemon=True).start()

    def store_memory(self) -> None:
        text = simpledialog.askstring("Store Memory", "Enter the memory text to store:", parent=self)
        if not text:
            return
        kind = simpledialog.askstring("Memory Kind", "Enter kind (fact / preference / procedure):", parent=self) or "fact"
        result = self._post_json("/memory/write", {"text": text, "kind": kind, "tags": ["user_memory"]}, timeout=30)
        if result and result.get("ok"):
            self._append_chat("Memory", f"Stored: {result.get('stored')}", "meta")
        else:
            self._append_chat("Memory", f"Store failed: {result}", "meta")

    def delete_memory(self) -> None:
        text = simpledialog.askstring("Delete Memory", "Enter exact memory text to delete:", parent=self)
        if not text:
            return
        result = self._post_json("/memory/delete", {"text": text, "include_tags": ["user_memory", "profile_auto"]}, timeout=30)
        if result and result.get("deleted", 0) > 0:
            self._append_chat("Memory", f"Deleted {result.get('deleted')} matching row(s).", "meta")
        else:
            self._append_chat("Memory", f"Nothing deleted: {result}", "meta")

    def show_promoted(self) -> None:
        result = self._get_json("/memory/promoted?limit=10", timeout=20)
        if not result:
            return
        if isinstance(result, list):
            lines = [item.get("text", "") for item in result if item.get("text")]
            self._append_chat("Promoted", "\n".join(lines) if lines else "No promoted memories.", "meta")
        else:
            self._append_chat("Promoted", json.dumps(result, indent=2), "meta")

    def ask_name(self) -> None:
        self.memory_query_var.set("what is my name")
        self.read_memory()


def main() -> int:
    try:
        app = MinaGUI()
        app.mainloop()
        return 0
    except Exception as e:
        messagebox.showerror("Mina GUI failed", str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
