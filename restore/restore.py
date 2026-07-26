"""MK1 backup restore GUI.

Usage:
  e:/Mina_MK1/.venv/Scripts/python.exe restore/restore.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from restore.code_backup import (
        DEFAULT_BACKUP_DIR as CODE_BACKUP_DIR,
        create_code_snapshot,
        list_code_snapshots,
        restore_code_snapshot,
    )
except ModuleNotFoundError:
    from code_backup import (  # type: ignore[no-redef]
        DEFAULT_BACKUP_DIR as CODE_BACKUP_DIR,
        create_code_snapshot,
        list_code_snapshots,
        restore_code_snapshot,
    )


ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "memory" / "backups"
STOP_SCRIPT = ROOT / "stop_mk1_api.ps1"
START_SCRIPT = ROOT / "start_mk1_api.ps1"
WATCHDOG_SCRIPT = ROOT / "mina_watchdog.ps1"
WATCHDOG_DIR = Path(os.getenv("MK1_WATCHDOG_DIR", r"E:\workspace\mina_watchdog"))

LIVE_FILES = {
    "memory.db": ROOT / "memory" / "memory.db",
    "faiss_small.index": ROOT / "memory" / "faiss_small.index",
    "faiss_base.index": ROOT / "memory" / "faiss_base.index",
}


@dataclass
class BackupEntry:
    kind: str
    name: str
    path: Path
    timestamp: float
    created_at: str
    reason: str
    file_count: int = 0


class RestoreApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MINA MK1 // Recovery Console")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self.configure(bg="#000000")

        self.backups: list[BackupEntry] = []
        self.restart_after_restore_var = tk.BooleanVar(value=True)
        self.confirm_overwrite_var = tk.BooleanVar(value=False)
        self._restore_armed = False
        self._arm_timeout_id: str | None = None
        self.watchdog_status_var = tk.StringVar(value="Watchdog: checking...")
        self.watchdog_buttons: list[ttk.Button] = []

        self._build_styles()
        self._build_ui()
        self._refresh_backups()
        self._refresh_watchdog_status()

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        mono = ("Cascadia Mono", 10)
        mono_bold = ("Cascadia Mono", 10, "bold")
        style.configure(".", background="#000000", foreground="#e0e0e0", font=mono)
        style.configure("TFrame", background="#000000")
        style.configure("Panel.TFrame", background="#050505")
        style.configure("TLabel", background="#000000", foreground="#e0e0e0", font=mono)
        style.configure("Title.TLabel", background="#000000", foreground="#ffb000", font=("Cascadia Mono", 18, "bold"))
        style.configure("Subtitle.TLabel", background="#000000", foreground="#777777", font=("Cascadia Mono", 9))
        style.configure("Green.TLabel", background="#050505", foreground="#00ff88", font=mono_bold)

        style.configure(
            "Panel.TLabelframe",
            background="#050505",
            bordercolor="#262626",
            darkcolor="#262626",
            lightcolor="#262626",
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background="#050505",
            foreground="#ffb000",
            font=mono_bold,
        )
        style.configure(
            "Green.Panel.TLabelframe",
            background="#050505",
            bordercolor="#075c36",
            darkcolor="#075c36",
            lightcolor="#075c36",
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Green.Panel.TLabelframe.Label",
            background="#050505",
            foreground="#00ff88",
            font=mono_bold,
        )

        style.configure(
            "TButton",
            background="#090909",
            foreground="#ffb000",
            bordercolor="#7a5400",
            darkcolor="#090909",
            lightcolor="#090909",
            relief="solid",
            borderwidth=1,
            padding=(10, 6),
            font=mono_bold,
        )
        style.map(
            "TButton",
            background=[("active", "#201700"), ("disabled", "#111111")],
            foreground=[("active", "#ffd060"), ("disabled", "#555555")],
            bordercolor=[("active", "#ffb000"), ("disabled", "#282828")],
        )
        style.configure("Green.TButton", foreground="#00ff88", bordercolor="#087847")
        style.map(
            "Green.TButton",
            background=[("active", "#002b19"), ("disabled", "#111111")],
            foreground=[("active", "#74ffba"), ("disabled", "#555555")],
            bordercolor=[("active", "#00ff88"), ("disabled", "#282828")],
        )
        style.configure("Danger.TButton", foreground="#ff5b5b", bordercolor="#7a1111")
        style.map(
            "Danger.TButton",
            background=[("active", "#350707"), ("disabled", "#111111")],
            foreground=[("active", "#ffffff"), ("disabled", "#555555")],
            bordercolor=[("active", "#ff3030"), ("disabled", "#282828")],
        )

        style.configure(
            "TCheckbutton",
            background="#000000",
            foreground="#b8b8b8",
            indicatorbackground="#050505",
            indicatorforeground="#00ff88",
            bordercolor="#444444",
            focuscolor="#000000",
            font=("Cascadia Mono", 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", "#000000")],
            foreground=[("active", "#ffffff")],
            indicatorbackground=[("selected", "#00aa5c")],
        )

        style.configure(
            "Treeview",
            background="#050505",
            fieldbackground="#050505",
            foreground="#c9c9c9",
            bordercolor="#262626",
            rowheight=28,
            font=mono,
        )
        style.map("Treeview", background=[("selected", "#063c25")], foreground=[("selected", "#a8ffbf")])
        style.configure(
            "Treeview.Heading",
            background="#111111",
            foreground="#ffb000",
            bordercolor="#303030",
            relief="flat",
            padding=(8, 7),
            font=mono_bold,
        )
        style.map("Treeview.Heading", background=[("active", "#201700")], foreground=[("active", "#ffd060")])
        style.configure(
            "Vertical.TScrollbar",
            background="#151515",
            troughcolor="#050505",
            bordercolor="#262626",
            arrowcolor="#ffb000",
        )

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="MINA MK1 // RECOVERY CONSOLE", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="EXTERNAL FALLBACK  |  MEMORY + CODE  |  LOCAL CONTROL",
            style="Subtitle.TLabel",
        ).pack(side="right", anchor="s", pady=(9, 1))

        top = ttk.LabelFrame(root, text=" BACKUP INVENTORY ", padding=10, style="Panel.TLabelframe")
        top.pack(fill="both", expand=True)

        cols = ("kind", "name", "created", "reason", "files")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse", height=14)
        self.tree.heading("kind", text="Type")
        self.tree.heading("name", text="Backup Folder")
        self.tree.heading("created", text="Created")
        self.tree.heading("reason", text="Reason")
        self.tree.heading("files", text="Files")
        self.tree.column("kind", width=80, anchor="w")
        self.tree.column("name", width=240, anchor="w")
        self.tree.column("created", width=170, anchor="w")
        self.tree.column("reason", width=300, anchor="w")
        self.tree.column("files", width=60, anchor="e")

        yscroll = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(10, 0))

        command_row = ttk.Frame(controls)
        command_row.pack(fill="x")

        ttk.Button(command_row, text="Refresh", command=self._refresh_backups).pack(side="left")
        ttk.Button(command_row, text="Open Backup Folder", command=self._open_backup_folder).pack(side="left", padx=(8, 0))
        ttk.Button(command_row, text="Create Code Snapshot", command=self._create_code_snapshot).pack(side="left", padx=(8, 0))

        options_row = ttk.Frame(controls)
        options_row.pack(fill="x", pady=(8, 0))

        ttk.Checkbutton(
            options_row,
            text="Restart API after restore",
            variable=self.restart_after_restore_var,
        ).pack(side="left")

        ttk.Checkbutton(
            options_row,
            text="I understand restore will overwrite live Mina files",
            variable=self.confirm_overwrite_var,
            command=self._update_restore_button_state,
        ).pack(side="left", padx=(20, 0))

        self.arm_button = tk.Button(
            command_row,
            text="ARM RESTORE",
            command=self._arm_restore,
            bg="#201700",
            fg="#ffb000",
            activebackground="#3a2800",
            activeforeground="#ffd060",
            disabledforeground="#555555",
            highlightbackground="#7a5400",
            highlightcolor="#ffb000",
            relief="flat",
            bd=1,
            padx=12,
            pady=6,
            font=("Cascadia Mono", 10, "bold"),
        )
        self.arm_button.pack(side="right")

        self.restore_button = tk.Button(
            command_row,
            text="RESTORE SELECTED BACKUP",
            command=self._restore_selected,
            bg="#2b0505",
            fg="#ff5b5b",
            activebackground="#510909",
            activeforeground="#ffffff",
            disabledforeground="#555555",
            highlightbackground="#7a1111",
            highlightcolor="#ff3030",
            relief="flat",
            bd=1,
            padx=12,
            pady=6,
            font=("Cascadia Mono", 10, "bold"),
            state="disabled",
        )
        self.restore_button.pack(side="right", padx=(0, 8))

        watchdog_frame = ttk.LabelFrame(
            root,
            text=" AUTONOMOUS UPDATE WATCHDOG ",
            padding=10,
            style="Green.Panel.TLabelframe",
        )
        watchdog_frame.pack(fill="x", pady=(10, 0))

        ttk.Label(
            watchdog_frame,
            textvariable=self.watchdog_status_var,
            justify="left",
            style="Green.TLabel",
        ).pack(side="left", fill="x", expand=True)

        for text, action in (
            ("Start", "start"),
            ("Stop", "stop"),
            ("Refresh Status", "status"),
        ):
            button = ttk.Button(
                watchdog_frame,
                text=text,
                style=("Danger.TButton" if action == "stop" else "Green.TButton"),
                command=lambda selected=action: self._run_watchdog_action(selected),
            )
            button.pack(side="left", padx=(8, 0))
            self.watchdog_buttons.append(button)

        open_logs = ttk.Button(
            watchdog_frame,
            text="Open Logs",
            style="Green.TButton",
            command=self._open_watchdog_logs,
        )
        open_logs.pack(side="left", padx=(8, 0))
        self.watchdog_buttons.append(open_logs)

        log_frame = ttk.LabelFrame(root, text=" EVENT LOG ", padding=10, style="Panel.TLabelframe")
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(
            log_frame,
            height=9,
            wrap="word",
            bg="#000000",
            fg="#a8ffbf",
            insertbackground="#00ff88",
            selectbackground="#063c25",
            selectforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=8,
            font=("Cascadia Mono", 10),
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_restore_button_state())
        self._update_restore_button_state()

    def _log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _read_manifest(self, backup_path: Path) -> dict:
        manifest = backup_path / "manifest.json"
        if not manifest.exists():
            return {}
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _refresh_backups(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.backups.clear()

        if not BACKUP_DIR.exists():
            self._log(f"Backup directory missing: {BACKUP_DIR}")
            return

        for child in sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not child.is_dir() or not child.name.startswith("backup_"):
                continue

            manifest = self._read_manifest(child)
            created_at = str(manifest.get("created_at") or "(unknown)")
            reason = str(manifest.get("reason") or "(unknown)")
            ts = float(child.stat().st_mtime)

            entry = BackupEntry(
                kind="memory",
                name=child.name,
                path=child,
                timestamp=ts,
                created_at=created_at,
                reason=reason,
            )
            self.backups.append(entry)

        for row in list_code_snapshots(CODE_BACKUP_DIR):
            self.backups.append(BackupEntry(
                kind="code",
                name=str(row.get("name") or ""),
                path=Path(str(row.get("path") or "")),
                timestamp=float(row.get("timestamp") or 0.0),
                created_at=str(row.get("created_at") or "(unknown)"),
                reason=str(row.get("reason") or "(unknown)"),
                file_count=int(row.get("file_count") or 0),
            ))

        self.backups.sort(key=lambda entry: entry.timestamp, reverse=True)
        for entry in self.backups:
            self.tree.insert(
                "",
                "end",
                values=(entry.kind.upper(), entry.name, entry.created_at, entry.reason, entry.file_count or ""),
            )

        if self.backups:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)

        self._log(f"Loaded {len(self.backups)} backup entries.")
        self._update_restore_button_state()

    def _open_backup_folder(self) -> None:
        entry = self._selected_backup()
        target = entry.path.parent if entry is not None else BACKUP_DIR
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))  # type: ignore[attr-defined]

    def _create_code_snapshot(self) -> None:
        self._log("Creating code snapshot...")
        result = create_code_snapshot(reason="manual_restore_gui")
        if not result.get("ok"):
            error = str(result.get("error") or "unknown error")
            self._log(f"Code snapshot failed: {error}")
            messagebox.showerror("Code snapshot failed", error)
            return
        self._log(f"Code snapshot created: {result.get('path')}")
        self._refresh_backups()

    def _selected_backup(self) -> BackupEntry | None:
        sel = self.tree.selection()
        if not sel:
            return None

        vals = self.tree.item(sel[0], "values")
        if not vals:
            return None

        kind = str(vals[0]).lower()
        name = str(vals[1])
        for b in self.backups:
            if b.kind == kind and b.name == name:
                return b
        return None

    def _arm_restore(self) -> None:
        if self._arm_timeout_id is not None:
            try:
                self.after_cancel(self._arm_timeout_id)
            except Exception:
                pass
            self._arm_timeout_id = None

        self._restore_armed = True
        self.arm_button.configure(
            text="ARMED (10s)",
            bg="#00351f",
            fg="#00ff88",
            activebackground="#005c36",
            activeforeground="#74ffba",
            highlightbackground="#087847",
        )
        self._log("Restore armed for 10 seconds.")
        self._update_restore_button_state()

        self._arm_timeout_id = self.after(10_000, self._disarm_restore)

    def _disarm_restore(self) -> None:
        self._restore_armed = False
        self.arm_button.configure(
            text="ARM RESTORE",
            bg="#201700",
            fg="#ffb000",
            activebackground="#3a2800",
            activeforeground="#ffd060",
            highlightbackground="#7a5400",
        )
        self._arm_timeout_id = None
        self._update_restore_button_state()

    def _update_restore_button_state(self) -> None:
        can_restore = bool(
            self._restore_armed
            and self.confirm_overwrite_var.get()
            and self._selected_backup() is not None
        )
        self.restore_button.configure(state=("normal" if can_restore else "disabled"))

    def _run_ps1(self, script_path: Path, *arguments: str) -> tuple[int, str]:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            *arguments,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        text = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        merged = text if not err else (text + "\n" + err).strip()
        return proc.returncode, merged

    def _set_watchdog_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.watchdog_buttons:
            button.configure(state=state)

    def _watchdog_status_summary(self, output: str) -> str:
        process = "UNKNOWN"
        startup = "UNKNOWN"
        for line in output.splitlines():
            if line.startswith("Mina watchdog process:"):
                process = line.split(":", 1)[1].strip()
            elif line.startswith("Windows logon startup:"):
                startup = line.split(":", 1)[1].strip().split(" (", 1)[0]

        state = self._read_json_file(WATCHDOG_DIR / "state.json")
        candidate = self._read_json_file(WATCHDOG_DIR / "candidate.json")
        healthy = state.get("healthy")
        health_text = "HEALTHY" if healthy is True else "UNHEALTHY" if healthy is False else "UNKNOWN"
        failures = int(state.get("consecutive_failures") or 0)
        phase = str(candidate.get("phase") or "none")
        return (
            f"Watchdog: {process} | Health: {health_text} | Failures: {failures}\n"
            f"Logon: {startup} | Candidate: {phase}"
        )

    @staticmethod
    def _read_json_file(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _run_watchdog_action(self, action: str) -> None:
        if action not in {"start", "stop", "status"}:
            return
        if not WATCHDOG_SCRIPT.is_file():
            messagebox.showerror("Watchdog unavailable", f"Missing controller: {WATCHDOG_SCRIPT}")
            return

        self._set_watchdog_buttons_enabled(False)
        self.watchdog_status_var.set(f"Watchdog: running {action}...")

        def worker() -> None:
            try:
                code, output = self._run_ps1(WATCHDOG_SCRIPT, action)
            except Exception as exc:
                code, output = 1, f"{type(exc).__name__}: {exc}"
            self.after(0, lambda: self._finish_watchdog_action(action, code, output))

        threading.Thread(target=worker, name=f"restore-watchdog-{action}", daemon=True).start()

    def _finish_watchdog_action(self, action: str, code: int, output: str) -> None:
        self._set_watchdog_buttons_enabled(True)
        self.watchdog_status_var.set(self._watchdog_status_summary(output))
        if code == 0:
            if action != "status":
                first_line = next((line for line in output.splitlines() if line.strip()), "completed")
                self._log(f"Watchdog {action}: {first_line}")
            return

        error = output.strip() or f"watchdog controller exited with code {code}"
        self._log(f"Watchdog {action} failed: {error}")
        messagebox.showerror("Watchdog command failed", error)

    def _refresh_watchdog_status(self) -> None:
        self._run_watchdog_action("status")

    def _open_watchdog_logs(self) -> None:
        WATCHDOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(WATCHDOG_DIR))  # type: ignore[attr-defined]

    def _create_safety_snapshot(self) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dst = BACKUP_DIR / f"backup_{ts}"
        dst.mkdir(parents=True, exist_ok=True)

        copied = []
        for name, live in LIVE_FILES.items():
            if live.exists():
                shutil.copy2(live, dst / name)
                copied.append(str(live))

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": "pre_restore_safety_snapshot",
            "sources": copied,
        }
        (dst / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return dst

    def _restore_selected(self) -> None:
        if not self._restore_armed:
            messagebox.showwarning("Not armed", "Click ARM RESTORE first.")
            return
        if not self.confirm_overwrite_var.get():
            messagebox.showwarning(
                "Confirmation required",
                "Enable the overwrite acknowledgement toggle before restoring.",
            )
            return

        entry = self._selected_backup()
        if entry is None:
            messagebox.showwarning("No selection", "Select a backup first.")
            return

        missing = [name for name in LIVE_FILES if not (entry.path / name).exists()] if entry.kind == "memory" else []
        if missing:
            messagebox.showerror(
                "Invalid backup",
                "Selected backup is missing required files:\n" + "\n".join(missing),
            )
            return

        restore_description = (
            "This will replace:\n"
            "- memory/memory.db\n"
            "- memory/faiss_small.index\n"
            "- memory/faiss_base.index\n\n"
            "A safety snapshot will be created first."
            if entry.kind == "memory"
            else (
                "This will overlay the backed-up source/configuration files onto Mina.\n"
                "Files created after the snapshot will not be deleted.\n\n"
                "A full code safety snapshot will be created first."
            )
        )
        confirm = messagebox.askyesno(
            "Confirm restore",
            f"Restore {entry.kind} backup {entry.name}?\n\n{restore_description}",
        )
        if not confirm:
            return

        try:
            self._log("Stopping API...")
            code, output = self._run_ps1(STOP_SCRIPT)
            if output:
                self._log(output)
            if code != 0:
                raise RuntimeError(f"stop_mk1_api.ps1 failed with code {code}")

            if entry.kind == "memory":
                self._log("Creating safety snapshot of current memory files...")
                snap = self._create_safety_snapshot()
                self._log(f"Safety snapshot created: {snap.name}")

                self._log(f"Restoring from backup: {entry.name}")
                for name, live in LIVE_FILES.items():
                    src = entry.path / name
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, live)
                    self._log(f"Restored {name}")
            else:
                self._log(f"Restoring code snapshot: {entry.name}")
                restored = restore_code_snapshot(entry.path, create_safety_snapshot=True)
                if not restored.get("ok"):
                    raise RuntimeError(str(restored.get("error") or "code restore failed"))
                self._log(f"Code safety snapshot: {restored.get('safety_snapshot')}")
                self._log(f"Restored {len(restored.get('restored_files') or [])} code files")

            if self.restart_after_restore_var.get():
                self._log("Starting API...")
                code, output = self._run_ps1(START_SCRIPT)
                if output:
                    self._log(output)
                if code != 0:
                    raise RuntimeError(f"start_mk1_api.ps1 failed with code {code}")

            self._log("Restore completed successfully.")
            messagebox.showinfo("Restore complete", f"Backup {entry.name} restored successfully.")
            self._disarm_restore()
            self.confirm_overwrite_var.set(False)
            self._refresh_backups()
        except Exception as e:
            self._log(f"Restore failed: {e}")
            messagebox.showerror("Restore failed", str(e))
            self._disarm_restore()


def main() -> int:
    app = RestoreApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
