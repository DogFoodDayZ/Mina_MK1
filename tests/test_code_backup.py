import json
import zipfile
from pathlib import Path

from restore.code_backup import create_code_snapshot, read_code_snapshot_manifest, restore_code_snapshot


def test_code_snapshot_excludes_secrets_runtime_data_and_build_outputs(tmp_path):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    (project / "agent").mkdir(parents=True)
    (project / "build").mkdir()
    (project / "memory").mkdir()
    (project / "agent" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=yes\n", encoding="utf-8")
    (project / "memory" / "memory.db").write_bytes(b"database")
    (project / "build" / "generated.py").write_text("generated = True\n", encoding="utf-8")

    result = create_code_snapshot("test", project_root=project, backup_dir=backups)

    assert result["ok"] is True
    paths = {item["path"] for item in result["manifest"]["files"]}
    assert paths == {"agent/core.py"}


def test_code_restore_overlays_snapshot_and_creates_safety_snapshot(tmp_path):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    project.mkdir()
    source = project / "core.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = create_code_snapshot("known_good", project_root=project, backup_dir=backups)
    source.write_text("VALUE = broken\n", encoding="utf-8")

    restored = restore_code_snapshot(
        Path(snapshot["path"]),
        project_root=project,
        backup_dir=backups,
        create_safety_snapshot=True,
    )

    assert restored["ok"] is True
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(restored["safety_snapshot"]).is_file()
    safety_manifest = read_code_snapshot_manifest(Path(restored["safety_snapshot"]))
    assert safety_manifest["reason"].startswith("pre_restore_safety_snapshot:")


def test_code_restore_rejects_checksum_mismatch(tmp_path):
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    project.mkdir()
    source = project / "core.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = create_code_snapshot("known_good", project_root=project, backup_dir=backups)
    archive_path = Path(snapshot["path"])

    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr("files/core.py", "VALUE = tampered\n")
    source.write_text("VALUE = current\n", encoding="utf-8")

    restored = restore_code_snapshot(
        archive_path,
        project_root=project,
        backup_dir=backups,
        create_safety_snapshot=False,
    )

    assert restored["ok"] is False
    assert "checksum_mismatch" in restored["error"]
    assert source.read_text(encoding="utf-8") == "VALUE = current\n"