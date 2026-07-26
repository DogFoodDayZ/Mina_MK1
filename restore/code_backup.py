from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = Path(os.getenv("MK1_CODE_BACKUP_DIR", r"E:\workspace\mina_code_backups"))
MAX_FILE_BYTES = 5 * 1024 * 1024

EXCLUDED_DIR_NAMES = {
    ".git",
    ".gradle",
    ".idea",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backups",
    "build",
    "cleanup_archive",
    "logs",
    "models",
    "node_modules",
    "tmp",
}

EXCLUDED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "google-services.json",
    "memory.db",
    "faiss_base.index",
    "faiss_small.index",
}

INCLUDED_SUFFIXES = {
    ".bat",
    ".cmd",
    ".css",
    ".gradle",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".properties",
    ".ps1",
    ".py",
    ".reg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

INCLUDED_EXTENSIONLESS_NAMES = {
    "gradlew",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_secret_like(path: Path) -> bool:
    name = path.name.lower()
    if name in EXCLUDED_FILE_NAMES or name.startswith(".env."):
        return True
    return path.suffix.lower() in {".jks", ".key", ".kdbx", ".p12", ".pem", ".pfx"}


def iter_code_files(project_root: Path = PROJECT_ROOT) -> Iterable[Path]:
    root = project_root.resolve()
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name.lower() not in EXCLUDED_DIR_NAMES]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            if _is_secret_like(path):
                continue
            if path.suffix.lower() not in INCLUDED_SUFFIXES and name.lower() not in INCLUDED_EXTENSIONLESS_NAMES:
                continue
            try:
                if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def create_code_snapshot(
    reason: str,
    project_root: Path = PROJECT_ROOT,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Dict[str, Any]:
    root = project_root.resolve()
    destination = backup_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = destination / f"mina_code_{stamp}.zip"
    temp_path = archive_path.with_suffix(".tmp")
    files = sorted(iter_code_files(root), key=lambda path: path.relative_to(root).as_posix().lower())
    manifest_files: List[Dict[str, Any]] = []

    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                manifest_files.append({
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                })
                archive.write(path, f"files/{relative}")

            manifest = {
                "schema": 1,
                "created_at": _utc_now().isoformat(timespec="seconds"),
                "reason": str(reason or "manual_code_snapshot").strip(),
                "project_root": str(root),
                "file_count": len(manifest_files),
                "files": manifest_files,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        os.replace(temp_path, archive_path)
        return {"ok": True, "path": str(archive_path), "manifest": manifest, "error": None}
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        return {"ok": False, "path": None, "manifest": None, "error": f"snapshot_failed: {exc}"}


def read_code_snapshot_manifest(archive_path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            payload = json.loads(archive.read("manifest.json").decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def list_code_snapshots(backup_dir: Path = DEFAULT_BACKUP_DIR) -> List[Dict[str, Any]]:
    directory = backup_dir.resolve()
    if not directory.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in directory.glob("mina_code_*.zip"):
        manifest = read_code_snapshot_manifest(path)
        if not manifest:
            continue
        rows.append({
            "path": str(path),
            "name": path.name,
            "created_at": manifest.get("created_at"),
            "reason": manifest.get("reason"),
            "file_count": manifest.get("file_count"),
            "timestamp": path.stat().st_mtime,
        })
    rows.sort(key=lambda row: float(row.get("timestamp") or 0.0), reverse=True)
    return rows


def _safe_member_target(member_name: str, extraction_root: Path) -> Optional[Path]:
    normalized = str(member_name or "").replace("\\", "/")
    if not normalized.startswith("files/"):
        return None
    relative = normalized[len("files/"):]
    if not relative or relative.startswith("/"):
        return None
    target = (extraction_root / relative).resolve()
    try:
        if os.path.commonpath([str(target), str(extraction_root.resolve())]) != str(extraction_root.resolve()):
            return None
    except ValueError:
        return None
    return target


def restore_code_snapshot(
    archive_path: Path,
    project_root: Path = PROJECT_ROOT,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    create_safety_snapshot: bool = True,
) -> Dict[str, Any]:
    archive_file = archive_path.resolve()
    destination = backup_dir.resolve()
    try:
        if os.path.commonpath([str(archive_file), str(destination)]) != str(destination):
            return {"ok": False, "error": "snapshot_outside_backup_directory"}
    except ValueError:
        return {"ok": False, "error": "snapshot_outside_backup_directory"}
    if not archive_file.is_file():
        return {"ok": False, "error": "snapshot_not_found"}

    manifest = read_code_snapshot_manifest(archive_file)
    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    expected = {
        str(item.get("path") or ""): item
        for item in manifest_files
        if isinstance(item, dict) and str(item.get("path") or "")
    }
    if not expected:
        return {"ok": False, "error": "snapshot_manifest_invalid"}

    safety = None
    if create_safety_snapshot:
        safety = create_code_snapshot(
            reason=f"pre_restore_safety_snapshot:{archive_file.name}",
            project_root=project_root,
            backup_dir=backup_dir,
        )
        if not safety.get("ok"):
            return {"ok": False, "error": "safety_snapshot_failed", "detail": safety.get("error")}

    root = project_root.resolve()
    restored: List[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="mina_code_restore_") as temporary:
            extraction_root = Path(temporary).resolve()
            with zipfile.ZipFile(archive_file, "r") as archive:
                archive_names = set(archive.namelist())
                for relative, metadata in expected.items():
                    member = f"files/{relative}"
                    if member not in archive_names:
                        raise RuntimeError(f"snapshot_member_missing:{relative}")
                    extracted = _safe_member_target(member, extraction_root)
                    if extracted is None:
                        raise RuntimeError(f"unsafe_snapshot_path:{relative}")
                    extracted.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source, extracted.open("wb") as target:
                        target.write(source.read())
                    if _sha256_file(extracted) != str(metadata.get("sha256") or ""):
                        raise RuntimeError(f"snapshot_checksum_mismatch:{relative}")

            for relative in expected:
                source = (extraction_root / relative).resolve()
                target = (root / relative).resolve()
                try:
                    if os.path.commonpath([str(target), str(root)]) != str(root):
                        raise RuntimeError(f"unsafe_restore_target:{relative}")
                except ValueError as exc:
                    raise RuntimeError(f"unsafe_restore_target:{relative}") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_target = target.with_name(f".{target.name}.mina-restore.tmp")
                temporary_target.write_bytes(source.read_bytes())
                os.replace(temporary_target, target)
                restored.append(relative)

        return {
            "ok": True,
            "error": None,
            "restored_files": restored,
            "safety_snapshot": safety.get("path") if isinstance(safety, dict) else None,
            "source_snapshot": str(archive_file),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"restore_failed: {exc}",
            "restored_files": restored,
            "safety_snapshot": safety.get("path") if isinstance(safety, dict) else None,
        }