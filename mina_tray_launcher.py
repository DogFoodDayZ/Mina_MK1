from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
	import pystray
	from PIL import Image, ImageDraw
except Exception as exc:  # pragma: no cover
	print("Missing dependencies for tray app: pystray, pillow")
	print("Install with: .\\.venv\\Scripts\\python.exe -m pip install pystray pillow")
	print(f"Details: {exc}")
	raise SystemExit(1)


ROOT = Path(__file__).resolve().parent
MK1_PS1 = ROOT / "mk1_api.ps1"
GUI_DIR = ROOT / "apps" / "mina-gui"
CONFIG_DIR = Path(os.getenv("APPDATA", str(ROOT))) / "Mina_MK1"
CONFIG_PATH = CONFIG_DIR / "tray_launcher_config.json"
VOICE_SPEAK_FLAG = ROOT / "config" / ".voice_speak.json"


def _write_speak_flag(enabled: bool) -> None:
	"""Write the shared speak flag the API reads, so the tray's GUI-speak toggle
	actually reaches the running API (env vars do not survive the detached child)."""
	try:
		VOICE_SPEAK_FLAG.parent.mkdir(parents=True, exist_ok=True)
		VOICE_SPEAK_FLAG.write_text(json.dumps({"speak": bool(enabled)}), encoding="utf-8")
	except Exception:
		pass


@dataclass
class LauncherConfig:
	load_fancy_gui: bool = True
	start_voice_interfaces: bool = False
	gui_speak: bool = False
	foreground: bool = False
	voice_device: int = -1
	voice_hint: str = "en-US-AnaNeural"
	api_port: int = 8000
	gui_port: int = 1420


