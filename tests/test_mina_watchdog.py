import hashlib
import json
import zipfile
from pathlib import Path

from watchdog.mina_watchdog import MinaWatchdog


def create_candidate_snapshot(project: Path, backups: Path, relative: str, content: bytes) -> Path:
    backups.mkdir(parents=True)
    archive_path = backups / "mina_code_test.zip"
    manifest = {
        "schema": 1,
        "files": [{
            "path": relative,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(f"files/{relative}", content)
        archive.writestr("manifest.json", json.dumps(manifest))
    return archive_path


def test_restore_candidate_restores_existing_and_removes_created_files(tmp_path):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    runtime = tmp_path / "watchdog"
    project.mkdir()
    source = project / "core.py"
    source.write_text("VALUE = broken\n", encoding="utf-8")
    added = project / "added.py"
    added.write_text("NEW = True\n", encoding="utf-8")
    snapshot = create_candidate_snapshot(project, backups, "core.py", b"VALUE = 1\n")
    watchdog = MinaWatchdog(project, runtime, backups)

    result = watchdog.restore_candidate({
        "project_root": str(project),
        "snapshot": str(snapshot),
        "files": ["core.py", "added.py"],
        "created_files": ["added.py"],
    })

    assert result == {"restored_files": ["core.py"], "removed_files": ["added.py"]}
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not added.exists()


def test_restore_candidate_rejects_tampered_snapshot(tmp_path):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    project.mkdir()
    source = project / "core.py"
    source.write_text("VALUE = broken\n", encoding="utf-8")
    snapshot = create_candidate_snapshot(project, backups, "core.py", b"VALUE = 1\n")
    with zipfile.ZipFile(snapshot, "a") as archive:
        archive.writestr("files/core.py", b"VALUE = tampered\n")
    watchdog = MinaWatchdog(project, tmp_path / "watchdog", backups)

    try:
        watchdog.restore_candidate({
            "project_root": str(project),
            "snapshot": str(snapshot),
            "files": ["core.py"],
            "created_files": [],
        })
    except RuntimeError as exc:
        assert "checksum_mismatch" in str(exc)
    else:
        raise AssertionError("tampered snapshot was accepted")

    assert source.read_text(encoding="utf-8") == "VALUE = broken\n"


def test_active_candidate_is_rolled_back_after_failed_restart(tmp_path, monkeypatch):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    runtime = tmp_path / "watchdog"
    project.mkdir()
    source = project / "core.py"
    source.write_text("VALUE = broken\n", encoding="utf-8")
    snapshot = create_candidate_snapshot(project, backups, "core.py", b"VALUE = 1\n")
    watchdog = MinaWatchdog(project, runtime, backups)
    candidate = {
        "candidate_id": "candidate-test",
        "phase": "pending_restart",
        "project_root": str(project),
        "snapshot": str(snapshot),
        "files": ["core.py"],
        "created_files": [],
    }
    watchdog.save_candidate(candidate, "pending_restart")
    health_results = iter([(False, {"error": "candidate failed"}), (True, {"api": {}})])
    monkeypatch.setattr(watchdog, "restart_and_validate", lambda: next(health_results))
    monkeypatch.setattr(watchdog, "stop_api", lambda: None)

    assert watchdog.process_candidate(candidate) is True
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    final = watchdog.load_candidate()
    assert final["phase"] == "rolled_back"