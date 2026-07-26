from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_CANDIDATE_PHASES = {
    "applying",
    "pending_restart",
    "restarting",
    "validating",
    "rolling_back",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def path_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path.resolve()), str(root.resolve())]) == str(root.resolve())
    except ValueError:
        return False


class MinaWatchdog:
    def __init__(
        self,
        project_root: Path,
        watchdog_dir: Path,
        backup_dir: Path,
        port: int = 8000,
        embed_port: int = 8084,
        failure_threshold: int = 3,
        restart_attempts: int = 2,
        startup_grace_seconds: int = 90,
        apply_grace_seconds: int = 300,
    ) -> None:
        self.project_root = project_root.resolve()
        self.watchdog_dir = watchdog_dir.resolve()
        self.backup_dir = backup_dir.resolve()
        self.port = int(port)
        self.embed_port = int(embed_port)
        self.failure_threshold = max(1, int(failure_threshold))
        self.max_restart_attempts = max(1, int(restart_attempts))
        self.startup_grace_seconds = max(5, int(startup_grace_seconds))
        self.apply_grace_seconds = max(30, int(apply_grace_seconds))
        self.candidate_path = self.watchdog_dir / "candidate.json"
        self.state_path = self.watchdog_dir / "state.json"
        self.log_path = self.watchdog_dir / "watchdog.jsonl"
        self.start_script = self.project_root / "start_mk1_api.ps1"

    def log(self, event: str, **details: Any) -> None:
        self.watchdog_dir.mkdir(parents=True, exist_ok=True)
        row = {"ts": utc_now(), "event": event, **details}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")

    def load_state(self) -> dict[str, Any]:
        state = read_json(self.state_path, {})
        return state if isinstance(state, dict) else {}

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        write_json_atomic(self.state_path, state)

    def load_candidate(self) -> dict[str, Any] | None:
        candidate = read_json(self.candidate_path)
        return candidate if isinstance(candidate, dict) else None

    def save_candidate(self, candidate: dict[str, Any], phase: str, **details: Any) -> None:
        candidate.update(details)
        candidate["phase"] = phase
        candidate["updated_at"] = utc_now()
        write_json_atomic(self.candidate_path, candidate)
        self.log("candidate_phase", candidate_id=candidate.get("candidate_id"), phase=phase, **details)

    @staticmethod
    def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("health_payload_not_object")
        return value

    def check_health(self) -> tuple[bool, dict[str, Any]]:
        checks: dict[str, Any] = {}
        urls = {
            "embed": f"http://127.0.0.1:{self.embed_port}/health",
            "api": f"http://127.0.0.1:{self.port}/status?force_refresh=true",
            "db": f"http://127.0.0.1:{self.port}/db/status?force_refresh=true",
            "curiosity": f"http://127.0.0.1:{self.port}/curiosity/status",
        }
        try:
            for name, url in urls.items():
                checks[name] = self._get_json(url)
            if checks["embed"].get("ok") is not True:
                raise RuntimeError("embed_reported_unhealthy")
            if checks["db"].get("ok") is False or checks["db"].get("db_ok") is False:
                raise RuntimeError("database_reported_unhealthy")
            if checks["curiosity"].get("error") or checks["curiosity"].get("last_error"):
                raise RuntimeError("curiosity_reported_error")
            return True, checks
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            checks["error"] = f"{type(exc).__name__}: {exc}"
            return False, checks

    def wait_for_health(self) -> tuple[bool, dict[str, Any]]:
        deadline = time.monotonic() + self.startup_grace_seconds
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            healthy, latest = self.check_health()
            if healthy:
                return True, latest
            time.sleep(2)
        return False, latest

    def stop_api(self) -> None:
        command = (
            f"$pids=@(Get-NetTCPConnection -LocalPort {self.port} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); "
            "foreach($processId in $pids){Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue}; "
            f"Remove-Item -LiteralPath '{str(self.project_root / '.mk1_api.pid').replace("'", "''")}' "
            "-Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def start_api(self) -> tuple[bool, str]:
        if not self.start_script.is_file():
            return False, "start_script_missing"
        process = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.start_script),
                "-Port",
                str(self.port),
                "-EmbedPort",
                str(self.embed_port),
            ],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        output = (process.stdout + process.stderr).strip()[-4000:]
        return process.returncode == 0, output

    def restart_and_validate(self) -> tuple[bool, dict[str, Any]]:
        self.stop_api()
        started, output = self.start_api()
        if not started:
            return False, {"start_output": output, "error": "start_failed"}
        healthy, checks = self.wait_for_health()
        checks["start_output"] = output
        return healthy, checks

    def restore_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        snapshot = Path(str(candidate.get("snapshot") or "")).resolve()
        if not path_within(snapshot, self.backup_dir) or not snapshot.is_file():
            raise RuntimeError("candidate_snapshot_not_in_backup_directory")
        if Path(str(candidate.get("project_root") or "")).resolve() != self.project_root:
            raise RuntimeError("candidate_project_root_mismatch")

        touched = [str(item).replace("\\", "/") for item in candidate.get("files", [])]
        created = {str(item).replace("\\", "/") for item in candidate.get("created_files", [])}
        if not touched or not created.issubset(set(touched)):
            raise RuntimeError("candidate_file_inventory_invalid")

        restored: list[str] = []
        removed: list[str] = []
        with zipfile.ZipFile(snapshot, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            entries = {
                str(item.get("path") or ""): item
                for item in manifest.get("files", [])
                if isinstance(item, dict) and item.get("path")
            }
            for relative in touched:
                target = (self.project_root / relative).resolve()
                if not path_within(target, self.project_root):
                    raise RuntimeError(f"unsafe_candidate_path:{relative}")
                if relative in created:
                    if target.is_file():
                        target.unlink()
                    removed.append(relative)
                    continue
                metadata = entries.get(relative)
                if not isinstance(metadata, dict):
                    raise RuntimeError(f"snapshot_file_missing:{relative}")
                payload = archive.read(f"files/{relative}")
                digest = hashlib.sha256(payload).hexdigest()
                if digest != str(metadata.get("sha256") or ""):
                    raise RuntimeError(f"snapshot_checksum_mismatch:{relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.watchdog-restore.tmp")
                temporary.write_bytes(payload)
                os.replace(temporary, target)
                restored.append(relative)
        return {"restored_files": restored, "removed_files": removed}

    def rollback(self, candidate: dict[str, Any], failure: dict[str, Any]) -> bool:
        self.save_candidate(candidate, "rolling_back", failed_health=failure)
        self.stop_api()
        try:
            recovery = self.restore_candidate(candidate)
        except Exception as exc:
            self.save_candidate(candidate, "manual_intervention", error=f"rollback_failed:{exc}")
            return False
        self.save_candidate(candidate, "rollback_restored", recovery=recovery)
        healthy, checks = self.restart_and_validate()
        if healthy:
            self.save_candidate(candidate, "rolled_back", recovery=recovery, rollback_health=checks)
            self.save_state({"healthy": True, "consecutive_failures": 0, "restart_attempts": 0})
            return True
        self.save_candidate(candidate, "manual_intervention", recovery=recovery, rollback_health=checks)
        self.save_state({"healthy": False, "manual_intervention": True, "last_error": "rollback_restart_failed"})
        return False

    def process_candidate(self, candidate: dict[str, Any]) -> bool | None:
        phase = str(candidate.get("phase") or "")
        if phase == "applying":
            updated = str(candidate.get("updated_at") or candidate.get("created_at") or "")
            try:
                age = time.time() - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                age = self.apply_grace_seconds + 1
            if age <= self.apply_grace_seconds:
                return None
            return self.rollback(candidate, {"error": "stale_applying_candidate"})
        if phase not in {"pending_restart", "restarting", "validating", "rolling_back"}:
            return None
        if phase != "pending_restart":
            return self.rollback(candidate, {"error": f"watchdog_resumed_during_{phase}"})
        self.save_candidate(candidate, "restarting")
        healthy, checks = self.restart_and_validate()
        if healthy:
            self.save_candidate(candidate, "promoted", promotion_health=checks)
            self.save_state({"healthy": True, "consecutive_failures": 0, "restart_attempts": 0})
            return True
        return self.rollback(candidate, checks)

    def run_once(self) -> bool:
        candidate = self.load_candidate()
        if candidate and str(candidate.get("phase") or "") in ACTIVE_CANDIDATE_PHASES:
            result = self.process_candidate(candidate)
            if result is not None:
                return result

        healthy, checks = self.check_health()
        state = self.load_state()
        if healthy:
            if state.get("consecutive_failures") or state.get("restart_attempts") or not state.get("healthy"):
                self.log("health_restored")
            self.save_state({"healthy": True, "consecutive_failures": 0, "restart_attempts": 0})
            return True

        failures = int(state.get("consecutive_failures") or 0) + 1
        attempts = int(state.get("restart_attempts") or 0)
        state.update({"healthy": False, "consecutive_failures": failures, "last_health": checks})
        self.save_state(state)
        self.log("health_failed", consecutive_failures=failures, checks=checks)
        if failures < self.failure_threshold or state.get("manual_intervention"):
            return False
        if attempts >= self.max_restart_attempts:
            state["manual_intervention"] = True
            state["last_error"] = "restart_attempts_exhausted"
            self.save_state(state)
            self.log("manual_intervention", reason="restart_attempts_exhausted")
            return False

        healthy, restart_checks = self.restart_and_validate()
        attempts += 1
        if healthy:
            self.save_state({"healthy": True, "consecutive_failures": 0, "restart_attempts": 0})
            self.log("restart_recovered", attempt=attempts)
            return True
        state.update({"restart_attempts": attempts, "last_health": restart_checks})
        self.save_state(state)
        self.log("restart_failed", attempt=attempts, checks=restart_checks)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent Mina health and rollback watchdog")
    parser.add_argument("action", choices=["once", "monitor", "status", "reset"])
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--watchdog-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--embed-port", type=int, default=8084)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--restart-attempts", type=int, default=2)
    parser.add_argument("--startup-grace", type=int, default=90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    watchdog = MinaWatchdog(
        project_root=args.project_root,
        watchdog_dir=args.watchdog_dir,
        backup_dir=args.backup_dir,
        port=args.port,
        embed_port=args.embed_port,
        failure_threshold=args.failure_threshold,
        restart_attempts=args.restart_attempts,
        startup_grace_seconds=args.startup_grace,
    )
    if args.action == "status":
        print(json.dumps({
            "state": watchdog.load_state(),
            "candidate": watchdog.load_candidate(),
            "state_path": str(watchdog.state_path),
            "log_path": str(watchdog.log_path),
        }, indent=2, sort_keys=True))
        return 0
    if args.action == "reset":
        watchdog.save_state({"healthy": False, "consecutive_failures": 0, "restart_attempts": 0})
        return 0
    if args.action == "once":
        return 0 if watchdog.run_once() else 1
    watchdog.log("watchdog_started", pid=os.getpid())
    while True:
        try:
            watchdog.run_once()
        except Exception as exc:
            watchdog.log("watchdog_exception", error=f"{type(exc).__name__}: {exc}")
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())