class MinaTrayLauncher:
	def __init__(self) -> None:
		self.config = self._load_config()
		self._lock = threading.Lock()
		self._last_status = "idle"
		self.icon = pystray.Icon("mina_tray")
		self.icon.icon = self._build_icon()
		self.icon.title = "Mina Tray Launcher"
		self.icon.menu = self._build_menu()

	def _build_icon(self) -> Image.Image:
		image = Image.new("RGB", (64, 64), color=(9, 14, 18))
		draw = ImageDraw.Draw(image)
		draw.rectangle((3, 3, 60, 60), outline=(0, 255, 136), width=2)
		draw.rectangle((7, 7, 56, 56), outline=(28, 82, 92), width=1)
		draw.polygon([(12, 25), (17, 10), (28, 21), (36, 21), (47, 10), (52, 25), (48, 45), (16, 45)], fill=(24, 34, 39), outline=(0, 255, 136))
		draw.polygon([(17, 15), (20, 25), (25, 21)], fill=(255, 72, 151))
		draw.polygon([(47, 15), (44, 25), (39, 21)], fill=(255, 72, 151))
		draw.rectangle((19, 28, 27, 33), fill=(0, 255, 136))
		draw.rectangle((37, 28, 45, 33), fill=(0, 255, 136))
		draw.rectangle((22, 29, 24, 31), fill=(9, 14, 18))
		draw.rectangle((40, 29, 42, 31), fill=(9, 14, 18))
		draw.line((29, 38, 35, 38), fill=(255, 184, 77), width=2)
		draw.line((32, 35, 32, 41), fill=(255, 184, 77), width=1)
		return image

	def _load_config(self) -> LauncherConfig:
		try:
			if CONFIG_PATH.exists():
				raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
				if isinstance(raw, dict):
					return LauncherConfig(
						load_fancy_gui=bool(raw.get("load_fancy_gui", True)),
						start_voice_interfaces=bool(raw.get("start_voice_interfaces", False)),
						gui_speak=bool(raw.get("gui_speak", False)),
						foreground=bool(raw.get("foreground", False)),
						voice_device=int(raw.get("voice_device", -1)),
						voice_hint=str(raw.get("voice_hint", "en-US-AnaNeural")),
						api_port=int(raw.get("api_port", 8000)),
						gui_port=int(raw.get("gui_port", 1420)),
					)
		except Exception:
			pass
		return LauncherConfig()

	def _is_port_open(self, port: int, timeout: float = 0.8) -> bool:
		try:
			with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
				return True
		except Exception:
			return False

	def _verify_started(self, timeout_seconds: float = 35.0) -> bool:
		deadline = time.monotonic() + max(1.0, float(timeout_seconds))
		while time.monotonic() < deadline:
			if self._is_port_open(self.config.api_port):
				return True
			time.sleep(0.8)
		return False

	def _launch_external_gui(self) -> None:
		if not self.config.load_fancy_gui:
			return
		if not GUI_DIR.exists():
			raise FileNotFoundError(f"Missing GUI directory: {GUI_DIR}")
		if self._is_port_open(self.config.gui_port):
			return

		gui_path = str(GUI_DIR).replace("'", "''")
		command = f"Set-Location -LiteralPath '{gui_path}'; pnpm tauri dev"
		subprocess.Popen(
			[
				"powershell",
				"-NoProfile",
				"-ExecutionPolicy",
				"Bypass",
				"-NoExit",
				"-Command",
				command,
			],
			cwd=str(ROOT),
			creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
		)

	def _verify_stopped(self, timeout_seconds: float = 70.0) -> bool:
		deadline = time.monotonic() + max(1.0, float(timeout_seconds))
		while time.monotonic() < deadline:
			if not self._is_port_open(self.config.api_port):
				return True
			time.sleep(0.8)
		return False

	def _set_status(self, status: str, title: str) -> None:
		with self._lock:
			self._last_status = status
			self.icon.title = title

	def _save_config(self) -> None:
		CONFIG_DIR.mkdir(parents=True, exist_ok=True)
		CONFIG_PATH.write_text(
			json.dumps(asdict(self.config), indent=2, ensure_ascii=True) + "\n",
			encoding="utf-8",
		)

	def _toggle(self, field: str) -> None:
		with self._lock:
			current = bool(getattr(self.config, field))
			setattr(self.config, field, not current)
			self._save_config()
			self._refresh_menu()

	def _refresh_menu(self) -> None:
		self.icon.menu = self._build_menu()
		self.icon.update_menu()

	def _build_menu(self) -> pystray.Menu:
		return pystray.Menu(
			pystray.MenuItem("Launch Mina", self._on_launch),
			pystray.MenuItem("Stop Mina", self._on_stop),
			pystray.Menu.SEPARATOR,
			pystray.MenuItem(
				"Load Fancy GUI (default)",
				lambda *_: self._toggle("load_fancy_gui"),
				checked=lambda _: self.config.load_fancy_gui,
			),
			pystray.MenuItem(
				"Start Voice Interfaces",
				lambda *_: self._toggle("start_voice_interfaces"),
				checked=lambda _: self.config.start_voice_interfaces,
			),
			pystray.MenuItem(
				"Enable GUI Speak",
				lambda *_: self._toggle("gui_speak"),
				checked=lambda _: self.config.gui_speak,
			),
			pystray.MenuItem(
				"Run In Foreground",
				lambda *_: self._toggle("foreground"),
				checked=lambda _: self.config.foreground,
			),
			pystray.Menu.SEPARATOR,
			pystray.MenuItem("Exit", self._on_exit),
		)

	def _build_start_command(self) -> list[str]:
		if not MK1_PS1.exists():
			raise FileNotFoundError(f"Missing script: {MK1_PS1}")

		cmd = [
			"powershell",
			"-NoProfile",
			"-ExecutionPolicy",
			"Bypass",
			"-File",
			str(MK1_PS1),
			"start",
			"-Port",
			str(int(self.config.api_port)),
			"-VoiceDevice",
			str(int(self.config.voice_device)),
			"-VoiceHint",
			str(self.config.voice_hint or "en-US-AnaNeural"),
		]

		cmd.append("-NoGuiDev")
		if self.config.start_voice_interfaces:
			cmd.append("-AutoVoice")
		if self.config.gui_speak:
			cmd.append("-GuiSpeak")
		if self.config.foreground:
			cmd.append("-Foreground")

		return cmd

	def _run_detached(self, cmd: list[str]) -> None:
		subprocess.Popen(
			cmd,
			cwd=str(ROOT),
			creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
		)

	def _on_launch(self, *_args) -> None:
		try:
			# Write the shared speak flag before launch so the API boots with the
			# tray's GUI-speak toggle applied (env vars don't survive the detached child).
			_write_speak_flag(self.config.gui_speak)
			cmd = self._build_start_command()
			self._run_detached(cmd)
			if self.config.load_fancy_gui:
				self._launch_external_gui()
			self._set_status("launch requested", "Mina Tray Launcher (launch requested)")

			def _verify() -> None:
				ok = self._verify_started()
				if ok:
					self._set_status("launched", "Mina Tray Launcher (launched, verified)")
				else:
					self._set_status("launch verification failed", "Mina Tray Launcher (launch unverified)")

			threading.Thread(target=_verify, name="mina-launch-verify", daemon=True).start()
		except Exception as exc:
			self._set_status(f"launch error: {exc}", "Mina Tray Launcher (launch error)")

	def _stop_voice_monitor(self) -> None:
		"""Stop the voice monitor + its loop so a launcher stop also silences the mic."""
		try:
			pid_file = ROOT / ".mk1_voice_monitor.pid"
			ps = (
				"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ("
				"$_.CommandLine -like '*start_mina_voice_monitor.ps1*' -or "
				"$_.CommandLine -like '*mina_windows_voice_loop.py*'"
				") } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }"
			)
			subprocess.run(
				["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
				cwd=str(ROOT),
				creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
				timeout=15,
			)
			if pid_file.exists():
				try:
					pid_file.unlink()
				except Exception:
					pass
		except Exception:
			pass

	def _stop_gui_dev(self) -> None:
		"""Stop the Tauri/Vite GUI dev process so a launcher stop also closes the GUI."""
		try:
			ps = (
				"Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'mina_gui' } "
				"| ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }; "
				"$c = Get-NetTCPConnection -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue; "
				"if ($c) { $c | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {} } }"
			)
			subprocess.run(
				["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
				cwd=str(ROOT),
				creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
				timeout=15,
			)
		except Exception:
			pass

	def _on_stop(self, *_args) -> None:
		try:
			# Stop the voice monitor and the GUI dev process too, not just the API.
			# The API shutdown (mk1_api.ps1 shutdown) covers core+memory, but the
			# voice monitor/loop and the Tauri/Vite GUI are outside that barrier.
			self._stop_voice_monitor()
			self._stop_gui_dev()

			cmd = [
				"powershell",
				"-NoProfile",
				"-ExecutionPolicy",
				"Bypass",
				"-File",
				str(MK1_PS1),
				"shutdown",
				"-Port",
				str(int(self.config.api_port)),
			]
			self._run_detached(cmd)
			self._set_status("shutdown requested", "Mina Tray Launcher (safe shutdown requested)")

			def _verify() -> None:
				ok = self._verify_stopped()
				if ok:
					self._set_status("stopped", "Mina Tray Launcher (stopped, verified)")
				else:
					self._set_status("shutdown verification failed", "Mina Tray Launcher (shutdown unverified)")

			threading.Thread(target=_verify, name="mina-stop-verify", daemon=True).start()
		except Exception as exc:
			self._set_status(f"stop error: {exc}", "Mina Tray Launcher (stop error)")

	def _on_exit(self, *_args) -> None:
		self.icon.stop()

	def run(self) -> None:
		self._save_config()
		self.icon.run()


def main() -> int:
	app = MinaTrayLauncher()
	app.run()
	return 0


def _acquire_single_instance_mutex() -> bool:
	"""Ensure only one logical tray launcher owns the stack. A second launch
	(boot startup + manual, or a double-click) becomes a no-op instead of
	doubling the API, embed server, and voice monitor."""
	try:
		import ctypes
		mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "MinaMK1TrayLauncherSingleton")
		# ERROR_ALREADY_EXISTS = 183
		if ctypes.windll.kernel32.GetLastError() == 183:
			return False
		# Keep the handle alive for the process lifetime.
		_acquire_single_instance_mutex._handle = mutex
		return True
	except Exception:
		# If the mutex cannot be created, do not block startup.
		return True


if __name__ == "__main__":
	if not _acquire_single_instance_mutex():
		print("Mina Tray Launcher is already running; second instance ignored.")
		raise SystemExit(0)
	raise SystemExit(main())
