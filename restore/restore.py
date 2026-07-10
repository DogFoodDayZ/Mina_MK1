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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "memory" / "backups"
STOP_SCRIPT = ROOT / "stop_mk1_api.ps1"
START_SCRIPT = ROOT / "start_mk1_api.ps1"

LIVE_FILES = {
    "memory.db": ROOT / "memory" / "memory.db",
    "faiss_small.index": ROOT / "memory" / "faiss_small.index",
    "faiss_base.index": ROOT / "memory" / "faiss_base.index",
}


@dataclass
class BackupEntry:
    name: str
    path: Path
    timestamp: float
    created_at: str
    reason: str


class RestoreApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MK1 Restore Utility")
        self.geometry("980x640")
        self.minsize(860, 560)

        self.backups: list[BackupEntry] = []
        self.restart_after_restore_var = tk.BooleanVar(value=True)
        self.confirm_overwrite_var = tk.BooleanVar(value=False)
        self._restore_armed = False
        self._arm_timeout_id: str | None = None

        self._build_ui()
        self._refresh_backups()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        top = ttk.LabelFrame(root, text="Backups", padding=10)
        top.pack(fill="both", expand=True)

        cols = ("name", "created", "reason")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse", height=14)
        self.tree.heading("name", text="Backup Folder")
        self.tree.heading("created", text="Created")
        self.tree.heading("reason", text="Reason")
        self.tree.column("name", width=260, anchor="w")
        self.tree.column("created", width=180, anchor="w")
        self.tree.column("reason", width=380, anchor="w")

        yscroll = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(10, 0))

        ttk.Button(controls, text="Refresh", command=self._refresh_backups).pack(side="left")
        ttk.Button(controls, text="Open Backup Folder", command=self._open_backup_folder).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            controls,
            text="Restart API after restore",
            variable=self.restart_after_restore_var,
        ).pack(side="left", padx=(18, 0))

        ttk.Checkbutton(
            controls,
            text="I understand restore will overwrite live memory files",
            variable=self.confirm_overwrite_var,
            command=self._update_restore_button_state,
        ).pack(side="left", padx=(18, 0))

        self.arm_button = tk.Button(
            controls,
            text="ARM RESTORE",
            command=self._arm_restore,
            bg="#6f5200",
            fg="#ffffff",
            activebackground="#8a6700",
            activeforeground="#ffffff",
            relief="raised",
            bd=2,
            padx=10,
            pady=4,
        )
        self.arm_button.pack(side="right")

        self.restore_button = tk.Button(
            controls,
            text="RESTORE SELECTED BACKUP",
            command=self._restore_selected,
            bg="#8f0000",
            fg="#ffffff",
            activebackground="#b00000",
            activeforeground="#ffffff",
            relief="raised",
            bd=2,
            padx=12,
            pady=4,
            state="disabled",
        )
        self.restore_button.pack(side="right", padx=(0, 8))

        log_frame = ttk.LabelFrame(root, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=10, wrap="word")
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
                name=child.name,
                path=child,
                timestamp=ts,
                created_at=created_at,
                reason=reason,
            )
            self.backups.append(entry)
            self.tree.insert("", "end", values=(entry.name, entry.created_at, entry.reason))

        if self.backups:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)

        self._log(f"Loaded {len(self.backups)} backup entries.")
        self._update_restore_button_state()

    def _open_backup_folder(self) -> None:
        if not BACKUP_DIR.exists():
            messagebox.showerror("Missing folder", f"Backup folder not found:\n{BACKUP_DIR}")
            return
        os.startfile(str(BACKUP_DIR))  # type: ignore[attr-defined]

    def _selected_backup(self) -> BackupEntry | None:
        sel = self.tree.selection()
        if not sel:
            return None

        vals = self.tree.item(sel[0], "values")
        if not vals:
            return None

        name = str(vals[0])
        for b in self.backups:
            if b.name == name:
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
        self.arm_button.configure(text="ARMED (10s)", bg="#1f6f00", activebackground="#2a9100")
        self._log("Restore armed for 10 seconds.")
        self._update_restore_button_state()

        self._arm_timeout_id = self.after(10_000, self._disarm_restore)

    def _disarm_restore(self) -> None:
        self._restore_armed = False
        self.arm_button.configure(text="ARM RESTORE", bg="#6f5200", activebackground="#8a6700")
        self._arm_timeout_id = None
        self._update_restore_button_state()

    def _update_restore_button_state(self) -> None:
        can_restore = bool(
            self._restore_armed
            and self.confirm_overwrite_var.get()
            and self._selected_backup() is not None
        )
        self.restore_button.configure(state=("normal" if can_restore else "disabled"))

    def _run_ps1(self, script_path: Path) -> tuple[int, str]:
        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
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

        missing = [name for name in LIVE_FILES if not (entry.path / name).exists()]
        if missing:
            messagebox.showerror(
                "Invalid backup",
                "Selected backup is missing required files:\n" + "\n".join(missing),
            )
            return

        confirm = messagebox.askyesno(
            "Confirm restore",
            (
                f"Restore backup {entry.name}?\n\n"
                "This will replace:\n"
                "- memory/memory.db\n"
                "- memory/faiss_small.index\n"
                "- memory/faiss_base.index\n\n"
                "A safety snapshot will be created first."
            ),
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

            self._log("Creating safety snapshot of current memory files...")
            snap = self._create_safety_snapshot()
            self._log(f"Safety snapshot created: {snap.name}")

            self._log(f"Restoring from backup: {entry.name}")
            for name, live in LIVE_FILES.items():
                src = entry.path / name
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, live)
                self._log(f"Restored {name}")

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
